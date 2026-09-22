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

import base64
import hashlib
import json
import os
import re
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

# Media limits. Both numbers exist because of one Vercel constraint: a
# Serverless Function request/response body is hard-capped at 4.5 MB in
# both directions (https://vercel.com/docs/functions/limitations). A
# single file must fit that on the way OUT (we serve it whole), so it is
# capped well under it; MAX_POST_MEDIA_BYTES is the total per post the
# user sized against their Neon plan's 0.5 GB storage budget.
MAX_FILE_BYTES = 4 * 1024 * 1024          # 4 MiB per file
MAX_POST_MEDIA_BYTES = 50 * 1024 * 1024   # 50 MiB per post
CHUNK_RAW_BYTES = 2_097_150               # multiple of 3 -> clean base64 joins
ALLOWED_MIME_PREFIXES = ("image/",)       # video deliberately excluded for now:
                                          # a usable clip will not fit 4 MiB


def media_asset_out(row):
    return {
        "id": row["id"], "filename": row["filename"], "mime": row["mime"],
        "size_bytes": row["size_bytes"], "status": row["status"],
    }


ROLE_GRANTS = {
    "admin": {"post.create", "post.delete_own", "post.delete_any",
              "post.edit_own", "post.edit_any",
              "comment.create", "user.manage", "audit.read"},
    "contributor": {"post.create", "post.delete_own", "post.edit_own",
                    "comment.create"},
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
        "can_edit_own": can(row, "post.edit_own"),
        "can_edit_any": can(row, "post.edit_any"),
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
                "WHERE s.token = %s AND s.expires_at > %s",
                (hash_token(token), now_iso()))
    return cur.fetchone()


CSRF_COOKIE = "dental_info_csrf"

# Rate limits, counted in the database because serverless functions share
# no memory. Generous enough not to lock out a real person fat-fingering a
# password, tight enough that guessing is not viable.
RATE_WINDOW_MIN = 15
MAX_FAILS_PER_EMAIL = 8
MAX_FAILS_PER_IP = 20
MAX_SIGNUPS_PER_IP = 5


def hash_token(raw):
    """Sessions are stored hashed, so a database read cannot be replayed."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def client_ip(environ):
    fwd = environ.get("HTTP_X_FORWARDED_FOR", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return environ.get("REMOTE_ADDR") or "unknown"


def window_start(minutes=RATE_WINDOW_MIN):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)
            ).replace(microsecond=0).isoformat()


def record_attempt(cur, email, ip, ok):
    cur.execute("INSERT INTO login_attempts (id,email,ip,ok,created_at) "
                "VALUES (%s,%s,%s,%s,%s)",
                (new_id(), (email or "").lower().strip() or None, ip,
                 1 if ok else 0, now_iso()))


def rate_limited(cur, email, ip):
    """True when this email or IP has failed too often recently."""
    since = window_start()
    cur.execute("SELECT COUNT(*) AS n FROM login_attempts "
                "WHERE email = %s AND ok = 0 AND created_at > %s",
                ((email or "").lower().strip(), since))
    if cur.fetchone()["n"] >= MAX_FAILS_PER_EMAIL:
        return True
    cur.execute("SELECT COUNT(*) AS n FROM login_attempts "
                "WHERE ip = %s AND ok = 0 AND created_at > %s", (ip, since))
    return cur.fetchone()["n"] >= MAX_FAILS_PER_IP


def signup_limited(cur, ip):
    cur.execute("SELECT COUNT(*) AS n FROM login_attempts "
                "WHERE ip = %s AND email IS NOT NULL AND ok = 2 "
                "AND created_at > %s", (ip, window_start(60)))
    return cur.fetchone()["n"] >= MAX_SIGNUPS_PER_IP


def origin_ok(environ):
    """Reject cross-site state-changing requests.

    Browsers always send Origin on cross-origin POST/DELETE, so a mismatch
    is a genuine cross-site attempt. A missing Origin means a non-browser
    client (curl, our own tests), which cookies alone cannot be tricked into.
    """
    origin = environ.get("HTTP_ORIGIN")
    if not origin:
        return True
    host = environ.get("HTTP_HOST", "")
    return origin.split("://")[-1].lower() == host.lower()


def csrf_ok(environ, token):
    """Double-submit: the header must match the non-HttpOnly cookie.

    A cross-site page can make the browser send cookies, but cannot read
    them to set the matching header.
    """
    if not token:
        return True                      # anonymous request, nothing to protect
    sent = environ.get("HTTP_X_CSRF_TOKEN", "")
    raw = environ.get("HTTP_COOKIE") or ""
    jar = SimpleCookie()
    try:
        jar.load(raw)
    except Exception:
        return False
    m = jar.get(CSRF_COOKIE)
    if not (sent and m and m.value):
        return False
    return secrets.compare_digest(sent, m.value)


def create_session(cur, user_id):
    token = secrets.token_urlsafe(32)
    created = datetime.now(timezone.utc).replace(microsecond=0)
    cur.execute("INSERT INTO sessions (token,user_id,created_at,expires_at) "
                "VALUES (%s,%s,%s,%s)",
                (hash_token(token), user_id, created.isoformat(),
                 (created + SESSION_TTL).isoformat()))
    return token


# ── signup validation ──────────────────────────────────────────────────────
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")
MIN_PASSWORD = 10


def validate_signup(body):
    """Returns an error string, or None when the payload is acceptable."""
    email = (body.get("email") or "").strip().lower()
    name = (body.get("name") or "").strip()
    pw = body.get("password") or ""
    if not EMAIL_RE.match(email):
        return "Enter a valid email address"
    if len(name) < 2:
        return "Enter your full name"
    if len(pw) < MIN_PASSWORD:
        return "Password must be at least %d characters" % MIN_PASSWORD
    if pw.lower() in (email, email.split("@")[0], name.lower()):
        return "Password must not be your name or email"
    if len(set(pw)) < 4:
        return "Password is too repetitive"
    return None


def create_user(cur, email, name, password, role="reader", credential=None,
                location=None, verification_status="unverified"):
    uid = new_id()
    pw_hash, salt, iters = hash_password(password)
    ts = now_iso()
    cur.execute(
        """INSERT INTO users (id,email,name,credential,location,
                              password_hash,password_salt,password_iterations,
                              role,verification_status,created_at,updated_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (uid, email.strip().lower(), name.strip(), credential, location,
         pw_hash, salt, iters, role, verification_status, ts, ts))
    return uid


