"""
Updates, against the official repository.

An appliance with no screen must be able to update itself without the user finding a
terminal, and must never leave itself unbootable if the download is bad. So: check,
download, verify the hash, stage, swap, restart. The running version is never deleted
until the new one is in place.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.error
import urllib.request

from . import __version__
from . import platform as P

REPO = "jackharvest/riparr"
GITHUB_API = "https://api.github.com"
INSTALL_DIR = os.environ.get("RIPARR_INSTALL_DIR", "/opt/riparr")
SERVICE = "riparr.service"


def current_version():
    return __version__


def check(repo=REPO, timeout=8):
    """Never raises. A box with no internet still has to run."""
    url = "%s/repos/%s/releases/latest" % (GITHUB_API, repo)
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "riparr/%s" % __version__,
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"status": "no-releases", "current": __version__, "repo": repo,
                    "message": "No published releases yet."}
        return {"status": "error", "current": __version__, "repo": repo,
                "message": "GitHub returned HTTP %s" % e.code}
    except Exception as e:
        return {"status": "offline", "current": __version__, "repo": repo,
                "message": "Could not reach GitHub: %s" % e}

    tag = (data.get("tag_name") or "").lstrip("v")
    assets = data.get("assets") or []
    payload = _pick_asset(assets)
    return {
        "status": "update" if _newer(tag, __version__) else "current",
        "current": __version__,
        "latest": tag,
        "repo": repo,
        "notes": data.get("body") or "",
        "published": data.get("published_at"),
        "url": data.get("html_url"),
        "asset": payload,
        "can_install": bool(payload) and P.IS_APPLIANCE,
        "message": ("Version %s is available." % tag) if _newer(tag, __version__)
                   else "Riparr is up to date.",
    }


# The appliance payload, and nothing else. The same release also carries the Preparer
# for three desktop operating systems, and one of those is a .tar.gz whose name starts
# with "riparr-" -- a looser match picks up `riparr-preparer-linux-beta.tar.gz` and
# unpacks a desktop GUI app over /opt/riparr, which leaves a box that cannot start and
# has no screen to say so.
SERVER_ASSET = "riparr-server.tar.gz"


def _pick_asset(assets):
    for a in assets:
        if (a.get("name") or "").lower() == SERVER_ASSET:
            return {"name": a["name"], "url": a["browser_download_url"],
                    "size": a.get("size")}
    return None


def install(repo=REPO):
    """Download, verify, stage, swap, reinstall dependencies, restart.

    Refuses to run anywhere but the appliance -- swapping /opt/riparr on a development
    Mac would be a genuinely destructive surprise.

    Two things here are not obvious and both were learned the hard way:

    **The virtualenv lives inside the directory being replaced.** riparr.service starts
    `/opt/riparr/.venv/bin/python`, and a release tarball has no `.venv` in it. Moving
    the old directory aside and unpacking the new one therefore removes the interpreter
    the service is defined to run, and a headless box with no screen simply stops
    coming back. The venv is carried across, exactly as `tools/install.sh` does on a
    fresh install.

    **A refused update is a success and a broken one is not.** Every failure path leaves
    the running version untouched, and if the swap completes but the new version cannot
    install its dependencies, the previous directory is moved back before returning.
    """
    if not P.IS_APPLIANCE:
        return {"ok": False,
                "message": "Updates install on the appliance only. "
                           "This process is running in development mode."}
    info = check(repo)
    if info["status"] != "update":
        return {"ok": False, "message": info.get("message", "Nothing to install.")}
    asset = info.get("asset")
    if not asset:
        return {"ok": False,
                "message": "That release has no appliance archive, so there is nothing "
                           "to install. The download page has the desktop app only."}

    tmp = tempfile.mkdtemp(prefix="riparr-update-")
    backup = INSTALL_DIR + ".previous"
    swapped = False
    try:
        archive = os.path.join(tmp, asset["name"])
        _download(asset["url"], archive)

        # Fail closed. If the published checksums cannot be read, that is a reason to
        # stop, not a reason to proceed without checking -- this archive is about to
        # become the code the box runs.
        expected = _expected_hash(repo, info["latest"], asset["name"])
        if not expected:
            return {"ok": False,
                    "message": "That release publishes no checksum for the appliance "
                               "archive, so the download cannot be verified. Nothing "
                               "was changed."}
        actual = _sha256(archive)
        if actual != expected:
            return {"ok": False,
                    "message": "The download did not match its published checksum. "
                               "Nothing was changed.",
                    "detail": "expected %s, got %s" % (expected[:16], actual[:16])}

        staged = os.path.join(tmp, "staged")
        os.makedirs(staged)
        with tarfile.open(archive) as t:
            _safe_extract(t, staged)

        root = staged
        entries = os.listdir(staged)
        if len(entries) == 1 and os.path.isdir(os.path.join(staged, entries[0])):
            root = os.path.join(staged, entries[0])

        if not os.path.exists(os.path.join(root, "server", "riparr", "main.py")):
            return {"ok": False,
                    "message": "That archive does not look like Riparr, so it was not "
                               "installed. Nothing was changed."}

        # Carry the virtualenv across before anything moves. See the docstring.
        venv = os.path.join(INSTALL_DIR, ".venv")
        if os.path.isdir(venv):
            shutil.move(venv, os.path.join(root, ".venv"))

        shutil.rmtree(backup, ignore_errors=True)
        if os.path.exists(INSTALL_DIR):
            shutil.move(INSTALL_DIR, backup)
        shutil.move(root, INSTALL_DIR)
        swapped = True

        # A release may add a dependency, and the venv that just came across predates
        # it. Failing here is recoverable and rolling back is the right answer, because
        # the alternative is a service that starts and then dies on an import.
        pip = os.path.join(INSTALL_DIR, ".venv", "bin", "pip")
        reqs = os.path.join(INSTALL_DIR, "server", "requirements.txt")
        if os.path.exists(pip) and os.path.exists(reqs):
            p = subprocess.run([pip, "install", "--quiet", "-r", reqs],
                               capture_output=True, text=True, timeout=600)
            if p.returncode != 0:
                _rollback(backup)
                return {"ok": False,
                        "message": "The new version could not install what it needs, "
                                   "so the previous one was put back.",
                        "detail": (p.stderr or p.stdout or "")[-400:]}

        _restart_detached()
        return {"ok": True,
                "message": "Updated to %s. Riparr is restarting." % info["latest"],
                "version": info["latest"]}
    except Exception as e:
        if swapped:
            _rollback(backup)
            return {"ok": False,
                    "message": "The update failed and the previous version was put "
                               "back.", "detail": str(e)}
        return {"ok": False, "message": "The update failed. Nothing was changed.",
                "detail": str(e)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _rollback(backup):
    """Put the previous install back. Best effort, and better than leaving a hole."""
    try:
        if os.path.isdir(backup):
            shutil.rmtree(INSTALL_DIR, ignore_errors=True)
            shutil.move(backup, INSTALL_DIR)
    except Exception:
        pass


def _restart_detached():
    """Restart after this request has been answered, not during it.

    The updater runs inside riparr.service, so `systemctl restart` kills the process
    that is still writing the HTTP response -- the browser gets a dropped connection at
    the exact moment it most needs to be told the update worked. A short-lived transient
    unit does the restart a moment later, from outside the service being restarted.
    """
    delay = "sleep 2; systemctl restart %s" % SERVICE
    for cmd in (["systemd-run", "--collect", "--unit=riparr-update-restart",
                 "/bin/sh", "-c", delay],
                # No systemd-run on a stripped image: fall back to an orphaned shell,
                # which survives its parent being killed.
                ["/bin/sh", "-c", "(" + delay + ") >/dev/null 2>&1 &"]):
        try:
            if subprocess.run(cmd, capture_output=True, timeout=10).returncode == 0:
                return
        except Exception:
            continue


def _download(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": "riparr/%s" % __version__})
    with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)


# Both spellings, because the answer to "which case is it?" should not be a brick.
# The release publishes SHA256SUMS.txt; the original code asked for sha256sums.txt,
# got a 404, returned None -- and the caller read None as "nothing to compare against"
# and installed the download unverified. A checksum that silently stops being checked
# is worse than no checksum at all, so a missing file is now a refusal (see install).
_SUMS_NAMES = ("SHA256SUMS.txt", "sha256sums.txt")


def _expected_hash(repo, tag, name):
    """The published SHA-256 for `name`, or None if it cannot be established."""
    for sums in _SUMS_NAMES:
        url = "https://github.com/%s/releases/download/v%s/%s" % (repo, tag, sums)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "riparr"})
            with urllib.request.urlopen(req, timeout=30) as r:
                body = r.read().decode()
        except Exception:
            continue
        for line in body.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[-1].lstrip("*") == name:
                return parts[0]
    return None


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_extract(tar, dest):
    """Refuse path traversal. An update archive is remote input."""
    base = os.path.realpath(dest)
    for m in tar.getmembers():
        target = os.path.realpath(os.path.join(dest, m.name))
        if not target.startswith(base + os.sep) and target != base:
            raise ValueError("archive contains an unsafe path: %s" % m.name)
    tar.extractall(dest)


def _newer(a, b):
    def parts(v):
        return [int(x) if x.isdigit() else 0
                for x in re.split(r"[.\-+]", v or "") if x != ""]
    pa, pb = parts(a), parts(b)
    n = max(len(pa), len(pb))
    return (pa + [0] * (n - len(pa))) > (pb + [0] * (n - len(pb)))
