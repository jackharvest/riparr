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


def make_release(dirpath, marker):
    """A tarball shaped like riparr-server.tar.gz: one top-level `riparr/`."""
    root = os.path.join(dirpath, "riparr")
    os.makedirs(os.path.join(root, "server", "riparr"))
    os.makedirs(os.path.join(root, "tools"))
    with open(os.path.join(root, "server", "riparr", "main.py"), "w") as f:
        f.write("# %s\n" % marker)
    with open(os.path.join(root, "server", "requirements.txt"), "w") as f:
        f.write("")
    with open(os.path.join(root, "tools", "install.sh"), "w") as f:
        f.write("#!/bin/sh\n")
    archive = os.path.join(dirpath, "riparr-server.tar.gz")
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
        f.write("#!/bin/sh\nexit %d\n" % pip_rc)
    os.chmod(pip, 0o755)
    with open(os.path.join(inst, ".venv", "bin", "python"), "w") as f:
        f.write("#!/bin/sh\n")
    return inst


def run_install(inst, archive):
    """install(), with only the network and the restart stubbed out."""
    saved = (P.IS_APPLIANCE, U.INSTALL_DIR, U.check, U._download,
             U._expected_hash, U._restart_detached)
    try:
        P.IS_APPLIANCE = True
        U.INSTALL_DIR = inst
        U.check = lambda repo=None: {
            "status": "update", "latest": "9.9.9",
            "asset": {"name": "riparr-server.tar.gz", "url": "stub", "size": 1},
        }
        U._download = lambda url, dest: shutil.copy2(archive, dest)
        U._expected_hash = lambda repo, tag, name: U._sha256(archive)
        U._restart_detached = lambda: None
        return U.install()
    finally:
        (P.IS_APPLIANCE, U.INSTALL_DIR, U.check, U._download,
         U._expected_hash, U._restart_detached) = saved


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

print()
if FAILURES:
    print("FAILED: " + ", ".join(FAILURES))
    sys.exit(1)
print("all good")
