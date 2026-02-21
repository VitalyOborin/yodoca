"""NerProvider abstract base class."""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..models import Entity


class NerProvider(ABC):
    """Abstract base for NER providers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider identifier (e.g. 'regex', 'spacy', 'llm')."""

    @abstractmethod
    async def extract(
        self, text: str, *, entity_types: list[str] | None = None
    ) -> list["Entity"]:
        """Extract entities from text. Returns empty list on error."""

    async def initialize(self, config: dict[str, Any], context: Any) -> None:
        """Optional setup (load model, build client). Default: no-op."""

    def is_available(self) -> bool:
        """Return True if provider is ready. Default: True."""
        return True
