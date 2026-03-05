# Task Engine

Multi-step background task execution for AI agents. The extension manages a queue of tasks, runs a worker that executes them via a ReAct-style agent loop with checkpointing and retries, and exposes tools so the Orchestrator (and other agents) can submit, track, cancel work, and build task chains.

## Overview

- **ServiceProvider**: background worker loop that claims tasks, runs the agent loop, and completes or retries.
- **ToolProvider**: eight tools for the Orchestrator — submit tasks (single or chain), get status, list active tasks, cancel, and human-in-the-loop (pause for user input, resume with response).
- **SchedulerProvider**: scheduled cleanup of old completed/failed tasks (configurable retention).
- **Events**: `task.submitted`, `task.completed`, `task.progress`.

Tasks are stored in SQLite (`agent_task`, `task_step`). Each task has a goal, agent id (`orchestrator` or a registered `AgentProvider`), priority, optional parent (for subtasks), optional predecessor (`after_task_id` for chains), and a checkpointed `TaskState` (goal, step, partial result, context for subtask results and human review).

## Architecture

| Module | Role |
|--------|-----|
| `main.py` | Extension lifecycle, `submit_task`, `submit_chain`, event handler, thin wrappers for tools and worker. |
| `worker.py` | Claim (CAS), lease renewal, `run_agent_loop` / `run_orchestrator_loop`, `execute_task` (run one task and handle errors). |
| `schema.py` | SQLite DB (WAL), `agent_task` and `task_step` tables, chain migrations. |
| `state.py` | `TaskState` dataclass — goal, step, context, partial_result, JSON (de)serialization for checkpoint. |
| `models.py` | Pydantic tool results (`SubmitTaskResult`, `TaskStatusResult`, `SubmitChainResult`, `ChainStatusResult`, etc.) and internal `TaskRecord` / `StepRecord`. |
| `task_queries.py` | DB reads/writes for status, list active, cancel. |
| `chains.py` | Chain logic: `unblock_successors` (pass result or cascade failure), `cancel_chain_downstream`, `get_chain_tasks`. |
| `subtasks.py` | Depth check, update parent checkpoint, collect sibling results, try resume parent on `task.completed`. |
| `hitl.py` | Request human review (pause task, notify user), respond to review (inject response into state, set pending). |
| `cleanup.py` | Delete old done/failed/cancelled tasks and their steps (used by scheduler). |
| `task_engine_tools.py` | Builds the eight Orchestrator tools that delegate to the extension. |

## Configuration

In `manifest.yaml` (and overrides in `settings.yaml` under `extensions.task_engine.<key>`):

| Key | Default | Description |
|-----|---------|-------------|
| `tick_sec` | 1.0 | Sleep between claim attempts when queue is empty. |
| `max_concurrent_tasks` | 3 | Max tasks run in parallel (semaphore). |
| `lease_ttl_sec` | 90 | Task lease TTL; worker renews during execution. |
| `max_retries` | 5 | Retries before marking task failed. |
| `default_max_steps` | 20 | Default cap on agent loop steps per task. |
| `step_timeout_sec` | 120 | Not used in current loop (reserved). |
| `retention_days` | 30 | Cleanup: delete completed tasks older than this. |

**Dependencies:** `depends_on: [kv]`.

**Agent resolution:** tasks with `agent_id="orchestrator"` are executed via `ctx.invoke_agent_background(prompt)`. All other agent ids are resolved from `context.agent_registry`, which is populated by extensions that implement `AgentProvider`.

## Tools (Orchestrator)

- **submit_task** — Submit a background task: `goal`, `agent_id` (default `orchestrator`), `priority`, optional `parent_task_id`, optional `after_task_id` (predecessor for ad-hoc chaining), optional `max_steps`, optional `output_channel`. Returns `task_id`, `status`, `message`.
- **submit_chain** — Submit a sequence of tasks that execute one after another: `steps` (list of `ChainStep` with `goal` and `agent_id`), `priority`, optional `output_channel`. Each step's result is passed as context to the next. Returns `chain_id`, `tasks` list, `message`.
- **get_chain_status** — By `chain_id`: overall status (`done`/`failed`/`running`/`blocked`/`cancelled`) and per-task details.
- **get_task_status** — By `task_id`: status, goal, step/max_steps, partial_result, error.
- **list_active_tasks** — All tasks in pending, running, blocked, retry_scheduled, waiting_subtasks, human_review.
- **cancel_task** — Cancel by `task_id` (and optional reason). Cancellation takes effect **between steps**; the current step (if any) completes first.
- **request_human_review** — Pause a running task and ask the user a question; task moves to `human_review` and user is notified.
- **respond_to_review** — Provide the user's answer for a task in `human_review`; task moves back to `pending` and continues with the response in `state.context["review_response"]`.

## Agent loop and completion

