"""
Full-disc backup: the disc's own folder, menus and all, decrypted.

An MKV is the film. A backup is the *disc* -- `VIDEO_TS` for a DVD, `BDMV` for a
Blu-ray -- with its menus, extras, angles and every playlist, decrypted so it plays from
the folder and can be turned into an ISO later without the drive. It is what people ask
for when they want to keep the thing they bought rather than the part of it they watch.

## Two tools, because MakeMKV only does half of it

`makemkvcon backup --decrypt` handles Blu-ray and 4K UHD. **It does not do DVDs, and
never has**: the Backup button is not offered for one, in the GUI or on the command
line. GuinpinSoft's position is that a DVD is already backed up by copying it, which is
true of an encrypted ISO and not of a folder that plays.

So DVDs go through `dvdbackup -M`, which mirrors the whole disc through libdvdread --
and libdvdread decrypts CSS only if libdvdcss is installed. Neither ships on the box by
default, and libdvdcss is not in Debian at all. `packaging/dvdtools-install.sh` puts
them there, through the same one-way root door MakeMKV's installer uses; this module
reports whether that has happened and asks for it if not.

## What is promised and what is not

This module drives the tools and measures what they wrote. It does not decide where the
folder goes or whether it is safe to put it there -- that is the pipeline's job
(`rip.py`), because it is the same set of rules a film file obeys: never write into
somebody's library half-finished, never overwrite another disc's copy, prove it arrived.
"""
import ctypes.util
import os
import re
import shutil
import subprocess
import threading
import time

from . import platform as P

RUNDIR = "/run/riparr"
REQUEST = os.path.join(RUNDIR, "dvdtools.request")
STATE = os.path.join(RUNDIR, "dvdtools.state")
BRIDGE_UNIT = "/etc/systemd/system/riparr-dvdtools.path"

# What a finished backup must contain, per family. Checked after the tool exits, because
# an exit status is the tool's opinion and a folder that plays is the fact. dvdbackup in
# particular has been seen to exit 0 having written nothing on a disc it could not open.
PROOF = {
    "dvd": os.path.join("VIDEO_TS", "VIDEO_TS.IFO"),
    "bluray": os.path.join("BDMV", "index.bdmv"),
    "uhd": os.path.join("BDMV", "index.bdmv"),
}


class BackupFailed(Exception):
    pass


class BackupCancelled(Exception):
    pass


# ── are the tools here ──

_lib_cache = {"at": 0.0, "found": None}


def _libdvdcss():
    """Whether the loader will find libdvdcss -- which is the question, not whether a
    file exists somewhere. libdvdread dlopen()s it by soname, so a library sitting in a
    directory ldconfig has not indexed is a DVD that comes out scrambled. `find_library`
    asks ldconfig, which is the same answer the loader gets. Cached briefly: it shells
    out, and the settings page asks on every render."""
    now = time.time()
    if now - _lib_cache["at"] > 60 or _lib_cache["found"] is None:
        _lib_cache["found"] = bool(ctypes.util.find_library("dvdcss"))
        _lib_cache["at"] = now
    return _lib_cache["found"]


def dvd_tools():
    """{"dvdbackup": bool, "libdvdcss": bool, "ready": bool} for the DVD half."""
    if P.MOCK:
        ready = os.environ.get("RIPARR_MOCK_DVDTOOLS", "1") == "1"
        return {"dvdbackup": ready, "libdvdcss": ready, "ready": ready}
    has_tool = bool(shutil.which("dvdbackup"))
    has_lib = _libdvdcss()
    return {"dvdbackup": has_tool, "libdvdcss": has_lib, "ready": has_tool and has_lib}


