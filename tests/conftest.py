import shutil
import subprocess
from pathlib import Path

import pytest

from rag_mcp.config import PROJECT_ROOT, Settings
from rag_mcp.pipeline import Pipeline

EXAMPLES = PROJECT_ROOT / "examples" / "docs"


@pytest.fixture
def workspace(tmp_path: Path):
    docs = tmp_path / "docs"
    shutil.copytree(EXAMPLES, docs)
    return docs, tmp_path / "index"


@pytest.fixture
def pipeline(workspace):
    docs, index = workspace
    settings = Settings(
        docs_dir=docs,
        index_dir=index,
        embed_provider="hash",
        qdrant_url=":memory:",
        chunk_size=400,
        chunk_overlap=50,
        _env_file=None,
    )
    return Pipeline(settings)


@pytest.fixture
def make_repo():
    """Factory for a throwaway git repository: make_repo(root, {"docs/a.md": "..."}, origin=...)."""
    if shutil.which("git") is None:
        pytest.skip("git is not installed")

    def make(root: Path, files: dict[str, str], origin: str | None = None, commit: bool = True) -> Path:
        root.mkdir(parents=True, exist_ok=True)
        git = lambda *a: subprocess.run(["git", "-C", str(root), *a], check=True, capture_output=True)  # noqa: E731
        git("init", "-q")
        for name, text in files.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(text, encoding="utf-8")
        if origin:
            git("remote", "add", "origin", origin)
        if commit:
            git("add", "-A")
            git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
        return root

    return make


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    (tmp_path / "docs").mkdir()
    monkeypatch.setenv("RAG_DOCS_DIR", str(tmp_path / "docs"))
    monkeypatch.setenv("RAG_INDEX_DIR", str(tmp_path / "index"))
    monkeypatch.setenv("RAG_EMBED_PROVIDER", "hash")
    monkeypatch.setenv("RAG_QDRANT_URL", "")
