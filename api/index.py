"""
Vercel serverless entry point — the entire JSON API in ONE self-contained file.

Deliberately a single file with no sibling imports. An earlier version did
`import _db`, which Vercel's Python runtime could not resolve at cold start,
so every request died as FUNCTION_INVOCATION_FAILED (an opaque 500). Sibling
imports inside api/ are not reliable here; inlining removes the failure mode.

Imports that can fail (the Postgres driver) are guarded, so a broken
deployment answers with a JSON explanation instead of crashing. Hit
/api/health to see exactly what is wrong.

Local development does NOT use this file — run server/app.py (SQLite) instead.
"""

import hashlib
import json
import os
import secrets
import sys
import uuid
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
from urllib.parse import parse_qs

# ── guarded driver import ──────────────────────────────────────────────────
# If the wheel is missing or ABI-incompatible we must still be able to
# answer /api/health and say so, rather than failing to boot.
DRIVER_ERROR = None
try:
    import psycopg2
    import psycopg2.extras
except Exception as exc:                      # ImportError, or a linker error
    psycopg2 = None
    DRIVER_ERROR = "%s: %s" % (type(exc).__name__, exc)

COOKIE_NAME = "dental_info_session"

# Be tolerant of how the value was pasted into the dashboard: surrounding
# whitespace/newlines, or wrapping quotes, are stripped rather than producing
# an opaque connection failure.
_RAW_DATABASE_URL = os.environ.get("DATABASE_URL", "") or ""
DATABASE_URL = _RAW_DATABASE_URL.strip().strip('"').strip("'").strip()
# Role enforcement is the DEFAULT. Admins delete any case, contributors
# delete only their own, read-only accounts delete nothing. Permissive mode
# is now an explicit opt-in for debugging, not the default.
PERMISSIVE_MODE = os.environ.get("DENTAL_INFO_PERMISSIVE", "") in ("1", "true", "yes")

SESSION_TTL = timedelta(days=7)
PBKDF2_ITERATIONS = 240_000
JSON_FIELDS = ("complications", "tools", "resolution", "takeaways", "media")

ROLE_GRANTS = {
    "admin": {"post.create", "post.delete_own", "post.delete_any", "post.edit_any",
              "comment.create", "user.manage", "audit.read"},
    "contributor": {"post.create", "post.delete_own", "comment.create"},
    "reader": set(),
}


class ConfigError(RuntimeError):
    """Deployment misconfiguration — surfaced to the client as 503."""


# ── small helpers ──────────────────────────────────────────────────────────
def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def new_id():
    return str(uuid.uuid4())


def connect():
    if psycopg2 is None:
        raise ConfigError(
            "The Postgres driver failed to load on the server (%s). "
            "Check that requirements.txt is deployed." % DRIVER_ERROR)
    if not DATABASE_URL:
        raise ConfigError(
            "DATABASE_URL is not set. Add your Neon pooled connection string to "
            "the Vercel project's Environment Variables, then redeploy.")
    try:
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=8,
                                cursor_factory=psycopg2.extras.RealDictCursor)
    except Exception as exc:
        raise ConfigError("Could not connect to the database (%s: %s)"
                          % (type(exc).__name__, exc))
    conn.autocommit = True
    return conn


def hash_password(password, salt=None, iterations=PBKDF2_ITERATIONS):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 bytes.fromhex(salt), iterations)
    return digest.hex(), salt, iterations


def verify_password(password, row):
    candidate, _, _ = hash_password(password, row["password_salt"],
                                    row["password_iterations"])
    return secrets.compare_digest(candidate, row["password_hash"])


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
        "id": row["id"], "email": row["email"], "name": row["name"],
        "credential": row["credential"], "location": row["location"],
        "role": row["role"], "verification_status": row["verification_status"],
        "license": {"number": row["license_number"], "board": row["license_board"],
                    "country": row["license_country"]},
        "reverify_due": row["reverify_due"],
        "can_post": can(row, "post.create"),
        "can_delete_any": can(row, "post.delete_any"),
        "can_delete_own": can(row, "post.delete_own"),
        "can_comment": can(row, "comment.create"),
        "is_admin": row["role"] == "admin",
        "permissive_mode": PERMISSIVE_MODE,
    }


# ── queries ────────────────────────────────────────────────────────────────
_POST_SELECT = """
  SELECT p.*, u.name AS author_name, u.credential AS author_credential,
         u.location AS author_location, u.verification_status AS author_verification,
         u.role AS author_role
    FROM posts p JOIN users u ON u.id = p.author_id
"""


