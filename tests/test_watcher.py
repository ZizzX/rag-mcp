"""Watch mode: filter, background re-indexing and the reindex MCP tool."""

import json
import time

import pytest

from rag_mcp import server
from rag_mcp.watcher import is_relevant


def test_is_relevant_filters_temp_and_hidden_files():
    assert is_relevant("/docs/shop/spec.md")
    assert is_relevant("/docs/shop/Spec.PDF")
    assert is_relevant("/docs/notes.txt")
    assert not is_relevant("/docs/shop/image.png")
    assert not is_relevant("/docs/.git/config.md")
    assert not is_relevant("/docs/shop/.spec.md.swp")
    assert not is_relevant("/docs/shop/~$spec.md")
    assert not is_relevant("/docs/shop/spec.md.tmp")
    assert not is_relevant("/docs/shop/.~lock.spec.md#")
    assert not is_relevant("/docs/shop")  # directory event


def _wait(predicate, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return False


def test_watcher_reindexes_on_change(pipeline, workspace):
    docs, _ = workspace
    pipeline.settings.watch_debounce_ms = 200
    watcher = pipeline.start_watcher()
    try:
        # initial catch-up ingest
        assert _wait(lambda: watcher.runs >= 1), watcher.last_error
        assert "onboarding.md" in pipeline.store.sources()

        (docs / "new-policy.md").write_text("# New policy\n\nBadges must be worn at all times.", encoding="utf-8")
        assert _wait(lambda: "new-policy.md" in pipeline.store.sources()), (watcher.runs, watcher.last_error)
        assert pipeline.search("badges worn", top_k=1)[0]["source"] == "new-policy.md"

        (docs / "deploy-runbook.md").unlink()
        assert _wait(lambda: "deploy-runbook.md" not in pipeline.store.sources())

        # a temp file must not trigger anything
        runs_before = watcher.runs
        (docs / "~$draft.md").write_text("junk", encoding="utf-8")
        time.sleep(1.0)
        assert watcher.runs == runs_before
        assert watcher.last_error is None
        assert pipeline.status()["watch"]["enabled"] is True
    finally:
        pipeline.stop_watcher()
    assert not watcher.thread.is_alive()
    assert pipeline.status()["watch"]["enabled"] is False


def test_reindex_tool(pipeline, workspace, monkeypatch):
    docs, _ = workspace
    monkeypatch.setattr(server, "_pipeline", pipeline)
    first = json.loads(server.reindex())
    assert first["added"] == 3 and first["errors"] == []
    (docs / "extra.md").write_text("# Extra\n\nmore text", encoding="utf-8")
    second = json.loads(server.reindex())
    assert (second["added"], second["unchanged"]) == (1, 3)
