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

from . import db, led as LED, notify, platform as P, shares as SH, system as SY

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

    # Before anything expensive: can this drive read this disc at all? Refused here
    # rather than three minutes later inside MakeMKV, and the disc comes back out --
    # a job that exists only to fail is a worse answer than never taking the disc.
    #
    # LibreDrive is asked only for a 4K disc. It costs a `makemkvcon` run, it is
    # irrelevant to DVD and to 1080p Blu-ray, and paying for it on every disc would
    # put a minute of dead time in front of the common case to serve the rare one.
    # block=True: the refusal below has to be right, and a UHD disc is worth the wait.
    libredrive = P.libredrive_status(d, block=True) if disc_family(d) == "uhd" else None
    refusal = unreadable_reason(d, libredrive)
    if refusal:
        log.info("Refused a disc this drive cannot read: %s", refusal)
        notify.send("failed", title=d.get("label") or "A disc", body=refusal)
        LED.announce("failed")
        P.eject()
        return None, refusal

    label = d.get("label") or ""

    # The row exists *before* the slow part, not after. `fingerprint()` reads the disc
    # structure through makemkvcon, which is minutes on a real drive -- and while it
    # ran there was no job, so the queue had nothing to draw and sat on "Rip this disc"
    # for the whole identification. The click looked like it had done nothing, which is
    # the one impression an appliance cannot afford. `identifying` is a state the
    # interface already renders ("Reading the disc"), so this costs no new UI.
    job_id = db.create_job(
        title=None, disc_label=label, kind="movie", fingerprint="",
        state="identifying", phase="Reading the disc \u2014 a few minutes on an encrypted DVD",
        mode=None, bytes_total=int(d.get("size_bytes") or 0))

    # The identify stage opens *here*, not in `_identify`. The scan is minutes long and
    # it happens inside enqueue -- the worker's later call is a cache hit -- so timing
    # it from the worker would record the slowest stage of the whole rip as zero
    # seconds, and the one stage that most needs a "usually about nine minutes" would
    # be the one stage that never got one.
    db.stage_enter(job_id, "identify")

    def _abandon(reason):
        """Take the row back down when the disc turns out not to be rippable."""
        db.stage_end(job_id)
        db.update_job(job_id, state="cancelled", phase=None,
                      finished_at=int(time.time()), error=reason)

    # The scan that costs the minutes happens here, inside enqueue -- the worker's later
    # call is a cache hit. Reporting from the worker therefore reported nothing, and the
    # first ten minutes of every rip stayed at "no idea". The row already exists by this
    # point, so it can carry the number.
    def scan_progress(frac, msg=None):
        fields = {}
        if frac is not None:
            fields["stage_pct"] = round(frac, 4)
        if msg:
            fields["phase"] = msg
        if fields:
            db.update_job(job_id, **fields)

    fp = fingerprint(d, on_progress=scan_progress)
    db.update_job(job_id, fingerprint=fp)

    if not force:
        known = db.get_disc(fp)
        if known and known.get("ripped_at"):
            log.info("Refused a duplicate: %s", known.get("title") or label)
            notify.send("duplicate", title=known.get("title") or label,
                        body="Already ripped on %s. Ejected without re-reading it."
                             % time.strftime("%d %b %Y", time.localtime(known["ripped_at"])))
            LED.announce("duplicate")
            P.eject()
            msg = "You've already ripped %s." % (known.get("title") or label)
            _abandon(msg)
            return None, msg
        # Exclude the row just created, which now carries this same fingerprint.
        existing = db.job_for_fingerprint(fp, states=db.ACTIVE_STATES)
        if existing and existing["id"] != job_id:
            _abandon("Already queued as job %d." % existing["id"])
            return existing["id"], None

    # Close identify before the job goes into the queue. A job that waits behind
    # another disc can sit at "queued" for half an hour, and leaving the stage open
    # across that would record the wait as scan time and poison the median with it.
    # `_identify` reopens it for its own pass, which is a cache hit and costs seconds;
    # the two runs sum to the truth.
    db.stage_end(job_id)
    db.update_job(job_id, state="queued", phase="Waiting to start")
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


