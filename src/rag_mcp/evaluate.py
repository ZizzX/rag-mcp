"""Retrieval evaluation against a golden question set.

eval/questions.jsonl, one JSON object per line:
  {"question": "...", "expected_source": "handbook/onboarding.md"}
  {"question": "...", "expected_text": "some phrase that must appear in a retrieved chunk"}
Either key (or both) may be present. Metrics: hit@k and MRR.
"""

from __future__ import annotations

import json
from pathlib import Path

from .pipeline import Pipeline


def load_questions(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            rows.append(json.loads(line))
    return rows


def _hit_rank(results: list[dict], q: dict) -> int | None:
    for i, r in enumerate(results, start=1):
        if q.get("expected_source") and r["source"] == q["expected_source"]:
            return i
        if q.get("expected_text") and q["expected_text"].lower() in r["text"].lower():
            return i
    return None


def evaluate(pipe: Pipeline, questions: list[dict], k: int) -> dict:
    hits = 0
    mrr = 0.0
    misses = []
    for q in questions:
        results = pipe.search(q["question"], top_k=k)
        rank = _hit_rank(results, q)
        if rank:
            hits += 1
            mrr += 1.0 / rank
        else:
            misses.append({"question": q["question"], "got": [r["source"] for r in results]})
    n = max(len(questions), 1)
    return {"questions": len(questions), "k": k, "hit_rate": round(hits / n, 3), "mrr": round(mrr / n, 3), "misses": misses}
