"""Markdown → Telegram HTML conversion for agent responses."""

import html
import re

_STASH_PREFIX = "\x00P"


def _stash(value: str, store: list[str]) -> str:
    idx = len(store)
    store.append(value)
    return f"{_STASH_PREFIX}{idx}\x00"


def _restore(text: str, store: list[str]) -> str:
    for idx, value in enumerate(store):
        text = text.replace(f"{_STASH_PREFIX}{idx}\x00", value)
    return text


def md_to_tg_html(text: str) -> str:
    """Convert Markdown to Telegram-compatible HTML.

    Processing order:
    1. Extract fenced code blocks (content HTML-escaped, stored as placeholder).
    2. Extract inline code (same).
    3. HTML-escape the remaining prose (&, <, >).
    4. Apply inline Markdown patterns (bold, italic, strikethrough, links).
    5. Convert ATX headers to bold, bullet lists to • prefix.
    6. Restore code placeholders.
    """
    store: list[str] = []

    def _fenced(m: re.Match[str]) -> str:
        lang = (m.group(1) or "").strip()
        code = html.escape(m.group(2))
        inner = (
            f'<code class="language-{lang}">{code}</code>'
            if lang
            else f"<code>{code}</code>"
        )
        return _stash(f"<pre>{inner}</pre>", store)

    text = re.sub(r"```(\w*)\n(.*?)```", _fenced, text, flags=re.DOTALL)

    def _inline(m: re.Match[str]) -> str:
        return _stash(f"<code>{html.escape(m.group(1))}</code>", store)

    text = re.sub(r"`([^`\n]+)`", _inline, text)
    text = html.escape(text, quote=False)

    text = re.sub(r"\*\*\*(.+?)\*\*\*", r"<b><i>\1</i></b>", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"__(.+?)__", r"<b>\1</b>", text)
    text = re.sub(r"\*([^*\n]+)\*", r"<i>\1</i>", text)
    text = re.sub(r"_([^_\n]+)_", r"<i>\1</i>", text)
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text)
    text = re.sub(r"\[(.+?)\]\((.+?)\)", r'<a href="\2">\1</a>', text)
    text = re.sub(r"^#{1,6}\s+(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)
    text = re.sub(r"^[ \t]*[-*]\s+", "• ", text, flags=re.MULTILINE)

    return _restore(text, store)


def escape_html(text: str) -> str:
    """HTML-escape only — used for partial streaming buffers."""
    return html.escape(text, quote=False)
