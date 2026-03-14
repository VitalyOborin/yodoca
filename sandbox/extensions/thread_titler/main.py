"""Automatic thread titling with sync provisional titles and async AI refine."""

import hashlib
import logging
import re
import time
from typing import Any

from agents import Agent, ModelSettings, Runner

logger = logging.getLogger(__name__)

REFINE_REQUESTED_TOPIC = "thread.title.refine_requested"
TITLE_UPDATED_TOPIC = "thread.title.updated"
GENERIC_TITLES = {
    "help",
    "question",
    "problem",
    "issue",
    "bug",
    "hi",
    "hello",
    "hey",
    "привет",
    "вопрос",
    "помоги",
    "помощь",
    "проблема",
    "ошибка",
}


class ThreadTitlerExtension:
    """Owns thread title generation policy."""

    def __init__(self) -> None:
        self._ctx: Any = None
        self._title_max_length = 50
        self._refine_threshold = 80
        self._refine_agent: Agent | None = None

    async def initialize(self, context: Any) -> None:
        self._ctx = context
        self._title_max_length = int(context.get_config("title_max_length", 50))
        self._refine_threshold = int(context.get_config("refine_threshold", 80))
        context.subscribe("user_message", self._on_user_message)
        context.subscribe_event(REFINE_REQUESTED_TOPIC, self._on_refine_requested)

        if context.model_router:
            try:
                model = context.model_router.get_model("thread_title_agent")
                self._refine_agent = Agent(
                    name="ThreadTitleAgent",
                    instructions=(
                        "Generate a concise conversation title. "
                        "Return only the title text in the same language as the user. "
                        "Use at most 6 words, no quotes, no markdown, "
                        "no trailing punctuation."
                    ),
                    model=model,
                    model_settings=ModelSettings(parallel_tool_calls=False),
                )
            except Exception as exc:
                logger.warning("thread_titler: title refine model unavailable: %s", exc)

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def destroy(self) -> None:
        self._refine_agent = None

    def health_check(self) -> bool:
        return self._ctx is not None

    async def _on_user_message(self, payload: dict[str, Any]) -> None:
        thread_id = payload.get("thread_id")
        text = str(payload.get("text") or "").strip()
        if not thread_id or not text or not self._ctx:
            return
        thread = await self._ctx.get_thread(thread_id, include_archived=True)
        if thread is None or thread.title:
            return
        if thread.title_source == "manual":
            return

        provisional = self._build_provisional_title(text)
        if not provisional:
            return

        updated = await self._ctx.update_thread(
            thread_id,
            title=provisional,
            title_source="derived",
            title_status="provisional",
            title_updated_at=int(time.time()),
        )
        if updated is None:
            return
        await self._emit_title_updated(updated)

        if self._should_refine(text, provisional):
            await self._ctx.emit(
                REFINE_REQUESTED_TOPIC,
                {
                    "thread_id": thread_id,
                    "message_text": text,
                    "channel_id": updated.channel_id,
                    "message_fingerprint": hashlib.sha1(
                        text.encode("utf-8")
                    ).hexdigest()[:12],
                },
            )

    async def _on_refine_requested(self, event: Any) -> None:
        if not self._ctx or not self._refine_agent:
            return
        payload = event.payload
        thread_id = payload.get("thread_id")
        message_text = str(payload.get("message_text") or "").strip()
        if not thread_id or not message_text:
            return

        thread = await self._ctx.get_thread(thread_id, include_archived=True)
        if not self._can_refine(thread):
            return

        title = await self._generate_ai_title(message_text)
        if not title:
            return

        thread = await self._ctx.get_thread(thread_id, include_archived=True)
        if not self._can_refine(thread):
            return

        updated = await self._ctx.update_thread(
            thread_id,
            title=title,
            title_source="ai",
            title_status="finalized",
            title_updated_at=int(time.time()),
        )
        if updated is not None:
            await self._emit_title_updated(updated)

    def _build_provisional_title(self, text: str) -> str:
        normalized = self._normalize_text(text)
        if not normalized:
            return ""
        if len(normalized) <= self._title_max_length:
            return normalized

        cutoff = normalized[: self._title_max_length + 1]
        if " " in cutoff:
            cutoff = cutoff.rsplit(" ", 1)[0]
        cutoff = cutoff.strip(" ,;:-")
        if not cutoff:
            cutoff = normalized[: self._title_max_length].strip()
        return f"{cutoff}..."

    def _normalize_text(self, text: str) -> str:
        lines: list[str] = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("```"):
                continue
            line = re.sub(r"`([^`]*)`", r"\1", line)
            line = re.sub(r"^#{1,6}\s*", "", line)
            line = re.sub(r"^\s*[-*+]\s+", "", line)
            line = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1", line)
            line = re.sub(r"https?://\S+", "", line)
            line = re.sub(r"\s+", " ", line).strip()
            if line:
                lines.append(line)
            if lines:
                break
        return lines[0] if lines else ""

    def _should_refine(self, message_text: str, provisional: str) -> bool:
        if len(message_text) > self._refine_threshold:
            return True
        if self._looks_noisy(message_text) or self._looks_noisy(provisional):
            return True
        return provisional.strip(" .,!?:;").lower() in GENERIC_TITLES

    def _looks_noisy(self, text: str) -> bool:
        lowered = text.lower()
        return any(
            marker in lowered
            for marker in (
                "http://",
                "https://",
                "{",
                "}",
                "traceback",
                "error:",
                "```",
            )
        )

    def _can_refine(self, thread: Any) -> bool:
        return bool(
            thread
            and thread.title_source != "manual"
            and thread.title_status == "provisional"
        )

    async def _generate_ai_title(self, message_text: str) -> str | None:
        try:
            result = await Runner.run(
                self._refine_agent,
                (
                    "Create a short thread title for this first user message:\n\n"
                    f"{message_text}"
                ),
                max_turns=1,
            )
        except Exception as exc:
            logger.warning("thread_titler: refine failed: %s", exc)
            return None
        return self._sanitize_ai_title(result.final_output or "")

    def _sanitize_ai_title(self, text: str) -> str | None:
        cleaned = re.sub(r"\s+", " ", text.strip())
        cleaned = cleaned.strip("\"'`“”‘’")
        cleaned = cleaned.rstrip(" .,!?:;")
        if not cleaned:
            return None
        words = cleaned.split()
        if len(words) > 6:
            cleaned = " ".join(words[:6])
        if len(cleaned) > 60:
            cleaned = cleaned[:60].rsplit(" ", 1)[0].strip()
        cleaned = cleaned.strip(" .,!?:;")
        return cleaned or None

    async def _emit_title_updated(self, thread: Any) -> None:
        if not self._ctx:
            return
        await self._ctx.emit(
            TITLE_UPDATED_TOPIC,
            {
                "thread_id": thread.id,
                "title": thread.title,
                "title_source": thread.title_source,
                "title_status": thread.title_status,
                "title_updated_at": thread.title_updated_at,
            },
        )
