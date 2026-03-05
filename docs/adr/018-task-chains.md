# ADR 018: Task Chains

## Status

Accepted. Implemented

## Context

ADR 017 introduced the Agent Registry and delegation tools. The task_engine supports parent-child relationships where a parent waits for ALL children to complete. However, there is no mechanism for **sequential pipelines**: task A must finish before task B starts, and B before C, with results flowing forward.

### Current limitations

| Limitation | Description |
|------------|-------------|
| **No task ordering** | `parent_id` creates fan-out (one parent, many children); no fan-in sequential flow |
| **No result propagation** | Subtask results flow to parent; no predecessor → successor flow between sibling tasks |
| **No blocked state** | Tasks are either `pending` or `running`; no "waiting for dependency" |
| **Single submit** | `submit_task` creates one task; no atomic creation of linked sequences |

### Use case

Multi-phase workflows: research → draft → review. Each phase should receive the previous phase's output as context.

## Decision

### 1. Core concept

A **task chain** is an ordered sequence of tasks where each task is blocked until its predecessor completes. The predecessor's result is automatically injected into the successor's context.

### 2. Schema changes

Add to `agent_task`:

| Column | Type | Purpose |
|--------|------|---------|
| `after_task_id` | TEXT | Predecessor task; this task is blocked until it completes |
| `chain_id` | TEXT | Groups tasks belonging to the same chain |
| `chain_order` | INTEGER | Display/execution order within the chain |

### 3. New status: `blocked`

Tasks with `after_task_id` set start as `status='blocked'`. The worker's `claim_next_task` filters `status IN ('pending', 'retry_scheduled')`, so blocked tasks are excluded without changing the claim query.

### 4. Unblocking flow

When a task completes (`task.completed` event):

- Find successors: `WHERE after_task_id = ? AND status = 'blocked'`
- If predecessor `status='done'`: inject result into successor payload, set `status='pending'`
- If predecessor failed/cancelled: cascade failure to all downstream blocked tasks recursively

### 5. Result propagation

When unblocking a successor, store in its payload:

- `predecessor_result`: the predecessor's result (content)
- `predecessor_task_id`: for traceability

The worker's `_build_step_prompt` includes this in the agent's prompt (like `subtask_results`).

### 6. Failure cascade

If a predecessor fails or is cancelled, all downstream blocked tasks are marked `failed`. Future: configurable per-chain policy (e.g., skip-on-failure).

### 7. New tools

**`submit_chain(steps, priority?, output_channel?)`** — creates N tasks: first `pending`, rest `blocked` with `after_task_id` chaining. All share `chain_id`.

**`get_chain_status(chain_id)`** — returns all tasks in a chain with status; overall status derived from individual statuses.

**`submit_task`** — extended with optional `after_task_id` for ad-hoc chaining.

### 8. Cancellation cascade

When `cancel_task` is called, all downstream blocked tasks (via `after_task_id` chain) are also cancelled.

### 9. What does not change

- Parent-child subtask mechanism (`parent_id`, `try_resume_parent`)
- Worker claim logic
- AgentRegistry and delegation tools
- Existing tool interfaces (backward-compatible extension of `submit_task`)

## Consequences

### Positive

- Sequential pipelines: research → draft → review workflows
- Automatic result propagation between steps
- Orchestrator can use `submit_chain` for multi-phase work
- Minimal schema and code changes; builds on existing event-driven flow

### Trade-offs

- Single predecessor only (no DAG); future enhancement could add multi-predecessor
- Failure cascade is strict (all downstream fail); no skip-on-failure in MVP

### Migration

- Existing tasks unchanged; new columns nullable
- Schema migration via idempotent `ALTER TABLE ... ADD COLUMN` (handles existing DBs)

## References

- ADR 017: Agent Registry and Dynamic Delegation (Phase 3 Task Chains)
- [docs/task_engine.md](../task_engine.md) — task engine documentation
