import sqlite3
from contextlib import closing
from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS candidates(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT, phone TEXT, email TEXT, college TEXT, degree TEXT,
  specialization TEXT, graduation_year TEXT,
  resume_path TEXT, token TEXT UNIQUE,
  interview_status TEXT DEFAULT 'pending',   -- pending | in_progress | completed
  created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS interview_questions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  question TEXT NOT NULL, category TEXT DEFAULT 'General',
  maximum_score REAL DEFAULT 100, order_number INTEGER DEFAULT 1,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS answers(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  candidate_id INTEGER NOT NULL, question_id INTEGER NOT NULL,
  audio_file TEXT, transcript TEXT,
  response_time REAL, answer_duration REAL,
  raw_score REAL, penalty_pct REAL DEFAULT 0, score REAL,
  ai_likelihood REAL, ai_suspected INTEGER DEFAULT 0,
  evaluation_json TEXT, status TEXT DEFAULT 'processing', error TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(candidate_id, question_id));
CREATE TABLE IF NOT EXISTS evaluations(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  candidate_id INTEGER UNIQUE, total_score REAL, max_score REAL, percentage REAL,
  rank INTEGER, ai_suspected INTEGER DEFAULT 0, avg_response_time REAL,
  summary TEXT, pdf_path TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS job_roles(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, description TEXT,
  followup_chain TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS turns(
  id INTEGER PRIMARY KEY AUTOINCREMENT, candidate_id INTEGER, question_id INTEGER,
  speaker TEXT, kind TEXT, text TEXT, audio_file TEXT, response_time REAL, duration REAL,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_turns_c ON turns(candidate_id, id);
CREATE TABLE IF NOT EXISTS interview_state(candidate_id INTEGER PRIMARY KEY, state_json TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
"""

def connect():
    conn = sqlite3.connect(str(config.DATABASE_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn

def _add_cols(c, table, cols):
    have = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
    for name, typ in cols.items():
        if name not in have: c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {typ}")

def init_db():
    for p in (config.UPLOAD_FOLDER, config.RESUME_FOLDER, config.TRANSCRIPT_FOLDER,
              config.REPORT_FOLDER, config.EXPORT_FOLDER, config.TTS_CACHE_FOLDER, config.PIPER_MODEL_DIR, config.GOOGLE_DIR):
        p.mkdir(parents=True, exist_ok=True)
    with closing(connect()) as c:
        try: c.execute("PRAGMA journal_mode=WAL")
        except Exception: pass
        c.executescript(SCHEMA)
        had_active = "active" in {r["name"] for r in c.execute("PRAGMA table_info(interview_questions)")}
        _add_cols(c, "candidates", {"role_id": "INTEGER", "ai_review": "TEXT DEFAULT ''", "started_at": "TEXT",
                                    "finished_at": "TEXT", "last_activity": "TEXT"})
        _add_cols(c, "evaluations", {"drive_link": "TEXT", "drive_file_id": "TEXT"})
        _add_cols(c, "interview_questions", {"role_id": "INTEGER", "answer_key": "TEXT", "needs_followup": "INTEGER DEFAULT 0", "active": "INTEGER DEFAULT 1"})
        _add_cols(c, "answers", {"followups": "INTEGER DEFAULT 0", "hold": "INTEGER DEFAULT 0", "followup_json": "TEXT", "candidate_text": "TEXT"})
        c.commit()
    from .services import role_banks
    role_banks.seed_roles()
    if not had_active: role_banks.apply_default_selection()

def query(sql, args=(), one=False):
    with closing(connect()) as c:
        rows = c.execute(sql, args).fetchall()
    return (rows[0] if rows else None) if one else rows

def execute(sql, args=()):
    with closing(connect()) as c:
        cur = c.execute(sql, args)
        c.commit()
        return cur.lastrowid

def get_setting(key, default=""):
    r = query("SELECT value FROM settings WHERE key=?", (key,), one=True)
    return r["value"] if r else default

def set_setting(key, value):
    execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
