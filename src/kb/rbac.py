"""Users, groups and permission checks for writes.

Reads are enforced by row-level security (sql/004_security.sql); writes go
through the trusted owner connection, so every write path must call a check here.
"""

from collections.abc import Sequence

import psycopg


class PermissionDenied(Exception):
    pass


def upsert_user(
    conn: psycopg.Connection,
    email: str,
    display_name: str,
    is_admin: bool = False,
    *,
    country: str | None = None,
    location: str | None = None,
    department: str | None = None,
    position: str | None = None,
) -> int:
    return conn.execute(
        """
        INSERT INTO kb.users (email, display_name, is_admin, country, location, department, position)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (email) DO UPDATE SET
            display_name = EXCLUDED.display_name,
            is_admin = EXCLUDED.is_admin,
            country = EXCLUDED.country,
            location = EXCLUDED.location,
            department = EXCLUDED.department,
            position = EXCLUDED.position
        RETURNING id
        """,
        (email.strip().lower(), display_name, is_admin, country and country.upper(), location, department, position),
    ).fetchone()[0]


def upsert_group(conn: psycopg.Connection, name: str) -> int:
    return conn.execute(
        "INSERT INTO kb.groups (name) VALUES (%s) ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name RETURNING id",
        (name,),
    ).fetchone()[0]


def add_member(conn: psycopg.Connection, group_id: int, user_id: int, role: str = "member") -> None:
    conn.execute(
        """
        INSERT INTO kb.group_members (group_id, user_id, role) VALUES (%s, %s, %s)
        ON CONFLICT (group_id, user_id) DO UPDATE SET role = EXCLUDED.role
        """,
        (group_id, user_id, role),
    )


def user_id_by_email(conn: psycopg.Connection, email: str) -> int:
    row = conn.execute("SELECT id FROM kb.users WHERE email = %s", (email.strip().lower(),)).fetchone()
    if row is None:
        raise LookupError(f"unknown user: {email}")
    return row[0]


def require_user(conn: psycopg.Connection, user_id: int) -> None:
    if conn.execute("SELECT 1 FROM kb.users WHERE id = %s", (user_id,)).fetchone() is None:
        raise PermissionDenied("unknown user")


def resolve_groups(conn: psycopg.Connection, names: Sequence[str]) -> list[int]:
    wanted = set(names)
    rows = conn.execute("SELECT id, name FROM kb.groups WHERE name = ANY(%s)", (list(wanted),)).fetchall()
    missing = wanted - {name for _, name in rows}
    if missing:
        raise LookupError(f"unknown groups: {', '.join(sorted(missing))}")
    return [gid for gid, _ in rows]


def is_admin(conn: psycopg.Connection, user_id: int) -> bool:
    row = conn.execute("SELECT is_admin FROM kb.users WHERE id = %s", (user_id,)).fetchone()
    return bool(row and row[0])


def check_can_share_with(conn: psycopg.Connection, user_id: int, group_ids: Sequence[int]) -> None:
    """Users may only share documents with groups they belong to."""
    if not group_ids or is_admin(conn, user_id):
        return
    count = conn.execute(
        "SELECT count(*) FROM kb.group_members WHERE user_id = %s AND group_id = ANY(%s)",
        (user_id, list(set(group_ids))),
    ).fetchone()[0]
    if count != len(set(group_ids)):
        raise PermissionDenied("you can only share documents with groups you belong to")


def check_can_replace(conn: psycopg.Connection, user_id: int, doc_id: int) -> None:
    """Only the original uploader, a manager of one of its groups, or an admin may overwrite a document."""
    allowed = conn.execute(
        """
        SELECT EXISTS (SELECT 1 FROM kb.users WHERE id = %(uid)s AND is_admin)
            OR EXISTS (SELECT 1 FROM kb.documents WHERE id = %(doc)s AND uploaded_by = %(uid)s)
            OR EXISTS (
                SELECT 1 FROM kb.document_groups dg
                JOIN kb.group_members gm ON gm.group_id = dg.group_id
                WHERE dg.doc_id = %(doc)s AND gm.user_id = %(uid)s AND gm.role = 'manager'
            )
        """,
        {"uid": user_id, "doc": doc_id},
    ).fetchone()[0]
    if not allowed:
        raise PermissionDenied("you may not replace this document")


def can_read(conn: psycopg.Connection, user_id: int, doc_id: int) -> bool:
    return conn.execute(
        "SELECT %s IN (SELECT kb.readable_document_ids(%s))", (doc_id, user_id)
    ).fetchone()[0]
