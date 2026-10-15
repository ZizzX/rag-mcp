"""Several processes share one index directory: one MCP server per client session plus `rag-mcp ingest`."""

import json
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from rag_mcp import server
from rag_mcp.chunking import Chunk
from rag_mcp.config import Settings
from rag_mcp.embeddings import HashEmbedder
from rag_mcp.pipeline import Pipeline
from rag_mcp.store import HybridStore, IndexBusy


def _chunk(doc: str, text: str) -> Chunk:
    return Chunk(chunk_id=f"{doc}#0", doc_id=doc, source=f"{doc}.md", title=doc, heading="",
                 text=text, page=None, index=0, project="")


def _local_settings(docs, index) -> Settings:
    return Settings(docs_dir=docs, index_dir=index, embed_provider="hash", chunk_size=400, chunk_overlap=50,
                    _env_file=None)


def test_readers_share_dir_with_a_writer_and_see_its_commit(tmp_path):
    open_store = lambda: HybridStore(tmp_path, "docs", HashEmbedder())  # noqa: E731
    writer = open_store()
    with writer.write_lock():
        writer.upsert_chunks([_chunk("vpn", "Request a VPN certificate from the IT desk")])
        writer.commit({"vpn": {}})
    readers = [open_store(), open_store()]

    def read(store):
        return [r["doc_id"] for r in store.search("kubernetes rollback procedure", top_k=3)]

    with ThreadPoolExecutor(4) as pool:
        before = list(pool.map(read, readers * 5))
        with writer.write_lock():
            writer.upsert_chunks([_chunk("deploy", "Kubernetes rollback procedure: helm rollback")])
            with pytest.raises(IndexBusy):  # a second writer is refused, readers are not
                with open_store().write_lock():
                    pass
            during = list(pool.map(read, readers * 5))
            writer.commit({"vpn": {}, "deploy": {}})
        after = list(pool.map(read, readers))

    assert all("deploy" not in r for r in before + during)
    for hits in after:
        assert hits[0] == "deploy"
    assert "dense" in readers[0].search("kubernetes", top_k=1)[0]["matched"]  # vectors.npy reloaded too


def test_cli_ingest_while_a_server_store_is_open(workspace):
    docs, index = workspace
    serving = Pipeline(_local_settings(docs, index))
    serving.ingest()
    assert serving.needs_ingest() is False
    before = serving.store.count()

    (docs / "new.md").write_text("# Parking\n\nVisitor parking is on level minus two.", encoding="utf-8")
    assert serving.needs_ingest() is True
    # what `rag-mcp ingest` does in its own process, while `serving` keeps its store open
    report = Pipeline(_local_settings(docs, index)).ingest()
    assert report.added == 1

    assert serving.store.count() > before
    assert serving.search("visitor parking", top_k=1)[0]["source"] == "new.md"


def test_reindex_tool_picks_up_new_files(workspace, monkeypatch):
    docs, index = workspace
    monkeypatch.setattr(server, "_pipeline", Pipeline(_local_settings(docs, index)))
    r = json.loads(server.reindex())
    assert r["added"] == 3 and r["total_chunks"] > 0
    (docs / "new.md").write_text("# Parking\n\nVisitor parking is on level minus two.", encoding="utf-8")
    assert json.loads(server.reindex())["added"] == 1


@pytest.mark.anyio
async def test_search_falls_back_to_bm25_when_ollama_is_down(workspace, monkeypatch):
    docs, index = workspace
    pipe = Pipeline(_local_settings(docs, index))
    pipe.ingest()

    def refuse(texts):
        raise httpx.ConnectError("Connection refused")

    monkeypatch.setattr(pipe.store.embedder, "embed", refuse)
    monkeypatch.setattr(pipe.settings, "embed_provider", "ollama")
    monkeypatch.setattr(server, "_pipeline", pipe)
    r = await server.server.call_tool("search", {"query": "VPN"})
    payload = json.loads(r.content[0].text)
    assert "error" not in payload
    assert payload["results"] and all(x["matched"] == ["bm25"] for x in payload["results"])
    assert payload["degraded"].startswith("dense off:")
    assert "ollama serve" in payload["degraded"] and pipe.settings.ollama_url in payload["degraded"]


def test_only_an_unreachable_embedder_falls_back_and_only_when_asked(workspace, monkeypatch):
    docs, index = workspace
    pipe = Pipeline(_local_settings(docs, index))
    pipe.ingest()
    error = httpx.ConnectError("Connection refused")

    def fail(texts):
        raise error

    monkeypatch.setattr(pipe.store.embedder, "embed", fail)
    with pytest.raises(httpx.ConnectError):  # CLI and evaluate pass no list: they still see the failure
        pipe.search("VPN")
    collected: list[Exception] = []
    assert pipe.search("VPN", dense_errors=collected)
    assert collected == [error]

    request = httpx.Request("POST", "http://localhost:11434/api/embed")
    error = httpx.HTTPStatusError("not found", request=request, response=httpx.Response(404, request=request))
    with pytest.raises(httpx.HTTPStatusError):  # a missing model is a config error, not an outage
        pipe.search("VPN", dense_errors=[])


@pytest.mark.anyio
async def test_search_on_empty_index_says_what_to_do(workspace, monkeypatch):
    docs, index = workspace
    monkeypatch.setattr(server, "_pipeline", Pipeline(_local_settings(docs, index)))
    r = await server.server.call_tool("search", {"query": "VPN"})
    assert "reindex" in json.loads(r.content[0].text)["error"]
