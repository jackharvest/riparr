"""
MakeMKV: consent, fetch, verify, install.

MakeMKV is made by GuinpinSoft, not by us. Its EULA is an agreement between the user and
GuinpinSoft — "by installing or using this Software, you agree to be bound by the terms"
— and that agreement cannot be given on somebody else's behalf. So nothing here downloads
a single byte before the user has explicitly accepted (D14).

Riparr invokes `makemkvcon` as a separate process over its CLI and never links against
libmakemkv, which is what keeps a GPL-3 codebase and a proprietary decoder at arm's
length.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.request

from . import platform as P

EULA_URL = "https://www.makemkv.com/eula/"
HOMEPAGE = "https://www.makemkv.com/"
FORUM_KEY_TOPIC = "https://forum.makemkv.com/forum/viewtopic.php?f=5&t=1053"

# Pinned release. Both hashes were taken from tarballs fetched via a third-party mirror
# while makemkv.com was down (see JOURNAL.md, 2026-08-19). They have NOT been confirmed
# against makemkv.com's own published checksums. Re-verify before any public release —
# a wrong pin here silently refuses every install, and a malicious one would be worse.
MANIFEST = {
    "version": "1.18.4",
    "verified_against_official": False,
    "packages": [
        {"name": "makemkv-oss-1.18.4.tar.gz",
         "url": "https://www.makemkv.com/download/makemkv-oss-1.18.4.tar.gz",
         "sha256": "8590063648d42ec2a958b74573d7022f0f4c334e4e4fe7dd53b70c6e748ba453"},
        {"name": "makemkv-bin-1.18.4.tar.gz",
         "url": "https://www.makemkv.com/download/makemkv-bin-1.18.4.tar.gz",
         "sha256": "cee56de0baa5531abed16bd862742d308d772b4ab4dae16ee865bf74f04a1608"},
    ],
}

# Shown before consent. Paraphrased from makemkv-oss-1.18.4/License.txt; the full text is
# always one click away, and the wizard links to it rather than relying on this summary.
EULA_POINTS = [
    "MakeMKV is made by GuinpinSoft inc. This agreement is between you and them.",
    "You may only use it to copy discs you own or are otherwise permitted to copy.",
    "You may not sell, rent, lease or sublicense it.",
    "You may not reverse engineer, decompile or modify it.",
    "The free beta key expires roughly every 60 days. A permanent key can be purchased.",
]

# Checked before anything is downloaded. makemkv.com has been unreachable, and the
# tarballs are frequently already on the box -- copied across by hand during validation,
# or left over from a previous install.
LOCAL_SOURCES = [
    "/root/makemkv",            # where the Preparer puts them on an Armbian card
    "/boot/makemkv",
    "/home/riparr/makemkv",
    "/opt/riparr/makemkv",
    "/boot/firmware/makemkv",
    os.path.expanduser("~/makemkv"),
]


def find_local_source():
    """A directory already holding every package, with checksums that match."""
    for d in LOCAL_SOURCES:
        if not os.path.isdir(d):
            continue
        have = []
        for pkg in MANIFEST["packages"]:
            f = os.path.join(d, pkg["name"])
            if os.path.exists(f) and os.path.getsize(f) > 0:
                have.append((f, pkg["sha256"]))
        if len(have) == len(MANIFEST["packages"]):
            return d
    return None


INSTALL_SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "tools", "makemkv-install.sh")

_state = {"phase": "idle", "message": "", "detail": "", "progress": 0.0}
_lock = threading.Lock()


def info():
    st = P.makemkv_status()
    local = find_local_source()
    return {
        "status": st,
        "local_source": local,
        "manifest": {k: MANIFEST[k] for k in ("version", "verified_against_official")},
        "eula_url": EULA_URL,
        "eula_points": EULA_POINTS,
        "homepage": HOMEPAGE,
        "key_topic": FORUM_KEY_TOPIC,
        "install": dict(_state),
        "installable": can_install()[0],
    }


def _set(**kw):
    with _lock:
        _state.update(kw)


def install_status():
    """Progress, preferring what the root installer reports over our own guess.

    Once the bridge takes over, this process is not doing the work and has nothing to
    say about it. The root side publishes to a file; read that. It also survives a
    restart of this service, which a thread-local dict would not.
    """
    bridged = _read_bridge_state()
    if bridged:
        return bridged
    with _lock:
        return dict(_state)


def _read_bridge_state():
    try:
        with open(BRIDGE_STATE) as f:
            st = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(st, dict) or "phase" not in st:
        return None
    st.setdefault("progress", 0.0)
    st.setdefault("message", "")
    st.setdefault("detail", "")
    # A build takes half an hour; showing the last few lines is the difference between
    # "it is working" and "it is hung".
    try:
        with open(BRIDGE_LOG) as f:
            tail = f.read()[-4000:]
        st["log"] = tail.strip().splitlines()[-12:]
    except OSError:
        st["log"] = []
    return st


# ── the privilege bridge ──
# Building MakeMKV installs apt packages and writes to /usr/local, so it needs root.
# This service runs as `riparr` with NoNewPrivileges=yes and cannot get there — sudo
# included. Sending the user to a terminal is the wrong answer for an appliance, so
# install.sh sets up a one-way door: creating REQUEST is watched by riparr-makemkv.path,
# which starts a root oneshot whose command line is fixed in the unit file. This side
# can ask. It cannot say what runs.
RUNDIR = "/run/riparr"
REQUEST = os.path.join(RUNDIR, "makemkv.request")
BRIDGE_STATE = os.path.join(RUNDIR, "makemkv.state")
BRIDGE_LOG = os.path.join(RUNDIR, "makemkv.log")
BRIDGE_UNIT = "/etc/systemd/system/riparr-makemkv.path"


def bridge_available():
    """The bridge is present and this process can actually ring the bell."""
    return os.path.exists(BRIDGE_UNIT) and os.path.isdir(RUNDIR) and os.access(
        RUNDIR, os.W_OK)


def can_install():
    """Whether this process could actually complete an install."""
    if not P.IS_APPLIANCE:
        return False, "MakeMKV installs on the appliance only."
    if os.geteuid() == 0:
        return True, ""
    if bridge_available():
        return True, ""
    return False, (
        "This copy of Riparr was installed before in-place MakeMKV setup existed, so "
        "it cannot install MakeMKV for you. Re-run the installer to add it:\n\n"
        "    sudo bash /opt/riparr/tools/install.sh\n\n"
        "Then come back to this page.")


def start_install(accepted_eula):
    """Begin an install. Refuses without explicit consent — this is the whole point.

    Consent is enforced here and recorded in the database. The root side takes
    `--accept-eula` as given, because the only way to reach it is through this
    function, and the only process that can reach this function is the web service
    behind authentication.
    """
    if not accepted_eula:
        return {"ok": False,
                "error": "MakeMKV's licence agreement has to be accepted first."}
    ok, why = can_install()
    if not ok:
        return {"ok": False, "error": why}
    with _lock:
        if _state["phase"] in ("downloading", "verifying", "building"):
            return {"ok": False, "error": "An install is already running."}

    if os.geteuid() != 0 and bridge_available():
        try:
            # Touching the file is the whole request. systemd does the rest.
            with open(REQUEST, "w") as f:
                f.write("%d\n" % int(time.time()))
        except OSError as e:
            return {"ok": False,
                    "error": "Could not ask the system to install MakeMKV: %s" % e}
        _set(phase="downloading", progress=0.05,
             message="Starting the installer", detail="")
        return {"ok": True}

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True}


def _run():
    try:
        if not P.IS_APPLIANCE:
            # Walk the same phases so the flow is exercisable off-hardware, but never
            # pretend a binary was installed.
            for phase, msg, pr in [
                ("downloading", "Downloading MakeMKV %s" % MANIFEST["version"], 0.35),
                ("verifying", "Checking the download against its checksum", 0.6),
                ("building", "Building for this device — this takes a few minutes", 0.9),
            ]:
                _set(phase=phase, message=msg, progress=pr, detail="")
                time.sleep(1.4)
            _set(phase="error", progress=0,
                 message="MakeMKV installs on the appliance only.",
                 detail="This process is running in development mode, so nothing was "
                        "downloaded or installed.")
            return

        local = find_local_source()
        tmp = tempfile.mkdtemp(prefix="riparr-makemkv-")
        try:
            for i, pkg in enumerate(MANIFEST["packages"]):
                dest = os.path.join(tmp, pkg["name"])
                if local:
                    _set(phase="downloading", progress=0.1 + 0.25 * i,
                         message="Using the copy already on this device",
                         detail=os.path.join(local, pkg["name"]))
                    shutil.copyfile(os.path.join(local, pkg["name"]), dest)
                else:
                    _set(phase="downloading", progress=0.1 + 0.25 * i,
                         message="Downloading %s" % pkg["name"], detail="")
                    try:
                        _download(pkg["url"], dest)
                    except Exception as e:
                        _set(phase="error", progress=0,
                             message="Could not download MakeMKV.",
                             detail="%s\n\nmakemkv.com has been intermittently "
                                    "unreachable. Copy the tarballs to one of %s and "
                                    "try again." % (e, " or ".join(LOCAL_SOURCES[:2])))
                        return

                _set(phase="verifying", progress=0.2 + 0.25 * i,
                     message="Checking %s" % pkg["name"])
                actual = _sha256(dest)
                if actual != pkg["sha256"]:
                    _set(phase="error", progress=0,
                         message="%s didn't match its expected checksum. Nothing was "
                                 "installed." % pkg["name"],
                         detail="expected %s\ngot      %s"
                                % (pkg["sha256"][:24], actual[:24]))
                    return

            _set(phase="building", progress=0.7,
                 message="Building MakeMKV for this device — this takes a few minutes",
                 detail="")
            # --jobs 1: the C++ build is the OOM risk on a 512MB board (R1). Slower
            # than -j2 and much likelier to finish.
            p = subprocess.run(
                ["bash", INSTALL_SCRIPT, "--accept-eula", "--srcdir", tmp, "--jobs", "1"],
                capture_output=True, text=True, timeout=60 * 60)
            if p.returncode != 0:
                _set(phase="error", progress=0,
                     message="MakeMKV did not finish installing.",
                     detail=(p.stderr or p.stdout or "")[-1200:])
                return

            _set(phase="done", progress=1.0,
                 message="MakeMKV %s is installed." % MANIFEST["version"], detail="")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    except Exception as e:
        _set(phase="error", progress=0,
             message="The install stopped unexpectedly.", detail=str(e))


# ── the current beta key ──
# MakeMKV is free while it is in beta, and GuinpinSoft publishes a registration key on
# their forum that they roll over roughly monthly. Everyone running a beta is expected
# to fetch it themselves, notice when it lapses, and paste in the new one — which is a
# chore this box is in a much better position to do than its owner.
#
# This is a forum page, not an API, so it will break one day. Every failure path here
# ends in "here is the link, paste it yourself" rather than an error, because that is
# exactly as good as the situation before this existed.
_key_cache = {"at": 0, "value": None}
KEY_TTL = 6 * 3600
KEY_RE = re.compile(r"\bT-[A-Za-z0-9@_\-]{40,80}\b")
EXPIRY_RE = re.compile(
    r"valid\s+until\s+(?:the\s+)?(?:end\s+of\s+)?([A-Z][a-z]+\s+\d{1,2},?\s+\d{4}"
    r"|[A-Z][a-z]+\s+\d{4}|\d{1,2}\s+[A-Z][a-z]+\s+\d{4})", re.I)


def beta_key(force=False):
    """The current beta key, with whatever validity the forum states.

    Cached, because the answer changes monthly at most and this is somebody else's
    forum — polling it on every page load would be rude and slow.
    """
    now = time.time()
    if not force and _key_cache["value"] and now - _key_cache["at"] < KEY_TTL:
        return dict(_key_cache["value"], cached=True)

    result = {"key": None, "expires": None, "source": FORUM_KEY_TOPIC,
              "fetched_at": int(now), "error": None, "cached": False}
    try:
        req = urllib.request.Request(
            FORUM_KEY_TOPIC,
            headers={"User-Agent": "Mozilla/5.0 (compatible; riparr)"})
        with urllib.request.urlopen(req, timeout=20) as r:
            html = r.read(400000).decode("utf-8", "replace")
    except Exception as e:
        result["error"] = ("Couldn't reach the MakeMKV forum (%s). Open the link and "
                           "paste the key in yourself." % e)
        return result

    # The first match is the announcement post, which is the one kept up to date.
    m = KEY_RE.search(_strip_tags(html))
    if not m:
        result["error"] = ("The forum page didn't contain a key in the expected "
                           "format. Open the link and copy it in yourself.")
        return result
    result["key"] = m.group(0)
    e = EXPIRY_RE.search(_strip_tags(html))
    if e:
        result["expires"] = e.group(1).strip().rstrip(".,")
    _key_cache["at"] = now
    _key_cache["value"] = result
    return result


def _strip_tags(html):
    html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    return re.sub(r"<[^>]+>", " ", html)


def _download(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": "riparr"})
    with urllib.request.urlopen(req, timeout=300) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
