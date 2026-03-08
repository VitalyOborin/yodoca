# ADR 025: Mail Extension (Source Extension for Email Ingestion)

## Status

Proposed.

## Context

The Unified Inbox (ADR 024) defines the canonical ingestion storage and contract for
all external source extensions. The `mail` extension is the **first source extension**
in the system: it periodically polls email accounts via IMAP and pushes new messages
into `inbox` through the service API (`upsert_item`, `get_cursor`, `set_cursor`).

Key constraints:

- `mail` is strictly an ingestion adapter — it does not store messages, does not
  process them, does not make decisions. All persistence and deduplication is
  delegated to `inbox`.
- Authentication in MVP is limited to **App Passwords** (Gmail, Yandex). OAuth2
  (XOAUTH2) is deferred to Phase 2 to avoid provider registration, callback servers,
  and extra dependencies.
- Setup is conversational: the Orchestrator calls `mail` tools based on user dialog.
  No `SetupProvider` protocol is used.
- Gmail disabled "Less Secure Apps" (May 2025). App Passwords remain the officially
  recommended mechanism for third-party IMAP access and require 2FA to be enabled.

## Decision

### 1) Extension identity and protocols

Create extension `sandbox/extensions/mail/` implementing:

- **`ToolProvider`** — setup tools (add / list / remove / test accounts) and future
  read/write tools for the agent.
- **`SchedulerProvider`** — periodic sync via manifest cron.

Manifest dependency: `depends_on: [inbox]`.

### 2) No core changes required

All necessary `ExtensionContext` API already exists:

| Need | Existing API |
|------|--------------|
| Read secrets | `ctx.get_secret(name)` |
| Write secrets | `ctx.set_secret(name, value)` |
| Access inbox | `ctx.get_extension("inbox")` via `depends_on` |
| Emit events | `ctx.emit(topic, payload)` |
| Notify user | `ctx.notify_user(text)` |
| Read config | `ctx.get_config(key, default)` |
| Local storage | `ctx.data_dir` |
| Cron scheduling | Manifest `schedules` + `SchedulerProvider.execute_task` |

No additions to core are necessary.

### 3) Authentication: App Password only (MVP)

| Criterion | App Password | OAuth2 (Phase 2) |
|-----------|-------------|-------------------|
| Provider app registration | Not needed | Required (Google Cloud / Yandex ID) |
| Barrier to entry | Minimal (2FA + generate password) | High (developer console, client ID) |
| Localhost callback server | No | Yes |
| IMAP transport | `LOGIN` (stdlib) | `AUTHENTICATE XOAUTH2` |
| Extra Python dependencies | None | `google-auth`, `oauthlib` |
| Gmail support | Yes (requires 2FA) | Yes |
| Yandex support | Yes (requires 2FA + IMAP enabled) | Yes |

Prerequisite: user must have 2FA enabled on the mail account. The agent communicates
this during setup and provides direct links to provider settings.

### 4) Setup tools (ToolProvider)

Setup is a conversational flow: the user says "connect my Gmail", the agent walks
through the App Password steps and calls the tools.

```
mail_account_add(
    provider: Literal["gmail", "yandex"],
    email: str,
    app_password: str,
    account_id: str | None = None     # auto-generated from provider + email if None
) -> dict    # {success, account_id, message}

mail_account_list() -> list[dict]     # [{account_id, provider, email, status, last_sync}]

mail_account_remove(account_id: str) -> dict    # {success, message}

mail_account_test(account_id: str) -> dict      # {success, mailboxes: list[str], message}
```

All tools return structured output (Pydantic models or fixed-shape dicts).

#### What `mail_account_add` does internally

1. **Normalize** — strip spaces from app_password.
2. **Verify via IMAP** — synchronous `imaplib.IMAP4_SSL` connection wrapped in
   `asyncio.to_thread()`: login, list mailboxes, logout.
3. **On success** — store app_password via `ctx.set_secret(f"mail.{account_id}.app_password", ...)`;
   persist account metadata (provider, email, enabled, timestamps) in a JSON file
   under `ctx.data_dir/accounts.json`.
