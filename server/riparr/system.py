"""
The four things the System pages report on: events, scheduled tasks, log files, backups.

These exist because an appliance with no screen has to be able to answer "what has this
thing been doing?" from the web interface alone. The *arrs settled this shape years ago
and it is worth copying wholesale (see DECISIONS.md D12): a rolling event log, a table
of recurring tasks with last/next execution, downloadable log files, and dated backups.

Two appliance-specific constraints shape everything here:

  * **The SD card is the enemy.** Every write is a write to flash that will eventually
    wear out, so the log files are small and few, the event table is capped and trimmed
    on a schedule, and nothing polls tightly.
  * **Power gets pulled.** The scheduler keeps its state in SQLite (WAL, D4) rather than
    in memory, so "last execution" survives a yank and tasks do not all stampede at once
    on the next boot.
"""
import glob
import io
import json
import logging
import logging.handlers
import os
import threading
import time
import zipfile

from . import db
from . import platform as P

# Everything lives beside the database, so one directory is the whole appliance state.
DATA_DIR = os.path.dirname(db.DB_PATH)
LOG_DIR = os.path.join(DATA_DIR, "logs")
BACKUP_DIR = os.path.join(DATA_DIR, "backups")

# Small and few, deliberately. 1 MB x 5 is about a fortnight of ordinary operation and
# caps the log directory at 10 MB across both files -- see the SD-card note above.
LOG_MAX_BYTES = 1024 * 1024
LOG_BACKUPS = 5

EVENT_CAP = 5000          # rows kept in the events table; trimmed by Clean Up Events
BACKUP_KEEP = 5           # dated backups kept; the oldest is dropped past this

log = logging.getLogger("riparr")


def component(name):
    """A child logger whose name becomes the Component column on the Events page.

    Prowlarr's Events table is only useful because every row says which part of the app
    spoke. A single "riparr" for everything would make the column dead weight.
    """
    return logging.getLogger("riparr." + name)


# ─────────────────────────────── events ───────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  id        INTEGER PRIMARY KEY,
  at        INTEGER NOT NULL,
  level     TEXT NOT NULL,
  component TEXT NOT NULL,
  message   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_at ON events(at DESC);

CREATE TABLE IF NOT EXISTS task_runs (
  name        TEXT PRIMARY KEY,
  last_start  INTEGER,
  last_end    INTEGER,
  last_error  TEXT
);

