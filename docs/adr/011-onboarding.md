# ADR 011: Onboarding — First-Run Setup Wizard and AI Introduction

## Status

Accepted. Implemented.

## Context

When a new user launches the application for the first time (`uv run python -m supervisor`), the system currently assumes that `config/settings.yaml` and `.env` are already configured with valid provider credentials and model assignments. There is no guided setup: a missing or incomplete config leads to cryptic errors at startup.

The application needs a **first-run onboarding flow** that:

1. **Collects provider credentials.** The user must configure at least one LLM provider before the agent can function. Supported providers differ by auth mechanism:
   - **OpenAI** — requires `OPENAI_API_KEY`
   - **Anthropic** — requires `ANTHROPIC_API_KEY`
   - **OpenRouter** — requires `OPENROUTER_API_KEY` (and optional headers)
   - **Local (LM Studio, Ollama, etc.)** — requires a `base_url` (no secret key, or a placeholder)

2. **Assigns models to agent roles.** The architecture already supports per-agent provider/model overrides via `agents.default`, `agents.orchestrator`, and the `embedding` extension. Different roles may use different providers — for example, `gpt-5` (OpenAI) for the orchestrator, `claude-3.5-haiku` (Anthropic) for a lightweight sub-agent, and a local model for embeddings.

3. **Transitions to an AI-driven introduction.** After the technical setup is complete and the agent is operational, the AI itself should initiate a "getting to know you" conversation — learning the user's name, occupation, interests, timezone, and city. This information becomes part of the agent's long-term memory.

4. **Connects communication channels.** Optionally, the user can set up Telegram, email, or other integrations during or shortly after onboarding.

### Design constraints

- **Single entry point preserved.** `uv run python -m supervisor` remains the only way to start the application. The supervisor becomes a state-machine router: it detects whether config is complete and launches either the onboarding wizard or the core agent.
- **Onboarding is a separate process.** Like `core`, the onboarding module runs as a subprocess under the supervisor. This keeps it isolated and restartable.
- **Config validation is shared.** A single `is_configured()` function is used by both the supervisor (to decide which mode to launch) and the onboarding wizard (to verify its own output).
- **Restart-file contract.** The existing `sandbox/.restart_requested` mechanism is reused: after successful onboarding, the wizard creates this file, and the supervisor picks it up in the next loop iteration — now with a valid config, it launches `core`.
- **Exit-code contract.** The onboarding process communicates its result to the supervisor via exit codes, following the same pattern as any supervised subprocess.

## Decision

### 1. Supervisor as a Mode Router

The supervisor's main loop gains a `determine_mode()` check at the top of each iteration. This is the only behavioral change to the existing supervisor code — approximately 20 lines.

```
┌─────────────────────────────────────────────────────┐
│              uv run python -m supervisor             │
└─────────────────────────────────────────────────────┘
                          │
                    ┌─────▼─────┐
              ┌─────│ determine │─────┐
              │     │  _mode()  │     │
              │     └───────────┘     │
          ONBOARDING               CORE
              │                       │
    ┌─────────▼──────────┐  ┌────────▼────────────────┐
    │ subprocess.run(    │  │ subprocess.Popen(        │
    │   -m onboarding    │  │   -m core                │
    │ )  [blocking]      │  │ )  [non-blocking]        │
    └─────────┬──────────┘  └────────┬────────────────┘
              │                       │
              │ returncode?           │ poll() + restart file
              │                       │
         0 ───┼──► continue          crash ──► backoff → continue
         1 ───┼──► sys.exit()     restart ──► continue
         2 ───┼──► continue (retry)
              │
    [every continue → determine_mode() again]
```

Key distinction: onboarding runs via `subprocess.run()` (blocking, owns stdin) while core runs via `subprocess.Popen()` (non-blocking, polled). This is because the onboarding wizard is interactive and requires exclusive terminal access.

### 2. Config Validation: `is_configured()`

A new shared module `core/config_check.py` provides a single function used by both supervisor and onboarding:

```python
def is_configured(
    settings_path: Path = Path("config/settings.yaml"),
    env_path: Path = Path(".env"),
) -> tuple[bool, str]:
    """
    Check whether config is sufficient to start core.
    Returns (ok, reason).
    """
```

Validation logic:

1. `settings.yaml` must exist and parse without errors.
2. At least one provider must be configured under `providers:`.
3. For each provider, either `api_key_literal` is set (local providers) or `api_key_secret` references an environment variable that is present in `.env` / environment.
4. `agents.default` must reference a configured provider.

