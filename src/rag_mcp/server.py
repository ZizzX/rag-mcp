"""MCP server exposing the index to Claude Code, Claude Desktop, Cursor, etc.

Transport is stdio, so never print to stdout from this process.
"""

from __future__ import annotations

import json

from mcp.server.mcpserver import MCPServer

from .pipeline import Pipeline, configure_logging

INSTRUCTIONS = (
    "Local knowledge base built from the user's Markdown and PDF documents. "
    "Call `search` with a natural-language question to get the most relevant passages "
    "with their source file, heading and page. Cite the `source` (and `page` when present) "
    "in answers. Use `fetch_document` when a passage is cut off and you need the full file. "
    "Use `list_sources` to see what is indexed."
)

server = MCPServer("rag-mcp", instructions=INSTRUCTIONS)
_pipeline: Pipeline | None = None


def pipeline() -> Pipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = Pipeline()
    return _pipeline


@server.tool()
def search(query: str, top_k: int = 6, source_prefix: str | None = None) -> str:
    """Hybrid search (vector + BM25) over the indexed documents.

    Args:
        query: Natural-language question or keywords.
        top_k: Number of passages to return (1-20).
        source_prefix: Optional path prefix to restrict results, e.g. "handbook/" or "specs/api.pdf".
    """
    top_k = max(1, min(int(top_k), 20))
    results = pipeline().search(query, top_k=top_k, source_prefix=source_prefix or None)
    slim = [
        {
            "source": r["source"],
            "title": r["title"],
            "heading": r["heading"] or None,
            "page": r["page"],
            "doc_id": r["doc_id"],
            "chunk_id": r["chunk_id"],
            "score": r["score"],
            "matched": r["matched"],
            "text": r["text"],
        }
        for r in results
    ]
    return json.dumps(slim, ensure_ascii=False, indent=1)


@server.tool()
def fetch_document(doc_id: str) -> str:
    """Return the full text of one indexed document by its doc_id (from search results)."""
    doc = pipeline().document(doc_id)
    if doc is None:
        return json.dumps({"error": f"unknown doc_id {doc_id}"})
    return json.dumps(doc, ensure_ascii=False)


@server.tool()
def list_sources() -> str:
    """List indexed files with titles and chunk counts."""
    return json.dumps(pipeline().store.sources(), ensure_ascii=False, indent=1)


@server.tool()
def index_status() -> str:
    """Show where documents and the index live, which embedder is used, and counts."""
    return json.dumps(pipeline().status(), ensure_ascii=False, indent=1)


def run() -> None:
    configure_logging()
    server.run(transport="stdio")


if __name__ == "__main__":
    run()
