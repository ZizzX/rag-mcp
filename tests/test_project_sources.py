"""Connected project folders: registry, file listing, indexing and the CLI (T-014)."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from rag_mcp import cli, projects
from rag_mcp.config import Settings
from rag_mcp.pipeline import Pipeline
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
from rag_mcp.store import IndexBusy


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
    files, warning, complete = list_files(repo)
    assert rel(files, repo) == ["docs/a.md", "docs/new.md", "docs/Спец ификация.md"]
    assert warning is None and complete


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
    monkeypatch.setattr(projects, "git", lambda cwd, *a, **kw: None if a[0] == "ls-files" else real(cwd, *a, **kw))
    files, warning, complete = list_files(repo)
    assert rel(files, repo) == ["a.md"] and "git" in warning and complete


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


def test_connected_project_is_indexed_in_place(pipeline, make_repo, tmp_path):
    pipeline.ingest()  # examples/docs first, so the counts below are ours
    repo = make_repo(tmp_path / "shop", {"docs/refunds.md": "# Refunds\n\nRefunds go through Stripe."})
    pipeline.add_project(repo)
    r = pipeline.ingest()
    assert r.added == 1 and "shop" in pipeline.store.projects()
    hit = pipeline.search("refunds stripe", top_k=1, source_prefixes=["shop/"])[0]
    assert hit["source"] == "shop/docs/refunds.md" and pipeline.needs_ingest() is False
    (repo / "docs/refunds.md").write_text("# Refunds\n\nRefunds go through Adyen.", encoding="utf-8")
    assert pipeline.needs_ingest() is True and pipeline.ingest().updated == 1


def test_remove_drops_chunks_and_rename_reindexes(pipeline, make_repo, tmp_path):
    repo = make_repo(tmp_path / "shop", {"a.md": "# A\n\nalpha"})
    pipeline.add_project(repo)
    pipeline.ingest()
    pipeline.remove_project("shop")
    pipeline.add_project(repo, "shop2")
    pipeline.ingest()
    assert "shop" not in pipeline.store.projects() and "shop2" in pipeline.store.projects()


def test_missing_path_keeps_chunks(pipeline, make_repo, tmp_path):
    repo = make_repo(tmp_path / "shop", {"a.md": "# A\n\nalpha"})
    pipeline.add_project(repo)
    pipeline.ingest()
    repo.rename(repo.with_name("shop-gone"))  # rmtree fails on read-only .git objects on Windows
    assert pipeline.needs_ingest() is False
    r = pipeline.ingest()
    assert r.removed == 0 and any("shop" in e and "not found" in e for e in r.errors)
    assert "shop" in pipeline.store.projects()


def test_corrupt_registry_blocks_ingest(pipeline, make_repo, tmp_path):
    repo = make_repo(tmp_path / "shop", {"a.md": "x"})
    pipeline.add_project(repo)
    pipeline.ingest()
    (pipeline.settings.index_dir / "projects.json").write_text("{", encoding="utf-8")
    with pytest.raises(RegistryError):
        pipeline.ingest()
    with pytest.raises(RegistryError):
        pipeline.ingest(rebuild=True)  # must refuse before the rebuild empties the index
    assert "shop" in pipeline.store.projects()


def test_add_while_index_is_busy(pipeline, make_repo, tmp_path):
    repo = make_repo(tmp_path / "shop", {"a.md": "x"})
    with pipeline._open_store().write_lock():
        with pytest.raises(IndexBusy):
            pipeline.add_project(repo)
    assert load_projects(pipeline.settings.index_dir) == {}


def test_only_projects_without_docs_dir(tmp_path, make_repo):
    pipe = Pipeline(Settings(docs_dir=tmp_path / "absent", index_dir=tmp_path / "idx", embed_provider="hash",
                             _env_file=None))
    repo = make_repo(tmp_path / "shop", {"a.md": "# A\n\nalpha"})
    pipe.add_project(repo)
    assert pipe.needs_ingest() is True and pipe.ingest().added == 1 and pipe.needs_ingest() is False


def test_projects_info(pipeline, make_repo, tmp_path):
    (pipeline.settings.docs_dir / "team").mkdir()
    (pipeline.settings.docs_dir / "team/x.md").write_text("# X\n\nx")
    repo = make_repo(tmp_path / "shop", {"a.md": "# A\n\nalpha"})
    pipeline.add_project(repo)
    pipeline.ingest()
    rows = {r["name"]: r for r in pipeline.projects_info()}
    assert rows["shop"] | {"chunks": 0} == {"name": "shop", "kind": "connected", "path": str(repo.resolve()),
                                         "exists": True, "documents": 1, "chunks": 0}
    assert rows["shop"]["chunks"] > 0
    assert rows["team"]["kind"] == "folder" and rows["team"]["documents"] == 1


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    (tmp_path / "docs").mkdir()
    monkeypatch.setenv("RAG_DOCS_DIR", str(tmp_path / "docs"))
    monkeypatch.setenv("RAG_INDEX_DIR", str(tmp_path / "index"))
    monkeypatch.setenv("RAG_EMBED_PROVIDER", "hash")
    monkeypatch.setenv("RAG_QDRANT_URL", "")


def test_cli_add_from_subfolder_indexes_repo(cli_env, make_repo, tmp_path, monkeypatch, capsys):
    repo = make_repo(tmp_path / "shop", {"docs/a.md": "# A\n\nalpha"}, origin="git@github.com:Acme/shop.git")
    monkeypatch.chdir(repo / "docs")
    assert cli.main(["add"]) == 0
    out = capsys.readouterr().out
    assert "shop" in out and str(repo.resolve()) in out and "github.com/acme/shop" in out and "added=1" in out


def test_cli_add_refusal_exits_2(cli_env, capsys):
    assert cli.main(["add", str(Path.home())]) == 2 and "too broad" in capsys.readouterr().err


def test_cli_projects_and_remove(cli_env, make_repo, tmp_path, capsys):
    repo = make_repo(tmp_path / "shop", {"a.md": "# A\n\nalpha"})
    assert cli.main(["add", str(repo)]) == 0 and cli.main(["projects"]) == 0
    line = next(ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("shop"))
    assert "connected" in line and " 1 " in line
    assert cli.main(["remove", "shop"]) == 0 and cli.main(["projects"]) == 0
    assert not any(ln.startswith("shop") for ln in capsys.readouterr().out.splitlines())
    assert cli.main(["remove", "ghost"]) == 2 and "no project" in capsys.readouterr().err


# ---------- final review fixes ----------

@pytest.mark.skipif(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0), reason="needs POSIX permissions")
def test_unreadable_project_folder_keeps_chunks(pipeline, make_repo, tmp_path):
    repo = make_repo(tmp_path / "shop", {"a.md": "# A\n\nalpha"})
    pipeline.add_project(repo)
    pipeline.ingest()
    repo.chmod(0)  # e.g. macOS privacy rules: the GUI-started server may not read ~/Documents
    try:
        assert pipeline.needs_ingest() is False
        r = pipeline.ingest()
    finally:
        repo.chmod(0o755)
    assert r.removed == 0 and any("shop" in e and "not readable" in e for e in r.errors)
    assert "shop" in pipeline.store.projects()


def test_remove_without_docs_dir_drops_chunks(tmp_path, make_repo):
    pipe = Pipeline(Settings(docs_dir=tmp_path / "absent", index_dir=tmp_path / "idx", embed_provider="hash",
                             _env_file=None))
    repo = make_repo(tmp_path / "shop", {"a.md": "# A\n\nalpha"})
    pipe.add_project(repo)
    assert pipe.ingest().errors == []
    pipe.remove_project("shop")
    pipe.add_project(repo, "shop2")
    r = pipe.ingest()
    assert (r.added, r.removed) == (1, 1) and pipe.store.projects() == ["shop2"]
    pipe.remove_project("shop2")
    r = pipe.ingest()
    assert r.removed == 1 and pipe.store.projects() == [] and r.errors == []


def test_rebuild_refuses_to_drop_unreachable_sources(pipeline, make_repo, tmp_path):
    repo = make_repo(tmp_path / "shop", {"a.md": "# A\n\nalpha"})
    pipeline.add_project(repo)
    pipeline.ingest()
    repo.rename(repo.with_name("shop-gone"))  # rmtree fails on read-only .git objects on Windows
    with pytest.raises(ProjectError, match="shop"):
        pipeline.ingest(rebuild=True)
    assert "shop" in pipeline.store.projects()


def test_add_from_new_place_after_move_keeps_index(pipeline, make_repo, tmp_path):
    repo = make_repo(tmp_path / "shop", {"a.md": "# A\n\nalpha", "b.md": "# B\n\nbeta"})
    pipeline.add_project(repo)
    pipeline.ingest()
    moved = tmp_path / "work" / "shop"
    moved.parent.mkdir()
    shutil.move(str(repo), str(moved))
    project, _ = pipeline.add_project(moved)
    assert project.name == "shop" and project.path == moved.resolve()
    r = pipeline.ingest()
    assert (r.added, r.updated, r.removed) == (0, 0, 0) and r.errors == []


def test_ls_files_gets_a_long_timeout(make_repo, tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "r", {"a.md": "x"})
    seen = {}
    real_run = subprocess.run

    def run(cmd, *a, **kw):
        seen[cmd[3]] = kw.get("timeout")
        return real_run(cmd, *a, **kw)

    monkeypatch.setattr(projects.subprocess, "run", run)
    list_files(repo)
    assert seen["ls-files"] >= 60  # a big repo on a cold cache takes more than the 2 s meant for detection


def test_docs_dir_folder_shadowed_by_connected_project(pipeline, make_repo, tmp_path):
    repo = make_repo(tmp_path / "shop", {"a.md": "# A\n\nfrom the repository"})
    pipeline.add_project(repo)
    pipeline.ingest()
    (pipeline.settings.docs_dir / "shop").mkdir()
    (pipeline.settings.docs_dir / "shop/a.md").write_text("# A\n\nold copy in data/docs", encoding="utf-8")
    r = pipeline.ingest()
    assert (r.added, r.updated) == (0, 0) and any("shadowed" in e for e in r.errors)
    assert pipeline.needs_ingest() is False
    assert "from the repository" in pipeline.document(pipeline.search("repository", top_k=1)[0]["doc_id"])["text"]


def test_odd_file_names_from_git(make_repo, tmp_path):
    repo = make_repo(tmp_path / "r", {" notes.md": "x", "a.md": "x"})
    if os.name != "nt":  # a path that is not UTF-8 in the git index must not break the listing
        blob = subprocess.run(["git", "-C", str(repo), "hash-object", "-w", "a.md"], check=True,
                              capture_output=True, text=True).stdout.strip()
        subprocess.run([b"git", b"-C", bytes(repo), b"update-index", b"--add", b"--cacheinfo",
                        b"100644," + blob.encode() + b",bad\xff.md"], check=True, capture_output=True)
    assert rel(list_files(repo)[0], repo) == [" notes.md", "a.md"]


# ---------- PR #6 review (Codex) ----------

posix_permissions = pytest.mark.skipif(
    os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0), reason="needs POSIX permissions"
)


@posix_permissions
@pytest.mark.parametrize("with_git", [True, False])
def test_unreadable_subfolder_keeps_its_chunks(pipeline, make_repo, tmp_path, with_git):
    files = {"README.md": "# Readme\n\nroot", "docs/a.md": "# A\n\nalpha"}
    if with_git:
        root = make_repo(tmp_path / "shop", files)
    else:
        root = tmp_path / "shop"
        for name, text in files.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(text, encoding="utf-8")
    pipeline.add_project(root)
    pipeline.ingest()
    (root / "docs").chmod(0)
    try:
        listed, warning, complete = list_files(root)
        assert rel(listed, root) == ["README.md"] and not complete and "could not be read" in warning
        assert pipeline.needs_ingest() is False
        r = pipeline.ingest()
    finally:
        (root / "docs").chmod(0o755)
    assert r.removed == 0 and any("could not be read" in e for e in r.errors)
    assert any(src == "shop/docs/a.md" for src in pipeline.store.sources())


def test_document_back_in_docs_dir_loses_project_tag(pipeline, make_repo, tmp_path):
    repo = make_repo(tmp_path / "shop", {"a.md": "# A\n\nsame text"})
    pipeline.add_project(repo)
    pipeline.ingest()
    docs = pipeline.settings.docs_dir
    (docs / "shop").mkdir()
    (docs / "shop/a.md").write_text("# A\n\nsame text", encoding="utf-8")  # identical copy, shadowed for now
    pipeline.remove_project("shop")
    pipeline.ingest()  # shop/a.md now belongs to docs_dir, with the same source and content hash
    docs.rename(docs.with_name("docs-unmounted"))
    r = pipeline.ingest()
    assert r.removed == 0 and "shop" in pipeline.store.projects()
