# ADR 014: Task Engine and Agent Loop

## Status

Accepted. Implimented.

## Context

### What exists today

The system has a reactive message path (user → Orchestrator → response) and a proactive path via the Heartbeat extension. The Heartbeat extension (`SchedulerProvider`) runs a lightweight Scout agent every 10 minutes; Scout checks memory context and either does nothing (`noop`), responds directly (`done`), or escalates to the Orchestrator (`escalate`). On escalation the kernel emits `system.agent.task`, the Orchestrator handles the task, and the result goes to the user.

This covers simple proactive checks, but does **not** solve multi-step background work:

| Problem | Detail |
|---------|--------|
| **No task persistence** | There is no durable representation of "a task the system is working on." If the Orchestrator is interrupted mid-work, there is no way to resume — the work is lost. |
| **State lives in conversation history** | The Orchestrator's context window is the only "state" of an ongoing task. This violates explicit state management — the state of a multi-step task should live in its own structure, not in ephemeral LLM conversation. |
| **No subtask delegation** | The Orchestrator cannot spawn a long-running background job for a specialized agent and continue handling user messages. Everything is synchronous within one `Runner.run()` call. |
| **Heartbeat is reactive, not generative** | Heartbeat can escalate to the Orchestrator, but it cannot create discrete tasks for any agent. It is a "pulse check," not a "task factory" as described in the OpenClaw pattern. |
| **No progress tracking** | There is no way to show the user that a background task is 60% done, or that step 3 of 7 has completed. |
| **No crash recovery for agent work** | If the process restarts while an agent is mid-task, the work vanishes. The Event Bus provides at-least-once delivery for events, but there is no equivalent for multi-step agent work. |

### What the research recommends

The research (Agent Loop Architecture report, Feb 2026) identifies three key components for background agent work: **agent loop** (ReAct cycle), **task queue** (durable storage + claim), and **state management** (explicit checkpointing). For a local-first, single-user, SQLite-based system, the recommended tier is **MVP Tier 1** — a SQLite task table with lease-based claiming, a ReAct agent loop with explicit state, and checkpointing after every significant step. This gives 90% of production-grade capabilities without infrastructure complexity.

The Perplexity discussion further clarified:

1. The architecture splits into two independent layers: **Task Engine** (lifecycle of tasks) and **Agent Loop** (multi-step execution within one task).
2. Heartbeat should become an autonomous **source of tasks** (OpenClaw "Gardener" pattern), not just an escalation trigger.
3. Three equal task sources — user, heartbeat, external events — all feed through one mechanism.

### Design goals

- **Durable multi-step execution** — tasks survive process restarts; agent resumes from the last checkpoint.
- **Async delegation** — Orchestrator can submit a background task and return to the user immediately.
- **Explicit state** — each task has its own `TaskState`, decoupled from conversation history.
- **Composable** — implemented as an extension (`ServiceProvider` + `ToolProvider`), following the all-is-extension principle.
- **Observable** — every step is recorded; task history is queryable.
- **Bounded** — hard limits on steps, tokens, retries, and time per task.
- **No new infrastructure** — SQLite only, no Redis/Celery/Temporal.

## Decision

### 1. Two-layer architecture

```
┌─────────────────────────────────────────────────────────────────┐
│ Task sources                                                     │
│                                                                  │
│  User ──► Orchestrator ──► submit_task(...)                      │
│  Heartbeat Scout ────────► submit_task(...)                      │
│  External event ─────────► submit_task(...)                      │
│                                    │                             │
│                                    ▼                             │
│              ┌─────────────────────────────────┐                 │
│              │      LAYER 1: Task Engine        │                │
│              │                                  │                │
│              │  agent_task table (SQLite)        │                │
│              │  submit / cancel / query          │                │
│              │  lease-based claiming             │                │
│              │  retry with exponential backoff   │                │
│              │  subtask tree (parent_id)         │                │
│              │  EventBus notifications           │                │
│              └──────────────┬──────────────────┘                 │
│                             │                                    │
│                             ▼                                    │
│              ┌─────────────────────────────────┐                 │
│              │      LAYER 2: Agent Loop         │                │
│              │                                  │                │
│              │  ReAct cycle (plan → act → obs)   │                │
│              │  Explicit TaskState               │                │
│              │  Checkpoint after each step       │                │
│              │  Tool calls + subtask delegation   │                │
│              │  Guardrails (steps, tokens, time)  │                │
│              └──────────────────────────────────┘                │
└──────────────────────────────────────────────────────────────────┘
```

**Layer 1 (Task Engine)** manages the lifecycle: create, queue, claim, complete, fail, retry, cancel. It is storage-centric and agent-agnostic.

**Layer 2 (Agent Loop)** executes a single task: iterative ReAct loop with tool calls, state management, and checkpointing. It is agent-specific and stateful.

The separation means the Task Engine can queue tasks for different agents without knowing how they execute, and the Agent Loop can focus on step-by-step work without managing queues or leases.

### 2. SQLite schema

Two new tables are added to the existing SQLite database (alongside `event_journal`).

