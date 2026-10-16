"""Connected project folders: registry, file listing, indexing and the CLI (T-014)."""

import os
import subprocess
from pathlib import Path

import pytest

from rag_mcp import projects
from rag_mcp.projects import (
    Project,
    ProjectError,
    RegistryError,
    git_common_dir,
    list_files,
    load_projects,
    normalize_origin,
    origin_of,
    repo_root,
    resolve_project,
    save_projects,
)


@pytest.mark.parametrize("url", [
    "git@github.com:Acme/shop.git",
    "https://user@github.com/Acme/shop",
    "https://github.com/acme/shop.git/",
    "ssh://git@github.com:22/Acme/shop.git",
])
def test_normalize_origin(url):
    assert normalize_origin(url) == "github.com/acme/shop"


def test_normalize_origin_keeps_nested_groups():
    assert normalize_origin("git@gitlab.mycar.kz:myhr/frontend/apps/ats.git") == "gitlab.mycar.kz/myhr/frontend/apps/ats"


def test_registry_round_trip(tmp_path):
    p = Project("shop", tmp_path / "shop", "github.com/acme/shop", tmp_path / "shop/.git", "2026-10-16")
    save_projects(tmp_path, {"shop": p})
    assert load_projects(tmp_path) == {"shop": p}
    assert load_projects(tmp_path / "nothing-here") == {}


def test_corrupt_registry_raises_with_path(tmp_path):
    (tmp_path / "projects.json").write_text("{nope", encoding="utf-8")
    with pytest.raises(RegistryError, match="projects.json"):
        load_projects(tmp_path)


def test_git_helpers(make_repo, tmp_path):
    repo = make_repo(tmp_path / "shop", {"README.md": "# Shop"}, origin="git@github.com:Acme/shop.git")
    (repo / "docs").mkdir()
    assert repo_root(repo / "docs") == repo.resolve()
    assert git_common_dir(repo / "docs") == (repo / ".git").resolve()
    assert origin_of(repo) == "github.com/acme/shop"
    assert repo_root(tmp_path) is None and origin_of(tmp_path) is None


def rel(files, root):
    return sorted(p.relative_to(root).as_posix() for p in files)


def test_list_files_uses_git(make_repo, tmp_path):
    repo = make_repo(tmp_path / "r", {".gitignore": "out/\n", "README.md": "x", "docs/a.md": "x",
                                      "src/app.py": "x", ".github/x.md": "x", "out/gen.md": "x"})
    (repo / "docs/new.md").write_text("untracked", encoding="utf-8")
    (repo / "docs/Спец ификация.md").write_text("x", encoding="utf-8")
    (repo / "README.md").unlink()  # deleted, still in the git index
    files, warning = list_files(repo)
    assert rel(files, repo) == ["docs/a.md", "docs/new.md", "docs/Спец ификация.md"] and warning is None


def test_list_files_subfolder_only(make_repo, tmp_path):
    repo = make_repo(tmp_path / "r", {"README.md": "x", "docs/a.md": "x"})
    assert rel(list_files(repo / "docs")[0], repo / "docs") == ["a.md"]


def test_list_files_without_git_skips_junk(tmp_path):
    for f in ["a.md", "node_modules/p/readme.md", "build/x.md", ".cache/y.md", "sub/b.pdf", "c.py"]:
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_text("x")
    assert rel(list_files(tmp_path)[0], tmp_path) == ["a.md", "sub/b.pdf"]


def test_list_files_falls_back_with_warning(make_repo, tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "r", {"a.md": "x", "node_modules/m.md": "x"})
    real = projects.git
    monkeypatch.setattr(projects, "git", lambda cwd, *a: None if a[0] == "ls-files" else real(cwd, *a))  # "dubious ownership"
    files, warning = list_files(repo)
    assert rel(files, repo) == ["a.md"] and "git" in warning


def test_list_files_skips_subtree(tmp_path):
    for f in ["a.md", "data/docs/b.md"]:
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_text("x")
    assert rel(list_files(tmp_path, skip=tmp_path / "data/docs")[0], tmp_path) == ["a.md"]


