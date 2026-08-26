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
CREATE INDEX IF NOT EXISTS jobs_state ON jobs(state);
"""

# Columns added after the first release. SQLite cannot add a column that is already
# there and has no IF NOT EXISTS for it, so this is applied by inspection rather than
# by a version number -- an appliance whose upgrade path involves a migration table is
# an appliance that eventually needs a DBA (D2).
ADDED_COLUMNS = {
    "jobs": [
        ("queued_at", "INTEGER"),      # the queue orders by this, not by started_at
        ("fingerprint", "TEXT"),       # links a job to the disc it came from
        ("dest_path", "TEXT"),         # where it actually landed, for History
        ("phase", "TEXT"),             # sub-state within `state`, for the progress line
        ("updated_at", "INTEGER"),     # last progress write; stall detection and ETA
        ("eta_seconds", "INTEGER"),
        ("attempts", "INTEGER DEFAULT 0"),
        ("titles", "TEXT"),            # JSON: candidate titles found on the disc
        ("chosen_title", "INTEGER"),
        # 0..1 through whatever stage the job is in right now. Bytes cannot express
        # every stage -- reading an encrypted disc is minutes of CPU with no file yet --
        # and a stage with no number is a stage the user is watching blind.
        ("stage_pct", "REAL"),
        ("question", "TEXT"),          # what Riparr needs a human to answer
        ("local_path", "TEXT"),        # staging file, so a resumed job can find it
        ("bytes_verified", "INTEGER DEFAULT 0"),
        ("warning", "TEXT"),           # non-fatal: the rip proceeds, the user is told
        ("disc_family", "TEXT"),       # dvd | bluray | uhd -- what was actually in the tray
    ],
    "discs": [
        ("title_index", "INTEGER"),    # the remembered title choice (R5: fix once, ever)
        ("job_id", "INTEGER"),
    ],
}

# Defaults are the shipped opinion. The settings reference in docs/guide mirrors these.
DEFAULTS = {
    "setup_complete": False,
    "auto_rip": False,
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
    # "quick" | "deep" | "off". Quick compares the size the share reports against the
    # file that was sent -- nearly free, and it catches the failure that actually
    # happens (a truncated or refused transfer). Deep reads the whole file back and
    # hashes it, which is correct and expensive: it costs a second full download and,
    # because smbclient needs a seekable destination, as much free space again as the
    # title itself.
    "verify_mode": "quick",
    "keep_local_copy": True,
    # Looks the disc up on Wikipedia to put its poster faintly behind the page. It is
    # the only feature that tells an outside server what you are ripping, so it is a
    # setting rather than an assumption -- and purely decorative when off.
    "disc_artwork": True,
    "webhook_url": "",
    "watch_folder": "",
    # Notifications. The box's whole promise is "walk away", so these are the only way
    # it can reach someone who did.
    "notify_events": ["done", "needs_you", "failed", "share_lost", "key_expiring"],
    "ntfy_server": "https://ntfy.sh",
    "ntfy_topic": "",
    "ntfy_token": "",
    "discord_webhook": "",
    "smtp_host": "",
    "smtp_port": 587,
    "smtp_tls": True,
    "smtp_username": "",
    "smtp_password": "",
    "smtp_from": "",
    "smtp_to": "",
    "makemkv_key": "",
    "makemkv_eula_accepted_at": 0,
    "warn_key_days": 7,
    "update_channel": "stable",
    "auto_check_updates": True,
}


def _lock_down(path):
    """Keep the database readable only by the account that owns it.

    The file holds the account password hashes, the session signing secret and the SMB
    share password in the clear -- it is the one file on the box that must not be read
    by anyone but the service. SQLite creates it under the process umask, which on a
    stock image leaves it world-readable, so any local account could open it. Tighten
    the directory to 0700 and every database file (the DB plus its WAL/SHM sidecars) to
    0600. Best-effort: a filesystem that cannot represent these modes is not a reason to
    refuse to start.
    """
    try:
        os.chmod(os.path.dirname(path), 0o700)
    except OSError:
        pass
    for p in (path, path + "-wal", path + "-shm"):
        try:
            if os.path.exists(p):
                os.chmod(p, 0o600)
        except OSError:
            pass


def conn():
    c = getattr(_local, "c", None)
    if c is None:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        c = sqlite3.connect(DB_PATH, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")      # survives the yanked cable (D4)
        c.execute("PRAGMA synchronous=NORMAL")
        _lock_down(DB_PATH)                        # WAL/SHM now exist; lock them too
        _local.c = c
    return c


def init():
    c = conn()
    c.executescript(SCHEMA)
    for table, cols in ADDED_COLUMNS.items():
        have = {r["name"] for r in c.execute("PRAGMA table_info(%s)" % table)}
        for name, decl in cols:
            if name not in have:
                c.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, name, decl))
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


# A real pbkdf2 hash of nothing in particular, used only to burn the same time a genuine
# verify would when the username does not exist -- see verify_user.
_DUMMY_HASH = None


def verify_user(username, password):
    from passlib.hash import pbkdf2_sha256
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = pbkdf2_sha256.hash("riparr-timing-equaliser")
    row = conn().execute("SELECT password_hash FROM users WHERE username=?",
                         (username,)).fetchone()
    # An unknown username used to return before hashing anything, so a wrong username
    # answered in microseconds and a real one took a pbkdf2 verify -- the gap told an
    # attacker which usernames exist. Verify against a dummy hash instead, so both paths
    # cost the same, then return False.
    if not row:
        try:
            pbkdf2_sha256.verify(password, _DUMMY_HASH)
        except Exception:
            pass
        return False
    try:
        return pbkdf2_sha256.verify(password, row["password_hash"])
    except Exception:
        return False


def clear_users():
    """Remove every account, so first-run setup offers to create one again.

    Deliberately not "reset the password to something": there is nowhere safe to
    display a generated password on a headless box, and the setup flow already knows
    how to ask for a new one.
    """
    c = conn()
    c.execute("DELETE FROM users")
    c.commit()


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


# ── the job lifecycle ──
#
# Every state the engine can be in. `needs_input` is the one that did not exist before
# the scenario walk-through: `on_unknown_disc` defaults to "ask", and until there was a
# state meaning "waiting on a human" there was nowhere for that default to go.

ACTIVE_STATES = ["queued", "identifying", "ripping", "transferring", "verifying",
                 "needs_input"]
FINAL_STATES = ["done", "failed", "cancelled"]

# States a rip is physically mid-flight in. Anything found in one of these at boot was
# interrupted by the cable coming out, which D4 says to expect rather than prevent.
INTERRUPTIBLE = ["identifying", "ripping", "transferring", "verifying"]


def get_job(job_id):
    row = conn().execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return dict(row) if row else None


def create_job(**fields):
    fields.setdefault("state", "queued")
    fields.setdefault("queued_at", int(time.time()))
    fields.setdefault("updated_at", int(time.time()))
    if isinstance(fields.get("titles"), (list, dict)):
        fields["titles"] = json.dumps(fields["titles"])
    keys = list(fields)
    c = conn()
    cur = c.execute("INSERT INTO jobs(%s) VALUES(%s)"
                    % (",".join(keys), ",".join("?" * len(keys))),
                    [fields[k] for k in keys])
    c.commit()
    return cur.lastrowid


def update_job(job_id, **fields):
    if not fields:
        return
    if isinstance(fields.get("titles"), (list, dict)):
        fields["titles"] = json.dumps(fields["titles"])
    fields["updated_at"] = int(time.time())
    keys = list(fields)
    c = conn()
    c.execute("UPDATE jobs SET %s WHERE id=?" % ",".join("%s=?" % k for k in keys),
              [fields[k] for k in keys] + [job_id])
    c.commit()


def next_queued_job():
    """The oldest job waiting to start. One at a time -- there is one drive."""
    row = conn().execute(
        "SELECT * FROM jobs WHERE state='queued' ORDER BY id LIMIT 1").fetchone()
    return dict(row) if row else None


def active_job():
    row = conn().execute(
        "SELECT * FROM jobs WHERE state IN (%s) ORDER BY id LIMIT 1"
        % ",".join("?" * len(INTERRUPTIBLE)), INTERRUPTIBLE).fetchone()
    return dict(row) if row else None


def job_for_fingerprint(fingerprint, states=None):
    q = "SELECT * FROM jobs WHERE fingerprint=?"
    args = [fingerprint]
    if states:
        q += " AND state IN (%s)" % ",".join("?" * len(states))
        args += list(states)
    q += " ORDER BY id DESC LIMIT 1"
    row = conn().execute(q, args).fetchone()
    return dict(row) if row else None


def get_disc(fingerprint):
    row = conn().execute("SELECT * FROM discs WHERE fingerprint=?",
                         (fingerprint,)).fetchone()
    return dict(row) if row else None


def typical_job_seconds(kind=None, minimum=2):
    """How long a rip usually takes on this box, from this box's own history.

    There is no way to compute a real estimate for the slow half of a rip: MakeMKV
    reports nothing during the disc scan and the kernel cannot see the reads because
    they go through /dev/sg0. What there *is* is evidence -- this machine has ripped
    discs before and it took about as long each time. A median over past successful
    rips is fuzzy, honestly labelled, and infinitely better than a blank space.

    Returns (median_seconds, sample_count), or (None, n) until there is enough to say.
    """
    q = ("SELECT started_at, finished_at FROM jobs "
         "WHERE state='done' AND started_at IS NOT NULL AND finished_at IS NOT NULL")
    args = []
    if kind:
        q += " AND disc_family=?"
        args.append(kind)
    q += " ORDER BY id DESC LIMIT 20"
    spans = sorted(r["finished_at"] - r["started_at"]
                   for r in conn().execute(q, args)
                   if r["finished_at"] > r["started_at"])
    if len(spans) < minimum:
        return None, len(spans)
    mid = len(spans) // 2
    med = spans[mid] if len(spans) % 2 else (spans[mid - 1] + spans[mid]) // 2
    return med, len(spans)


def record_disc(fingerprint, **fields):
    """Remember a disc by fingerprint, so it is refused next time and any correction
    made to it survives (R5: get it right most of the time, easy to fix, never ask
    twice)."""
    c = conn()
    existing = get_disc(fingerprint)
    if existing:
        if fields:
            c.execute("UPDATE discs SET %s WHERE fingerprint=?"
                      % ",".join("%s=?" % k for k in fields),
                      list(fields.values()) + [fingerprint])
            c.commit()
        return
    fields["fingerprint"] = fingerprint
    keys = list(fields)
    c.execute("INSERT INTO discs(%s) VALUES(%s)"
              % (",".join(keys), ",".join("?" * len(keys))),
              [fields[k] for k in keys])
    c.commit()
