# Configuration

Application settings are loaded from `config/settings.yaml`. Secrets (API keys) come from environment variables or `.env`. This document describes all configurable sections.

---

## File Location

- **Path:** `config/settings.yaml` (relative to project root)
- **Format:** YAML
- **Merge:** Values are deep-merged over built-in defaults in `core/settings.py`

---

## Sections

### supervisor

Controls the Supervisor process (when running via `python -m supervisor`).

| Key | Default | Description |
|-----|---------|-------------|
| `restart_file` | `sandbox/.restart_requested` | Path to file. When present, Supervisor restarts the agent. |
| `restart_file_check_interval` | 5 | Seconds between restart-file polls. |

**Environment:** `SUPERVISOR_MAX_RESTARTS` (default 5), `SUPERVISOR_RESTART_WINDOW_MINUTES` (default 5) — limit crash restarts within the window.

---

### agents

LLM model configuration per agent. See [llm.md](llm.md) for details.

| Key | Description |
|-----|-------------|
| `default` | Fallback when agent has no explicit config |
| `orchestrator` | Main agent |
| `agents.<id>.provider` | Provider ID from `providers` |
| `agents.<id>.model` | Model identifier |
| `agents.<id>.instructions` | Path to prompt file or literal string |
| `agents.<id>.temperature` | Optional (default 0.7) |
| `agents.<id>.max_tokens` | Optional |
| `agents.<id>.extra` | Optional dict of extra kwargs passed to the provider |

Extensions register per-agent model configs via manifest `agent_config` block. Loader calls `model_router.register_agent_config()` for each entry.

Built-in defaults (`core/settings.py`): orchestrator → `provider: openai, model: gpt-5`. The YAML values deep-merge over these.

---

### providers

LLM API endpoints and credentials. See [llm.md](llm.md).

| Key | Description |
|-----|-------------|
| `providers.<id>.type` | `openai_compatible` (default) or `anthropic` |
| `providers.<id>.base_url` | API base URL |
| `providers.<id>.api_key_secret` | Env var name for API key |
| `providers.<id>.api_key_literal` | Literal key (dev/local) |
| `providers.<id>.default_headers` | Extra HTTP headers |
| `providers.<id>.supports_hosted_tools` | Whether hosted tools supported (default true) |

---

### event_bus

Event Bus storage and dispatch. See [event_bus.md](event_bus.md).

| Key | Default | Description |
|-----|---------|-------------|
| `db_path` | `sandbox/data/event_journal.db` | SQLite path (relative to project root) |
| `poll_interval` | 5.0 | Dispatch loop wait timeout (seconds) |
| `batch_size` | 3 | Max pending events per loop iteration |

---

### logging

Logging configuration.

| Key | Default | Description |
|-----|---------|-------------|
| `file` | `sandbox/logs/app.log` | Log file path |
| `level` | `INFO` | Log level |
| `log_to_console` | false | Also print to stderr |
| `max_bytes` | 10485760 | Rotating file handler max size (10 MB) |
| `backup_count` | 3 | Number of backup files |

---

## Secrets

API keys and tokens are **not** stored in `settings.yaml`. Use:

1. **Environment variables** — Set in shell or `.env` (loaded by `python-dotenv` at startup)
2. **api_key_secret** — References env var name, e.g. `OPENAI_API_KEY`
3. **api_key_literal** — For local/dev only; avoid in production

Example `.env`:

```
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
OPENROUTER_API_KEY=sk-or-...
```

---

## Extension Config

Extensions receive config from manifest `config` block:

```yaml
# sandbox/extensions/my_ext/manifest.yaml
config:
  tick_interval: 30
  namespace: my_app
```

Access via `context.get_config("tick_interval", 30)`.

---

## Data Directories

Extensions get `data_dir = sandbox/data/<extension_id>/`. Created on first access. Examples:

- `sandbox/data/memory/memory.db`
- `sandbox/data/scheduler/scheduler.db`
- `sandbox/data/kv/values.json`
- `sandbox/data/event_journal.db` (Event Bus, from `event_bus.db_path`)

---

## References

- [llm.md](llm.md) — Agents and providers
- [event_bus.md](event_bus.md) — Event Bus config
- [extensions.md](extensions.md) — Extension config block