def can_backup(family):
    """(True, "") if a disc of this family can be backed up right now, else (False, why).

    `why` is written to sit after "This disc was ripped as a film file instead," -- the
    pipeline falls back rather than failing, because on an auto-ripping box a refused
    disc is a disc somebody has to come back and feed in again.
    """
    if family == "dvd":
        t = dvd_tools()
        if t["ready"]:
            return True, ""
        if installing():
            return False, "the DVD backup tools are still installing"
        return False, "the DVD backup tools aren't installed yet (Settings → Ripping)"
    if family in ("bluray", "uhd"):
        if P.MOCK or shutil.which("makemkvcon") or os.path.exists("/usr/local/bin/makemkvcon"):
            return True, ""
        return False, "MakeMKV isn't installed"
    return False, "Riparr couldn't tell what kind of disc this is"


# ── installing them ──

def _read_state():
    try:
        import json
        with open(STATE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def installing():
    st = _read_state() or {}
    return st.get("phase") in ("starting", "installing", "building")


def bridge_available():
    return os.path.exists(BRIDGE_UNIT) and os.path.isdir(RUNDIR) and os.access(
        RUNDIR, os.W_OK)


def status():
    """Everything the settings page shows about DVD backups."""
    t = dvd_tools()
    st = _read_state() or {}
    return {"ready": t["ready"], "dvdbackup": t["dvdbackup"],
            "libdvdcss": t["libdvdcss"], "installing": installing(),
            "phase": st.get("phase"), "message": st.get("message"),
            "detail": st.get("detail"),
            "can_install": P.MOCK or bridge_available()}


def request_install():
    """Ask the root side to install dvdbackup and libdvdcss. One empty file, as with
    every other privileged action: the request carries nothing the root side reads."""
    if P.MOCK:
        return {"ok": True, "message": "Off-hardware: nothing to install."}
    if dvd_tools()["ready"]:
        return {"ok": True, "message": "Already installed."}
    if installing():
        return {"ok": True, "message": "Already installing."}
    if not bridge_available():
        return {"ok": False,
                "error": "This box is missing the part that installs them. "
                         "System → Tasks can install the system parts."}
    try:
        with open(REQUEST, "w") as f:
            f.write("%d\n" % int(time.time()))
    except OSError as e:
        return {"ok": False, "error": "Could not ask the system to install them: %s" % e}
    _lib_cache["found"] = None
    return {"ok": True, "message": "Installing — a few minutes."}


def start():
    """Ask for the DVD tools once per boot, if they are missing.

    Two callers ask, because neither covers every box alone. apply-system.sh asks after
    an update -- but on a fresh install it runs before the service has ever started, so
    /run/riparr does not exist yet and there is nowhere to put the request. This asks
    when the service starts, a minute and a half in so a first boot is not doing
    everything at once. The state file lives in /run, so an attempt that failed is shown
    on the settings page for the rest of this boot rather than retried in a loop.
    """
    if P.MOCK or not P.IS_APPLIANCE:
        return

    def later():
        time.sleep(90)
        try:
            if dvd_tools()["ready"] or _read_state() is not None:
                return
            if bridge_available():
                request_install()
        except Exception:
            pass

    threading.Thread(target=later, name="riparr-dvdtools", daemon=True).start()


# ── what a folder holds ──

def tree_files(root):
    """[(relative path with forward slashes, size)] for every file under `root`,
    sorted, so two listings of the same tree compare equal."""
    out = []
    for d, _dirs, files in os.walk(root):
        for f in files:
            p = os.path.join(d, f)
            try:
                size = os.path.getsize(p)
            except OSError:
                continue
            out.append((os.path.relpath(p, root).replace(os.sep, "/"), size))
    return sorted(out)


def tree_size(root):
    total = 0
    for d, _dirs, files in os.walk(root):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(d, f))
            except OSError:
                pass
    return total


# ── running the tools ──

# How often the folder is measured for the progress bar. Walking a BDMV tree is a few
# hundred stat() calls, which is nothing locally and is not nothing over SMB in direct
# mode -- so every few seconds, not continuously.
POLL = 3.0