```sql
CREATE TABLE IF NOT EXISTS agent_task (
    task_id       TEXT    PRIMARY KEY,
    parent_id     TEXT    REFERENCES agent_task(task_id),
    run_id        TEXT    NOT NULL,
    agent_id      TEXT    NOT NULL,
    status        TEXT    NOT NULL DEFAULT 'pending',
    priority      INTEGER DEFAULT 5,
    payload       TEXT    NOT NULL,   -- JSON: { goal, context_refs, max_steps, source }
    result        TEXT,               -- JSON: output artifact
    checkpoint    TEXT,               -- JSON: explicit TaskState snapshot
    error         TEXT,
    attempt_no    INTEGER DEFAULT 0,
    schedule_at   REAL,              -- Unix ts; NULL = ready now
    leased_by     TEXT,              -- worker id (asyncio task id)
    lease_exp     REAL,              -- Unix ts; lease expiry
    created_at    REAL    DEFAULT (unixepoch('subsec')),
    updated_at    REAL    DEFAULT (unixepoch('subsec'))
);

CREATE INDEX IF NOT EXISTS idx_at_status_schedule
    ON agent_task(status, schedule_at);
CREATE INDEX IF NOT EXISTS idx_at_parent
    ON agent_task(parent_id);

CREATE TABLE IF NOT EXISTS task_step (
    step_id          TEXT PRIMARY KEY,
    task_id          TEXT NOT NULL REFERENCES agent_task(task_id),
    step_no          INTEGER NOT NULL,
    step_type        TEXT NOT NULL,  -- 'llm_call' | 'tool_call' | 'sub_task' | 'human_input'
    status           TEXT NOT NULL,  -- 'running' | 'done' | 'failed'
    idempotency_key  TEXT UNIQUE,
    input_ref        TEXT,           -- JSON
    output_ref       TEXT,           -- JSON
    tokens_used      INTEGER,
    duration_ms      INTEGER,
    error_code       TEXT,
    created_at       REAL DEFAULT (unixepoch('subsec'))
);

CREATE INDEX IF NOT EXISTS idx_ts_task
    ON task_step(task_id, step_no);
```

#### Table semantics

**`agent_task`** — one row per task. `status` transitions:

```
pending ──► running ──► done
   │            │
   │            ├──► waiting_subtasks ──► running (resumed)
   │            │
   │            ├──► human_review ──► running (resumed)
   │            │
   │            └──► failed
   │                   │
   └──► retry_scheduled ◄┘ (if retryable, attempt < max)
```

- `pending` — queued, waiting for a worker to claim.
- `running` — claimed by a worker, agent loop is executing.
- `waiting_subtasks` — parent task paused until child tasks complete.
- `human_review` — paused, waiting for user input or approval.
- `done` — completed successfully; `result` contains the output.
- `failed` — exhausted retries or hit a non-retryable error.
- `retry_scheduled` — will be retried after `schedule_at`; exponential backoff with jitter.

`parent_id` forms a tree: a task can spawn subtasks, and the parent waits until all children are `done` or `failed`.

`run_id` groups tasks that were submitted together (e.g. a single user request that generates several subtasks). Useful for querying "show me everything related to that request."

`leased_by` / `lease_exp` — lease-based claiming. A worker sets these atomically when picking up a task. If the process dies, `lease_exp` eventually passes and another worker (or the same worker after restart) can reclaim the task. With a single worker this is a safety net for crashes, not a concurrency mechanism.

**`task_step`** — one row per step within a task's agent loop. Records what happened for observability and idempotency. `idempotency_key` prevents duplicate execution if a step is retried after a crash (the worker checks whether a step with the same key already has status `done`).

#### Why a separate table, not the Event Bus journal

The Event Bus journal (`event_journal`) is optimized for event transport: fire-and-forget publish, topic-based dispatch, FIFO processing. Tasks are fundamentally different: they have complex state machines, leases, parent-child relationships, retry policies, and long lifetimes (minutes to hours). Merging these concerns would complicate both systems. The tables coexist in the same SQLite database; the Task Engine emits events to the Event Bus when tasks change state.

### 3. TaskEngine extension

A new extension at `sandbox/extensions/task_engine/` implements `ServiceProvider` (background worker loop) and `ToolProvider` (tools for agents).

```
sandbox/extensions/task_engine/
├── manifest.yaml
├── main.py           # TaskEngineExtension: initialize, worker, tools
├── worker.py         # AgentLoopWorker: claim-execute-complete cycle
├── state.py          # TaskState dataclass + checkpoint serialization
├── schema.py         # DB schema creation and migration
└── models.py         # TaskRecord, StepRecord dataclasses
```

#### manifest.yaml

```yaml
id: task_engine
name: Task Engine
version: "1.0.0"
entrypoint: main:TaskEngineExtension
description: >
  Multi-step task execution engine. Manages async agent work:
  submit, track, cancel tasks. Provides background worker with
  ReAct agent loop, checkpointing, retry, and subtask delegation.

depends_on:
  - kv
  # agent extensions listed in config.agent_extensions must also appear here
  # so they initialize before TaskEngine resolves them
  # - image_agent
  # - code_agent

config:
  tick_sec: 1.0
  max_concurrent_tasks: 3
  lease_ttl_sec: 90
  max_retries: 5
  default_max_steps: 20
  step_timeout_sec: 120
  agent_extensions: []
  # Explicit list of AgentProvider extension IDs that TaskEngine can dispatch to.
  # Example: [image_agent, code_agent, research_agent]
  # Each ID must also appear in depends_on for initialization order.

events:
  publishes:
    - topic: task.submitted
      description: "New task submitted to the queue"
    - topic: task.completed
      description: "Task finished (done or failed)"
    - topic: task.progress
      description: "Task made progress (step completed)"
```

