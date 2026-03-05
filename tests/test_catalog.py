"""Tests for ModelCatalog and cost/capability routing."""

import pytest

from core.llm.catalog import (
    ModelCatalog,
    VALID_CAPABILITY_TIERS,
    VALID_COST_TIERS,
)


class TestModelCatalog:
    def test_get_info_returns_builtin(self) -> None:
        catalog = ModelCatalog()
        info = catalog.get_info("gpt-5-mini")
        assert info is not None
        assert info.id == "gpt-5-mini"
        assert info.cost_tier == "low"
        assert info.capability_tier == "standard"
        assert "speed" in info.strengths

    def test_get_info_unknown_returns_none(self) -> None:
        catalog = ModelCatalog()
        assert catalog.get_info("nonexistent-model") is None

    def test_get_info_empty_string_returns_none(self) -> None:
        catalog = ModelCatalog()
        assert catalog.get_info("") is None

    def test_list_models_returns_sorted(self) -> None:
        catalog = ModelCatalog()
        models = catalog.list_models()
        assert len(models) >= 5
        ids = [m.id for m in models]
        assert ids == sorted(ids)

    def test_overrides_merge_with_builtin(self) -> None:
        catalog = ModelCatalog(
            overrides={
                "gpt-5-mini": {
                    "cost_tier": "free",
                    "capability_tier": "basic",
                    "strengths": ["custom"],
                }
            }
        )
        info = catalog.get_info("gpt-5-mini")
        assert info is not None
        assert info.cost_tier == "free"
        assert info.capability_tier == "basic"
        assert info.strengths == ("custom",)

    def test_overrides_add_new_model(self) -> None:
        catalog = ModelCatalog(
            overrides={
                "my-local-model": {
                    "cost_tier": "free",
                    "capability_tier": "basic",
                    "strengths": ["privacy", "speed"],
                }
            }
        )
        info = catalog.get_info("my-local-model")
        assert info is not None
        assert info.cost_tier == "free"
        assert info.capability_tier == "basic"

    def test_invalid_cost_tier_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid cost_tier"):
            ModelCatalog(
                overrides={
                    "bad-model": {"cost_tier": "cheap", "capability_tier": "basic"}
                }
            )

    def test_invalid_capability_tier_raises(self) -> None:
        with pytest.raises(ValueError, match="capability_tier.*invalid"):
            ModelCatalog(
                overrides={
                    "bad-model": {"cost_tier": "low", "capability_tier": "super"}
                }
            )


class TestLiteralTypes:
    def test_valid_cost_tiers(self) -> None:
        assert VALID_COST_TIERS == {"free", "low", "medium", "high"}

    def test_valid_capability_tiers(self) -> None:
        assert VALID_CAPABILITY_TIERS == {"basic", "standard", "advanced", "frontier"}
