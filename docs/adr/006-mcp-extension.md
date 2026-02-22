# ADR 006: MCP Extension — Model Context Protocol Bridge

## Status

Proposed

## Context

The [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) is an open standard that allows applications to connect to MCP servers exposing three primitives: **Tools**, **Resources**, and **Prompts**. MCP servers can be external processes (stdio), HTTP endpoints (SSE, Streamable HTTP), or hosted by OpenAI. The ecosystem includes hundreds of servers: web search, GitHub, filesystem, databases, browser automation, documentation search, and more.

The [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/mcp/) supports MCP natively: agents accept an `mcp_servers` parameter, and the SDK automatically calls `list_tools()` before each `Runner.run()`, converts MCP tool schemas, and invokes tools. Manual wrapping of MCP tools as `@function_tool` would reimplement this and lose SDK features (caching, filtering, approval, reconnection, tracing).

To leverage the MCP ecosystem in assistant4 we need a **bridge extension** that:

1. Connects to one or more MCP servers from config
2. Exposes SDK server instances to the Orchestrator via **native `Agent(mcp_servers=[...])`**
3. Optionally exposes MCP Prompts as context (Phase 2)

This aligns with the nano-kernel principle: **all functionality in extensions**. The MCP extension is a standard extension; the kernel gains only a small duck-typed integration point (`get_mcp_servers()`).

### Problems solved

| Problem | Solution |
|---------|----------|
| **Tool explosion per MCP** | One extension manages N servers; no new extension per server |
| **Heterogeneous transports** | Support stdio, Streamable HTTP (recommended), SSE (legacy), Hosted MCP via SDK classes |
| **Schema conversion, caching, approval** | Use SDK built-in: `convert_schemas_to_strict`, `cache_tools_list`, `require_approval` |
| **Reconnection and resilience** | Use SDK `MCPServerManager` with `reconnect(failed_only=True)`, `drop_failed_servers` |
| **Security** | stdio servers run as subprocess; HTTP servers use configured URLs; approval flow for dangerous tools |

## Decision

### 1. Native SDK passthrough (no ToolProvider wrapping)

The extension **does not** implement `ToolProvider`. It creates SDK server objects (`MCPServerStdio`, `MCPServerStreamableHttp`, etc.) from manifest config and exposes them so the Orchestrator passes them to `Agent(mcp_servers=[...])`. The SDK then:

- Lists tools automatically before each run
- Converts MCP schemas to strict JSON Schema when requested
- Handles tool invocation, retries, and error formatting
- Supports tool filtering, approval policies, and caching

Manual wrapping would lose all of this and create a brittle reimplementation.

### 2. Extension identity and role

| Field | Value |
|-------|-------|
| **id** | `mcp` |
| **Location** | `sandbox/extensions/mcp/` |
| **Protocols** | `ServiceProvider` (manages MCP server lifecycle) |
| **Integration** | Duck-typed `get_mcp_servers() -> list`; no new protocol in contract.py |
| **Dependencies** | None — standalone bridge |

The extension implements `ServiceProvider`: it uses `run_background()` only to satisfy the protocol; the real work is in `start()` (enter `MCPServerManager`) and `stop()` (exit manager). It also exposes `get_mcp_servers()` so the Loader can collect servers and pass them to the Orchestrator.

### 3. Four transports (SDK)

| Transport | SDK class | When to use |
|-----------|-----------|-------------|
| **Streamable HTTP** | `MCPServerStreamableHttp` | **Recommended** for local or remote HTTP servers under your control |
| **stdio** | `MCPServerStdio` | Local subprocess (npx, python, uv) |
| **SSE** | `MCPServerSse` | Legacy; only for servers that do not support Streamable HTTP |
| **Hosted MCP** | `HostedMCPTool` (in `tools`, not `mcp_servers`) | Publicly reachable server; calls go through OpenAI Responses API |

For a local standalone app, stdio and Streamable HTTP are the most relevant.

### 4. Kernel integration (minimal)

