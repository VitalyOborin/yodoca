# ADR 006: MCP Extension — Model Context Protocol Bridge

## Status

Proposed

## Context

The [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) is an open standard that allows applications to connect to MCP servers exposing three primitives: **Tools**, **Resources**, and **Prompts**. MCP servers can be external processes (stdio), HTTP endpoints (SSE, Streamable HTTP), or embedded. The ecosystem includes hundreds of servers: web search, GitHub, filesystem, databases, browser automation, documentation search, and more.

Currently, assistant4 enriches the agent via extensions that implement `ToolProvider`. Each tool extension is hand-written. To leverage the growing MCP ecosystem — and enable users to connect **any** MCP server without writing custom code — we need a bridge extension that:

1. Connects to one or more MCP servers
2. Exposes their tools as agent tools (via `ToolProvider`)
3. Optionally exposes resources and prompts (Phase 2)

This aligns with the nano-kernel principle: **all functionality in extensions**. The MCP extension is a standard extension, not kernel code. It uses the official [MCP Python SDK](https://modelcontextprotocol.github.io/python-sdk/) for client connectivity.

### Problems solved

| Problem | Solution |
|---------|----------|
| **Tool explosion per MCP** | One extension manages N servers; no new extension per server |
| **Heterogeneous transports** | Support stdio, SSE, Streamable HTTP via SDK |
| **Tool naming collisions** | Prefix tools with server alias: `mcp_<alias>_<tool_name>` |
| **Dynamic tool discovery** | Tools fetched at runtime from each server; no code generation |
| **Security** | stdio servers run as subprocess; HTTP servers use configured URLs |

## Decision

### 1. Extension Identity and Role

| Field | Value |
|-------|-------|
| **id** | `mcp` |
| **Location** | `sandbox/extensions/mcp/` |
| **Protocols** | `ToolProvider` (Phase 1), optionally `ContextProvider` (Phase 2 for Resources) |
| **Dependencies** | None — standalone bridge |

The extension implements `ToolProvider`: `get_tools()` returns a list of `@function_tool` objects. Each tool is a wrapper that forwards the call to the corresponding MCP server via the SDK client.

### 2. Manifest: `config.servers`

MCP servers are configured in the manifest `config` block. No new manifest schema — we use the existing `config` dict.

```yaml
# sandbox/extensions/mcp/manifest.yaml

id: mcp
name: MCP Bridge
version: "1.0.0"
description: >
  Connects to MCP servers and exposes their tools to the agent.
  Supports stdio, SSE, and Streamable HTTP transports.

entrypoint: main:McpBridgeExtension

depends_on: []

config:
  servers:
    - alias: web_search
      transport: streamable-http
      url: http://localhost:8000/mcp

    - alias: filesystem
      transport: stdio
      command: npx
      args: ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/allowed/dir"]

    - alias: github
      transport: sse
      url: https://api.example.com/mcp
      headers:
        Authorization: "Bearer ${GITHUB_TOKEN}"  # resolved from secrets

enabled: true
```

#### Server config fields

| Field | Required | Description |
|-------|----------|--------------|
| `alias` | Yes | Short identifier for tool prefix and logs. Must be unique per extension instance. |
| `transport` | Yes | `stdio` \| `sse` \| `streamable-http` |
| `command` | For stdio | Executable (e.g. `npx`, `uv`, `python`) |
| `args` | For stdio | List of arguments (e.g. `["-y", "@modelcontextprotocol/server-filesystem", "/path"]`) |
| `url` | For sse/streamable-http | Endpoint URL |
| `headers` | No | HTTP headers; values like `${SECRET_NAME}` resolved via `context.get_secret()` |
| `env` | No | Environment variables for stdio subprocess; `${SECRET_NAME}` resolved |

**Secret resolution:** Any value containing `${NAME}` is replaced with `context.get_secret("NAME")`. If the secret is missing, the server is skipped at startup (logged as warning).

### 3. Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         MCP Bridge Extension                             │
│                                                                          │
│  initialize()                                                            │
│    ├── For each server in config.servers:                                │
│    │     ├── Resolve secrets in url/headers/env                         │
│    │     ├── Create MCP client (stdio | sse | streamable-http)           │
│    │     └── Connect, list tools via SDK                                │
│    └── Build wrapper tools: mcp_<alias>_<tool_name>                     │
│                                                                          │
│  get_tools()  ──►  [mcp_web_search_search, mcp_filesystem_read_file, …]  │
│                                                                          │
│  start()  ──►  Keep connections alive (or lazy connect on first call)   │
│  stop()   ──►  Disconnect all clients                                   │
└─────────────────────────────────────────────────────────────────────────┘
                              │
                              │ MCP protocol (JSON-RPC over transport)
                              ▼
┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
│  MCP Server A     │  │  MCP Server B     │  │  MCP Server C     │
│  (stdio)          │  │  (SSE)            │  │  (Streamable HTTP) │
│  filesystem       │  │  web search       │  │  custom API       │
└──────────────────┘  └──────────────────┘  └──────────────────┘
```

### 4. Tool Wrapping Strategy

MCP tools have a `name` and `inputSchema` (JSON Schema). The OpenAI Agents SDK expects `@function_tool` with a function that accepts kwargs. We need to:

1. **Fetch tool list** from each server at `initialize()` via `client.list_tools()`
2. **Build dynamic tools** — one `@function_tool` per MCP tool
3. **Naming:** `mcp_<alias>_<tool_name>` — e.g. `mcp_web_search_search`, `mcp_filesystem_read_file`
4. **Invocation:** On agent call, `client.call_tool(tool_name, arguments)` and return the result

The MCP SDK provides `ClientSession` with `list_tools()` and `call_tool()`. We wrap each in an async function and use `agents.function_tool` (or equivalent) with a dynamic schema derived from `inputSchema`.

**Lazy vs eager connection:** Phase 1 uses **eager** — connect at `start()`, fail fast if a server is unreachable. Alternative: lazy connect on first tool call (deferred to implementation).

### 5. Connection Lifecycle

| Phase | Action |
|-------|--------|
| `initialize()` | Parse config, resolve secrets, validate server entries. Do **not** connect yet — `get_tools()` may be called before `start()`. |
| `start()` | For each server: create client, connect, fetch tool list. Build wrapper tools. If a server fails, log error, skip it; other servers still work. |
| `get_tools()` | Return cached wrapper tools (built in `start()`). If called before `start()`, return empty list (Loader calls `get_tools()` after `start()` in current flow; verify). |
| `stop()` | Disconnect all clients, cancel in-flight calls. |
| `health_check()` | Ping each connected server (e.g. `list_tools` or a lightweight RPC). Return `False` if any server is down. |

**Reconnection:** If a server disconnects mid-session, the next tool call will fail. Phase 1 does not auto-reconnect; the user restarts the app. Phase 2 could add exponential backoff reconnect.

### 6. Error Handling

| Scenario | Behavior |
|----------|----------|
| Server unreachable at `start()` | Log warning, exclude that server's tools. Extension stays healthy with remaining servers. |
| Tool call fails (timeout, connection lost) | Return error to agent as tool result; agent can retry or report to user. |
| Invalid config (missing alias, unknown transport) | Fail `initialize()` with clear error; extension does not load. |
| Secret missing | Skip that server at `start()`, log which secret is missing. |

### 7. Security Considerations

| Concern | Mitigation |
|---------|------------|
| **stdio subprocess** | Server runs with same privileges as the kernel. User configures `command`/`args`; sandbox convention applies. Document that stdio servers are trusted. |
| **HTTP servers** | Only connect to configured URLs. No automatic discovery. |
| **Secrets in config** | Never log resolved secrets. Use `context.get_secret()`; store in `.env`. |
| **Tool argument injection** | MCP SDK handles JSON-RPC encoding. We pass agent-provided arguments as-is; server validates. |

### 8. Dependencies

- **Python package:** `mcp` (official SDK) — add to project `pyproject.toml` or `requirements.txt`
- **No kernel changes** — extension is self-contained; Loader detects `ToolProvider` as usual

### 9. Manifest Example (Full)

```yaml
id: mcp
name: MCP Bridge
version: "1.0.0"
description: >
  Connects to MCP servers and exposes their tools to the agent.
  Use when you need: web search, file access, GitHub, databases, etc.
  Configure servers in config.servers.

entrypoint: main:McpBridgeExtension

depends_on: []

config:
  servers: []

setup_instructions: |
  Add MCP servers to config.servers in manifest.yaml.
  Example (stdio): alias, transport: stdio, command, args.
  Example (HTTP): alias, transport: streamable-http, url.
  Use ${SECRET_NAME} in url/headers/env to inject secrets.

enabled: true
```

### 10. Orchestrator Integration

The Orchestrator receives tools from `loader.get_all_tools()`, which includes the MCP extension's tools. No special handling. The capabilities summary will list tools like `mcp_web_search_search`, `mcp_filesystem_read_file` with descriptions from the MCP server.

**Tool description:** MCP tools include a `description` field. We pass it to the agent. Optionally prefix: `[MCP: web_search] <original description>` for clarity.

## Implementation Plan

### Phase 1: Tools Only (MVP)

1. Create `sandbox/extensions/mcp/` with `manifest.yaml` and `main.py`
2. Implement `McpBridgeExtension`: Extension + ToolProvider
3. Parse `config.servers`, support `stdio` and `streamable-http` (most common)
4. Use MCP SDK `ClientSession` with appropriate transport
5. At `start()`: connect, `list_tools()`, build wrapper `@function_tool` for each
6. Implement `get_tools()` returning the wrappers
7. Tool invocation: `call_tool(name, arguments)` → format result for agent
8. Add `mcp` to project dependencies
9. Document in `docs/extensions.md` and add example server configs

### Phase 2: Resources and Prompts

1. **Resources:** Implement `ContextProvider`. Before each agent invocation, optionally fetch relevant resources (e.g. `file://` or custom URI) and inject into context. Requires mapping "which resources to fetch" — could be manifest-driven or agent-triggered.
2. **Prompts:** Expose MCP prompts as tools (e.g. `mcp_<alias>_get_prompt_<name>`) that return a prompt string for the agent to use, or as a dedicated mechanism. TBD based on MCP prompt semantics.
3. **SSE transport:** Add support if needed for specific servers.

### Phase 3: Resilience and UX

1. **Reconnection:** Auto-reconnect on connection loss with backoff
2. **Health check:** Implement `health_check()` pinging each server
3. **Observability:** Emit `mcp.tool_called`, `mcp.tool_failed` events to EventBus for debugging
4. **Config validation:** Pydantic model for server config; clear errors on invalid YAML

## Consequences

### Benefits

- **Extensibility without code:** Users add new capabilities by editing manifest YAML
- **Ecosystem leverage:** Hundreds of MCP servers become available instantly
- **Consistent with architecture:** Extension, not kernel; protocol detection via `isinstance(ToolProvider)`
- **Transport flexibility:** stdio for local servers, HTTP for remote

### Trade-offs

| Trade-off | Impact |
|-----------|--------|
| **External dependency** | MCP SDK adds ~1 package; acceptable |
| **Connection state** | Extension holds connections; must handle disconnect gracefully |
| **Tool count** | Many servers → many tools; Orchestrator prompt grows. Mitigation: allow `config.enabled_servers` to filter which servers to load, or `config.tool_allowlist` per server |
| **Latency** | Each tool call is a round-trip to MCP server; may add 50–200ms. Acceptable for MVP |

### Risks

| Risk | Severity | Mitigation |
|------|----------|-------------|
| **MCP server crash** | Medium | Catch errors, return structured error to agent; `health_check` marks extension unhealthy |
| **Schema mismatch** | Low | MCP `inputSchema` → OpenAI tool schema conversion; SDK may have helpers; fallback to generic `**kwargs` |
| **Tool name collision** | Low | Prefix `mcp_<alias>_` ensures uniqueness within extension; alias must be unique in config |

## Alternatives Considered

### One extension per MCP server

**Rejected.** Would require generating a new extension for each server (e.g. `mcp_web_search`, `mcp_filesystem`). Config-driven list in one extension is simpler; user edits YAML, no code.

### MCP in kernel

**Rejected.** Violates "all functionality in extensions." Kernel stays minimal.

### Resources as tools only

**Alternative for Phase 2.** Instead of `ContextProvider`, expose `mcp_<alias>_read_resource` tool that fetches a resource by URI. Agent calls it when needed. Simpler but less automatic than context injection.

### Declarative MCP (no entrypoint)

**Deferred.** A future `DeclarativeMcpAdapter` could create the extension from manifest only (like `DeclarativeAgentAdapter`). For now, programmatic extension is clearer and allows custom logic (reconnect, filtering).

## Relation to Other ADRs

- **ADR 002** — MCP extension implements `ToolProvider`; Loader wires it like any other. No new protocols.
- **ADR 003** — Agent-extensions can list `mcp` in `uses_tools` to get MCP tools. Tool isolation works as designed.
- **ADR 004** — Phase 3 observability can emit events to EventBus.
- **ADR 005** — Memory is unrelated; MCP could provide a "memory" MCP server as alternative backend (out of scope).

## References

- [Model Context Protocol — Specification](https://modelcontextprotocol.io/)
- [MCP Python SDK](https://modelcontextprotocol.github.io/python-sdk/)
- [MCP Python SDK — GitHub](https://github.com/modelcontextprotocol/python-sdk)
- [OpenAI Agents SDK — MCP](https://openai.github.io/openai-agents-python/mcp/) — OpenAI's MCP integration patterns
- ADR 002: Nano-Kernel + Extensions
- ADR 003: Agent-as-Extension