def command(family, device, dest, name="disc"):
    """The command line for this family. Separate so the tests can read it.

    dvdbackup writes `<-o>/<-n>/VIDEO_TS`; makemkvcon writes BDMV and CERTIFICATE
    straight into the folder it is given. Both end up at `<dest>/<name>/...`.

    `-r b` is dvdbackup's skip-on-read-error. Abort sounds more honest and is the wrong
    default: several studios ship DVDs with deliberately unreadable sectors in VOBs no
    player ever opens (ARccOS and its relatives), and an abort on those fails a disc that
    plays perfectly. Skipped blocks are counted from the log and reported as a warning.
    """
    if family == "dvd":
        return ["dvdbackup", "-M", "-r", "b", "-i", device or "/dev/sr0",
                "-o", dest, "-n", name]
    binary = shutil.which("makemkvcon") or "/usr/local/bin/makemkvcon"
    return [binary, "-r", "--progress=-same", "--decrypt", "backup",
            _disc_arg(device), os.path.join(dest, name)]


def _disc_arg(device):
    if not device:
        return "disc:0"
    m = re.search(r"sr(\d+)$", device)
    return "disc:%s" % (m.group(1) if m else "0")


_PRGC = re.compile(r'^PRGC:\d+,\d+,"(.*)"')
_MSG = re.compile(r'^MSG:\d+,\d+,\d+,"(.*?)"')


def run(family, device, dest, cancel_ev, total_bytes=0, on_progress=None,
        on_first_byte=None, log=None):
    """Back the disc up into `<dest>/disc`. Returns (folder, warning or None).

    Progress is the size of the folder against the size of the disc, for the reason
    `rip._run_makemkv` gives at length: neither tool's own counter is a number a person
    can read as "how far along". The disc size is a slight overestimate for a DVD (the
    filesystem itself is not copied) so the bar is held short of full until the tool
    exits, rather than sitting at 100% while it works.
    """
    folder = os.path.join(dest, "disc")
    if os.path.isdir(folder):
        shutil.rmtree(folder, ignore_errors=True)      # a previous attempt's leftovers
    if P.MOCK:
        return _mock(family, folder, cancel_ev, total_bytes, on_progress, on_first_byte)

    os.makedirs(dest, exist_ok=True)
    logpath = os.path.join(dest, "backup.log")
    cmd = command(family, device, dest)
    if log:
        log.info("Backup: %s", " ".join(cmd))
    with open(logpath, "w") as out:
        proc = subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL)
    started, first_at = time.time(), None
    try:
        while proc.poll() is None:
            if cancel_ev.is_set():
                proc.terminate()
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.kill()
                raise BackupCancelled()
            time.sleep(POLL)
            done = tree_size(folder) if os.path.isdir(folder) else 0
            if done and first_at is None:
                first_at = time.time()
                if on_first_byte:
                    on_first_byte()
            if on_progress:
                shown = min(done, int(total_bytes * 0.99)) if total_bytes else done
                eta = None
                frac = (shown / total_bytes) if total_bytes else 0
                if first_at and frac > 0.01 and time.time() - first_at > 5:
                    w = time.time() - first_at
                    eta = int(w / frac - w)
                on_progress(shown, eta, _last_operation(logpath, family))
    finally:
        if proc.poll() is None:
            proc.kill()
    rc = proc.returncode
    text = _read(logpath)

    proof = PROOF.get(family, PROOF["bluray"])
    if not os.path.exists(os.path.join(folder, proof)):
        raise BackupFailed(_failure(family, rc, text))
    if rc != 0 and family != "dvd":
        # MakeMKV's exit status is reliable; dvdbackup's is not (it returns non-zero
        # after skipping blocks it was told to skip). Only the proof file counts for it.
        raise BackupFailed(_failure(family, rc, text))
    warning = None
    if family == "dvd":
        skipped = len(re.findall(r"(?im)^.*(?:skipp|read error|unable to read).*$", text))
        if skipped:
            warning = ("The DVD had %d unreadable patch%s, which were skipped. That is "
                       "normal for some studio copy protection; if the disc is scratched, "
                       "check the menus and film play through."
                       % (skipped, "" if skipped == 1 else "es"))
    if log:
        log.info("Backup finished in %ds: %s", int(time.time() - started), folder)
    return folder, warning


