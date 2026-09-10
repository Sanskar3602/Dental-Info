# Deploying to Vercel with Neon Postgres

The deployed site currently shows **"This build needs its backend"** because the
frontend expects an API and Vercel has none. This fixes that.

Everything in the repo is ready. Three steps are yours, because they need an
account I can't create.

---

## Why not SQLite

Vercel's filesystem is **read-only and per-request ephemeral**. A SQLite file
can't be written to, and each concurrent function instance would see its own
copy. Hosted Postgres is the requirement, not a preference.

`server/` (SQLite) stays for local development. `api/` (Postgres) is what runs
on Vercel. Both return identical JSON, so the frontend is unchanged.

---

## Step 1 — Create the Neon database  *(~2 minutes, free, no card)*

1. Go to **https://neon.tech** and sign up (GitHub login works).
2. Create a project — any name, e.g. `dental-info`. Pick the region closest to
   you (`ap-southeast-1` for India).
3. On the dashboard, find **Connection string** and copy the **Pooled
   connection** — the hostname contains **`-pooler`**.

```
postgresql://neondb_owner:PASSWORD@ep-xxx-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require
                                          ^^^^^^^ must be there
```

> Use the **pooled** string. Serverless functions open a connection per
> request; the direct URL will exhaust connection slots under any real traffic.

---

## Step 2 — Create the tables and seed the accounts

From the repo root, on your machine:

```powershell
pip install psycopg2-binary

$env:DATABASE_URL="postgresql://...-pooler...neon.tech/neondb?sslmode=require"
python server/migrate_postgres.py
```

Expected output:

```
  target: ep-xxx-pooler.ap-southeast-1.aws.neon.tech
  applying schema
  seeding
  created account  admin@dentalinfo.test  (admin)
  created account  test@dentalinfo.test  (contributor)
  created account  reader@dentalinfo.test  (reader)
  imported cases   8
  counts: {'users': 10, 'posts': 8, ...}
```

Verify any time without changing anything:

```powershell
python server/migrate_postgres.py --check
```

The accounts and passwords are the same as local — see `ACCOUNTS.txt`.

---

## Step 3 — Give Vercel the connection string, then push

1. Vercel dashboard → your **dental-info** project → **Settings → Environment
   Variables**.
2. Add:

   | Key | Value | Environments |
   |---|---|---|
   | `DATABASE_URL` | the same pooled string from Step 1 | Production, Preview, Development |

3. Commit and push:

```bash
git add .
git commit -m "Add Postgres API for Vercel"
git push
```

Vercel builds on push, installs `requirements.txt`, and exposes `api/index.py`.

---

## Verify it worked

```bash
curl https://dental-info.vercel.app/api/health
```

| Response | Meaning |
|---|---|
| `{"ok":true,"users":10,"posts":8,...}` | Working. Go sign in. |
| `{"error":"DATABASE_URL is not set..."}` | Step 3 missed, or no redeploy after adding the variable |
| `{"error":"Could not connect..."}` | Wrong string, or you used the non-pooled URL |
| Vercel HTML 404 | `vercel.json` didn't deploy, or the build failed — check the build log |
| `FUNCTION_INVOCATION_FAILED` (500) | The function crashed before running. Was caused by a sibling `import _db`; that module is now inlined into `api/index.py`. If it recurs, check the Vercel build log for a failed `psycopg2-binary` install. |

Then open **https://dental-info.vercel.app** and sign in with the admin account.

---

## What's in the repo

| Path | Role |
|---|---|
| `vercel.json` | Rewrites `/api/*` to the one Python function |
| `requirements.txt` | `psycopg2-binary` (deployed only — local needs nothing) |
| `api/index.py` | The whole API as a WSGI app — **one self-contained file**, no sibling imports (those crashed on Vercel) |
| `server/schema_postgres.sql` | Postgres schema — JSONB list columns, GIN indexes |
| `server/migrate_postgres.py` | Applies the schema and seeds; `--reset`, `--check` |
| `server/` (rest) | The local SQLite server, unchanged |

Accounts are defined once in `server/seed.py` and imported by the Postgres
migration, so local and deployed credentials can't drift.

---

## Before this is genuinely public

Fine for testing; **not** hardened for real users:

- **No rate limiting on `/api/login`.** Add it before the URL is shared widely.
- **No CSRF protection** on state-changing routes. The cookie is `SameSite=Lax`,
  which blocks the common cross-site cases but is not a substitute.
- **Permissions are enforced**: admin deletes any case, a contributor deletes
  only their own, a read-only account deletes nothing. `DENTAL_INFO_PERMISSIVE=1`
  bypasses this for debugging — do not set it in production.
- **Change the seeded passwords.** They're in a gitignored file, but they're
  known-weak and documented in this repo's history.
- Session tokens are stored unhashed; a database read would expose live
  sessions. Hash them like passwords before production.

## Reset the deployed data

```powershell
python server/migrate_postgres.py --reset    # destructive: drops and rebuilds
```
