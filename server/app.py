"""
Dental Info — API + static server. Standard library only.

Run:   python server/app.py
Then:  http://localhost:8000

Serves the prototype/ folder at / and a JSON API under /api/.

This is a development server (http.server, single-threaded per request via
ThreadingHTTPServer). It is deliberately not a production stack -- see
server/README.md for what changes when you deploy.
"""

from __future__ import annotations

import json
import os
import sys
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import store  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR.parent / "prototype"
PORT = int(os.environ.get("PORT", "8000"))
COOKIE_NAME = "dental_info_session"


class Handler(SimpleHTTPRequestHandler):
    # serve the prototype folder as the web root
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    # ── plumbing ──────────────────────────────────────────────────────────
    def log_message(self, fmt, *args):
        # quieter, and prefixed so it is obvious which server is talking
        sys.stderr.write("  [api] %s\n" % (fmt % args))

    def _send_json(self, payload, status=HTTPStatus.OK, cookie=None):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status, message):
        self._send_json({"error": message}, status=status)

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    def _token(self):
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        jar = SimpleCookie()
        jar.load(raw)
        morsel = jar.get(COOKIE_NAME)
        return morsel.value if morsel else None

    def _current_user(self, conn):
        return store.user_for_token(conn, self._token())

    def _require_user(self, conn):
        user = self._current_user(conn)
        if user is None:
            self._error(HTTPStatus.UNAUTHORIZED, "Not signed in")
            return None
        return user

    # ── routing ───────────────────────────────────────────────────────────
    def do_GET(self):
        path = urlparse(self.path).path
        if path.startswith("/api/"):
            return self._api_get(path)
        # SPA: unknown non-file paths fall back to index.html
        candidate = STATIC_DIR / path.lstrip("/")
        if path != "/" and not candidate.exists():
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        if not path.startswith("/api/"):
            return self._error(HTTPStatus.NOT_FOUND, "Not found")
        return self._api_post(path)

    def do_DELETE(self):
        path = urlparse(self.path).path
        if not path.startswith("/api/"):
            return self._error(HTTPStatus.NOT_FOUND, "Not found")
        return self._api_delete(path)

    # ── GET /api/... ──────────────────────────────────────────────────────
    def _api_get(self, path):
        with store.connect() as conn:
            if path == "/api/me":
                user = self._current_user(conn)
                return self._send_json({"user": store.public_user(user)})

            if path == "/api/posts":
                user = self._current_user(conn)
                # drafts are only visible to someone who could manage them
                include_drafts = store.can(user, "post.edit_any")
                return self._send_json(
                    {"posts": store.list_posts(conn, include_drafts=include_drafts)}
                )

            if path.startswith("/api/posts/"):
                post = store.get_post(conn, path.rsplit("/", 1)[-1])
                if not post:
                    return self._error(HTTPStatus.NOT_FOUND, "Case not found")
                return self._send_json({"post": post})

            if path == "/api/verification":
                user = self._require_user(conn)
                if user is None:
                    return
                r = store.latest_request(conn, user["id"])
                return self._send_json({"request": dict(r) if r else None})

            if path == "/api/verification/queue":
                user = self._require_user(conn)
                if user is None:
                    return
                if not store.can(user, "user.manage"):
                    return self._error(HTTPStatus.FORBIDDEN, "Admin only")
                return self._send_json(
                    {"requests": [dict(r) for r in store.queue(conn)]})

            if path == "/api/users":
                user = self._require_user(conn)
                if user is None:
                    return
                if not store.can(user, "user.manage"):
                    return self._error(HTTPStatus.FORBIDDEN, "Admin only")
                return self._send_json(
                    {"users": [store.public_user(u) for u in store.list_users(conn)]}
                )

            if path == "/api/audit":
                user = self._require_user(conn)
                if user is None:
                    return
                if not store.can(user, "audit.read"):
                    return self._error(HTTPStatus.FORBIDDEN, "Admin only")
                return self._send_json(
                    {"entries": [dict(r) for r in store.list_audit(conn)]}
                )

        return self._error(HTTPStatus.NOT_FOUND, "Unknown endpoint")

    # ── POST /api/... ─────────────────────────────────────────────────────
    def _api_post(self, path):
        body = self._read_json()
        if body is None:
            return self._error(HTTPStatus.BAD_REQUEST, "Body must be valid JSON")

        with store.connect() as conn:
            if path == "/api/login":
                email = (body.get("email") or "").strip()
                password = body.get("password") or ""
                row = store.get_user_by_email(conn, email)
                # same message either way: do not reveal which accounts exist
                if row is None or not store.verify_password(password, row):
                    return self._error(HTTPStatus.UNAUTHORIZED,
                                       "Incorrect email or password")
                token = store.create_session(conn, row["id"])
                store.audit(conn, actor_id=row["id"], action="login", target=row["email"])
                cookie = (f"{COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Lax; "
                          f"Max-Age={int(store.SESSION_TTL.total_seconds())}")
                return self._send_json({"user": store.public_user(row)}, cookie=cookie)

            if path == "/api/logout":
                token = self._token()
                if token:
                    store.delete_session(conn, token)
                cookie = f"{COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"
                return self._send_json({"ok": True}, cookie=cookie)

            if path == "/api/signup":
                problem = store.validate_signup(body)
                if problem:
                    return self._error(HTTPStatus.BAD_REQUEST, problem)
                email = body["email"].strip().lower()
                if store.get_user_by_email(conn, email):
                    return self._error(HTTPStatus.CONFLICT,
                                       "That email is already registered. Try signing in.")
                uid = store.create_user(
                    conn, email=email, name=body["name"], password=body["password"],
                    role="reader",
                    credential=(body.get("credential") or "").strip() or None,
                    location=(body.get("location") or "").strip() or None)
                store.audit(conn, actor_id=uid, action="user.signup", target=email)
                token = store.create_session(conn, uid)
                row = store.get_user(conn, uid)
                cookie = (f"{COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Lax; "
                          f"Max-Age={int(store.SESSION_TTL.total_seconds())}")
                return self._send_json({"user": store.public_user(row)},
                                       status=HTTPStatus.CREATED, cookie=cookie)

            if path == "/api/verification":
                user = self._require_user(conn)
                if user is None:
                    return
                num = (body.get("license_number") or "").strip()
                board = (body.get("license_board") or "").strip()
                country = (body.get("license_country") or "").strip()
                if len(num) < 3:
                    return self._error(HTTPStatus.BAD_REQUEST, "Enter your license number")
                if len(board) < 3:
                    return self._error(HTTPStatus.BAD_REQUEST,
                                       "Enter the issuing board or council")
                if len(country) < 2:
                    return self._error(HTTPStatus.BAD_REQUEST,
                                       "Enter the country of registration")
                if store.pending_request(conn, user["id"]):
                    return self._error(HTTPStatus.CONFLICT,
                                       "You already have a verification request under review.")
                rid = store.create_request(
                    conn, user["id"], number=num, board=board, country=country,
                    credential=(body.get("credential") or "").strip() or None,
                    document_note=(body.get("document_note") or "").strip() or None)
                store.audit(conn, actor_id=user["id"], action="verification.submit",
                            target=rid, detail=board)
                return self._send_json({"request": dict(store.get_request(conn, rid))},
                                       status=HTTPStatus.CREATED)

            if path.startswith("/api/verification/") and path.endswith("/decide"):
                user = self._require_user(conn)
                if user is None:
                    return
                if not store.can(user, "user.manage"):
                    return self._error(HTTPStatus.FORBIDDEN, "Admin only")
                rid = path.split("/")[3]
                decision = (body.get("decision") or "").strip()
                if decision not in ("approve", "reject"):
                    return self._error(HTTPStatus.BAD_REQUEST,
                                       "decision must be 'approve' or 'reject'")
                note = (body.get("note") or "").strip() or None
                if decision == "reject" and not note:
                    return self._error(HTTPStatus.BAD_REQUEST,
                                       "A rejection needs a reason the applicant can act on")
                vreq = store.get_request(conn, rid)
                if not vreq:
                    return self._error(HTTPStatus.NOT_FOUND, "Request not found")
                if vreq["status"] != "pending":
                    return self._error(HTTPStatus.CONFLICT,
                                       f"That request was already {vreq['status']}")
                out = store.decide_request(conn, rid, reviewer_id=user["id"],
                                           approve=(decision == "approve"), note=note)
                store.audit(conn, actor_id=user["id"],
                            action="verification." + decision, target=rid,
                            detail=vreq["license_board"])
                return self._send_json({"request": dict(out)})

            if path == "/api/posts":
                user = self._require_user(conn)
                if user is None:
                    return
                if not store.can(user, "post.create"):
                    return self._error(HTTPStatus.FORBIDDEN,
                                       "Only verified contributors can publish cases")
                title = (body.get("title") or "").strip()
                procedure = (body.get("procedure") or "").strip()
                if not title:
                    return self._error(HTTPStatus.BAD_REQUEST, "A title is required")
                if not procedure:
                    return self._error(HTTPStatus.BAD_REQUEST,
                                       "A procedure type is required")
                if body.get("difficulty") not in (None, "Low", "Medium", "High"):
                    return self._error(HTTPStatus.BAD_REQUEST, "Invalid difficulty")
                pid = store.create_post(conn, author_id=user["id"], data=body)
                store.audit(conn, actor_id=user["id"], action="post.create",
                            target=pid, detail=title)
                return self._send_json({"post": store.get_post(conn, pid)},
                                       status=HTTPStatus.CREATED)

        return self._error(HTTPStatus.NOT_FOUND, "Unknown endpoint")

    # ── DELETE /api/... ───────────────────────────────────────────────────
    def _api_delete(self, path):
        with store.connect() as conn:
            if path.startswith("/api/posts/"):
                post_id = path.rsplit("/", 1)[-1]
                user = self._require_user(conn)
                if user is None:
                    return
                post = store.get_post(conn, post_id)
                if not post:
                    return self._error(HTTPStatus.NOT_FOUND, "Case not found")

                own = post["author"]["id"] == user["id"]
                allowed = (store.can(user, "post.delete_any")
                           or (own and store.can(user, "post.delete_own")))
                if not allowed:
                    return self._error(
                        HTTPStatus.FORBIDDEN,
                        "You can only delete your own cases"
                        if store.can(user, "post.delete_own")
                        else "Your account is read-only and cannot delete cases")

                store.delete_post(conn, post_id)
                store.audit(conn, actor_id=user["id"], action="post.delete",
                            target=post_id, detail=post["title"])
                return self._send_json({"ok": True, "deleted": post_id})

            if path.startswith("/api/sessions"):
                user = self._require_user(conn)
                if user is None:
                    return
                n = store.purge_expired_sessions(conn)
                return self._send_json({"purged": n})

        return self._error(HTTPStatus.NOT_FOUND, "Unknown endpoint")


def main():
    store.init_db()
    if not STATIC_DIR.exists():
        sys.exit(f"Static folder not found: {STATIC_DIR}")

    with store.connect() as conn:
        users = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
        posts = conn.execute("SELECT COUNT(*) c FROM posts").fetchone()["c"]

    mode = "PERMISSIVE (every account has full access)" if store.PERMISSIVE_MODE \
        else "STRICT (roles enforced)"
    print("\n  Dental Info — dev server")
    print(f"  database    {store.DB_PATH}")
    print(f"  users       {users}")
    print(f"  cases       {posts}")
    print(f"  permissions {mode}")
    if users == 0:
        print("\n  No accounts yet. Run:  python server/seed.py")
    print(f"\n  →  http://localhost:{PORT}\n  Ctrl+C to stop\n")

    try:
        ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped\n")


if __name__ == "__main__":
    main()
