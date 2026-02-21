"""RegexProvider: deterministic patterns for email, URL, @mention, #hashtag."""

import re
from typing import Any

from models import Entity, EntityType
from providers.base import NerProvider


_RE_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+", re.UNICODE)
_RE_URL = re.compile(r"https?://[^\s)>\],;]+", re.UNICODE)
_RE_MENTION = re.compile(r"@([a-zA-Z0-9_-]+)", re.UNICODE)
_RE_HASHTAG = re.compile(r"#([a-zA-Z0-9_а-яёА-ЯЁ]+)", re.UNICODE)


class RegexProvider(NerProvider):
    """Deterministic pattern matching for structured entities only."""

    @property
    def name(self) -> str:
        return "regex"

    async def extract(
        self, text: str, *, entity_types: list[str] | None = None
    ) -> list[Entity]:
        """Extract email, URL, @mention, #hashtag. No name/org/project recognition."""
        if not text or not text.strip():
            return []

        entities: list[Entity] = []
        type_filter = set(entity_types) if entity_types else None

        def add(entity_type: str, pattern: re.Pattern, canonical_fn: Any = None) -> None:
            if type_filter and entity_type not in type_filter:
                return
            for m in pattern.finditer(text):
                span = m.span()
                raw = m.group(0)
                canonical = canonical_fn(raw) if canonical_fn else raw
                entities.append(
                    Entity(
                        text=raw,
                        type=entity_type,
                        canonical=canonical,
                        confidence=1.0,
                        span=span,
                        provider=self.name,
                    )
                )

        add(EntityType.EMAIL, _RE_EMAIL, lambda s: s.lower())
        add(EntityType.URL, _RE_URL, lambda s: s.lower())
        add(EntityType.MENTION, _RE_MENTION)
        add(EntityType.HASHTAG, _RE_HASHTAG)

        return entities
