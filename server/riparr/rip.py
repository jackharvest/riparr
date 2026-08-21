"""
The rip engine: disc in the tray to verified file on the share.

One worker thread, one job at a time, because there is one drive. State lives in
SQLite and every transition is written before it is acted on, so pulling the cable
mid-rip -- the expected operating condition (D4) -- leaves a job that can be read on
the next boot and honestly resolved rather than a process that vanished.

## What is and is not implemented

**Implemented:** identification and fingerprinting, duplicate refusal, preflight and
mode selection (D10/D11), driving `makemkvcon` in robot mode with real progress,
whole-file transfer with progress, read-back verification (D6), the retain-until-
pressure purge policy, and resume-or-fail of an interrupted job on boot.

**Not implemented: D11's byte-level follow-copy.** The uploader does not chase the
file as MakeMKV writes it, because two things have to be true first and neither is
established yet:

  1. **R8** -- whether MakeMKV writes an MKV linearly or seeks back at the end to
     finalise headers and Cues. If it rewrites, follow-copy is dead by construction.
  2. A transport that can write at an offset. `smbclient` cannot append, and taking a
     dependency that can costs a native build on this hardware (see the note in
     `shares.py`).

Until then a rip is transferred when it is complete, which makes burst and stream
differ only in when the tray opens. Preflight therefore still *refuses* a disc that
does not fit, which is D10 as originally written -- the refusal D11 was meant to
retire. `Transport.supports_follow_copy` is the seam; when it goes True, only
`_plan_transfer` below needs to change.
"""
import json
import os
import re
import shutil
import subprocess
import threading
import time

from . import db, notify, platform as P, shares as SH, system as SY

log = SY.component("Rip")

# Where a rip lands before it goes anywhere. Same partition the capacity numbers in
# _capacity() are computed against, so "room for 2 DVDs" and "will this fit" agree.
STAGING = P.STAGING

# Below this much free space the box cannot rip anything safely, whatever the disc is.
WINDOW_BYTES = 4 * 2 ** 30

# Anything shorter than this is a menu, a logo sting or a copyright card.
DEFAULT_MIN_TITLE = 120

_wake = threading.Event()
_stop = threading.Event()
_cancel = {}                    # job id -> threading.Event, for a cancel mid-flight


# ─────────────────────────────── the disc watcher ───────────────────────────────

def _disc_signature(drives):
    """What "a different disc is in the tray" means, cheaply.

    Polling rather than udev: udev would be a rules file, a privilege bridge and a
    dependency on the board's kernel, to learn something a 3-second poll of /dev/sr0
    answers just as well on a box doing nothing else.
    """
    d = next((x for x in drives if x.get("present")), None)
    if not d:
        return None
    return "%s|%s" % (d.get("device"), d.get("label") or "?")


def _watch_discs():
    seen = None
    while not _stop.wait(3):
        try:
            drives = P.optical_drives()
            # Auto Rip's own state is part of what "changed" means. Otherwise the
            # obvious sequence -- put the disc in, notice nothing happens, go and turn
            # Auto Rip on -- does nothing, because the disc has not changed since the
            # switch flipped. Somebody doing exactly the right thing would be met with
            # silence and no way to tell which of the two steps had failed.
            sig = (_disc_signature(drives), bool(db.get("auto_rip")))
            if sig == seen:
                continue
            seen = sig
            if sig[0] is None:
                continue
            if not sig[1]:
                log.info("Disc inserted (%s), but Auto Rip is off.", sig[0])
                continue
            st = _autorip_ready()
            if not st:
                continue
            job_id, why = enqueue()
            if job_id:
                log.info("Auto Rip queued job %d for %s", job_id, sig[0])
            else:
                log.info("Auto Rip did not queue this disc: %s", why)
        except Exception as e:
            log.error("Disc watch failed: %s", e)


def _autorip_ready():
    """Auto Rip's own gate, without importing main (which imports this module)."""
    if not P.optical_drives():
        return False
    if not P.makemkv_status().get("installed"):
        return False
    if not db.default_share():
        return False
    return True


