"""Connected project folders: registry, file listing, indexing and the CLI (T-014)."""

import pytest

from rag_mcp import projects
from rag_mcp.projects import (
    Project,
    RegistryError,
    git_common_dir,
    list_files,
    load_projects,
    normalize_origin,
    origin_of,
    repo_root,
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
