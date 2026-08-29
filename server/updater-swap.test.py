"""The update swap, tested against the failure that ate a live box's virtualenv.

Run: python3 server/updater-swap.test.py

v0.1.18's updater moved /opt/riparr aside to put the new version in its place. The
service runs as `riparr` and /opt is root-owned, so that raised PermissionError every
time -- appliance self-update had never once worked. Worse, it carried the virtualenv
into a temp directory *before* that step, and the `finally` deleted the temp directory
on the way out: a failed update reported "Nothing was changed" while removing the
interpreter the service is defined to run. The box kept serving from open file handles
and would not have survived its next restart.

So the two things asserted here are the two that were wrong:

  1. the swap happens *inside* the install directory, needing no permission on its parent
  2. the virtualenv is still there afterwards -- on success, on failure, and on rollback
"""
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))

from riparr import updater as U
from riparr import platform as P

FAILURES = []


def check(name, ok, why=""):
    print(("  ok  " if ok else "  FAIL ") + name + (("  -- " + why) if why and not ok else ""))
    if not ok:
        FAILURES.append(name)


def make_release(dirpath, marker, name=None):
    """A tarball shaped like riparr-server.tar.gz: one top-level `riparr/`.

    `name` gives a second release in the same temporary directory somewhere of its own.
    Two updates in a row is a case worth testing -- it is what exposed the deleted-cwd
    bug -- and without this the two would build in the same path and collide.
    """
    stage = os.path.join(dirpath, "stage-%s" % (name or "one"))
    root = os.path.join(stage, "riparr")
    os.makedirs(os.path.join(root, "server", "riparr"))
    os.makedirs(os.path.join(root, "tools"))
    with open(os.path.join(root, "server", "riparr", "main.py"), "w") as f:
        f.write("# %s\n" % marker)
    with open(os.path.join(root, "server", "requirements.txt"), "w") as f:
        f.write("")
    with open(os.path.join(root, "tools", "install.sh"), "w") as f:
        f.write("#!/bin/sh\n")
    archive = os.path.join(stage, "riparr-server.tar.gz")
    with tarfile.open(archive, "w:gz") as t:
        t.add(root, arcname="riparr")
    shutil.rmtree(root)
    return archive


def make_install_dir(dirpath, marker, pip_rc=0):
    """An install that looks like a real one: owned tree, venv inside it."""
    inst = os.path.join(dirpath, "opt-riparr")
    os.makedirs(os.path.join(inst, "server", "riparr"))
    os.makedirs(os.path.join(inst, ".venv", "bin"))
    with open(os.path.join(inst, "server", "riparr", "main.py"), "w") as f:
        f.write("# %s\n" % marker)
    with open(os.path.join(inst, "server", "requirements.txt"), "w") as f:
        f.write("")
    pip = os.path.join(inst, ".venv", "bin", "pip")
    with open(pip, "w") as f:
        # `pwd` first, because real pip calls os.getcwd() and dies on a working directory
        # that has been deleted -- which is precisely how the deleted-cwd bug surfaced,
        # as "Could not install packages due to an OSError: [Errno 2]". A stub that
        # ignores its cwd cannot reproduce it, and a test that cannot fail is not a test.
        f.write("#!/bin/sh\npwd >/dev/null 2>&1 || exit 1\nexit %d\n" % pip_rc)
    os.chmod(pip, 0o755)
    with open(os.path.join(inst, ".venv", "bin", "python"), "w") as f:
        f.write("#!/bin/sh\n")
    return inst


# install() deliberately changes this process's working directory -- that is the fix for
# the deleted-cwd bug, and on the appliance INSTALL_DIR is permanent so it is the right
# place to be left. In a test it is a temporary directory that is about to be removed, so
# every run is followed by a step back to somewhere that outlives the fixture. Without
# this the *next* test dies on getcwd() and blames itself.
_REPO = os.path.dirname(os.path.abspath(__file__))

# Where install() left us, recorded before stepping back. The assertion that matters for
# the deleted-cwd regression: whatever it left us in has to exist, or every subprocess
# spawned afterwards -- pip, makemkvcon, anything -- dies on it.
LAST_CWD = {"ok": None}