#### Tools for agents (ToolProvider)

The Orchestrator (and any agent with `uses_tools: [task_engine]`) gets these tools:

```python
@function_tool
async def submit_task(
    goal: str,
    agent_id: str = "orchestrator",
    priority: int = 5,
    parent_task_id: str | None = None,
    max_steps: int | None = None,
) -> SubmitTaskResult:
    """Submit a new background task for async execution by a specified agent.

    Use when:
    - The task requires multiple steps (research, generation, analysis)
    - The task should run in the background while the user continues chatting
    - The task needs a specialized agent (image_agent, code_agent, etc.)

    Returns task_id for tracking.
    """

@function_tool
async def get_task_status(task_id: str) -> TaskStatusResult:
    """Get current status, progress, and partial result of a background task."""

@function_tool
async def list_active_tasks() -> ActiveTasksResult:
    """List all running and pending tasks with statuses and progress."""

@function_tool
async def cancel_task(task_id: str, reason: str = "") -> CancelTaskResult:
    """Cancel a running or pending task."""
```

All tool return types are structured Pydantic models (per project conventions — no raw strings).

```python
class SubmitTaskResult(BaseModel):
    task_id: str
    status: str
    message: str

class TaskStatusResult(BaseModel):
    task_id: str
    status: str
    agent_id: str
    goal: str
    step: int
    max_steps: int
    attempt_no: int
    partial_result: str | None
    error: str | None
    created_at: float
    updated_at: float

class ActiveTasksResult(BaseModel):
    tasks: list[TaskStatusResult]
    total: int
```

#### ServiceProvider: worker loop

`TaskEngineExtension` implements `ServiceProvider`. The `run_background()` method runs the worker loop:

```python
async def run_background(self) -> None:
    while True:
        claimed = await self._claim_next_task()
        if claimed:
            await self._execute_task(claimed)
        else:
            await asyncio.sleep(self._tick_sec)
```

The worker processes one task at a time in the main loop. For `max_concurrent_tasks > 1`, tasks are dispatched into a bounded `asyncio.Semaphore`-guarded set of coroutines. Even so, all execution is single-threaded (asyncio) — concurrency comes from I/O overlap (LLM calls, tool calls), not parallelism.

#### Claiming: CAS on lease

SQLite does not have `SELECT ... FOR UPDATE SKIP LOCKED`. Instead, claiming uses a Compare-And-Swap pattern:

```python
async def _claim_next_task(self) -> TaskRecord | None:
    now = time.time()
    row = await self._db.fetchone("""
        SELECT task_id, status FROM agent_task
        WHERE status IN ('pending', 'retry_scheduled')
          AND (schedule_at IS NULL OR schedule_at <= ?)
          AND (lease_exp IS NULL OR lease_exp < ?)
        ORDER BY priority DESC, created_at ASC
        LIMIT 1
    """, (now, now))

    if not row:
        return None

    updated = await self._db.execute("""
        UPDATE agent_task
        SET status = 'running',
            leased_by = ?,
            lease_exp = ?,
            updated_at = unixepoch('subsec')
        WHERE task_id = ?
          AND status IN ('pending', 'retry_scheduled')
    """, (self._worker_id, now + self._lease_ttl, row["task_id"]))

    if updated.rowcount == 0:
        return None  # someone else claimed it (or status changed)

    return await self._load_task(row["task_id"])
```

With a single worker process, the CAS is a safety net. If multiple workers ever exist (future scaling), this pattern prevents double-claiming without requiring explicit locks.

### 4. Agent Loop: ReAct with checkpointing

The agent loop is the core of Layer 2. It executes a single task by driving an agent through iterative ReAct cycles.

#### TaskState — explicit state

```python
@dataclass
class TaskState:
    goal: str
    step: int = 0
    status: str = "running"
    context: dict = field(default_factory=dict)
    steps_log: list[dict] = field(default_factory=list)
    pending_subtasks: list[str] = field(default_factory=list)
    partial_result: str | None = None
    schema_version: int = 1

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, data: str) -> "TaskState":
        return cls(**json.loads(data))
```

`TaskState` is a plain dataclass, serializable to JSON, stored in `agent_task.checkpoint`. It is **not** conversation history — it is the structured representation of what the agent has accomplished and what remains.

`schema_version` enables forward-compatible migrations: if a future version adds fields, the loader can upgrade old checkpoints without losing data.

#### Completion signal: `finish_task` tool

The agent loop needs an unambiguous signal that the agent considers the task done. Without it, the loop either runs to `max_steps` every time or relies on fragile heuristics. The solution is a dedicated tool injected into every agent running inside the Task Engine:

