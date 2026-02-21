"""NER domain types: Entity and EntityType."""

from dataclasses import dataclass
from enum import StrEnum


class EntityType(StrEnum):
    """Standard entity types. Providers may return custom string types."""

    PERSON = "person"
    ORGANIZATION = "organization"
    PROJECT = "project"
    LOCATION = "location"
    URL = "url"
    EMAIL = "email"
    HASHTAG = "hashtag"
    MENTION = "mention"
    OTHER = "other"


@dataclass(frozen=True)
class Entity:
    """A single extracted entity from text."""

    text: str
    type: str
    canonical: str | None
    confidence: float
    span: tuple[int, int]
    provider: str
