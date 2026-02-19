"""Tests for ExtensionManifest and load_manifest."""

import pytest
from pathlib import Path

from core.extensions.manifest import ExtensionManifest, load_manifest
from pydantic import ValidationError


class TestExtensionManifest:
    """ExtensionManifest model validation."""

    def test_valid_minimal(self) -> None:
        data = {"id": "foo", "name": "Foo", "entrypoint": "main:Foo"}
        m = ExtensionManifest.model_validate(data)
        assert m.id == "foo"
        assert m.name == "Foo"
        assert m.entrypoint == "main:Foo"
        assert m.version == "1.0.0"
        assert m.enabled is True
        assert m.depends_on == []
        assert m.config == {}

    def test_valid_full(self) -> None:
        data = {
            "id": "bar",
            "name": "Bar",
            "version": "2.0.0",
            "entrypoint": "main:Bar",
            "description": "NL",
            "setup_instructions": "Setup",
            "depends_on": ["kv"],
            "secrets": ["token"],
            "config": {"key": "value"},
            "enabled": False,
        }
        m = ExtensionManifest.model_validate(data)
        assert m.id == "bar"
        assert m.version == "2.0.0"
        assert m.depends_on == ["kv"]
        assert m.config == {"key": "value"}
        assert m.enabled is False

    def test_missing_required_id(self) -> None:
        with pytest.raises(ValidationError):
            ExtensionManifest.model_validate({"name": "X", "entrypoint": "main:X"})

    def test_missing_required_entrypoint(self) -> None:
        with pytest.raises(ValidationError):
            ExtensionManifest.model_validate({"id": "x", "name": "X"})


class TestLoadManifest:
    """load_manifest from filesystem."""

    def test_load_manifest_from_path(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "manifest.yaml"
        yaml_path.write_text(
            "id: my_ext\nname: My Ext\nentrypoint: main:MyClass\n",
            encoding="utf-8",
        )
        m = load_manifest(yaml_path)
        assert m.id == "my_ext"
        assert m.name == "My Ext"
        assert m.entrypoint == "main:MyClass"

    def test_load_manifest_invalid_yaml_not_dict(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "manifest.yaml"
        yaml_path.write_text("- a\n- b\n", encoding="utf-8")
        with pytest.raises(ValueError, match="Manifest must be a YAML object"):
            load_manifest(yaml_path)

    def test_load_manifest_invalid_yaml_scalar(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "manifest.yaml"
        yaml_path.write_text("just a string\n", encoding="utf-8")
        with pytest.raises(ValueError, match="Manifest must be a YAML object"):
            load_manifest(yaml_path)

    def test_load_manifest_validation_error(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "manifest.yaml"
        yaml_path.write_text("id: x\n", encoding="utf-8")  # missing name, entrypoint
        with pytest.raises(ValidationError):
            load_manifest(yaml_path)