```python
@function_tool
async def finish_task(result: str) -> FinishTaskResult:
    """Call this when you have completed the assigned task.
    Provide the final result as a structured summary.
    The task loop will stop after this call."""
    return FinishTaskResult(finished=True, result=result)

class FinishTaskResult(BaseModel):
    finished: bool
    result: str
```

The worker injects `finish_task` into the agent's tool set before running the loop. The agent's step prompt instructs it to call `finish_task(result)` when done. If the agent returns a response without calling `finish_task`, the loop continues to the next step. If the agent exhausts `max_steps` without ever calling `finish_task`, the task completes with `status='done'` using the last `partial_result` and a warning flag — partial results are better than nothing.

For `AgentProvider` agents, the tool is added to `uses_tools` automatically. For orchestrator-path tasks (`agent_id="orchestrator"`), the worker wraps the call such that the prompt includes explicit instructions to signal completion.

#### Lease renewal

A single agent invocation (LLM call with tool use) can take significant time. If a step approaches the lease TTL (default 90s), the lease expires and `_recover_stale_tasks()` could reset the task — causing double execution. The worker must renew the lease during execution:

```python
async def _renew_lease(self, task_id: str) -> bool:
    result = await self._db.execute("""
        UPDATE agent_task SET lease_exp = ?
        WHERE task_id = ? AND leased_by = ?
    """, (time.time() + self._lease_ttl, task_id, self._worker_id))
    return result.rowcount > 0
```

Renewal happens at two points:
1. **At the start of each step** in `run_agent_loop` — before calling `agent.invoke()`.
2. **Periodically via background task** — for steps that take longer than half the TTL, an `asyncio.Task` renews the lease every `lease_ttl / 3` seconds while the step is in progress. This covers long LLM calls with extended tool use.

If renewal fails (e.g. another worker already reclaimed the task after a long network partition), the current worker aborts the step gracefully.

#### run_agent_loop

```python
async def run_agent_loop(
    agent: AgentProvider,
    state: TaskState,
    task: TaskRecord,
    db: Database,
    ctx: ExtensionContext,
    renew_lease: Callable,
) -> dict | None:

    max_steps = task.payload.get("max_steps", DEFAULT_MAX_STEPS)
    finish_result: str | None = None

    while state.step < max_steps:
        # 1. Renew lease before each step
        if not await renew_lease(task.task_id):
            raise LeaseRevoked(f"Lease lost for task {task.task_id}")

        # 2. Build context for this step
        step_context = AgentInvocationContext(
            conversation_summary=state.partial_result,
            correlation_id=task.run_id,
        )

        # 3. Invoke agent for one step (with background lease renewal)
        step_prompt = _build_step_prompt(state)
        async with _lease_keepalive(task.task_id, renew_lease):
            response = await agent.invoke(step_prompt, step_context)

        # 4. Record step
        step_record = StepRecord(
            step_id=uuid4().hex,
            task_id=task.task_id,
            step_no=state.step,
            step_type="llm_call",
            status="done" if response.status == "success" else "failed",
            tokens_used=response.tokens_used,
        )
        await _save_step(step_record, db)

        # 5. Handle errors
        if response.status == "error":
            raise RetryableError(response.error or "Agent step failed")
        if response.status == "refused":
            raise NonRetryableError(response.error or "Agent refused task")

        # 6. Update state
        state.step += 1
        state.partial_result = response.content
        state.steps_log.append({
            "step": state.step,
            "type": "llm_call",
            "summary": response.content[:200],
        })

        # 7. Checkpoint
        await _save_checkpoint(task.task_id, state, db)

        # 8. Check if agent called finish_task
        finish_result = _extract_finish_result(response)
        if finish_result is not None:
            return {"content": finish_result}

        # 9. Emit progress event
        await ctx.emit("task.progress", {
            "task_id": task.task_id,
            "step": state.step,
            "max_steps": max_steps,
        })

    # max_steps exhausted — return last partial result with warning
    return {
        "content": state.partial_result or "",
        "warning": f"Reached max_steps ({max_steps}) without finish_task signal",
    }
```

```python
@asynccontextmanager
async def _lease_keepalive(task_id: str, renew_lease: Callable):
    """Background lease renewal while a long step executes."""
    renewal_interval = LEASE_TTL / 3
    stop = asyncio.Event()

    async def _renew_loop():
        while not stop.is_set():
            await asyncio.sleep(renewal_interval)
            if not stop.is_set():
                await renew_lease(task_id)

    task = asyncio.create_task(_renew_loop())
    try:
        yield
    finally:
        stop.set()
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
```

Key design choices:

- **One agent invocation per step.** Each iteration calls `agent.invoke()` once. The agent internally may use tool calls (the OpenAI Agents SDK `Runner.run()` handles the tool loop within one invocation). The outer loop here manages multi-invocation steps — e.g. "step 1: gather data, step 2: analyze, step 3: generate report."
- **Checkpoint after every step.** If the process crashes between steps, the task resumes from the last checkpoint. The agent receives the `TaskState` (including `partial_result` and `steps_log`) as context for the next step.
- **Step recording in `task_step`.** Every step is persisted independently for observability. Steps are never deleted — they form the full audit trail.
- **Explicit completion via `finish_task` tool.** The agent must call `finish_task(result)` to signal completion. No heuristics. If the agent never calls it, the loop runs to `max_steps` and returns the last partial result with a warning.
- **Lease renewal.** The worker renews the lease before each step and via a background keepalive during long steps. This prevents stale-task recovery from interfering with active execution.