# ─────────────────────────────── queueing ───────────────────────────────

def enqueue(force=False):
    """Queue the disc currently in the tray. Returns (job_id, reason_if_not).

    `force` is the "Re-rip" path: it skips the duplicate check, which is the only
    thing standing between a user and forty minutes they have already spent.
    """
    drives = P.optical_drives()
    d = next((x for x in drives if x.get("present")), None)
    if not d:
        return None, "There's no disc in the tray."

    if db.active_job():
        return None, "Riparr is already working on a disc."

    label = d.get("label") or ""
    fp = fingerprint(d)

    if not force:
        known = db.get_disc(fp)
        if known and known.get("ripped_at"):
            log.info("Refused a duplicate: %s", known.get("title") or label)
            notify.send("duplicate", title=known.get("title") or label,
                        body="Already ripped on %s. Ejected without re-reading it."
                             % time.strftime("%d %b %Y", time.localtime(known["ripped_at"])))
            P.eject()
            return None, "You've already ripped %s." % (known.get("title") or label)
        existing = db.job_for_fingerprint(fp, states=db.ACTIVE_STATES)
        if existing:
            return existing["id"], None

    job_id = db.create_job(
        title=None, disc_label=label, kind="movie", fingerprint=fp,
        state="queued", phase="Waiting to start",
        mode=None, bytes_total=int(d.get("size_bytes") or 0))
    _wake.set()
    return job_id, None


def cancel(job_id):
    job = db.get_job(job_id)
    if not job:
        return False, "No such job."
    if job["state"] in db.FINAL_STATES:
        return False, "That job has already finished."
    ev = _cancel.get(job_id)
    if ev:
        ev.set()
    db.update_job(job_id, state="cancelled", phase=None,
                  finished_at=int(time.time()), error="Cancelled")
    _cleanup_staging(job)
    return True, "Cancelled."


def answer(job_id, title_index=None, name=None, skip=False):
    """Resolve a `needs_input` job -- the other half of `on_unknown_disc: ask`.

    The answer is written to the disc record as well as the job, because the whole
    point of the fingerprint cache is that a disc is corrected at most once, ever (R5).
    """
    job = db.get_job(job_id)
    if not job or job["state"] != "needs_input":
        return False, "That job isn't waiting on an answer."
    if skip:
        db.update_job(job_id, state="cancelled", question=None,
                      finished_at=int(time.time()), error="Skipped")
        P.eject()
        return True, "Skipped, and the disc has been ejected."

    fields = {"state": "queued", "question": None, "phase": "Waiting to start"}
    if name:
        fields["title"] = name
    if title_index is not None:
        fields["chosen_title"] = int(title_index)
    db.update_job(job_id, **fields)
    if job.get("fingerprint"):
        db.record_disc(job["fingerprint"],
                       **{k: v for k, v in (("title", name),
                                            ("title_index", title_index))
                          if v is not None})
    _wake.set()
    return True, "Thanks — starting the rip."


# ─────────────────────────────── identification ───────────────────────────────

def fingerprint(drive):
    """A stable identity for a disc, without reading the whole thing.

    The volume label alone is not enough: DVDs ship labels like `LOGICAL_VOLUME_ID`
    and several different discs in a box set frequently share one. Structure --
    how many titles and how long each runs -- separates them, and is cheap because
    MakeMKV has to read the disc header anyway.
    """
    parts = [drive.get("label") or "", drive.get("media") or ""]
    try:
        for t in read_titles(drive.get("device")):
            parts.append("%d:%d" % (t["index"], t["seconds"]))
    except Exception:
        pass
    import hashlib
    return hashlib.sha1("|".join(parts).encode("utf-8", "replace")).hexdigest()


TINFO = re.compile(r'^TINFO:(\d+),(\d+),\d+,"(.*)"\s*$')
_DURATION = re.compile(r"^(?:(\d+):)?(\d+):(\d+)$")


def _seconds(text):
    m = _DURATION.match((text or "").strip())
    if not m:
        return 0
    h, mm, ss = m.group(1) or 0, m.group(2), m.group(3)
    return int(h) * 3600 + int(mm) * 60 + int(ss)


