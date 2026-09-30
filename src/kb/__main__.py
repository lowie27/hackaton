"""Admin/dev CLI: python -m kb <command>.

--as EMAIL picks the acting user. The CLI is a trusted local tool; the real
app gets the user id from its authentication layer.
"""

import argparse
import textwrap
from datetime import date
from pathlib import Path

from kb import experts, rbac
from kb.config import Settings
from kb.db import connect, init_schema
from kb.retriever import SearchFilters
from kb.service import KnowledgeBase

DEFAULT_SEED = Path(__file__).resolve().parents[2] / "data" / "sample" / "seed.json"
DEFAULT_EXPERTS = DEFAULT_SEED.with_name("experts.json")


def print_hits(hits) -> None:
    if not hits:
        print("no results")
    for rank, hit in enumerate(hits, start=1):
        updated = hit.updated_at.date() if hit.updated_at else "unknown"
        why = []
        if hit.bm25_rank:
            why.append(f"bm25 #{hit.bm25_rank} ({hit.bm25_score:.2f}: {', '.join(hit.matched_terms)})")
        if hit.vector_rank:
            why.append(f"vector #{hit.vector_rank} (cos {hit.vector_score:.2f})")
        print(f"{rank}. [{hit.score:.4f}] {hit.title}  ({hit.external_id}#{hit.chunk_ord})")
        print(f"   owner={hit.owner or '-'} country={hit.country or 'all'} updated={updated} groups={','.join(hit.groups) or 'private'}")
        scope = [f"dept={hit.department}" if hit.department else None, f"site={hit.location}" if hit.location else None,
                 f"lang={hit.language}" if hit.language else None, f"tags={','.join(hit.tags)}" if hit.tags else None]
        if any(scope):
            print(f"   {' '.join(x for x in scope if x)}")
        print(f"   why: {'; '.join(why)}")
        if hit.reasons:
            print(f"   trust: {'; '.join(hit.reasons)}")
        if hit.warnings:
            print(f"   CAREFUL: {'; '.join(hit.warnings)}")
        print(textwrap.indent(textwrap.shorten(hit.text, 240), "   "))


def main() -> None:
    parser = argparse.ArgumentParser(prog="kb")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create schema, views, RLS policies (and pgvector if enabled)")
    seed = sub.add_parser("seed", help="load demo users, groups and documents")
    seed.add_argument("spec", type=Path, nargs="?", default=DEFAULT_SEED)
    sub.add_parser("embed", help="backfill missing embeddings (after enabling vectors)")

    user = sub.add_parser("user", help="create or update a user")
    user.add_argument("email")
    user.add_argument("name")
    user.add_argument("--admin", action="store_true")
    user.add_argument("--country")
    user.add_argument("--location")
    user.add_argument("--department")
    user.add_argument("--position")

    group = sub.add_parser("group", help="create a group, optionally adding a member")
    group.add_argument("name")
    group.add_argument("--member", metavar="EMAIL")
    group.add_argument("--manager", action="store_true", help="add --member as manager")

    ingest = sub.add_parser("ingest", help="upload .md/.txt files as a user")
    ingest.add_argument("path", type=Path)
    ingest.add_argument("--as", dest="user", required=True, metavar="EMAIL")
    ingest.add_argument("--group", action="append", default=[], help="share with this group (repeatable)")

    search = sub.add_parser("search", help="search as a user")
    search.add_argument("query")
    search.add_argument("--as", dest="user", required=True, metavar="EMAIL")
    search.add_argument("-k", "--top-k", type=int, default=5)
    search.add_argument("--mode", choices=["bm25", "vector", "hybrid"])
    search.add_argument("--country", help="only this country (plus documents for all countries)")
    search.add_argument("--department", help="only this department (plus documents for all departments)")
    search.add_argument("--source")
    search.add_argument("--language")
    search.add_argument("--tag", action="append", default=[], help="required tag (repeatable)")
    search.add_argument("--valid-on", type=date.fromisoformat, metavar="YYYY-MM-DD",
                        help="only documents in force on that date")

    who = sub.add_parser("experts", help="find people to ask, ranked like documents")
    who.add_argument("query")
    who.add_argument("--as", dest="user", required=True, metavar="EMAIL")

    notes = sub.add_parser("notifications", help="list a user's notifications")
    notes.add_argument("--as", dest="user", required=True, metavar="EMAIL")
    notes.add_argument("--unread", action="store_true")

    args = parser.parse_args()
    settings = Settings.from_env()

    with connect() as conn:
        if args.command == "init":
            init_schema(conn, settings)
            experts.ensure_database(conn)
            with experts.connect() as xconn:
                experts.init_schema(xconn)
            print(f"schema ready (vector search {'on' if settings.vector_enabled else 'off'}, experts database ready)")
        elif args.command == "user":
            uid = rbac.upsert_user(conn, args.email, args.name, args.admin, country=args.country,
                                   location=args.location, department=args.department, position=args.position)
            print(f"user id {uid}")
        elif args.command == "group":
            group_id = rbac.upsert_group(conn, args.name)
            if args.member:
                role = "manager" if args.manager else "member"
                rbac.add_member(conn, group_id, rbac.user_id_by_email(conn, args.member), role)
            print(f"group id {group_id}")
        elif args.command == "notifications":
            for n in KnowledgeBase(conn, settings).notifications(rbac.user_id_by_email(conn, args.user), args.unread):
                status = "    " if n.read_at else "NEW "
                print(f"{status}#{n.id} {n.created_at:%Y-%m-%d %H:%M}  {n.message}")
        else:
            kb = KnowledgeBase(conn, settings)
            if args.command == "seed":
                kb.seed(args.spec)
                with experts.connect() as xconn:
                    n = experts.seed(xconn, DEFAULT_EXPERTS, kb.embedder)
                print(f"seeded (and {n} experts)")
            elif args.command == "embed":
                print(f"embedded {kb.embed_missing()} chunks")
            elif args.command == "ingest":
                results = kb.upload_directory(rbac.user_id_by_email(conn, args.user), args.path, args.group)
                print(f"ingested {len(results)} documents")
                for result in results:
                    for s in result.similar:
                        print(f"  doc {result.doc_id} is similar to {s.title or 'a document you cannot see'}")
            elif args.command == "experts":
                uid = rbac.user_id_by_email(conn, args.user)
                hits = kb.search(uid, args.query, 5)
                with experts.connect() as xconn:
                    people = experts.find_experts(xconn, settings, args.query, kb.retriever._user_context(uid), hits,
                                                  embedder=kb.embedder, reranker=kb.retriever.reranker)
                for rank, p in enumerate(people, start=1):
                    print(f"{rank}. [{p.score:.3f}] {p.name}, {p.position} ({p.country or 'group'}) <{p.email}>")
                    print(f"   trust: {'; '.join(p.reasons) or '-'}")
                    if p.warnings:
                        print(f"   CAREFUL: {'; '.join(p.warnings)}")
            elif args.command == "search":
                filters = SearchFilters(args.country, args.department, args.source, args.language, args.tag, args.valid_on)
                print_hits(kb.search(rbac.user_id_by_email(conn, args.user), args.query, args.top_k, args.mode, filters))


if __name__ == "__main__":
    main()
