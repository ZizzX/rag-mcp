import json

import pytest

from rag_mcp import server


@pytest.fixture(autouse=True)
def _use_test_pipeline(pipeline, monkeypatch):
    pipeline.ingest()
    monkeypatch.setattr(server, "_pipeline", pipeline)


def test_tools_registered():
    names = {t.name for t in server.server._tool_manager.list_tools()}
    assert names == {"search", "fetch_document", "list_sources", "index_status"}


@pytest.mark.anyio
async def test_search_tool_roundtrip():
    result = await server.server.call_tool("search", {"query": "VPN certificate", "top_k": 2})
    assert not result.is_error
    payload = json.loads(result.content[0].text)
    assert payload[0]["source"] == "onboarding.md"
    assert set(payload[0]) >= {"source", "heading", "page", "doc_id", "text"}


def test_list_sources_and_status():
    sources = json.loads(server.list_sources())
    assert "security-policy.pdf" in sources
    status = json.loads(server.index_status())
    assert status["embedder"] == "hash" and status["documents"] == 3
