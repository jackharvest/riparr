#!/usr/bin/env python3
"""Full-disc backup, end to end, through the real pipeline.

A backup is the first job whose output is a *folder* -- a few hundred files in nested
directories -- and the pipeline was built around one job producing one file. Seasons
taught that lesson once (see tv-pipeline.test.py); this runs every stage a backup goes
through against the mock drive and the mock share, so the places that still assume a
single file show up here rather than on somebody's NAS:

  * identify takes no title and scans nothing, but still names the disc
  * the rip writes VIDEO_TS or BDMV, and nothing else, into the library folder
  * staged backups are sent a file at a time; direct ones are one rename
  * a folder that already holds anything is never written into -- unless it is this
    same disc's earlier backup, which Re-rip replaces
  * verification checks every file, and fails when one is wrong
  * the purge only frees a staged backup the share can prove it has
  * a resumed send (after a power cut) sends a folder, not a directory entry
  * a DVD on a box without the DVD tools is ripped as a film, and says so
  * a disc ripped to an MKV is not a "duplicate" when it comes back for a backup

Nothing is stubbed except the hardware, as everywhere else in this repository.

Run: python3 server/backup-pipeline.test.py
"""
import os
import shutil
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

_tmp = tempfile.mkdtemp(prefix="riparr-backup-test-")
os.environ["RIPARR_DB"] = os.path.join(_tmp, "test.db")
os.environ["RIPARR_MOCK_CONTENT"] = "movie"
os.environ["RIPARR_APPLIANCE"] = "0"
os.environ["RIPARR_MOCK_FAST"] = "1"
os.environ["RIPARR_MOCK_DISC"] = "bluray"
# On a box the library mount *is* the share. Pointing the mock mount at the mock share's
# folder for "nas/Media" makes direct mode the same thing here: what MakeMKV writes
# through the mount is what the transport reads back.
_MOCK_SHARE = "/tmp/riparr-mock-share"
os.environ["RIPARR_LIBRARY_MOUNT"] = os.path.join(_MOCK_SHARE, "nas", "Media")

from riparr import backup as BK, db, platform as P, rip, shares as SH  # noqa: E402

# The mock discs are real sizes (24 GiB for the Blu-ray), and staging is checked against
# real free space -- so a staged test on a laptop with less than that free is refused,
# correctly. The pipeline is not what is under test there; shrink the discs.
for _d in P._MOCK_DISCS.values():
    _d["size_bytes"] = 64 * 2 ** 20

failures = []


def check(name, got, want):
    if got == want:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s: got %r, wanted %r" % (name, got, want))
        failures.append(name)


def contains(name, haystack, needle):
    ok = needle in (haystack or "")
    print("  %s %s" % ("ok  " if ok else "FAIL", name))
    if not ok:
        print("       %r does not contain %r" % (haystack, needle))
        failures.append(name)


def share_files(under="nas/Media/Movies"):
    out = []
    base = os.path.join(SH.MOCK_SHARE_ROOT, under)
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d != ".riparr-incoming"]
        for f in files:
            out.append(os.path.relpath(os.path.join(root, f), base))
    return sorted(out)


def fresh_share():
    shutil.rmtree(SH.MOCK_SHARE_ROOT, ignore_errors=True)
    os.makedirs(os.environ["RIPARR_LIBRARY_MOUNT"], exist_ok=True)
    shutil.rmtree(P.STAGING, ignore_errors=True)
    os.makedirs(P.STAGING, exist_ok=True)
    for row in db.list_shares():
        db.delete_share(row["id"])
    return db.add_share("Media", "nas", "Media", None, None, make_default=True)


def new_job(label, fp=None):
    os.environ["RIPARR_MOCK_LABEL"] = label
    job_id = db.create_job(state="queued", disc_label=label, kind="movie",
                           fingerprint=fp or ("fp-" + label))
    return db.get_job(job_id)