def _read(path):
    try:
        with open(path, errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def _last_operation(logpath, family):
    """A phase line for the progress display. MakeMKV says what it is doing (PRGC);
    dvdbackup does not say anything useful mid-copy, so it gets a fixed one."""
    if family == "dvd":
        return "Copying the whole disc, menus and all"
    tail = _read(logpath)[-4000:]
    last = None
    for line in tail.splitlines():
        m = _PRGC.match(line.strip())
        if m:
            last = m.group(1)
    return last or "Backing up the whole disc"


def _failure(family, rc, text):
    if family == "dvd":
        low = text.lower()
        if "libdvdcss" in low or ("css" in low and "key" in low):
            return ("The DVD couldn't be decrypted. The decryption library may be missing "
                    "— reinstall the DVD backup tools in Settings → Ripping.")
        return ("The DVD couldn't be copied. If it's dirty or scratched, clean it and try "
                "again. (dvdbackup exited %s.)" % rc)
    last = None
    for line in text.splitlines():
        m = _MSG.match(line.strip())
        if m:
            last = m.group(1)
    return last or ("MakeMKV couldn't back this disc up. If it's dirty or scratched, "
                    "clean it and try again.")


# ── off-hardware ──

def _mock(family, folder, cancel_ev, total_bytes, on_progress, on_first_byte):
    """A small disc-shaped folder, genuinely written, so every stage downstream of here
    -- the move, the upload, the verification, the purge -- runs for real off-hardware.
    The layout is the real one, scaled down: what matters downstream is that it is a
    tree of several files in nested folders, not one file."""
    if family == "dvd":
        files = [("VIDEO_TS/VIDEO_TS.IFO", 12), ("VIDEO_TS/VIDEO_TS.BUP", 12),
                 ("VIDEO_TS/VIDEO_TS.VOB", 256), ("VIDEO_TS/VTS_01_0.IFO", 40),
                 ("VIDEO_TS/VTS_01_0.BUP", 40), ("VIDEO_TS/VTS_01_1.VOB", 6144),
                 ("VIDEO_TS/VTS_01_2.VOB", 3072)]
    else:
        files = [("BDMV/index.bdmv", 1), ("BDMV/MovieObject.bdmv", 1),
                 ("BDMV/PLAYLIST/00800.mpls", 1), ("BDMV/CLIPINF/00001.clpi", 1),
                 ("BDMV/STREAM/00001.m2ts", 8192), ("BDMV/STREAM/00002.m2ts", 1024),
                 ("BDMV/BACKUP/index.bdmv", 1), ("CERTIFICATE/id.bdmv", 1)]
    total = sum(k for _, k in files) * 1024
    fast = bool(os.environ.get("RIPARR_MOCK_FAST"))
    if not fast:
        time.sleep(1.0)
    if on_first_byte:
        on_first_byte()
    written, started = 0, time.time()
    for rel, kib in files:
        if cancel_ev.is_set():
            raise BackupCancelled()
        p = os.path.join(folder, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            # Not zeroes: a deep verify that compared two all-zero files would pass even
            # if the wrong file had been fetched back.
            f.write((rel.encode() * (kib * 1024 // len(rel) + 1))[:kib * 1024])
        written += kib * 1024
        if on_progress:
            frac = written / float(total)
            el = time.time() - started
            on_progress(written, int(el / frac - el) if frac > 0.1 else None,
                        "Backing up the whole disc")
        if not fast:
            time.sleep(0.3)
    return folder, None


# Exported for tests that want to watch a mock backup without the pipeline.
__all__ = ["run", "command", "can_backup", "dvd_tools", "status", "request_install",
           "tree_files", "tree_size", "BackupFailed", "BackupCancelled", "PROOF"]
