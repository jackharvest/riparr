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
    # The tag is the release's name, not this half's version. The web interface and the
    # Preparer move independently, so a release that only changes the Preparer must not
    # tell every box it is out of date -- and the other way round.
    latest = _component_version(assets, "appliance", tag)
    return {
        "status": "update" if _newer(latest, __version__) else "current",
        "current": __version__,
        "latest": latest,
        "tag": tag,
        "repo": repo,
        "notes": data.get("body") or "",
        "published": data.get("published_at"),
        "url": data.get("html_url"),
        "asset": payload,
        # The checksums file, located by asset URL rather than by building a path out of
        # a version. `latest` is this half's version and stopped being the tag when the
        # two halves began versioning apart, so a constructed path now points at the
        # wrong release -- or at no release at all.
        "sums_url": _sums_url(assets),
        "can_install": bool(payload) and P.IS_APPLIANCE,
        "message": ("Version %s is available." % latest) if _newer(latest, __version__)
                   else "Riparr is up to date.",
    }


# The appliance payload, and nothing else. The same release also carries the Preparer
# for three desktop operating systems, and one of those is a .tar.gz whose name starts
# with "riparr-" -- a looser match picks up `riparr-preparer-linux-beta.tar.gz` and
# unpacks a desktop GUI app over /opt/riparr, which leaves a box that cannot start and
# has no screen to say so.
SERVER_ASSET = "riparr-server.tar.gz"

# Both live *inside* the install directory, which is the only place this service has
# permission to create anything. See install().
VENV_DIR = ".venv"
PREV_DIR = ".previous"


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

    # Asked before anything is downloaded or moved. The service owns /opt/riparr but not
    # /opt, and finding that out half way through an update is how the venv was lost.
    if not os.access(INSTALL_DIR, os.W_OK):
        return {"ok": False,
                "message": "Riparr cannot update itself: %s is not writable by the "
                           "service. Nothing was changed." % INSTALL_DIR,
                "detail": "Re-run the installer over SSH to repair the permissions: "
                          "sudo bash %s/tools/install.sh" % INSTALL_DIR}

    # Stand somewhere that will still exist in a moment.
    #
    # riparr.service sets WorkingDirectory=/opt/riparr/server, and the swap below MOVES
    # that directory into .previous. A process's cwd follows the directory it is in, so
    # this process ends up standing inside .previous -- and the next update deletes
    # .previous, leaving it in a directory that no longer exists. Every subprocess then
    # inherits a dead cwd and dies on it: pip reported
    # "OSError: [Errno 2] No such file or directory" and the update rolled back, with
    # nothing wrong with the venv or the requirements at all.
    #
    # INSTALL_DIR is the right place to stand because only its *contents* are moved --
    # the directory itself is never touched. This also covers makemkvcon and every other
    # subprocess spawned before the restart lands, not just pip.
    try:
        os.chdir(INSTALL_DIR)
    except OSError:
        pass

    tmp = tempfile.mkdtemp(prefix="riparr-update-")
    backup = os.path.join(INSTALL_DIR, PREV_DIR)
    swapped = False
    try:
        archive = os.path.join(tmp, asset["name"])
        _download(asset["url"], archive)

        # Fail closed. If the published checksums cannot be read, that is a reason to
        # stop, not a reason to proceed without checking -- this archive is about to
        # become the code the box runs.
        expected = _expected_hash(asset["name"], info.get("sums_url"),
                                  repo, info.get("tag"))
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

        # The directory is replaced from the inside, and the virtualenv never moves.
        #
        # /opt is root-owned and this service runs as `riparr`, so renaming /opt/riparr
        # raises PermissionError -- which is how appliance self-update managed never to
        # work at all, on any release. What the service *can* do is add and remove
        # entries inside the directory it owns, so the swap moves contents rather than
        # the directory itself and needs no privilege it does not have.
        #
        # The venv used to be carried into the staging directory before that failing
        # step, and the `finally` below deletes the staging directory -- so a failed
        # update took the interpreter with it. The box kept serving, because the running
        # process holds its own files open, and would not have come back from the next
        # restart. It stays exactly where it is now, and a failed update cannot reach it.
        keep = {VENV_DIR, PREV_DIR}
        shutil.rmtree(backup, ignore_errors=True)
        os.makedirs(backup)
        for name in os.listdir(INSTALL_DIR):
            if name in keep:
                continue
            shutil.move(os.path.join(INSTALL_DIR, name), os.path.join(backup, name))
        for name in os.listdir(root):
            if name in keep:
                continue
            shutil.move(os.path.join(root, name), os.path.join(INSTALL_DIR, name))
        swapped = True

        # A release may add a dependency, and the venv that just came across predates
        # it. Failing here is recoverable and rolling back is the right answer, because
        # the alternative is a service that starts and then dies on an import.
        pip = os.path.join(INSTALL_DIR, ".venv", "bin", "pip")
        reqs = os.path.join(INSTALL_DIR, "server", "requirements.txt")
        if os.path.exists(pip) and os.path.exists(reqs):
            # cwd is explicit rather than inherited. The chdir above prevents this
            # process acquiring a dead cwd in the first place, but a box that already
            # has one -- from an update installed before that fix -- would otherwise
            # keep failing here forever with no way out through this page.
            p = subprocess.run([pip, "install", "--quiet", "-r", reqs],
                               capture_output=True, text=True, timeout=600,
                               cwd=INSTALL_DIR)
            if p.returncode != 0:
                _rollback(backup)
                return {"ok": False,
                        "message": "The new version could not install what it needs, "
                                   "so the previous one was put back.",
                        "detail": (p.stderr or p.stdout or "")[-400:]}

        # Units and root-side helper scripts live outside /opt/riparr, so the swap
        # above did not install them -- this process cannot write /etc/systemd/system
        # and should never be able to. Ask the root side through the same one-way door
        # the rest of the privileged actions use.
        #
        # This is the step whose absence made every previous release ship its systemd
        # changes to /opt/riparr/packaging and install none of them.
        provisioned = P.request_provision()

        # Arranged *after* the swap and reported honestly. The new code is on disk
        # either way -- what differs is whether anything is going to start running it,
        # and that is the difference between "updated" and "updated, nothing happened".
        how = _arrange_restart()
        note = ("" if provisioned else
                " This box predates the automatic system-update step, so open "
                "System \u2192 Tasks afterwards to finish installing it.")

        if how == "restart":
            msg = "Updated to %s. Riparr is restarting." % info["latest"]
        elif how == "reboot":
            msg = ("Updated to %s. The box is restarting to finish \u2014 about a "
                   "minute." % info["latest"])
        else:
            msg = ("Updated to %s, but Riparr could not restart itself, so it is "
                   "still running the previous version. Restart it from the account "
                   "menu to finish \u2014 nothing is lost, and the new version is "
                   "already on the box." % info["latest"])

        return {"ok": True, "message": msg + note, "version": info["latest"],
                "restarted": how, "needs_restart": how is None,
                "provisioned": provisioned}
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
    """Put the previous contents back. Best effort, and better than leaving a hole.

    Contents, not the directory: the same permission that stops the swap renaming
    /opt/riparr stops the rollback renaming it back. The virtualenv is untouched by
    either direction, because it never left.
    """
    try:
        if not os.path.isdir(backup):
            return
        keep = {VENV_DIR, PREV_DIR}
        for name in os.listdir(INSTALL_DIR):
            if name in keep:
                continue
            target = os.path.join(INSTALL_DIR, name)
            shutil.rmtree(target, ignore_errors=True)
            if os.path.exists(target):
                os.remove(target)
        for name in os.listdir(backup):
            shutil.move(os.path.join(backup, name), os.path.join(INSTALL_DIR, name))
    except Exception:
        pass


