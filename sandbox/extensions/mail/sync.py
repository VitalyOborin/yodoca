"""MailSyncer: per-account IMAP sync orchestration."""

import logging
from datetime import date, timedelta
from typing import Any

from aioimaplib import IMAP4_SSL

from sandbox.extensions.mail.accounts import AccountInfo, AccountStore
from sandbox.extensions.mail.parser import parse_message
from sandbox.extensions.mail.providers import PROVIDERS

logger = logging.getLogger(__name__)

# Error messages that indicate credential problems (user action required)
_CREDENTIAL_ERROR_SUBSTRINGS = (
    "authentication failed",
    "application-specific password required",
    "invalid credentials",
    "login failed",
    "auth failed",
)


def _is_credential_error(exc: BaseException) -> bool:
    """True if the error indicates a credential/auth problem."""
    msg = str(exc).lower()
    return any(s in msg for s in _CREDENTIAL_ERROR_SUBSTRINGS)


def _format_since_date(d: date) -> str:
    """Format date for IMAP SINCE search (DD-Mon-YYYY)."""
    return d.strftime("%d-%b-%Y")


class MailSyncer:
    """Orchestrates per-account sync via aioimaplib."""

    def __init__(
        self,
        ctx: Any,
        inbox: Any,
        accounts: AccountStore,
        config: dict[str, Any],
    ) -> None:
        self._ctx = ctx
        self._inbox = inbox
        self._accounts = accounts
        self._config = config
        self._initial_sync_days = int(config.get("initial_sync_days", 7))
        self._sync_mailboxes = config.get("sync_mailboxes") or ["INBOX"]
        self._batch_size = int(config.get("batch_size", 50))
        self._body_max_bytes = int(config.get("body_max_bytes", 8192))

    async def sync_all(self) -> dict[str, Any] | None:
        """Sync all enabled accounts. Returns {'text': '...'} on credential errors."""
        accounts = await self._accounts.list_accounts()
        enabled = [a for a in accounts if a.enabled]
        if not enabled:
            return None

        credential_errors: list[str] = []
        for account in enabled:
            result = await self.sync_account(account)
            if result.get("credential_error"):
                credential_errors.append(result["message"])

        if credential_errors:
            return {"text": "\n".join(credential_errors)}
        return None

    async def sync_account(self, account: AccountInfo) -> dict[str, Any]:
        """Sync one account. Returns dict with credential_error and message."""
        account_id = account.account_id
        provider_config = PROVIDERS.get(account.provider)
        if not provider_config:
            logger.warning(
                "Unknown provider %s for account %s", account.provider, account_id
            )
            return {}

        secret_name = f"mail.{account_id}.app_password"
        app_password = await self._ctx.get_secret(secret_name)
        if not app_password:
            return {
                "credential_error": True,
                "message": f"Mail {account.email}: App password not found. "
                "Re-add the account with mail_account_add.",
            }

        # Step 0: initial sync notification
        if not account.initial_sync_done:
            await self._ctx.notify_user(
                f"Starting initial sync for {account.email} "
                f"(last {self._initial_sync_days} days)…"
            )

        try:
            imap = IMAP4_SSL(
                host=provider_config.imap_host,
                port=provider_config.imap_port,
            )
            await imap.wait_hello_from_server()
            await imap.login(account.email, app_password)

            total_new = 0
            for mailbox in self._sync_mailboxes:
                n = await self._sync_mailbox(
                    imap, account, mailbox, provider_config.imap_host
                )
                total_new += n

            await imap.logout()

            # Step 5: mark initial sync done, notify
            if not account.initial_sync_done:
                now = date.today().isoformat()
                await self._accounts.update_account(
                    account_id,
                    initial_sync_done=True,
                    last_sync_at=now,
                )
                await self._ctx.notify_user(
                    f"Initial sync for {account.email} complete: "
                    f"{total_new} messages imported."
                )
            else:
                await self._accounts.update_account(
                    account_id, last_sync_at=date.today().isoformat()
                )

            await self._ctx.emit(
                "mail.sync.completed",
                {"account_id": account_id, "new_items": total_new, "errors": []},
            )
            return {}

        except Exception as e:
            logger.exception("Sync failed for %s: %s", account.email, e)
            if _is_credential_error(e):
                return {
                    "credential_error": True,
                    "message": f"Mail {account.email}: {e}. "
                    "Check your App Password or enable 2FA.",
                }
            # Transient: log only, no user notification
            return {}

    async def _sync_mailbox(
        self,
        imap: IMAP4_SSL,
        account: AccountInfo,
        mailbox: str,
        _host: str,
    ) -> int:
        """Sync one mailbox. Returns count of new items."""
        await imap.select(mailbox)
        cursor = await self._inbox.get_cursor("mail", account.account_id, mailbox)

        if cursor is None:
            since = date.today() - timedelta(days=self._initial_sync_days)
            since_str = _format_since_date(since)
            response = await imap.uid("search", None, "SINCE", since_str)
        else:
            next_uid = int(cursor) + 1
            response = await imap.uid("search", None, f"UID {next_uid}:*")

        uids = self._parse_search_response(response)
        if not uids:
            return 0

        max_uid = 0
        count = 0
        for i in range(0, len(uids), self._batch_size):
            batch = uids[i : i + self._batch_size]
            for uid in batch:
                raw = await self._fetch_message(imap, uid)
                if raw:
                    item = parse_message(
                        raw,
                        uid,
                        mailbox,
                        account.account_id,
                        body_max_bytes=self._body_max_bytes,
                    )
                    result = await self._inbox.upsert_item(item)
                    if result.success and result.change_type != "duplicate":
                        count += 1
                    max_uid = max(max_uid, uid)

        # Cursor only after all items persisted (crash-safe)
        if max_uid > 0:
            await self._inbox.set_cursor(
                "mail", account.account_id, mailbox, str(max_uid)
            )

        return count

    def _parse_search_response(self, response: Any) -> list[int]:
        """Extract UID list from IMAP search response."""
        uids: list[int] = []
        # aioimaplib returns Response with .lines; search returns e.g. [b'1 2 3 4']
        lines = getattr(response, "lines", []) or []
        for line in lines:
            if isinstance(line, bytes):
                line = line.decode("utf-8", errors="replace")
            for part in line.split():
                try:
                    uids.append(int(part))
                except ValueError:
                    pass
        return sorted(set(uids))

    async def _fetch_message(self, imap: IMAP4_SSL, uid: int) -> bytes | None:
        """Fetch raw RFC822 for one UID. Uses BODY.PEEK[] to avoid marking as read."""
        response = await imap.uid("fetch", str(uid), "(BODY.PEEK[])")
        lines = getattr(response, "lines", [])
        if not lines:
            return None
        # Fetch response: lines[0] may be metadata (UID FETCH ...), lines[1:] message
        # aioimaplib returns Response with .lines; message is typically in a literal
        for i, line in enumerate(lines):
            if isinstance(line, bytes):
                # Skip metadata line (e.g. b'1 (UID 1 BODY[] {1234}')
                if i == 0 and b"BODY" in line and b"{" in line:
                    continue
                # Message bytes (RFC822)
                if len(line) > 50 and (line.startswith(b"From ") or b"\r\n" in line):
                    return line
                if len(line) > 100:
                    return line
        # Fallback: concatenate all bytes
        parts = [ln for ln in lines if isinstance(ln, bytes) and len(ln) > 0]
        if parts:
            return b"\r\n".join(parts)
        return None
