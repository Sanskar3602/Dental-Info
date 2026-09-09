"""
Dental Info — data + auth layer.

Standard library only: sqlite3, hashlib, secrets. Nothing to pip install.

Why SQLite: it runs with zero setup, it is real SQL, and the schema is
written to port to Postgres later (see schema.sql). Note that a deployed
Vercel app CANNOT use this file — its filesystem is read-only and
ephemeral — so production will need Postgres. Everything DB-specific is
confined to this module to keep that swap small.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("DENTAL_INFO_DB", BASE_DIR / "dental_info.db"))
SCHEMA_PATH = BASE_DIR / "schema.sql"

# ---------------------------------------------------------------------------
# Permissions
#
# The user asked that, for now, every account have full access. Rather than
# stripping roles out of the model (CLAUDE.md is explicit that contributor
# must stay a distinct role), permissions are enforced through one function
# with a single override flag. Set PERMISSIVE_MODE = False -- or the env var
# DENTAL_INFO_STRICT=1 -- and real role enforcement resumes immediately.
# ---------------------------------------------------------------------------
PERMISSIVE_MODE = os.environ.get("DENTAL_INFO_STRICT", "") not in ("1", "true", "yes")

SESSION_TTL = timedelta(days=7)
PBKDF2_ITERATIONS = 240_000

JSON_FIELDS = ("complications", "tools", "resolution", "takeaways", "media")


# ── helpers ────────────────────────────────────────────────────────────────
def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def new_id() -> str:
    return str(uuid.uuid4())


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db() -> None:
    """Create tables if they do not exist. Safe to call repeatedly."""
    with connect() as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))


# ── passwords ──────────────────────────────────────────────────────────────
def hash_password(password: str, salt: str | None = None,
                  iterations: int = PBKDF2_ITERATIONS) -> tuple[str, str, int]:
    """pbkdf2-hmac-sha256. Returns (hash_hex, salt_hex, iterations)."""
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 bytes.fromhex(salt), iterations)
    return digest.hex(), salt, iterations


def verify_password(password: str, row) -> bool:
    expected = row["password_hash"]
    candidate, _, _ = hash_password(password, row["password_salt"],
                                    row["password_iterations"])
    # constant-time compare so a wrong password cannot be timed out char by char
    return secrets.compare_digest(candidate, expected)


# ── users ──────────────────────────────────────────────────────────────────
def create_user(conn, *, email, name, password, role="reader",
                credential=None, location=None, verification_status="unverified",
                license_number=None, license_board=None, license_country=None) -> str:
    pw_hash, salt, iters = hash_password(password)
    ts = now_iso()
    uid = new_id()
    verified_at = ts if verification_status == "verified" else None
    reverify_due = (
        (datetime.now(timezone.utc) + timedelta(days=365)).replace(microsecond=0).isoformat()
        if verification_status == "verified" else None
    )
    conn.execute(
        """INSERT INTO users (id,email,name,credential,location,
                              password_hash,password_salt,password_iterations,
                              role,verification_status,license_number,license_board,
                              license_country,verified_at,reverify_due,
                              created_at,updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (uid, email.lower().strip(), name, credential, location,
         pw_hash, salt, iters, role, verification_status,
         license_number, license_board, license_country, verified_at, reverify_due,
         ts, ts),
    )
    return uid


def get_user_by_email(conn, email: str):
    return conn.execute("SELECT * FROM users WHERE email = ?",
                        (email.lower().strip(),)).fetchone()


def get_user(conn, user_id: str):
    return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def list_users(conn):
    return conn.execute(
        "SELECT * FROM users ORDER BY CASE role WHEN 'admin' THEN 0 "
        "WHEN 'contributor' THEN 1 ELSE 2 END, name"
    ).fetchall()


def public_user(row) -> dict:
    """The shape the frontend sees. Never leaks hash/salt."""
    if row is None:
        return None
    return {
        "id": row["id"],
        "email": row["email"],
        "name": row["name"],
        "credential": row["credential"],
        "location": row["location"],
        "role": row["role"],
        "verification_status": row["verification_status"],
        "license": {
            "number": row["license_number"],
            "board": row["license_board"],
            "country": row["license_country"],
        },
        "reverify_due": row["reverify_due"],
        # what the UI should let this account do
        "can_post": can(row, "post.create"),
        "can_delete_any": can(row, "post.delete_any"),
        "is_admin": row["role"] == "admin",
        "permissive_mode": PERMISSIVE_MODE,
    }


# ── authorisation ──────────────────────────────────────────────────────────
ROLE_GRANTS = {
    "admin":       {"post.create", "post.delete_own", "post.delete_any",
                    "post.edit_any", "comment.create", "user.manage", "audit.read"},
    "contributor": {"post.create", "post.delete_own", "comment.create"},
    "reader":      set(),
}


def can(user_row, action: str) -> bool:
    """Single authorisation gate for the whole app."""
    if user_row is None:
        return False
    if PERMISSIVE_MODE:
        # "for now, let every account have all access"
        return True
    return action in ROLE_GRANTS.get(user_row["role"], set())