def test_default_path_is_repo_root_and_name_is_main_repo(make_repo, tmp_path):
    repo = make_repo(tmp_path / "shop", {"docs/a.md": "x"}, origin="git@github.com:Acme/shop.git")
    wt = tmp_path / "ATS-3010"
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", str(wt)], check=True, capture_output=True)
    p, files = resolve_project(None, None, cwd=wt / "docs", docs_dir=tmp_path / "d", existing={})
    assert p.name == "shop" and p.path == wt.resolve() and p.origin == "github.com/acme/shop"
    assert p.git_dir == (repo / ".git").resolve() and len(files) == 1


def test_explicit_subfolder_and_name(make_repo, tmp_path):
    repo = make_repo(tmp_path / "shop", {"docs/a.md": "x", "README.md": "x"})
    p, files = resolve_project("docs", "shop-docs", cwd=repo, docs_dir=tmp_path / "d", existing={})
    assert p.path == (repo / "docs").resolve() and p.name == "shop-docs" and len(files) == 1


def test_non_git_folder(tmp_path):
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes/a.md").write_text("x")
    p, _ = resolve_project(tmp_path / "notes", None, cwd=tmp_path, docs_dir=tmp_path / "d", existing={})
    assert (p.name, p.origin, p.git_dir) == ("notes", None, None)


@pytest.mark.parametrize("bad", [".hidden", "a/b", "x y"])
def test_invalid_name(tmp_path, bad):
    with pytest.raises(ProjectError, match="name"):
        resolve_project(tmp_path, bad, cwd=tmp_path, docs_dir=tmp_path / "d", existing={})


@pytest.mark.parametrize("case, match", [
    ("name_taken_by_folder", "taken"),
    ("name_taken_by_project", "taken"),
    ("inside_project", "nested"),
    ("contains_project", "nested"),
    ("same_path_via_symlink", "already connected"),
    ("inside_docs_dir", "docs_dir"),
    ("home", "too broad"),
    ("missing", "not a folder"),
])
def test_refusals(tmp_path, case, match):
    docs_dir = tmp_path / "docs"
    (docs_dir / "shop").mkdir(parents=True)
    for d in ("x", "other/sub"):
        (tmp_path / d).mkdir(parents=True)
    existing = {"other": Project("other", (tmp_path / "other").resolve(), None, None, "2026-10-16")}
    if case == "same_path_via_symlink":
        try:
            os.symlink(tmp_path / "other", tmp_path / "link", target_is_directory=True)
        except OSError:
            pytest.skip("cannot create symlinks here")
    path, name = {
        "name_taken_by_folder": (tmp_path / "x", "shop"),
        "name_taken_by_project": (tmp_path / "x", "other"),
        "inside_project": (tmp_path / "other/sub", "y"),
        "contains_project": (tmp_path, "y"),
        "same_path_via_symlink": (tmp_path / "link", "y"),
        "inside_docs_dir": (docs_dir / "shop", "y"),
        "home": (Path.home(), "y"),
        "missing": (tmp_path / "absent", "y"),
    }[case]
    with pytest.raises(ProjectError, match=match):
        resolve_project(path, name, cwd=tmp_path, docs_dir=docs_dir, existing=existing)


def test_file_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(projects, "MAX_FILES", 2)
    for n in range(3):
        (tmp_path / f"{n}.md").write_text("x")
    with pytest.raises(ProjectError, match="3 documents"):
        resolve_project(tmp_path, "x", cwd=tmp_path, docs_dir=tmp_path / "d", existing={})
    _, files = resolve_project(tmp_path, "x", cwd=tmp_path, docs_dir=tmp_path / "d", existing={}, force=True)
    assert len(files) == 3


def test_same_path_same_name_is_an_update(tmp_path):
    (tmp_path / "x").mkdir()
    old = Project("x", (tmp_path / "x").resolve(), None, None, "2020-01-01")
    p, _ = resolve_project(tmp_path / "x", "x", cwd=tmp_path, docs_dir=tmp_path / "d", existing={"x": old})
    assert p.name == "x" and p.path == old.path