def run_install(inst, archive, restart="restart"):
    """install(), with only the network and the restart stubbed out.

    `restart` is what _arrange_restart is made to report: "restart", "reboot", or None
    for a box where nothing could be arranged. It is a parameter because "the update
    worked but nothing will start it" is a real outcome with its own message, and it
    used to be reported as a plain success — see the restart test below.
    """
    saved = (P.IS_APPLIANCE, U.INSTALL_DIR, U.check, U._download,
             U._expected_hash, U._arrange_restart, P.request_provision)
    try:
        P.IS_APPLIANCE = True
        U.INSTALL_DIR = inst
        U.check = lambda repo=None: {
            "status": "update", "latest": "9.9.9",
            "asset": {"name": "riparr-server.tar.gz", "url": "stub", "size": 1},
        }
        U._download = lambda url, dest: shutil.copy2(archive, dest)
        # *args: this stub should not have to be edited every time the real
        # signature moves — it exists to say "the checksum matches", nothing more.
        U._expected_hash = lambda *a, **k: U._sha256(archive)
        U._arrange_restart = lambda: restart
        P.request_provision = lambda: True
        return U.install()
    finally:
        try:
            os.getcwd()
            LAST_CWD["ok"] = True
        except OSError:
            LAST_CWD["ok"] = False
        os.chdir(_REPO)
        (P.IS_APPLIANCE, U.INSTALL_DIR, U.check, U._download,
         U._expected_hash, U._arrange_restart, P.request_provision) = saved


def marker_of(inst):
    with open(os.path.join(inst, "server", "riparr", "main.py")) as f:
        return f.read().strip().lstrip("# ")


print("a successful update")
with tempfile.TemporaryDirectory() as d:
    archive = make_release(d, "NEW")
    inst = make_install_dir(d, "OLD")
    r = run_install(inst, archive)
    check("reports success", r.get("ok"), str(r))
    check("the new version is in place", marker_of(inst) == "NEW", marker_of(inst))
    check("the virtualenv is still there", os.path.isfile(os.path.join(inst, ".venv", "bin", "python")))
    check("the install directory itself was never renamed", os.path.isdir(inst))
    check("the previous version is kept inside it",
          os.path.isdir(os.path.join(inst, ".previous")))

print("a failed update -- dependencies will not install")
with tempfile.TemporaryDirectory() as d:
    archive = make_release(d, "NEW")
    inst = make_install_dir(d, "OLD", pip_rc=1)
    r = run_install(inst, archive)
    check("reports failure", not r.get("ok"), str(r))
    check("the old version is back", marker_of(inst) == "OLD", marker_of(inst))
    check("THE VIRTUALENV SURVIVED", os.path.isfile(os.path.join(inst, ".venv", "bin", "python")))

print("an install directory the service cannot write")
with tempfile.TemporaryDirectory() as d:
    archive = make_release(d, "NEW")
    inst = make_install_dir(d, "OLD")
    os.chmod(inst, 0o555)
    try:
        r = run_install(inst, archive)
        check("refuses before touching anything", not r.get("ok"), str(r))
        check("says so in the message", "not writable" in (r.get("message") or ""),
              r.get("message", ""))
        check("the virtualenv is untouched",
              os.path.isfile(os.path.join(inst, ".venv", "bin", "python")))
    finally:
        os.chmod(inst, 0o755)

# ── the update must not report a restart it did not arrange ──
#
# Three in-place updates in a row swapped the code correctly and restarted nothing,
# each reporting "Riparr is restarting". _restart_detached tried systemd-run and then a
# backgrounded shell and treated exit 0 as proof -- but `&` makes the shell exit 0
# before the command runs, so the check could not fail, and the refusal (Access denied,
# because the service is unprivileged) went to /dev/null.
#
# The interesting case is the third one. A swap that worked with no way to start the new
# code is not a failure -- the update is on disk and one restart away -- but reporting it
# as a plain success is exactly the bug.
print("the update reports what it actually arranged")
for arranged, want_flag, want_word in (("restart", False, "restarting"),
                                       ("reboot", False, "restarting"),
                                       (None, True, "could not restart")):
    with tempfile.TemporaryDirectory() as d:
        archive = make_release(d, "NEW")
        inst = make_install_dir(d, "OLD")
        r = run_install(inst, archive, restart=arranged)
        label = arranged or "nothing"
        check("%s: still reports success" % label, r.get("ok"), str(r))
        check("%s: the new version is in place" % label, marker_of(inst) == "NEW")
        check("%s: needs_restart is %s" % (label, want_flag),
              bool(r.get("needs_restart")) is want_flag, str(r.get("needs_restart")))
        check("%s: says so in the message" % label,
              want_word in (r.get("message") or "").lower(), r.get("message", ""))

