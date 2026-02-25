# Application configuration

Configuration is stored in `config/settings.yaml`. The file is optional: missing keys fall back to built-in defaults. Values from the file are **deep-merged** over defaults, so you can override only the keys you need.

---

## File location and loading

- **Path:** `config/settings.yaml` (relative to project root).
- **Format:** YAML.
- **Loading:** On startup the app loads the file and merges it with internal defaults. Later changes require an app restart (or use the supervisor restart file).
- **Example template:** `config/settings.example.yaml` can be copied to `config/settings.yaml` and edited.

---

## Structure of `config/settings.yaml`

### `agents`

Agent definitions used by the orchestrator and other components. Each entry can specify `provider`, `model`, and optionally `instructions` (path to a Jinja2 prompt file).

| Key | Description |
|-----|-------------|
| `agents.default` | Default agent: provider, model, and instructions used when no specific agent is requested. |
| `agents.orchestrator` | Orchestrator agent. If omitted, the app falls back to `agents.default`. |

Example:

```yaml
agents:
  default:
    instructions: prompts/default.jinja2
    model: gpt-5.2
    provider: openai
  orchestrator:
    instructions: prompts/default.jinja2
    model: gpt-5.2
    provider: openai
```

### `providers`

LLM and API providers (OpenAI, Anthropic, OpenRouter, local, etc.). Each provider has a `type` and usually an `api_key_secret` (environment variable name). Secrets are read from the environment or keyring.

Example:

```yaml
providers:
  openai:
    type: openai_compatible
    api_key_secret: OPENAI_API_KEY
  anthropic:
    type: anthropic
    api_key_secret: ANTHROPIC_API_KEY
```

### `event_bus`

Durable event journal and polling.

| Key | Default | Description |
|-----|---------|-------------|
| `db_path` | `sandbox/data/event_journal.db` | SQLite path for the event journal. |
| `poll_interval` | `5.0` | Poll interval in seconds. |
| `batch_size` | `3` | Batch size for processing. |
| `max_retries` | `3` | Max retries per event. |

### `logging`

| Key | Default | Description |
|-----|---------|-------------|
| `file` | `sandbox/logs/app.log` | Log file path. |
| `level` | `INFO` | Log level. |
| `log_to_console` | `false` | Whether to duplicate logs to stderr. |
| `max_bytes` | `10485760` | Max size per log file (bytes). |
| `backup_count` | `3` | Number of rotated backup files. |

### `session`

| Key | Default | Description |
|-----|---------|-------------|
| `timeout_sec` | `1800` | Session timeout in seconds. |

### `supervisor`

| Key | Default | Description |
|-----|---------|-------------|
| `restart_file` | `sandbox/.restart_requested` | When this file exists, the app restarts. |
| `restart_file_check_interval` | `5` | How often to check the restart file (seconds). |

### `extensions`

Per-extension overrides. Keys are extension IDs (e.g. `embedding`, `telegram_channel`). Values are merged over each extension’s **manifest** `config` (see [Extension config priority](#extension-config-priority)).

Example:

```yaml
extensions:
  embedding:
    default_model: text-embedding-3-large
    provider: openai
  telegram_channel:
    bot_token_secret: TELEGRAM_BOT_TOKEN
```

---

## Extension config priority

Extension configuration comes from two places:

1. **Manifest** — `sandbox/extensions/<id>/manifest.yaml`, section `config:` (defaults for the extension).
2. **Application config** — `config/settings.yaml`, section `extensions.<id>` (overrides).

**Priority: values in `config/settings.yaml` override the same keys in the extension manifest.**

Resolution order when an extension calls `context.get_config(key, default)`:

1. **`settings.yaml`** → `extensions.<extension_id>.<key>`
2. If absent → **manifest** → `config.<key>`
3. If absent → **`default`** argument of `get_config()`

So the manifest defines default and optional parameters; the app config is used to override them per deployment (e.g. another model, another provider).

Example: embedding extension has in its manifest `config.default_model: text-embedding-3-large` and `config.provider: null`. If in `settings.yaml` you set:

```yaml
extensions:
  embedding:
    provider: openai
    default_model: text-embedding-3-small
```

then at runtime the extension sees `provider: openai` and `default_model: text-embedding-3-small`; manifest values for those keys are ignored.

---

## Examples

### Minimal (defaults only)

Use built-in defaults; create `config/settings.yaml` only if you need to set providers or secrets. Providers and API keys can also be configured via onboarding, which writes `config/settings.yaml` and `.env`.

### After onboarding

Typical content after running the setup wizard:

```yaml
agents:
  default:
    instructions: prompts/default.jinja2
    model: gpt-5.2
    provider: openai
  orchestrator:
    instructions: prompts/default.jinja2
    model: gpt-5.2
    provider: openai

providers:
  openai:
    api_key_secret: OPENAI_API_KEY
    type: openai_compatible

extensions:
  embedding:
    default_model: text-embedding-3-large
    provider: openai
```

(Other sections such as `event_bus`, `logging`, `session`, `supervisor` keep their defaults if omitted.)

### Override only logging and session

You can leave agents and providers as defaults and override a few keys:

```yaml
logging:
  level: DEBUG
  log_to_console: true

session:
  timeout_sec: 3600
```

---

## See also

- [Extensions](extensions.md) — extension contract and `get_config` usage.
- [Secrets](secrets.md) — API keys and keyring.
- Onboarding flow — creates initial `config/settings.yaml` and `.env`.