**No new protocol.** The Loader uses duck-typing: after `start_all()`, it calls `get_mcp_servers()` on any extension that has this method and aggregates the lists.

- **[core/extensions/loader.py](core/extensions/loader.py):** Add `get_mcp_servers() -> list[Any]` that scans active extensions for `get_mcp_servers` and concatenates results.
- **[core/agents/orchestrator.py](core/agents/orchestrator.py):** Add parameter `mcp_servers: list[Any] | None = None` to `create_orchestrator_agent()`; pass to `Agent(..., mcp_servers=mcp_servers or [], mcp_config={"convert_schemas_to_strict": True})`.
- **[core/runner.py](core/runner.py):** Call `loader.get_mcp_servers()` and pass to `create_orchestrator_agent(mcp_servers=...)`.

Agent creation happens **after** `start_all()`, so MCP servers are already connected when the Orchestrator is built.

### 5. Extension lifecycle: MCPServerManager

MCP server instances in the SDK are async context managers. The extension uses `MCPServerManager` to connect multiple servers and expose only the successful ones:

```python
async def start(self) -> None:
    # Build list of MCPServerStdio / MCPServerStreamableHttp from config.servers
    self._manager = MCPServerManager(
        self._servers,
        drop_failed_servers=True,
        connect_timeout_seconds=10,
    )
    await self._manager.__aenter__()

def get_mcp_servers(self) -> list:
    return self._manager.active_servers

async def stop(self) -> None:
    await self._manager.__aexit__(None, None, None)
```

`run_background()` can be a no-op or a loop that periodically calls `manager.reconnect(failed_only=True)` for Phase 2 resilience.

### 6. Manifest: `config.servers`

Servers are configured under the existing `config` block. No new top-level manifest keys.

```yaml
# sandbox/extensions/mcp/manifest.yaml

id: mcp
name: MCP Bridge
version: "1.0.0"
description: >
  Connects to external MCP servers and exposes their tools to the agent.
  Supports stdio, Streamable HTTP, and SSE transports.

entrypoint: main:McpBridgeExtension

depends_on: []

config:
  servers:
    - alias: filesystem
      transport: stdio
      command: npx
      args: ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/allowed/dir"]
      cache_tools: true
      tool_filter: ["read_file", "list_directory"]

    - alias: web_search
      transport: streamable-http
      url: http://localhost:8000/mcp
      cache_tools: true

    - alias: github
      transport: streamable-http
      url: http://localhost:3000/mcp
      headers:
        Authorization: "Bearer ${GITHUB_TOKEN}"
      cache_tools: true
      require_approval:
        always: ["delete_repository"]

enabled: true
```

#### Server config fields

| Field | Required | Description |
|-------|----------|--------------|
| `alias` | Yes | Display name for logs and SDK server `name`. Unique per extension. |
| `transport` | Yes | `stdio` \| `streamable-http` \| `sse` |
| `command` | For stdio | Executable (e.g. `npx`, `uv`, `python`) |
| `args` | For stdio | List of arguments |
| `url` | For HTTP | Endpoint URL |
| `headers` | No | HTTP headers; `${SECRET_NAME}` resolved via `context.get_secret()` |
| `env` | No | Environment for stdio subprocess; `${SECRET_NAME}` resolved |
| `cache_tools` | No | If true, set SDK `cache_tools_list=True` to avoid repeated `list_tools()` per run |
| `tool_filter` | No | List of allowed tool names; maps to SDK `create_static_tool_filter(allowed_tool_names=[...])` |
| `require_approval` | No | Map `always` / `never` to tool name lists; maps to SDK `require_approval` |

**Secret resolution:** Values containing `${NAME}` are replaced with `context.get_secret("NAME")`. If missing, that server is skipped at startup (log warning).

### 7. Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         MCP Bridge Extension                             │
│                                                                          │
│  initialize()  ──►  Parse config.servers, resolve secrets               │
│  start()       ──►  MCPServerManager.__aenter__()  →  active_servers      │
│  get_mcp_servers()  ──►  return manager.active_servers                  │
│  stop()        ──►  MCPServerManager.__aexit__()                          │
└─────────────────────────────────────────────────────────────────────────┘
                              │
                              │ SDK server instances
                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  Loader.get_mcp_servers()  ──►  create_orchestrator_agent(mcp_servers=…) │
