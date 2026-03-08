"""RFC822 parsing: raw bytes to InboxItemInput."""

import email
from email.header import decode_header
from email.utils import parsedate_to_datetime

from bs4 import BeautifulSoup

try:
    from sandbox.extensions.inbox.models import InboxItemInput
except ImportError:  # pragma: no cover - fallback for direct module loading
    import importlib.util
    import sys
    from pathlib import Path

    _inbox_parent = Path(__file__).resolve().parent.parent / "inbox"
    _models_path = _inbox_parent / "models.py"
    if not _models_path.exists():
        raise ImportError(f"Inbox models not found at {_models_path}") from None
    _spec = importlib.util.spec_from_file_location("ext_inbox_models", _models_path)
    if _spec is None or _spec.loader is None:
        raise ImportError(f"Cannot load inbox models from {_models_path}") from None
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules[_spec.name] = _mod
    _spec.loader.exec_module(_mod)
    InboxItemInput = _mod.InboxItemInput


def _decode_header_value(value: str | None) -> str:
    """Decode RFC 2047-encoded header. Uses errors='replace' for robustness."""
    if not value:
        return ""
    parts = decode_header(value)
    result_parts: list[str] = []
    for part, charset in parts:
        if isinstance(part, bytes):
            enc = charset or "utf-8"
            result_parts.append(part.decode(enc, errors="replace"))
        else:
            result_parts.append(str(part))
    return "".join(result_parts)


def _decode_body_part(part: email.message.Message, body_max_bytes: int) -> str:
    """Decode a MIME part to text. Charset from part or utf-8, errors='replace'."""
    payload = part.get_payload(decode=True)
    if not payload:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        text = payload.decode(charset, errors="replace")
    except LookupError:
        text = payload.decode("utf-8", errors="replace")
    return text[:body_max_bytes]


def _extract_text_body(msg: email.message.Message, body_max_bytes: int) -> str:
    """Extract body: prefer text/plain, fallback to text/html stripped."""
    text_body = ""
    html_body = ""
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        ct = part.get_content_type()
        if ct == "text/plain" and not text_body:
            text_body = _decode_body_part(part, body_max_bytes)
        elif ct == "text/html" and not html_body:
            raw = _decode_body_part(part, body_max_bytes)
            if raw:
                soup = BeautifulSoup(raw, "html.parser")
                html_body = soup.get_text(separator=" ", strip=True)[:body_max_bytes]
    if text_body:
        return text_body[:body_max_bytes]
    if html_body:
        return html_body[:body_max_bytes]
    return ""


def _has_html_part(msg: email.message.Message) -> bool:
    """Check if message has a text/html part."""
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            return True
    return False


def _get_attachments_metadata(msg: email.message.Message) -> list[dict]:
    """Return attachment metadata only (filename, content_type, size_bytes)."""
    result: list[dict] = []
    for part in msg.walk():
        if part.get_content_disposition() != "attachment":
            continue
        payload = part.get_payload(decode=True) or b""
        result.append(
            {
                "filename": part.get_filename() or "",
                "content_type": part.get_content_type(),
                "size_bytes": len(payload),
            }
        )
    return result


def parse_message(
    raw: bytes,
    uid: int,
    mailbox: str,
    account_id: str,
    body_max_bytes: int = 8192,
) -> InboxItemInput:
    """Parse RFC822 bytes into InboxItemInput for inbox.upsert_item."""
    msg = email.message_from_bytes(raw)

    message_id = msg.get("Message-ID") or f"uid-{account_id}-{uid}"
    subject = _decode_header_value(msg.get("Subject")) or "(no subject)"
    from_addr = _decode_header_value(msg.get("From")) or ""
    date_str = msg.get("Date") or ""
    try:
        dt = parsedate_to_datetime(date_str)
        occurred_at = dt.timestamp()
    except (TypeError, ValueError):
        occurred_at = 0.0

    body_text = _extract_text_body(msg, body_max_bytes)
    attachments = _get_attachments_metadata(msg)

    title = f"{from_addr} | {subject}" if from_addr else subject

    payload = {
        "uid": uid,
        "mailbox": mailbox,
        "from": from_addr,
        "subject": subject,
        "date": date_str,
        "body": body_text,
        "has_html": _has_html_part(msg),
        "attachments": attachments,
        "flags": [],
    }

    return InboxItemInput(
        source_type="mail",
        source_account=account_id,
        entity_type="email.message",
        external_id=message_id,
        title=title,
        occurred_at=occurred_at,
        payload=payload,
    )
