"""Ingest and query orchestration."""

from __future__ import annotations

import atexit
import logging
import sys
from dataclasses import dataclass

from .chunking import chunk_document
from .config import Settings, load_settings
from .embeddings import make_embedder
from .loaders import doc_id_for, file_hash, iter_files, load_document
from .store import HybridStore

log = logging.getLogger("rag_mcp")


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
        if manifest and any(m.get("embedder") not in (None, self.embedder.name) for m in manifest.values()):
            raise RuntimeError(
                "Index was built with a different embedding model. Run `rag-mcp ingest --rebuild`."
            )

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

        for doc_id in [d for d in manifest if d not in seen]:
            self.store.delete_document(doc_id)
            del manifest[doc_id]
            report.removed += 1

        self.store.commit()
        self.store.save_manifest(manifest)
        return report

    def search(self, query: str, top_k: int | None = None, source_prefix: str | None = None) -> list[dict]:
        return self.store.search(
            query,
            top_k=top_k or self.settings.top_k,
            candidate_multiplier=self.settings.candidate_multiplier,
            source_prefix=source_prefix,
        )

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
            "documents": len(self.store.manifest()),
            "chunks": self.store.count(),
        }


def configure_logging(verbose: bool = False) -> None:
    # MCP over stdio uses stdout for protocol messages: logs must go to stderr.
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        stream=sys.stderr,
        format="%(levelname)s %(name)s: %(message)s",
    )
