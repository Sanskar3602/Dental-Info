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
| DELETE | `/api/posts/:id` | delete — own case, or any case for an admin |
| GET | `/api/users` | admin only |
| GET | `/api/audit` | admin only |

## Permissions

Right now **every signed-in account has full access**, as requested. That is one
flag, not a missing feature — roles are stored per account and every check runs
through a single function:

```python
# store.py
PERMISSIVE_MODE = os.environ.get("DENTAL_INFO_STRICT", "") not in ("1","true","yes")

def can(user_row, action):
    if PERMISSIVE_MODE:
        return True          # ← the override
    return action in ROLE_GRANTS.get(user_row["role"], set())
```

Turn real enforcement on:

```powershell
$env:DENTAL_INFO_STRICT=1 ; python server/app.py     # PowerShell
DENTAL_INFO_STRICT=1 python server/app.py            # bash
```

Verified behaviour with it on:

| | admin | contributor | reader |
|---|---|---|---|
| publish a case | yes | yes | **403** |
| delete own case | yes | yes | — |
| delete someone else's | yes | **403** | — |
| read audit log | yes | **403** | **403** |

Keeping roles in the model is deliberate — CLAUDE.md requires contributor to stay
distinct from a read-only user everywhere in the data model and permissions.

## Security notes

Appropriate for local development, **not** for exposure to the internet:

- Passwords: PBKDF2-HMAC-SHA256, 240k iterations, per-user salt, constant-time
  compare. Never stored or logged in plain text.
- Sessions: 32-byte random tokens, `HttpOnly`, `SameSite=Lax`, 7-day expiry.
- Login failures return one message for both unknown email and wrong password,
  so the endpoint doesn't enumerate accounts.
- Binds to `127.0.0.1` only.

Before anyone else can reach this you need: HTTPS, rate limiting on `/api/login`,
CSRF protection on state-changing routes, and `Secure` on the cookie.

## Reset

```bash
python server/seed.py --reset     # wipe the DB and rebuild
```
