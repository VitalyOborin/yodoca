"""Shared sandbox path resolution. All agent file/shell operations are confined to the sandbox."""

from pathlib import Path

SANDBOX_DIR: Path = (
    Path(__file__).resolve().parent.parent.parent / "sandbox"
).resolve()

ACCESS_DENIED_MSG = "Access denied: operations outside sandbox are prohibited."


def resolve_sandbox_path(
    path: str,
    root: Path | None = None,
    ensure_parent: bool = False,
) -> Path:
    """Resolve path relative to root (defaults to SANDBOX_DIR).
    Rejects paths outside root. Strips redundant sandbox/ prefix."""
    sandbox = (root or SANDBOX_DIR).resolve()
    candidate = Path(path)
    target = candidate if candidate.is_absolute() else (sandbox / candidate)
    target = target.resolve()
    try:
        target.relative_to(sandbox)
    except ValueError:
        raise RuntimeError(f"{ACCESS_DENIED_MSG} Path: {path}") from None
    redundant = sandbox / sandbox.name
    if not candidate.is_absolute() and target != sandbox:
        try:
            rel = target.relative_to(redundant)
            corrected = (sandbox / rel).resolve()
            corrected.relative_to(sandbox)
            target = corrected
        except ValueError:
            pass
    if ensure_parent:
        target.parent.mkdir(parents=True, exist_ok=True)
    return target