def read_titles(device):
    """Every title on the disc, with its runtime and size.

    Codes are MakeMKV's own AP_ItemAttributeId: 2 name, 9 duration, 10 size as text,
    11 size in bytes, 27 the output filename. Reading them by number is unpleasant and
    is what the tool gives; the alternative is parsing its human output, which is
    localised.
    """
    if P.MOCK:
        # A DVD-sized main title by default, because the mock card reports ~16 GiB free
        # and the happy path has to be reachable. RIPARR_MOCK_DISC_GB=28 turns this
        # into a Blu-ray and exercises the preflight refusal instead.
        gb = float(os.environ.get("RIPARR_MOCK_DISC_GB", "7.8"))
        return [
            {"index": 0, "seconds": 7860, "bytes": int(gb * 2 ** 30),
             "name": "The Matrix", "file": "title_t00.mkv"},
            {"index": 1, "seconds": 132, "bytes": 400 * 2 ** 20,
             "name": "Trailer", "file": "title_t01.mkv"},
            {"index": 2, "seconds": 61, "bytes": 90 * 2 ** 20,
             "name": "Menu loop", "file": "title_t02.mkv"},
        ]
    binary = shutil.which("makemkvcon") or "/usr/local/bin/makemkvcon"
    if not os.path.exists(binary):
        return []
    p = subprocess.run([binary, "-r", "--cache=1", "info", _disc_arg(device)],
                       capture_output=True, text=True, timeout=300)
    titles = {}
    for line in (p.stdout or "").splitlines():
        m = TINFO.match(line)
        if not m:
            continue
        idx, code, value = int(m.group(1)), int(m.group(2)), m.group(3)
        t = titles.setdefault(idx, {"index": idx, "seconds": 0, "bytes": 0,
                                    "name": "", "file": ""})
        if code == 9:
            t["seconds"] = _seconds(value)
        elif code == 11:
            t["bytes"] = int(value or 0)
        elif code == 2:
            t["name"] = value
        elif code == 27:
            t["file"] = value
    return [titles[k] for k in sorted(titles)]


def _disc_arg(device):
    """MakeMKV addresses drives by its own index, not by /dev path."""
    if not device:
        return "disc:0"
    m = re.search(r"sr(\d+)$", device)
    return "disc:%s" % (m.group(1) if m else "0")


_JUNK_LABEL = re.compile(r"^(?:logical_volume_id|dvd_video|bluray|untitled|unknown)$", re.I)


