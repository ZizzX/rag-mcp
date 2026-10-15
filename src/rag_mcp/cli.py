"""Command line: rag-mcp ingest | search | serve | eval | status"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import PROJECT_ROOT
from .pipeline import Pipeline, configure_logging


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="rag-mcp", description="Local hybrid RAG over Markdown/PDF with an MCP server.")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("ingest", help="index new/changed documents (incremental)")
    i.add_argument("--rebuild", action="store_true", help="drop the index and re-embed everything")

    s = sub.add_parser("search", help="query the index from the terminal")
    s.add_argument("query")
    s.add_argument("-k", "--top-k", type=int, default=None)
    s.add_argument("--source", action="append", default=None, metavar="PREFIX",
                   help="restrict to sources starting with PREFIX (repeatable)")
    s.add_argument("--project", default=None, help="scope to this project folder plus shared folders")
    s.add_argument("--json", action="store_true")

    sv = sub.add_parser("serve", help="run the MCP server on stdio (re-indexes on file changes unless --no-watch)")
    sv.add_argument("--no-watch", action="store_true", help="do not watch docs_dir for changes")

    sub.add_parser("watch", help="index now, then re-index whenever documents change (Ctrl+C to stop)")
    sub.add_parser("status", help="show index location, embedder and counts")

    e = sub.add_parser("eval", help="retrieval metrics against a golden question set")
    e.add_argument("questions", nargs="?", default=str(PROJECT_ROOT / "eval" / "questions.jsonl"))
    e.add_argument("-k", "--top-k", type=int, default=5)

    args = p.parse_args(argv)
    configure_logging(args.verbose)

    if args.cmd == "serve":
        from .server import run

        run(watch=False if args.no_watch else None)
        return 0

    pipe = Pipeline()
    if args.cmd == "watch":
        import time

        watcher = pipe.start_watcher()
        print(f"watching {pipe.settings.docs_dir} (Ctrl+C to stop)", file=sys.stderr)
        try:
            while watcher.thread.is_alive():
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            pipe.stop_watcher()
        return 1 if watcher.last_error else 0

    if args.cmd == "ingest":
        r = pipe.ingest(rebuild=args.rebuild)
        print(
            f"added={r.added} updated={r.updated} removed={r.removed} unchanged={r.unchanged} "
            f"chunks_written={r.chunks} total_chunks={pipe.store.count()}"
        )
        for err in r.errors:
            print("ERROR", err, file=sys.stderr)
        return 1 if r.errors else 0

    if args.cmd == "search":
        prefixes = args.source or pipe.scope_for(args.project)
        results = pipe.search(args.query, top_k=args.top_k, source_prefixes=prefixes)
        if args.json:
            print(json.dumps(results, ensure_ascii=False, indent=1))
        else:
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
