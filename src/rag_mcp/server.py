"""MCP server exposing the index to Claude Code, Claude Desktop, Cursor, etc.

Transport is stdio, so never print to stdout from this process.

Multi-project scoping: documents live in docs_dir/<project>/..., where <project>
is the name of the project folder on disk. On each `search` the server asks the
client for its workspace roots (Claude Code sends the folder it was started in),
maps them to an indexed project, and scopes results to that project plus the
shared folders (RAG_SHARED_PROJECTS, default "common"). Pass `all_projects=true`
or an explicit `source_prefix` to override.
"""

from __future__ import annotations

import json
import logging
import os
import warnings
from urllib.parse import unquote, urlparse

from mcp.server.mcpserver import Context, MCPServer

from .pipeline import Pipeline, configure_logging

log = logging.getLogger("rag_mcp.server")

INSTRUCTIONS = (
    "Local knowledge base built from the user's Markdown and PDF documents, organised as one "
    "folder per project plus shared folders. `search` automatically scopes to the project the "
    "client is currently working in (based on its workspace folder) plus shared docs; the "
    "response says which scope was used. Cite `source` (and `page` when present) in answers. "
    "Use `fetch_document` when a passage is cut off. Use `index_status` to see indexed projects "
    "and the detected one; pass `all_projects=true` to search everything."
)

server = MCPServer("rag-mcp", instructions=INSTRUCTIONS)
_pipeline: Pipeline | None = None


def pipeline() -> Pipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = Pipeline()
    return _pipeline


def _root_to_path(uri: str) -> str | None:
    parsed = urlparse(uri)
    if parsed.scheme not in ("file", ""):
        return None
    path = unquote(parsed.path)
    # file:///C:/Users/x -> C:/Users/x on Windows
    if len(path) > 2 and path[0] == "/" and path[2] == ":":
        path = path[1:]
    return path or None


async def workspace_paths(ctx: Context | None) -> list[str]:
    """Client workspace folders, best source first.

    1. MCP roots: Claude Code answers with the folder the session was started in.
       (Deprecated in the 2026-07-28 spec but still served; kept while clients send it.)
    2. CLAUDE_PROJECT_DIR, which Claude Code sets in the server's environment.
    3. The server's own cwd (unreliable: some hosts launch servers from $HOME).
    """
    paths: list[str] = []
    if ctx is not None:
        try:
            caps = ctx.client_capabilities
            if caps is not None and caps.roots is not None:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    result = await ctx.session.list_roots()
                paths = [p for r in result.roots if (p := _root_to_path(str(r.uri)))]
        except Exception as e:  # client refused or protocol hiccup: fall back silently
            log.debug("list_roots failed: %s", e)
    if not paths and os.environ.get("CLAUDE_PROJECT_DIR"):
        paths = [os.environ["CLAUDE_PROJECT_DIR"]]
    if not paths:
        paths = [os.getcwd()]
    return paths


async def resolve_scope(
    ctx: Context | None, source_prefix: str | None, all_projects: bool, project: str | None = None
) -> dict:
    pipe = pipeline()
    if source_prefix:
        return {"mode": "prefix", "prefixes": [source_prefix], "project": None}
    if all_projects:
        return {"mode": "all", "prefixes": None, "project": None}
    if project:
        project = project.strip("/")
        if project not in pipe.store.projects():
            return {"mode": "all", "prefixes": None, "project": None,
                    "note": f"project '{project}' is not indexed; searched everything",
                    "projects": pipe.store.projects()}
        return {"mode": "project", "prefixes": pipe.scope_for(project), "project": project}
    paths = await workspace_paths(ctx)
    project = pipe.detect_project(paths)
    prefixes = pipe.scope_for(project)
    if project is None:
        return {"mode": "all", "prefixes": None, "project": None, "workspace": paths,
                "note": "workspace folder does not match any indexed project; searched everything"}
    return {"mode": "project", "prefixes": prefixes, "project": project, "workspace": paths}


@server.tool()
async def search(
    query: str,
    top_k: int = 6,
    project: str | None = None,
    source_prefix: str | None = None,
    all_projects: bool = False,
    ctx: Context = None,  # type: ignore[assignment]
) -> str:
    """Hybrid search (vector + BM25) over the indexed documents, scoped to the current project.

    Args:
        query: Natural-language question or keywords.
        top_k: Number of passages to return (1-20).
        project: Project folder to search (plus shared folders). Omit to auto-detect from the workspace.
        source_prefix: Restrict to sources starting with this path, e.g. "shop-backend/" or "common/policies.pdf".
            Overrides both `project` and auto-detection.
        all_projects: Search every indexed project instead of only the current one plus shared folders.
    """
    top_k = max(1, min(int(top_k), 20))
    scope = await resolve_scope(ctx, source_prefix or None, all_projects, project)
    results = pipeline().search(query, top_k=top_k, source_prefixes=scope["prefixes"])
    slim = [
        {
            "source": r["source"],
            "project": r["project"] or None,
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
    return json.dumps({"scope": scope, "results": slim}, ensure_ascii=False, indent=1)


@server.tool()
def fetch_document(doc_id: str) -> str:
    """Return the full text of one indexed document by its doc_id (from search results)."""
    doc = pipeline().document(doc_id)
    if doc is None:
        return json.dumps({"error": f"unknown doc_id {doc_id}"})
    return json.dumps(doc, ensure_ascii=False)


@server.tool()
def list_sources(project: str | None = None) -> str:
    """List indexed files with titles and chunk counts, optionally only for one project folder."""
    sources = pipeline().store.sources()
    if project:
        sources = {k: v for k, v in sources.items() if k.startswith(project.rstrip("/") + "/")}
    return json.dumps(sources, ensure_ascii=False, indent=1)


@server.tool()
async def index_status(ctx: Context = None) -> str:  # type: ignore[assignment]
    """Show indexed projects, the project detected for the current workspace, embedder and counts."""
    pipe = pipeline()
    status = pipe.status()
    paths = await workspace_paths(ctx)
    status["workspace"] = paths
    status["detected_project"] = pipe.detect_project(paths)
    return json.dumps(status, ensure_ascii=False, indent=1)


def run() -> None:
    configure_logging()
    server.run(transport="stdio")


if __name__ == "__main__":
    run()