4. **On failure** — return a structured error with a human-readable hint:
   - "Application-specific password required" → advise enabling 2FA;
   - `AUTH[CLIENTBUG]` → advise checking the pasted password;
   - Timeout → advise checking network / provider host.

#### Security note for Telegram

App Passwords typed in Telegram chat remain in message history. MVP mitigation:
the agent warns the user and suggests using CLI for password entry. Full mitigation
(auto-deleting the message via Telegram API) is deferred.

### 5) Sync loop (SchedulerProvider)

Manifest cron fires `sync_all` every 5 minutes:

```yaml
schedules:
  - name: sync_all
    cron: "*/5 * * * *"
    task: sync_all
```

`execute_task("sync_all")` returns `None` (no user notification) on normal runs.

#### Sync algorithm per account

```
for each account where enabled = true:

  1. Retrieve app_password via ctx.get_secret(...)
     → skip account with warning if None

  2. Connect via aioimaplib (async IMAP4_SSL)

  3. For each configured mailbox (default: ["INBOX"]):
     a. cursor = inbox.get_cursor("mail", account_id, mailbox)
     b. If cursor is None (first sync):
           search SINCE (today - initial_sync_days)
        Else:
           search UID (cursor+1):*
     c. For each UID batch (batch_size, default 50):
           fetch RFC822 → parse → inbox.upsert_item(item)
     d. Update cursor to max UID only after all items persisted

  4. Disconnect

  5. Emit event: mail.sync.completed {account_id, new_items, errors}
```

Cursor is updated **strictly after** successful persistence, ensuring crash-safe
resume via inbox's idempotent upsert (dedup by `external_id = Message-ID`).

### 6) RFC822 parsing → InboxItemInput

```
parse_message(raw: bytes, uid: int, mailbox: str, account_id: str) → InboxItemInput

Mapping:
  source_type    = "mail"
  source_account = account_id
  entity_type    = "email.message"
  external_id    = Message-ID header (RFC 5322, globally unique, stable across folders)
  title          = "{from} | {subject}"
  occurred_at    = parsed Date header as unix timestamp
  payload        = {
      uid, mailbox, from, subject, date,
      body (text/plain preferred, HTML stripped as fallback, max 8 KB),
      has_html, attachments (metadata only: filename, content_type, size_bytes),
      flags (empty in Phase 1)
  }
```

Message-ID is used as `external_id` because it is stable even if the message is moved
between IMAP folders (UID changes on move, Message-ID does not). This prevents
duplicates across mailboxes.

### 7) Account storage

Non-secret account metadata is stored in `ctx.data_dir/accounts.json`:

```json
[
  {
    "account_id": "gmail_work",
    "provider": "gmail",
    "email": "work@company.com",
    "enabled": true,
    "added_at": "2026-03-08T21:00:00Z",
    "last_sync_at": null,
    "initial_sync_done": false
  }
]
```

Secrets (app_password) are stored exclusively via `ctx.set_secret()` (OS keyring
with `.env` fallback), never in JSON files.

### 8) Provider configuration

```python
PROVIDERS = {
    "gmail": {
        "imap_host": "imap.gmail.com",
        "imap_port": 993,
        "app_password_url": "https://myaccount.google.com/apppasswords",
        "setup_note": "Requires 2FA enabled on Google account."
    },
    "yandex": {
        "imap_host": "imap.yandex.ru",
        "imap_port": 993,
        "app_password_url": "https://id.yandex.ru/security/app-passwords",
        "setup_note": "Requires 2FA. Also enable IMAP in Yandex Mail settings."
    }
}
```

Adding a new provider means adding an entry here and (if auth differs) a new
auth strategy — no changes to sync, parsing, or inbox integration.

### 9) Health check

```python
def health_check(self) -> bool:
    # True if at least one account is configured
    # AND the last sync_all did not fail critically.
    # Does not perform live IMAP connection (too expensive for periodic health checks).
```

### 10) File structure

```
sandbox/extensions/mail/
├── manifest.yaml      # Extension manifest with cron schedule
├── main.py            # MailExtension: lifecycle + ToolProvider + SchedulerProvider
├── sync.py            # MailSyncer: per-account sync orchestration
├── parser.py          # RFC822 bytes → InboxItemInput
├── accounts.py        # AccountStore: JSON-backed accounts registry
└── providers.py       # IMAP host/port config per provider
```