# ────────────────────────── what this drive can do with this disc ──────────────────────────

# A BD-ROM dual layer tops out at 50 GB. UHD Blu-ray uses 66 GB and 100 GB media, so
# anything above 50 GB is certainly UHD -- but the converse does not hold: 50 GB UHD
# discs exist and are indistinguishable by size. This constant therefore proves UHD
# and never disproves it, which is exactly how `disc_family()` uses it.
BD_DL_BYTES = 50 * 2 ** 30

# What to call each family when talking to a person. "BD-ROM" is what the drive says;
# it is not what is printed on the box the disc came in.
DISC_WORD = {"dvd": "DVD", "bluray": "Blu-ray", "uhd": "4K UHD disc"}


def disc_family(drive):
    """Riparr's three families -- "dvd", "bluray", "uhd" -- or None.

    Not the same question as the MMC profile, and the gap is the point: **there is no
    UHD profile**. A UHD disc reports BD-ROM exactly like a 1080p one (`optical.py`),
    so the only thing that separates them without decrypting anything is capacity, and
    capacity only separates them in one direction. A disc reported as "bluray" here may
    still be UHD; a disc reported as "uhd" certainly is.
    """
    kind = drive.get("media_kind")
    if kind == "bluray" and (drive.get("size_bytes") or 0) > BD_DL_BYTES:
        return "uhd"
    return kind


def unreadable_reason(drive, libredrive=None):
    """Why this drive cannot rip the disc that is in it, or None if it can try.

    Only certainties refuse. "This drive has no Blu-ray support and there is a Blu-ray
    in it" is a certainty, and so is MakeMKV itself reporting that LibreDrive is
    unavailable on a 4K disc -- that one is worth forty minutes, which is why it is
    asked for here despite costing a `makemkvcon` run.

    "This drive is not on Riparr's UHD list" is **not** a certainty. The list is finite
    and the world is not, so that case warns through `uhd_warning()` and lets MakeMKV
    have its say: being told no by software that was guessing is the one failure mode
    worse than a slow failure.

    Note that LibreDrive says nothing about 1080p Blu-ray, which is AACS 1.0 and needs
    none of this. Only the UHD branch may consult it.
    """
    family = disc_family(drive)
    if family == "dvd" and not drive.get("reads_dvd"):
        return "There's a DVD in the tray and this drive can't read DVDs."
    if family in ("bluray", "uhd") and not drive.get("reads_bluray"):
        return ("There's a %s in the tray and this drive only reads DVDs — "
                "ripping it needs a Blu-ray drive. See the drive list in the setup "
                "guide." % DISC_WORD[family])
    if family == "uhd" and drive.get("uhd") == "no":
        return ("This is a 4K UHD disc and this drive can't read UHD media. 4K needs "
                "one of a small number of specific drives — see the drive list "
                "in the setup guide.")
    if family == "uhd" and libredrive == "no":
        return ("This is a 4K UHD disc, and MakeMKV reports it can't get underneath "
                "this drive's firmware — which is the only way to read 4K on this "
                "hardware. The disc won't decode in this drive. See the drive list "
                "in the setup guide.")
    return None


def uhd_warning(drive, libredrive=None):
    """The honest hedge for a UHD disc in a drive nobody has confirmed, or None.

    Surfaced rather than acted on. Riparr starts the rip anyway: MakeMKV is the only
    thing that truly knows, and its answer arrives in about a minute.
    """
    if disc_family(drive) != "uhd":
        return None
    if libredrive == "enabled":
        return None
    if libredrive == "no":
        return ("MakeMKV reports it can't get underneath this drive's firmware, so a "
                "4K disc will almost certainly fail to decode. It's being tried "
                "anyway, because MakeMKV is the only thing that can say for certain.")
    if drive.get("uhd") in ("unknown", "firmware"):
        return ("This is a 4K UHD disc. 4K needs a drive on MakeMKV's LibreDrive "
                "list, often on particular firmware, and this one hasn't been "
                "confirmed. Riparr is trying it — if it fails to decode, "
                "that is why.")
    return None


# ─────────────────────────────── identification ───────────────────────────────

