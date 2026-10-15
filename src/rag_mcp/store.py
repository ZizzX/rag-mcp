"""Hybrid index: dense vectors in Qdrant + BM25 over the same chunks, fused with RRF.

Qdrant runs embedded (a local directory) by default, so nothing needs to be
installed; set RAG_QDRANT_URL to use a server (e.g. the docker-compose one).
BM25 is kept in-process and persisted as JSON next to the Qdrant data.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict
from pathlib import Path

from qdrant_client import QdrantClient
from qdrant_client.http import models as qm
from rank_bm25 import BM25Okapi

from .chunking import Chunk
from .embeddings import Embedder

TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def tokenize(text: str) -> list[str]:
    return [t for t in TOKEN_RE.findall(text.lower()) if len(t) > 1]


def point_id(chunk_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_id))


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
        self._remote = bool(qdrant_url) and qdrant_url != ":memory:"
        if qdrant_url == ":memory:":
            self.client = QdrantClient(":memory:")
        elif self._remote:
            self.client = QdrantClient(url=qdrant_url, api_key=qdrant_api_key)
        else:
            self.client = QdrantClient(path=str(index_dir / "qdrant"))
        self._bm25_path = index_dir / "bm25.json"
        self._manifest_path = index_dir / "manifest.json"
        self._chunks: dict[str, dict] = {}  # chunk_id -> chunk payload (also feeds BM25)
        self._bm25: BM25Okapi | None = None
        self._bm25_ids: list[str] = []
        self._load_bm25()

    def close(self) -> None:
        try:
            self.client.close()
        except Exception:
            pass

    # ---------- persistence ----------
    def _load_bm25(self) -> None:
        if self._bm25_path.exists():
            self._chunks = json.loads(self._bm25_path.read_text(encoding="utf-8"))
        self._rebuild_bm25()

    def _save_bm25(self) -> None:
        self._bm25_path.write_text(json.dumps(self._chunks, ensure_ascii=False), encoding="utf-8")

    def _rebuild_bm25(self) -> None:
        self._bm25_ids = list(self._chunks)
        corpus = [tokenize(self._chunks[cid]["heading"] + " " + self._chunks[cid]["text"]) for cid in self._bm25_ids]
        self._bm25 = BM25Okapi(corpus) if corpus else None

    def manifest(self) -> dict[str, dict]:
        """doc_id -> {source, content_hash, chunks, embedder}"""
        if self._manifest_path.exists():
            return json.loads(self._manifest_path.read_text(encoding="utf-8"))
        return {}

    def save_manifest(self, manifest: dict[str, dict]) -> None:
        self._manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")

    # ---------- collection ----------
    def _ensure_collection(self, dim: int) -> None:
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

    def reset(self) -> None:
        if self.client.collection_exists(self.collection):
            self.client.delete_collection(self.collection)
        self._chunks = {}
        self._rebuild_bm25()
        self._save_bm25()
        self.save_manifest({})

    # ---------- writes ----------
    def delete_document(self, doc_id: str) -> None:
        if self.client.collection_exists(self.collection):
            self.client.delete(
                self.collection,
                points_selector=qm.FilterSelector(
                    filter=qm.Filter(must=[qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=doc_id))])
                ),
            )
        for cid in [c for c, p in self._chunks.items() if p["doc_id"] == doc_id]:
            del self._chunks[cid]

    def upsert_chunks(self, chunks: list[Chunk]) -> None:
        if not chunks:
            return
        vectors = self.embedder.embed([f"{c.heading}\n{c.text}" if c.heading else c.text for c in chunks])
        self._ensure_collection(len(vectors[0]))
        points = [
            qm.PointStruct(id=point_id(c.chunk_id), vector=v, payload=asdict(c)) for c, v in zip(chunks, vectors)
        ]
        for i in range(0, len(points), 128):
            self.client.upsert(self.collection, points=points[i : i + 128])
        for c in chunks:
            self._chunks[c.chunk_id] = asdict(c)

    def commit(self) -> None:
        self._rebuild_bm25()
        self._save_bm25()

    # ---------- reads ----------
    def count(self) -> int:
        return len(self._chunks)

    def chunks_for_document(self, doc_id: str) -> list[dict]:
        return sorted((p for p in self._chunks.values() if p["doc_id"] == doc_id), key=lambda p: p["index"])

    def sources(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for p in self._chunks.values():
            entry = out.setdefault(p["source"], {"doc_id": p["doc_id"], "title": p["title"], "chunks": 0})
            entry["chunks"] += 1
        return dict(sorted(out.items()))

    def search(self, query: str, top_k: int, candidate_multiplier: int = 4, source_prefix: str | None = None) -> list[dict]:
        n = max(top_k * candidate_multiplier, top_k)
        dense = self._dense_search(query, n, source_prefix)
        sparse = self._bm25_search(query, n, source_prefix)
        fused: dict[str, float] = {}
        for ranking in (dense, sparse):
            for rank, cid in enumerate(ranking):
                fused[cid] = fused.get(cid, 0.0) + 1.0 / (self.rrf_k + rank + 1)
        ordered = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
        results = []
        for cid, score in ordered:
            payload = dict(self._chunks[cid])
            payload["score"] = round(score, 6)
            payload["matched"] = [name for name, r in (("dense", dense), ("bm25", sparse)) if cid in r]
            results.append(payload)
        return results

    def _dense_search(self, query: str, n: int, source_prefix: str | None) -> list[str]:
        if not self.client.collection_exists(self.collection):
            return []
        vector = self.embedder.embed([query])[0]
        flt = None
        if source_prefix:
            flt = qm.Filter(must=[qm.FieldCondition(key="source", match=qm.MatchText(text=source_prefix))])
        res = self.client.query_points(self.collection, query=vector, limit=n, query_filter=flt, with_payload=["chunk_id"])
        return [p.payload["chunk_id"] for p in res.points]

    def _bm25_search(self, query: str, n: int, source_prefix: str | None) -> list[str]:
        if self._bm25 is None:
            return []
        scores = self._bm25.get_scores(tokenize(query))
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        out = []
        for i in ranked:
            if scores[i] <= 0:
                break
            cid = self._bm25_ids[i]
            if source_prefix and not self._chunks[cid]["source"].startswith(source_prefix):
                continue
            out.append(cid)
            if len(out) >= n:
                break
        return out
