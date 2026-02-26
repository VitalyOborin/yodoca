# Secrets management

## Overview

Secrets (API keys, tokens, passwords) are stored in the OS credential store via the [`keyring`](https://github.com/jaraco/keyring) package. On desktop platforms this means Windows Credential Manager, macOS Keychain, or the Freedesktop Secret Service (GNOME Keyring / KWallet). In headless and CI environments where no real keyring backend is available, secrets fall back to `os.environ`, which is populated from `.env` at startup.

All secret I/O is centralised in `core/secrets.py`. Nothing outside that module calls `keyring` directly.

---

## Resolution order

```
keyring.get_password("yodoca", name)  ← tried first
        ↓ missing or error
os.environ.get(name)                  ← fallback (populated by load_dotenv)
        ↓ missing
None
```

This means that in a headless deployment (Docker, CI, cron) you can still use `.env` without any changes — secrets are read from the environment just as before.

---

## `core/secrets.py` API

### `get_secret(name: str) -> str | None`

Synchronous lookup. Use in:
- `ModelRouter` initialization (`secrets_getter=secrets.get_secret`)
- `core/config_check.py` (`is_configured`)
- standalone scripts (`run_memory_maintenance.py`, etc.)

### `get_secret_async(name: str) -> str | None`

Async variant. Wraps the blocking `keyring.get_password` call in `asyncio.to_thread` to avoid stalling the event loop. Use whenever you are already inside an `async` context:
- `ExtensionContext.get_secret(name)` delegates here
- any extension that needs a secret at runtime

### `set_secret(name: str, value: str) -> None`

Stores a secret in the keyring. Raises `KeyringError` if no real backend is active (check `is_keyring_available()` first). Used by `onboarding/config_writer.py` at the end of the setup wizard.

### `set_secret_async(name: str, value: str) -> None`

Async variant of `set_secret`.

### `delete_secret(name: str) -> None`

Removes a secret. No-op if the key is absent.

### `is_keyring_available() -> bool`

Returns `True` when a real OS backend is active. Returns `False` when only the `FailKeyring` stub is loaded (headless / no D-Bus session). Use this to decide between keyring and `.env` write paths.

---

## Onboarding flow

During `python -m onboarding` the wizard collects API keys. At the end `onboarding/config_writer.py` calls `write_config()`:

1. All provider configs are written to `config/settings.yaml`.
2. Secret keys are identified by reading the `api_key_secret` field from each provider block in `WizardState.providers`.
3. If `is_keyring_available()` is `True`:
   - each secret is stored via `set_secret(name, value)`
   - those keys are **excluded** from the `.env` file
4. If `is_keyring_available()` is `False` (headless / CI):
   - a warning is logged
   - all secrets fall back to `.env`, same as before

After the wizard runs, `config/settings.yaml` contains `api_key_secret: OPENAI_API_KEY` (the name, not the value). The value is resolved at runtime by `ModelRouter._resolve_key()` via `secrets_getter`.

---

## Runtime flow

```
supervisor/runner.py
  └── load_dotenv(.env)            ← populates os.environ for headless fallback
  └── spawns: python -m core

core/runner.py
  └── load_dotenv(.env)            ← same, for child process
  └── ModelRouter(secrets_getter=secrets.get_secret)
        └── _resolve_key(provider_cfg)
              └── secrets.get_secret("OPENAI_API_KEY")
                    ├── keyring.get_password("yodoca", "OPENAI_API_KEY")  ← primary
                    └── os.environ.get("OPENAI_API_KEY")                  ← fallback
```

`ExtensionContext.get_secret(name)` follows the same path via `get_secret_async`.

---

## Writing a new extension that needs a secret

Extensions must **never** call `os.environ.get` or `keyring` directly. Use the context API:

```python
async def initialize(self, context):
    token = await context.get_secret("MY_BOT_TOKEN")
    if not token:
        self.logger.warning("MY_BOT_TOKEN not set, extension disabled")
        return
```

Store the secret by running onboarding (or `set_secret` programmatically in tests):

```python
from core.secrets import set_secret
set_secret("MY_BOT_TOKEN", "secret-value")
```

---

## Extension setup verification

There is no standalone tool for checking Telegram (or other extension) connectivity. Setup verification is done via the **SetupProvider** protocol: when an extension implements `on_setup_complete()`, the orchestrator calls it after configuration is saved. For example, the Telegram channel extension validates the token format and calls the Telegram API (`Bot.get_me()`) inside `on_setup_complete()`, returning a success or sanitized error message. The agent never receives a dedicated "check connection" tool; verification is part of the setup flow.

---

## Headless / CI / cron environments

If the system keyring is unavailable (e.g. a Docker container or a cron job without a D-Bus session):

- `is_keyring_available()` returns `False`
- Onboarding writes secrets to `.env` as before
- `get_secret()` falls back to `os.environ`, which is populated by `load_dotenv`
- No code changes needed

If secrets were previously stored in the keyring by an interactive run and you need the same service to work headless, re-run onboarding in the target environment so it writes `.env` instead.

---

## Resetting secrets

There is no automatic migration between storage backends. To change the storage location (e.g. move from keyring to `.env` or vice versa):

1. Delete `config/settings.yaml` and `.env`
2. Re-run `python -m supervisor` (or `python -m onboarding`) to trigger the setup wizard again

The wizard will write secrets to whichever backend is appropriate for the current environment.

---

## Platform backends

| Platform | Backend used by `keyring` |
|---|---|
| Windows | Windows Credential Manager |
| macOS | macOS Keychain |
| Linux (GNOME) | libsecret / GNOME Keyring |
| Linux (KDE) | KWallet |
| Headless Linux / CI | FailKeyring → `.env` fallback |

The `SERVICE_NAME` used in the keyring is `"yodoca"`. All secrets stored by this application appear under that service name.