CREATE TABLE IF NOT EXISTS task_history (
  id        INTEGER PRIMARY KEY,
  name      TEXT NOT NULL,
  queued_at INTEGER NOT NULL,
  started_at INTEGER,
  ended_at  INTEGER,
  trigger   TEXT NOT NULL,
  error     TEXT
);
CREATE INDEX IF NOT EXISTS task_history_at ON task_history(queued_at DESC);
"""


class DbHandler(logging.Handler):
    """Mirrors the Python log into SQLite so the Events page has something to show.

    Attached alongside the file handler rather than instead of it: the file is what you
    download and send to someone, the table is what you page through in the browser.
    Failures here are swallowed on purpose -- a broken log must never take down a rip.
    """

    def emit(self, record):
        try:
            db.conn().execute(
                "INSERT INTO events(at, level, component, message) VALUES(?,?,?,?)",
                (int(record.created), record.levelname.lower(),
                 record.name.replace("riparr.", "") or "riparr",
                 self.format(record)))
            db.conn().commit()
        except Exception:
            pass


def init():
    """Create the tables and attach the handlers. Safe to call more than once."""
    c = db.conn()
    c.executescript(SCHEMA)
    c.commit()

    if getattr(log, "_riparr_configured", False):
        return
    os.makedirs(LOG_DIR, exist_ok=True)
    os.makedirs(BACKUP_DIR, exist_ok=True)

    fmt = logging.Formatter("%(asctime)s|%(levelname)s|%(name)s|%(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
    # Two files, matching the *arrs: riparr.txt is the ordinary record, riparr.debug.txt
    # keeps everything. Whoever is helping you asks for the debug one.
    main = logging.handlers.RotatingFileHandler(
        os.path.join(LOG_DIR, "riparr.txt"), maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUPS, encoding="utf-8")
    main.setLevel(logging.INFO)
    main.setFormatter(fmt)

    debug = logging.handlers.RotatingFileHandler(
        os.path.join(LOG_DIR, "riparr.debug.txt"), maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUPS, encoding="utf-8")
    debug.setLevel(logging.DEBUG)
    debug.setFormatter(fmt)

    dbh = DbHandler()
    dbh.setLevel(logging.INFO)
    dbh.setFormatter(logging.Formatter("%(message)s"))

    log.setLevel(logging.DEBUG)
    for h in (main, debug, dbh):
        log.addHandler(h)
    log._riparr_configured = True
    component("Riparr").info("Starting up")


def events(limit=50, offset=0, levels=None):
    """Newest first, which is the only order this is ever read in."""
    sql = "SELECT * FROM events"
    args = []
    if levels:
        sql += " WHERE level IN (%s)" % ",".join("?" * len(levels))
        args += list(levels)
    total = db.conn().execute(
        sql.replace("SELECT *", "SELECT COUNT(*) c"), args).fetchone()["c"]
    sql += " ORDER BY at DESC, id DESC LIMIT ? OFFSET ?"
    rows = db.conn().execute(sql, args + [limit, offset]).fetchall()
    return {"total": total, "events": [dict(r) for r in rows]}


def clear_events():
    c = db.conn()
    c.execute("DELETE FROM events")
    c.commit()
    component("Events").info("Event log cleared")


# ─────────────────────────────── log files ───────────────────────────────

def log_files():
    """Every rotated file, newest write first, as the Log Files page lists them."""
    out = []
    for path in glob.glob(os.path.join(LOG_DIR, "*.txt*")):
        try:
            st = os.stat(path)
        except OSError:
            continue
        out.append({"name": os.path.basename(path),
                    "size": st.st_size,
                    "modified": int(st.st_mtime)})
    out.sort(key=lambda f: f["modified"], reverse=True)
    return out


def log_path(name):
    """Resolve a log filename, refusing anything that escapes the log directory."""
    if "/" in name or "\\" in name or name.startswith("."):
        return None
    path = os.path.join(LOG_DIR, name)
    if os.path.realpath(os.path.dirname(path)) != os.path.realpath(LOG_DIR):
        return None
    return path if os.path.isfile(path) else None


def delete_log_files():
    """Clears the rotated backups, leaving the two files currently being written to.

    Deleting the live files out from under an open RotatingFileHandler leaves it writing
    to an unlinked inode, and the log silently stops appearing on disk.
    """
    live = {"riparr.txt", "riparr.debug.txt"}
    n = 0
    for f in log_files():
        if f["name"] in live:
            continue
        try:
            os.unlink(os.path.join(LOG_DIR, f["name"]))
            n += 1
        except OSError:
            pass
    component("Logs").info("Deleted %d rotated log file(s)", n)
    return n


# ─────────────────────────────── backups ───────────────────────────────

def backups():
    out = []
    for path in glob.glob(os.path.join(BACKUP_DIR, "riparr_backup_*.zip")):
        try:
            st = os.stat(path)
        except OSError:
            continue
        out.append({"name": os.path.basename(path),
                    "size": st.st_size,
                    "modified": int(st.st_mtime),
                    "kind": "scheduled" if "_scheduled_" in path else "manual"})
    out.sort(key=lambda b: b["modified"], reverse=True)
    return out


def backup_path(name):
    if "/" in name or "\\" in name or not name.endswith(".zip"):
        return None
    path = os.path.join(BACKUP_DIR, name)
    if os.path.realpath(os.path.dirname(path)) != os.path.realpath(BACKUP_DIR):
        return None
    return path if os.path.isfile(path) else None


def create_backup(kind="manual"):
    """A zip of the settings and the disc history -- everything that is not re-derivable.

    Deliberately *not* a copy of the database file: a live SQLite file plus its WAL is
    not safe to copy, and restoring a binary DB across a schema change is exactly the
    kind of thing that bricks an appliance. JSON restores into any later schema.
    """
    from . import __version__
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = time.strftime("%Y.%m.%d_%H.%M.%S")
    name = "riparr_backup_v%s_%s_%s.zip" % (__version__, kind, stamp)
    path = os.path.join(BACKUP_DIR, name)

    payload = {
        "version": __version__,
        "created_at": int(time.time()),
        "settings": db.all_settings(),
        "shares": [dict(s) for s in db.list_shares()],
        "discs": [dict(d) for d in db.list_discs(limit=100000)],
    }
    # Credentials are in the share rows. A backup is a file the user downloads and mails
    # to themselves, so the password never goes in it -- the restore re-prompts.
    for s in payload["shares"]:
        s.pop("password", None)

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("riparr.json", json.dumps(payload, indent=2))
    _prune_backups()
    component("Backup").debug("Wrote %s (%d bytes)", name, os.path.getsize(path))
    return {"name": name, "size": os.path.getsize(path), "kind": kind}


def _prune_backups():
    extra = backups()[BACKUP_KEEP:]
    for b in extra:
        try:
            os.unlink(os.path.join(BACKUP_DIR, b["name"]))
        except OSError:
            pass


def delete_backup(name):
    path = backup_path(name)
    if not path:
        return False
    os.unlink(path)
    component("Backup").info("Deleted %s", name)
    return True


def import_backup(filename, data):
    """Accept an uploaded backup and restore from it.

    Takes the bytes rather than a path so the caller never has to write an untrusted
    file into the backup directory before deciding whether it is one of ours. Both the
    zip this module writes and the bare riparr.json from Settings > Export are accepted,
    because a user who exported before backups existed still has the JSON.
    """
    try:
        if data[:2] == b"PK":
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                payload = json.loads(z.read("riparr.json"))
        else:
            payload = json.loads(data.decode("utf-8"))
    except Exception as e:
        return {"ok": False, "error": "Not a Riparr backup (%s)" % e}
    if not isinstance(payload.get("settings"), dict):
        return {"ok": False, "error": "Not a Riparr backup: no settings in it"}
    return _apply(payload, filename)


def restore_backup(name):
    path = backup_path(name)
    if not path:
        return {"ok": False, "error": "No such backup"}
    with zipfile.ZipFile(path) as z:
        payload = json.loads(z.read("riparr.json"))
    return _apply(payload, name)


def _apply(payload, name):
    n = 0
    for k, v in (payload.get("settings") or {}).items():
        if k in ("session_secret",):        # never restore the cookie key
            continue
        db.set(k, v)
        n += 1
    component("Backup").warning("Restored %d setting(s) from %s", n, name)
    return {"ok": True, "settings": n,
            "shares": len(payload.get("shares") or []),
            "note": "Share passwords are not stored in a backup and must be re-entered."}


# ─────────────────────────────── scheduled tasks ───────────────────────────────

def _task_check_health():
    P.system_status()
    P.optical_diagnosis()
    c = P.clock_status()
    if not c["plausible"]:
        component("Clock").error(
            "The system clock reads %s, which cannot be right. Anything Riparr says "
            "about dates -- key expiry, when a rip finished, when a task is due -- is "
            "wrong until the time syncs.",
            time.strftime("%d %b %Y %H:%M", time.localtime(c["now"])))
    elif c["synced"] is False:
        component("Clock").warning("The clock has not synchronised with a time server yet.")
    return "Health checked"


def _task_update_check():
    from . import updater
    r = updater.check()
    return "Update check: %s" % r.get("status", "unknown")


def _task_key_check():
    """The MakeMKV beta key expires roughly monthly and a lapsed key stops every rip.

    This is the single most likely way for a working box to quietly stop working, so it
    gets its own daily task rather than being folded into the health check.
    """
    st = P.makemkv_status()
    days = st.get("days_left")
    if days is None:
        return "No MakeMKV key registered"
    if days <= db.get("warn_key_days", 7):
        component("MakeMKV").warning("Key expires in %s day(s)", days)
        # Notified rather than only logged: a lapsed key is the classic "came home
        # after a month, ripped five discs, all five failed" -- and every warning
        # Riparr had for it lived on a page nobody had open.
        from . import notify
        notify.send(
            "key_expiring",
            title="MakeMKV key expires in %d day%s" % (days, "" if days == 1 else "s"),
            body=("Every rip fails the day it lapses. Settings \u2192 General fetches "
                  "the current beta key in one click.") if days > 0 else
                 "The key has expired. Rips will fail until it's replaced.")
    return "MakeMKV key: %s day(s) left" % days


def _task_share_check():
    """Every share something is configured to write to, not just the default one.

    Films and television can be on different machines. Checking only the default share
    means the health check passes while the machine half the rips are bound for has
    been asleep for a week.
    """
    ids = db.shares_in_use()
    if not ids:
        return "No share configured"
    names = []
    for sid in ids:
        share = db.share_by_id(sid)
        if share:
            names.append(share["host"])
    return "Share%s %s reachable" % ("s" if len(names) > 1 else "", ", ".join(names))


def _task_backup():
    b = create_backup(kind="scheduled")
    return "Wrote %s" % b["name"]


def _task_cleanup():
    c = db.conn()
    row = c.execute("SELECT COUNT(*) n FROM events").fetchone()
    excess = row["n"] - EVENT_CAP
    if excess > 0:
        c.execute("DELETE FROM events WHERE id IN "
                  "(SELECT id FROM events ORDER BY at ASC, id ASC LIMIT ?)", (excess,))
    c.execute("DELETE FROM task_history WHERE queued_at < ?",
              (int(time.time()) - 7 * 86400,))
    c.commit()
    return "Trimmed %d event(s)" % max(excess, 0)


# name, label, interval in seconds, function
TASKS = [
    ("health",  "Check Health",             6 * 3600,  _task_check_health, "Health"),
    ("update",  "Application Update Check", 6 * 3600,  _task_update_check, "Update"),
    ("key",     "MakeMKV Key Check",        24 * 3600, _task_key_check,    "MakeMKV"),
    ("share",   "Share Check",              3600,      _task_share_check,  "Share"),
    ("backup",  "Backup",                   7 * 86400, _task_backup,       "Backup"),
    ("cleanup", "Clean Up Events",          24 * 3600, _task_cleanup,      "Housekeeping"),
]
_BY_NAME = {t[0]: t for t in TASKS}


def task_list():
    """The Scheduled table: interval, last execution, last duration, next execution."""
    now = int(time.time())
    rows = {r["name"]: r for r in db.conn().execute("SELECT * FROM task_runs")}
    out = []
    for name, label, interval, _fn, _comp in TASKS:
        r = rows.get(name)
        last_end = r["last_end"] if r else None
        duration = (last_end - r["last_start"]) if (r and last_end and r["last_start"]) else None
        out.append({
            "name": name,
            "label": label,
            "interval": interval,
            "last_execution": last_end,
            "last_duration": duration,
            "next_execution": (last_end + interval) if last_end else now,
            "last_error": r["last_error"] if r else None,
        })
    return out


def task_history(limit=20):
    rows = db.conn().execute(
        "SELECT * FROM task_history ORDER BY queued_at DESC, id DESC LIMIT ?",
        (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["label"] = _BY_NAME[d["name"]][1] if d["name"] in _BY_NAME else d["name"]
        out.append(d)
    return out


def run_task(name, trigger="manual"):
    """Runs one task inline and records it. Returns the history row."""
    t = _BY_NAME.get(name)
    if not t:
        return None
    _, label, _interval, fn, comp = t
    c = db.conn()
    now = int(time.time())
    cur = c.execute("INSERT INTO task_history(name, queued_at, started_at, trigger) "
                    "VALUES(?,?,?,?)", (name, now, now, trigger))
    hid = cur.lastrowid
    c.commit()

    err = None
    try:
        msg = fn()
        component(comp).info(msg)
    except Exception as e:                      # a failing task must not stop the rest
        err = str(e)
        component(comp).error("%s failed: %s", label, e)

    end = int(time.time())
    c.execute("UPDATE task_history SET ended_at=?, error=? WHERE id=?", (end, err, hid))
    c.execute("INSERT INTO task_runs(name,last_start,last_end,last_error) VALUES(?,?,?,?) "
              "ON CONFLICT(name) DO UPDATE SET last_start=excluded.last_start, "
              "last_end=excluded.last_end, last_error=excluded.last_error",
              (name, now, end, err))
    c.commit()
    return {"name": name, "label": label, "started_at": now, "ended_at": end, "error": err}


# ─────────────────────────────── the scheduler ───────────────────────────────

_stop = threading.Event()


def _loop():
    """One thread, waking once a minute. Not a cron library and not asyncio.

    A minute of granularity is plenty when the shortest interval is an hour, and a plain
    daemon thread costs nothing on a 512 MB box. Due-ness is computed from `last_end` in
    SQLite, so pulling the power does not lose the schedule or cause every task to fire
    at once on the next boot.
    """
    while not _stop.wait(60):
        try:
            now = int(time.time())
            for t in task_list():
                if t["next_execution"] and t["next_execution"] <= now:
                    run_task(t["name"], trigger="scheduled")
        except Exception as e:
            component("Scheduler").error("Tick failed: %s", e)


def start_scheduler():
    threading.Thread(target=_loop, name="riparr-scheduler", daemon=True).start()


def stop_scheduler():
    _stop.set()
