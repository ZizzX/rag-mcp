"""Connected projects: folders outside data/docs indexed in place under `<name>/`.

The registry lives next to the index (`<index_dir>/projects.json`) because it holds absolute,
machine-local paths. Git is optional: without it a folder is walked with the usual exclusions.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .loaders import SUPPORTED_SUFFIXES, _hidden
from .store import _atomic_write

REGISTRY = "projects.json"
SKIP_DIRS = {"node_modules", ".venv", "venv", "build", "dist", "target", "vendor", "__pycache__"}
MAX_FILES = 5000  # more than this is usually a mistake (a home folder, a monorepo root); --force overrides
NAME_RE = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9._-]*$")


class ProjectError(ValueError):
    """`add`/`remove` refused; the message is meant for the user."""


class RegistryError(RuntimeError):
    """projects.json cannot be read."""


@dataclass
class Project:
    name: str
    path: Path
    origin: str | None
    git_dir: Path | None
    added: str


def normalize_origin(url: str) -> str:
    """'git@github.com:Acme/shop.git', 'https://u@github.com/Acme/shop' -> 'github.com/acme/shop'."""
    u = url.strip()
    has_scheme = re.match(r"^[a-z][a-z0-9+.-]*://", u, flags=re.I) is not None
    u = re.sub(r"^[a-z][a-z0-9+.-]*://", "", u, flags=re.I)
    u = re.sub(r"^[^@/]+@", "", u)
    if not has_scheme and re.match(r"^[^/:]+:(?!\d+/)", u):  # scp form host:owner/repo
        u = u.replace(":", "/", 1)
    u = re.sub(r"^([^/:]+):\d+/", r"\1/", u)  # port
    return u.rstrip("/").removesuffix(".git").rstrip("/").lower()


def git(cwd: Path, *args: str, timeout: float = 2) -> str | None:
    """Run git in `cwd`; stdout, or None when git is missing, slow or fails. The short default
    timeout suits the quick lookups; listing a big repository passes a longer one."""
    try:
        r = subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    # only the line break: `-z` output may start with a file name that begins with a space
    return r.stdout.rstrip("\r\n") if r.returncode == 0 else None


def repo_root(path: Path) -> Path | None:
    out = git(path, "rev-parse", "--show-toplevel")
    return Path(out).resolve() if out else None


def git_common_dir(path: Path) -> Path | None:
    """The .git directory shared by every worktree of the repository."""
    out = git(path, "rev-parse", "--git-common-dir")
    return (Path(path) / out).resolve() if out else None


def origin_of(path: Path) -> str | None:
    out = git(path, "remote", "get-url", "origin")
    return normalize_origin(out) if out else None


def load_projects(index_dir: Path) -> dict[str, Project]:
    file = index_dir / REGISTRY
    if not file.exists():
        return {}
    try:
        raw = json.loads(file.read_text(encoding="utf-8"))
        return {
            name: Project(name, Path(d["path"]), d.get("origin"), Path(d["git_dir"]) if d.get("git_dir") else None,
                          d.get("added", ""))
            for name, d in raw.items()
        }
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        raise RegistryError(f"Cannot read {file}: {e}. Fix or delete it.") from None


def save_projects(index_dir: Path, projects: dict[str, Project]) -> None:
    index_dir.mkdir(parents=True, exist_ok=True)
    raw = {
        p.name: {"path": str(p.path), "origin": p.origin, "git_dir": str(p.git_dir) if p.git_dir else None,
                 "added": p.added}
        for p in sorted(projects.values(), key=lambda p: p.name)
    }
    payload = json.dumps(raw, ensure_ascii=False, indent=1).encode("utf-8")
    _atomic_write(index_dir / REGISTRY, lambda f: f.write(payload))


def _key(path: Path) -> str:
    return os.path.normcase(os.path.realpath(path))


def _inside(path: Path, parent: Path) -> bool:
    """`path` is `parent` or lies under it, after resolving symlinks and case on case-insensitive systems."""
    p, q = _key(path), _key(parent)
    return p == q or p.startswith(q.rstrip(os.sep) + os.sep)


def list_files(root: Path, skip: Path | None = None) -> tuple[list[Path], str | None]:
    """Documents under `root`: what git tracks or would track (so .gitignore applies), or a plain
    walk without junk folders when `root` is not in a repository. The second value is a warning
    when git is there but could not list the files."""
    warning = None
    out = git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard", timeout=60)
    if out is not None:
        candidates = [root / rel for rel in dict.fromkeys(out.split("\0")) if rel]
    else:
        if repo_root(root) is not None:
            warning = f"git ls-files failed or timed out in {root}; indexed it without .gitignore"
        candidates = []
        for folder, dirs, names in os.walk(root):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
            candidates += [Path(folder) / n for n in names]
    files = [
        p for p in candidates
        if p.suffix.lower() in SUPPORTED_SUFFIXES and not _hidden(p, root) and p.is_file()
        and not (skip is not None and _inside(p, skip))
    ]
    return sorted(files), warning


def resolve_project(
    path: str | Path | None,
    name: str | None,
    *,
    cwd: Path,
    docs_dir: Path,
    existing: dict[str, Project],
    force: bool = False,
) -> tuple[Project, list[Path]]:
    """Check what `rag-mcp add` was asked for and describe the project it would connect.

    No path means the git repository around `cwd` (or `cwd` itself outside git); no name means the
    main repository's folder name, so `add` from a worktree still names the project after the repo.
    Raises ProjectError with a message for the user when the folder cannot be connected.
    """
    if path is None:
        target = repo_root(cwd) or Path(cwd).resolve()
    else:
        target = (Path(cwd) / path).resolve()
    if not target.is_dir():
        raise ProjectError(f"{target} is not a folder")
    if not force and target in (Path.home().resolve(), Path(target.anchor)):
        raise ProjectError(f"{target} is too broad to index (home folder or drive root); pass --force if you mean it")

    common = git_common_dir(target)
    if name is None:
        if common is None:
            name = target.name
        else:
            name = common.parent.name if common.name == ".git" else common.name.removesuffix(".git")
    if not NAME_RE.match(name):
        raise ProjectError(f"Invalid project name {name!r}: use letters, digits, '.', '_' or '-' (set it with --name)")

    if _inside(target, docs_dir):
        raise ProjectError(f"{target} is inside docs_dir ({docs_dir}); its files are indexed from there already")
    if (docs_dir / name).is_dir():
        raise ProjectError(f"The name {name!r} is taken by the folder {docs_dir / name}; choose another with --name")
    same = existing.get(name)
    # a project whose old folder is gone is being added from its new place: that is a move
    if same is not None and _key(same.path) != _key(target) and same.path.is_dir():
        raise ProjectError(f"The name {name!r} is taken by the project at {same.path}; choose another with --name")
    for other in existing.values():
        if other.name == name:
            continue
        if _key(other.path) == _key(target):
            raise ProjectError(f"{target} is already connected as {other.name!r}")
        if _inside(target, other.path) or _inside(other.path, target):
            raise ProjectError(
                f"{target} overlaps the project {other.name!r} at {other.path}; nested projects are not "
                f"supported, run `rag-mcp remove {other.name}` first"
            )

    files, _ = list_files(target, skip=docs_dir)
    if len(files) > MAX_FILES and not force:
        raise ProjectError(f"{len(files)} documents, more than {MAX_FILES}; pass --force to connect anyway")
    project = Project(name, target, origin_of(target) if common else None, common, date.today().isoformat())
    return project, files