def _arrange_restart():
    """Get the new code running. Returns "restart", "reboot" or None.

    None is the important return value and the reason this was rewritten.

    The old version tried `systemd-run` and then a backgrounded shell, and treated a
    zero exit status as proof. Both are refused for this account -- verified on the
    reference box, where each answers "Access denied", because riparr.service runs as
    an unprivileged user with NoNewPrivileges=yes. The shell nevertheless exited 0,
    every time, because `&` backgrounds the command and the refusal went to /dev/null.

    So `install()` swapped the code correctly, reported "Riparr is restarting", and
    restarted nothing. The box kept serving the old version from a process holding its
    own deleted files open, the page still showed the old version, and the only clue
    was that nothing happened. Three updates in a row failed that way with three 200s
    in the log and no restart between them.

    The lesson is the one this codebase keeps relearning: a privileged action attempted
    directly from this process cannot work, and must go through a door. Restarting is
    now a door of its own. Rebooting is the fallback, because that door has existed
    since early versions and is present on every box that could be running this.
    """
    if P.request_service_restart():
        return "restart"
    ok, _ = P.power_action("reboot")
    if ok:
        return "reboot"
    # Running as root (a developer box, or an install that never dropped privileges) is
    # the only case where doing it here can work. Checked last and checked honestly:
    # no backgrounding, so the return code means something.
    try:
        r = subprocess.run(["systemd-run", "--collect", "--on-active=2",
                            "--unit=riparr-update-restart",
                            "systemctl", "restart", SERVICE],
                           capture_output=True, timeout=10)
        if r.returncode == 0:
            return "restart"
    except Exception:
        pass
    return None


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


def _sums_url(assets):
    """The release's own URL for its checksums file, if it publishes one."""
    for a in assets or []:
        if (a.get("name") or "") in _SUMS_NAMES and a.get("browser_download_url"):
            return a["browser_download_url"]
    return None


def _expected_hash(name, sums_url=None, repo=REPO, tag=""):
    """The published SHA-256 for `name`, or None if it cannot be established.

    Prefers the URL the release itself gives for its checksums file. The constructed
    path is kept only for releases whose asset list could not be read, and is built from
    the *tag* -- never from a component version, which is what broke this: on a release
    tagged v0.2.3 carrying appliance 0.2.2, asking for v0.2.2's checksums fetches a
    different release's numbers and compares them against this one's download.
    """
    if sums_url:
        urls = [sums_url]
    elif tag:
        urls = ["https://github.com/%s/releases/download/v%s/%s" % (repo, tag, s)
                for s in _SUMS_NAMES]
    else:
        urls = []
    for url in urls:
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


def _component_version(assets, key, fallback):
    """This half's version in a release, from the `versions.json` asset.

    The appliance and the Preparer are released together and versioned apart: one tag,
    two version numbers, and each updater reads only its own. Releases published before
    versions.json existed do not carry it, so the tag remains the fallback and those keep
    behaving exactly as they did.

    Never raises. A release we cannot read the manifest of is treated as one that does
    not have it, which is the conservative answer: compare against the tag.
    """
    for a in assets or []:
        if a.get("name") != "versions.json":
            continue
        url = a.get("browser_download_url")
        if not url:
            break
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                v = json.load(r).get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
        except Exception:
            pass
        break
    return fallback


def _newer(a, b):
    def parts(v):
        return [int(x) if x.isdigit() else 0
                for x in re.split(r"[.\-+]", v or "") if x != ""]
    pa, pb = parts(a), parts(b)
    n = max(len(pa), len(pb))
    return (pa + [0] * (n - len(pa))) > (pb + [0] * (n - len(pb)))
