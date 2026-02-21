"""NER extension: multi-provider Named Entity Recognition with strategy pipelines."""

import hashlib
import logging
import sys
import time
from pathlib import Path
from typing import Any

_ext_dir = Path(__file__).resolve().parent
if str(_ext_dir) not in sys.path:
    sys.path.insert(0, str(_ext_dir))

from models import Entity
from providers import LlmProvider, RegexProvider, SpacyProvider
from providers.base import NerProvider

logger = logging.getLogger(__name__)


def _spans_overlap(span1: tuple[int, int], span2: tuple[int, int]) -> bool:
    """Check if two character spans overlap."""
    return not (span1[1] <= span2[0] or span2[1] <= span1[0])


class NerExtension:
    """Multi-provider NER with strategy-based pipeline composition."""

    def __init__(self) -> None:
        self._providers: dict[str, NerProvider] = {}
        self._pipelines: dict[str, list[NerProvider]] = {}
        self._strategy_config: dict[str, dict[str, Any]] = {}
        self._cache: dict[str, list[Entity]] = {}
        self._cache_max_size: int = 512

    async def extract(
        self,
        text: str,
        *,
        strategy: str = "fast",
        entity_types: list[str] | None = None,
    ) -> list[Entity]:
        """Extract entities using configured strategy."""
        if strategy not in self._strategy_config:
            logger.warning("Unknown NER strategy '%s', using 'fast'", strategy)
            strategy = "fast"

        cfg = self._strategy_config.get(strategy, {})
        cache_key: str | None = None
        if cfg.get("cache", False):
            types_key = ",".join(sorted(entity_types)) if entity_types else ""
            cache_key = f"{strategy}:{types_key}:{hashlib.md5(text.encode()).hexdigest()}"
            if cache_key in self._cache:
                return self._cache[cache_key]

        start = time.monotonic()
        result = await self._run_pipeline(text, strategy, entity_types)
        elapsed_ms = (time.monotonic() - start) * 1000

        max_latency = cfg.get("max_latency_ms")
        if max_latency and elapsed_ms > max_latency:
            logger.warning(
                "NER strategy '%s': %.1fms (max %dms)",
                strategy,
                elapsed_ms,
                max_latency,
            )

        if cache_key is not None:
            if len(self._cache) >= self._cache_max_size:
                self._cache.pop(next(iter(self._cache)))
            self._cache[cache_key] = result

        return result

    async def _run_pipeline(
        self,
        text: str,
        strategy: str,
        entity_types: list[str] | None,
    ) -> list[Entity]:
        """Execute provider pipeline and merge results."""
        pipeline = self._pipelines.get(strategy, [])
        all_entities: list[Entity] = []
        for provider in pipeline:
            try:
                entities = await provider.extract(text, entity_types=entity_types)
                all_entities.extend(entities)
            except Exception as e:
                logger.error("NER provider '%s' failed: %s", provider.name, e)
        return self._deduplicate(all_entities)

    def _deduplicate(self, entities: list[Entity]) -> list[Entity]:
        """Merge overlapping entities, prefer higher confidence + longer span."""
        if not entities:
            return []

        groups: list[list[Entity]] = []
        for entity in entities:
            merged = False
            for group in groups:
                if any(
                    _spans_overlap(entity.span, e.span) for e in group
                ) or (
                    entity.span == (-1, -1)
                    and any(
                        e.text.lower() == entity.text.lower()
                        for e in group
                    )
                ):
                    group.append(entity)
                    merged = True
                    break
            if not merged:
                groups.append([entity])

        merged_list: list[Entity] = []
        for group in groups:
            best = max(
                group,
                key=lambda e: (e.confidence, e.span[1] - e.span[0]),
            )
            merged_list.append(best)
        return merged_list

    async def initialize(self, context: Any) -> None:
        """Build providers and pipelines from config."""
        providers_cfg = context.get_config("providers", {}) or {}
        strategies_cfg = context.get_config("strategies", {}) or {}
        self._cache_max_size = context.get_config("cache_max_size", 512)

        provider_classes = {
            "regex": RegexProvider,
            "spacy": SpacyProvider,
            "llm": LlmProvider,
        }
        for name, cls in provider_classes.items():
            pcfg = providers_cfg.get(name, {}) or {}
            enabled = pcfg.get("enabled", name == "regex")
            if not enabled:
                continue
            provider = cls()
            await provider.initialize(pcfg, context)
            self._providers[name] = provider

        for name, cfg in strategies_cfg.items():
            pipeline_names = cfg.get("pipeline", [])
            available = [
                self._providers[p]
                for p in pipeline_names
                if p in self._providers and self._providers[p].is_available()
            ]
            self._pipelines[name] = available
            self._strategy_config[name] = cfg

        if not self._pipelines and "regex" in self._providers:
            default_strategy = {
                "pipeline": ["regex"],
                "cache": True,
                "max_latency_ms": 10,
            }
            self._pipelines["fast"] = [self._providers["regex"]]
            self._strategy_config["fast"] = default_strategy

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def destroy(self) -> None:
        self._providers.clear()
        self._pipelines.clear()
        self._strategy_config.clear()
        self._cache.clear()

    def health_check(self) -> bool:
        """True if at least one provider is available."""
        return any(p.is_available() for p in self._providers.values())

    def has_provider(self, name: str) -> bool:
        """Check if a named provider is loaded and available."""
        return name in self._providers and self._providers[name].is_available()
