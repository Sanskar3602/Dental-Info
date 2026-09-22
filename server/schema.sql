-- ============================================================
-- Dental Info — schema
--
-- Written in portable SQL so the move to Postgres is small:
--   · no SQLite-only types (TEXT / INTEGER / REAL only)
--   · timestamps stored as ISO-8601 TEXT (UTC)
--   · JSON-ish lists stored as TEXT holding a JSON array
--     (becomes JSONB in Postgres)
--   · every id is TEXT holding a uuid4, not an autoincrement,
--     so rows can be created before the DB round-trip and
--     merged across environments without collisions
--
-- Roles are FIRST CLASS here even though permissions are
-- currently permissive (see PERMISSIVE_MODE in store.py).
-- Per CLAUDE.md, "contributor" is a distinct role from a
-- read-only user everywhere in the data model.
-- ============================================================

CREATE TABLE IF NOT EXISTS users (
  id                  TEXT PRIMARY KEY,
  email               TEXT NOT NULL UNIQUE,
  name                TEXT NOT NULL,
  credential          TEXT,                -- e.g. "MDS Endodontics"
  location            TEXT,                -- e.g. "Bengaluru, IN"

  -- auth
  password_hash       TEXT NOT NULL,       -- pbkdf2-sha256, hex
  password_salt       TEXT NOT NULL,       -- hex
  password_iterations INTEGER NOT NULL,

  -- authorisation
  role                TEXT NOT NULL DEFAULT 'reader'
                      CHECK (role IN ('admin','contributor','reader')),

  -- contributor verification (CLAUDE.md core requirement 1)
  verification_status TEXT NOT NULL DEFAULT 'unverified'
                      CHECK (verification_status IN ('unverified','pending','verified','lapsed')),
  license_number      TEXT,
  license_board       TEXT,
  license_country     TEXT,
  verified_at         TEXT,
  reverify_due        TEXT,                -- annual re-verification

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

-- A "post" is a documented clinical case.
CREATE TABLE IF NOT EXISTS posts (
  id             TEXT PRIMARY KEY,
  author_id      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,

  title          TEXT NOT NULL,
  summary        TEXT,

  -- taxonomy: procedure is the leaf id from the frontend taxonomy;
  -- procedure_path is the denormalised display label
  procedure      TEXT NOT NULL,
  procedure_path TEXT,
  difficulty     TEXT NOT NULL DEFAULT 'Medium'
                 CHECK (difficulty IN ('Low','Medium','High')),

  -- JSON arrays of strings
  complications  TEXT NOT NULL DEFAULT '[]',
  tools          TEXT NOT NULL DEFAULT '[]',
  resolution     TEXT NOT NULL DEFAULT '[]',
  takeaways      TEXT NOT NULL DEFAULT '[]',
  media          TEXT NOT NULL DEFAULT '[]',

  -- narrative
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
CREATE INDEX IF NOT EXISTS idx_posts_created   ON posts(created_at);

CREATE TABLE IF NOT EXISTS comments (
  id         TEXT PRIMARY KEY,
  post_id    TEXT NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
  author_id  TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  body       TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_comments_post ON comments(post_id);

-- Append-only audit trail. Deleting a post is a real DELETE for
-- your testing, but the audit row survives so you can see what
-- happened and who did it.
CREATE TABLE IF NOT EXISTS audit_log (
  id         TEXT PRIMARY KEY,
  actor_id   TEXT,
  action     TEXT NOT NULL,     -- 'login' | 'post.create' | 'post.delete' | ...
  target     TEXT,
  detail     TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at);

-- ============================================================
-- Contributor verification queue (CLAUDE.md core requirement 1)
--
-- A request is a row, not a flag on users, so that:
--   · the history survives an approve/reject/re-apply cycle
--   · annual re-verification creates a new row, keeping the old decision
--   · an admin decision is attributable (reviewer_id + note)
-- users.verification_status stays as the fast denormalised answer.
-- ============================================================
CREATE TABLE IF NOT EXISTS verification_requests (
  id              TEXT PRIMARY KEY,
  user_id         TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,

  license_number  TEXT NOT NULL,
  license_board   TEXT NOT NULL,
  license_country TEXT NOT NULL,
  credential      TEXT,

  -- automatic registry cross-check; 'unavailable' when no integration
  -- exists for that country yet, which is the normal case today
  registry_check  TEXT NOT NULL DEFAULT 'not_run'
                  CHECK (registry_check IN ('not_run','pass','fail','unavailable')),
  registry_detail TEXT,

  -- stands in for the document upload until blob storage exists
  document_note   TEXT,

  status          TEXT NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending','approved','rejected','withdrawn')),
  reviewer_id     TEXT REFERENCES users(id) ON DELETE SET NULL,
  reviewer_note   TEXT,

  created_at      TEXT NOT NULL,
  decided_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_vreq_user   ON verification_requests(user_id);
CREATE INDEX IF NOT EXISTS idx_vreq_status ON verification_requests(status);

-- ============================================================
-- Security hardening
--
-- sessions.token holds a SHA-256 HASH of the session token, never the
-- token itself. The raw value lives only in the user's cookie, so a
-- database read cannot be replayed as a live session. (Column name kept
-- for compatibility; the contents changed.)
--
-- login_attempts backs rate limiting. Serverless functions share no
-- memory, so the counter has to live in the database.
-- ============================================================
CREATE TABLE IF NOT EXISTS login_attempts (
  id         TEXT PRIMARY KEY,
  email      TEXT,              -- what was attempted, may not exist
  ip         TEXT,
  ok         INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_attempts_email ON login_attempts(email, created_at);
CREATE INDEX IF NOT EXISTS idx_attempts_ip    ON login_attempts(ip, created_at);

-- ============================================================
-- Media uploads
--
-- Bytes are stored in Postgres (base64 TEXT), which is what the user's
-- Neon plan budget (0.5GB) is being sized against -- see MEDIA.md for
-- the reasoning. Uploads are CHUNKED because Vercel serverless functions
-- cap a single request body well under 50MB; the client sends a file as
-- a sequence of small POSTs, which this table accumulates.
-- ============================================================
CREATE TABLE IF NOT EXISTS media_assets (
  id             TEXT PRIMARY KEY,
  owner_id       TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  filename       TEXT NOT NULL,
  mime           TEXT NOT NULL,
  size_bytes     INTEGER NOT NULL,       -- declared size, checked against actual on finish
  data           BLOB NOT NULL DEFAULT x'',  -- raw bytes, appended chunk by chunk
  chunks_received INTEGER NOT NULL DEFAULT 0,
  chunks_total    INTEGER NOT NULL,
  status         TEXT NOT NULL DEFAULT 'uploading'
                 CHECK (status IN ('uploading','complete','abandoned')),
  created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_media_owner  ON media_assets(owner_id);
CREATE INDEX IF NOT EXISTS idx_media_status ON media_assets(status);