- Worker runs a ReAct loop: each step builds a prompt from `TaskState` (goal, step N of M, predecessor result, subtask results/failures, review response, partial result) and invokes the agent (or Orchestrator). If task payload includes `output_channel`, the step prompt also includes an explicit delivery requirement for that channel.
- Step is timed; lease is renewed in the background during the call (`_lease_keepalive`). After the call: step is recorded in `task_step`, state is updated and checkpointed, then completion is checked.
- **Completion**: the agent must include the marker `<<TASK_COMPLETE>>` at the start of a line, followed by the final result. The loop treats that as done and returns `{"content": result}`. Without the marker, the loop continues until `max_steps` and then returns with a warning.
- Errors: `RetryableError` → retry with backoff (up to `max_retries`); `NonRetryableError` / `MaxStepsExceeded` / `LeaseRevoked` / `TaskCancelled` → fail or cancel immediately.

## Task states

| Status | Meaning |
|--------|---------|
| `pending` | Ready to run. |
| `blocked` | Waiting on predecessor (`after_task_id`) to complete. |
| `running` | Claimed by worker, executing. |
| `retry_scheduled` | Failed transiently, waiting for backoff timer. |
| `waiting_subtasks` | Parent waiting for child tasks to finish. |
| `human_review` | Paused, waiting for user response. |
| `done` | Finished successfully. |
| `failed` | Failed permanently. |
| `cancelled` | Cancelled by user or cascade. |

Key transitions:

- `pending` → `running` (claim)
- `blocked` → `pending` (predecessor completed successfully)
- `blocked` → `failed` (predecessor failed/cancelled — cascaded)
- `running` → `done` / `failed` / `cancelled` / `waiting_subtasks` / `human_review`
- `waiting_subtasks` → `pending` (all children terminal)
- `human_review` → `pending` (user responded)
- `retry_scheduled` → `running` (at `schedule_at`)

## Chains

Chains are ordered sequences of tasks where each step runs after the previous one completes, and the predecessor's result is forwarded.

- **`submit_chain`** creates all tasks in one transaction: the first task starts as `pending`, subsequent tasks start as `blocked` with `after_task_id` pointing to their predecessor. All tasks share a `chain_id` and have a `chain_order` index.
- When a task completes (`task.completed` event), `unblock_successors` finds all blocked tasks with `after_task_id` matching the completed task:
  - If the predecessor is `done`: the result content is injected into the successor's `payload["predecessor_result"]`, and the successor is set to `pending`.
  - If the predecessor is `failed` or `cancelled`: the failure cascades recursively to all downstream blocked tasks.
- The step prompt includes `predecessor_result` (truncated to 500 chars) so the agent has context from the previous chain step.
- **`get_chain_status`** returns the overall chain status derived from individual task statuses, plus per-task details in execution order.
- Ad-hoc chaining is also possible via `submit_task(after_task_id=...)` without a formal chain id.

## Subtasks

- When submitting a task, `parent_task_id` can point to a running task. Depth is limited (`MAX_SUBTASK_DEPTH = 3`). The child is inserted; the parent is set to `waiting_subtasks` and the child id is appended to the parent's checkpoint `pending_subtasks`.
- When a child completes, the engine handles `task.completed`: if all siblings are terminal, it collects results/failures from the DB, injects `subtask_results` and `subtask_failures` into the parent's checkpoint, and sets the parent to `pending` so the worker resumes it. The next step prompt includes these results so the parent agent can continue.

## Human-in-the-loop

- A running task can call the `request_human_review` tool: the task is set to `human_review`, the question is stored in the checkpoint, and the user is notified. The worker releases the lease and does not mark the task done.
- When the user answers, something (e.g. channel or another tool) calls `respond_to_review`: the response is written to `state.context["review_response"]`, the task is set back to `pending`, and the next step prompt includes the review response.

## Cleanup

- A schedule (`cleanup_old_tasks`) runs daily (at 04:00). It deletes `task_step` rows for old tasks, then deletes `agent_task` rows with status `done`/`failed`/`cancelled` and `updated_at` older than `retention_days`. Parents with existing children are not deleted.

## Events

- **task.submitted** — Payload: `task_id`, `goal`, `agent_id`, `priority`.
- **task.completed** — Payload: `task_id`, `parent_id`, `status` (done/failed/cancelled), `result` or `error`.
- **task.progress** — Payload: `task_id`, `step`, `max_steps`.

The extension subscribes to `task.completed` to:
1. Resume parent tasks when all subtasks are terminal (`try_resume_parent`).
2. Unblock chain successors or cascade failure (`unblock_successors`).
3. Notify the user for top-level task completions via `ctx.notify_user`.

## Integration

- **Orchestrator** receives task engine tools automatically: all `ToolProvider` extensions contribute their tools via `loader.get_all_tools()`. No manifest `uses_tools` entry is required for the orchestrator itself.
- **Other agents** that need task engine tools should add `task_engine` to their `depends_on` and `uses_tools`.
- **Scheduler**: schedule recurring or one-shot events with the topic system to create tasks on a timer via the EventBus.
