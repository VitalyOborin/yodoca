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


def split_for_telegram(text: str, max_len: int = 4096) -> list[str]:
    """Split markdown text into Telegram HTML parts, each within max_len chars.

    Splits on paragraph boundaries (double newline) in the original markdown,
    then converts each chunk independently. This guarantees no HTML tag is ever
    broken mid-element. Falls back to single-newline splits for oversized paragraphs,
    and to hard character splits only as a last resort.
    """
    if not text:
        return []

    parts: list[str] = []
    current_md: list[str] = []
    current_len = 0

    def _flush() -> None:
        chunk = md_to_tg_html("\n\n".join(current_md))
        parts.append(chunk)
        current_md.clear()
        nonlocal current_len
        current_len = 0

    paragraphs = re.split(r"\n{2,}", text)

    for para in paragraphs:
        para_html_len = len(md_to_tg_html(para))

        if para_html_len > max_len:
            if current_md:
                _flush()
            lines = para.splitlines()
            sub_md: list[str] = []
            sub_len = 0
            for line in lines:
                line_html_len = len(md_to_tg_html(line))
                if line_html_len > max_len:
                    if sub_md:
                        parts.append(md_to_tg_html("\n".join(sub_md)))
                        sub_md.clear()
                        sub_len = 0
                    converted = md_to_tg_html(line)
                    for i in range(0, len(converted), max_len):
                        parts.append(converted[i : i + max_len])
                elif sub_len + line_html_len > max_len:
                    parts.append(md_to_tg_html("\n".join(sub_md)))
                    sub_md = [line]
                    sub_len = line_html_len
                else:
                    sub_md.append(line)
                    sub_len += line_html_len
            if sub_md:
                parts.append(md_to_tg_html("\n".join(sub_md)))
            continue

        separator_len = len(md_to_tg_html("\n\n")) if current_md else 0
        if current_len + separator_len + para_html_len > max_len:
            _flush()

        current_md.append(para)
        current_len += para_html_len

    if current_md:
        _flush()

    return parts or [md_to_tg_html(text)]
