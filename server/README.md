# Backend — SQLite + a stdlib Python server

No dependencies. Nothing to `pip install`. Python 3.11+ (you have 3.14).

## Run it

```bash
python server/seed.py      # once — creates accounts, cases, ACCOUNTS.txt
python server/app.py       # start the server
```

Then open **http://localhost:8000**.

Credentials are written to `ACCOUNTS.txt` at the repo root (gitignored).

> The app no longer works by double-clicking `prototype/index.html` — it needs
> the API. Opening the file directly shows a "Cannot reach the API" screen.

## Why SQLite

| | |
|---|---|
| **Zero setup** | A file. No service, no Docker, no account to create. |
| **Real SQL** | Same queries, constraints and joins you'd write against Postgres. |
| **Right size** | You're testing whether posts add and delete correctly, not load-balancing. |

**It will not work on Vercel.** Vercel's filesystem is read-only and per-request
ephemeral, so writes vanish and concurrent instances see different files. Deploying
the backend means moving to hosted Postgres — **Neon** or **Supabase** are the
natural fits (Supabase also gives you file storage for radiographs and video,
which this project needs).

The schema was written for that move:

- no SQLite-only types — `TEXT` / `INTEGER` only
- timestamps are ISO-8601 UTC strings
- list fields hold JSON text → becomes `JSONB`
- ids are uuid4 strings, not `AUTOINCREMENT`, so rows merge across environments

Everything DB-specific lives in `store.py`. Porting means changing `connect()`
and the placeholder style (`?` → `%s`).

## Files

| File | Role |
|---|---|
| `schema.sql` | Tables: users, sessions, posts, comments, audit_log |
| `store.py` | Connection, password hashing, sessions, queries, **the permission gate** |
| `app.py` | HTTP server: static files + JSON API |
| `seed.py` | Creates accounts and imports the example cases |
| `seed_cases.json` | The 8 example cases, extracted from the old `data.js` |

## API

| Method | Path | Notes |
|---|---|---|
| POST | `/api/login` | `{email, password}` → sets an HttpOnly session cookie |
| POST | `/api/logout` | clears the session |
| GET | `/api/me` | current user, or `{"user": null}` |
| GET | `/api/posts` | published cases (admins also see drafts) |
| GET | `/api/posts/:id` | one case |
| POST | `/api/posts` | create — needs `post.create` |
| PUT | `/api/posts/:id` | edit — own case, or any case for an admin |
| DELETE | `/api/posts/:id` | delete — own case, or any case for an admin |
| POST | `/api/media/start` | begin an upload — `{filename, mime, size_bytes}`, 4 MB/file cap |
| POST | `/api/media/chunk` | send one chunk — `{id, index, data_base64}`, in order |
| GET | `/api/media/:id` | fetch a completed upload |
| GET | `/api/users` | admin only |
| GET | `/api/audit` | admin only |

See `MEDIA.md` at the repo root for why uploads are chunked, why the two size
limits (4 MB/file, 50 MB/case) are what they are, and a SQLite blob-concat bug
it surfaced.

## Permissions

Roles are **enforced by default**. Every check runs through one function:

```python
# store.py
PERMISSIVE_MODE = os.environ.get("DENTAL_INFO_PERMISSIVE", "") in ("1","true","yes")

def can(user_row, action):
    if PERMISSIVE_MODE:      # debugging escape hatch, off by default
        return True
    return action in ROLE_GRANTS.get(user_row["role"], set())
```

Deleting also checks **ownership**, separately from the role grant, so a
contributor cannot touch another dentist's case.

Bypass every check while debugging (not the default):

```powershell
$env:DENTAL_INFO_PERMISSIVE=1 ; python server/app.py   # PowerShell
DENTAL_INFO_PERMISSIVE=1 python server/app.py          # bash
```

Verified behaviour:

| | admin | contributor | reader |
|---|---|---|---|
| publish a case | yes | yes | **403** |
| delete own case | yes | yes | **403** |
| delete someone else's | yes | **403** | **403** |
| read audit log | yes | **403** | **403** |

Keeping roles in the model is deliberate — CLAUDE.md requires contributor to stay
distinct from a read-only user everywhere in the data model and permissions.

## Security notes

- **Passwords**: PBKDF2-HMAC-SHA256, 240k iterations, per-user salt,
  constant-time compare. Never stored or logged in plain text.
- **Sessions**: 32-byte random tokens, stored **SHA-256 hashed**. The raw value
  exists only in the cookie, so a database read cannot be replayed as a live
  session. `HttpOnly`, `SameSite=Lax`, `Secure` over HTTPS, 7-day expiry.
- **CSRF**: state-changing requests need an `X-CSRF-Token` header matching a
  readable `dental_info_csrf` cookie (double-submit). A cross-site page can make
  the browser send cookies but cannot read them to forge the header. Plus an
  `Origin` check that rejects cross-site POST/DELETE outright.
- **Rate limiting**, counted in the database because serverless functions share
  no memory: 8 failed sign-ins per email and 20 per IP per 15 minutes; 5 signups
  per IP per hour. Exceeding returns 429.
- **No account enumeration**: unknown email and wrong password return the same
  message, and so does a rate-limited attempt.
- Binds to `127.0.0.1` only.

Still outstanding before real users: email verification of the address, password
reset, and a review of how long sessions should live.

## Reset

```bash
python server/seed.py --reset     # wipe the DB and rebuild
```
