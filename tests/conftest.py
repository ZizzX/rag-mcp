import shutil
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
