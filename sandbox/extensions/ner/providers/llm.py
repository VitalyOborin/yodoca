"""LlmProvider: NER via LLM structured output."""

import json
import logging
from typing import Any

from models import Entity, EntityType
from providers.base import NerProvider

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """Extract all named entities from the user's text.
Return a JSON object with a single key "entities" containing an array of objects.
Each object must have: "text" (exact substring), "type" (person|organization|project|location|other), "canonical" (normalized form, or null).
Extract people, organizations, projects, locations, and other notable entities. Do not include URLs or emails."""


class LlmProvider(NerProvider):
    """LLM-based NER via structured JSON output. Uses model_router for client."""

    def __init__(self) -> None:
        self._client: Any = None
        self._model: str | None = None
        self._provider_id: str | None = None

    @property
    def name(self) -> str:
        return "llm"

    def is_available(self) -> bool:
        return self._client is not None

    async def initialize(self, config: dict[str, Any], context: Any) -> None:
        if not config.get("enabled", False):
            return
        self._provider_id = config.get("provider")
        self._model = config.get("model")
        router = getattr(context, "model_router", None)
        if router:
            self._client = router.get_provider_client(self._provider_id)
        if not self._client:
            logger.warning("LlmProvider: no OpenAI-compatible client, disabled")

    async def extract(
        self, text: str, *, entity_types: list[str] | None = None
    ) -> list[Entity]:
        if not self._client or not text or not text.strip():
            return []

        type_filter = set(entity_types) if entity_types else None

        try:
            resp = await self._client.chat.completions.create(
                model=self._model or "gpt-4o-mini",
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": text},
                ],
                response_format={"type": "json_object"},
                max_tokens=500,
            )
            content = resp.choices[0].message.content
            if not content:
                return []
            data = json.loads(content)
            raw_entities = data.get("entities", [])
            if not isinstance(raw_entities, list):
                return []
        except Exception as e:
            logger.warning("LlmProvider extract failed: %s", e)
            return []

        entities: list[Entity] = []
        type_map = {
            "person": EntityType.PERSON,
            "organization": EntityType.ORGANIZATION,
            "org": EntityType.ORGANIZATION,
            "project": EntityType.PROJECT,
            "location": EntityType.LOCATION,
            "loc": EntityType.LOCATION,
            "other": EntityType.OTHER,
        }
        for item in raw_entities:
            if not isinstance(item, dict):
                continue
            text_val = item.get("text") or item.get("entity")
            if not text_val or not isinstance(text_val, str):
                continue
            type_val = (item.get("type") or "other").lower()
            mapped = type_map.get(type_val, EntityType.OTHER)
            if type_filter and mapped not in type_filter:
                continue
            canonical = item.get("canonical")
            if canonical is not None and not isinstance(canonical, str):
                canonical = None
            entities.append(
                Entity(
                    text=text_val.strip(),
                    type=mapped,
                    canonical=canonical,
                    confidence=0.9,
                    span=(-1, -1),
                    provider=self.name,
                )
            )
        return entities
