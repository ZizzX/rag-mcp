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
    s.add_argument("--source", default=None, help="restrict to sources starting with this prefix")
    s.add_argument("--json", action="store_true")

    sub.add_parser("serve", help="run the MCP server on stdio")
    sub.add_parser("status", help="show index location, embedder and counts")

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
        results = pipe.search(args.query, top_k=args.top_k, source_prefix=args.source)
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
