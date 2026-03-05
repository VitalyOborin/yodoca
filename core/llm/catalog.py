"""Model catalog: cost and capability metadata for routing decisions.

Separate from ModelRouter (which resolves agent_id → SDK Model instances).
"""

from dataclasses import dataclass
from typing import Any, Literal, cast

CostTier = Literal["free", "low", "medium", "high"]
CapabilityTier = Literal["basic", "standard", "advanced", "frontier"]

VALID_COST_TIERS: frozenset[str] = frozenset({"free", "low", "medium", "high"})
VALID_CAPABILITY_TIERS: frozenset[str] = frozenset(
    {"basic", "standard", "advanced", "frontier"}
)


@dataclass(frozen=True)
class ModelInfo:
    """Model metadata for cost/capability routing decisions."""

    id: str
    cost_tier: CostTier
    capability_tier: CapabilityTier
    strengths: tuple[str, ...]
    context_window: int | None = None


def _model_info(
    id: str,
    cost_tier: CostTier,
    capability_tier: CapabilityTier,
    strengths: tuple[str, ...],
    context_window: int | None = None,
) -> ModelInfo:
    """Helper to construct ModelInfo with typed strengths tuple."""
    return ModelInfo(
        id=id,
        cost_tier=cost_tier,
        capability_tier=capability_tier,
        strengths=strengths,
        context_window=context_window,
    )


_BUILTIN_CATALOG: dict[str, ModelInfo] = {
    "gpt-5-mini": _model_info(
        id="gpt-5-mini",
        cost_tier="low",
        capability_tier="standard",
        strengths=("speed", "general", "cost-effective"),
        context_window=128_000,
    ),
    "gpt-5": _model_info(
        id="gpt-5",
        cost_tier="medium",
        capability_tier="advanced",
        strengths=("reasoning", "general", "multilingual"),
        context_window=128_000,
    ),
    "gpt-5.2": _model_info(
        id="gpt-5.2",
        cost_tier="medium",
        capability_tier="advanced",
        strengths=("reasoning", "general", "tool-use"),
        context_window=128_000,
    ),
    "gpt-5.2-codex": _model_info(
        id="gpt-5.2-codex",
        cost_tier="high",
        capability_tier="frontier",
        strengths=("code", "reasoning", "tool-use"),
        context_window=256_000,
    ),
    "gpt-4o-mini": _model_info(
        id="gpt-4o-mini",
        cost_tier="low",
        capability_tier="standard",
        strengths=("speed", "general", "vision"),
        context_window=128_000,
    ),
    "gpt-4o": _model_info(
        id="gpt-4o",
        cost_tier="medium",
        capability_tier="advanced",
        strengths=("reasoning", "vision", "multilingual"),
        context_window=128_000,
    ),
    "mistralai/codestral-22b-v0.1": _model_info(
        id="mistralai/codestral-22b-v0.1",
        cost_tier="medium",
        capability_tier="advanced",
        strengths=("code", "speed"),
        context_window=128_000,
    ),
}


def _parse_overrides(overrides: dict[str, Any]) -> dict[str, ModelInfo]:
    """Parse settings models overrides. Raises ValueError on invalid tier."""
    result: dict[str, ModelInfo] = {}
    for model_id, raw in (overrides or {}).items():
        if not isinstance(raw, dict):
            continue
        cost_tier_raw = raw.get("cost_tier")
        capability_tier_raw = raw.get("capability_tier")
        if cost_tier_raw is not None:
            ct = str(cost_tier_raw).strip().lower()
            if ct not in VALID_COST_TIERS:
                raise ValueError(
                    f"Invalid cost_tier {cost_tier_raw!r} for model {model_id!r}. "
                    f"Valid: {sorted(VALID_COST_TIERS)}"
                )
        else:
            ct = "medium"  # default for new models
        if capability_tier_raw is not None:
            cpt = str(capability_tier_raw).strip().lower()
            if cpt not in VALID_CAPABILITY_TIERS:
                valid = sorted(VALID_CAPABILITY_TIERS)
                raise ValueError(
                    f"capability_tier {capability_tier_raw!r} invalid for "
                    f"{model_id!r}. Valid: {valid}"
                )
        else:
            cpt = "standard"
        strengths_raw = raw.get("strengths")
        if isinstance(strengths_raw, list):
            strengths = tuple(str(s) for s in strengths_raw)
        else:
            strengths = ()
        context_window = raw.get("context_window")
        if context_window is not None:
            context_window = int(context_window)
        result[str(model_id)] = ModelInfo(
            id=str(model_id),
            cost_tier=cast(CostTier, ct),
            capability_tier=cast(CapabilityTier, cpt),
            strengths=strengths,
            context_window=context_window,
        )
    return result


class ModelCatalog:
    """Maps model names to metadata. Built-in defaults + settings.yaml overrides."""

    VALID_COST_TIERS: frozenset[str] = VALID_COST_TIERS
    VALID_CAPABILITY_TIERS: frozenset[str] = VALID_CAPABILITY_TIERS

    def __init__(self, overrides: dict[str, Any] | None = None) -> None:
        """Build catalog from built-in defaults and optional settings overrides.

        Args:
            overrides: Optional dict from config (models section). Keys are model IDs.
                Validates tiers; raises ValueError on unknown values.
        """
        self._catalog: dict[str, ModelInfo] = dict(_BUILTIN_CATALOG)
        parsed = _parse_overrides(overrides)
        self._catalog.update(parsed)

    def get_info(self, model_name: str) -> ModelInfo | None:
        """Lookup model metadata by name. Returns None for unknown models."""
        if not model_name:
            return None
        return self._catalog.get(model_name.strip())

    def list_models(self) -> list[ModelInfo]:
        """Return all known models, sorted by id."""
        return sorted(self._catalog.values(), key=lambda m: m.id)