def _post_out(row):
    d = dict(row)
    for f in JSON_FIELDS:
        v = d.get(f)
        if isinstance(v, str):
            try:
                v = json.loads(v)
            except Exception:
                v = []
        d[f] = v if isinstance(v, list) else []
    d["procedurePath"] = d.pop("procedure_path", None)
    d["date"] = (d.get("created_at") or "")[:10]
    # Always present, even when there is no discussion. The frontend reads
    # .comments.length unconditionally; omitting the key blanks the page.
    d["comments"] = []
    d["author"] = {
        "id": d.pop("author_id", None), "name": d.pop("author_name", None),
        "credential": d.pop("author_credential", None),
        "location": d.pop("author_location", None),
        "verified": d.pop("author_verification", None) == "verified",
        "role": d.pop("author_role", None),
    }
    return d


def _attach_comments(cur, posts):
    """Fill in each post's discussion with one query for the whole set."""
    if not posts:
        return posts
    cur.execute(
        """SELECT c.post_id, c.body, c.created_at,
                  u.name AS author_name, u.verification_status AS author_verification
             FROM comments c JOIN users u ON u.id = c.author_id
            WHERE c.post_id = ANY(%s)
         ORDER BY c.created_at ASC, c.id ASC""",
        ([p["id"] for p in posts],))
    grouped = {}
    for r in cur.fetchall():
        grouped.setdefault(r["post_id"], []).append({
            "author": r["author_name"],
            "verified": r["author_verification"] == "verified",
            "text": r["body"],
            "date": (r["created_at"] or "")[:10],
        })
    for p in posts:
        p["comments"] = grouped.get(p["id"], [])
    return posts


def get_user_by_email(cur, email):
    cur.execute("SELECT * FROM users WHERE email = %s", ((email or "").lower().strip(),))
    return cur.fetchone()


def user_for_token(cur, token):
    if not token:
        return None
    cur.execute("SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id "
                "WHERE s.token = %s AND s.expires_at > %s", (token, now_iso()))
    return cur.fetchone()


def create_session(cur, user_id):
    token = secrets.token_urlsafe(32)
    created = datetime.now(timezone.utc).replace(microsecond=0)
    cur.execute("INSERT INTO sessions (token,user_id,created_at,expires_at) "
                "VALUES (%s,%s,%s,%s)",
                (token, user_id, created.isoformat(),
                 (created + SESSION_TTL).isoformat()))
    return token


