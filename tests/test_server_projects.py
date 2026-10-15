"""The MCP `search` tool scopes to the project detected from the client's workspace roots."""

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import FileUrl
from mcp.types import ListRootsResult, Root

from rag_mcp import server
from rag_mcp.config import PROJECT_ROOT, Settings
from rag_mcp.pipeline import Pipeline

EXAMPLES = PROJECT_ROOT / "examples" / "docs"


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch):
    docs = tmp_path / "docs"
    (docs / "shop-backend").mkdir(parents=True)
    (docs / "mobile-app").mkdir()
    (docs / "common").mkdir()
    (docs / "shop-backend" / "payments.md").write_text("# Payments\n\nRefunds go through Stripe.", encoding="utf-8")
    (docs / "mobile-app" / "release.md").write_text("# Release\n\nRefunds are handled by Apple.", encoding="utf-8")
    shutil.copy(EXAMPLES / "onboarding.md", docs / "common" / "onboarding.md")
    pipe = Pipeline(Settings(docs_dir=docs, index_dir=tmp_path / "index", embed_provider="hash",
                             qdrant_url=":memory:", _env_file=None))
    pipe.ingest()
    monkeypatch.setattr(server, "_pipeline", pipe)
    ws = tmp_path / "code" / "shop-backend" / "src"
    ws.mkdir(parents=True)
    return ws


def fake_ctx(root_path: Path):
    """Minimal stand-in for mcp's Context: advertises roots and answers list_roots()."""

    async def list_roots():
        return ListRootsResult(roots=[Root(uri=FileUrl(root_path.as_uri()), name="ws")])

    return SimpleNamespace(
        client_capabilities=SimpleNamespace(roots=SimpleNamespace(listChanged=False)),
        session=SimpleNamespace(list_roots=list_roots),
    )


@pytest.mark.anyio
async def test_search_scopes_to_detected_project(workspace):
    out = json.loads(await server.search("refunds", top_k=5, ctx=fake_ctx(workspace)))
    assert out["scope"]["mode"] == "project"
    assert out["scope"]["project"] == "shop-backend"
    assert out["scope"]["prefixes"] == ["shop-backend/", "common/"]
    assert out["results"] and {r["project"] for r in out["results"]} <= {"shop-backend", "common"}


@pytest.mark.anyio
async def test_search_falls_back_to_all_when_workspace_unknown(workspace, tmp_path):
    elsewhere = tmp_path / "code" / "something-else"
    elsewhere.mkdir()
    out = json.loads(await server.search("refunds", top_k=5, ctx=fake_ctx(elsewhere)))
    assert out["scope"]["mode"] == "all"
    assert {r["project"] for r in out["results"]} >= {"shop-backend", "mobile-app"}


@pytest.mark.anyio
async def test_client_without_roots_uses_cwd(workspace, monkeypatch):
    monkeypatch.chdir(workspace)
    ctx = SimpleNamespace(client_capabilities=SimpleNamespace(roots=None), session=None)
    out = json.loads(await server.search("refunds", top_k=5, ctx=ctx))
    assert out["scope"]["project"] == "shop-backend"


@pytest.mark.anyio
async def test_index_status_shows_detected_project(workspace):
    status = json.loads(await server.index_status(ctx=fake_ctx(workspace)))
    assert status["projects"] == ["common", "mobile-app", "shop-backend"]
    assert status["detected_project"] == "shop-backend"


@pytest.mark.anyio
async def test_explicit_project_parameter(workspace, tmp_path):
    elsewhere = tmp_path / "code" / "nowhere"
    elsewhere.mkdir()
    out = json.loads(await server.search("refunds", top_k=5, project="mobile-app", ctx=fake_ctx(elsewhere)))
    assert out["scope"]["mode"] == "project" and out["scope"]["project"] == "mobile-app"
    assert {r["project"] for r in out["results"]} <= {"mobile-app", "common"}

    out = json.loads(await server.search("refunds", top_k=5, project="ghost", ctx=fake_ctx(elsewhere)))
    assert out["scope"]["mode"] == "all" and "not indexed" in out["scope"]["note"]


@pytest.mark.anyio
async def test_claude_project_dir_env_fallback(workspace, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # cwd is NOT the project
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(workspace))
    ctx = SimpleNamespace(client_capabilities=SimpleNamespace(roots=None), session=None)
    out = json.loads(await server.search("refunds", top_k=5, ctx=ctx))
    assert out["scope"]["project"] == "shop-backend"
