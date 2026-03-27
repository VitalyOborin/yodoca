"""Architecture tests for extension import conventions."""

import re
import sys
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


def test_extensions_do_not_use_file_based_import_fallbacks() -> None:
    """Extension runtime code should rely on package imports, not file-based loading."""
    root = Path(__file__).resolve().parent.parent / "sandbox" / "extensions"
    offenders: list[str] = []
    for py_file in root.rglob("*.py"):
        if "__pycache__" in py_file.parts:
            continue
        content = py_file.read_text(encoding="utf-8")
        if "spec_from_file_location(" in content:
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


def test_extension_factory_supports_relative_imports_in_package_loaded_extensions(
    tmp_path: Path,
) -> None:
    """Relative imports should work when extensions are imported as packages."""
    extensions_root = tmp_path / "sandbox" / "extensions"
    ext_dir = extensions_root / "tmp_relative"
    ext_dir.mkdir(parents=True)
    (tmp_path / "sandbox" / "__init__.py").write_text("", encoding="utf-8")
    (extensions_root / "__init__.py").write_text("", encoding="utf-8")
    (ext_dir / "__init__.py").write_text("", encoding="utf-8")
    (ext_dir / "helper.py").write_text(
        "class Helper:\n    value = 'ok'\n",
        encoding="utf-8",
    )
    (ext_dir / "main.py").write_text(
        "from .helper import Helper\n\n"
        "class TmpRelativeExtension:\n"
        "    def __init__(self):\n"
        "        self.helper = Helper()\n",
        encoding="utf-8",
    )
    manifest = ExtensionManifest(
        id="tmp_relative",
        name="Tmp Relative",
        entrypoint="main:TmpRelativeExtension",
    )
    saved_modules = {
        name: sys.modules.get(name)
        for name in (
            "sandbox",
            "sandbox.extensions",
            "sandbox.extensions.tmp_relative",
            "sandbox.extensions.tmp_relative.main",
            "sandbox.extensions.tmp_relative.helper",
        )
    }
    sys.path.insert(0, str(tmp_path))
    try:
        for name in saved_modules:
            sys.modules.pop(name, None)
        factory = ExtensionFactory(extensions_dir=extensions_root)
        ext = factory.create(manifest)
    finally:
        sys.path.remove(str(tmp_path))
        for name, module in saved_modules.items():
            if module is not None:
                sys.modules[name] = module

    assert ext.helper.value == "ok"


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
