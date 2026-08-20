"""
SQLite state. One file, one connection pool, no separate database process (D2).

Settings are a typed key/value table rather than columns, because the settings surface
will keep growing and an appliance cannot afford a migration story that involves a DBA.
"""
import json
import os
import sqlite3
import threading
import time

DB_PATH = os.environ.get("RIPARR_DB", os.path.expanduser("~/.riparr/riparr.db"))
_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
  id            INTEGER PRIMARY KEY,
  username      TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  created_at    INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS shares (
  id          INTEGER PRIMARY KEY,
  name        TEXT NOT NULL,
  host        TEXT NOT NULL,
  path        TEXT NOT NULL,
  username    TEXT,
  password    TEXT,
  is_default  INTEGER NOT NULL DEFAULT 0,
  verified_at INTEGER
);
CREATE TABLE IF NOT EXISTS jobs (
  id           INTEGER PRIMARY KEY,
  title        TEXT,
  disc_label   TEXT,
  kind         TEXT,
  state        TEXT NOT NULL,
  mode         TEXT,
  bytes_total  INTEGER DEFAULT 0,
  bytes_ripped INTEGER DEFAULT 0,
  bytes_sent   INTEGER DEFAULT 0,
  started_at   INTEGER,
  finished_at  INTEGER,
  error        TEXT
);
CREATE TABLE IF NOT EXISTS discs (
  fingerprint TEXT PRIMARY KEY,
  label       TEXT,
  title       TEXT,
  kind        TEXT,
  ripped_at   INTEGER,
  correction  TEXT
);
"""

# Defaults are the shipped opinion. The settings reference in docs/guide mirrors these.
DEFAULTS = {
    "setup_complete": False,
    "theme": "servarr",
    "movie_template": "{Title} ({Year})/{Title} ({Year}).mkv",
    "tv_template": "{Title} ({Year})/Season {Season:00}/"
                   "{Title} - S{Season:00}E{Episode:00} - {EpisodeTitle}.mkv",
    "movie_folder": "Movies",
    "tv_folder": "TV",
    "on_unknown_disc": "ask",
    "audio_languages": ["eng"],
    "subtitle_languages": ["eng"],
    "keep_forced_subtitles": True,
    "keep_commentary": False,
    "min_title_seconds": 120,
    "rip_mode": "main",
    "transfer_mode": "auto",
    "verify_after_transfer": True,
    "keep_local_copy": True,
    "webhook_url": "",
    "watch_folder": "",
    "makemkv_key": "",
    "makemkv_eula_accepted_at": 0,
    "warn_key_days": 7,
    "update_channel": "stable",
    "auto_check_updates": True,
}


def conn():
    c = getattr(_local, "c", None)
    if c is None:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        c = sqlite3.connect(DB_PATH, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")      # survives the yanked cable (D4)
        c.execute("PRAGMA synchronous=NORMAL")
        _local.c = c
    return c


def init():
    c = conn()
    c.executescript(SCHEMA)
    c.commit()


def get(key, default=None):
    row = conn().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    if row is None:
        return DEFAULTS.get(key, default)
    return json.loads(row["value"])


def set(key, value):
    c = conn()
    c.execute("INSERT INTO settings(key,value) VALUES(?,?) "
              "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              (key, json.dumps(value)))
    c.commit()
    return value


def all_settings():
    out = dict(DEFAULTS)
    for row in conn().execute("SELECT key,value FROM settings"):
        out[row["key"]] = json.loads(row["value"])
    return out


# ── users ──

def create_user(username, password):
    from passlib.hash import pbkdf2_sha256
    c = conn()
    c.execute("INSERT INTO users(username,password_hash,created_at) VALUES(?,?,?)",
              (username, pbkdf2_sha256.hash(password), int(time.time())))
    c.commit()


def verify_user(username, password):
    from passlib.hash import pbkdf2_sha256
    row = conn().execute("SELECT password_hash FROM users WHERE username=?",
                         (username,)).fetchone()
    if not row:
        return False
    try:
        return pbkdf2_sha256.verify(password, row["password_hash"])
    except Exception:
        return False


def has_users():
    return conn().execute("SELECT COUNT(*) n FROM users").fetchone()["n"] > 0


def set_password(username, password):
    from passlib.hash import pbkdf2_sha256
    c = conn()
    c.execute("UPDATE users SET password_hash=? WHERE username=?",
              (pbkdf2_sha256.hash(password), username))
    c.commit()


# ── shares ──

def list_shares():
    return [dict(r) for r in conn().execute(
        "SELECT id,name,host,path,username,is_default,verified_at FROM shares "
        "ORDER BY is_default DESC, name")]


def add_share(name, host, path, username, password, make_default=True):
    c = conn()
    if make_default:
        c.execute("UPDATE shares SET is_default=0")
    cur = c.execute(
        "INSERT INTO shares(name,host,path,username,password,is_default) "
        "VALUES(?,?,?,?,?,?)",
        (name, host, path, username, password, 1 if make_default else 0))
    c.commit()
    return cur.lastrowid


def default_share():
    row = conn().execute(
        "SELECT * FROM shares ORDER BY is_default DESC, id LIMIT 1").fetchone()
    return dict(row) if row else None


def mark_share_verified(share_id):
    c = conn()
    c.execute("UPDATE shares SET verified_at=? WHERE id=?", (int(time.time()), share_id))
    c.commit()


def delete_share(share_id):
    c = conn()
    c.execute("DELETE FROM shares WHERE id=?", (share_id,))
    c.commit()


# ── jobs / discs (queue + history) ──

def list_jobs(states=None, limit=50):
    q = "SELECT * FROM jobs"
    args = []
    if states:
        q += " WHERE state IN (%s)" % ",".join("?" * len(states))
        args += list(states)
    q += " ORDER BY COALESCE(started_at,0) DESC LIMIT ?"
    args.append(limit)
    return [dict(r) for r in conn().execute(q, args)]


def list_discs(limit=200):
    return [dict(r) for r in conn().execute(
        "SELECT * FROM discs ORDER BY COALESCE(ripped_at,0) DESC LIMIT ?", (limit,))]


def forget_disc(fingerprint):
    c = conn()
    c.execute("DELETE FROM discs WHERE fingerprint=?", (fingerprint,))
    c.commit()
