"""Which project a workspace folder belongs to: path, origin, shared .git, folder name."""

import subprocess
from pathlib import Path

import pytest

from rag_mcp import projects
from rag_mcp.projects import Project, git_common_dir, match_registry, origin_of

ORIGIN = "git@github.com:Acme/shop.git"


def reg(**repos: Path) -> dict[str, Project]:
    return {n: Project(n, p.resolve(), origin_of(p), git_common_dir(p), "") for n, p in repos.items()}


def worktree(repo: Path, where: Path) -> Path:
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", str(where), "-b", where.name],
                   check=True, capture_output=True)
    return where


# ---------- match_registry ----------

def test_path_match(make_repo, tmp_path):
    repo = make_repo(tmp_path / "shop", {"src/a.md": "a"}, origin=ORIGIN)
    p, by, note = match_registry(repo / "src", reg(shop=repo))
    assert (p.name, by, note) == ("shop", "path", None)


def test_worktree_elsewhere_matches_by_git_dir(make_repo, tmp_path):
    repo = make_repo(tmp_path / "shop", {"a.md": "a"})  # no origin: only the shared .git links them
    p, by, _ = match_registry(worktree(repo, tmp_path / "orca" / "ATS-3010"), reg(shop=repo))
    assert (p.name, by) == ("shop", "git_dir")


def test_second_clone_matches_by_origin(make_repo, tmp_path):
    repo = make_repo(tmp_path / "shop", {"a.md": "a"}, origin=ORIGIN)
    clone = make_repo(tmp_path / "tmp" / "shop-copy", {"a.md": "a"}, origin="https://github.com/acme/shop")
    p, by, note = match_registry(clone, reg(shop=repo))
    assert (p.name, by, note) == ("shop", "origin", None)


def test_same_origin_twice_prefers_own_git_dir(make_repo, tmp_path):
    a = make_repo(tmp_path / "a" / "shop", {"a.md": "a"}, origin=ORIGIN)
    b = make_repo(tmp_path / "b" / "shop", {"a.md": "a"}, origin=ORIGIN)
    connected = reg(shop=a, shop2=b)
    p, by, note = match_registry(worktree(b, tmp_path / "wt"), connected)
    assert (p.name, by, note) == ("shop2", "origin", None)
    other = make_repo(tmp_path / "c" / "shop", {"a.md": "a"}, origin=ORIGIN)
    p, by, note = match_registry(other, connected)
    assert (p.name, by) == ("shop", "origin") and "shop2" in note


def test_connected_subfolder_found_from_repo(make_repo, tmp_path):
    repo = make_repo(tmp_path / "shop", {"docs/a.md": "a", "src/x.md": "x"})
    connected = {"shop": Project("shop", (repo / "docs").resolve(), None, git_common_dir(repo / "docs"), "")}
    p, by, _ = match_registry(repo / "src", connected)
    assert (p.name, by) == ("shop", "git_dir")


def test_no_match(make_repo, tmp_path):
    repo = make_repo(tmp_path / "shop", {"a.md": "a"}, origin=ORIGIN)
    other = make_repo(tmp_path / "other", {"a.md": "a"}, origin="git@github.com:Acme/other.git")
    assert match_registry(other, reg(shop=repo)) is None
    assert match_registry(tmp_path / "missing", reg(shop=repo)) is None
    assert match_registry(tmp_path, {}) is None


@pytest.mark.parametrize("failure", [FileNotFoundError("git"), subprocess.TimeoutExpired("git", 2)])
def test_without_git_only_path_matches(make_repo, tmp_path, monkeypatch, failure):
    repo = make_repo(tmp_path / "shop", {"src/a.md": "a"})
    wt = worktree(repo, tmp_path / "wt")
    connected = reg(shop=repo)

    def broken(*a, **kw):
        raise failure

    monkeypatch.setattr(projects.subprocess, "run", broken)
    assert match_registry(wt, connected) is None
    assert match_registry(repo / "src", connected)[1] == "path"
