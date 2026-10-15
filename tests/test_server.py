import json

import pytest

from rag_mcp import server


@pytest.fixture(autouse=True)
def _use_test_pipeline(pipeline, monkeypatch):
    pipeline.ingest()
    monkeypatch.setattr(server, "_pipeline", pipeline)


def test_tools_registered():
    names = {t.name for t in server.server._tool_manager.list_tools()}
    assert names == {"search", "fetch_document", "list_sources", "index_status", "reindex"}


def test_root_uri_to_path():
    assert server._root_to_path("file:///home/me/proj") == "/home/me/proj"
    assert server._root_to_path("file:///C:/Users/me/proj") == "C:/Users/me/proj"
    assert server._root_to_path("file:///home/me/my%20proj") == "/home/me/my proj"
    assert server._root_to_path("https://example.com/x") is None


@pytest.mark.anyio
async def test_search_tool_roundtrip():
    result = await server.server.call_tool("search", {"query": "VPN certificate", "top_k": 2})
    assert not result.is_error
    payload = json.loads(result.content[0].text)
    # example docs have no project folders, so the workspace cannot match: falls back to "all"
    assert payload["scope"]["mode"] == "all"
    assert "degraded" not in payload
    hit = payload["results"][0]
    assert hit["source"] == "onboarding.md"
    assert set(hit) >= {"source", "project", "heading", "page", "doc_id", "text"}


@pytest.mark.anyio
async def test_explicit_prefix_and_all_projects_flags():
    r = await server.server.call_tool("search", {"query": "rollback", "source_prefix": "deploy-"})
    payload = json.loads(r.content[0].text)
    assert payload["scope"] == {"mode": "prefix", "prefixes": ["deploy-"], "project": None}
    assert all(x["source"].startswith("deploy-") for x in payload["results"])

    r = await server.server.call_tool("search", {"query": "rollback", "all_projects": True})
    assert json.loads(r.content[0].text)["scope"]["mode"] == "all"


@pytest.mark.anyio
async def test_index_status_reports_workspace(monkeypatch):
    async def fake_paths(ctx):
        return ["/tmp/some/workspace"]

    monkeypatch.setattr(server, "workspace_paths", fake_paths)
    r = await server.server.call_tool("index_status", {})
    status = json.loads(r.content[0].text)
    assert status["embedder"] == "hash" and status["documents"] == 3
    assert status["workspace"] == ["/tmp/some/workspace"]
    assert status["detected_project"] is None


def test_list_sources():
    sources = json.loads(server.list_sources())
    assert "security-policy.pdf" in sources
    assert json.loads(server.list_sources(project="nope")) == {}
