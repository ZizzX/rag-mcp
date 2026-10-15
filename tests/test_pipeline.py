from rag_mcp.evaluate import evaluate, load_questions
from rag_mcp.config import PROJECT_ROOT


def test_ingest_is_incremental(pipeline, workspace):
    docs, _ = workspace
    r1 = pipeline.ingest()
    assert r1.added == 3 and r1.errors == []
    assert pipeline.store.count() > 3

    r2 = pipeline.ingest()
    assert (r2.added, r2.updated, r2.removed, r2.unchanged) == (0, 0, 0, 3)

    (docs / "onboarding.md").write_text("# Onboarding\n\nchanged content about VPN", encoding="utf-8")
    (docs / "deploy-runbook.md").unlink()
    (docs / "new.md").write_text("# New\n\nbrand new file", encoding="utf-8")
    r3 = pipeline.ingest()
    assert (r3.added, r3.updated, r3.removed) == (1, 1, 1)
    assert set(pipeline.store.sources()) == {"onboarding.md", "new.md", "security-policy.pdf"}


def test_search_returns_sources_and_pages(pipeline):
    pipeline.ingest()
    res = pipeline.search("How long is the VPN certificate valid?", top_k=3)
    assert res and res[0]["source"] == "onboarding.md"
    assert "90 days" in res[0]["text"]
    assert set(res[0]["matched"]) <= {"dense", "bm25"}

    pdf = pipeline.search("phishing email report", top_k=3)
    assert pdf[0]["source"] == "security-policy.pdf" and pdf[0]["page"] == 2


def test_source_prefix_filter(pipeline):
    pipeline.ingest()
    res = pipeline.search("rollback", top_k=5, source_prefixes=["onboarding"])
    assert all(r["source"].startswith("onboarding") for r in res)


def test_fetch_document_and_eval(pipeline):
    pipeline.ingest()
    doc_id = pipeline.search("vacation days", top_k=1)[0]["doc_id"]
    doc = pipeline.document(doc_id)
    assert doc["source"] == "onboarding.md" and "28 days" in doc["text"]

    questions = load_questions(PROJECT_ROOT / "eval" / "questions.example.jsonl")
    report = evaluate(pipeline, questions, k=3)
    assert report["questions"] == 4
    assert report["hit_rate"] == 1.0, report["misses"]
