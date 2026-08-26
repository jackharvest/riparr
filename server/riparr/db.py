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
        # JSON list of {name, started, ended}: how long each stage of this job took.
        # A rip is five very different operations wearing one progress bar, and until
        # this existed the only number anyone had was the total -- which is why the
        # "sixteen minutes before a byte is written" behaviour had to be measured by
        # hand with a stopwatch rather than simply read off the box.
        ("stages", "TEXT"),
        ("verified_mode", "TEXT"),     # quick | deep -- which check actually passed
        # The share-relative path the transfer actually wrote to. `dest_path` is the
        # human description a transport prints and cannot be handed back to one, and
        # rebuilding the path from the template afterwards is guessing -- the year is
        # already stripped out of `title` by then, so the guess comes out wrong.
        ("remote_name", "TEXT"),
        ("disc_bytes", "INTEGER"),     # the whole disc, not the title: see discs.size_bytes
    ],
    "discs": [
        ("title_index", "INTEGER"),    # the remembered title choice (R5: fix once, ever)
        ("job_id", "INTEGER"),
        # The size of the disc itself, paired with `label` to recognise a disc in
        # seconds instead of minutes -- see db.disc_by_label_size.
        ("size_bytes", "INTEGER"),
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
    # How the box says "you already ripped this" to somebody who is not looking at a
    # browser. "flash" blinks the optical drive's own light by reading the disc in a
    # rhythm -- there is no command that addresses that light, so activity is the only
    # lever (see platform.drive_flash). "tray" opens and closes the tray instead, which
    # is unmissable and is machinery. "both", or "off".
    "duplicate_signal": "flash",
    "webhook_url": "",
    "watch_folder": "",
    # Notifications. The box's whole promise is "walk away", so these are the only way
    # it can reach someone who did.
    "notify_events": ["done", "needs_you", "failed", "share_lost", "key_expiring"],
    "ntfy_server": "https://ntfy.sh",
    "ntfy_topic": "",
    "ntfy_token": "",
    "discord_webhook": "",
    # A Discord webhook posts into a *channel*. Somebody who wants the box to tell
    # *them* wants their phone to buzz, and in Discord the only thing that buzzes a
    # phone reliably is being mentioned -- so the user's own ID goes on the message.
    # Empty means "post quietly into the channel", which is the right default for a
    # shared server.
    "discord_mention": "",
    "discord_mention_events": ["needs_you", "failed", "share_lost", "key_expiring"],
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


def disc_by_label_size(label, size_bytes):
    """A disc we have seen before, recognised without reading it properly.

    The fingerprint is the real identity, and getting one means a full `makemkvcon`
    scan -- three to nine minutes with the drive spinning. That is a long time to make
    somebody wait to be told a thing they could have been told at once, and it is the
    difference between "Riparr noticed" and "Riparr eventually noticed".

    The volume label arrives in about fifteen seconds and the disc size comes with it.
    Neither is enough alone: labels repeat across the discs of a boxed set, and plenty
    of discs share a size. **Together** they are strong -- two different films with the
    same volume label *and* the same byte count is not a case that turns up -- and the
    cost of being wrong is bounded, because the page says which film it thinks this is
    and Forget is next to it.

    Rows recorded before this column existed have no size and are never matched here,
    so they simply fall back to the full scan. Returns the disc row, or None.
    """
    if not label or not size_bytes:
        return None
    row = conn().execute(
        "SELECT * FROM discs WHERE label=? AND size_bytes=? AND ripped_at IS NOT NULL "
        "ORDER BY ripped_at DESC LIMIT 1", (label, int(size_bytes))).fetchone()
    return dict(row) if row else None


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


# ── stage timings ──
#
# A rip is five operations, not one, and they are wildly unequal: on the reference box
# a DVD spends ~9 min being scanned, ~7 min being decrypted with nothing written at
# all, ~9 min being saved to the card, ~5 min uploading and seconds verifying. A single
# total hides all of that, and the stage that looks hung to a user (the silent seven
# minutes) is precisely the one no progress bar can describe.
#
# Stored as a JSON list on the job rather than a table: it is written a handful of
# times per job, read all at once, and never queried across jobs by SQL.

STAGE_ORDER = ["identify", "decrypt", "save", "upload", "verify"]

STAGE_LABEL = {
    "identify": "Reading the disc",
    "decrypt":  "Decrypting",
    "save":     "Saving to the card",
    "upload":   "Uploading",
    "verify":   "Verifying",
}


def _stages(job):
    try:
        got = json.loads(job.get("stages") or "[]")
        return got if isinstance(got, list) else []
    except (ValueError, TypeError):
        return []


def stage_enter(job_id, name, at=None):
    """Open a stage unless it is already the open one.

    The idempotent form, and the one to reach for. A stage can legitimately be entered
    from two places -- `identify` opens in `enqueue`, because that is where the nine
    minutes of disc scanning actually happen, and the worker then walks into
    `_identify` and would open it a second time. Splitting one stretch of work into
    two runs still sums correctly, but it resets the clock the queue is counting
    against, so the timer would jump back to zero halfway through the slowest stage.
    """
    job = get_job(job_id) or {}
    for st in reversed(_stages(job)):
        if st.get("ended") is None:
            if st.get("name") == name:
                return
            break
    stage_start(job_id, name, at=at)


def stage_start(job_id, name, at=None):
    """Open a stage, closing whatever was open before it.

    Closing the previous stage here rather than at each call site means a stage that
    raises still gets an end time -- so a failed job's breakdown says how far it got
    instead of showing one stage running forever.
    """
    now = int(at or time.time())
    job = get_job(job_id) or {}
    stages = _stages(job)
    for st in stages:
        if st.get("ended") is None:
            st["ended"] = now
    stages.append({"name": name, "started": now, "ended": None})
    update_job(job_id, stages=json.dumps(stages))


def stage_end(job_id, at=None):
    """Close the open stage, if there is one."""
    now = int(at or time.time())
    job = get_job(job_id) or {}
    stages = _stages(job)
    changed = False
    for st in stages:
        if st.get("ended") is None:
            st["ended"] = now
            changed = True
    if changed:
        update_job(job_id, stages=json.dumps(stages))


def job_stages(job):
    """Stage timings for one job, oldest first, with seconds worked out.

    Repeated stages are summed -- a job whose upload was retried three times uploaded
    three times, and the honest answer to "how long did the upload take" is all of it.
    """
    totals = {}
    for st in _stages(job):
        name = st.get("name")
        start, end = st.get("started"), st.get("ended")
        if not name or not start:
            continue
        end = end or start
        if end < start:
            continue
        row = totals.setdefault(name, {"name": name, "seconds": 0, "runs": 0,
                                       "label": STAGE_LABEL.get(name, name)})
        row["seconds"] += end - start
        row["runs"] += 1
    return [totals[n] for n in STAGE_ORDER if n in totals] + \
           [v for k, v in totals.items() if k not in STAGE_ORDER]


def typical_stage_seconds(kind=None, minimum=2, limit=20):
    """Median seconds per stage over this box's own successful rips.

    The counting timer the queue shows is built out of this. There is no way to ask
    MakeMKV how far through a scan it is (see typical_job_seconds), but this machine
    has done the same work before and took about as long each time -- which is a real
    estimate for every stage, not just the ones that can count bytes.

    Returns {stage: {"seconds": median, "samples": n}}.
    """
    q = "SELECT stages FROM jobs WHERE state='done' AND stages IS NOT NULL"
    args = []
    if kind:
        q += " AND disc_family=?"
        args.append(kind)
    q += " ORDER BY id DESC LIMIT ?"
    args.append(limit)

    buckets = {}
    for row in conn().execute(q, args):
        for st in job_stages({"stages": row["stages"]}):
            if st["seconds"] > 0:
                buckets.setdefault(st["name"], []).append(st["seconds"])

    out = {}
    for name, vals in buckets.items():
        if len(vals) < minimum:
            continue
        vals.sort()
        mid = len(vals) // 2
        med = vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) // 2
        out[name] = {"seconds": int(med), "samples": len(vals)}
    return out


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
