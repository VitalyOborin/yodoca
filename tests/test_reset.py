"""Tests for the reset maintenance script."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import patch


def _load_reset_module():
    reset_path = Path(__file__).resolve().parent.parent / "scripts" / "reset.py"
    spec = spec_from_file_location("reset_script", reset_path)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_clear_directory_contents_removes_nested_entries(tmp_path: Path) -> None:
    """sandbox/data and sandbox/logs should be emptied recursively."""
    reset = _load_reset_module()
    root = tmp_path
    nested_dir = root / "sandbox" / "data" / "memory"
    nested_dir.mkdir(parents=True)
    (nested_dir / "chunk.json").write_text("{}", encoding="utf-8")
    (root / "sandbox" / "data" / "event_journal.db").write_text("db", encoding="utf-8")

    removed, errors = reset._clear_directory_contents(root, "sandbox/data")

    assert removed == 2
    assert errors == []
    assert list((root / "sandbox" / "data").iterdir()) == []


def test_clear_saved_secrets_uses_registry_env_settings_and_manifests(
    tmp_path: Path,
) -> None:
    """Reset should clear every secret name known to the application."""
    reset = _load_reset_module()
    root = tmp_path
    (root / "config").mkdir(parents=True)
    (root / "sandbox" / "extensions" / "telegram_channel").mkdir(parents=True)

    (root / "config" / "settings.yaml").write_text(
        "providers:\n  openai:\n    api_key_secret: OPENAI_API_KEY\n",
        encoding="utf-8",
    )
    (root / ".env").write_text("ENV_ONLY=value\n", encoding="utf-8")
    (root / "sandbox" / "extensions" / "telegram_channel" / "manifest.yaml").write_text(
        "config:\n  token_secret: telegram_bot_token\nsecrets:\n  - manifest_declared\n",
        encoding="utf-8",
    )

    with (
        patch("core.secrets.list_registered_secrets", return_value={"REGISTRY_ONLY"}),
        patch("core.secrets.delete_secret") as mock_delete,
    ):
        removed, errors = reset._clear_saved_secrets(root)

    cleared = {call.args[0] for call in mock_delete.call_args_list}
    assert removed == 5
    assert errors == []
    assert cleared == {
        "ENV_ONLY",
        "OPENAI_API_KEY",
        "REGISTRY_ONLY",
        "manifest_declared",
        "telegram_bot_token",
    }