def audit(cur, actor_id, action, target=None, detail=None):
    cur.execute("INSERT INTO audit_log (id,actor_id,action,target,detail,created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (new_id(), actor_id, action, target, detail, now_iso()))


# ── WSGI plumbing ──────────────────────────────────────────────────────────
STATUS = {200: "200 OK", 201: "201 Created", 400: "400 Bad Request",
          401: "401 Unauthorized", 403: "403 Forbidden", 404: "404 Not Found",
          405: "405 Method Not Allowed", 500: "500 Internal Server Error",
          503: "503 Service Unavailable"}


def _json(start_response, payload, code=200, cookie=None):
    body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    headers = [("Content-Type", "application/json; charset=utf-8"),
               ("Content-Length", str(len(body))),
               ("Cache-Control", "no-store")]
    if cookie:
        headers.append(("Set-Cookie", cookie))
    start_response(STATUS.get(code, "200 OK"), headers)
    return [body]


def _err(start_response, code, message):
    return _json(start_response, {"error": message}, code)


def _route(environ):
    """The originally requested path, before Vercel's rewrite."""
    qs = parse_qs(environ.get("QUERY_STRING", ""))
    if qs.get("__path", [""])[0]:
        return qs["__path"][0].split("?")[0]
    # Fall back to the real path. If the rewrite did not fire we may see
    # /api/index, which carries no route information.
    return environ.get("PATH_INFO", "") or "/"


def _token(environ):
    raw = environ.get("HTTP_COOKIE")
    if not raw:
        return None
    jar = SimpleCookie()
    try:
        jar.load(raw)
    except Exception:
        return None
    m = jar.get(COOKIE_NAME)
    return m.value if m else None


def _body(environ):
    try:
        size = int(environ.get("CONTENT_LENGTH") or 0)
    except ValueError:
        return {}
    if not size:
        return {}
    try:
        raw = environ["wsgi.input"].read(size)
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return None


def _cookie(token, environ, clear=False):
    https = environ.get("HTTP_X_FORWARDED_PROTO", "http") == "https"
    parts = ["%s=%s" % (COOKIE_NAME, "" if clear else token),
             "Path=/", "HttpOnly", "SameSite=Lax"]
    if https:
        parts.append("Secure")
    parts.append("Max-Age=0" if clear
                 else "Max-Age=%d" % int(SESSION_TTL.total_seconds()))
    return "; ".join(parts)


def _health():
    """Diagnostics that work even when the database does not."""
    # Env-var KEY NAMES only -- never values. If DATABASE_URL was set under a
    # different name, or scoped to the wrong environment, this shows it.
    db_like = sorted(k for k in os.environ
                     if any(w in k.upper() for w in ("DATABASE", "POSTGRES", "PG", "NEON")))
    info = {
        "ok": False,
        "python": sys.version.split()[0],
        "driver_loaded": psycopg2 is not None,
        "driver_error": DRIVER_ERROR,
        "database_url_set": bool(DATABASE_URL),
        "database_url_pooled": "-pooler" in DATABASE_URL if DATABASE_URL else None,
        # length and scheme only — never the credentials. Distinguishes
        # "variable saved empty" from "variable saved wrong".
        "database_url_raw_len": len(_RAW_DATABASE_URL),
        "database_url_len": len(DATABASE_URL),
        "database_url_scheme": DATABASE_URL.split("://")[0] if "://" in DATABASE_URL else None,
        "permissive_mode": PERMISSIVE_MODE,
        # which Vercel environment is actually serving this request
        "vercel_env": os.environ.get("VERCEL_ENV"),
        "vercel_region": os.environ.get("VERCEL_REGION"),
        "deployed_commit": (os.environ.get("VERCEL_GIT_COMMIT_SHA") or "")[:7] or None,
        "db_env_keys_visible": db_like,
    }
    if psycopg2 is None or not DATABASE_URL:
        info["hint"] = ("Install/redeploy requirements.txt" if psycopg2 is None
                        else "Set DATABASE_URL in Vercel env vars and redeploy")
        return info
    try:
        conn = connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS n FROM users")
                info["users"] = cur.fetchone()["n"]
                cur.execute("SELECT COUNT(*) AS n FROM posts")
                info["posts"] = cur.fetchone()["n"]
            info["ok"] = True
        finally:
            conn.close()
    except ConfigError as exc:
        info["error"] = str(exc)
        info["hint"] = "Run: python server/migrate_postgres.py"
    except Exception as exc:
        info["error"] = "%s: %s" % (type(exc).__name__, exc)
        info["hint"] = ("Tables may not exist yet. "
                        "Run: python server/migrate_postgres.py")
    return info


# ── the app ────────────────────────────────────────────────────────────────
def app(environ, start_response):
    method = environ.get("REQUEST_METHOD", "GET").upper()
    path = _route(environ).rstrip("/") or "/"

    # Never let an unexpected exception become an opaque 500 — report it.
    try:
        if path in ("/api/health", "/api/index", "/api", "/"):
            return _json(start_response, _health())

        try:
            conn = connect()
        except ConfigError as exc:
            return _err(start_response, 503, str(exc))

        try:
            with conn.cursor() as cur:
                me = user_for_token(cur, _token(environ))

                if path == "/api/me":
                    return _json(start_response, {"user": public_user(me)})

                if path == "/api/login":
                    if method != "POST":
                        return _err(start_response, 405, "Use POST")
                    body = _body(environ)
                    if body is None:
                        return _err(start_response, 400, "Body must be valid JSON")
                    row = get_user_by_email(cur, body.get("email"))
                    if row is None or not verify_password(body.get("password") or "", row):
                        return _err(start_response, 401, "Incorrect email or password")
                    token = create_session(cur, row["id"])
                    audit(cur, row["id"], "login", row["email"])
                    return _json(start_response, {"user": public_user(row)},
                                 cookie=_cookie(token, environ))

                if path == "/api/logout":
                    tok = _token(environ)
                    if tok:
                        cur.execute("DELETE FROM sessions WHERE token = %s", (tok,))
                    return _json(start_response, {"ok": True},
                                 cookie=_cookie(None, environ, clear=True))

                if path == "/api/posts":
                    if method == "GET":
                        sql = _POST_SELECT + ("" if can(me, "post.edit_any")
                                              else " WHERE p.status = 'published'") \
                            + " ORDER BY p.created_at DESC"
                        cur.execute(sql)
                        posts = [_post_out(r) for r in cur.fetchall()]
                        return _json(start_response,
                                     {"posts": _attach_comments(cur, posts)})
                    if method == "POST":
                        if me is None:
                            return _err(start_response, 401, "Not signed in")
                        if not can(me, "post.create"):
                            return _err(start_response, 403,
                                        "Only verified contributors can publish cases")
                        body = _body(environ)
                        if body is None:
                            return _err(start_response, 400, "Body must be valid JSON")
                        if not (body.get("title") or "").strip():
                            return _err(start_response, 400, "A title is required")
                        if not (body.get("procedure") or "").strip():
                            return _err(start_response, 400, "A procedure type is required")
                        if body.get("difficulty") not in (None, "Low", "Medium", "High"):
                            return _err(start_response, 400, "Invalid difficulty")

                        pid = body.get("id") or new_id()
                        ts = now_iso()
                        J = psycopg2.extras.Json
                        cur.execute(
                            """INSERT INTO posts (id,author_id,title,summary,procedure,
                                   procedure_path,difficulty,complications,tools,
                                   resolution,takeaways,media,presentation,unusual,
                                   outcome,status,reads,saves,created_at,updated_at)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                                       %s,%s,%s,%s,%s)""",
                            (pid, me["id"], body["title"].strip(), body.get("summary"),
                             body["procedure"].strip(),
                             body.get("procedurePath") or body.get("procedure_path"),
                             body.get("difficulty") or "Medium",
                             J(body.get("complications") or []), J(body.get("tools") or []),
                             J(body.get("resolution") or []), J(body.get("takeaways") or []),
                             J(body.get("media") or []),
                             body.get("presentation"), body.get("unusual"),
                             body.get("outcome"), "published",
                             int(body.get("reads") or 0), int(body.get("saves") or 0),
                             body.get("created_at") or ts, ts))
                        audit(cur, me["id"], "post.create", pid, body["title"])
                        cur.execute(_POST_SELECT + " WHERE p.id = %s", (pid,))
                        return _json(start_response,
                                     {"post": _post_out(cur.fetchone())}, 201)
                    return _err(start_response, 405, "Use GET or POST")

                if path.startswith("/api/posts/"):
                    post_id = path.rsplit("/", 1)[-1]
                    cur.execute(_POST_SELECT + " WHERE p.id = %s", (post_id,))
                    row = cur.fetchone()
                    post = _post_out(row) if row else None
                    if post:
                        _attach_comments(cur, [post])

                    if method == "GET":
                        if not post:
                            return _err(start_response, 404, "Case not found")
                        return _json(start_response, {"post": post})
                    if method == "DELETE":
                        if me is None:
                            return _err(start_response, 401, "Not signed in")
                        if not post:
                            return _err(start_response, 404, "Case not found")
                        own = post["author"]["id"] == me["id"]
                        if not (can(me, "post.delete_any")
                                or (own and can(me, "post.delete_own"))):
                            # distinguish "not yours" from "you cannot delete at all"
                            return _err(start_response, 403,
                                        "You can only delete your own cases"
                                        if can(me, "post.delete_own")
                                        else "Your account is read-only and cannot delete cases")
                        cur.execute("DELETE FROM posts WHERE id = %s", (post_id,))
                        audit(cur, me["id"], "post.delete", post_id, post["title"])
                        return _json(start_response, {"ok": True, "deleted": post_id})
                    return _err(start_response, 405, "Use GET or DELETE")

                if path == "/api/users":
                    if me is None:
                        return _err(start_response, 401, "Not signed in")
                    if not can(me, "user.manage"):
                        return _err(start_response, 403, "Admin only")
                    cur.execute("SELECT * FROM users ORDER BY CASE role "
                                "WHEN 'admin' THEN 0 WHEN 'contributor' THEN 1 "
                                "ELSE 2 END, name")
                    return _json(start_response,
                                 {"users": [public_user(u) for u in cur.fetchall()]})

                if path == "/api/audit":
                    if me is None:
                        return _err(start_response, 401, "Not signed in")
                    if not can(me, "audit.read"):
                        return _err(start_response, 403, "Admin only")
                    cur.execute("SELECT * FROM audit_log ORDER BY created_at DESC "
                                "LIMIT 100")
                    return _json(start_response,
                                 {"entries": [dict(r) for r in cur.fetchall()]})

            return _err(start_response, 404, "Unknown endpoint: %s" % path)
        finally:
            conn.close()

    except Exception as exc:
        # Last resort: still answer in JSON so the browser shows something useful.
        return _json(start_response, {
            "error": "Server error: %s: %s" % (type(exc).__name__, exc),
            "hint": "Check /api/health for configuration diagnostics.",
        }, 500)


# Vercel accepts a module-level WSGI callable; expose both common names.
application = app
handler = app