def fingerprint(drive, on_progress=None):
    """A stable identity for a disc, without reading the whole thing.

    The volume label alone is not enough: DVDs ship labels like `LOGICAL_VOLUME_ID`
    and several different discs in a box set frequently share one. Structure --
    how many titles and how long each runs -- separates them, and is cheap because
    MakeMKV has to read the disc header anyway.
    """
    parts = [drive.get("label") or "", drive.get("media") or ""]
    try:
        for t in read_titles(drive.get("device"), drive, on_progress=on_progress):
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


# One disc gets read once. `fingerprint()` needs the title structure to tell two discs
# with the same label apart, and the worker needs it again a moment later to choose
# what to rip -- and each read is a ~2.5 minute `makemkvcon` run on a real drive, so
# doing it twice put five minutes of silence in front of every rip. Keyed on the same
# cheap signature the disc watcher uses, so swapping discs invalidates it.
_titles_cache = {"key": None, "titles": None, "at": 0.0}
TITLES_TTL = 1800
# Ceiling for one `makemkvcon info` scan. See the note at the subprocess call.
TITLES_TIMEOUT = 1800


def _titles_key(disc):
    if not disc:
        return None
    return "%s|%s|%s" % (disc.get("device"), disc.get("label") or "?",
                         disc.get("size_bytes") or 0)


