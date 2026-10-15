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


def test_watcher_skips_when_another_writer_holds_the_lock(pipeline, workspace, monkeypatch):
    from rag_mcp.store import IndexBusy

    docs, _ = workspace
    pipeline.ingest()
    calls = {"n": 0}

    def busy_ingest():
        calls["n"] += 1
        raise IndexBusy("another rag-mcp process is writing")

    monkeypatch.setattr(pipeline, "ingest", busy_ingest)
    monkeypatch.setattr(pipeline, "needs_ingest", lambda: False)
    pipeline.settings.watch_debounce_ms = 200
    watcher = pipeline.start_watcher()
    try:
        assert _wait(lambda: watcher.thread.is_alive())
        (docs / "later.md").write_text("# Later\n\ntext", encoding="utf-8")
        assert _wait(lambda: watcher.skipped_busy >= 1), watcher.last_error
        assert watcher.last_error is None and watcher.runs == 0 and calls["n"] >= 1
    finally:
        pipeline.stop_watcher()


def test_watcher_does_not_reingest_when_index_is_current(pipeline, workspace):
    pipeline.ingest()
    assert pipeline.needs_ingest() is False
    pipeline.settings.watch_debounce_ms = 200
    watcher = pipeline.start_watcher()
    try:
        time.sleep(1.0)
        assert watcher.runs == 0  # nothing changed since the last commit: no catch-up ingest
    finally:
        pipeline.stop_watcher()