### 11) Manifest

```yaml
id: mail
name: Mail
version: "1.0.0"
entrypoint: main:MailExtension
description: >
  Periodically syncs email from Gmail and Yandex.Mail via IMAP.
  Stores new messages in Inbox.
  Setup: use mail_account_add tool to connect a mailbox.

depends_on:
  - inbox

config:
  initial_sync_days: 7
  sync_mailboxes: ["INBOX"]
  batch_size: 50
  body_max_bytes: 8192

schedules:
  - name: sync_all
    cron: "*/5 * * * *"
    task: sync_all

events:
  publishes:
    - topic: mail.sync.completed
      description: >
        Emitted after each sync cycle per account.
        Payload: account_id, new_items (int), errors (list).

enabled: true
```

### 12) Import pattern inside extension

The Loader uses `importlib.util.spec_from_file_location`, making relative imports
(`from .parser import ...`) unreliable. Extension modules use an explicit fallback:

```python
try:
    from .parser import parse_message
except ImportError:
    import importlib.util
    import pathlib
    _spec = importlib.util.spec_from_file_location(
        "parser", pathlib.Path(__file__).parent / "parser.py"
    )
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    parse_message = _mod.parse_message
```

### 13) New Python dependencies

| Package | Version | Purpose | Already in project? |
|---------|---------|---------|---------------------|
| `aioimaplib` | ^1.1 | Async IMAP4 client for sync loop | No |
| `beautifulsoup4` | ^4.12 | HTML → plain text fallback for email body | No |

Standard library modules used: `email` (RFC822 parsing), `imaplib` (sync IMAP for
setup verification via `asyncio.to_thread`).

### 14) Phased delivery

**Phase 1 (MVP) — ingestion only:**

- `mail_account_add` / `mail_account_list` / `mail_account_remove` / `mail_account_test`
- App Password authentication (Gmail, Yandex)
- `sync_all` cron every 5 minutes, INBOX only
- RFC822 parsing: headers + body (8 KB limit) + attachment metadata (no content)
- Integration with `inbox.upsert_item()` + cursor management
- `mail.sync.completed` event

**Phase 2 — write operations and OAuth2:**

- `mail_send` tool (SMTP + App Password or XOAUTH2)
- `mail_mark_read` / `mail_move` / `mail_delete` tools
- OAuth2 / XOAUTH2 as alternative to App Password
- Additional mailbox support (Sent, Spam, custom folders)
- Reconciliation job (ADR 024, Decision 8)

## Consequences

### Positive

- First concrete source extension validates the inbox ingestion contract (ADR 024).
- Conversational setup keeps UX consistent across CLI and Telegram — no provider
  consoles or redirect URIs needed.
- App Password minimizes onboarding friction: no app registration, no callback server,
  no extra dependencies.
- Cursor-based incremental sync + idempotent upsert guarantees crash-safe, duplicate-free
  ingestion.
- Message-ID as `external_id` ensures stability across IMAP folder moves.
- Provider configuration is isolated in a single module; adding providers does not
  require structural changes.

### Trade-offs

- App Password requires 2FA on the user's mail account; accounts without 2FA cannot
  connect until OAuth2 is added in Phase 2.
- Corporate Google Workspace admins may disable App Passwords — also requires Phase 2
  OAuth2.
- Body is truncated to 8 KB; long emails lose content. Acceptable for triage; full
  content retrieval can be added as a tool in Phase 2.
- Attachment content is not ingested (metadata only); acceptable for MVP triage use case.

### Risks and mitigations

- **Large initial sync** — `initial_sync_days: 7` bounds the first run, but an active
  user may have 500+ messages. Batching (50 UIDs) and async IMAP prevent event-loop
  blocking. Progress is reported to user via `ctx.notify_user()`.
- **Crash mid-sync** — cursor advances only after all items in a mailbox are persisted.
  Incomplete batches are re-fetched on next run; inbox deduplicates by `external_id`.
- **Password in Telegram history** — MVP warns the user; full mitigation (message
  deletion) deferred.
- **Yandex IMAP disabled by default** — agent instructs user to enable "IMAP access"
  in Yandex Mail settings during setup.