#### Subtask delegation

When an agent determines that part of the work should be delegated to a specialized agent, it uses the `submit_task` tool with `parent_task_id` set to the current task:

```python
# Inside an agent's tool call
await submit_task(
    goal="Generate a logo based on these requirements: ...",
    agent_id="image_agent",
    parent_task_id=current_task_id,
)
```

The parent task transitions to `waiting_subtasks`. The worker monitors subtask completion via the Event Bus:

```python
# On task.completed event
async def _on_task_completed(self, event: Event) -> None:
    parent_id = event.payload.get("parent_id")
    if not parent_id:
        return

    # Check if all subtasks of this parent are done
    pending = await self._db.fetchone("""
        SELECT COUNT(*) as cnt FROM agent_task
        WHERE parent_id = ? AND status NOT IN ('done', 'failed', 'cancelled')
    """, (parent_id,))

    if pending["cnt"] == 0:
        # Resume parent: set status back to 'pending' so worker picks it up
        await self._db.execute("""
            UPDATE agent_task
            SET status = 'pending', updated_at = unixepoch('subsec')
            WHERE task_id = ? AND status = 'waiting_subtasks'
        """, (parent_id,))
```

The parent's checkpoint contains the subtask IDs in `state.pending_subtasks`. When resumed, the agent loop loads subtask results from the database and injects them into the state before continuing.

#### Retry with exponential backoff

```python
def _compute_retry_delay(attempt: int) -> float:
    base = 5.0
    max_delay = 300.0
    delay = min(base * (2 ** attempt), max_delay)
    jitter = random.uniform(0, delay * 0.3)
    return delay + jitter
```

On `RetryableError`, the worker sets `status = 'retry_scheduled'`, increments `attempt_no`, and sets `schedule_at` to `now + delay`. The next tick of the worker loop will pick up the task after the delay. If `attempt_no >= max_retries`, the task transitions to `failed`.

### 5. Heartbeat refactoring: task factory

The existing Heartbeat extension changes from an escalation-only mechanism to an autonomous **task source**. This aligns with the OpenClaw "Gardener" pattern: periodically scan context, create tasks when work is detected.

#### Current flow (replaced)

```
Scout → Memory scan → Decision: noop | done | escalate
                                          ↓
                              ctx.request_agent_task(reason)
                                          ↓
                              Orchestrator handles synchronously
```

#### New flow

```
Scout → Memory/KV scan → Decision: noop | submit_task | alert
                                              ↓
                          task_engine.submit_task(goal, agent_id, priority)
                                              ↓
                          Task queued → Worker executes asynchronously
                                              ↓
                          On completion → notify_user (if needed)
```

#### HeartbeatDecision changes

```python
@dataclass
class HeartbeatDecision:
    action: Literal["noop", "submit_task", "alert"]
    reason: str
    task: TaskSpec | None = None

@dataclass
class TaskSpec:
    goal: str
    agent_id: str = "orchestrator"
    priority: int = 5
```

#### Updated dispatch

```python
match decision.action:
    case "noop":
        logger.debug("heartbeat: noop")
        return None

    case "submit_task":
        task_engine = self.ctx.get_extension("task_engine")
        result = await task_engine.submit_task(
            goal=decision.task.goal,
            agent_id=decision.task.agent_id,
            priority=decision.task.priority,
        )
        logger.info(
            "heartbeat: created task %s — %s",
            result.task_id, decision.reason,
        )
        return None

    case "alert":
        return {"text": decision.reason}
```

#### Scout gets `submit_task` tool

The Heartbeat manifest adds `task_engine` to `uses_tools` (or the Heartbeat extension accesses it via `ctx.get_extension()`). The Scout's prompt is updated to include task creation as an available action.

#### Two operational modes

1. **Scanning mode** (primary) — every N minutes, Scout scans memory and context for work that needs doing, then creates tasks via `submit_task`.
2. **Monitoring mode** (secondary) — Scout checks `list_active_tasks()` for stalled or failed tasks and either retries them or alerts the user.

Both modes run on the same cron schedule; the Scout's prompt instructs it to perform both scans.

### 6. Task sources

Three equal sources feed into the same Task Engine:

| Source | Trigger | Example |
|--------|---------|---------|
| **User** | Direct message in channel | "Research competitor pricing" → Orchestrator → `submit_task(goal, "research_agent")` |
| **Heartbeat** | Periodic cron (every N minutes) | Scout detects upcoming deadline → `submit_task("prepare report", "orchestrator", priority=7)` |
| **External event** | EventBus event from extension | `schedule.due` event → handler calls `submit_task(...)` |

All paths use the same `submit_task` API. The `payload.source` field records the origin for observability.

### 7. Agent registry and task dispatch

#### Registry: config-driven, not Loader-wired