# ── the swap must not leave the updater standing in a deleted directory ──
#
# riparr.service sets WorkingDirectory=/opt/riparr/server, and the swap MOVES that
# directory into .previous. A process follows the directory it is in, so the service
# ended up inside the backup -- and the next update's rmtree of .previous deleted the
# ground under it. Every subprocess then inherited a dead cwd and died on getcwd(), which
# surfaced as "Could not install packages due to an OSError: [Errno 2]" and rolled the
# update back, with nothing wrong with pip or the venv at all.
#
# Both updates are run from inside the doomed directory, because one update is not enough
# to reproduce it: the first only moves the cwd, the second deletes it.
print("two updates in a row from inside the directory that gets moved")
with tempfile.TemporaryDirectory() as d:
    archive = make_release(d, "NEW")
    inst = make_install_dir(d, "OLD")
    served_from = os.path.join(inst, "server")   # WorkingDirectory=/opt/riparr/server

    # A directory *handle*, not a path. The running service does not re-enter its
    # working directory between updates -- it stays in the one it started in, and that
    # directory gets moved out from under it. Holding an fd and fchdir()ing back is how
    # a test reproduces "the same process, still standing where it was" after the
    # harness has stepped elsewhere. Re-chdir'ing by path instead lands in the *new*
    # server/ each time, which is what made the first version of this test pass against
    # the bug it was written to catch.
    fd = os.open(served_from, os.O_RDONLY)
    try:
        os.fchdir(fd)
        r1 = run_install(inst, archive)
        check("first update succeeds", r1.get("ok"), str(r1))
        check("it left us somewhere that exists", LAST_CWD["ok"] is True)

        # One update is not enough. The first only moves the cwd into .previous; the
        # second deletes .previous with the process still standing in it.
        os.fchdir(fd)
        archive2 = make_release(d, "NEWER", name="two")
        r2 = run_install(inst, archive2)
        check("second update succeeds too", r2.get("ok"), str(r2))
        check("and installed the newer one", marker_of(inst) == "NEWER", marker_of(inst))
        check("still standing somewhere that exists", LAST_CWD["ok"] is True)
    finally:
        os.close(fd)
        os.chdir(_REPO)

# ── the checksum must come from *this* release ──
#
# The tag and a component version stopped being the same string when the two halves began
# versioning apart. Both updaters built the checksums URL out of a version, so on a
# release tagged v0.2.3 the Preparer asked for v0.1.21's checksums (404 -> "publishes no
# checksum") and the appliance asked for v0.2.2's (a real file, the wrong numbers ->
# "the download did not match its published checksum"). The second is worse: a false
# corruption report against a perfectly good download.
print("the checksums come from the release being installed")

SUMS = "deadbeef" * 8 + "  riparr-server.tar.gz\n" + ("cafe" * 16) + "  x.dmg\n"
RIGHT = "https://example.invalid/v0.2.3/SHA256SUMS.txt"


class _Resp:
    def __init__(self, body): self.body = body.encode()
    def read(self): return self.body
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _only(expected_url):
    """A urlopen that serves the sums file at one URL and 404s everywhere else."""
    def fake(req, timeout=None, **kw):
        url = getattr(req, "full_url", req)
        if url != expected_url:
            raise OSError("404 at %s" % url)
        return _Resp(SUMS)
    return fake


assets_json = [{"name": "riparr-server.tar.gz", "browser_download_url": "u"},
               {"name": "SHA256SUMS.txt", "browser_download_url": RIGHT}]
check("the appliance finds the sums asset by name", U._sums_url(assets_json) == RIGHT,
      str(U._sums_url(assets_json)))

import urllib.request as _ur
_saved = _ur.urlopen
try:
    _ur.urlopen = _only(RIGHT)
    got = U._expected_hash("riparr-server.tar.gz", U._sums_url(assets_json), "r/r", "0.2.3")
    check("the appliance reads it from that URL", got == "deadbeef" * 8, str(got))
    # A version that is not the tag must never be turned into a download path.
    stray = U._expected_hash("riparr-server.tar.gz", None, "r/r", "0.2.2")
    check("no component version is used as a release path", stray is None, str(stray))
finally:
    _ur.urlopen = _saved

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "tools", "preparer"))
try:
    import core as PC
except Exception as e:                                   # pragma: no cover
    PC = None
    print("  (skipped preparer half: %s)" % e)

if PC is not None:
    pa = [{"name": "riparr-preparer-macos.dmg", "url": "u"},
          {"name": "SHA256SUMS.txt", "url": RIGHT}]
    saved = PC.urlopen
    try:
        PC.urlopen = _only(RIGHT)
        got = PC.published_sha256(pa, "riparr-server.tar.gz", "0.2.3", "r/r")
        check("the preparer reads it from the asset URL", got == "deadbeef" * 8, str(got))
        stray = PC.published_sha256([], "riparr-server.tar.gz", "0.1.21", "r/r")
        check("and never from its own version", stray is None, str(stray))
    finally:
        PC.urlopen = saved

print()
if FAILURES:
    print("FAILED: " + ", ".join(FAILURES))
    sys.exit(1)
print("all good")
