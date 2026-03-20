# Architecture Boundary Checks

Automated checks that protect the extension system's clean architecture from gradual erosion as the extension catalog grows.

---

## Overview

The extension system is the core differentiator of assistant4. These boundary enforcement scripts act as its immune system, catching violations that would silently couple extensions to each other's internals or to core implementation details.

All checks run in CI (GitHub Actions quality job) and are available as pre-commit hooks for local development.

| Check | Script | What it catches |
|-------|--------|-----------------|
| **Extension boundaries** | `scripts/check_extension_boundaries.py` | Extensions importing another extension's code without declaring it in `depends_on` |
| **Core API boundary** | `scripts/check_extension_context_boundary.py` | Extensions importing internal core modules instead of using the public API |
| **Max LOC** | `scripts/check_max_loc.py` | Python files exceeding a line-count threshold |
| **Dead code** | `vulture` (pyproject.toml config) | Unused Python code (non-blocking in CI) |
| **Core independence** | `import-linter` (pyproject.toml config) | Core importing from sandbox or extensions |

---

## Extension Boundary Check

**Script:** `scripts/check_extension_boundaries.py`

**Rule:** An extension may only import `sandbox.extensions.<other_id>` if `<other_id>` appears in the extension's `depends_on` list (or is the extension itself).

**How it works:**

1. Parses `sandbox/extensions/*/manifest.yaml` to build a map of `extension_id` to `depends_on`
2. For each extension, AST-scans all `.py` files for `import` and `from ... import` statements
3. Extracts the target extension ID from `sandbox.extensions.<id>.*` patterns
4. Reports a violation if the target ID is not in `depends_on` and is not the extension itself

**Example violation:**

```
sandbox/extensions/my_ext/main.py:5: imports sandbox.extensions.kv
via 'sandbox.extensions.kv.models' but 'kv' is not in depends_on
for extension 'my_ext'
```

**Fix:** Either add the dependency to `depends_on` in `manifest.yaml`, or use `context.get_extension()` at runtime (which already enforces `depends_on`).

**Run locally:**

```bash
uv run python scripts/check_extension_boundaries.py
uv run python scripts/check_extension_boundaries.py --verbose  # per-file OK lines
```

---

## Core API Boundary Check

**Script:** `scripts/check_extension_context_boundary.py`

**Rule:** Extensions may only import from the public core API. Internal core modules (loader, routing, persistence internals, agent orchestration, LLM routing, etc.) are forbidden.

### Public Core API Allowlist

| Allowed module | Purpose |
|----------------|---------|
| `core.extensions` | Top-level re-exports (`ExtensionContext`, protocols) |
| `core.extensions.context` | `ExtensionContext` class |
| `core.extensions.contract` | Extension protocols (`TurnContext`, `AgentProvider`, etc.) |
| `core.extensions.manifest` | Manifest types |
| `core.extensions.persistence.models` | `ThreadInfo`, `ProjectInfo` data models |
| `core.extensions.update_fields` | `UNSET` sentinel |
| `core.extensions.instructions` | `resolve_instructions` helper |
| `core.extensions.declarative_agent` | Declarative agent base |
| `core.events.topics` | `SystemTopics` constants |
| `core.events.models` | `Event` model |
| `core.llm.capabilities` | Capability protocols (`EmbeddingCapability`) |
| `core.utils.formatting` | Shared formatting helpers |

Everything else under `core.*` is internal: `core.extensions.loader.*`, `core.extensions.routing.*`, `core.agents.*`, `core.llm.router`, `core.events.bus`, `core.runner`, `core.settings`, `core.settings_models`, etc.

The allowlist lives in the script as `_ALLOWED_PREFIXES` and `_ALLOWED_EXACT`, making it easy to evolve as the public API grows.

**Example violation:**

```
sandbox/extensions/my_ext/main.py:3: forbidden core import
'core.extensions.loader.loader' (not in public extension API allowlist)
```

**Fix:** Use `ExtensionContext` methods instead of reaching into core internals. If a legitimate need exists, discuss adding the module to the allowlist.

**Run locally:**

```bash
uv run python scripts/check_extension_context_boundary.py
uv run python scripts/check_extension_context_boundary.py --verbose
```

---

## Max LOC Check

**Script:** `scripts/check_max_loc.py`

**Rule:** Python files under `core/` and `sandbox/extensions/` must not exceed 500 lines (configurable).

**Allowlist:** Files that already exceed the threshold are listed in `scripts/max_loc_allowlist.txt`. These are grandfathered; remove entries as modules are refactored.

