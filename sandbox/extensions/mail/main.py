"""Mail extension: ToolProvider + SchedulerProvider for email ingestion."""

import asyncio
import imaplib
import importlib.util
import logging
import sys
from pathlib import Path
from typing import Any, Literal

from agents import function_tool
from pydantic import BaseModel, Field

try:
    from sandbox.extensions.mail.accounts import AccountInfo, AccountStore
    from sandbox.extensions.mail.providers import PROVIDERS
    from sandbox.extensions.mail.sync import MailSyncer
except ImportError:  # pragma: no cover - fallback for direct module loading
    _mail_dir = Path(__file__).resolve().parent
    for mod_name, mod_file in [
        ("accounts", "accounts.py"),
        ("providers", "providers.py"),
        ("sync", "sync.py"),
    ]:
        _path = _mail_dir / mod_file
        _spec = importlib.util.spec_from_file_location(f"ext_mail_{mod_name}", _path)
        if _spec is None or _spec.loader is None:
            raise ImportError(f"Cannot load {mod_name} from {_path}") from None
        _mod = importlib.util.module_from_spec(_spec)
        sys.modules[_spec.name] = _mod
        _spec.loader.exec_module(_mod)
        if mod_name == "accounts":
            AccountInfo = _mod.AccountInfo
            AccountStore = _mod.AccountStore
        elif mod_name == "providers":
            PROVIDERS = _mod.PROVIDERS
        else:
            MailSyncer = _mod.MailSyncer

logger = logging.getLogger(__name__)


# --- Tool result models ---


class MailAccountAddResult(BaseModel):
    """Result of mail_account_add tool."""

    success: bool
    account_id: str = ""
    message: str = ""


class MailAccountListResult(BaseModel):
    """Result of mail_account_list tool."""

    success: bool
    accounts: list[dict] = Field(default_factory=list)
    error: str | None = None


class MailAccountRemoveResult(BaseModel):
    """Result of mail_account_remove tool."""

    success: bool
    message: str = ""


class MailAccountTestResult(BaseModel):
    """Result of mail_account_test tool."""

    success: bool
    mailboxes: list[str] = Field(default_factory=list)
    message: str = ""


def _verify_imap_sync(
    host: str, port: int, email: str, app_password: str
) -> tuple[bool, list[str], str]:
    """Verify IMAP connection and return (ok, mailboxes, error_message)."""
    try:
        imap = imaplib.IMAP4_SSL(host, port=port)
        imap.login(email, app_password)
        status, data = imap.list()
        mailboxes: list[str] = []
        if status == "OK" and data:
            for item in data:
                if isinstance(item, bytes):
                    # Format: b'(\\HasNoChildren) "/" "INBOX"'
                    parts = item.decode("utf-8", errors="replace").split('"')
                    if len(parts) >= 3:
                        mailboxes.append(parts[-2])
        imap.logout()
        return True, mailboxes, ""
    except imaplib.IMAP4.error as e:
        msg = str(e)
        return False, [], msg
    except Exception as e:
        return False, [], str(e)


