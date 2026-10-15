"""Hybrid index: dense vectors + BM25 over the same chunks, fused with RRF.

By default everything lives in plain files in the index directory, so any number
of processes (one MCP server per Claude Code session, plus `rag-mcp ingest`) can
use the same index at once:

    manifest.json   doc_id -> content hash; written last, so it marks a finished commit
    bm25.json       chunk_id -> chunk payload (feeds BM25 and the results)
    vectors.npy     one L2-normalised row per chunk, in bm25.json order
    index.lock      held by a writer while it updates the files above

Dense search is a brute-force cosine over vectors.npy with numpy: milliseconds for
tens of thousands of chunks. Readers take no lock: files are replaced atomically and
a reader reloads them when manifest.json changes.

Set RAG_QDRANT_URL to keep the vectors in a Qdrant server instead (the docker-compose
one); ":memory:" runs an in-process Qdrant, for tests.
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

import httpx
import numpy as np
from rank_bm25 import BM25Okapi

from .chunking import Chunk
from .embeddings import Embedder

log = logging.getLogger("rag_mcp.store")

TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def tokenize(text: str) -> list[str]:
    return [t for t in TOKEN_RE.findall(text.lower()) if len(t) > 1]


def point_id(chunk_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_id))


class IndexBusy(RuntimeError):
    """Another process is writing the index right now."""


def _atomic_write(path: Path, write) -> None:
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    with open(tmp, "wb") as f:
        write(f)
    os.replace(tmp, path)


class HybridStore:
    def __init__(
        self,
        index_dir: Path,
        collection: str,
        embedder: Embedder,
        qdrant_url: str | None = None,
        qdrant_api_key: str | None = None,
        rrf_k: int = 60,
    ):
        self.index_dir = index_dir
        self.collection = collection
        self.embedder = embedder
        self.rrf_k = rrf_k
        index_dir.mkdir(parents=True, exist_ok=True)
        self.in_memory = qdrant_url == ":memory:"
        self._remote = bool(qdrant_url) and not self.in_memory
        self.client = None
        if qdrant_url:
            from qdrant_client import QdrantClient

            self.client = QdrantClient(":memory:") if self.in_memory else QdrantClient(url=qdrant_url, api_key=qdrant_api_key)
        self._bm25_path = index_dir / "bm25.json"
        self._vectors_path = index_dir / "vectors.npy"
        self._manifest_path = index_dir / "manifest.json"
        self._lock_path = index_dir / "index.lock"
        self._chunks: dict[str, dict] = {}  # chunk_id -> chunk payload (also feeds BM25)
        self._vecs: dict[str, np.ndarray] = {}  # chunk_id -> vector (local mode only)
        self._bm25: BM25Okapi | None = None
        self._bm25_ids: list[str] = []
        self._matrix = np.zeros((0, 0), dtype=np.float32)
        self._loaded_key = None
        self.reload()

    def close(self) -> None:
        if self.client is not None:
            try:
                self.client.close()
            except Exception:
                pass

    # ---------- persistence ----------
    def _manifest_key(self):
        try:
            st = self._manifest_path.stat()
        except FileNotFoundError:
            return None
        return st.st_ino, st.st_mtime_ns, st.st_size  # os.replace gives a new inode on every commit

    def _refresh(self) -> None:
        """Pick up a commit made by another process."""
        if not self.in_memory and self._manifest_key() != self._loaded_key:
            self.reload()

    def reload(self) -> None:
        key = self._manifest_key()
        chunks = json.loads(self._bm25_path.read_text(encoding="utf-8")) if self._bm25_path.exists() else {}
        vecs: dict[str, np.ndarray] = {}
        if self.client is None and chunks and self._vectors_path.exists():
            matrix = np.load(self._vectors_path)
            if len(matrix) == len(chunks):
                vecs = dict(zip(chunks, matrix))
            else:  # caught a writer between files; its manifest write will trigger another reload
                log.debug("vectors.npy and bm25.json differ in length; dense search off until next commit")
        self._chunks, self._vecs = chunks, vecs
        self._rebuild()
        self._loaded_key = key

    def _rebuild(self) -> None:
        self._bm25_ids = list(self._chunks)
        corpus = [tokenize(self._chunks[cid]["heading"] + " " + self._chunks[cid]["text"]) for cid in self._bm25_ids]
        self._bm25 = BM25Okapi(corpus) if corpus else None
        if self._vecs and len(self._vecs) == len(self._chunks):
            self._matrix = np.stack([self._vecs[cid] for cid in self._bm25_ids]).astype(np.float32)
        else:
            self._matrix = np.zeros((0, 0), dtype=np.float32)

    def manifest(self) -> dict[str, dict]:
        """doc_id -> {source, content_hash, chunks, embedder, stat: [size, mtime_ns]}"""
        if self._manifest_path.exists():
            return json.loads(self._manifest_path.read_text(encoding="utf-8"))
        return {}

    @contextmanager
    def write_lock(self):
        """Serialise writers across processes. Readers never take it."""
        f = open(self._lock_path, "a+b")
        try:
            f.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise IndexBusy(
                    "Another rag-mcp process is updating the index right now; try again when it finishes."
                ) from None
            yield
        finally:
            if os.name == "nt":
                try:
                    import msvcrt

                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            f.close()

    # ---------- collection (Qdrant modes) ----------
    def _ensure_collection(self, dim: int) -> None:
        from qdrant_client.http import models as qm

        if self.client.collection_exists(self.collection):
            info = self.client.get_collection(self.collection)
            existing = info.config.params.vectors
            size = existing.size if isinstance(existing, qm.VectorParams) else None
            if size is not None and size != dim:
                raise RuntimeError(
                    f"Collection '{self.collection}' has dim {size}, embedder produces {dim}. "
                    "Run `rag-mcp ingest --rebuild` after changing the embedding model."
                )
            return
        self.client.create_collection(
            self.collection,
            vectors_config=qm.VectorParams(size=dim, distance=qm.Distance.COSINE),
        )
        if self._remote:  # payload indexes only exist on a Qdrant server
            self.client.create_payload_index(self.collection, "doc_id", qm.PayloadSchemaType.KEYWORD)
            self.client.create_payload_index(self.collection, "source", qm.PayloadSchemaType.KEYWORD)
            self.client.create_payload_index(self.collection, "project", qm.PayloadSchemaType.KEYWORD)

    # ---------- writes (call inside write_lock) ----------
    def reset(self) -> None:
        if self.client is not None and self.client.collection_exists(self.collection):
            self.client.delete_collection(self.collection)
        self._chunks, self._vecs = {}, {}
        self.commit({})

    def delete_document(self, doc_id: str) -> None:
        if self.client is not None and self.client.collection_exists(self.collection):
            from qdrant_client.http import models as qm

            self.client.delete(
                self.collection,
                points_selector=qm.FilterSelector(
                    filter=qm.Filter(must=[qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=doc_id))])
                ),
            )
        for cid in [c for c, p in self._chunks.items() if p["doc_id"] == doc_id]:
            del self._chunks[cid]
            self._vecs.pop(cid, None)

    def upsert_chunks(self, chunks: list[Chunk]) -> None:
        if not chunks:
            return
        vectors = self.embedder.embed([f"{c.heading}\n{c.text}" if c.heading else c.text for c in chunks])
        if self.client is not None:
            from qdrant_client.http import models as qm

            self._ensure_collection(len(vectors[0]))
            points = [
                qm.PointStruct(id=point_id(c.chunk_id), vector=v, payload=asdict(c)) for c, v in zip(chunks, vectors)
            ]
            for i in range(0, len(points), 128):
                self.client.upsert(self.collection, points=points[i : i + 128])
        else:
            dim = len(next(iter(self._vecs.values()))) if self._vecs else len(vectors[0])
            if len(vectors[0]) != dim:
                raise RuntimeError(
                    f"Index has dim {dim}, embedder produces {len(vectors[0])}. "
                    "Run `rag-mcp ingest --rebuild` after changing the embedding model."
                )
            for c, v in zip(chunks, vectors):
                self._vecs[c.chunk_id] = np.asarray(v, dtype=np.float32)
        for c in chunks:
            self._chunks[c.chunk_id] = asdict(c)

    def commit(self, manifest: dict[str, dict]) -> None:
        """Write vectors, chunks, then the manifest: readers reload when the manifest changes."""
        self._rebuild()
        if self.client is None:
            _atomic_write(self._vectors_path, lambda f: np.save(f, self._matrix))
        payload = json.dumps(self._chunks, ensure_ascii=False).encode("utf-8")
        _atomic_write(self._bm25_path, lambda f: f.write(payload))
        payload = json.dumps(manifest, ensure_ascii=False, indent=1).encode("utf-8")
        _atomic_write(self._manifest_path, lambda f: f.write(payload))
        self._loaded_key = self._manifest_key()

    # ---------- reads ----------
    def count(self) -> int:
        self._refresh()
        return len(self._chunks)

    def chunks_for_document(self, doc_id: str) -> list[dict]:
        self._refresh()
        return sorted((p for p in self._chunks.values() if p["doc_id"] == doc_id), key=lambda p: p["index"])

    def sources(self) -> dict[str, dict]:
        self._refresh()
        out: dict[str, dict] = {}
        for p in self._chunks.values():
            entry = out.setdefault(p["source"], {"doc_id": p["doc_id"], "title": p["title"], "chunks": 0})
            entry["chunks"] += 1
        return dict(sorted(out.items()))

    def projects(self) -> list[str]:
        """Top-level folders present in the index (the multi-project layout)."""
        self._refresh()
        return sorted({p["project"] for p in self._chunks.values() if p.get("project")})

    def search(
        self,
        query: str,
        top_k: int,
        candidate_multiplier: int = 4,
        source_prefixes: list[str] | None = None,
        dense_errors: list[Exception] | None = None,
    ) -> list[dict]:
        """Hybrid search. `source_prefixes` restricts results to sources starting with any of them
        ("shop-backend/", "common/", "specs/api.pdf"). With `dense_errors`, an unreachable embedder
        (connection refused or timed out) is appended there and the search runs on BM25 alone instead
        of raising. Other embedder errors (bad API key, missing model) still raise: they need a fix."""
        self._refresh()
        n = max(top_k * candidate_multiplier, top_k)
        prefixes = [p for p in (source_prefixes or []) if p] or None
        try:
            dense = self._dense_search(query, n, prefixes)
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            if dense_errors is None:
                raise
            dense_errors.append(e)
            dense = []
        sparse = self._bm25_search(query, n, prefixes)
        fused: dict[str, float] = {}
        for ranking in (dense, sparse):
            for rank, cid in enumerate(ranking):
                fused[cid] = fused.get(cid, 0.0) + 1.0 / (self.rrf_k + rank + 1)
        ordered = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
        results = []
        for cid, score in ordered:
            if cid not in self._chunks:  # Qdrant point whose chunk was removed since our last reload
                continue
            payload = dict(self._chunks[cid])
            payload["score"] = round(score, 6)
            payload["matched"] = [name for name, r in (("dense", dense), ("bm25", sparse)) if cid in r]
            results.append(payload)
        return results

    @staticmethod
    def _matches(source: str, prefixes: list[str] | None) -> bool:
        return not prefixes or any(source.startswith(p) for p in prefixes)

    def _dense_search(self, query: str, n: int, prefixes: list[str] | None) -> list[str]:
        if self.client is not None:
            return self._qdrant_search(query, n, prefixes)
        if not len(self._matrix):
            return []
        vector = np.asarray(self.embedder.embed([query])[0], dtype=np.float32)
        if vector.shape[0] != self._matrix.shape[1]:
            raise RuntimeError(
                f"Index has dim {self._matrix.shape[1]}, embedder produces {vector.shape[0]}. "
                "Run `rag-mcp ingest --rebuild` after changing the embedding model."
            )
        scores = self._matrix @ vector  # rows and query are L2-normalised: dot product = cosine
        out = []
        for i in np.argsort(-scores):
            cid = self._bm25_ids[i]
            if self._matches(self._chunks[cid]["source"], prefixes):
                out.append(cid)
                if len(out) >= n:
                    break
        return out

    def _qdrant_search(self, query: str, n: int, prefixes: list[str] | None) -> list[str]:
        from qdrant_client.http import models as qm

        if not self.client.collection_exists(self.collection):
            return []
        vector = self.embedder.embed([query])[0]
        flt = None
        limit = n
        if prefixes:
            # Whole-project prefixes ("shop-backend/") map to an exact keyword filter on `project`.
            # Anything finer is filtered in Python after oversampling.
            projects = [p.rstrip("/") for p in prefixes]
            if all(p and "/" not in p for p in projects):
                flt = qm.Filter(must=[qm.FieldCondition(key="project", match=qm.MatchAny(any=projects))])
            else:
                limit = n * 4
        res = self.client.query_points(
            self.collection, query=vector, limit=limit, query_filter=flt, with_payload=["chunk_id", "source"]
        )
        ids = [p.payload["chunk_id"] for p in res.points if self._matches(p.payload["source"], prefixes)]
        return ids[:n]

    def _bm25_search(self, query: str, n: int, prefixes: list[str] | None) -> list[str]:
        if self._bm25 is None:
            return []
        scores = self._bm25.get_scores(tokenize(query))
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        out = []
        for i in ranked:
            if scores[i] <= 0:
                break
            cid = self._bm25_ids[i]
            if not self._matches(self._chunks[cid]["source"], prefixes):
                continue
            out.append(cid)
            if len(out) >= n:
                break
        return out
