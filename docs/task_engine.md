# Task Engine

Multi-step background task execution for AI agents. The extension manages a queue of tasks, runs a worker that executes them via a ReAct-style agent loop with checkpointing and retries, and exposes tools so the Orchestrator (and other agents) can submit, track, and cancel work.

## Overview

- **ServiceProvider**: background worker loop that claims tasks, runs the agent loop, and completes or retries.
- **ToolProvider**: tools for the Orchestrator — submit tasks, get status, list active tasks, cancel, and human-in-the-loop (pause for user input, resume with response).
- **SchedulerProvider**: scheduled cleanup of old completed/failed tasks (configurable retention).
- **Events**: `task.submitted`, `task.completed`, `task.progress`.

Tasks are stored in SQLite (`agent_task`, `task_step`). Each task has a goal, agent id (`orchestrator` or a registered `AgentProvider`), priority, optional parent (for subtasks), and a checkpointed `TaskState` (goal, step, partial result, context for subtask results and human review).

## Architecture

| Module | Role |
|--------|-----|
| `main.py` | Extension lifecycle, `submit_task`, event handler, thin wrappers for tools and worker. |
| `worker.py` | Claim (CAS), lease renewal, `run_agent_loop` / `run_orchestrator_loop`, `execute_task` (run one task and handle errors). |
| `schema.py` | SQLite DB (WAL), `agent_task` and `task_step` tables. |
| `state.py` | `TaskState` dataclass — goal, step, context, partial_result, JSON (de)serialization for checkpoint. |
| `models.py` | Pydantic tool results (`SubmitTaskResult`, `TaskStatusResult`, etc.) and internal `TaskRecord` / `StepRecord`. |
| `task_queries.py` | DB reads/writes for status, list active, cancel. |
| `subtasks.py` | Depth check, update parent checkpoint, collect sibling results, try resume parent on `task.completed`. |
| `hitl.py` | Request human review (pause task, notify user), respond to review (inject response into state, set pending). |
| `cleanup.py` | Delete old done/failed/cancelled tasks and their steps (used by scheduler). |
| `task_engine_tools.py` | Builds the six Orchestrator tools that delegate to the extension. |

## Configuration

In `manifest.yaml` (and overrides in settings):

| Key | Default | Description |
|-----|---------|-------------|
| `tick_sec` | 1.0 | Sleep between claim attempts when queue is empty. |
| `max_concurrent_tasks` | 3 | Max tasks run in parallel (semaphore). |
| `lease_ttl_sec` | 90 | Task lease TTL; worker renews during execution. |
| `max_retries` | 5 | Retries before marking task failed. |
| `default_max_steps` | 20 | Default cap on agent loop steps per task. |
| `step_timeout_sec` | 120 | Not used in current loop (reserved). |
| `retention_days` | 30 | Cleanup: delete completed tasks older than this. |
| `agent_extensions` | [] | Extension ids to register as agents (e.g. `["image_agent"]`). Must be in `depends_on`. |

Tasks with `agent_id="orchestrator"` are executed via `ctx.invoke_agent_background(prompt)`. All other agent ids are resolved from the registry built from `agent_extensions` and `ctx.get_extension()`.

## Tools (Orchestrator)

- **submit_task** — Submit a background task: `goal`, `agent_id` (default `orchestrator`), `priority`, optional `parent_task_id`, optional `max_steps`. Returns `task_id`, `status`, `message`.
- **get_task_status** — By `task_id`: status, goal, step/max_steps, partial_result, error.
- **list_active_tasks** — All tasks in pending, running, retry_scheduled, waiting_subtasks, human_review.
- **cancel_task** — Cancel by `task_id` (and optional reason). Cancellation takes effect **between steps**; the current step (if any) completes first.
- **request_human_review** — Pause a running task and ask the user a question; task moves to `human_review` and user is notified.
- **respond_to_review** — Provide the user’s answer for a task in `human_review`; task moves back to `pending` and continues with the response in `state.context["review_response"]`.

## Agent loop and completion

- Worker runs a ReAct loop: each step builds a prompt from `TaskState` (goal, step N of M, subtask results/failures, review response, partial result) and invokes the agent (or Orchestrator).
- Step is timed; lease is renewed in the background during the call (`_lease_keepalive`). After the call: step is recorded in `task_step`, state is updated and checkpointed, then completion is checked.
- **Completion**: the agent must include the marker `<<TASK_COMPLETE>>` at the start of a line, followed by the final result. The loop treats that as done and returns `{"content": result}`. Without the marker, the loop continues until `max_steps` and then returns with a warning.
- Errors: `RetryableError` → retry with backoff (up to `max_retries`); `NonRetryableError` / `LeaseRevoked` / `TaskCancelled` → fail or cancel immediately.

## Subtasks

- When submitting a task, `parent_task_id` can point to a running task. Depth is limited (e.g. 3). The child is inserted; the parent is set to `waiting_subtasks` and the child id is appended to the parent’s checkpoint `pending_subtasks`.
- When a child completes, the engine handles `task.completed`: if all siblings are terminal, it collects results/failures from the DB, injects `subtask_results` and `subtask_failures` into the parent’s checkpoint, and sets the parent to `pending` so the worker resumes it. The next step prompt includes these results so the parent agent can continue.

## Human-in-the-loop

- A running task can call the `request_human_review` tool: the task is set to `human_review`, the question is stored in the checkpoint, and the user is notified. The worker releases the lease and does not mark the task done.
- When the user answers, something (e.g. channel or another tool) calls `respond_to_review`: the response is written to `state.context["review_response"]`, the task is set back to `pending`, and the next step prompt includes the review response.

## Cleanup

- A schedule (`cleanup_old_tasks`) runs daily (e.g. 04:00). It deletes `task_step` rows for old tasks, then deletes `agent_task` rows with status `done`/`failed`/`cancelled` and `updated_at` older than `retention_days`. Parents with existing children are not deleted.

## Events

- **task.submitted** — Payload includes `task_id`, `goal`, `agent_id`, etc.
- **task.completed** — Payload includes `task_id`, `parent_id`, `status` (done/failed/cancelled), `result` or `error`.
- **task.progress** — Payload includes `task_id`, `step`, `max_steps`.

The extension subscribes to `task.completed` to run subtask resume and to notify the user for top-level completions (via `ctx.notify_user`).

## Integration

- **Orchestrator**: add `task_engine` to the agent’s `uses_tools` in the manifest so it gets `submit_task`, `get_task_status`, `list_active_tasks`, `cancel_task`, `request_human_review`, `respond_to_review`.
- **Heartbeat**: can call `task_engine.submit_task(goal, agent_id, priority)` to create background tasks from the Scout’s “submit_task” decision instead of escalating synchronously.
