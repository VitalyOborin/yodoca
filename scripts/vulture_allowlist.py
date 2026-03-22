"""Vulture whitelist: symbols used only via dynamic loading (false positives).

Pass this file as an extra path to vulture, or list it under [tool.vulture] paths
in pyproject.toml. Add references when vulture reports dead code that is reached
only through reflection, entrypoints, or Protocol structural matching.

Example when needed::

    from some.module import used_only_dynamically
    used_only_dynamically

"""

# Intentionally minimal — extend when a real false positive appears.
