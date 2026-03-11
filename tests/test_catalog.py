"""Tests for ModelCatalog and cost/capability routing."""

import pytest

from core.llm.catalog import (
    VALID_CAPABILITY_TIERS,
    VALID_COST_TIERS,
    ModelCatalog,
)


class TestModelCatalog:
    def test_get_info_unknown_returns_default(self) -> None:
        catalog = ModelCatalog()
        info = catalog.get_info("nonexistent-model")
        assert info is not None
        assert info.id == "nonexistent-model"
        assert info.cost_tier == "medium"
        assert info.capability_tier == "standard"
        assert info.strengths == ()
        assert info.context_window is None

    def test_get_info_empty_string_returns_none(self) -> None:
        catalog = ModelCatalog()
        assert catalog.get_info("") is None

    def test_list_models_returns_sorted(self) -> None:
        catalog = ModelCatalog()
        models = catalog.list_models()
        ids = [m.id for m in models]
        assert ids == sorted(ids)

    def test_list_models_empty_without_overrides(self) -> None:
        catalog = ModelCatalog()
        assert catalog.list_models() == []

    def test_overrides_override_model(self) -> None:
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
