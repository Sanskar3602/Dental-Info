"""
Postgres data + auth layer for the deployed (Vercel) backend.

This is the Postgres twin of server/store.py, which stays SQLite for local
development. Two implementations is deliberate: the local one must run with
zero installs, the deployed one must run on a serverless filesystem where
SQLite cannot persist. They share the same schema shape and the same
public_user() / post JSON contract, so the frontend cannot tell them apart.

Files in api/ beginning with "_" are helpers, not routes -- Vercel does not
expose them as endpoints.

Connection strategy: serverless functions are short-lived and concurrent, so
we open a connection per request and rely on Neon's *pooled* connection
string (the host containing "-pooler"). Do not use the direct URL here.
"""

import hashlib
import json
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import psycopg2
import psycopg2.extras

DATABASE_URL = os.environ.get("DATABASE_URL", "")

# Same override as the local server: every signed-in account currently has
# full access. Set DENTAL_INFO_STRICT=1 in the Vercel env vars to enforce roles.
PERMISSIVE_MODE = os.environ.get("DENTAL_INFO_STRICT", "") not in ("1", "true", "yes")

SESSION_TTL = timedelta(days=7)
PBKDF2_ITERATIONS = 240_000

ROLE_GRANTS = {
    "admin": {"post.create", "post.delete_own", "post.delete_any", "post.edit_any",
              "comment.create", "user.manage", "audit.read"},
    "contributor": {"post.create", "post.delete_own", "comment.create"},
    "reader": set(),
}


class ConfigError(RuntimeError):
    """DATABASE_URL missing or unusable -- surfaced to the client as 503."""


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def new_id():
    return str(uuid.uuid4())


def connect():
    if not DATABASE_URL:
        raise ConfigError(
            "DATABASE_URL is not set. Add your Neon pooled connection string "
            "to the Vercel project's environment variables and redeploy."
        )
    try:
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=8,
                                cursor_factory=psycopg2.extras.RealDictCursor)
    except psycopg2.Error as exc:
        raise ConfigError("Could not connect to the database: %s" % exc)
    conn.autocommit = True
    return conn


# ── passwords ──────────────────────────────────────────────────────────────
def hash_password(password, salt=None, iterations=PBKDF2_ITERATIONS):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 bytes.fromhex(salt), iterations)
    return digest.hex(), salt, iterations


def verify_password(password, row):
    candidate, _, _ = hash_password(password, row["password_salt"],
                                    row["password_iterations"])
    return secrets.compare_digest(candidate, row["password_hash"])


# ── authorisation ──────────────────────────────────────────────────────────
def can(user, action):
    if user is None:
        return False
    if PERMISSIVE_MODE:
        return True
    return action in ROLE_GRANTS.get(user["role"], set())


def public_user(row):
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
        "can_post": can(row, "post.create"),
        "can_delete_any": can(row, "post.delete_any"),
        "is_admin": row["role"] == "admin",
        "permissive_mode": PERMISSIVE_MODE,
    }


# ── users & sessions ───────────────────────────────────────────────────────
def get_user_by_email(cur, email):
    cur.execute("SELECT * FROM users WHERE email = %s", (email.lower().strip(),))
    return cur.fetchone()


def list_users(cur):
    cur.execute("SELECT * FROM users ORDER BY CASE role WHEN 'admin' THEN 0 "
                "WHEN 'contributor' THEN 1 ELSE 2 END, name")
    return cur.fetchall()


def create_session(cur, user_id):
    token = secrets.token_urlsafe(32)
    created = datetime.now(timezone.utc).replace(microsecond=0)
    cur.execute("INSERT INTO sessions (token,user_id,created_at,expires_at) "
                "VALUES (%s,%s,%s,%s)",
                (token, user_id, created.isoformat(),
                 (created + SESSION_TTL).isoformat()))
    return token


def user_for_token(cur, token):
    if not token:
        return None
    cur.execute("SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id "
                "WHERE s.token = %s AND s.expires_at > %s", (token, now_iso()))
    return cur.fetchone()


def delete_session(cur, token):
    cur.execute("DELETE FROM sessions WHERE token = %s", (token,))


# ── posts ──────────────────────────────────────────────────────────────────
_POST_SELECT = """
  SELECT p.*, u.name AS author_name, u.credential AS author_credential,
         u.location AS author_location, u.verification_status AS author_verification,
         u.role AS author_role
    FROM posts p JOIN users u ON u.id = p.author_id
"""

JSON_FIELDS = ("complications", "tools", "resolution", "takeaways", "media")


def _post_out(row):
    d = dict(row)
    for f in JSON_FIELDS:
        v = d.get(f)
        if isinstance(v, str):                 # defensive: TEXT instead of JSONB
            try:
                v = json.loads(v)
            except (json.JSONDecodeError, TypeError):
                v = []
        d[f] = v if isinstance(v, list) else []
    d["procedurePath"] = d.pop("procedure_path", None)
    d["date"] = (d.get("created_at") or "")[:10]
    d["author"] = {
        "id": d.pop("author_id", None),
        "name": d.pop("author_name", None),
        "credential": d.pop("author_credential", None),
        "location": d.pop("author_location", None),
        "verified": d.pop("author_verification", None) == "verified",
        "role": d.pop("author_role", None),
    }
    return d


def list_posts(cur, include_drafts=False):
    sql = _POST_SELECT + ("" if include_drafts else " WHERE p.status = 'published'") \
        + " ORDER BY p.created_at DESC"
    cur.execute(sql)
    return [_post_out(r) for r in cur.fetchall()]


def get_post(cur, post_id):
    cur.execute(_POST_SELECT + " WHERE p.id = %s", (post_id,))
    row = cur.fetchone()
    return _post_out(row) if row else None


def create_post(cur, author_id, data):
    pid = data.get("id") or new_id()
    ts = now_iso()
    cur.execute(
        """INSERT INTO posts (id,author_id,title,summary,procedure,procedure_path,
                              difficulty,complications,tools,resolution,takeaways,media,
                              presentation,unusual,outcome,status,reads,saves,
                              created_at,updated_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (pid, author_id,
         (data.get("title") or "").strip(),
         data.get("summary"),
         (data.get("procedure") or "").strip(),
         data.get("procedurePath") or data.get("procedure_path"),
         data.get("difficulty") or "Medium",
         psycopg2.extras.Json(data.get("complications") or []),
         psycopg2.extras.Json(data.get("tools") or []),
         psycopg2.extras.Json(data.get("resolution") or []),
         psycopg2.extras.Json(data.get("takeaways") or []),
         psycopg2.extras.Json(data.get("media") or []),
         data.get("presentation"), data.get("unusual"), data.get("outcome"),
         data.get("status") or "published",
         int(data.get("reads") or 0), int(data.get("saves") or 0),
         data.get("created_at") or ts, ts))
    return pid


def delete_post(cur, post_id):
    cur.execute("DELETE FROM posts WHERE id = %s", (post_id,))
    return cur.rowcount > 0


# ── audit ──────────────────────────────────────────────────────────────────
def audit(cur, actor_id, action, target=None, detail=None):
    cur.execute("INSERT INTO audit_log (id,actor_id,action,target,detail,created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (new_id(), actor_id, action, target, detail, now_iso()))


def list_audit(cur, limit=100):
    cur.execute("SELECT * FROM audit_log ORDER BY created_at DESC LIMIT %s", (limit,))
    return cur.fetchall()
