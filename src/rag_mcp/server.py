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

import functools
import inspect
import json
import logging
import os
import threading
import warnings
from dataclasses import asdict
from urllib.parse import unquote, urlparse

import httpx
from mcp.server.mcpserver import Context, MCPServer

from .pipeline import Pipeline, configure_logging
from .store import IndexBusy

log = logging.getLogger("rag_mcp.server")

INSTRUCTIONS = (
    "Local knowledge base built from the user's Markdown and PDF documents, organised as one "
    "folder per project plus shared folders. `search` automatically scopes to the project the "
    "client is currently working in (based on its workspace folder) plus shared docs; the "
    "response says which scope was used. Cite `source` (and `page` when present) in answers. "
    "Use `fetch_document` when a passage is cut off. Use `index_status` to see indexed projects "
    "and the detected one; pass `all_projects=true` to search everything. Call `reindex` after "
    "the user adds or edits documents."
)

server = MCPServer("rag-mcp", instructions=INSTRUCTIONS)
_pipeline: Pipeline | None = None
_pipeline_lock = threading.Lock()


def pipeline() -> Pipeline:
    global _pipeline
    with _pipeline_lock:
        if _pipeline is None:
            _pipeline = Pipeline()
        return _pipeline


def error_text(e: Exception) -> str:
    """Turn an exception into a message the user can act on (MCP clients show bare errors otherwise)."""
    return json.dumps({"error": error_message(e)}, ensure_ascii=False)


def error_message(e: Exception) -> str:
    s = _pipeline.settings if _pipeline is not None else None
    if isinstance(e, httpx.ConnectError | httpx.TimeoutException):
        where = s.ollama_url if s and s.embed_provider == "ollama" else "the embedding server"
        msg = f"Cannot reach the embedding model at {where} ({e})."
        if s is None or s.embed_provider == "ollama":
            msg += " Start Ollama (`ollama serve`, or open the Ollama app) and try again."
    elif isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 404 and s and s.embed_provider == "ollama":
        msg = f"Ollama does not have the model {s.embed_model}. Run `ollama pull {s.embed_model}`."
    elif isinstance(e, httpx.HTTPStatusError):
        msg = f"The embedding server answered {e.response.status_code}: {e.response.text[:200]}"
    else:
        msg = str(e) or type(e).__name__
    return msg


def readable_errors(fn):
    """Return errors as tool output instead of raising: clients show raised errors as just "Error executing tool"."""
    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            try:
                return await fn(*args, **kwargs)
            except Exception as e:
                log.warning("%s failed: %s", fn.__name__, e)
                return error_text(e)

        return wrapper

    @functools.wraps(fn)
    def sync_wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            log.warning("%s failed: %s", fn.__name__, e)
            return error_text(e)

    return sync_wrapper


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
@readable_errors
async def search(
    query: str,
    top_k: int = 6,
    project: str | None = None,
    source_prefix: str | None = None,
    all_projects: bool = False,
    ctx: Context = None,  # type: ignore[assignment]
) -> str:
    """Hybrid search (vector + BM25) over the indexed documents, scoped to the current project.
    If the embedding model is down, results come from BM25 only and the response has a `degraded` field saying why.

    Args:
        query: Natural-language question or keywords.
        top_k: Number of passages to return (1-20).
        project: Project folder to search (plus shared folders). Omit to auto-detect from the workspace.
        source_prefix: Restrict to sources starting with this path, e.g. "shop-backend/" or "common/policies.pdf".
            Overrides both `project` and auto-detection.
        all_projects: Search every indexed project instead of only the current one plus shared folders.
    """
    top_k = max(1, min(int(top_k), 20))
    problem = pipeline().index_problem()
    if problem:
        return json.dumps({"error": problem}, ensure_ascii=False)
    scope = await resolve_scope(ctx, source_prefix or None, all_projects, project)
    dense_errors: list[Exception] = []
    results = pipeline().search(query, top_k=top_k, source_prefixes=scope["prefixes"], dense_errors=dense_errors)
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
    out = {"scope": scope, "results": slim}
    if dense_errors:
        log.warning("search without vectors: %s", dense_errors[0])
        out["degraded"] = (
            f"dense off: {error_message(dense_errors[0])} Until then, results come from keyword (BM25) "
            "search only, so an empty or weak result does not mean the documents lack the answer."
        )
    return json.dumps(out, ensure_ascii=False, indent=1)


@server.tool()
@readable_errors
def fetch_document(doc_id: str) -> str:
    """Return the full text of one indexed document by its doc_id (from search results)."""
    doc = pipeline().document(doc_id)
    if doc is None:
        return json.dumps({"error": f"unknown doc_id {doc_id}"})
    return json.dumps(doc, ensure_ascii=False)


@server.tool()
@readable_errors
def list_sources(project: str | None = None) -> str:
    """List indexed files with titles and chunk counts, optionally only for one project folder."""
    sources = pipeline().store.sources()
    if project:
        sources = {k: v for k, v in sources.items() if k.startswith(project.rstrip("/") + "/")}
    return json.dumps(sources, ensure_ascii=False, indent=1)


@server.tool()
@readable_errors
async def index_status(ctx: Context = None) -> str:  # type: ignore[assignment]
    """Show indexed projects, the project detected for the current workspace, embedder and counts."""
    pipe = pipeline()
    status = pipe.status()
    paths = await workspace_paths(ctx)
    status["workspace"] = paths
    status["detected_project"] = pipe.detect_project(paths)
    return json.dumps(status, ensure_ascii=False, indent=1)


@server.tool()
@readable_errors
def reindex(rebuild: bool = False) -> str:
    """Index documents added, changed or removed since the last ingest. Searches keep working meanwhile.

    Args:
        rebuild: Drop the index and re-embed everything (needed after changing the embedding model).
    """
    pipe = pipeline()
    report = pipe.ingest(rebuild=rebuild)
    return json.dumps({**asdict(report), "total_chunks": pipe.store.count()}, ensure_ascii=False, indent=1)


def startup_ingest() -> None:
    """Bring the index up to date when documents changed while no server was running."""
    try:
        pipe = pipeline()
        if pipe.needs_ingest():
            r = pipe.ingest()
            log.info("startup ingest: added=%d updated=%d removed=%d errors=%d", r.added, r.updated, r.removed, len(r.errors))
    except IndexBusy:
        log.info("startup ingest skipped: another rag-mcp process is updating the index")
    except Exception as e:
        log.warning("startup ingest failed: %s", e)


def run() -> None:
    configure_logging()
    # In the background, so the client gets its tools immediately; searches see the result once it commits.
    threading.Thread(target=startup_ingest, name="startup-ingest", daemon=True).start()
    server.run(transport="stdio")


if __name__ == "__main__":
    run()
