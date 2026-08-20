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

INSTALL_SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "tools", "makemkv-install.sh")

_state = {"phase": "idle", "message": "", "detail": "", "progress": 0.0}
_lock = threading.Lock()


def info():
    st = P.makemkv_status()
    return {
        "status": st,
        "manifest": {k: MANIFEST[k] for k in ("version", "verified_against_official")},
        "eula_url": EULA_URL,
        "eula_points": EULA_POINTS,
        "homepage": HOMEPAGE,
        "key_topic": FORUM_KEY_TOPIC,
        "install": dict(_state),
        "installable": P.IS_APPLIANCE,
    }


def _set(**kw):
    with _lock:
        _state.update(kw)


def install_status():
    with _lock:
        return dict(_state)


def start_install(accepted_eula):
    """Begin an install. Refuses without explicit consent — this is the whole point."""
    if not accepted_eula:
        return {"ok": False,
                "error": "MakeMKV's licence agreement has to be accepted first."}
    with _lock:
        if _state["phase"] in ("downloading", "verifying", "building"):
            return {"ok": False, "error": "An install is already running."}
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

        tmp = tempfile.mkdtemp(prefix="riparr-makemkv-")
        try:
            paths = []
            for i, pkg in enumerate(MANIFEST["packages"]):
                _set(phase="downloading", progress=0.1 + 0.25 * i,
                     message="Downloading %s" % pkg["name"], detail="")
                dest = os.path.join(tmp, pkg["name"])
                _download(pkg["url"], dest)

                _set(phase="verifying", progress=0.2 + 0.25 * i,
                     message="Checking %s" % pkg["name"])
                actual = _sha256(dest)
                if actual != pkg["sha256"]:
                    _set(phase="error", progress=0,
                         message="The download didn't match its expected checksum. "
                                 "Nothing was installed.",
                         detail="%s\nexpected %s\ngot      %s"
                                % (pkg["name"], pkg["sha256"][:24], actual[:24]))
                    return
                paths.append(dest)

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