The Loader has no mechanism to wire extensions into each other — it only wires protocols into kernel registries (`MessageRouter`, tool lists). TaskEngine is a regular extension and cannot rely on Loader magic. Instead, it **self-discovers** agents during `initialize()` using an explicit config list and `ctx.get_extension()`:

```python
class TaskEngineExtension:
    def __init__(self):
        self._agent_registry: dict[str, AgentProvider] = {}

    async def initialize(self, context: ExtensionContext) -> None:
        self.ctx = context
        await self._setup_schema()
        self._subscribe_events()

        for ext_id in context.config.get("agent_extensions", []):
            provider = context.get_extension(ext_id)
            if provider and isinstance(provider, AgentProvider):
                descriptor = provider.get_agent_descriptor()
                self._agent_registry[descriptor.name] = provider
                logger.info("task_engine: registered agent %s (%s)", descriptor.name, ext_id)
            else:
                logger.warning("task_engine: %s is not an AgentProvider, skipping", ext_id)
```

Each agent extension listed in `config.agent_extensions` must also appear in `depends_on` to guarantee initialization order. This is explicit — no auto-discovery, no Loader changes, no core modifications.

#### Two dispatch paths: orchestrator vs specialized agents

The Orchestrator is **not** an `AgentProvider` — it is a raw `agents.Agent` object created by `create_orchestrator_agent()` in core. It does not implement `invoke(task, context)`. Forcing it into the `AgentProvider` protocol would be artificial and would require wrapping the kernel's agent — a layering violation.

Instead, task dispatch uses two paths based on `agent_id`:

```python
async def _execute_task(self, task: TaskRecord) -> None:
    state = self._load_or_init_state(task)

    if task.agent_id == "orchestrator":
        result = await self._run_orchestrator_loop(state, task)
    else:
        agent = self._agent_registry.get(task.agent_id)
        if not agent:
            raise NonRetryableError(f"Unknown agent: {task.agent_id}")
        result = await run_agent_loop(agent, state, task, self._db, self.ctx)

    await self._complete_task(task, result)
```

- **`agent_id="orchestrator"`** — the worker calls `ctx.invoke_agent(prompt)` (or `ctx.request_agent_background(prompt)` for silent execution). This uses the existing `ExtensionContext` API that routes to the kernel's Orchestrator. The outer loop still manages checkpointing, retries, and step recording — only the inner invocation path differs.
- **Any other `agent_id`** — the worker looks up the `AgentProvider` in the registry and calls `agent.invoke(prompt, context)` directly.

This keeps the Orchestrator as a kernel-level construct (no protocol wrapping) while giving specialized agents a clean `AgentProvider` path.

### 8. Integration with existing systems

#### EventBus

The Task Engine emits events at key lifecycle points:

| Event topic | When | Payload |
|-------------|------|---------|
| `task.submitted` | New task created | `{ task_id, agent_id, goal, priority, source }` |
| `task.progress` | Step completed | `{ task_id, step, max_steps }` |
| `task.completed` | Task finished (success or failure) | `{ task_id, parent_id, status, result?, error? }` |

Extensions can subscribe to these events for custom reactions (e.g. notify the user, trigger follow-up work, update dashboards).

#### Orchestrator

The Orchestrator receives `submit_task`, `get_task_status`, `list_active_tasks`, and `cancel_task` as tools (via `ToolProvider` from the Task Engine extension). It uses them when:

- The user asks for something that requires background work ("research X and send me a summary").
- The user asks about ongoing tasks ("what are you working on?").
- The user wants to cancel a task ("stop that research").

The Orchestrator's prompt is updated with guidance on when to use `submit_task` vs handling inline.

#### SchedulerManager

The existing cron loop (`SchedulerManager`) continues to drive Heartbeat ticks. No changes to the scheduler mechanism itself — it simply calls `execute_task("emit_heartbeat")` on schedule. The change is inside the Heartbeat extension, which now creates tasks instead of escalating.

#### MessageRouter

No changes to the reactive path. User messages still go through `MessageRouter` → Orchestrator. If the Orchestrator decides the request needs background processing, it calls `submit_task` as a tool call and returns a quick acknowledgment to the user.

### 9. Lifecycle: startup and shutdown

#### Startup

1. `TaskEngineExtension.initialize()` — create DB tables, subscribe to `task.completed` event, resolve `AgentProvider` extensions from `config.agent_extensions` into the agent registry via `ctx.get_extension()`.
2. `TaskEngineExtension.start()` → `run_background()` starts the worker loop.
3. Worker calls `_recover_stale_tasks()` — any task with `status = 'running'` and `lease_exp < now` is reset to `pending` (crash recovery).

#### Shutdown

1. Worker stops claiming new tasks.
2. In-flight tasks are checkpointed (current `TaskState` saved).
3. Leases are not explicitly released — they expire naturally. On next startup, `_recover_stale_tasks()` handles them.

#### Crash recovery

If the process crashes:

1. On restart, `_recover_stale_tasks()` finds tasks with expired leases and `status = 'running'`.
2. These tasks are set to `status = 'pending'` (or `retry_scheduled` if `attempt_no > 0`).
3. The worker picks them up and resumes from the last checkpoint (`agent_task.checkpoint`).

