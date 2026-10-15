import pytest

from rag_mcp.embeddings import HashEmbedder
from rag_mcp.store import HybridStore


def test_locked_index_gives_friendly_error(tmp_path):
    first = HybridStore(tmp_path, "docs", HashEmbedder())
    with pytest.raises(SystemExit, match="locked by another rag-mcp process"):
        HybridStore(tmp_path, "docs", HashEmbedder())
    first.client.close()
