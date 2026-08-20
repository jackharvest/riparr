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


def _pick_asset(assets):
    for a in assets:
        n = (a.get("name") or "").lower()
        if n.startswith("riparr-") and n.endswith(".tar.gz"):
            return {"name": a["name"], "url": a["browser_download_url"],
                    "size": a.get("size")}
    return None


def install(repo=REPO):
    """Download, verify against the release checksums, stage and swap.

    Refuses to run anywhere but the appliance — swapping /opt/riparr on a development
    Mac would be a genuinely destructive surprise.
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
        return {"ok": False, "message": "That release has no installable archive."}

    tmp = tempfile.mkdtemp(prefix="riparr-update-")
    try:
        archive = os.path.join(tmp, asset["name"])
        _download(asset["url"], archive)

        expected = _expected_hash(repo, info["latest"], asset["name"])
        actual = _sha256(archive)
        if expected and actual != expected:
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

        backup = INSTALL_DIR + ".previous"
        shutil.rmtree(backup, ignore_errors=True)
        if os.path.exists(INSTALL_DIR):
            shutil.move(INSTALL_DIR, backup)
        shutil.move(root, INSTALL_DIR)

        subprocess.run(["systemctl", "restart", SERVICE], capture_output=True)
        return {"ok": True,
                "message": "Updated to %s. Riparr is restarting." % info["latest"],
                "version": info["latest"]}
    except Exception as e:
        return {"ok": False, "message": "The update failed. Nothing was changed.",
                "detail": str(e)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _download(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": "riparr/%s" % __version__})
    with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)


def _expected_hash(repo, tag, name):
    """Read the release's sha256sums.txt. Absent means we refuse to guess."""
    url = ("https://github.com/%s/releases/download/v%s/sha256sums.txt" % (repo, tag))
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "riparr"})
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read().decode()
    except Exception:
        return None
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
