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


DEFAULT_MODEL_INFO = ModelInfo(
    id="",
    cost_tier="medium",
    capability_tier="standard",
    strengths=(),
    context_window=None,
)


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
    """Maps model names to metadata. Settings overrides + default fallback for unknowns."""

    VALID_COST_TIERS: frozenset[str] = VALID_COST_TIERS
    VALID_CAPABILITY_TIERS: frozenset[str] = VALID_CAPABILITY_TIERS

    def __init__(self, overrides: dict[str, Any] | None = None) -> None:
        """Build catalog from settings overrides (models section).

        Args:
            overrides: Optional dict from config (models section). Keys are model IDs.
                Validates tiers; raises ValueError on unknown values.
        """
        self._catalog: dict[str, ModelInfo] = _parse_overrides(overrides)

    def get_info(self, model_name: str) -> ModelInfo | None:
        """Lookup model metadata by name. Returns default (medium/standard) for unknown models."""
        if not model_name:
            return None
        key = model_name.strip()
        info = self._catalog.get(key)
        if info is not None:
            return info
        return ModelInfo(
            id=key,
            cost_tier=DEFAULT_MODEL_INFO.cost_tier,
            capability_tier=DEFAULT_MODEL_INFO.capability_tier,
            strengths=DEFAULT_MODEL_INFO.strengths,
            context_window=DEFAULT_MODEL_INFO.context_window,
        )

    def list_models(self) -> list[ModelInfo]:
        """Return explicitly configured models from settings, sorted by id."""
        return sorted(self._catalog.values(), key=lambda m: m.id)
