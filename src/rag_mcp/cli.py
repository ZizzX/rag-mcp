"""Command line: rag-mcp ingest | search | serve | eval | status | add | remove | projects"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .config import PROJECT_ROOT
from .pipeline import IngestReport, Pipeline, configure_logging
from .projects import ProjectError, RegistryError
from .store import IndexBusy


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="rag-mcp", description="Local hybrid RAG over Markdown/PDF with an MCP server.")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("ingest", help="index new/changed documents (incremental)")
    i.add_argument("--rebuild", action="store_true", help="drop the index and re-embed everything")

    s = sub.add_parser("search", help="query the index from the terminal")
    s.add_argument("query")
    s.add_argument("-k", "--top-k", type=int, default=None)
    where = s.add_mutually_exclusive_group()
    where.add_argument("--source", action="append", default=None, metavar="PREFIX",
                       help="restrict to sources starting with PREFIX (repeatable)")
    where.add_argument("--project", default=None,
                       help="scope to this project plus shared folders (default: the project of the current folder)")
    where.add_argument("--all", action="store_true", help="search every project")
    s.add_argument("--json", action="store_true")

    sub.add_parser("serve", help="run the MCP server on stdio")
    sub.add_parser("status", help="show index location, embedder and counts")

    a = sub.add_parser("add", help="connect a project folder: index its documents in place")
    a.add_argument("path", nargs="?", default=None,
                   help="folder to connect (default: the git repository around the current folder)")
    a.add_argument("--name", default=None, help="project name (default: the repository's folder name)")
    a.add_argument("--force", action="store_true", help="allow a home folder or more than 5000 documents")

    r = sub.add_parser("remove", help="disconnect a project and drop its documents from the index")
    r.add_argument("name")

    pr = sub.add_parser("projects", help="list connected projects and project folders of docs_dir")
    pr.add_argument("--json", action="store_true")

    e = sub.add_parser("eval", help="retrieval metrics against a golden question set")
    e.add_argument("questions", nargs="?", default=str(PROJECT_ROOT / "eval" / "questions.jsonl"))
    e.add_argument("-k", "--top-k", type=int, default=5)

    args = p.parse_args(argv)
    configure_logging(args.verbose)

    if args.cmd == "serve":
        from .server import run

        run()
        return 0

    pipe = Pipeline()
    try:
        return _run(pipe, args)
    except (ProjectError, RegistryError) as e:
        print(e, file=sys.stderr)
        return 2
    except IndexBusy as e:
        print(e, file=sys.stderr)
        return 1


def _print_report(r: IngestReport, pipe: Pipeline) -> int:
    print(
        f"added={r.added} updated={r.updated} removed={r.removed} unchanged={r.unchanged} "
        f"chunks_written={r.chunks} total_chunks={pipe.store.count()}"
    )
    for err in r.errors:
        print("ERROR", err, file=sys.stderr)
    return 1 if r.errors else 0


def _run(pipe: Pipeline, args: argparse.Namespace) -> int:
    if args.cmd == "ingest":
        return _print_report(pipe.ingest(rebuild=args.rebuild), pipe)

    if args.cmd == "add":
        project, found = pipe.add_project(args.path, args.name, cwd=Path.cwd(), force=args.force)
        print(f"connected {project.name}: {project.path} (origin {project.origin or 'none'}), {found} documents found")
        code = _print_report(pipe.ingest(), pipe)
        print(f'search it: rag-mcp search "your question" --project {project.name}')
        return code

    if args.cmd == "remove":
        project = pipe.remove_project(args.name)
        print(f"removed {project.name} ({project.path})")
        return _print_report(pipe.ingest(), pipe)

    if args.cmd == "projects":
        rows = pipe.projects_info()
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=1))
        elif not rows:
            print("No projects yet. Connect one with `rag-mcp add` inside its folder.")
        else:
            here = pipe.detect_project([os.getcwd()]).project
            width = max(len(r["name"]) for r in rows)
            for r in rows:
                gone = "" if r["exists"] else "  (path not found)"
                gone += "  <- here" if r["name"] == here else ""  # ASCII: a cp1252 pipe on Windows has no arrow
                print(f"{r['name']:<{width}}  {r['kind']:<9}  {r['documents']:>5}  {r['chunks']:>6}  {r['path']}{gone}")
        return 0

    if args.cmd == "search":
        if args.source:
            prefixes, scope = args.source, " ".join(args.source)
        elif args.all:
            prefixes, scope = None, "all projects"
        else:
            found = pipe.detect_project([os.getcwd()], explicit=args.project)
            prefixes = pipe.scope_for(found.project)
            if prefixes is None:
                scope = f"all projects ({found.hint})"
            else:
                docs_from = f", documents from {found.docs_from}" if found.docs_from else ""
                scope = f"{' '.join(prefixes)} (matched by {found.matched_by}{docs_from})"
        results = pipe.search(args.query, top_k=args.top_k, source_prefixes=prefixes)
        if args.json:
            print(json.dumps(results, ensure_ascii=False, indent=1))
        else:
            print(f"scope: {scope}")
            for n, r in enumerate(results, 1):
                where = r["source"] + (f" p.{r['page']}" if r["page"] else "")
                head = f" [{r['heading']}]" if r["heading"] else ""
                print(f"{n}. {where}{head}  score={r['score']}  via={','.join(r['matched'])}")
                print("   " + r["text"][:300].replace("\n", " ") + ("..." if len(r["text"]) > 300 else ""))
        return 0

    if args.cmd == "status":
        print(json.dumps(pipe.status(), ensure_ascii=False, indent=1))
        return 0

    if args.cmd == "eval":
        from .evaluate import evaluate, load_questions

        path = Path(args.questions)
        if not path.exists():
            print(f"No question set at {path}. Copy eval/questions.example.jsonl and edit it.", file=sys.stderr)
            return 2
        report = evaluate(pipe, load_questions(path), k=args.top_k)
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