def settle():
    """Wait for any re-verification thread to finish. Polling the job's state races it:
    the state is still `done` until the thread starts and says otherwise."""
    for t in threading.enumerate():
        if t.name == "riparr-reverify":
            t.join(timeout=10)


def run(job):
    """The worker's own entry point, then the sender if the job was handed to it."""
    rip._run_job(job)
    j = db.get_job(job["id"])
    if j["state"] == "transferring":            # staged: the tray opened, now send
        rip._send_one(j)
    return db.get_job(job["id"])


db.init()
db.set("setup_complete", True)
db.set("rip_mode", "backup")
db.set("transfer_mode", "burst")
db.set("verify_mode", "quick")
db.set("on_unknown_disc", "label")

# ── the command lines ────────────────────────────────────────────────────────

print("each family gets the tool that can actually back it up")
dvd = BK.command("dvd", "/dev/sr0", "/x")
check("a DVD goes through dvdbackup's mirror mode", dvd[:2], ["dvdbackup", "-M"])
check("skipping unreadable blocks, not aborting on them", dvd[dvd.index("-r") + 1], "b")
check("into <dest>/disc", (dvd[dvd.index("-o") + 1], dvd[dvd.index("-n") + 1]),
      ("/x", "disc"))
bd = BK.command("bluray", "/dev/sr1", "/x")
check("a Blu-ray goes through makemkvcon backup, decrypted",
      bd[-4:], ["--decrypt", "backup", "disc:1", "/x/disc"])
check("in robot mode", "-r" in bd, True)

# ── staged Blu-ray ───────────────────────────────────────────────────────────

print("a Blu-ray backup, staged on the card and then sent")
fresh_share()
job = new_job("THE_MATRIX")
job = run(job)
check("finished", job["state"], "done")
check("recorded as a backup", job["output"], "backup")
check("no title was chosen", job["chosen_title"], None)
check("verified file by file", job["verified_mode"], "quick")
check("the whole disc, in the film's folder", share_files(), [
    "The Matrix/BDMV/BACKUP/index.bdmv", "The Matrix/BDMV/CLIPINF/00001.clpi",
    "The Matrix/BDMV/MovieObject.bdmv", "The Matrix/BDMV/PLAYLIST/00800.mpls",
    "The Matrix/BDMV/STREAM/00001.m2ts", "The Matrix/BDMV/STREAM/00002.m2ts",
    "The Matrix/BDMV/index.bdmv", "The Matrix/CERTIFICATE/id.bdmv"])
check("History points at the folder", job["remote_name"], "Movies/The Matrix")
check("the copy is kept on the card (D6)", os.path.isdir(job["local_path"]), True)
check("bytes add up to the folder", job["bytes_total"], BK.tree_size(job["local_path"]))

print("the same disc again is a duplicate; a different output is not")
known = db.get_disc("fp-THE_MATRIX")
check("backed up, asked for a backup: already have it", rip._already_have(known), True)
db.set("rip_mode", "main")
check("backed up, asked for a film file: rip it", rip._already_have(known), False)
db.set("rip_mode", "backup")

print("re-verification checks every file, and notices a bad one")
ok, msg = rip.reverify(job["id"], mode="quick")
check("accepted", ok, True)
settle()
check("a good backup passes again", db.get_job(job["id"])["state"], "done")
victim = os.path.join(SH.MOCK_SHARE_ROOT, "nas/Media/Movies/The Matrix/BDMV/STREAM/00002.m2ts")
with open(victim, "r+b") as f:
    f.truncate(100)
rip.reverify(job["id"], mode="quick")
settle()
bad = db.get_job(job["id"])
check("a truncated stream fails it", bad["state"], "failed")
contains("and names the file", bad["error"], "BDMV/STREAM/00002.m2ts")