# ── sessions ───────────────────────────────────────────────────────────────
def create_session(conn, user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    created = datetime.now(timezone.utc).replace(microsecond=0)
    conn.execute(
        "INSERT INTO sessions (token,user_id,created_at,expires_at) VALUES (?,?,?,?)",
        (token, user_id, created.isoformat(), (created + SESSION_TTL).isoformat()),
    )
    return token


def user_for_token(conn, token: str):
    if not token:
        return None
    row = conn.execute(
        "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id "
        "WHERE s.token = ? AND s.expires_at > ?",
        (token, now_iso()),
    ).fetchone()
    return row


def delete_session(conn, token: str) -> None:
    conn.execute("DELETE FROM sessions WHERE token = ?", (token,))


def purge_expired_sessions(conn) -> int:
    cur = conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now_iso(),))
    return cur.rowcount


# ── posts ──────────────────────────────────────────────────────────────────
def _post_out(row) -> dict:
    """DB row -> the JSON shape the frontend already understands."""
    d = dict(row)
    for f in JSON_FIELDS:
        try:
            d[f] = json.loads(d.get(f) or "[]")
        except json.JSONDecodeError:
            d[f] = []
    d["procedurePath"] = d.pop("procedure_path", None)
    d["date"] = (d.get("created_at") or "")[:10]
    # Always present — the frontend reads .comments.length unconditionally.
    d["comments"] = []
    d["author"] = {
        "id": d.pop("author_id", None),
        "name": d.pop("author_name", None),
        "credential": d.pop("author_credential", None),
        "location": d.pop("author_location", None),
        "verified": d.pop("author_verification", None) == "verified",
        "role": d.pop("author_role", None),
    }
    return d


_POST_SELECT = """
  SELECT p.*, u.name AS author_name, u.credential AS author_credential,
         u.location AS author_location, u.verification_status AS author_verification,
         u.role AS author_role
    FROM posts p JOIN users u ON u.id = p.author_id
"""


def attach_comments(conn, posts):
    """Fill in each post's discussion with one query for the whole set."""
    if not posts:
        return posts
    marks = ",".join("?" for _ in posts)
    rows = conn.execute(
        "SELECT c.post_id, c.body, c.created_at, u.name AS author_name, "
        "       u.verification_status AS author_verification "
        "  FROM comments c JOIN users u ON u.id = c.author_id "
        f" WHERE c.post_id IN ({marks}) ORDER BY c.created_at ASC",
        [p["id"] for p in posts]).fetchall()
    grouped = {}
    for r in rows:
        grouped.setdefault(r["post_id"], []).append({
            "author": r["author_name"],
            "verified": r["author_verification"] == "verified",
            "text": r["body"],
            "date": (r["created_at"] or "")[:10],
        })
    for p in posts:
        p["comments"] = grouped.get(p["id"], [])
    return posts


def list_posts(conn, *, include_drafts=False) -> list[dict]:
    sql = _POST_SELECT + (
        "" if include_drafts else " WHERE p.status = 'published'"
    ) + " ORDER BY p.created_at DESC"
    posts = [_post_out(r) for r in conn.execute(sql).fetchall()]
    return attach_comments(conn, posts)


def get_post(conn, post_id: str):
    row = conn.execute(_POST_SELECT + " WHERE p.id = ?", (post_id,)).fetchone()
    if not row:
        return None
    post = _post_out(row)
    attach_comments(conn, [post])
    return post


def create_post(conn, *, author_id: str, data: dict) -> str:
    pid = data.get("id") or new_id()
    ts = now_iso()
    conn.execute(
        """INSERT INTO posts (id,author_id,title,summary,procedure,procedure_path,
                              difficulty,complications,tools,resolution,takeaways,media,
                              presentation,unusual,outcome,status,reads,saves,
                              created_at,updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (pid, author_id,
         data.get("title", "").strip(),
         data.get("summary"),
         data.get("procedure", "").strip(),
         data.get("procedurePath") or data.get("procedure_path"),
         data.get("difficulty", "Medium"),
         json.dumps(data.get("complications", [])),
         json.dumps(data.get("tools", [])),
         json.dumps(data.get("resolution", [])),
         json.dumps(data.get("takeaways", [])),
         json.dumps(data.get("media", [])),
         data.get("presentation"),
         data.get("unusual"),
         data.get("outcome"),
         data.get("status", "published"),
         int(data.get("reads", 0) or 0),
         int(data.get("saves", 0) or 0),
         data.get("created_at") or ts, ts),
    )
    return pid


def delete_post(conn, post_id: str) -> bool:
    cur = conn.execute("DELETE FROM posts WHERE id = ?", (post_id,))
    return cur.rowcount > 0


# ── audit ──────────────────────────────────────────────────────────────────
def audit(conn, *, actor_id, action, target=None, detail=None) -> None:
    conn.execute(
        "INSERT INTO audit_log (id,actor_id,action,target,detail,created_at) "
        "VALUES (?,?,?,?,?,?)",
        (new_id(), actor_id, action, target, detail, now_iso()),
    )


def list_audit(conn, limit=100):
    return conn.execute(
        "SELECT * FROM audit_log ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
