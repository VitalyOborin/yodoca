"""Entity linker: maps NER Entity to memory entity storage and orchestrates create/link."""

from typing import Any

# NER Entity has: text, type, canonical, confidence, span, provider
# We use duck typing - no import from ner to avoid coupling


_TYPE_MAP = {
    "mention": "person",
    "hashtag": "project",
    "email": "email",
    "url": "url",
    "person": "person",
    "organization": "organization",
    "project": "project",
    "location": "location",
    "other": "other",
}


def _to_memory_entity(ner_entity: Any) -> dict[str, Any]:
    """Map NER Entity to memory entity params (canonical_name, entity_type, aliases)."""
    raw = ner_entity.text
    canonical = ner_entity.canonical or raw
    etype = _TYPE_MAP.get(ner_entity.type, ner_entity.type)

    # Strip @ / # prefixes for canonical_name, keep original as alias
    if ner_entity.type == "mention":
        clean = (canonical or raw).lstrip("@")
        return {"canonical_name": clean, "entity_type": etype, "aliases": [raw]}
    if ner_entity.type == "hashtag":
        clean = (canonical or raw).lstrip("#")
        return {"canonical_name": clean, "entity_type": etype, "aliases": [raw]}
    return {"canonical_name": canonical or raw, "entity_type": etype, "aliases": []}


async def extract_and_link(
    ner_ext: Any,
    repo: Any,
    memory_id: str,
    content: str,
    *,
    strategy: str = "fast",
) -> list[str]:
    """Extract entities via NER, map to memory types, create/get + link. Returns entity_ids."""
    ner_entities = await ner_ext.extract(content, strategy=strategy)
    if not ner_entities:
        return []
    entity_ids: list[str] = []
    for ne in ner_entities:
        mapped = _to_memory_entity(ne)
        eid = await repo.create_or_get_entity(**mapped)
        entity_ids.append(eid)
    if entity_ids:
        await repo.link_memory_to_entities(memory_id, entity_ids)
    return entity_ids
