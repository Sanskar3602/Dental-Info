"""
Vercel serverless entry point — the whole JSON API in one WSGI app.

Vercel's Python runtime picks up a module-level WSGI callable named `app`.
vercel.json rewrites every /api/* request here and passes the original path
in the __path query parameter, so one function serves all routes (rather
than one file per endpoint duplicating the DB layer).

Local development does NOT use this file — run server/app.py instead.
"""

import json
import os
from http.cookies import SimpleCookie
from urllib.parse import parse_qs

import _db as db

COOKIE_NAME = "dental_info_session"


# ── WSGI helpers ───────────────────────────────────────────────────────────
def _json(start_response, payload, status="200 OK", cookie=None):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = [
        ("Content-Type", "application/json; charset=utf-8"),
        ("Content-Length", str(len(body))),
        ("Cache-Control", "no-store"),
    ]
    if cookie:
        headers.append(("Set-Cookie", cookie))
    start_response(status, headers)
    return [body]


STATUS = {
    400: "400 Bad Request",
    401: "401 Unauthorized",
    403: "403 Forbidden",
    404: "404 Not Found",
    405: "405 Method Not Allowed",
    503: "503 Service Unavailable",
}


def _err(start_response, code, message):
    return _json(start_response, {"error": message}, STATUS.get(code, "400 Bad Request"))


def _route(environ):
    """The originally requested path, before Vercel's rewrite."""
    qs = parse_qs(environ.get("QUERY_STRING", ""))
    if "__path" in qs and qs["__path"][0]:
        return qs["__path"][0].split("?")[0]
    return environ.get("PATH_INFO", "") or "/"


def _token(environ):
    raw = environ.get("HTTP_COOKIE")
    if not raw:
        return None
    jar = SimpleCookie()
    jar.load(raw)
    m = jar.get(COOKIE_NAME)
    return m.value if m else None


def _body(environ):
    try:
        size = int(environ.get("CONTENT_LENGTH") or 0)
    except ValueError:
        return {}
    if not size:
        return {}
    raw = environ["wsgi.input"].read(size)
    try:
        return json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _cookie(token, environ, clear=False):
    # Secure only over HTTPS, so this still works against a local run
    https = environ.get("HTTP_X_FORWARDED_PROTO", "http") == "https"
    parts = [f"{COOKIE_NAME}={'' if clear else token}", "Path=/", "HttpOnly",
             "SameSite=Lax"]
    if https:
        parts.append("Secure")
    parts.append("Max-Age=0" if clear
                 else f"Max-Age={int(db.SESSION_TTL.total_seconds())}")
    return "; ".join(parts)


# ── the app ────────────────────────────────────────────────────────────────
def app(environ, start_response):
    method = environ.get("REQUEST_METHOD", "GET").upper()
    path = _route(environ).rstrip("/") or "/"

    try:
        conn = db.connect()
    except db.ConfigError as exc:
        # The most likely deployment mistake: no DATABASE_URL yet. Say so
        # explicitly instead of failing with an opaque 500.
        return _err(start_response, 503, str(exc))

    try:
        with conn.cursor() as cur:
            me = db.user_for_token(cur, _token(environ))

            # ── auth ──────────────────────────────────────────────────────
            if path == "/api/me":
                return _json(start_response, {"user": db.public_user(me)})

            if path == "/api/login":
                if method != "POST":
                    return _err(start_response, 405, "Use POST")
                body = _body(environ)
                if body is None:
                    return _err(start_response, 400, "Body must be valid JSON")
                row = db.get_user_by_email(cur, body.get("email") or "")
                if row is None or not db.verify_password(body.get("password") or "", row):
                    # identical message for both cases: no account enumeration
                    return _err(start_response, 401, "Incorrect email or password")
                token = db.create_session(cur, row["id"])
                db.audit(cur, row["id"], "login", row["email"])
                return _json(start_response, {"user": db.public_user(row)},
                             cookie=_cookie(token, environ))

            if path == "/api/logout":
                tok = _token(environ)
                if tok:
                    db.delete_session(cur, tok)
                return _json(start_response, {"ok": True},
                             cookie=_cookie(None, environ, clear=True))

            # ── posts ─────────────────────────────────────────────────────
            if path == "/api/posts":
                if method == "GET":
                    return _json(start_response, {
                        "posts": db.list_posts(cur, include_drafts=db.can(me, "post.edit_any"))
                    })
                if method == "POST":
                    if me is None:
                        return _err(start_response, 401, "Not signed in")
                    if not db.can(me, "post.create"):
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
                    pid = db.create_post(cur, me["id"], body)
                    db.audit(cur, me["id"], "post.create", pid, body.get("title"))
                    return _json(start_response, {"post": db.get_post(cur, pid)},
                                 status="201 Created")
                return _err(start_response, 405, "Use GET or POST")

            if path.startswith("/api/posts/"):
                post_id = path.rsplit("/", 1)[-1]
                if method == "GET":
                    post = db.get_post(cur, post_id)
                    if not post:
                        return _err(start_response, 404, "Case not found")
                    return _json(start_response, {"post": post})
                if method == "DELETE":
                    if me is None:
                        return _err(start_response, 401, "Not signed in")
                    post = db.get_post(cur, post_id)
                    if not post:
                        return _err(start_response, 404, "Case not found")
                    own = post["author"]["id"] == me["id"]
                    if not (db.can(me, "post.delete_any")
                            or (own and db.can(me, "post.delete_own"))):
                        return _err(start_response, 403,
                                    "You can only delete your own cases")
                    db.delete_post(cur, post_id)
                    db.audit(cur, me["id"], "post.delete", post_id, post["title"])
                    return _json(start_response, {"ok": True, "deleted": post_id})
                return _err(start_response, 405, "Use GET or DELETE")

            # ── admin ─────────────────────────────────────────────────────
            if path == "/api/users":
                if me is None:
                    return _err(start_response, 401, "Not signed in")
                if not db.can(me, "user.manage"):
                    return _err(start_response, 403, "Admin only")
                return _json(start_response,
                             {"users": [db.public_user(u) for u in db.list_users(cur)]})

            if path == "/api/audit":
                if me is None:
                    return _err(start_response, 401, "Not signed in")
                if not db.can(me, "audit.read"):
                    return _err(start_response, 403, "Admin only")
                return _json(start_response,
                             {"entries": [dict(r) for r in db.list_audit(cur)]})

            if path == "/api/health":
                cur.execute("SELECT COUNT(*) AS users FROM users")
                users = cur.fetchone()["users"]
                cur.execute("SELECT COUNT(*) AS posts FROM posts")
                posts = cur.fetchone()["posts"]
                return _json(start_response, {
                    "ok": True, "users": users, "posts": posts,
                    "permissive_mode": db.PERMISSIVE_MODE,
                })

        return _err(start_response, 404, "Unknown endpoint")
    finally:
        conn.close()


# Vercel also accepts a `handler`; expose the WSGI app under both names.
application = app