This function deliberately checks only what is necessary to *start* the agent. Runtime validation (e.g., whether an API key is actually valid, whether the model exists) is handled by `core` itself — if it fails, the supervisor's crash-restart logic applies, and `determine_mode()` will route back to onboarding if the config becomes invalid.

### 3. Onboarding Exit Code Contract

The onboarding process communicates its result via exit codes:

| Exit code | Constant | Meaning | Supervisor action |
|-----------|----------|---------|-------------------|
| 0 | `ONBOARDING_SUCCESS` | Config written successfully | `continue` (re-check → CORE) |
| 1 | `ONBOARDING_QUIT` | User cancelled (Ctrl+C) | `sys.exit(0)` |
| 2 | `ONBOARDING_RETRY` | Verification failed, retry | `continue` (re-check → ONBOARDING) |

On exit code 0, the onboarding wizard also creates `sandbox/.restart_requested` as a signal. The supervisor clears this file at the top of each iteration, then runs `is_configured()` — if the config is now valid, it proceeds to CORE mode.

### 4. Onboarding Wizard — Phase 1: Provider and Model Setup (TUI)

The wizard is a terminal-based interactive UI (using `questionary` or `prompt_toolkit`). It runs as a standalone module (`python -m onboarding`) and writes directly to `config/settings.yaml` and `.env`.

#### Step 1: Provider Selection

```
Welcome to Yodoca setup!

Which LLM providers will you use? (select all that apply)
  [x] OpenAI (GPT-5, GPT-5-mini, ...)
  [ ] Anthropic (Claude 4, Haiku, ...)
  [ ] OpenRouter (access to multiple providers)
  [x] Local model (LM Studio, Ollama, ...)
```

For each selected provider, the wizard collects the required credentials:

- **OpenAI**: API key → stored as `OPENAI_API_KEY` in `.env`
- **Anthropic**: API key → stored as `ANTHROPIC_API_KEY` in `.env`
- **OpenRouter**: API key → stored as `OPENROUTER_API_KEY` in `.env`
- **Local**: base URL (default `http://127.0.0.1:1234/v1`) → stored in `settings.yaml` under `providers.lm_studio.base_url`

#### Step 2: Model Assignment

```
Now let's assign models to roles.

Default model (used for most tasks):
  Provider: [OpenAI ▾]    Model: [gpt-5-mini        ]

Orchestrator (main reasoning agent):
  Provider: [OpenAI ▾]    Model: [gpt-5             ]

Embeddings:
  Provider: [Local  ▾]    Model: [nomic-embed-text   ]
```

Each role can independently select a provider and model. The wizard populates `agents.default`, `agents.orchestrator`, and embedding config in `settings.yaml`.

#### Step 3: Connection Verification

For each configured provider, the wizard makes a lightweight API call (e.g., list models or a minimal completion) to verify that the credentials work:

```
Verifying connections...
  ✓ OpenAI — connected (gpt-5-mini available)
  ✓ Local (LM Studio) — connected (3 models available)

All providers verified. Writing configuration...
```

If verification fails, the user can re-enter credentials (exit code 2 → retry) or skip verification and proceed.

#### Step 4: Atomic Config Write

The wizard writes configuration atomically:

- `config/settings.yaml` — provider definitions, agent model assignments, all non-secret settings.
- `.env` — API keys and other secrets, appended without overwriting existing entries.

Write is atomic: a temp file is written first, then renamed. If the write fails, the original files are preserved.

After successful write, the wizard creates `sandbox/.restart_requested` and exits with code 0.

### 5. Onboarding Phase 2: AI-Driven Introduction (Extension)

Once the core agent starts for the first time, it needs to "get to know" the user. This is implemented as an **onboarding extension** (`sandbox/extensions/onboarding/`) — a regular extension that activates on the first run.

The extension detects that onboarding-introduction has not been completed (e.g., by checking a KV store flag or the absence of user profile data in memory) and proactively initiates a conversation:

```
Hello! I'm your AI assistant. Since we're meeting for the first time,
I'd like to learn a bit about you so I can be more helpful.

What's your name?
```

The AI-driven introduction gathers:

- User's name and preferred form of address
- Occupation and professional context
- Interests and hobbies
- Timezone and city of residence
- Communication preferences

