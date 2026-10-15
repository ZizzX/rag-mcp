"""Re-index documents when files in docs_dir change.

Built on `watchfiles` (native notifications on Linux, macOS and Windows). Events are
debounced so a save that touches a file several times, or a sync client dropping
twenty files at once, results in one incremental ingest.

The watcher runs in a thread so the MCP server keeps answering searches. Writes go
through `Pipeline.ingest`, i.e. under `index.lock`: when several servers watch the
same folder, one of them indexes and the others see `IndexBusy`, skip, and pick the
commit up from disk. `rag-mcp watch` is the standalone equivalent for when no
server is running.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable

from watchfiles import Change, watch

from .loaders import SUPPORTED_SUFFIXES
from .store import IndexBusy

log = logging.getLogger("rag_mcp.watch")

# Editors and sync clients write these while saving; they never need indexing.
_TEMP_MARKERS = ("~$", ".~", ".#")
_TEMP_SUFFIXES = (".tmp", ".swp", ".swx", ".part", ".crdownload", ".download")


def is_relevant(path: str) -> bool:
    """True for a Markdown/PDF/text file that is not hidden or a temp artefact."""
    p = Path(path)
    if any(part.startswith(".") for part in p.parts if part not in (".", "..")):
        return False
    name = p.name
    if name.startswith(_TEMP_MARKERS) or name.lower().endswith(_TEMP_SUFFIXES):
        return False
    return p.suffix.lower() in SUPPORTED_SUFFIXES


def _filter(change: Change, path: str) -> bool:
    # Directory events arrive without a suffix and are ignored; a deleted folder still
    # produces delete events for the files inside it.
    return is_relevant(path)


def watch_docs(
    docs_dir: Path,
    on_change: Callable[[set[tuple[Change, str]]], None],
    debounce_ms: int = 1500,
    stop_event: threading.Event | None = None,
) -> None:
    """Block, calling `on_change(changes)` after each debounced batch of relevant changes.

    Returns when `stop_event` is set. Exceptions from `on_change` are logged, not raised,
    so one bad file cannot kill the watcher.
    """
    if not docs_dir.exists():
        raise FileNotFoundError(f"Documents directory does not exist: {docs_dir}")
    log.info("watching %s", docs_dir)
    for changes in watch(docs_dir, watch_filter=_filter, debounce=debounce_ms, stop_event=stop_event, rust_timeout=1000):
        try:
            on_change(changes)
        except Exception:
            log.exception("re-index after change failed")


class DocsWatcher:
    """Runs `watch_docs` in a daemon thread.

    On start it calls `ingest` once if `needs_ingest()` says documents changed while no
    watcher was running, then re-indexes after each batch of changes.
    """

    def __init__(
        self,
        docs_dir: Path,
        ingest: Callable[[], object],
        needs_ingest: Callable[[], bool] = lambda: True,
        debounce_ms: int = 1500,
    ):
        self.docs_dir = docs_dir
        self.ingest = ingest
        self.needs_ingest = needs_ingest
        self.debounce_ms = debounce_ms
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, name="rag-mcp-watch", daemon=True)
        self.last_error: str | None = None
        self.runs = 0
        self.skipped_busy = 0

    def start(self) -> "DocsWatcher":
        self.thread.start()
        return self

    def stop(self, timeout: float = 5.0) -> None:
        self.stop_event.set()
        self.thread.join(timeout)

    def _reindex(self, changes: set[tuple[Change, str]] | None = None) -> None:
        if changes:
            log.info("%d change(s): %s", len(changes), ", ".join(sorted(Path(p).name for _, p in changes)[:5]))
        try:
            result = self.ingest()
            self.runs += 1
            self.last_error = None
            log.info("index updated: %s", result)
        except IndexBusy:
            # Another process (a second server, or `rag-mcp ingest`) is already writing this
            # very change; our store reloads from its commit. Not an error.
            self.skipped_busy += 1
            log.info("re-index skipped: another rag-mcp process is updating the index")
        except Exception as e:
            self.last_error = str(e)
            log.error("re-index failed: %s", e)

    def _run(self) -> None:
        try:
            if self.needs_ingest():
                self._reindex()  # catch up on anything that changed while nobody was watching
        except Exception as e:
            self.last_error = str(e)
            log.error("initial check failed: %s", e)
        try:
            watch_docs(self.docs_dir, self._reindex, self.debounce_ms, self.stop_event)
        except Exception as e:
            self.last_error = str(e)
            log.error("watcher stopped: %s", e)
