"""SpacyProvider: local spaCy NER model."""

import asyncio
import logging
from typing import Any

from models import Entity, EntityType
from providers.base import NerProvider

logger = logging.getLogger(__name__)

_SPACY_LABEL_MAP = {
    "PER": EntityType.PERSON,
    "PERSON": EntityType.PERSON,
    "ORG": EntityType.ORGANIZATION,
    "ORGANIZATION": EntityType.ORGANIZATION,
    "LOC": EntityType.LOCATION,
    "LOCATION": EntityType.LOCATION,
    "GPE": EntityType.LOCATION,
    "MISC": EntityType.OTHER,
}


class SpacyProvider(NerProvider):
    """Local spaCy NER. Optional dependency; is_available=False if not installed."""

    def __init__(self) -> None:
        self._nlp: Any = None
        self._model_name: str = "xx_ent_wiki_sm"

    @property
    def name(self) -> str:
        return "spacy"

    def is_available(self) -> bool:
        return self._nlp is not None

    async def initialize(self, config: dict[str, Any], context: Any) -> None:
        if not config.get("enabled", False):
            return
        self._model_name = config.get("model", "xx_ent_wiki_sm")
        try:
            import spacy

            self._nlp = spacy.load(self._model_name)
        except ImportError:
            logger.warning("spacy not installed, SpacyProvider disabled")
        except Exception as e:
            logger.warning("spacy model %s failed to load: %s", self._model_name, e)

    async def extract(
        self, text: str, *, entity_types: list[str] | None = None
    ) -> list[Entity]:
        if not self._nlp or not text or not text.strip():
            return []

        type_filter = set(entity_types) if entity_types else None
        loop = asyncio.get_event_loop()

        def _run() -> list[Entity]:
            doc = self._nlp(text)
            entities: list[Entity] = []
            for ent in doc.ents:
                mapped = _SPACY_LABEL_MAP.get(ent.label_, EntityType.OTHER)
                if type_filter and mapped not in type_filter:
                    continue
                entities.append(
                    Entity(
                        text=ent.text,
                        type=mapped,
                        canonical=None,
                        confidence=0.75,
                        span=(ent.start_char, ent.end_char),
                        provider=self.name,
                    )
                )
            return entities

        return await loop.run_in_executor(None, _run)