def read_titles(device, disc=None, on_progress=None):
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
    key = _titles_key(disc)
    if key and _titles_cache["key"] == key and _titles_cache["titles"] \
            and time.time() - _titles_cache["at"] < TITLES_TTL:
        return _titles_cache["titles"]

    binary = shutil.which("makemkvcon") or "/usr/local/bin/makemkvcon"
    if not os.path.exists(binary):
        return []
    # 300s was killing real discs mid-scan. An encrypted retail DVD makes MakeMKV do
    # the decryption work in software, and on four A53 cores that is CPU-bound for
    # minutes -- measured on the reference board with nothing else touching the drive:
    # over two minutes of user CPU and still adding titles. The scan is interruptible
    # from the interface, so a generous ceiling costs nothing and a tight one cost
    # every rip attempted on this box.
    # Streamed rather than captured whole, so the scan can say how far along it is.
    # makemkvcon emits PRGV throughout `info`; it was being thrown away, which is why
    # reading an encrypted disc looked like nine minutes of nothing happening.
    proc = subprocess.Popen(
        [binary, "-r", "--cache=1", "info", _disc_arg(device)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    out_lines = []
    found = [0]
    deadline = time.time() + TITLES_TIMEOUT
    try:
        for raw in proc.stdout:
            out_lines.append(raw)
            if time.time() > deadline:
                proc.kill()
                raise subprocess.TimeoutExpired(binary, TITLES_TIMEOUT)
            if on_progress:
                # `makemkvcon info` emits no PRGV at all -- a full scan of a real disc
                # is 172 MSG lines and 16 DRV lines and nothing else, so there is no
                # percentage to be had here however much one is wanted. What it does do
                # is announce each title as it finds it (MSG 3028), and a rising count
                # is honest evidence of motion where a fabricated percentage would not
                # be. The scan is CPU-bound for minutes; this is what keeps it company.
                mm = MSG.match(raw.strip())
                if mm and mm.group(1) == "3028":
                    found[0] += 1
                    # "titles" is DVD jargon and reads as "films" to everyone else --
                    # a disc reporting "17 titles found" sounds like it is about to rip
                    # seventeen movies. It is menus, trailers, idents and chapter stubs;
                    # exactly one of them, the longest, becomes the film.
                    on_progress(None, "Reading the disc \u2014 %d track%s catalogued"
                                % (found[0], "" if found[0] == 1 else "s"))
    finally:
        try:
            proc.stdout.close()
        except Exception:
            pass
        proc.wait()

    class _P:
        stdout = "".join(out_lines)
    p = _P()
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
    out = [titles[k] for k in sorted(titles)]
    if key and out:
        _titles_cache.update(key=key, titles=out, at=time.time())
    return out


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
    db.update_job(job["id"], state="identifying",
                  phase="Reading the disc \u2014 a few minutes on an encrypted DVD",
                  started_at=job.get("started_at") or int(time.time()))
    db.stage_enter(job["id"], "identify")
    drives = P.optical_drives()
    d = next((x for x in drives if x.get("present")), None)
    if not d:
        raise RipFailed("The disc was removed before Riparr could read it.")

    # Report the scan as it goes. Nine minutes of "Reading the disc" with a sweeping
    # bar is honest but it is not company: makemkvcon knows how far through it is, and
    # the user should too.
    def identify_progress(frac, msg=None):
        fields = {}
        if frac is not None:
            fields["stage_pct"] = round(frac, 4)
        if msg:
            fields["phase"] = msg
        if fields:
            db.update_job(job["id"], **fields)

    titles = read_titles(d.get("device"), d, on_progress=identify_progress)
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
            db.stage_end(job["id"])
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
            db.stage_end(job["id"])
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
    # The UHD hedge is recorded, not acted on: MakeMKV is the only thing that can say
    # for certain whether this drive will decode the disc, and it answers in a minute.
    warning = uhd_warning(d, P.libredrive_status(d, block=True))
    if warning:
        log.warning("Job %d: %s", job["id"], warning)
    db.stage_end(job["id"])
    db.update_job(job["id"], title=title_name, chosen_title=chosen["index"],
                  titles=titles, bytes_total=chosen.get("bytes") or 0,
                  warning=warning, disc_family=disc_family(d),
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


def _output_size(out_dir):
    """How big the MKV being written is right now, or 0 before it exists.

    MakeMKV names the file from the title, so this takes whatever .mkv turned up
    rather than assuming a name.
    """
    try:
        return max((os.path.getsize(os.path.join(out_dir, f))
                    for f in os.listdir(out_dir) if f.endswith(".mkv")), default=0)
    except OSError:
        return 0


def _rip(job, s, cancel_ev):
    out_dir = _job_dir(job["id"])
    title = job["_title"]
    # Remember the disc now that we know what it is. record_disc used to happen only in
    # _finish(), after verification -- so a disc that ripped, uploaded and landed in the
    # library but failed the read-back left no trace anywhere, and the Discs page stayed
    # empty after a rip the user watched happen. `ripped_at` is still set only by
    # _finish(), so duplicate refusal continues to mean "verified", not "attempted".
    if job.get("fingerprint"):
        db.record_disc(job["fingerprint"], label=job.get("disc_label"),
                       title=job.get("title"), kind="movie",
                       title_index=job.get("chosen_title"))
    db.update_job(job["id"], state="ripping", phase="Reading the disc",
                  local_path=None, bytes_ripped=0, stage_pct=0)
    # Two stages wearing one state. MakeMKV analyses and decrypts for minutes before it
    # opens the output file, and that silent stretch is the one users read as a hang --
    # so it is timed separately, and the split point is the moment a byte lands.
    db.stage_start(job["id"], "decrypt")

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
    first_byte_at = None
    try:
        for line in proc.stdout:
            if cancel_ev.is_set():
                proc.terminate()
                raise Cancelled()
            line = line.strip()
            m = PRGV.match(line)
            if m:
                # Progress comes from the file that is actually growing, not from
                # MakeMKV's operation counter. Neither PRGV field can drive one
                # continuous bar: field 1 restarts on every sub-operation (title sets,
                # contents, decrypt, save) and field 2 restarts once, when the save
                # begins. Both read past 90% while the process had written literally
                # zero bytes and staging was empty -- a bar that is smooth and wrong.
                #
                # The output file's size against the title's expected size is what a
                # person means by "how far along is it", and it is the same number
                # `bytes_ripped` has always claimed to be. PRGV still drives this
                # branch because it is MakeMKV's heartbeat; it just no longer supplies
                # the number. Before the file exists this reports 0, which the
                # interface already renders as an indeterminate sweep.
                total = job.get("bytes_total") or title.get("bytes") or 0
                done = _output_size(out_dir)
                if done and not first_byte_at:
                    first_byte_at = time.time()
                    db.stage_start(job["id"], "save")
                # `total` is MakeMKV's estimate of the title, so the finished file can
                # overshoot it. Hold just short of full until the process has actually
                # exited: a bar that sits at 100% while work continues is the same lie
                # as one that sits at zero while work happens, and `_finish` sets the
                # real figure from the file on disk.
                if total and done > total:
                    done = int(total * 0.99)
                frac = (done / total) if total else 0
                # Rate is measured from the first byte on disk, so the minutes of
                # decryption before the save do not drag the estimate down.
                eta = None
                if first_byte_at and frac > 0.01:
                    writing = time.time() - first_byte_at
                    if writing > 5:
                        eta = int(writing / frac - writing)
                db.update_job(job["id"], bytes_ripped=done, eta_seconds=eta,
                              stage_pct=round(frac, 4),
                              phase=last_msg or "Reading the disc")
                continue
            m = PRGC.match(line)
            if m:
                last_msg = m.group(1)
                continue
            # MSG lines are MakeMKV's running commentary -- "Automatic SDF downloading
            # is disabled or failed", "Title #22 has length of 33 seconds which is less
            # than minimum". Accurate, addressed to somebody debugging MakeMKV, and
            # alarming on an appliance: the word "failed" during a healthy rip is how
            # you get someone pulling the cable. PRGC (the operation name) is what the
            # phase line shows; MSG stays in the log where it is useful.
            m = MSG.match(line)
            if m:
                log.info("Job %d: %s", job["id"], m.group(2))
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
    db.stage_end(job["id"])
    db.update_job(job["id"], local_path=path, bytes_ripped=os.path.getsize(path),
                  bytes_total=os.path.getsize(path), eta_seconds=None)
    return path


def _mock_rip(job, out_dir, cancel_ev):
    """A rip that takes a believable amount of time and produces a real file.

    Small (32 MiB) but genuinely written, hashed and transferred, so every stage
    downstream of here is exercised for real off-hardware rather than stubbed.
    """
    path = os.path.join(out_dir, "title_t00.mkv")
    # `_rip` already opened "decrypt". Stand in for MakeMKV's silent analysis pass so
    # the stage breakdown off-hardware has the same shape it has on the box.
    time.sleep(1.5)
    db.stage_start(job["id"], "save")
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
                          stage_pct=round(frac, 4),
                          phase="Reading title %d" % job["_title"]["index"],
                          eta_seconds=int(elapsed / frac - elapsed) if frac > 0.05 else None)
            time.sleep(0.25)
    db.stage_end(job["id"])
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
    db.stage_start(job["id"], "upload")
    db.update_job(job["id"], state="transferring", phase="Sending to your library",
                  dest_path=transport.describe(name), remote_name=name,
                  bytes_sent=0, bytes_total=total)

    started = time.time()

    def progress(sent, of):
        elapsed = time.time() - started
        frac = (sent / of) if of else 0
        # Put the phase back. A share that went away sets "Waiting for your library to
        # come back", and nothing cleared it once bytes started moving again -- so the
        # box sat there claiming to be waiting while the bar climbed past 10%.
        db.update_job(job["id"], bytes_sent=sent, phase="Sending to your library",
                      stage_pct=round(frac, 4),
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
    db.stage_end(job["id"])
    db.update_job(job["id"], bytes_sent=total, eta_seconds=None)
    return transport, name


# ── stage 4: prove it arrived ──

def _verify(job, s, transport, name, local_path):
    # Old boolean setting, honoured so an upgrade does not silently change behaviour.
    mode = s.get("verify_mode") or ("deep" if s.get("verify_after_transfer", True)
                                    else "off")
    if mode == "off":
        db.update_job(job["id"], verified_mode="off")
        return

    db.stage_start(job["id"], "verify")
    db.update_job(job["id"], state="verifying", bytes_verified=0,
                  phase=("Checking the size on your library" if mode == "quick"
                         else "Reading it back to check every byte"))

    def progress(done, total):
        db.update_job(job["id"], bytes_verified=done,
                      stage_pct=round(done / total, 4) if total else None)

    r = SH.verify_remote(transport, name, local_path, progress=progress, mode=mode)
    db.stage_end(job["id"])
    if not r.get("ok"):
        raise RipFailed("The file reached your library but didn't verify: %s"
                        % r.get("error"))
    db.update_job(job["id"], verified_mode=r.get("mode") or mode)


# ── stage 5: tidy up ──

def _finish(job, s, transport, name, local_path):
    now = int(time.time())
    db.stage_end(job["id"], at=now)
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
    LED.announce("done")
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
        db.stage_end(job_id)
        db.update_job(job_id, state="cancelled", phase=None,
                      finished_at=int(time.time()), error="Cancelled")
        _cleanup_staging({"id": job_id})
    except RipFailed as e:
        log.error("Job %d failed: %s", job_id, e)
        db.stage_end(job_id)
        row = db.get_job(job_id) or {}
        db.update_job(job_id, state="failed", phase=None,
                      finished_at=int(time.time()), error=str(e))
        LED.announce("failed")
        P.eject()
        notify.send("failed",
                    title=row.get("title") or row.get("disc_label") or "A disc",
                    body=str(e))
    except Exception as e:
        log.exception("Job %d hit an unexpected error", job_id)
        db.stage_end(job_id)
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


def reverify(job_id, mode="quick"):
    """Re-check a job's file against the share. No disc, no re-rip.

    Runs on its own thread and writes its progress to the job like any other stage, so
    the History row shows it happening and the timings gain another `verify` run. The
    job's recorded state is left alone until it finishes: a `done` job being re-checked
    is still done, and a `failed` one becomes done only if the check now passes.
    """
    job = db.get_job(job_id)
    if not job:
        return False, "No such job."
    local = job.get("local_path")
    if not local or not os.path.exists(local):
        return False, ("The staged copy is gone, so there is nothing to compare "
                       "against. Re-rip the disc to check it.")
    share = db.default_share()
    if not share:
        return False, "There's no library share configured."
    name = _remote_name(job)
    if not name:
        return False, "Riparr doesn't know where this one landed."
    if db.active_job():
        return False, "Riparr is busy with a disc. Try again when it's finished."

    def run():
        transport = SH.Transport(share)
        db.stage_start(job_id, "verify")
        db.update_job(job_id, state="verifying", bytes_verified=0, error=None,
                      phase=("Checking the size on your library" if mode == "quick"
                             else "Reading it back to check every byte"))

        def progress(done, total):
            db.update_job(job_id, bytes_verified=done,
                          stage_pct=round(done / total, 4) if total else None)

        try:
            r = SH.verify_remote(transport, name, local, progress=progress, mode=mode)
        except Exception as e:
            r = {"ok": False, "error": str(e)}
        db.stage_end(job_id)
        # `finished_at` is when the *rip* finished, and it is what History dates the row
        # by. Re-checking a rip from last week does not make it a rip from just now, so
        # this only fills it in when it was never set.
        done_at = job.get("finished_at") or int(time.time())
        if r.get("ok"):
            db.update_job(job_id, state="done", phase=None, error=None,
                          verified_mode=r.get("mode") or mode,
                          finished_at=done_at, stage_pct=None)
            log.info("Job %d re-verified (%s).", job_id, mode)
        else:
            db.update_job(job_id, state="failed", phase=None, stage_pct=None,
                          finished_at=done_at,
                          error="Verification failed again: %s" % r.get("error"))
            log.warning("Job %d failed re-verification: %s", job_id, r.get("error"))

    threading.Thread(target=run, name="riparr-reverify", daemon=True).start()
    return True, ("Checking the size on your library."
                  if mode == "quick" else
                  "Reading the whole file back. This takes about as long as the "
                  "upload did.")


def _remote_name(job):
    """The share-relative path this job's file was written to.

    Recorded by `_transfer`, because it is the only place that knows it for certain.
    Jobs that predate the column fall back to the tail of `dest_path`, which is the
    same string with the share prefix on the front -- correct for every transport
    whose `describe()` is "//host/share/" + name.
    """
    name = job.get("remote_name")
    if name:
        return name
    dest = job.get("dest_path") or ""
    s = _settings()
    folder = (s.get("movie_folder") or "Movies").strip("/")
    at = dest.find("/%s/" % folder) if folder else -1
    return dest[at + 1:] if at >= 0 else None


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
