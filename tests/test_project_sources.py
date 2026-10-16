"""Connected project folders: registry, file listing, indexing and the CLI (T-014)."""

import pytest

from rag_mcp.projects import (
    Project,
    RegistryError,
    git_common_dir,
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