print("the purge keeps a backup the share can't vouch for, and frees one it can")
freed, _ = rip.purge_staging(need_bytes=1)
check("not freed while the share copy is wrong", freed, 0)
check("still on the card", os.path.isdir(bad["local_path"]), True)
db.update_job(job["id"], state="done", error=None)
shutil.copy(os.path.join(bad["local_path"], "BDMV/STREAM/00002.m2ts"), victim)
freed, notes = rip.purge_staging(need_bytes=1)
check("freed once every file matches", freed > 0, True)
check("and the card copy is gone", db.get_job(job["id"])["local_path"], None)

# ── deep verification ────────────────────────────────────────────────────────

print("deep verification reads every file back")
db.set("verify_mode", "deep")
dj = run(new_job("ARRIVAL"))
check("finished", dj["state"], "done")
check("deep actually ran", dj["verified_mode"], "deep")
db.set("verify_mode", "quick")

# ── a folder that is already there ───────────────────────────────────────────

print("never into a folder that holds somebody else's copy")
fresh_share()
existing = os.path.join(SH.MOCK_SHARE_ROOT, "nas/Media/Movies/Arthur Christmas")
os.makedirs(existing)
with open(os.path.join(existing, "Arthur Christmas.mkv"), "wb") as f:
    f.write(b"somebody's film")
aj = run(new_job("ARTHUR_CHRISTMAS"))
check("finished", aj["state"], "done")
check("went beside it, tagged with the source", aj["remote_name"],
      "Movies/Arthur Christmas - Bluray")
check("the MKV that was there is untouched",
      open(os.path.join(existing, "Arthur Christmas.mkv"), "rb").read(), b"somebody's film")
check("and nothing was added to its folder", os.listdir(existing), ["Arthur Christmas.mkv"])
contains("the user is told", aj["warning"], "nothing was overwritten")

print("Re-rip of the same disc replaces its own backup, not a sibling")
before = share_files()
rip.arm_force("fp-ARTHUR_CHRISTMAS")
again = run(new_job("ARTHUR_CHRISTMAS"))
check("finished", again["state"], "done")
check("same folder as last time", again["remote_name"], "Movies/Arthur Christmas - Bluray")
check("no third copy appeared", share_files(), before)

# ── direct mode, DVD ─────────────────────────────────────────────────────────

print("a DVD backup written straight to the library")
fresh_share()
os.environ["RIPARR_MOCK_DISC"] = "dvd"
db.set("transfer_mode", "direct")
db.set("verify_mode", "deep")                 # asked for, and honestly downgraded
dv = run(new_job("THE_IRON_GIANT"))
check("finished", dv["state"], "done")
check("VIDEO_TS in the film's folder", [f for f in share_files() if f.endswith(".IFO")],
      ["The Iron Giant/VIDEO_TS/VIDEO_TS.IFO", "The Iron Giant/VIDEO_TS/VTS_01_0.IFO"])
check("the local path is the library copy",
      dv["local_path"].startswith(P.LIBRARY_MOUNT), True)
check("deep was downgraded, and said so", dv["verified_mode"], "quick")
contains("...in words", dv["warning"], "no second copy")
check("no scratch folder left in the library",
      os.path.exists(os.path.join(P.LIBRARY_MOUNT, "Movies", ".riparr-incoming",
                                  "job-%d" % dv["id"])), False)
db.set("verify_mode", "quick")

# ── interrupted, then resumed ────────────────────────────────────────────────

print("a staged backup interrupted mid-send resumes as a folder")
fresh_share()
db.set("transfer_mode", "burst")
rj = new_job("PADDINGTON")
rip._run_job(rj)                                 # rips, ejects, hands to the sender
rj = db.get_job(rj["id"])
check("handed to the sender", rj["state"], "transferring")
check("nothing in the library yet", share_files(), [])
rip.recover()                                    # what boot does after a power cut
rip._send_one(db.get_job(rj["id"]))
rj = db.get_job(rj["id"])
check("finished on the resumed send", rj["state"], "done")
check("all seven DVD files arrived", len(share_files()), 7)

# ── the DVD tools are missing ────────────────────────────────────────────────

