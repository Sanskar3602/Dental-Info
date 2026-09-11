-- ============================================================
-- Dental Info — Postgres schema (deployed / Neon)
--
-- The Postgres counterpart of schema.sql. Differences, all deliberate:
--   · list columns are JSONB rather than TEXT holding JSON
--   · timestamps stay ISO-8601 TEXT in UTC, matching SQLite, so the
--     two backends return byte-identical JSON to the frontend.
--     (ISO-8601 UTC sorts correctly as text, so ORDER BY still works.
--     Switching these to TIMESTAMPTZ later is a contained migration.)
--
-- Safe to run repeatedly.
-- ============================================================

CREATE TABLE IF NOT EXISTS users (
  id                  TEXT PRIMARY KEY,
  email               TEXT NOT NULL UNIQUE,
  name                TEXT NOT NULL,
  credential          TEXT,
  location            TEXT,

  password_hash       TEXT NOT NULL,
  password_salt       TEXT NOT NULL,
  password_iterations INTEGER NOT NULL,

  role                TEXT NOT NULL DEFAULT 'reader'
                      CHECK (role IN ('admin','contributor','reader')),

  verification_status TEXT NOT NULL DEFAULT 'unverified'
                      CHECK (verification_status IN ('unverified','pending','verified','lapsed')),
  license_number      TEXT,
  license_board       TEXT,
  license_country     TEXT,
  verified_at         TEXT,
  reverify_due        TEXT,

  created_at          TEXT NOT NULL,
  updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
  token      TEXT PRIMARY KEY,
  user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

CREATE TABLE IF NOT EXISTS posts (
  id             TEXT PRIMARY KEY,
  author_id      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,

  title          TEXT NOT NULL,
  summary        TEXT,

  procedure      TEXT NOT NULL,
  procedure_path TEXT,
  difficulty     TEXT NOT NULL DEFAULT 'Medium'
                 CHECK (difficulty IN ('Low','Medium','High')),

  complications  JSONB NOT NULL DEFAULT '[]'::jsonb,
  tools          JSONB NOT NULL DEFAULT '[]'::jsonb,
  resolution     JSONB NOT NULL DEFAULT '[]'::jsonb,
  takeaways      JSONB NOT NULL DEFAULT '[]'::jsonb,
  media          JSONB NOT NULL DEFAULT '[]'::jsonb,

  presentation   TEXT,
  unusual        TEXT,
  outcome        TEXT,

  status         TEXT NOT NULL DEFAULT 'published'
                 CHECK (status IN ('draft','published','removed')),

  reads          INTEGER NOT NULL DEFAULT 0,
  saves          INTEGER NOT NULL DEFAULT 0,

  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_posts_procedure ON posts(procedure);
CREATE INDEX IF NOT EXISTS idx_posts_author    ON posts(author_id);
CREATE INDEX IF NOT EXISTS idx_posts_status    ON posts(status);
CREATE INDEX IF NOT EXISTS idx_posts_created   ON posts(created_at DESC);

-- JSONB lets us filter by tool/complication in SQL later, which the
-- TEXT version could not do without a LIKE hack.
CREATE INDEX IF NOT EXISTS idx_posts_tools_gin ON posts USING GIN (tools);
CREATE INDEX IF NOT EXISTS idx_posts_comps_gin ON posts USING GIN (complications);

CREATE TABLE IF NOT EXISTS comments (
  id         TEXT PRIMARY KEY,
  post_id    TEXT NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
  author_id  TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  body       TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_comments_post ON comments(post_id);

CREATE TABLE IF NOT EXISTS audit_log (
  id         TEXT PRIMARY KEY,
  actor_id   TEXT,
  action     TEXT NOT NULL,
  target     TEXT,
  detail     TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at DESC);

-- ============================================================
-- Contributor verification queue (CLAUDE.md core requirement 1)
-- A request is a row, not a flag, so history survives approve/reject/
-- re-apply and annual re-verification, and each decision is attributable.
-- ============================================================
CREATE TABLE IF NOT EXISTS verification_requests (
  id              TEXT PRIMARY KEY,
  user_id         TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,

  license_number  TEXT NOT NULL,
  license_board   TEXT NOT NULL,
  license_country TEXT NOT NULL,
  credential      TEXT,

  registry_check  TEXT NOT NULL DEFAULT 'not_run'
                  CHECK (registry_check IN ('not_run','pass','fail','unavailable')),
  registry_detail TEXT,
  document_note   TEXT,

  status          TEXT NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending','approved','rejected','withdrawn')),
  reviewer_id     TEXT REFERENCES users(id) ON DELETE SET NULL,
  reviewer_note   TEXT,

  created_at      TEXT NOT NULL,
  decided_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_vreq_user    ON verification_requests(user_id);
CREATE INDEX IF NOT EXISTS idx_vreq_status  ON verification_requests(status);
CREATE INDEX IF NOT EXISTS idx_vreq_created ON verification_requests(created_at DESC);