│  Agent(mcp_servers=[...], mcp_config={...})                              │
│  Runner.run(agent, prompt)  ──►  SDK calls list_tools() / call_tool()    │
└─────────────────────────────────────────────────────────────────────────┘
                              │
                              │ MCP protocol (JSON-RPC)
                              ▼
┌──────────────────┐  ┌──────────────────┐
│  MCP Server       │  │  MCP Server      │
│  (stdio)          │  │  (Streamable HTTP)│
└──────────────────┘  └──────────────────┘
```

### 8. SDK features used (no custom reimplementation)

| Feature | SDK support |
|---------|-------------|
| Tool list caching | `cache_tools_list=True`; invalidate with `server.invalidate_tools_cache()` |
| Tool filtering | `tool_filter=create_static_tool_filter(allowed_tool_names=[...])` or dynamic callable |
| Approval flow | `require_approval={"always": {"tool_names": ["delete_file"]}}`; optional `on_approval_request` callback |
| Schema conversion | `mcp_config={"convert_schemas_to_strict": True}` |
| Graceful degradation | `MCPServerManager(drop_failed_servers=True)`; agent sees only `active_servers` |
| Reconnection | `manager.reconnect(failed_only=True)` (e.g. in health loop) |
| MCP Prompts | `server.get_prompt(name, args)` — usable from ContextProvider in Phase 2 |

### 9. Error handling

| Scenario | Behavior |
|----------|----------|
| Server unreachable at `start()` | Manager marks it failed; `active_servers` excludes it. Extension stays healthy. |
| Tool call fails at runtime | SDK returns error to model (or raises if `failure_error_function` set); agent can retry or report. |
| Invalid config | Fail `initialize()` with clear error; extension does not load. |
| Secret missing | Skip that server at `start()`, log which secret is missing. |

### 10. Security

| Concern | Mitigation |
|---------|------------|
| stdio subprocess | Runs with same privileges as kernel. User controls `command`/`args`. Document trust. |
| HTTP | Only configured URLs; no automatic discovery. |
| Secrets | Resolve via `context.get_secret()`; never log resolved values. |
| Dangerous tools | Use `require_approval`; approval callback can publish to EventBus for user confirmation (Phase 2). |

### 11. Full manifest example

```yaml
id: mcp
name: MCP Bridge
version: "1.0.0"
description: >
  Connects to MCP servers and exposes their tools to the agent.
  Configure servers in config.servers. Use stdio or streamable-http.

entrypoint: main:McpBridgeExtension

depends_on: []

config:
  servers: []

setup_instructions: |
  Add entries to config.servers: alias, transport (stdio | streamable-http),
  and for stdio: command, args; for HTTP: url. Optional: cache_tools,
  tool_filter, require_approval. Use ${SECRET_NAME} in url/headers/env.

