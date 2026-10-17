"""Which project a workspace folder belongs to: path, origin, shared .git, folder name."""

import json
import subprocess
from pathlib import Path

import pytest

from rag_mcp import cli, projects
from rag_mcp.config import PROJECT_ROOT
from rag_mcp.pipeline import Detection
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


# ---------- Pipeline.detect_project ----------

def add_team_folder(pipeline):
    (pipeline.settings.docs_dir / "team").mkdir()
    (pipeline.settings.docs_dir / "team" / "t.md").write_text("# T\n\nteam", encoding="utf-8")


def test_detect_order_and_docs_from(pipeline, make_repo, tmp_path):
    add_team_folder(pipeline)
    repo = make_repo(tmp_path / "team" / "shop-repo", {"src/a.md": "a"})  # parent named like a docs folder
    pipeline.add_project(repo, "shop")
    pipeline.ingest()
    src = str(repo / "src")
    assert pipeline.detect_project([src]) == Detection("shop", "path")
    wt = worktree(repo, tmp_path / "orca" / "ATS-3010")
    assert pipeline.detect_project([str(wt)]) == Detection("shop", "git_dir", docs_from=str(repo.resolve()))
    assert pipeline.detect_project([str(tmp_path / "code" / "team" / "x")]) == Detection("team", "name")
    assert pipeline.detect_project([str(tmp_path / "code" / "team"), src]) == Detection("shop", "path")
    assert pipeline.detect_project([], explicit="shop") == Detection("shop", "explicit", str(repo.resolve()))
    pipeline.settings.project = "team"
    assert pipeline.detect_project([src]) == Detection("team", "env")


def test_no_match_gives_hint(pipeline, tmp_path):
    missing = str(tmp_path / "missing")
    d = pipeline.detect_project([missing])
    assert (d.project, d.matched_by) == (None, None)
    assert missing in d.hint and "rag-mcp add" in d.hint
    assert pipeline.detect_project([]) == Detection()


def test_cache_reruns_git_only_after_registry_changes(pipeline, make_repo, tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "shop", {"a.md": "a"}, origin=ORIGIN)
    other = make_repo(tmp_path / "other", {"a.md": "a"}, origin="git@github.com:Acme/other.git")
    pipeline.add_project(repo)
    timeouts = []
    real_run = subprocess.run

    def run(cmd, *a, **kw):
        timeouts.append(kw.get("timeout"))
        return real_run(cmd, *a, **kw)

    monkeypatch.setattr(projects.subprocess, "run", run)
    assert pipeline.detect_project([str(other)]).project is None
    assert timeouts and set(timeouts) == {2}
    n = len(timeouts)
    assert pipeline.detect_project([str(other)]).project is None
    assert len(timeouts) == n  # cached: git is not run again for the same folder
    pipeline.add_project(other)  # the registry changed
    assert pipeline.detect_project([str(other)]) == Detection("other", "path")


def test_corrupt_registry_falls_back_to_name(pipeline, tmp_path):
    add_team_folder(pipeline)
    pipeline.ingest()
    (pipeline.settings.index_dir / "projects.json").write_text("{broken", encoding="utf-8")
    assert pipeline.detect_project([str(tmp_path / "code" / "team")]) == Detection("team", "name")


# ---------- final review fixes ----------

def test_name_rule_ignores_connected_projects(pipeline, make_repo, tmp_path):
    repo = make_repo(tmp_path / "acme" / "backend", {"a.md": "a"}, origin="git@github.com:acme/backend.git")
    pipeline.add_project(repo)
    pipeline.ingest()
    unrelated = make_repo(tmp_path / "beta" / "backend", {"a.md": "a"}, origin="git@github.com:beta/backend.git")
    d = pipeline.detect_project([str(unrelated)])
    assert d.project is None and "rag-mcp add" in d.hint


def test_no_hint_where_add_would_refuse_or_index_rag_mcp_itself(pipeline, tmp_path):
    for folder in (PROJECT_ROOT, PROJECT_ROOT / "src", Path.home(), Path(tmp_path.anchor), pipeline.settings.docs_dir):
        assert pipeline.detect_project([str(folder)]) == Detection(), folder


# ---------- CLI ----------

def test_cli_search_scopes_to_cwd_project(cli_env, make_repo, tmp_path, monkeypatch, capsys):
    (tmp_path / "docs" / "team").mkdir()
    (tmp_path / "docs" / "team" / "t.md").write_text("# T\n\nalpha in team", encoding="utf-8")
    repo = make_repo(tmp_path / "shop", {"src/a.md": "# A\n\nalpha in shop"})
    assert cli.main(["add", str(repo)]) == 0
    monkeypatch.chdir(repo / "src")
    capsys.readouterr()
    assert cli.main(["search", "alpha", "--json"]) == 0
    assert {r["source"] for r in json.loads(capsys.readouterr().out)} == {"shop/src/a.md"}
    assert cli.main(["search", "alpha", "--all", "--json"]) == 0
    assert {r["source"] for r in json.loads(capsys.readouterr().out)} == {"shop/src/a.md", "team/t.md"}
    assert cli.main(["search", "alpha"]) == 0
    assert capsys.readouterr().out.splitlines()[0] == "scope: shop/ (matched by path)"
    monkeypatch.chdir(tmp_path)
    assert cli.main(["search", "alpha"]) == 0
    first = capsys.readouterr().out.splitlines()[0]
    assert first.startswith("scope: all projects") and "rag-mcp add" in first
    with pytest.raises(SystemExit):
        cli.main(["search", "alpha", "--all", "--project", "shop"])


def test_cli_projects_marks_cwd_project(cli_env, make_repo, tmp_path, monkeypatch, capsys):
    (tmp_path / "docs" / "team").mkdir()
    (tmp_path / "docs" / "team" / "t.md").write_text("# T\n\nteam", encoding="utf-8")
    repo = make_repo(tmp_path / "shop", {"docs/a.md": "# A\n\nalpha"})
    assert cli.main(["add", str(repo)]) == 0
    monkeypatch.chdir(repo / "docs")
    capsys.readouterr()
    assert cli.main(["projects"]) == 0
    lines = {ln.split()[0]: ln for ln in capsys.readouterr().out.splitlines()}
    assert lines["shop"].endswith("  <- here") and not lines["team"].endswith("<- here")
