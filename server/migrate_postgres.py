"""
Create and seed the Postgres (Neon) database used by the deployed app.

Run this ONCE from your machine after creating the Neon project:

    pip install psycopg2-binary
    # PowerShell:
    $env:DATABASE_URL="postgresql://...-pooler...neon.tech/neondb?sslmode=require"
    python server/migrate_postgres.py

    # bash:
    DATABASE_URL="postgresql://..." python server/migrate_postgres.py

It applies server/schema_postgres.sql, creates the same accounts as the local
seeder, and imports the example cases -- so the deployed site starts with the
same content and the same credentials as localhost.

Flags:
    --reset     DROP the tables first, then rebuild (destructive)
    --check     connect and report counts, change nothing
"""

import json
import os
import sys
from pathlib import Path

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    sys.exit("psycopg2 is not installed.  Run:  pip install psycopg2-binary")

BASE_DIR = Path(__file__).resolve().parent
SCHEMA = BASE_DIR / "schema_postgres.sql"
SEED_CASES = BASE_DIR / "seed_cases.json"

# reuse the account definitions and password hashing from the local seeder
sys.path.insert(0, str(BASE_DIR))
import store            # noqa: E402  (hash_password, new_id, now_iso)
from seed import ACCOUNTS  # noqa: E402  (single source of truth for accounts)

TABLES = ["audit_log", "comments", "sessions", "posts", "users"]


def get_url():
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        sys.exit(
            "DATABASE_URL is not set.\n\n"
            "  Create a free project at https://neon.tech, copy the POOLED\n"
            "  connection string (the host contains '-pooler'), then:\n\n"
            '    $env:DATABASE_URL="postgresql://...-pooler...?sslmode=require"\n'
        )
    if "-pooler" not in url:
        print("  ! warning: this looks like Neon's DIRECT url.\n"
              "    Serverless functions should use the POOLED one "
              "(host contains '-pooler').\n")
    return url


def connect(url):
    conn = psycopg2.connect(url, connect_timeout=10,
                            cursor_factory=psycopg2.extras.RealDictCursor)
    conn.autocommit = True
    return conn


def counts(cur):
    out = {}
    for t in ("users", "posts", "sessions", "audit_log"):
        try:
            cur.execute(f"SELECT COUNT(*) AS n FROM {t}")
            out[t] = cur.fetchone()["n"]
        except psycopg2.Error:
            out[t] = None
    return out


def create_user(cur, acct):
    pw_hash, salt, iters = store.hash_password(acct["password"])
    ts = store.now_iso()
    uid = store.new_id()
    verified = acct["verification_status"] == "verified"
    cur.execute(
        """INSERT INTO users (id,email,name,credential,location,
                              password_hash,password_salt,password_iterations,
                              role,verification_status,license_number,license_board,
                              license_country,verified_at,reverify_due,
                              created_at,updated_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (uid, acct["email"].lower(), acct["name"], acct["credential"], acct["location"],
         pw_hash, salt, iters, acct["role"], acct["verification_status"],
         acct["license_number"], acct["license_board"], acct["license_country"],
         ts if verified else None, None, ts, ts))
    return uid


def seed_accounts(cur):
    for acct in ACCOUNTS:
        cur.execute("SELECT id, role FROM users WHERE email = %s", (acct["email"].lower(),))
        row = cur.fetchone()
        if row:
            print(f"  account exists   {acct['email']}  ({row['role']})")
            continue
        create_user(cur, acct)
        print(f"  created account  {acct['email']}  ({acct['role']})")


def seed_cases(cur):
    if not SEED_CASES.exists():
        print(f"  ! {SEED_CASES.name} missing, skipping cases")
        return
    cases = json.loads(SEED_CASES.read_text(encoding="utf-8"))
    made = 0
    for case in cases:
        cur.execute("SELECT 1 FROM posts WHERE id = %s", (case["id"],))
        if cur.fetchone():
            continue

        author = case.get("author") or {}
        name = author.get("name") or "Unknown"
        email = name.lower().replace("dr. ", "").replace(" ", ".") + "@dentalinfo.test"
        cur.execute("SELECT id FROM users WHERE email = %s", (email,))
        row = cur.fetchone()
        if row:
            author_id = row["id"]
        else:
            author_id = create_user(cur, {
                "email": email, "name": name,
                "password": store.secrets.token_urlsafe(24),
                "credential": author.get("credential"), "location": author.get("location"),
                "role": "contributor",
                "verification_status": "verified" if author.get("verified") else "unverified",
                "license_number": None, "license_board": None, "license_country": None,
            })

        ts = (case.get("date") or "") + "T09:00:00+00:00"
        cur.execute(
            """INSERT INTO posts (id,author_id,title,summary,procedure,procedure_path,
                                  difficulty,complications,tools,resolution,takeaways,media,
                                  presentation,unusual,outcome,status,reads,saves,
                                  created_at,updated_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (case["id"], author_id, case["title"], case.get("summary"),
             case["procedure"], case.get("procedurePath"), case.get("difficulty", "Medium"),
             psycopg2.extras.Json(case.get("complications", [])),
             psycopg2.extras.Json(case.get("tools", [])),
             psycopg2.extras.Json(case.get("resolution", [])),
             psycopg2.extras.Json(case.get("takeaways", [])),
             psycopg2.extras.Json(case.get("media", [])),
             case.get("presentation"), case.get("unusual"), case.get("outcome"),
             "published", int(case.get("reads", 0)), int(case.get("saves", 0)), ts, ts))
        made += 1
    print(f"  imported cases   {made}")


def main():
    url = get_url()
    host = url.split("@")[-1].split("/")[0] if "@" in url else "?"
    print(f"\n  target: {host}")

    conn = connect(url)
    try:
        with conn.cursor() as cur:
            if "--check" in sys.argv:
                print("  counts:", counts(cur))
                return

            if "--reset" in sys.argv:
                print("\n  dropping tables")
                for t in TABLES:
                    cur.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
                    print(f"    dropped {t}")

            print("\n  applying schema")
            cur.execute(SCHEMA.read_text(encoding="utf-8"))

            print("\n  seeding")
            seed_accounts(cur)
            seed_cases(cur)

            print("\n  counts:", counts(cur))
    finally:
        conn.close()

    print("\n  done. Set the same DATABASE_URL in your Vercel project's")
    print("  environment variables, then redeploy.\n")


if __name__ == "__main__":
    main()