All gathered information is stored in long-term memory via the existing memory extension. Once complete, the extension sets a flag (e.g., `onboarding.introduction_complete = true` in KV) so it does not re-trigger.

### 6. Onboarding Phase 3: Communication Channel Setup (Extension)

A subsequent step (can be part of the same extension or a separate one) guides the user through connecting communication channels:

- **Telegram**: the extension provides the bot token setup instructions and verifies the connection
- **Email**: SMTP/IMAP configuration
- **Other integrations**: as needed

This phase is optional and can be deferred — the agent is fully functional after Phase 1 + 2.

### 7. Supervisor Changes

The changes to `supervisor/runner.py` are minimal:

```python
from core.config_check import is_configured

ONBOARDING_SUCCESS = 0
ONBOARDING_QUIT = 1
ONBOARDING_RETRY = 2

def main() -> None:
    # ... existing signal setup ...

    while True:
        _RESTART_FILE.unlink(missing_ok=True)

        ok, reason = is_configured()

        if not ok:
            _log(f"Configuration incomplete: {reason}")
            _log("Starting setup wizard...")

            result = subprocess.run(
                [sys.executable, "-m", "onboarding"],
                cwd=str(_PROJECT_ROOT),
            )

            if result.returncode == ONBOARDING_QUIT:
                _log("Setup cancelled. Exiting.")
                sys.exit(0)

            # SUCCESS or RETRY → loop back to determine_mode()
            continue

        # --- existing CORE logic (spawn, poll, crash handling) ---
        child = _spawn_agent()
        # ...
```

### 8. Scenario Walkthroughs

**First launch (no config):**
```
uv run python -m supervisor
  → is_configured() → False ("No providers configured")
  → subprocess.run(-m onboarding)  [blocking, interactive]
     Wizard: select providers → enter keys → assign models → verify → write config
     Creates: sandbox/.restart_requested
     exit(0)
  → Supervisor unblocks, continue
  → .restart_requested cleared, is_configured() → True
  → subprocess.Popen(-m core)
  → Agent starts, onboarding extension initiates AI introduction
```

**Normal launch (config exists and valid):**
```
uv run python -m supervisor
  → is_configured() → True
  → subprocess.Popen(-m core)  # immediate, no onboarding
```

**Broken config (API key removed from .env):**
```
uv run python -m supervisor
  → is_configured() → False ("Providers found but no API keys set")
  → subprocess.run(-m onboarding)  # wizard re-runs
```

**API key valid in .env but expired/invalid at runtime:**
```
uv run python -m supervisor
  → is_configured() → True  (key present, format not checked)
  → subprocess.Popen(-m core)
  → core crashes on first LLM call (auth error)
  → Supervisor detects crash, re-loops
  → is_configured() → True  (key still present)
  → Restarts core (up to MAX_RESTARTS)
```

This last scenario is intentional: `is_configured()` checks presence, not validity. Runtime auth errors are a different class of problem — they could be transient (rate limit, network) or permanent (revoked key). The supervisor's existing crash-backoff handles transient issues. For permanent issues, the user would need to manually fix `.env` or re-run onboarding explicitly (a future CLI flag like `--reconfigure` could force onboarding mode).

## File Structure

```
core/
└── config_check.py          ← NEW: shared is_configured() function

supervisor/
└── runner.py                ← MODIFIED: ~20 lines added for mode routing

onboarding/                  ← NEW: standalone module
├── __init__.py
├── __main__.py              ← entry point, exit codes 0/1/2
├── wizard.py                ← TUI orchestration (questionary/prompt_toolkit)
├── steps/
│   ├── provider_step.py     ← provider selection + credential input
│   ├── models_step.py       ← model-to-role assignment
│   └── verify_step.py       ← async connection verification
├── config_writer.py         ← atomic write of settings.yaml + .env
└── provider_probe.py        ← lightweight API probe per provider

sandbox/extensions/onboarding/   ← Phase 2+3: AI introduction + integrations
├── manifest.yaml
├── main.py
└── prompts/
    └── introduction.jinja2
```

Changes to existing code are confined to **one modified file** (`supervisor/runner.py`) and **one new shared file** (`core/config_check.py`). Everything else is new, standalone modules.

## Implementation Plan

### Phase 1: TUI Wizard + Supervisor Routing

