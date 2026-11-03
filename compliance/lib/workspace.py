"""Where a command reads and writes: the workspace, and how a vendored core stays read-only.

A system repository is expected to *vendor* this core (git submodule or a copied tree) and run
its commands from the repository that holds the system content. Output therefore belongs in the
**workspace**, never in the core's own tree.

Resolution order for the workspace: an explicit ``--workspace`` value, then the
``OSCAL_VAULT_WORKSPACE`` environment variable, then the current directory.

A standalone checkout of the core (a clone someone is experimenting in) may still write under
its own tree, with a one-line notice. A vendored core refuses unless the caller opts in with
``--allow-write-in-core`` or ``OSCAL_VAULT_ALLOW_WRITE_IN_CORE=1``. See ADR-028.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ENV_WORKSPACE = "OSCAL_VAULT_WORKSPACE"
ENV_ALLOW_IN_CORE = "OSCAL_VAULT_ALLOW_WRITE_IN_CORE"

STANDALONE = "standalone"
SUBMODULE = "submodule"
NESTED = "nested"

_noticed = False


class WorkspaceError(RuntimeError):
    """A command was about to write somewhere a vendored core must not be written to."""


def core_root() -> Path:
    """Root of the core tree this module belongs to."""
    return Path(__file__).resolve().parents[2]


def _gitdir_of(dot_git: Path) -> str:
    try:
        text = dot_git.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    return text.split(":", 1)[1].strip() if text.startswith("gitdir:") else ""


def vendoring_state(root: Path | None = None) -> str:
    """``standalone``, ``submodule`` or ``nested``.

    * ``submodule``: ``<root>/.git`` is a file pointing into a ``modules`` directory.
    * ``nested``: no git metadata of its own, but an ancestor is a git working tree
      (a copied tree vendored into another repository).
    * ``standalone``: a clone (``.git`` directory), a linked worktree of the core, or an
      unpacked tarball with no enclosing repository.
    """
    root = (root or core_root()).resolve()
    dot_git = root / ".git"
    if dot_git.is_dir():
        return STANDALONE
    if dot_git.is_file():
        parts = Path(_gitdir_of(dot_git)).parts
        return SUBMODULE if "modules" in parts else STANDALONE
    for parent in root.parents:
        if (parent / ".git").exists():
            return NESTED
    return STANDALONE


def is_vendored(root: Path | None = None) -> bool:
    return vendoring_state(root) in (SUBMODULE, NESTED)


def resolve_workspace(explicit: str | os.PathLike[str] | None = None) -> Path:
    """The directory holding ``examples/vault`` and ``scripts/``: explicit, env, then cwd."""
    chosen = explicit or os.environ.get(ENV_WORKSPACE) or None
    return Path(chosen).expanduser().resolve() if chosen else Path.cwd().resolve()


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _allowed_by_env() -> bool:
    return os.environ.get(ENV_ALLOW_IN_CORE, "").strip().lower() in {"1", "true", "yes"}


def guard_output(
    path: str | os.PathLike[str],
    *,
    allow_in_core: bool = False,
    root: Path | None = None,
) -> Path:
    """Resolve ``path`` and refuse it if it is inside a vendored core's own tree.

    Returns the resolved path. A standalone core is allowed, with a single notice per process,
    so quick local experiments keep working.
    """
    global _noticed
    target = Path(path).expanduser().resolve()
    core = (root or core_root()).resolve()
    if not _inside(target, core):
        return target
    state = vendoring_state(core)
    if state != STANDALONE:
        if allow_in_core or _allowed_by_env():
            return target
        raise WorkspaceError(
            f"refusing to write inside the vendored core ({state}): {target}\n"
            f"  Write to the system repository instead: pass --workspace/--output-dir, or set "
            f"{ENV_WORKSPACE}.\n"
            f"  To override deliberately, pass --allow-write-in-core or set {ENV_ALLOW_IN_CORE}=1."
        )
    if not _noticed:
        _noticed = True
        print(
            f"note: writing under the core checkout ({core}). Generated and source-derived "
            "output must not be committed to the core; see CONTRIBUTING.md.",
            file=sys.stderr,
        )
    return target


def add_workspace_arguments(parser) -> None:  # noqa: ANN001 - argparse parser
    """The two shared flags: ``--workspace`` and ``--allow-write-in-core``."""
    parser.add_argument(
        "--workspace",
        default=None,
        help=f"System repository root (default: ${ENV_WORKSPACE}, then the current directory).",
    )
    parser.add_argument(
        "--allow-write-in-core",
        action="store_true",
        help="Permit writing inside a vendored core tree (not recommended).",
    )