The checkpoint contains the full `TaskState`, so the agent loop resumes from the last completed step, not from scratch.

### 10. Guardrails

| Guardrail | Config key | Default | Behavior |
|-----------|-----------|---------|----------|
| Max steps per task | `default_max_steps` | 20 | Exceeded → task fails with `MaxStepsExceeded` |
| Step timeout | `step_timeout_sec` | 120 | Single agent invocation timeout; exceeded → `RetryableError` |
| Max retries | `max_retries` | 5 | After N retries → `failed` |
| Lease TTL | `lease_ttl_sec` | 90 | Worker must renew or complete within TTL |
| Max concurrent tasks | `max_concurrent_tasks` | 3 | Semaphore-bounded; excess tasks wait in queue |
| Max subtask depth | (hardcoded) | 3 | Prevents infinite subtask recursion |

Configurable via `config/settings.yaml` under the `task_engine` key, following the existing configuration pattern (ADR 002).

### 11. Observability

Every task and every step are persisted in SQLite. This provides:

- **Task history** — query `agent_task` for all tasks by status, agent, time range.
- **Step trace** — query `task_step` for the full execution trace of a task, including timing and token usage.
- **Correlation** — `run_id` links related tasks; `parent_id` shows the subtask tree.
- **Event trail** — `task.submitted`, `task.progress`, `task.completed` in the Event Bus journal.

No external observability stack (LangSmith, Jaeger) in MVP. The SQLite tables are the observability layer. Future: structured JSON logging per step, exportable traces.

## Implementation Plan

### Phase 1: Foundation

1. **DB schema** — add `agent_task` and `task_step` tables. Schema migration in `task_engine/schema.py`.
2. **TaskState** — `state.py` with the dataclass, JSON serialization, schema versioning.
3. **Models** — `models.py` with `TaskRecord`, `StepRecord`, tool result models.
4. **TaskEngine extension** — `main.py` implementing `ServiceProvider` + `ToolProvider`. Tools: `submit_task`, `get_task_status`, `list_active_tasks`, `cancel_task`.
5. **Worker loop** — `worker.py` with claim-execute-complete cycle, lease renewal, retry logic.
6. **Agent loop** — `run_agent_loop()` with checkpointing, step recording, completion detection.
7. **Agent registry** — TaskEngine resolves `AgentProvider` extensions from `config.agent_extensions` via `ctx.get_extension()` during `initialize()`.
8. **Orchestrator integration** — add `task_engine` tools to the Orchestrator's tool set.

### Phase 2: Heartbeat refactoring

1. **HeartbeatDecision** — update to `noop | submit_task | alert`.
2. **Scout prompt** — rewrite to include task creation guidance and monitoring mode.
3. **Dispatch logic** — replace `ctx.request_agent_task()` with `task_engine.submit_task()`.
4. **Event subscriptions** — Heartbeat subscribes to `schedule.due` for reactive task creation.

### Phase 3: Subtasks and user notification

1. **Subtask creation** — when an agent calls `submit_task` with `parent_task_id`, the worker validates subtask depth (max 3), creates the child task with `parent_id`, and transitions the parent to `waiting_subtasks`.
2. **Subtask completion handler** — TaskEngine subscribes to `task.completed`. On each completion, check whether all siblings (same `parent_id`) are terminal (`done` / `failed` / `cancelled`). If yes, collect child results, inject them into the parent's `TaskState.context["subtask_results"]`, and set parent `status='pending'` for the worker to resume.
3. **Partial failure policy** — if some subtasks succeed and others fail, the parent resumes with available results and a `subtask_failures` list in its state. The parent agent decides whether to retry, work around, or fail.
4. **Result aggregation** — subtask results are stored in their own `agent_task.result` rows. The parent reads them via SQL query on resume, not via event payload (which may be large). Only `task_id` and `status` are in the event.
5. **User notification** — on `task.completed` where `parent_id IS NULL` (top-level task), emit `system.user.notify` with a result summary. The existing Event Bus → `notify_user` handler delivers to the active channel.
6. **Progress reporting** — Orchestrator's `get_task_status` and `list_active_tasks` tools give real-time answers when the user asks "what are you working on?" No additional mechanism needed.

### Phase 4: Observability and polish

1. **Task cleanup** — periodic cleanup of old `done`/`failed` tasks (configurable retention).
2. **Structured logging** — JSON log entries per step with session_id, step_type, duration, tokens.
3. **Human-in-the-loop** — `human_review` status: task pauses and asks user for input via channel.

## Consequences

### Benefits

- **Durable work** — tasks persist across restarts. The agent resumes from the last checkpoint, not from scratch. No more lost work on crashes.
- **Async delegation** — the Orchestrator can submit a task and return to the user immediately. Background work does not block the conversation.
- **Explicit state** — `TaskState` decouples task progress from conversation history. The state is queryable, serializable, and version-controlled.
- **Unified task mechanism** — user requests, heartbeat detections, and external events all use the same `submit_task` → TaskEngine path. One system to monitor, debug, and manage.
- **Proactive autonomy** — Heartbeat becomes a genuine task factory, enabling autonomous multi-step work (scheduled reports, monitoring, follow-ups).
- **Observable** — every step is recorded. Task history and step traces are queryable from SQLite.
- **Composable** — TaskEngine is an extension, not core. It follows the all-is-extension principle and can be disabled, replaced, or extended without modifying the kernel.