class MailExtension:
    """Extension: ToolProvider + SchedulerProvider for mail ingestion."""

    def __init__(self) -> None:
        self._ctx: Any = None
        self._inbox: Any = None
        self._accounts: AccountStore | None = None
        self._syncer: MailSyncer | None = None

    async def initialize(self, context: Any) -> None:
        self._ctx = context
        self._inbox = context.get_extension("inbox")
        if not self._inbox:
            logger.warning("Inbox extension not available; mail sync disabled")
        config = {
            "initial_sync_days": context.get_config("initial_sync_days", 7),
            "sync_mailboxes": context.get_config("sync_mailboxes") or ["INBOX"],
            "batch_size": context.get_config("batch_size", 50),
            "body_max_bytes": context.get_config("body_max_bytes", 8192),
        }
        self._accounts = AccountStore(context.data_dir)
        if self._inbox:
            self._syncer = MailSyncer(context, self._inbox, self._accounts, config)

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def destroy(self) -> None:
        self._syncer = None
        self._accounts = None
        self._inbox = None
        self._ctx = None

    def health_check(self) -> bool:
        """True unless internal state is broken (e.g. data_dir inaccessible)."""
        return True

    def get_tools(self) -> list[Any]:
        if not self._ctx or not self._accounts:
            return []
        ext = self

        @function_tool(name_override="mail_account_add")
        async def mail_account_add(
            provider: Literal["gmail", "yandex"],
            email: str,
            app_password: str,
            account_id: str | None = None,
        ) -> MailAccountAddResult:
            """Add a mail account using App Password (IMAP).

            IMPORTANT — conversational flow:
            1. Ask which provider (gmail/yandex) and email.
               Email can be ANY domain — provider = IMAP backend
               (e.g. corporate@company.com on Yandex).
            2. Collect App Password securely:
               - request_secure_input, then pass secret_id here
                 (resolved from secure storage automatically).
               - Or pass raw App Password (e.g. from Telegram).

            Prerequisites: 2FA must be enabled.
            Gmail: https://myaccount.google.com/apppasswords
            Yandex: enable IMAP, then https://id.yandex.ru/security/app-passwords
            """
            try:
                # Resolve: app_password may be a secret_id from request_secure_input
                resolved = await ext._ctx.get_secret(app_password)
                if resolved:
                    app_password = resolved
                app_password = app_password.replace(" ", "").strip()
                if not app_password:
                    return MailAccountAddResult(
                        success=False,
                        message="App password cannot be empty.",
                    )
                cfg = PROVIDERS.get(provider)
                if not cfg:
                    return MailAccountAddResult(
                        success=False,
                        message=f"Unknown provider: {provider}",
                    )
                ok, mailboxes, err = await asyncio.to_thread(
                    _verify_imap_sync,
                    cfg.imap_host,
                    cfg.imap_port,
                    email.strip(),
                    app_password,
                )
                if not ok:
                    hint = (
                        "Enable 2FA and create an App Password."
                        if "password" in err.lower()
                        else "Check network and credentials."
                    )
                    return MailAccountAddResult(
                        success=False,
                        message=f"IMAP verification failed: {err}. {hint}",
                    )
                aid = account_id or f"{provider}_{email.split('@')[0]}"
                await ext._ctx.set_secret(f"mail.{aid}.app_password", app_password)
                account = AccountInfo(
                    account_id=aid,
                    provider=provider,
                    email=email.strip(),
                    enabled=True,
                )
                await ext._accounts.add_account(account)
                if ext._syncer:
                    asyncio.create_task(ext._syncer.sync_account(account))
                return MailAccountAddResult(
                    success=True,
                    account_id=aid,
                    message=(
                        f"Connected. Mailboxes: {', '.join(mailboxes[:5])}"
                        f"{'…' if len(mailboxes) > 5 else ''}. "
                        f"Initial sync started."
                    ),
                )
            except Exception as e:
                return MailAccountAddResult(success=False, message=str(e))

        @function_tool(name_override="mail_account_list")
        async def mail_account_list() -> MailAccountListResult:
            """List configured mail accounts."""
            try:
                accounts = await ext._accounts.list_accounts()
                data = [
                    {
                        "account_id": a.account_id,
                        "provider": a.provider,
                        "email": a.email,
                        "enabled": a.enabled,
                        "last_sync_at": a.last_sync_at,
                    }
                    for a in accounts
                ]
                return MailAccountListResult(success=True, accounts=data)
            except Exception as e:
                return MailAccountListResult(success=False, error=str(e))

        @function_tool(name_override="mail_account_remove")
        async def mail_account_remove(account_id: str) -> MailAccountRemoveResult:
            """Remove a mail account. Cursors are cleared; inbox items are retained."""
            try:
                account = await ext._accounts.get_account(account_id)
                if not account:
                    return MailAccountRemoveResult(
                        success=False,
                        message=f"Account {account_id} not found.",
                    )
                await ext._ctx.set_secret(f"mail.{account_id}.app_password", "")
                if ext._inbox:
                    await ext._inbox.delete_cursors("mail", account_id)
                removed = await ext._accounts.remove_account(account_id)
                if removed:
                    return MailAccountRemoveResult(
                        success=True,
                        message=f"Account {account_id} removed.",
                    )
                return MailAccountRemoveResult(
                    success=False,
                    message=f"Failed to remove {account_id}.",
                )
            except Exception as e:
                return MailAccountRemoveResult(success=False, message=str(e))

        @function_tool(name_override="mail_account_test")
        async def mail_account_test(account_id: str) -> MailAccountTestResult:
            """Test connection for a mail account."""
            try:
                account = await ext._accounts.get_account(account_id)
                if not account:
                    return MailAccountTestResult(
                        success=False,
                        message=f"Account {account_id} not found.",
                    )
                cfg = PROVIDERS.get(account.provider)
                if not cfg:
                    return MailAccountTestResult(
                        success=False,
                        message=f"Unknown provider: {account.provider}",
                    )
                pwd = await ext._ctx.get_secret(f"mail.{account_id}.app_password")
                if not pwd:
                    return MailAccountTestResult(
                        success=False,
                        message="App password not found. Re-add the account.",
                    )
                ok, mailboxes, err = await asyncio.to_thread(
                    _verify_imap_sync,
                    cfg.imap_host,
                    cfg.imap_port,
                    account.email,
                    pwd,
                )
                if ok:
                    return MailAccountTestResult(
                        success=True,
                        mailboxes=mailboxes,
                        message=f"OK. Mailboxes: {', '.join(mailboxes[:10])}",
                    )
                return MailAccountTestResult(
                    success=False,
                    message=f"Connection failed: {err}",
                )
            except Exception as e:
                return MailAccountTestResult(success=False, message=str(e))

        return [
            mail_account_add,
            mail_account_list,
            mail_account_remove,
            mail_account_test,
        ]

    async def execute_task(self, task_name: str) -> dict[str, Any] | None:
        """SchedulerProvider: sync_all runs every 5 minutes."""
        if task_name != "sync_all" or not self._syncer:
            return None
        return await self._syncer.sync_all()