Current grandfathered files:

- `core/extensions/loader/loader.py`
- `sandbox/extensions/memory/main.py`
- `sandbox/extensions/memory/retrieval.py`
- `sandbox/extensions/memory/storage.py`
- `sandbox/extensions/memory/tools.py`
- `sandbox/extensions/scheduler/main.py`
- `sandbox/extensions/task_engine/main.py`
- `sandbox/extensions/task_engine/worker.py`
- `sandbox/extensions/web_channel/routes_api.py`

**Run locally:**

```bash
uv run python scripts/check_max_loc.py --allowlist scripts/max_loc_allowlist.txt
uv run python scripts/check_max_loc.py --max-lines 300  # stricter threshold, no allowlist
uv run python scripts/check_max_loc.py --verbose         # list all files and their counts
```

---

## Dead Code Detection (vulture)

**Tool:** [vulture](https://github.com/jendrikseipp/vulture) (configured in `pyproject.toml`)

**Rule:** Report unused Python code at 80%+ confidence. Runs as a non-blocking CI step (`continue-on-error: true`) while the baseline stabilizes.

**Configuration** (from `pyproject.toml`):

```toml
[tool.vulture]
min_confidence = 80
paths = ["core", "sandbox/extensions", "onboarding", "supervisor", "scripts/vulture_allowlist.py"]
exclude = [".venv", "tests", "**/__pycache__"]
```

**Allowlist:** `scripts/vulture_allowlist.py` is a Python file where you reference symbols that vulture incorrectly flags as dead (dynamic entrypoints, protocol methods, etc.). Add a bare reference to silence a false positive:

```python
from sandbox.extensions.my_ext.main import MyExtension
MyExtension.initialize  # loaded dynamically by Loader
```

**Run locally:**

```bash
uv run vulture --config pyproject.toml
```

---

## CI Integration

All checks run in the `quality` job of `.github/workflows/ci.yml`, after ruff and import-linter:

```yaml
- name: Extension boundary check
  run: uv run python scripts/check_extension_boundaries.py

- name: Extension context boundary check
  run: uv run python scripts/check_extension_context_boundary.py

- name: Max LOC check
  run: uv run python scripts/check_max_loc.py --allowlist scripts/max_loc_allowlist.txt

- name: Dead code (vulture)
  run: uv run vulture --config pyproject.toml
  continue-on-error: true
```

The first three are blocking (exit 1 fails the job). Vulture is advisory for now.

---

## Pre-commit Hooks

Hooks are defined in `.pre-commit-config.yaml`. Install once:

```bash
uv sync --extra dev
uv run pre-commit install
```

After installation, the boundary checks run automatically on each `git commit` when relevant files are staged:

| Hook | Triggers on |
|------|-------------|
| `extension-boundaries` | Changes to `sandbox/extensions/` |
| `extension-context-boundary` | Changes to `sandbox/extensions/` |
| `max-loc` | Changes to any `.py` file |

Run all hooks manually:

```bash
uv run pre-commit run --all-files
```

---

## Adding a New Boundary Rule

All boundary scripts follow the same conventions:

1. Pure Python using `ast`, `pathlib`, `yaml`
2. No external dependencies beyond `pyproject.toml`
3. Colorized TTY output via `scripts/_boundary_common.py`
4. `--verbose` flag for detailed output
5. Exit code 0 = clean, 1 = violations found
6. Standalone execution: `uv run python scripts/check_*.py`

To add a new check:

1. Create `scripts/check_<name>.py` following the pattern above
2. Add a CI step in `.github/workflows/ci.yml`
3. Add a pre-commit hook in `.pre-commit-config.yaml`
4. Document the rule in this file

---

## Evolving the Public Core API

When a new `core.*` module needs to be accessible to extensions:

1. Verify the module is stable and genuinely part of the extension contract
2. Add the module path to `_ALLOWED_PREFIXES` in `scripts/check_extension_context_boundary.py`
3. Update the allowlist table in this document
4. Run the check to confirm it passes

Avoid exposing internal loader, routing, or persistence implementation details. Prefer extending `ExtensionContext` or adding new protocol methods in `core.extensions.contract`.

---

## References

- [extensions.md](extensions.md) -- Extension architecture and `ExtensionContext` API
- [architecture.md](architecture.md) -- System overview
- [ADR 029](adr/029-refactor-core-extensions-boundaries.md) -- `core.extensions` boundaries refactor
- [ADR 021](adr/021-hard-dependency-contracts.md) -- Hard dependency contracts (`depends_on`)