### Trade-offs

- **Complexity** — the Task Engine adds a new extension with its own schema, worker loop, and state management. This is justified by the capabilities it provides, but increases the total system surface area.
- **SQLite concurrency limits** — with a single writer (WAL mode), the system handles hundreds of tasks per day without issues. Scaling beyond that would require a different backend (PostgreSQL, Redis).
- **Agent loop granularity** — the outer loop calls `agent.invoke()` per step. For agents that need fine-grained tool-level control (e.g. checkpointing after each tool call within a single invocation), the current design requires the agent to handle this internally.
- **CAS claiming inefficiency under concurrency** — when `max_concurrent_tasks > 1`, multiple coroutines may SELECT the same candidate row; only one wins the CAS UPDATE, the rest get `rowcount == 0` and retry. This is correct but wasteful under contention. For the expected load (max 3 concurrent tasks, local-first) this is negligible. If it becomes a bottleneck, the claiming query can be changed to use `RETURNING` or a serialized claim queue.
- **No distributed execution** — single process, single machine. Horizontal scaling is out of scope for the local-first architecture.

### What stays the same

- **Reactive path** — user messages still flow through `MessageRouter` → Orchestrator → response. No changes.
- **Extension protocols** — all existing protocols (`ToolProvider`, `AgentProvider`, `ChannelProvider`, etc.) are unchanged.
- **Event Bus** — used as a notification mechanism by the Task Engine, not replaced by it.
- **SchedulerManager** — cron loop continues to drive Heartbeat and any other scheduled tasks.
- **Orchestrator** — still the single brain in core. It gains new tools (`submit_task`, etc.) but its architecture is unchanged.

### Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| **DB growth** from long-running tasks with many steps | Medium | Periodic cleanup of old tasks. `task_step` rows for completed tasks can be archived or deleted after configurable retention. |
| **Agent loop divergence** — agent keeps working but never produces a final answer | Medium | Hard `max_steps` limit per task. Step timeout per invocation. Total time budget per task (future). |
| **Subtask cycles** — task A spawns task B which spawns task A | Low | Max subtask depth (default 3). Cycle detection on `parent_id` chain. |
| **Checkpoint schema drift** — old checkpoints incompatible with new `TaskState` | Low | `schema_version` field in TaskState enables forward-compatible migration. |
| **Lease TTL too short** — long LLM calls exceed lease TTL, task appears stale | Medium | Worker renews lease periodically during execution (heartbeat within the worker loop). TTL set conservatively (90s default). |
| **Orchestrator overuse of submit_task** — routes everything to background instead of responding directly | Low | Prompt engineering: clear guidance on when to use `submit_task` vs inline response. Simple questions should never become tasks. |

## Alternatives Considered

**LangGraph state machine** — provides checkpointing and graph-based execution natively. Rejected because it adds a significant dependency (LangChain ecosystem) and the explicit state + SQLite approach gives the same capabilities with less lock-in for this project's scale.

**Temporal / Inngest** — enterprise-grade durable execution. Rejected for requiring external infrastructure (Temporal Server, Kubernetes). The local-first, single-process constraint makes these overkill.

**Redis + Celery** — standard Python task queue. Rejected because it requires a Redis broker, adding operational complexity incompatible with the "run as a single executable" goal.

**Extending the Event Bus as a task queue** — reusing `event_journal` with additional columns for task state. Rejected because tasks and events have fundamentally different lifecycles, state machines, and query patterns. Mixing them would complicate both systems.

**Task state in conversation history** — letting the agent "remember" progress through its conversation context. Rejected because conversation context is ephemeral (limited by token window), not durable (lost on crash), and not queryable. The research explicitly identifies this as an anti-pattern.

**Task state in `kv` extension** — using the existing key-value store for task persistence. Rejected because `kv` is designed for simple key-value pairs, not complex state machines with queries, indexes, and transactions. A dedicated table is more appropriate.

## References

- [ReAct: Synergizing Reasoning and Acting in Language Models](https://arxiv.org/pdf/2210.03629.pdf)
- [How we built our multi-agent research system — Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)
- [Building LangGraph: Designing an Agent Runtime from first principles](https://blog.langchain.com/building-langgraph/)
- [Durable Execution meets AI — Temporal](https://temporal.io/blog/durable-execution-meets-ai-why-temporal-is-the-perfect-foundation-for-ai)
- [Building Durable AI Agents: A Guide to Context Engineering — Inngest](https://www.inngest.com/blog/building-durable-agents)
- [MCP Async Tasks: Building long-running workflows for AI Agents](https://workos.com/blog/mcp-async-tasks-ai-agent-workflows)
- [Build Long-Running AI Agents on Azure App Service](https://techcommunity.microsoft.com/blog/appsonazureblog/build-long-running-ai-agents-on-azure-app-service-with-microsoft-agent-framework/4463159)
- ADR 002: Nano-Kernel + Extensions
- ADR 003: Agent-as-Extension
- ADR 004: Event Bus in Core