# ── registry cross-check ───────────────────────────────────────────────────
# No licensing-registry integration exists yet, and CLAUDE.md leaves the
# launch region open. Rather than fake a result, report honestly that the
# automatic check could not run, and name the registry a human should use.
REGISTRIES = {
    "india": "Dental Council of India / state dental council",
    "in": "Dental Council of India / state dental council",
    "united states": "State dental board / NPI registry",
    "us": "State dental board / NPI registry",
    "usa": "State dental board / NPI registry",
    "united kingdom": "General Dental Council (GDC)",
    "uk": "General Dental Council (GDC)",
}


def validate_media(cur, owner_id, media_list):
    """Returns an error string, or None when the media list is acceptable."""
    if not isinstance(media_list, list):
        return "media must be a list"
    total = 0
    for item in media_list:
        if not isinstance(item, dict):
            return "Each media item must be an object"
        url = item.get("url") or ""
        if not url.startswith("/api/media/"):
            continue   # an external link -- not ours to check or charge for
        aid = url.rsplit("/", 1)[-1]
        cur.execute("SELECT owner_id, status, size_bytes FROM media_assets "
                    "WHERE id = %s", (aid,))
        asset = cur.fetchone()
        if not asset:
            return "One of the attached files no longer exists"
        if asset["owner_id"] != owner_id:
            return "You can only attach your own uploads"
        if asset["status"] != "complete":
            return "One of the attached files did not finish uploading"
        total += asset["size_bytes"]
    if total > MAX_POST_MEDIA_BYTES:
        return ("Attached media totals %.1f MB, over the %d MB limit per case"
                % (total / (1024 * 1024), MAX_POST_MEDIA_BYTES // (1024 * 1024)))
    return None


def registry_check(country):
    key = (country or "").strip().lower()
    who = REGISTRIES.get(key)
    if who:
        return "unavailable", ("No automated integration yet. Verify manually against: %s" % who)
    return "unavailable", ("No registry integration for %r. Verify manually against the "
                           "issuing board named on the certificate." % (country or "unknown"))


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
        for c in (cookie if isinstance(cookie, (list, tuple)) else [cookie]):
            headers.append(("Set-Cookie", c))
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


def _csrf_cookie(value, environ, clear=False):
    """Readable by JS on purpose -- the frontend echoes it back in a header."""
    https = environ.get("HTTP_X_FORWARDED_PROTO", "http") == "https"
    parts = ["%s=%s" % (CSRF_COOKIE, "" if clear else value),
             "Path=/", "SameSite=Lax"]
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
                raw_token = _token(environ)
                me = user_for_token(cur, raw_token)

                # CSRF: only state-changing methods need protecting, and a
                # GET must never mutate anything.
                if method in ("POST", "PUT", "PATCH", "DELETE"):
                    if not origin_ok(environ):
                        return _err(start_response, 403,
                                    "Cross-site request blocked")
                    if me is not None and not csrf_ok(environ, raw_token):
                        return _err(start_response, 403,
                                    "Missing or invalid CSRF token. Reload the page "
                                    "and try again.")

                if path == "/api/me":
                    return _json(start_response, {"user": public_user(me)})

                if path == "/api/login":
                    if method != "POST":
                        return _err(start_response, 405, "Use POST")
                    body = _body(environ)
                    if body is None:
                        return _err(start_response, 400, "Body must be valid JSON")
                    ip = client_ip(environ)
                    attempted = body.get("email") or ""
                    if rate_limited(cur, attempted, ip):
                        # deliberately says nothing about whether the account exists
                        return _err(start_response, 429,
                                    "Too many failed sign-in attempts. Wait %d minutes "
                                    "and try again." % RATE_WINDOW_MIN)
                    row = get_user_by_email(cur, attempted)
                    if row is None or not verify_password(body.get("password") or "", row):
                        record_attempt(cur, attempted, ip, ok=False)
                        return _err(start_response, 401, "Incorrect email or password")
                    record_attempt(cur, attempted, ip, ok=True)
                    token = create_session(cur, row["id"])
                    csrf = secrets.token_urlsafe(24)
                    audit(cur, row["id"], "login", row["email"])
                    return _json(start_response, {"user": public_user(row)},
                                 cookie=[_cookie(token, environ),
                                         _csrf_cookie(csrf, environ)])

                if path == "/api/logout":
                    if raw_token:
                        cur.execute("DELETE FROM sessions WHERE token = %s",
                                    (hash_token(raw_token),))
                    return _json(start_response, {"ok": True},
                                 cookie=[_cookie(None, environ, clear=True),
                                         _csrf_cookie(None, environ, clear=True)])

                # -- signup ------------------------------------------------
                if path == "/api/signup":
                    if method != "POST":
                        return _err(start_response, 405, "Use POST")
                    body = _body(environ)
                    if body is None:
                        return _err(start_response, 400, "Body must be valid JSON")
                    problem = validate_signup(body)
                    if problem:
                        return _err(start_response, 400, problem)
                    email = body["email"].strip().lower()
                    if get_user_by_email(cur, email):
                        return _err(start_response, 409,
                                    "That email is already registered. Try signing in.")
                    ip = client_ip(environ)
                    if signup_limited(cur, ip):
                        return _err(start_response, 429,
                                    "Too many accounts created from this network. "
                                    "Try again later.")
                    uid = create_user(cur, email, body["name"], body["password"],
                                      role="reader",
                                      credential=(body.get("credential") or "").strip() or None,
                                      location=(body.get("location") or "").strip() or None)
                    audit(cur, uid, "user.signup", email)
                    # ok=2 marks a signup, distinct from a login attempt
                    cur.execute("INSERT INTO login_attempts (id,email,ip,ok,created_at) "
                                "VALUES (%s,%s,%s,2,%s)",
                                (new_id(), email, ip, now_iso()))
                    cur.execute("SELECT * FROM users WHERE id = %s", (uid,))
                    row = cur.fetchone()
                    token = create_session(cur, uid)   # sign them straight in
                    csrf = secrets.token_urlsafe(24)
                    return _json(start_response, {"user": public_user(row)}, 201,
                                 cookie=[_cookie(token, environ),
                                         _csrf_cookie(csrf, environ)])

                # -- media: start an upload ---------------------------------
                if path == "/api/media/start":
                    if method != "POST":
                        return _err(start_response, 405, "Use POST")
                    if me is None:
                        return _err(start_response, 401, "Not signed in")
                    if not can(me, "post.create"):
                        return _err(start_response, 403,
                                    "Only verified contributors can upload media")
                    body = _body(environ)
                    if body is None:
                        return _err(start_response, 400, "Body must be valid JSON")
                    filename = (body.get("filename") or "upload").strip()[:200]
                    mime = (body.get("mime") or "").strip().lower()
                    try:
                        size = int(body.get("size_bytes") or 0)
                    except (TypeError, ValueError):
                        return _err(start_response, 400, "size_bytes must be a number")
                    if not any(mime.startswith(p) for p in ALLOWED_MIME_PREFIXES):
                        return _err(start_response, 400,
                                    "Only image uploads are supported right now")
                    if size <= 0 or size > MAX_FILE_BYTES:
                        return _err(start_response, 400,
                                    "Each file must be under %d MB"
                                    % (MAX_FILE_BYTES // (1024 * 1024)))
                    aid = new_id()
                    chunks_total = (size + CHUNK_RAW_BYTES - 1) // CHUNK_RAW_BYTES
                    cur.execute(
                        """INSERT INTO media_assets
                             (id,owner_id,filename,mime,size_bytes,data,
                              chunks_received,chunks_total,status,created_at)
                           VALUES (%s,%s,%s,%s,%s,''::bytea,0,%s,'uploading',%s)""",
                        (aid, me["id"], filename, mime, size, chunks_total, now_iso()))
                    return _json(start_response, {
                        "id": aid, "chunk_size": CHUNK_RAW_BYTES,
                        "chunks_total": chunks_total,
                    }, 201)

                # -- media: send one chunk -----------------------------------
                if path == "/api/media/chunk":
                    if method != "POST":
                        return _err(start_response, 405, "Use POST")
                    if me is None:
                        return _err(start_response, 401, "Not signed in")
                    body = _body(environ)
                    if body is None:
                        return _err(start_response, 400, "Body must be valid JSON")
                    aid = (body.get("id") or "").strip()
                    try:
                        index = int(body.get("index"))
                    except (TypeError, ValueError):
                        return _err(start_response, 400, "index must be a number")
                    b64 = body.get("data_base64") or ""

                    cur.execute("SELECT * FROM media_assets WHERE id = %s", (aid,))
                    asset = cur.fetchone()
                    if not asset:
                        return _err(start_response, 404, "Upload not found")
                    if asset["owner_id"] != me["id"]:
                        return _err(start_response, 403, "Not your upload")
                    if asset["status"] != "uploading":
                        return _err(start_response, 409,
                                    "This upload is already %s" % asset["status"])
                    if index != asset["chunks_received"]:
                        return _err(start_response, 409,
                                    "Expected chunk %d, got %d (chunks must arrive in order)"
                                    % (asset["chunks_received"], index))
                    try:
                        raw = base64.b64decode(b64, validate=True)
                    except Exception:
                        return _err(start_response, 400, "Invalid base64 chunk")

                    received = asset["chunks_received"] + 1
                    cur.execute(
                        "UPDATE media_assets SET data = data || %s::bytea, "
                        "chunks_received = %s WHERE id = %s",
                        (raw, received, aid))

                    if received >= asset["chunks_total"]:
                        cur.execute("SELECT octet_length(data) AS n FROM media_assets "
                                    "WHERE id = %s", (aid,))
                        actual = cur.fetchone()["n"]
                        if actual != asset["size_bytes"]:
                            cur.execute("UPDATE media_assets SET status='abandoned' "
                                        "WHERE id = %s", (aid,))
                            return _err(start_response, 400,
                                        "Upload corrupted in transit (got %d bytes, "
                                        "expected %d). Try again." % (actual, asset["size_bytes"]))
                        cur.execute("UPDATE media_assets SET status='complete' "
                                    "WHERE id = %s", (aid,))
                        audit(cur, me["id"], "media.upload", aid, asset["filename"])
                        return _json(start_response, {"done": True, "id": aid})

                    return _json(start_response,
                                 {"done": False, "chunks_received": received})

                # -- media: fetch a file --------------------------------------
                if path.startswith("/api/media/") and not path.endswith(("/start", "/chunk")):
                    if method != "GET":
                        return _err(start_response, 405, "Use GET")
                    aid = path.rsplit("/", 1)[-1]
                    cur.execute("SELECT * FROM media_assets WHERE id = %s AND "
                                "status = 'complete'", (aid,))
                    asset = cur.fetchone()
                    if not asset:
                        return _err(start_response, 404, "Not found")
                    data = bytes(asset["data"])
                    start_response("200 OK", [
                        ("Content-Type", asset["mime"]),
                        ("Content-Length", str(len(data))),
                        # content-addressed by a uuid -> never changes -> cache forever
                        ("Cache-Control", "public, max-age=31536000, immutable"),
                    ])
                    return [data]

                # -- verification: submit / read own status -----------------
                if path == "/api/verification":
                    if me is None:
                        return _err(start_response, 401, "Not signed in")

                    if method == "GET":
                        cur.execute(
                            "SELECT * FROM verification_requests WHERE user_id = %s "
                            "ORDER BY created_at DESC LIMIT 1", (me["id"],))
                        r = cur.fetchone()
                        return _json(start_response, {"request": dict(r) if r else None})

                    if method != "POST":
                        return _err(start_response, 405, "Use GET or POST")

                    body = _body(environ)
                    if body is None:
                        return _err(start_response, 400, "Body must be valid JSON")
                    num = (body.get("license_number") or "").strip()
                    board = (body.get("license_board") or "").strip()
                    country = (body.get("license_country") or "").strip()
                    if len(num) < 3:
                        return _err(start_response, 400, "Enter your license number")
                    if len(board) < 3:
                        return _err(start_response, 400, "Enter the issuing board or council")
                    if len(country) < 2:
                        return _err(start_response, 400, "Enter the country of registration")

                    cur.execute("SELECT 1 FROM verification_requests "
                                "WHERE user_id = %s AND status = 'pending'", (me["id"],))
                    if cur.fetchone():
                        return _err(start_response, 409,
                                    "You already have a verification request under review.")

                    check, detail = registry_check(country)
                    rid = new_id()
                    ts = now_iso()
                    cur.execute(
                        """INSERT INTO verification_requests
                             (id,user_id,license_number,license_board,license_country,
                              credential,registry_check,registry_detail,document_note,
                              status,created_at)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending',%s)""",
                        (rid, me["id"], num, board, country,
                         (body.get("credential") or "").strip() or None,
                         check, detail,
                         (body.get("document_note") or "").strip() or None, ts))
                    cur.execute(
                        "UPDATE users SET verification_status='pending', license_number=%s,"
                        " license_board=%s, license_country=%s,"
                        " credential=COALESCE(%s,credential), updated_at=%s WHERE id=%s",
                        (num, board, country,
                         (body.get("credential") or "").strip() or None, ts, me["id"]))
                    audit(cur, me["id"], "verification.submit", rid, board)
                    cur.execute("SELECT * FROM verification_requests WHERE id = %s", (rid,))
                    return _json(start_response, {"request": dict(cur.fetchone())}, 201)

                # -- the review queue (admin) ------------------------------
                if path == "/api/verification/queue":
                    if me is None:
                        return _err(start_response, 401, "Not signed in")
                    if not can(me, "user.manage"):
                        return _err(start_response, 403, "Admin only")
                    cur.execute(
                        """SELECT v.*, u.name AS user_name, u.email AS user_email,
                                  u.location AS user_location, u.role AS user_role
                             FROM verification_requests v
                             JOIN users u ON u.id = v.user_id
                            WHERE v.status = 'pending'
                         ORDER BY v.created_at ASC""")
                    return _json(start_response,
                                 {"requests": [dict(r) for r in cur.fetchall()]})

                # -- approve / reject (admin) ------------------------------
                if path.startswith("/api/verification/") and path.endswith("/decide"):
                    if method != "POST":
                        return _err(start_response, 405, "Use POST")
                    if me is None:
                        return _err(start_response, 401, "Not signed in")
                    if not can(me, "user.manage"):
                        return _err(start_response, 403, "Admin only")
                    rid = path.split("/")[3]
                    body = _body(environ) or {}
                    decision = (body.get("decision") or "").strip()
                    if decision not in ("approve", "reject"):
                        return _err(start_response, 400,
                                    "decision must be 'approve' or 'reject'")
                    note = (body.get("note") or "").strip() or None
                    if decision == "reject" and not note:
                        return _err(start_response, 400,
                                    "A rejection needs a reason the applicant can act on")

                    cur.execute("SELECT * FROM verification_requests WHERE id = %s", (rid,))
                    vreq = cur.fetchone()
                    if not vreq:
                        return _err(start_response, 404, "Request not found")
                    if vreq["status"] != "pending":
                        return _err(start_response, 409,
                                    "That request was already %s" % vreq["status"])

                    ts = now_iso()
                    if decision == "approve":
                        due = (datetime.now(timezone.utc) + timedelta(days=365)
                               ).replace(microsecond=0).isoformat()
                        cur.execute(
                            "UPDATE users SET verification_status='verified',"
                            " role = CASE WHEN role='admin' THEN 'admin' ELSE 'contributor' END,"
                            " verified_at=%s, reverify_due=%s, updated_at=%s WHERE id=%s",
                            (ts, due, ts, vreq["user_id"]))
                        cur.execute("UPDATE verification_requests SET status='approved',"
                                    " reviewer_id=%s, reviewer_note=%s, decided_at=%s"
                                    " WHERE id=%s", (me["id"], note, ts, rid))
                    else:
                        cur.execute("UPDATE users SET verification_status='unverified',"
                                    " updated_at=%s WHERE id=%s", (ts, vreq["user_id"]))
                        cur.execute("UPDATE verification_requests SET status='rejected',"
                                    " reviewer_id=%s, reviewer_note=%s, decided_at=%s"
                                    " WHERE id=%s", (me["id"], note, ts, rid))

                    audit(cur, me["id"], "verification." + decision, rid,
                          vreq["license_board"])
                    cur.execute("SELECT * FROM verification_requests WHERE id = %s", (rid,))
                    return _json(start_response, {"request": dict(cur.fetchone())})

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
                        media_problem = validate_media(cur, me["id"], body.get("media") or [])
                        if media_problem:
                            return _err(start_response, 400, media_problem)

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
                    if method in ("PUT", "PATCH"):
                        if me is None:
                            return _err(start_response, 401, "Not signed in")
                        if not post:
                            return _err(start_response, 404, "Case not found")
                        own = post["author"]["id"] == me["id"]
                        if not (can(me, "post.edit_any")
                                or (own and can(me, "post.edit_own"))):
                            return _err(start_response, 403,
                                        "You can only edit your own cases"
                                        if can(me, "post.edit_own")
                                        else "Your account is read-only and cannot edit cases")
                        body = _body(environ)
                        if body is None:
                            return _err(start_response, 400, "Body must be valid JSON")
                        if "title" in body and not (body.get("title") or "").strip():
                            return _err(start_response, 400, "A title is required")
                        if "procedure" in body and not (body.get("procedure") or "").strip():
                            return _err(start_response, 400, "A procedure type is required")
                        if body.get("difficulty") not in (None, "Low", "Medium", "High"):
                            return _err(start_response, 400, "Invalid difficulty")
                        if "media" in body:
                            media_problem = validate_media(cur, me["id"], body.get("media") or [])
                            if media_problem:
                                return _err(start_response, 400, media_problem)

                        # Only touch what was sent, so a partial edit cannot
                        # silently blank fields the form did not include.
                        sets, vals = [], []
                        text_cols = {"title": "title", "summary": "summary",
                                     "procedure": "procedure",
                                     "procedurePath": "procedure_path",
                                     "difficulty": "difficulty",
                                     "presentation": "presentation",
                                     "unusual": "unusual", "outcome": "outcome"}
                        for key, col in text_cols.items():
                            if key in body:
                                v = body.get(key)
                                sets.append("%s = %%s" % col)
                                vals.append(v.strip() if isinstance(v, str) else v)
                        for key in JSON_FIELDS:
                            if key in body:
                                sets.append("%s = %%s" % key)
                                vals.append(psycopg2.extras.Json(body.get(key) or []))
                        if not sets:
                            return _err(start_response, 400, "Nothing to update")
                        sets.append("updated_at = %s")
                        vals.append(now_iso())
                        vals.append(post_id)
                        cur.execute("UPDATE posts SET " + ", ".join(sets)
                                    + " WHERE id = %s", tuple(vals))
                        audit(cur, me["id"], "post.update", post_id,
                              body.get("title") or post["title"])
                        cur.execute(_POST_SELECT + " WHERE p.id = %s", (post_id,))
                        out = _post_out(cur.fetchone())
                        _attach_comments(cur, [out])
                        return _json(start_response, {"post": out})

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
                    return _err(start_response, 405,
                                "Use GET, PUT or DELETE")

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
