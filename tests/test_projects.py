"""Multi-project layout: docs_dir/<project>/... with shared folders."""

import shutil
from pathlib import Path

import pytest

from rag_mcp.chunking import project_of
from rag_mcp.config import PROJECT_ROOT, Settings
from rag_mcp.pipeline import Pipeline

EXAMPLES = PROJECT_ROOT / "examples" / "docs"


@pytest.fixture
def multi(tmp_path: Path):
    docs = tmp_path / "docs"
    (docs / "shop-backend").mkdir(parents=True)
    (docs / "mobile-app").mkdir()
    (docs / "common").mkdir()
    shutil.copy(EXAMPLES / "deploy-runbook.md", docs / "shop-backend" / "deploy-runbook.md")
    (docs / "shop-backend" / "payments.md").write_text(
        "# Payments\n\nRefunds are processed through Stripe within 5 business days.", encoding="utf-8"
    )
    (docs / "mobile-app" / "release.md").write_text(
        "# Mobile release\n\nTestFlight builds are promoted to the App Store on Tuesdays. Refunds are handled by Apple.",
        encoding="utf-8",
    )
    shutil.copy(EXAMPLES / "onboarding.md", docs / "common" / "onboarding.md")
    (docs / "root-note.md").write_text("# Root note\n\nA file with no project folder.", encoding="utf-8")
    settings = Settings(
        docs_dir=docs, index_dir=tmp_path / "index", embed_provider="hash", qdrant_url=":memory:",
        chunk_size=400, chunk_overlap=50, _env_file=None,
    )
    pipe = Pipeline(settings)
    pipe.ingest()
    workspaces = tmp_path / "code"
    (workspaces / "shop-backend" / "src" / "api").mkdir(parents=True)
    (workspaces / "unrelated").mkdir(parents=True)
    return pipe, workspaces


def test_project_of():
    assert project_of("shop-backend/specs/api.pdf") == "shop-backend"
    assert project_of("notes.md") == ""


def test_projects_listed(multi):
    pipe, _ = multi
    assert pipe.store.projects() == ["common", "mobile-app", "shop-backend"]
    assert pipe.status()["projects"] == ["common", "mobile-app", "shop-backend"]


def test_detect_project_walks_up_from_subfolder(multi):
    pipe, ws = multi
    assert pipe.detect_project([str(ws / "shop-backend" / "src" / "api")]) == "shop-backend"
    assert pipe.detect_project([str(ws / "shop-backend")]) == "shop-backend"
    assert pipe.detect_project([str(ws / "unrelated")]) is None
    assert pipe.detect_project([]) is None


def test_detect_project_override(multi):
    pipe, ws = multi
    pipe.settings.project = "mobile-app"
    assert pipe.detect_project([str(ws / "shop-backend")]) == "mobile-app"


def test_scope_includes_shared_folders_that_exist(multi):
    pipe, _ = multi
    assert pipe.scope_for("shop-backend") == ["shop-backend/", "common/"]
    assert pipe.scope_for("common") == ["common/"]
    assert pipe.scope_for(None) is None


def test_scoped_search_excludes_other_projects(multi):
    pipe, _ = multi
    scoped = pipe.search("how are refunds handled", top_k=5, source_prefixes=pipe.scope_for("shop-backend"))
    assert scoped and all(r["project"] in ("shop-backend", "common") for r in scoped)
    assert scoped[0]["source"] == "shop-backend/payments.md"

    everything = pipe.search("how are refunds handled", top_k=5)
    assert {r["project"] for r in everything} >= {"shop-backend", "mobile-app"}

    mobile = pipe.search("refunds", top_k=5, source_prefixes=["mobile-app/"])
    assert mobile and all(r["project"] == "mobile-app" for r in mobile)


def test_fine_grained_prefix_still_works(multi):
    pipe, _ = multi
    res = pipe.search("rollback", top_k=5, source_prefixes=["shop-backend/deploy"])
    assert res and all(r["source"].startswith("shop-backend/deploy") for r in res)