enabled: true
```

## Implementation Plan

### Phase 1: MVP

1. **Kernel:** Add `get_mcp_servers()` to Loader (duck-typed). Add `mcp_servers` (and `mcp_config`) to `create_orchestrator_agent()`. In runner, pass `loader.get_mcp_servers()` into `create_orchestrator_agent()`. Ensure agent is created after `start_all()` so MCP servers are connected.
2. **Extension:** Create `sandbox/extensions/mcp/` with `manifest.yaml` and `main.py`. Implement `McpBridgeExtension`: Extension + ServiceProvider; in `start()` build SDK server list from `config.servers`, enter `MCPServerManager`, implement `get_mcp_servers()` returning `manager.active_servers`. Support `stdio` and `streamable-http`; resolve secrets; map `cache_tools`, `tool_filter`, `require_approval` to SDK params.
3. **Dependencies:** Add OpenAI Agents SDK dependency if not already present (MCP classes live in `agents.mcp`).
4. **Docs:** Update `docs/extensions.md` with MCP extension and example server configs.

### Phase 2: Prompts and approval

1. **MCP Prompts:** Implement `ContextProvider` in the MCP extension (or a separate one) that calls `server.get_prompt(name, args)` and injects result into agent context.
2. **Approval flow:** Wire `require_approval` + `on_approval_request` to EventBus or channel so user can confirm dangerous tools.
3. **Health and reconnect:** In `run_background()` or health_check, call `manager.reconnect(failed_only=True)` and optionally emit events for observability.

### Phase 3: Observability and agent-extensions

1. **Observability:** Emit EventBus events for MCP tool calls (e.g. `mcp.tool_called`, `mcp.tool_failed`).
2. **Agent-extensions:** If needed, allow agent-extensions to declare `uses_mcp` so sub-agents get a subset of MCP servers (design TBD).
3. **Hosted MCP:** Document or support `HostedMCPTool` for publicly reachable servers (added to `tools`, not `mcp_servers`).

## Consequences

### Benefits

- **Native SDK path:** No reimplementation of listing, invocation, schema conversion, caching, or approval.
- **Extensibility without code:** Users add MCP servers by editing manifest YAML.
- **Minimal kernel surface:** No new protocol; one duck-typed method and three call sites.
- **Resilience and UX from SDK:** Reconnection, tool filter, approval, and tracing come from the SDK.

### Trade-offs

| Trade-off | Impact |
|-----------|--------|
| **Kernel awareness of MCP** | Orchestrator and runner must pass `mcp_servers`; loader must expose `get_mcp_servers()`. Small, localized change. |
| **Tool count** | Many servers → many tools in one agent. Mitigate with `tool_filter` per server. |
| **Creation order** | Agent must be created after `start_all()` so MCP servers are connected. Current runner already does this. |

### Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| **SDK API drift** | Low | Pin SDK version; follow OpenAI Agents SDK MCP docs. |
| **Manager lifecycle** | Low | Ensure `stop()` always calls `__aexit__` so connections close on shutdown. |

## Alternatives Considered

### ToolProvider + manual wrapping (original ADR)

**Rejected.** Would reimplement what the SDK already does: `list_tools()` before each run, schema conversion, tool invocation, retries. Would also lose `cache_tools_list`, `tool_filter`, `require_approval`, `MCPServerManager.reconnect()`, and tracing. Manual wrappers would be brittle and incomplete.

### New protocol `MCPProvider` in contract.py

**Rejected.** Only one extension is expected to provide MCP servers. Adding a 7th protocol for a single implementation violates YAGNI. Duck-typing in Loader is sufficient; a protocol can be introduced later if multiple extensions begin providing servers.

### MCP config in manifest root (`mcp_servers:`)

**Rejected.** Keeps manifest schema consistent: all extension-specific config lives under `config`. Server list stays in `config.servers`.

### One extension per MCP server

**Rejected.** Config-driven list in one extension is simpler; user edits YAML, no new extension per server.

## Relation to Other ADRs

- **ADR 002** — MCP extension implements `ServiceProvider`; Loader wires it. Tools reach the agent via `mcp_servers`, not `get_tools()`.
- **ADR 003** — Orchestrator receives MCP tools natively. Future: agent-extensions could receive a subset via `uses_mcp` (Phase 3).
- **ADR 004** — Phase 2/3 approval and observability can use EventBus.
- **ADR 005** — Memory is unrelated; MCP could provide an alternative memory backend (out of scope).

## References

- [Model Context Protocol](https://modelcontextprotocol.io/)
- [OpenAI Agents SDK — MCP](https://openai.github.io/openai-agents-python/mcp/)
- [OpenAI Agents SDK — MCP Server Reference](https://openai.github.io/openai-agents-python/ref/mcp/server/)
- [MCP Python SDK](https://modelcontextprotocol.github.io/python-sdk/) (client/transport layer; SDK server classes used here are from OpenAI Agents SDK)
- ADR 002: Nano-Kernel + Extensions
- ADR 003: Agent-as-Extension
