# ADR 030: AG-UI Endpoint in Web Channel

## Status

Accepted. Implemented

## Context

The web channel (ADR 026) provides OpenAI-compatible Chat Completions and Responses API endpoints. These serve LibreChat, LobeChat, Open WebUI, and similar frontends. A separate open standard — **AG-UI (Agent-User Interaction Protocol)** — is emerging as a general-purpose, event-based protocol for connecting AI agents to user-facing applications.

AG-UI is designed for agentic applications that need:

- Streaming chat with typed events
- Tool call visibility (frontend and backend tools)
- Shared state, reasoning steps, and multimodal content
- Interoperability across agent frameworks (LangGraph, CrewAI, OpenAI Agents SDK, etc.)

Adding AG-UI support to the web channel would enable:

- AG-UI-compatible clients (e.g., CopilotKit, Terminal + Agent) to connect to Yodoca
- Alignment with an open, vendor-neutral protocol
- Future extensibility for richer agent features (shared state, reasoning, tool output streaming)

Constraints:

- Reuse existing web channel infrastructure (RequestBridge, StreamingChannelProvider, EventBus)
- No core changes; extension-only implementation
- Follow the same request-response bridging pattern as Chat Completions and Responses API

## Decision

### 1) New endpoint: POST /agent

Add a new endpoint `POST /agent` that implements the AG-UI protocol over SSE. The endpoint:

- Accepts `RunAgentInput`-shaped JSON (threadId, runId, messages, tools, context, state, forwardedProps)
- Returns `text/event-stream` with AG-UI event types
- Uses the same busy guard and RequestBridge as existing streaming endpoints
- Maps AG-UI `threadId` to Yodoca `thread_id` for session continuity

### 2) Event mapping

The existing `StreamingChannelProvider` callbacks are translated to AG-UI events:

| Bridge event | AG-UI event(s) |
|--------------|----------------|
| (start of stream) | `RUN_STARTED` |
| `"start"` | `TEXT_MESSAGE_START` |
| `"chunk"` | `TEXT_MESSAGE_CONTENT` |
| `"status"` (tool call) | `STEP_STARTED`, `STEP_FINISHED` |
| `"end"` | `TEXT_MESSAGE_END` |
| (stream complete) | `RUN_FINISHED` |
| (error/timeout) | `RUN_ERROR` |

### 3) Dependencies and implementation

- Add `ag-ui-protocol>=0.1.13` for canonical Pydantic event types and `EventEncoder` (SSE formatting)
- New route file `routes_agui.py` following the pattern of `routes_openai.py` and `routes_api.py`
- Mount the AG-UI router at root level (no prefix) so the endpoint is `POST /agent`
- Lightweight local Pydantic models for request validation; `ag_ui.core` and `ag_ui.encoder` for event serialization

### 4) OpenAPI specification

Update `docs/api/web-channel-openapi.yaml` with the new path, request/response schemas, and `ag-ui` tag.

## Consequences

### Positive

- Enables AG-UI-compatible clients to connect to Yodoca without custom integration
- Aligns with an open, event-based protocol used by CopilotKit and other agent frameworks
- Reuses existing streaming infrastructure; no core changes
- Minimal implementation surface (single endpoint, one route file)

### Trade-offs

- AG-UI `messages` history is used only to extract the last user message; full history is not forwarded to the model (same as Chat Completions and Responses API)
- Tool call events are approximated via `STEP_STARTED`/`STEP_FINISHED` from `on_stream_status`; full `TOOL_CALL_*` streaming would require kernel changes
- New dependency (`ag-ui-protocol`) increases project footprint

### Implementation notes

- Route handler: `sandbox/extensions/web_channel/routes_agui.py`
- Models: `AgUIRunRequest` and related types in `models.py`
- OpenAPI: `POST /agent` path and schemas in `docs/api/web-channel-openapi.yaml`