print("a DVD with no DVD tools is ripped as a film, and the user is told")
fresh_share()
os.environ["RIPARR_MOCK_DVDTOOLS"] = "0"
fb = run(new_job("SHREK"))
check("finished", fb["state"], "done")
check("as a film file", fb["output"], "mkv")
check("one MKV, where the film goes", share_files(), ["Shrek/Shrek.mkv"])
contains("the warning says why", fb["warning"], "DVD backup tools")
os.environ["RIPARR_MOCK_DVDTOOLS"] = "1"

print("...and a Blu-ray is still backed up on the same box")
os.environ["RIPARR_MOCK_DISC"] = "bluray"
check("Blu-ray needs only MakeMKV", BK.can_backup("bluray"), (True, ""))
os.environ["RIPARR_MOCK_DVDTOOLS"] = "0"
check("DVD reports the missing tools", BK.can_backup("dvd")[0], False)
os.environ["RIPARR_MOCK_DVDTOOLS"] = "1"

# ── no name ──────────────────────────────────────────────────────────────────

print("an unnamed disc asks for a name, then backs up under it")
fresh_share()
db.set("on_unknown_disc", "ask")
nj = new_job("DVD_VIDEO")                # a label that names nothing
rip._run_job(nj)
nj = db.get_job(nj["id"])
check("waiting for a human", nj["state"], "needs_input")
check("no title list to choose from", nj["titles"] in (None, "[]", []), True)
ok, _ = rip.answer(nj["id"], name="Spirited Away (2001)")
check("answer accepted", ok, True)
nj = run(db.get_job(nj["id"]))
check("finished", nj["state"], "done")
check("named from the answer, year and all", nj["remote_name"],
      "Movies/Spirited Away (2001)")
db.set("on_unknown_disc", "label")

# ── the film path is unchanged ───────────────────────────────────────────────

print("with backups off, a disc still makes one MKV")
fresh_share()
db.set("rip_mode", "main")
mj = run(new_job("BLADE_RUNNER"))
check("finished", mj["state"], "done")
check("output recorded as mkv", mj["output"], "mkv")
check("one file", share_files(), ["Blade Runner/Blade Runner.mkv"])

# ── the smbclient listing parser ─────────────────────────────────────────────

print("a recursive smbclient listing is read correctly")
# The shape smbclient prints for `recurse ON; ls "dir/*"`: the folder's own entries,
# then a share-relative header before each subfolder's. Names with spaces and digits,
# an entry with no attribute letters, and a directory that must not count as a file.
SAMPLE = r"""  .                                   D        0  Sat Oct  3 12:00:00 2026
  ..                                  D        0  Sat Oct  3 12:00:00 2026
  VIDEO_TS                            D        0  Sat Oct  3 12:00:00 2026
  read me 2.txt                               5  Sat Oct  3 12:00:00 2026

\Movies\Film (1999)\VIDEO_TS
  .                                   D        0  Sat Oct  3 12:00:00 2026
  ..                                  D        0  Sat Oct  3 12:00:00 2026
  VIDEO_TS.IFO                        A    12288  Sat Oct  3 12:00:00 2026
  VTS_01_1.VOB                        A 1073709056  Sat Oct  3 12:00:00 2026

		61049651 blocks of size 4096. 1530 blocks available
"""
real_mock = P.MOCK
t = SH.Transport({"host": "nas", "path": "Root", "username": "", "password": ""})
t._run = lambda command, timeout=120: (0, SAMPLE, "")
P.MOCK = False
try:
    got = t.tree_sizes("Movies/Film (1999)")
finally:
    P.MOCK = real_mock
check("every file, by relative path, with its size", got, {
    "read me 2.txt": 5, "VIDEO_TS/VIDEO_TS.IFO": 12288,
    "VIDEO_TS/VTS_01_1.VOB": 1073709056})

shutil.rmtree(_tmp, ignore_errors=True)
print()
if failures:
    print("%d FAILED: %s" % (len(failures), ", ".join(failures)))
    sys.exit(1)
print("all passed")