def pretty_label(label):
    """Turn a volume label into something a person would accept as a film title.

    Deliberately conservative. A label is evidence, not an answer, and a wrong name
    quietly pollutes a library -- which R5 says is worse than no name at all.
    """
    s = (label or "").strip()
    if not s or _JUNK_LABEL.match(s):
        return ""
    s = s.replace("_", " ").replace(".", " ")
    s = re.sub(r"\s*\b(disc|disk)\s*\d+\b", "", s, flags=re.I)
    s = re.sub(r"\b(bd|dvd|uhd|4k|1080p|2160p|remux|ntsc|pal)\b", "", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip(" -")
    if not s:
        return ""
    return s.title() if s.isupper() or s.islower() else s


def choose_title(titles, min_seconds, mode="main"):
    """Which title is the film.

    Longest-wins, with a floor. This is knowingly the naive answer: major-studio discs
    ship ~100 near-identical decoy playlists specifically to defeat it (R5). The
    defence is not a cleverer heuristic here, it is that the choice is remembered per
    fingerprint, so a disc that picks wrong is corrected once and never again.
    """
    usable = [t for t in titles if t["seconds"] >= min_seconds]
    if not usable:
        usable = list(titles)
    if not usable:
        return None
    if mode == "main":
        return max(usable, key=lambda t: t["seconds"])
    return max(usable, key=lambda t: t["seconds"])


def looks_obfuscated(titles, min_seconds):
    """Several long titles within a couple of minutes of each other.

    That is the signature of playlist obfuscation, and it is worth telling the user
    about even though we still have to guess: it converts "why is my film 4 minutes
    of a menu" into "Riparr said this disc was ambiguous and let me pick".
    """
    long_ones = sorted((t["seconds"] for t in titles if t["seconds"] >= max(min_seconds, 1800)),
                       reverse=True)
    if len(long_ones) < 3:
        return False
    return (long_ones[0] - long_ones[2]) < 120


# ─────────────────────────────── the pipeline ───────────────────────────────

def _settings():
    s = db.all_settings()
    s["min_title_seconds"] = int(s.get("min_title_seconds") or DEFAULT_MIN_TITLE)
    return s


def _staging_free():
    st = P.storage_status()
    return int(st.get("free_bytes") or 0)


def _plan_transfer(needed_bytes):
    """Mode selection (D11), honest about what this build can actually do.

    With follow-copy unavailable the whole title has to fit in staging, so this is
    D10's original refuse-before-starting check rather than D11's mode switch. The
    branch that is missing is the interesting one: when `supports_follow_copy` is
    True, "does not fit" stops being a refusal and becomes `stream`.
    """
    free = _staging_free()
    if free < WINDOW_BYTES:
        return None, ("There's not enough room on the card to rip anything safely. "
                      "Free some space, then try again.")
    if needed_bytes and free < needed_bytes + WINDOW_BYTES:
        if SH.Transport.supports_follow_copy:
            return "stream", None
        return None, ("This disc needs about %d GB and there's %d GB free. Let the "
                      "queue drain, or use a larger card."
                      % (needed_bytes // 2 ** 30, free // 2 ** 30))
    return "burst", None


def _job_dir(job_id):
    d = os.path.join(STAGING, "job-%d" % job_id)
    os.makedirs(d, exist_ok=True)
    return d


def _cleanup_staging(job):
    d = os.path.join(STAGING, "job-%d" % job["id"])
    if os.path.isdir(d):
        shutil.rmtree(d, ignore_errors=True)


# Characters SMB and NTFS refuse outright. A film called "Mission: Impossible" is not
# an edge case, and a rip that succeeds for forty minutes and then cannot be written
# because of a colon is the worst possible place to discover that.
_ILLEGAL = re.compile(r'[<>"|?*\x00-\x1f]')
_SEPARATORS = re.compile(r'[/\\:]')


def sanitise(name):
    # Separators become spaces rather than vanishing: "Face/Off" should read as
    # "Face Off", not "FaceOff". Everything else is simply dropped.
    s = _SEPARATORS.sub(" ", name or "")
    s = _ILLEGAL.sub("", s)
    s = re.sub(r"\s{2,}", " ", s).strip()
    # Windows also refuses a name ending in a dot or a space, silently, at the server.
    return s.rstrip(". ") or "Untitled"


def _render_template(template, title, year=None):
    """Fill a Plex/Jellyfin-convention naming template.

    Unknown placeholders are left alone rather than blanked: a template with a typo
    should produce a visibly odd name, not a file called ` ().mkv`.
    """
    values = {"Title": sanitise(title), "Year": str(year) if year else ""}
    out = template
    for k, v in values.items():
        out = out.replace("{%s}" % k, v)
    out = re.sub(r"\s*\(\)\s*", " ", out)           # an absent year leaves empty parens
    out = re.sub(r"\s{2,}", " ", out)
    out = re.sub(r"\s+(\.[A-Za-z0-9]+)$", r"\1", out)  # ...and a space before the suffix
    segments = []
    for seg in out.split("/"):
        seg = seg.strip()
        if not seg:
            continue
        # Only the leaf keeps its extension; a trailing dot on a directory is invalid.
        segments.append(seg if seg is out.split("/")[-1].strip() else seg.rstrip(". "))
    return "/".join(segments)


# Only a *bracketed* year counts, and the last one wins.
#
# Both rules are the same lesson from "Blade Runner 2049 (2017)". Taking the first
# year-shaped token gives "Blade Runner (2017) (2049)"; accepting a bare trailing
# number turns a disc labelled BLADE_RUNNER_2049 into "Blade Runner (2049)" -- a
# confident, wrong answer. Volume labels are uppercase with underscores and never
# contain brackets, so in practice a label yields no year at all and the file is named
# "Blade Runner 2049.mkv", which Plex matches and which claims nothing untrue. A year
# only appears when a human typed one into the identify prompt. R5: get it right most
# of the time, make it easy to fix, and never assert what you do not know.
_YEAR_BRACKETED = re.compile(r"[\(\[](19\d{2}|20\d{2})[\)\]]")


def _split_year(name):
    name = (name or "").strip()
    m = None
    for m in _YEAR_BRACKETED.finditer(name):
        pass                                  # keep the last
    if m is None:
        return name, None
    cleaned = (name[:m.start()] + " " + name[m.end():])
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" -")
    return cleaned, m.group(1)


# ── stage 1: work out what this disc is ──

def _identify(job, s):
    db.update_job(job["id"], state="identifying", phase="Reading the disc",
                  started_at=job.get("started_at") or int(time.time()))
    drives = P.optical_drives()
    d = next((x for x in drives if x.get("present")), None)
    if not d:
        raise RipFailed("The disc was removed before Riparr could read it.")

    titles = read_titles(d.get("device"))
    if not titles:
        raise RipFailed("Riparr couldn't read any titles from this disc. "
                        "If it's dirty or scratched, clean it and try again.")

    remembered = db.get_disc(job.get("fingerprint") or "") or {}
    chosen_index = job.get("chosen_title")
    if chosen_index is None:
        chosen_index = remembered.get("title_index")

    if chosen_index is not None:
        chosen = next((t for t in titles if t["index"] == int(chosen_index)), None)
    else:
        chosen = choose_title(titles, s["min_title_seconds"], s.get("rip_mode", "main"))
    if not chosen:
        raise RipFailed("Nothing on this disc is longer than %d seconds, so there is "
                        "nothing worth ripping." % s["min_title_seconds"])

    name = job.get("title") or remembered.get("title") or pretty_label(d.get("label"))
    ambiguous = looks_obfuscated(titles, s["min_title_seconds"])

    # The one place `on_unknown_disc` has ever been able to mean anything.
    if (not name or ambiguous) and chosen_index is None:
        behaviour = s.get("on_unknown_disc", "ask")
        if behaviour == "skip":
            db.update_job(job["id"], state="cancelled", finished_at=int(time.time()),
                          error="Couldn't identify the disc, and the setting is to skip.")
            P.eject()
            return None
        if behaviour == "label" and d.get("label"):
            name = pretty_label(d.get("label")) or d.get("label")
        else:
            question = ("Riparr couldn't work out what this disc is."
                        if not name else
                        "This disc has several titles of almost the same length, which "
                        "usually means the studio is hiding the real one. Pick the film.")
            db.update_job(job["id"], state="needs_input", question=question,
                          phase="Waiting for you",
                          titles=titles, title=name or None,
                          chosen_title=chosen["index"],
                          disc_label=d.get("label") or job.get("disc_label"))
            log.info("Job %d needs a human: %s", job["id"], question)
            notify.send("needs_you", title=name or d.get("label") or "A disc",
                        body=question)
            return None

    title_name, year = _split_year(name)
    db.update_job(job["id"], title=title_name, chosen_title=chosen["index"],
                  titles=titles, bytes_total=chosen.get("bytes") or 0,
                  disc_label=d.get("label") or job.get("disc_label"))
    job = db.get_job(job["id"])
    job["_title"] = chosen
    job["_year"] = year
    job["_device"] = d.get("device")
    return job


# ── stage 2: get it off the disc ──

PRGV = re.compile(r"^PRGV:(\d+),(\d+),(\d+)")
PRGC = re.compile(r'^PRGC:\d+,\d+,"(.*)"')
MSG = re.compile(r'^MSG:(\d+),\d+,\d+,"(.*?)"')


def _rip(job, s, cancel_ev):
    out_dir = _job_dir(job["id"])
    title = job["_title"]
    db.update_job(job["id"], state="ripping", phase="Reading the disc",
                  local_path=None, bytes_ripped=0)

    if P.MOCK:
        return _mock_rip(job, out_dir, cancel_ev)

    binary = shutil.which("makemkvcon") or "/usr/local/bin/makemkvcon"
    cmd = [binary, "-r", "--progress=-same",
           "--minlength=%d" % s["min_title_seconds"],
           "mkv", _disc_arg(job.get("_device")), str(title["index"]), out_dir]
    log.info("Job %d: %s", job["id"], " ".join(cmd))

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1)
    last_msg = ""
    started = time.time()
    try:
        for line in proc.stdout:
            if cancel_ev.is_set():
                proc.terminate()
                raise Cancelled()
            line = line.strip()
            m = PRGV.match(line)
            if m:
                cur, _, mx = (int(x) for x in m.groups())
                frac = (cur / mx) if mx else 0
                total = job.get("bytes_total") or title.get("bytes") or 0
                done = int(total * frac)
                elapsed = time.time() - started
                eta = int(elapsed / frac - elapsed) if frac > 0.01 else None
                db.update_job(job["id"], bytes_ripped=done, eta_seconds=eta,
                              phase=last_msg or "Reading the disc")
                continue
            m = PRGC.match(line)
            if m:
                last_msg = m.group(1)
                continue
            m = MSG.match(line)
            if m:
                last_msg = m.group(2)
    finally:
        try:
            proc.stdout.close()
        except Exception:
            pass
    rc = proc.wait()

    produced = sorted(f for f in os.listdir(out_dir) if f.lower().endswith(".mkv"))
    if rc != 0 or not produced:
        raise RipFailed(last_msg or "MakeMKV couldn't read this disc. If it's dirty "
                                    "or scratched, clean it and try again.")
    path = os.path.join(out_dir, produced[0])
    db.update_job(job["id"], local_path=path, bytes_ripped=os.path.getsize(path),
                  bytes_total=os.path.getsize(path), eta_seconds=None)
    return path


def _mock_rip(job, out_dir, cancel_ev):
    """A rip that takes a believable amount of time and produces a real file.

    Small (32 MiB) but genuinely written, hashed and transferred, so every stage
    downstream of here is exercised for real off-hardware rather than stubbed.
    """
    path = os.path.join(out_dir, "title_t00.mkv")
    total = 32 * 2 ** 20
    chunk = total // 40
    written = 0
    started = time.time()
    with open(path, "wb") as f:
        while written < total:
            if cancel_ev.is_set():
                raise Cancelled()
            n = min(chunk, total - written)
            f.write(b"\0" * n)
            f.flush()
            written += n
            elapsed = time.time() - started
            frac = written / total
            db.update_job(job["id"], bytes_ripped=written, bytes_total=total,
                          phase="Reading title %d" % job["_title"]["index"],
                          eta_seconds=int(elapsed / frac - elapsed) if frac > 0.05 else None)
            time.sleep(0.25)
    db.update_job(job["id"], local_path=path, bytes_ripped=total, bytes_total=total,
                  eta_seconds=None)
    return path


# ── stage 3: get it onto the share ──

def _transfer(job, s, local_path, cancel_ev):
    share = db.default_share()
    if not share:
        raise RipFailed("There's no library share configured, so the rip has nowhere "
                        "to go. It's still on the card.")

    title = job.get("title") or job.get("disc_label") or "Unknown"
    year = job.get("_year")
    folder = (s.get("movie_folder") or "Movies").strip("/")
    rel = _render_template(s.get("movie_template") or "{Title} ({Year})/{Title} ({Year}).mkv",
                           title, year)
    name = "%s/%s" % (folder, rel) if folder else rel

    transport = SH.Transport(share)
    total = os.path.getsize(local_path)
    db.update_job(job["id"], state="transferring", phase="Sending to your library",
                  dest_path=transport.describe(name), bytes_sent=0, bytes_total=total)

    started = time.time()

    def progress(sent, of):
        elapsed = time.time() - started
        frac = (sent / of) if of else 0
        db.update_job(job["id"], bytes_sent=sent,
                      eta_seconds=int(elapsed / frac - elapsed) if frac > 0.02 else None)

    # D11's backpressure, in the form this build can honour: the rip is already safe on
    # the card, so a sleeping NAS is a wait, never a failure. The disc is not held --
    # it has already been read -- so the user can carry on feeding the machine.
    waited = 0
    while not transport.reachable():
        if cancel_ev.is_set():
            raise Cancelled()
        if waited == 0:
            log.info("Job %d: the share is unreachable; waiting.", job["id"])
            notify.send("share_lost", title=title,
                        body="Your library share isn't answering. The rip is safe on "
                             "the card and will finish on its own when the share is back.")
            db.update_job(job["id"], phase="Waiting for your library to come back")
        if waited > 6 * 3600:
            raise RipFailed("Your library share hasn't answered in six hours. The rip "
                            "is safe on the card — fix the share and retry this job.")
        time.sleep(min(60, 5 + waited // 10))
        waited += 30

    r = transport.put(local_path, name, progress=progress, cancel=cancel_ev)
    if cancel_ev.is_set():
        raise Cancelled()
    if not r.get("ok"):
        raise RipFailed("Couldn't write to your library: %s" % r.get("error"))
    db.update_job(job["id"], bytes_sent=total, eta_seconds=None)
    return transport, name


# ── stage 4: prove it arrived ──

def _verify(job, s, transport, name, local_path):
    if not s.get("verify_after_transfer", True):
        return
    db.update_job(job["id"], state="verifying", phase="Checking it arrived intact",
                  bytes_verified=0)

    def progress(done, total):
        db.update_job(job["id"], bytes_verified=done)

    r = SH.verify_remote(transport, name, local_path, progress=progress)
    if not r.get("ok"):
        raise RipFailed("The file reached your library but didn't verify: %s"
                        % r.get("error"))


# ── stage 5: tidy up ──

def _finish(job, s, transport, name, local_path):
    now = int(time.time())
    if job.get("fingerprint"):
        db.record_disc(job["fingerprint"], label=job.get("disc_label"),
                       title=job.get("title"), kind="movie", ripped_at=now,
                       title_index=job.get("chosen_title"), job_id=job["id"])

    # D6: a verified copy is kept until the space is needed, so a downstream problem
    # is a re-copy rather than a re-rip. "Not now" is the only correct time to delete
    # something that took forty minutes to make.
    if not s.get("keep_local_copy", True):
        _cleanup_staging(job)

    db.update_job(job["id"], state="done", phase=None, finished_at=now,
                  eta_seconds=None, error=None,
                  dest_path=transport.describe(name))
    P.eject()
    log.info("Job %d finished: %s", job["id"], transport.describe(name))
    notify.send("done", title=job.get("title") or job.get("disc_label") or "A disc",
                body="Ripped and verified. It's in your library at %s."
                     % transport.describe(name))


class RipFailed(Exception):
    pass


class Cancelled(Exception):
    pass


def _run_job(job):
    s = _settings()
    job_id = job["id"]                   # `job` is rebound below; the id must not be
    cancel_ev = threading.Event()
    _cancel[job_id] = cancel_ev
    try:
        job = _identify(job, s)
        if job is None:
            return                       # needs_input, skipped, or ejected

        mode, refusal = _plan_transfer(job.get("bytes_total") or 0)
        if refusal:
            raise RipFailed(refusal)
        db.update_job(job["id"], mode=mode)
        job["mode"] = mode

        local_path = _rip(job, s, cancel_ev)
        transport, name = _transfer(job, s, local_path, cancel_ev)
        _verify(job, s, transport, name, local_path)
        _finish(job, s, transport, name, local_path)

    except Cancelled:
        log.info("Job %d cancelled.", job_id)
        db.update_job(job_id, state="cancelled", phase=None,
                      finished_at=int(time.time()), error="Cancelled")
        _cleanup_staging({"id": job_id})
    except RipFailed as e:
        log.error("Job %d failed: %s", job_id, e)
        row = db.get_job(job_id) or {}
        db.update_job(job_id, state="failed", phase=None,
                      finished_at=int(time.time()), error=str(e))
        P.eject()
        notify.send("failed",
                    title=row.get("title") or row.get("disc_label") or "A disc",
                    body=str(e))
    except Exception as e:
        log.exception("Job %d hit an unexpected error", job_id)
        row = db.get_job(job_id) or {}
        db.update_job(job_id, state="failed", phase=None,
                      finished_at=int(time.time()),
                      error="Something went wrong: %s" % e)
        notify.send("failed", title=row.get("title") or "A disc", body=str(e))
    finally:
        _cancel.pop(job_id, None)


# ─────────────────────────────── boot recovery ───────────────────────────────

def recover():
    """Resolve anything that was mid-flight when the power went (D4).

    A rip cannot be resumed -- MakeMKV has no such notion -- but a *transfer* can be
    retried, because the file it was sending is still on the card. Distinguishing the
    two is the difference between "we lost your forty minutes" and "carrying on".
    """
    for job in db.list_jobs(states=db.INTERRUPTIBLE, limit=50):
        local = job.get("local_path")
        if job["state"] in ("transferring", "verifying") and local and os.path.exists(local):
            log.info("Job %d was interrupted mid-transfer; requeueing it.", job["id"])
            db.update_job(job["id"], state="queued", phase="Waiting to start",
                          bytes_sent=0, bytes_verified=0,
                          attempts=(job.get("attempts") or 0) + 1)
            continue
        log.info("Job %d was interrupted mid-rip and can't be resumed.", job["id"])
        db.update_job(
            job["id"], state="failed", phase=None, finished_at=int(time.time()),
            error="The power went out partway through. Nothing was left in your "
                  "library — put the disc back in to start it again.")
        _cleanup_staging(job)


def resume_transfer(job_id):
    """Retry a failed job whose rip is still on the card. Cheap; no re-read."""
    job = db.get_job(job_id)
    if not job:
        return False, "No such job."
    local = job.get("local_path")
    if not local or not os.path.exists(local):
        return False, "That rip is no longer on the card, so it has to be re-ripped."
    db.update_job(job_id, state="queued", phase="Waiting to start", error=None,
                  bytes_sent=0, bytes_verified=0, finished_at=None,
                  attempts=(job.get("attempts") or 0) + 1)
    _wake.set()
    return True, "Retrying the transfer."


# ─────────────────────────────── the worker ───────────────────────────────

def _loop():
    while not _stop.is_set():
        job = db.next_queued_job()
        if not job:
            _wake.wait(5)
            _wake.clear()
            continue
        try:
            # A requeued transfer skips straight past the disc.
            if job.get("local_path") and os.path.exists(job["local_path"]):
                _rerun_transfer(job)
            else:
                _run_job(job)
        except Exception as e:
            log.exception("Worker error on job %s: %s", job.get("id"), e)
            db.update_job(job["id"], state="failed", finished_at=int(time.time()),
                          error="Something went wrong: %s" % e)


def _rerun_transfer(job):
    s = _settings()
    cancel_ev = threading.Event()
    _cancel[job["id"]] = cancel_ev
    try:
        job["_year"] = _split_year(job.get("title") or "")[1]
        transport, name = _transfer(job, s, job["local_path"], cancel_ev)
        _verify(job, s, transport, name, job["local_path"])
        _finish(job, s, transport, name, job["local_path"])
    except Cancelled:
        db.update_job(job["id"], state="cancelled", finished_at=int(time.time()),
                      error="Cancelled")
    except RipFailed as e:
        db.update_job(job["id"], state="failed", finished_at=int(time.time()),
                      error=str(e))
        notify.send("failed", title=job.get("title") or "A disc", body=str(e))
    finally:
        _cancel.pop(job["id"], None)


def start():
    os.makedirs(STAGING, exist_ok=True)
    recover()
    threading.Thread(target=_loop, name="riparr-rip", daemon=True).start()
    threading.Thread(target=_watch_discs, name="riparr-disc-watch", daemon=True).start()
    log.info("Rip engine started.")


def stop():
    _stop.set()
    _wake.set()