1. **`core/config_check.py`** — implement `is_configured()` with provider/key validation.
2. **`supervisor/runner.py`** — add mode routing: call `is_configured()` at loop top, branch to onboarding or core.
3. **`onboarding/` module** — implement the interactive TUI wizard: provider selection, credential input, model assignment, connection verification, atomic config write.
4. **Dependencies** — add `questionary` (or `prompt_toolkit`) to `pyproject.toml`.

### Phase 2: AI-Driven Introduction

1. **`sandbox/extensions/onboarding/`** — extension that detects first-run state and initiates a "getting to know you" conversation via the agent.
2. **KV flag** — use the KV extension to store `onboarding.introduction_complete` so the introduction doesn't re-trigger.
3. **Memory integration** — store gathered user profile data in long-term memory.

### Phase 3: Communication Channel Setup

1. **Telegram setup flow** — guided bot token configuration and verification.
2. **Email setup flow** — SMTP/IMAP credential collection and test send.
3. **Future integrations** — extensible pattern for adding new channel setup steps.

## Consequences

### Benefits

- **Zero-config first run.** New users are guided through setup immediately; no need to manually edit YAML or `.env` files before first use.
- **Single entry point preserved.** `uv run python -m supervisor` works identically for new and returning users; the supervisor decides what to launch.
- **Minimal kernel impact.** One new shared function (`is_configured()`) and ~20 lines in supervisor. No changes to core, extensions, or the event bus.
- **Reuses existing restart mechanism.** The `sandbox/.restart_requested` file contract is already implemented and tested; onboarding simply triggers it.
- **Progressive disclosure.** Phase 1 (TUI) gets the agent running. Phase 2 (AI introduction) makes it personal. Phase 3 (channels) connects it to the world. Each phase is independently deployable.
- **Multi-provider from day one.** The wizard supports configuring multiple providers and assigning different models to different roles, matching the existing `settings.yaml` schema.

### Trade-offs

| Trade-off | Impact |
|-----------|--------|
| **`is_configured()` checks presence, not validity** | An expired API key passes the check; runtime failure handled by existing crash-backoff. Acceptable: checking validity would require network calls in the supervisor. |
| **Onboarding blocks the supervisor** | `subprocess.run()` blocks while the wizard runs. This is intentional: the wizard needs exclusive stdin access. The supervisor cannot do anything useful without a valid config anyway. |
| **New dependency (`questionary`)** | Adds a TUI library. Small, well-maintained, no transitive bloat. Could be replaced with raw `input()` calls at the cost of UX. |
| **No `--reconfigure` flag yet** | Users with valid config cannot easily re-run onboarding. Mitigation: deleting `.env` keys or adding a CLI flag is a small follow-up. |

### Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| **Onboarding writes corrupt config** | Medium | Atomic write (temp file + rename). Verification step before write. |
| **User cancels mid-write** | Low | Atomic write ensures partial writes don't corrupt existing config. Exit code 1 → supervisor exits cleanly. |
| **Provider API changes probe format** | Low | Probe is a best-effort verification; failure falls through to retry or skip. |

## Alternatives Considered

### Separate `setup` command (`uv run python -m setup`)

**Rejected.** Splits the entry point; users must know to run `setup` first. The single-command experience (`python -m supervisor`) is superior for first-time users.

### Web-based setup wizard

**Rejected for Phase 1.** Would require an HTTP server, static assets, and a browser. Over-engineered for a terminal-first application. Could be added as a Phase 4 enhancement if a web GUI is introduced.

### Interactive prompts inside the supervisor process (no subprocess)

**Rejected.** Mixing interactive I/O into the supervisor violates separation of concerns. The supervisor should remain a simple process manager. Running onboarding as a subprocess keeps the architecture consistent with how core is managed.

### Config validation inside core (no `is_configured()`)

**Rejected.** Core would start, fail, crash, and the supervisor would retry — potentially hitting the crash limit before the user understands the problem. Checking config *before* launching core provides a clean routing decision and clear user messaging.

## Relation to Other ADRs

- **ADR 001** — Supervisor-as-parent-process pattern is preserved. Onboarding is a new child process type alongside core.
- **ADR 002** — The onboarding extension (Phase 2) is a standard extension with a manifest, following the nano-kernel + extensions architecture.
- **ADR 004** — The onboarding extension can emit events (e.g., `onboarding.complete`) via the event bus for other extensions to react to.
- **ADR 005/008** — User profile data gathered during AI introduction is stored in long-term memory.
