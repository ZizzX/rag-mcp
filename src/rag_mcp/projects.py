"""Connected projects: folders outside data/docs indexed in place under `<name>/`.

The registry lives next to the index (`<index_dir>/projects.json`) because it holds absolute,
machine-local paths. Git is optional: without it a folder is walked with the usual exclusions.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .store import _atomic_write

REGISTRY = "projects.json"


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


def git(cwd: Path, *args: str) -> str | None:
    """Run git in `cwd`; stdout, or None when git is missing, slow or fails."""
    try:
        r = subprocess.run(
            ["git", "-C", str(cwd), *args], capture_output=True, text=True, encoding="utf-8", timeout=2
        )
    except (FileNotFoundError, NotADirectoryError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


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
