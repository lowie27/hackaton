"""CLI: python -m kb {init,ingest,search}."""

import argparse
import textwrap
from pathlib import Path

from kb.db import connect, init_schema
from kb.ingest import ingest_directory
from kb.retriever import BM25Retriever


def main() -> None:
    parser = argparse.ArgumentParser(prog="kb")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="create schema, views and the bm25_search function")
    ingest = sub.add_parser("ingest", help="load .md/.txt files and rebuild the index")
    ingest.add_argument("path", type=Path)
    search = sub.add_parser("search", help="run a BM25 query")
    search.add_argument("query")
    search.add_argument("-k", "--top-k", type=int, default=5)
    args = parser.parse_args()

    with connect() as conn:
        if args.command == "init":
            init_schema(conn)
            print("schema ready")
        elif args.command == "ingest":
            print(f"ingested {ingest_directory(conn, args.path)} documents")
        elif args.command == "search":
            hits = BM25Retriever(conn).search(args.query, args.top_k)
            if not hits:
                print("no results")
            for rank, hit in enumerate(hits, start=1):
                updated = hit.updated_at.date() if hit.updated_at else "unknown"
                print(f"{rank}. [{hit.score:.3f}] {hit.title}  ({hit.external_id}#{hit.chunk_ord})")
                print(f"   owner={hit.owner or '-'} country={hit.country or 'all'} updated={updated}")
                print(f"   matched: {', '.join(hit.matched_terms)}")
                print(textwrap.indent(textwrap.shorten(hit.text, 240), "   "))


if __name__ == "__main__":
    main()
