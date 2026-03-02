"""CommunityManager: incremental label propagation, LLM summary generation. Memory v3 Tier 3."""

import asyncio
import json
import logging
import uuid
from collections import Counter
from pathlib import Path
from typing import Any, Callable

from agents import Agent, Runner
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class CommunitySummaryResult(BaseModel):
    """Structured output for community summary generation."""

    name: str = ""
    summary: str = ""


class CommunityManager:
    """Incremental community assignment via label propagation. O(neighbors) per new entity."""

    def __init__(
        self,
        storage: Any,
        embed_fn: Callable[[str], Any] | None,
        model: Any,
        config: dict[str, Any],
        extension_dir: Path | None = None,
    ) -> None:
        self._storage = storage
        self._embed_fn = embed_fn
        self._model = model
        self._config = config
        self._extension_dir = extension_dir or Path(__file__).resolve().parent
        self._min_shared_facts = config.get("community_min_shared_facts", 2)

    def _render_prompt(self, template_name: str, vars: dict[str, Any]) -> str:
        """Render Jinja2 prompt from prompts/."""
        from jinja2 import Environment, FileSystemLoader, select_autoescape

        prompts_dir = self._extension_dir / "prompts"
        env = Environment(
            loader=FileSystemLoader(str(prompts_dir)),
            autoescape=select_autoescape(enabled_extensions=()),
        )
        return env.get_template(template_name).render(**vars).strip()

    async def on_entity_added(self, entity_id: str) -> None:
        """Assign entity to community. O(neighbors). Must not block pipeline."""
        try:
            if await self._storage.get_entity_community(entity_id):
                return
            neighbors = await self._storage.get_neighboring_communities(
                entity_id, min_shared_facts=self._min_shared_facts
            )
            if not neighbors:
                await self._create_new_community(entity_id)
                return
            community_id = Counter(neighbors).most_common(1)[0][0]
            await self._storage.add_community_member(community_id, entity_id)
            await self._update_community_summary(community_id)
        except Exception as e:
            logger.warning("community on_entity_added failed for %s: %s", entity_id[:8], e)

    async def _create_new_community(self, entity_id: str) -> str:
        """Create new community with entity as sole member. Generate LLM summary."""
        entity = await self._storage.get_entity_by_id(entity_id)
        name = (entity or {}).get("name", "Unknown")
        community_id = str(uuid.uuid4())
        await self._storage.insert_community(
            {"id": community_id, "name": name, "summary": ""}
        )
        await self._storage.add_community_member(community_id, entity_id)
        await self._update_community_summary(community_id)
        return community_id

    async def _update_community_summary(self, community_id: str) -> None:
        """Fetch members + facts, generate LLM summary, update community and embedding."""
        members = await self._storage.get_community_members(community_id)
        facts = await self._storage.get_facts_for_community(community_id, limit=5)
        if not members:
            return
        entity_names = [m.get("name", "") for m in members]
        fact_lines = [
            f"[{f.get('subject_id','')}] --[{f.get('predicate','')}]--> [{f.get('object_id','')}]: {f.get('fact_text','')}"
            for f in facts
        ]
        prompt = self._render_prompt(
            "community.jinja2",
            {
                "entity_names": entity_names,
                "sample_facts": fact_lines,
            },
        )
        try:
            agent = Agent(
                name="MemoryCommunityAgent",
                instructions="Generate a 2-3 sentence thematic summary for this cluster.",
                model=self._model,
                output_type=CommunitySummaryResult,
            )
            result = await Runner.run(agent, prompt, max_turns=1)
            out = result.final_output
            if isinstance(out, CommunitySummaryResult):
                name = out.name or (entity_names[0] if entity_names else "Community")
                summary = out.summary or ""
            elif isinstance(out, str) and out.strip().startswith("{"):
                data = json.loads(out)
                name = data.get("name", entity_names[0] if entity_names else "Community")
                summary = data.get("summary", "")
            else:
                name = entity_names[0] if entity_names else "Community"
                summary = (out[:500] if isinstance(out, str) and out.strip() else "") or "Cluster of related entities."
            await self._storage.update_community(
                community_id, {"name": name, "summary": summary}
            )
            if self._embed_fn and summary:
                try:
                    emb = self._embed_fn(summary)
                    if asyncio.iscoroutine(emb):
                        emb = await emb
                    if emb and isinstance(emb, list):
                        await self._storage.save_community_embedding(community_id, emb)
                    elif emb and hasattr(emb, "embedding"):
                        await self._storage.save_community_embedding(
                            community_id, getattr(emb, "embedding")
                        )
                except Exception as e:
                    logger.debug("save_community_embedding failed: %s", e)
        except Exception as e:
            logger.warning("community summary generation failed for %s: %s", community_id[:8], e)
            fallback = f"Cluster including: {', '.join(entity_names[:5])}"
            await self._storage.update_community(
                community_id, {"summary": fallback}
            )

    async def periodic_refresh(self) -> int:
        """Full label propagation. Recompute community assignment for all entities."""
        processed = 0
        offset = 0
        batch = 500
        while True:
            entity_ids = await self._storage.get_all_entity_ids(limit=batch, offset=offset)
            if not entity_ids:
                break
            for entity_id in entity_ids:
                try:
                    old_community = await self._storage.get_entity_community(entity_id)
                    neighbors = await self._storage.get_neighboring_communities(
                        entity_id, min_shared_facts=self._min_shared_facts
                    )
                    if not neighbors:
                        if old_community:
                            await self._storage.remove_community_member(
                                old_community, entity_id
                            )
                            await self._update_community_summary(old_community)
                        else:
                            await self._create_new_community(entity_id)
                        processed += 1
                        continue
                    new_community = Counter(neighbors).most_common(1)[0][0]
                    if old_community != new_community:
                        if old_community:
                            await self._storage.remove_community_member(
                                old_community, entity_id
                            )
                            await self._update_community_summary(old_community)
                        await self._storage.add_community_member(new_community, entity_id)
                        await self._update_community_summary(new_community)
                        processed += 1
                    elif not old_community:
                        await self._storage.add_community_member(new_community, entity_id)
                        await self._update_community_summary(new_community)
                        processed += 1
                except Exception as e:
                    logger.debug("periodic_refresh entity %s: %s", entity_id[:8], e)
            offset += batch
        return processed
