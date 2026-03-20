"""Architecture tests for extension import conventions."""

import re
from pathlib import Path

from core.extensions.loader.extension_factory import ExtensionFactory
from core.extensions.manifest import ExtensionManifest


def test_extensions_do_not_mutate_sys_path() -> None:
    """Extension runtime code should not add extension directories to sys.path."""
    root = Path(__file__).resolve().parent.parent / "sandbox" / "extensions"
    offenders: list[str] = []
    for py_file in root.rglob("*.py"):
        if "__pycache__" in py_file.parts:
            continue
        content = py_file.read_text(encoding="utf-8")
        if "sys.path.insert(" in content:
            offenders.append(str(py_file.relative_to(root.parent.parent)))
    assert offenders == []


def test_extension_factory_loads_extensions_via_package_imports() -> None:
    """Smoke test: factory can instantiate package-imported extension entrypoints."""
    extensions_dir = Path(__file__).resolve().parent.parent / "sandbox" / "extensions"
    factory = ExtensionFactory(extensions_dir=extensions_dir)
    inbox_manifest = ExtensionManifest(
        id="inbox",
        name="Inbox",
        entrypoint="main:InboxExtension",
    )
    telegram_manifest = ExtensionManifest(
        id="telegram_channel",
        name="Telegram Channel",
        entrypoint="main:TelegramChannelExtension",
    )

    inbox = factory.create(inbox_manifest)
    telegram = factory.create(telegram_manifest)

    assert inbox.__class__.__name__ == "InboxExtension"
    assert telegram.__class__.__name__ == "TelegramChannelExtension"


def test_no_synthetic_extension_module_name_dependencies() -> None:
    """Code and tests should not depend on legacy synthetic ext_*_main names."""
    root = Path(__file__).resolve().parent.parent
    pattern = re.compile(r"\bext_[a-z0-9_]+_main\b")
    offenders: list[str] = []
    for py_file in (root / "core").rglob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        if pattern.search(content):
            offenders.append(str(py_file.relative_to(root)))
    for py_file in (root / "sandbox" / "extensions").rglob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        if pattern.search(content):
            offenders.append(str(py_file.relative_to(root)))
    for py_file in (root / "tests").rglob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        if pattern.search(content):
            offenders.append(str(py_file.relative_to(root)))

    assert offenders == []
