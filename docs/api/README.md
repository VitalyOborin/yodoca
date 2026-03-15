# Web Channel API Specification

OpenAPI 3.1 specification for the Yodoca Web Channel HTTP API. The spec is split by domain for maintainability.

## Structure

```
docs/api/
├── openapi.yaml   # Main entry point (references all parts)
├── README.md                  # This file
├── components/
│   └── common.yaml            # Security schemes, parameters, shared responses
├── paths/
│   ├── openai.yaml            # /v1/models, /v1/chat/completions, /v1/responses
│   ├── ag-ui.yaml             # /agent (AG-UI protocol)
│   ├── system.yaml            # /api/health
│   ├── threads.yaml           # /api/threads, /api/threads/{thread_id}
│   ├── projects.yaml          # /api/projects, /api/projects/{project_id}
│   ├── notifications.yaml    # /api/notifications
│   └── schedules.yaml         # /api/schedules, /api/schedules/once, etc.
└── schemas/
    ├── common.yaml            # ErrorResponse, OperationResult, HealthResponse
    ├── openai.yaml            # Models, ChatCompletions, Responses, Usage
    ├── ag-ui.yaml             # AgUIRunRequest
    ├── threads.yaml           # Thread, ThreadDetailResponse, Create/Update
    ├── projects.yaml          # Project, CreateProjectRequest, UpdateProjectRequest
    ├── notifications.yaml    # Notification, NotificationsResponse
    └── schedules.yaml         # ScheduleItem, CreateOnce/Recurring, Update
```

## Usage

- **Entry point:** `openapi.yaml` — use this file when loading the spec. All `$ref` paths are relative to it.
- **Adding endpoints:** Add paths to the appropriate `paths/*.yaml` file and schemas to `schemas/*.yaml`. Update `openapi.yaml` with new `$ref` entries.
- **Tools:** Most OpenAPI tools (Swagger UI, Redoc, openapi-generator) support multi-file specs via `$ref`. If a tool requires a single file, use a bundler (e.g. `@redocly/cli bundle` or `openapi-bundler`).

## References

- [ADR 026](../adr/026-web-channel.md) — Web Channel architectural decision
- [channels.md](../channels.md) — Channel documentation
