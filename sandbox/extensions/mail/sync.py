"""MailSyncer: per-account IMAP sync orchestration."""

import logging
from datetime import UTC, date, datetime, timedelta
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

            login_resp = await imap.login(account.email, app_password)
            if login_resp.result != "OK":
                msg = (login_resp.lines[0:1] or [b""])[0]
                if isinstance(msg, bytes):
                    msg = msg.decode("utf-8", errors="replace")
                raise RuntimeError(f"IMAP login failed: {msg}")

            total_new = 0
            for mailbox in self._sync_mailboxes:
                n = await self._sync_mailbox(
                    imap, account, mailbox, provider_config.imap_host
                )
                total_new += n

            await imap.logout()

            # Step 5: mark initial sync done, notify
            if not account.initial_sync_done:
                now = datetime.now(UTC).date().isoformat()
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
                now = datetime.now(UTC).date().isoformat()
                await self._accounts.update_account(
                    account_id,
                    last_sync_at=now,
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
        sel_resp = await imap.select(mailbox)
        if sel_resp.result != "OK":
            logger.warning(
                "SELECT %s failed for %s: %s",
                mailbox,
                account.email,
                sel_resp.lines,
            )
            return 0

        cursor = await self._inbox.get_cursor(
            "mail",
            account.account_id,
            mailbox,
        )

        if cursor is None:
            since = datetime.now(UTC).date() - timedelta(
                days=self._initial_sync_days,
            )
            since_str = _format_since_date(since)
            response = await imap.uid_search("SINCE", since_str)
        else:
            next_uid = int(cursor) + 1
            response = await imap.uid_search(f"UID {next_uid}:*")

        if response.result != "OK":
            logger.warning(
                "UID SEARCH failed for %s/%s: %s",
                account.email,
                mailbox,
                response.lines,
            )
            return 0

        uids = self._parse_search_response(response)
        logger.info(
            "Search %s/%s: %d UIDs found",
            account.email,
            mailbox,
            len(uids),
        )
        if not uids:
            return 0

        max_uid = 0
        count = 0
        fetch_errors = 0
        for i in range(0, len(uids), self._batch_size):
            batch = uids[i : i + self._batch_size]
            for uid in batch:
                raw = await self._fetch_message(imap, uid)
                if not raw:
                    fetch_errors += 1
                    continue
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

        if fetch_errors:
            logger.warning(
                "Fetch errors for %s/%s: %d of %d",
                account.email,
                mailbox,
                fetch_errors,
                len(uids),
            )

        if max_uid > 0:
            await self._inbox.set_cursor(
                "mail",
                account.account_id,
                mailbox,
                str(max_uid),
            )

        logger.info(
            "Sync %s/%s done: %d new, %d fetch_errors",
            account.email,
            mailbox,
            count,
            fetch_errors,
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
        response = await imap.uid("fetch", str(uid), "BODY.PEEK[]")
        if response.result != "OK":
            logger.debug("FETCH UID %d: result=%s", uid, response.result)
            return None
        # aioimaplib response: lines[0]=metadata, lines[1]=RFC822 literal (bytearray)
        if len(response.lines) >= 2 and isinstance(
            response.lines[1],
            (bytes, bytearray),
        ):
            return bytes(response.lines[1])
        logger.warning(
            "FETCH UID %d: unexpected response structure (lines=%d, types=%s)",
            uid,
            len(response.lines),
            [type(ln).__name__ for ln in response.lines[:4]],
        )
        return None
