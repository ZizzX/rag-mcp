"""Ingest and query orchestration."""

from __future__ import annotations

import atexit
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

from .chunking import chunk_document
from .config import Settings, load_settings
from .embeddings import make_embedder
from .loaders import doc_id_for, file_hash, iter_files, load_document
from .store import HybridStore

log = logging.getLogger("rag_mcp")

# Bump when the chunk payload changes in a way that needs a full re-embed.
SCHEMA_VERSION = 2


@dataclass
class IngestReport:
    added: int = 0
    updated: int = 0
    removed: int = 0
    unchanged: int = 0
    chunks: int = 0
    errors: list[str] = None  # type: ignore[assignment]

    def __post_init__(self):
        self.errors = self.errors or []


class Pipeline:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or load_settings()
        self.embedder = make_embedder(self.settings)
        self.store = HybridStore(
            index_dir=self.settings.index_dir,
            collection=self.settings.collection,
            embedder=self.embedder,
            qdrant_url=self.settings.qdrant_url,
            qdrant_api_key=self.settings.qdrant_api_key,
            rrf_k=self.settings.rrf_k,
        )
        atexit.register(self.store.close)

    def ingest(self, rebuild: bool = False) -> IngestReport:
        s = self.settings
        report = IngestReport()
        if not s.docs_dir.exists():
            raise FileNotFoundError(f"Documents directory does not exist: {s.docs_dir}")
        if rebuild:
            self.store.reset()
        manifest = self.store.manifest()
        docs = {k: v for k, v in manifest.items() if k != "_meta"}
        if docs and any(m.get("embedder") not in (None, self.embedder.name) for m in docs.values()):
            raise RuntimeError(
                "Index was built with a different embedding model. Run `rag-mcp ingest --rebuild`."
            )
        if docs and manifest.get("_meta", {}).get("schema") != SCHEMA_VERSION:
            raise RuntimeError("Index format changed in this version of rag-mcp. Run `rag-mcp ingest --rebuild`.")
        manifest["_meta"] = {"schema": SCHEMA_VERSION}

        seen: set[str] = set()
        for path in iter_files(s.docs_dir):
            source = path.relative_to(s.docs_dir).as_posix()
            doc_id = doc_id_for(source)
            seen.add(doc_id)
            prev = manifest.get(doc_id)
            try:
                content_hash = file_hash(path)
                if prev and prev.get("content_hash") == content_hash:
                    report.unchanged += 1
                    continue
                doc = load_document(path, s.docs_dir)
                chunks = chunk_document(doc, s.chunk_size, s.chunk_overlap)
                if prev:
                    self.store.delete_document(doc_id)
                    report.updated += 1
                else:
                    report.added += 1
                self.store.upsert_chunks(chunks)
                manifest[doc_id] = {
                    "source": source,
                    "content_hash": content_hash,
                    "chunks": len(chunks),
                    "embedder": self.embedder.name,
                }
                report.chunks += len(chunks)
                log.info("indexed %s (%d chunks)", source, len(chunks))
            except Exception as e:  # keep going; report at the end
                report.errors.append(f"{source}: {e}")
                log.warning("failed %s: %s", source, e)

        for doc_id in [d for d in manifest if d != "_meta" and d not in seen]:
            self.store.delete_document(doc_id)
            del manifest[doc_id]
            report.removed += 1

        self.store.commit()
        self.store.save_manifest(manifest)
        return report

    def search(
        self, query: str, top_k: int | None = None, source_prefixes: list[str] | None = None
    ) -> list[dict]:
        return self.store.search(
            query,
            top_k=top_k or self.settings.top_k,
            candidate_multiplier=self.settings.candidate_multiplier,
            source_prefixes=source_prefixes,
        )

    # ---------- multi-project scoping ----------
    def detect_project(self, workspace_paths: list[str]) -> str | None:
        """Map the client's workspace folder(s) to an indexed project.

        Walks up from each path and returns the first ancestor whose folder name is an
        indexed top-level folder, so a subfolder of a project still resolves to it.
        RAG_PROJECT overrides detection.
        """
        if self.settings.project:
            return self.settings.project
        indexed = set(self.store.projects())
        if not indexed:
            return None
        for raw in workspace_paths:
            path = Path(raw)
            for candidate in (path, *path.parents):
                if candidate.name in indexed:
                    return candidate.name
        return None

    def scope_for(self, project: str | None) -> list[str] | None:
        """Prefixes to search for a project: the project itself plus shared folders that exist."""
        if not project:
            return None
        indexed = set(self.store.projects())
        shared = [s for s in self.settings.shared_projects if s in indexed and s != project]
        return [f"{project}/", *(f"{s}/" for s in shared)]

    def document(self, doc_id: str) -> dict | None:
        chunks = self.store.chunks_for_document(doc_id)
        if not chunks:
            return None
        return {
            "doc_id": doc_id,
            "source": chunks[0]["source"],
            "title": chunks[0]["title"],
            "chunks": len(chunks),
            "text": "\n\n".join(c["text"] for c in chunks),
        }

    def status(self) -> dict:
        s = self.settings
        return {
            "docs_dir": str(s.docs_dir),
            "index_dir": str(s.index_dir),
            "qdrant": s.qdrant_url or "embedded",
            "embedder": self.embedder.name,
            "documents": sum(1 for k in self.store.manifest() if k != "_meta"),
            "chunks": self.store.count(),
            "projects": self.store.projects(),
            "shared_projects": self.settings.shared_projects,
        }


def configure_logging(verbose: bool = False) -> None:
    # MCP over stdio uses stdout for protocol messages: logs must go to stderr.
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        stream=sys.stderr,
        format="%(levelname)s %(name)s: %(message)s",
    )
