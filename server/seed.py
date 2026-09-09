"""
Dental Info — seed the database.

    python server/seed.py           # create accounts + cases if missing
    python server/seed.py --reset   # wipe and rebuild from scratch

Creates the admin and test accounts, imports the 8 example cases, and
writes the credentials to ACCOUNTS.txt at the repo root.

ACCOUNTS.txt is gitignored. These are throwaway local development
passwords -- do not reuse them anywhere real, and do not commit them.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import store  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parent
SEED_CASES = BASE_DIR / "seed_cases.json"
ACCOUNTS_FILE = REPO_ROOT / "ACCOUNTS.txt"

# ── the accounts ───────────────────────────────────────────────────────────
# Dev-only credentials. Change them before this is reachable by anyone else.
ACCOUNTS = [
    {
        "email": "admin@dentalinfo.test",
        "password": "Admin@12345",
        "name": "Dr. Sanskar Agrawal",
        "credential": "Administrator",
        "location": "Bengaluru, IN",
        "role": "admin",
        "verification_status": "verified",
        "license_number": "KA-DEN-48291",
        "license_board": "Karnataka State Dental Council",
        "license_country": "India",
        "note": "Full access: publish, delete ANY case, manage users, read audit log.",
    },
    {
        "email": "test@dentalinfo.test",
        "password": "Test@12345",
        "name": "Dr. Test Contributor",
        "credential": "MDS Conservative Dentistry & Endodontics",
        "location": "Pune, IN",
        "role": "contributor",
        "verification_status": "verified",
        "license_number": "MH-DEN-10457",
        "license_board": "Maharashtra State Dental Council",
        "license_country": "India",
        "note": "Verified contributor: publish and delete OWN cases.",
    },
    {
        "email": "reader@dentalinfo.test",
        "password": "Reader@12345",
        "name": "Ms. Read Only",
        "credential": "Dental student",
        "location": "Chennai, IN",
        "role": "reader",
        "verification_status": "unverified",
        "license_number": None,
        "license_board": None,
        "license_country": None,
        "note": "Read-only. Included so you can see the role gate once "
                "PERMISSIVE_MODE is turned off.",
    },
]


def reset():
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(store.DB_PATH) + suffix)
        if p.exists():
            p.unlink()
            print(f"  removed {p.name}")


def seed_accounts(conn) -> dict[str, str]:
    ids = {}
    for acct in ACCOUNTS:
        existing = store.get_user_by_email(conn, acct["email"])
        if existing:
            print(f"  account exists   {acct['email']}  ({existing['role']})")
            ids[acct["email"]] = existing["id"]
            continue
        uid = store.create_user(
            conn,
            email=acct["email"], name=acct["name"], password=acct["password"],
            role=acct["role"], credential=acct["credential"],
            location=acct["location"], verification_status=acct["verification_status"],
            license_number=acct["license_number"], license_board=acct["license_board"],
            license_country=acct["license_country"],
        )
        ids[acct["email"]] = uid
        print(f"  created account  {acct['email']}  ({acct['role']})")
    return ids


def seed_cases(conn, owner_id: str) -> int:
    """Import the example cases.

    Their original authors are not real accounts, so each case is attributed
    to a contributor account created for that author name -- that keeps the
    author byline intact and the foreign key honest.
    """
    if not SEED_CASES.exists():
        print(f"  ! {SEED_CASES.name} missing, skipping cases")
        return 0

    cases = json.loads(SEED_CASES.read_text(encoding="utf-8"))
    created = 0
    for case in cases:
        if conn.execute("SELECT 1 FROM posts WHERE id = ?", (case["id"],)).fetchone():
            continue

        author = case.get("author") or {}
        name = author.get("name") or "Unknown"
        email = (name.lower().replace("dr. ", "").replace(" ", ".")
                 + "@dentalinfo.test")
        row = store.get_user_by_email(conn, email)
        if row is None:
            author_id = store.create_user(
                conn, email=email, name=name,
                password=store.secrets.token_urlsafe(24),  # unusable placeholder
                role="contributor", credential=author.get("credential"),
                location=author.get("location"),
                verification_status="verified" if author.get("verified") else "unverified",
            )
        else:
            author_id = row["id"]

        store.create_post(conn, author_id=author_id, data={
            **case,
            "created_at": (case.get("date") or "") + "T09:00:00+00:00",
        })
        created += 1
    print(f"  imported cases   {created}")
    return created


def write_accounts_file():
    lines = [
        "=" * 66,
        " Dental Info — local development accounts",
        "=" * 66,
        "",
        " Start the server:   python server/app.py",
        " Then open:          http://localhost:8000",
        "",
        " LOCAL DEV CREDENTIALS ONLY. This file is gitignored.",
        " Do not reuse these passwords anywhere real.",
        "",
    ]
    for a in ACCOUNTS:
        lines += [
            "-" * 66,
            f" {a['role'].upper()}",
            "-" * 66,
            f"   email     {a['email']}",
            f"   password  {a['password']}",
            f"   name      {a['name']}",
            f"   access    {a['note']}",
            "",
        ]
    lines += [
        "=" * 66,
        " PERMISSIONS — current state",
        "=" * 66,
        "",
        " PERMISSIVE_MODE is ON, so every account above has full access,",
        " exactly as requested. Roles are still stored per account and the",
        " permission checks are all in place -- they are simply short-",
        " circuited by one flag.",
        "",
        " To switch real role enforcement on:",
        "",
        "   Windows PowerShell:   $env:DENTAL_INFO_STRICT=1 ; python server/app.py",
        "   bash:                 DENTAL_INFO_STRICT=1 python server/app.py",
        "",
        " With it on: admin does everything, the contributor can publish and",
        " delete only their own cases, and the reader account cannot post at all.",
        "",
        "=" * 66,
        " RESET",
        "=" * 66,
        "",
        "   python server/seed.py --reset      wipe the DB and rebuild",
        "",
    ]
    ACCOUNTS_FILE.write_text("\n".join(lines), encoding="utf-8")
    print(f"  wrote            {ACCOUNTS_FILE.name}")


def main():
    if "--reset" in sys.argv:
        print("\n  resetting database")
        reset()

    store.init_db()
    print("\n  seeding")
    with store.connect() as conn:
        ids = seed_accounts(conn)
        seed_cases(conn, ids.get("admin@dentalinfo.test"))
        conn.commit()

        users = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
        posts = conn.execute("SELECT COUNT(*) c FROM posts").fetchone()["c"]

    write_accounts_file()
    print(f"\n  done — {users} users, {posts} cases")
    print(f"  database: {store.DB_PATH}")
    print("\n  next:  python server/app.py\n")


if __name__ == "__main__":
    main()
