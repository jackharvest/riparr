#!/usr/bin/env python3
"""A whole season disc, end to end, through the real pipeline.

`tv-detect.test.py` proves the reasoning. This proves the plumbing: that a season disc
identified as one job produces *many* files, in the right folders, with the right names,
and that the film path did not change shape on the way past.

It runs the actual `_identify`, `_rip_season` and `_transfer_season` against the mock
drive and the mock share, with a throwaway database. Nothing here is stubbed except the
hardware, which is the same thing every other mock in this repository means.

Worth knowing what this catches that unit tests could not: the pipeline was written for
one job to produce one file, and that assumption is spread across five stages and a
dozen job columns. Ripping a season with it is the only way to find the places that
still believe it -- `local_path` holding a directory, `bytes_total` meaning the sum of
six things, staging cleanup running after the first episode and deleting the other five.
That last one was real, and `direct_mode_keeps_the_rest_of_the_season` is why.

Run: python3 server/tv-pipeline.test.py
"""
import os
import shutil
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

_tmp = tempfile.mkdtemp(prefix="riparr-tv-test-")
os.environ["RIPARR_DB"] = os.path.join(_tmp, "test.db")
os.environ["RIPARR_MOCK_CONTENT"] = "tv"
os.environ["RIPARR_APPLIANCE"] = "0"

from riparr import db, rip, shares as SH  # noqa: E402

failures = []


def check(name, got, want):
    if got == want:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s: got %r, wanted %r" % (name, got, want))
        failures.append(name)


def contains(name, haystack, needle):
    check(name, needle in haystack, True)


def share_files():
    """Every file under the mock share, share-relative and sorted."""
    out = []
    for root, _dirs, files in os.walk(SH.MOCK_SHARE_ROOT):
        for f in files:
            out.append(os.path.relpath(os.path.join(root, f), SH.MOCK_SHARE_ROOT))
    return sorted(out)


def fresh_share():
    shutil.rmtree(SH.MOCK_SHARE_ROOT, ignore_errors=True)
    for row in db.list_shares():
        db.delete_share(row["id"])
    return db.add_share("Media", "nas", "Media", None, None, make_default=True)


def run(job):
    """Identify and run one job to completion."""
    os.environ["RIPARR_MOCK_LABEL"] = job.get("disc_label") or ""
    s = rip._settings()
    job = rip._identify(job, s)
    if job is None:
        return None                     # parked on a question
    base = rip._rip_season(job, s, threading.Event())
    transport, folder, base = rip._transfer_season(job, s, base, threading.Event())
    rip._finish_season(job, s, transport, folder, base)
    return db.get_job(job["id"])


def new_job(label):
    """A queued job for a disc with this volume label, in the tray.

    The label goes on the mock drive as well as the job, because `_identify_season`
    reads the drive first -- that is where a real label comes from, and pointing the
    test at the job field instead would have exercised a path no disc ever takes.
    """
    os.environ["RIPARR_MOCK_LABEL"] = label
    job_id = db.create_job(state="queued", disc_label=label, fingerprint="fp-" + label)
    return db.get_job(job_id)


print("settings and schema came up")
db.init()
db.set("setup_complete", True)
db.set("tv_metadata", False)          # no network in a test; names come from numbers
db.set("on_season_disc", "auto")      # exercise the unattended path first
db.set("transfer_mode", "burst")      # stage on the card, then send
db.set("verify_mode", "quick")
check("tv columns exist", "episode_plan" in db.get_job(db.create_job(state="queued")), True)

# ── the unattended path ──────────────────────────────────────────────────────

print("a season disc rips as a season, unattended")
share = fresh_share()
job = run(new_job("THE_EXPANSE_S02_D01"))
check("the job finished", job["state"], "done")
check("it is a TV job", job["kind"], "tv")
check("season came off the label", job["season"], 2)
plan = db.episode_plan(job)
check("six episodes planned", len(plan["episodes"]), 6)
check("every one of them landed",
      [e["state"] for e in plan["episodes"]], ["done"] * 6)

files = share_files()
check("six files in the library, not one", len(files), 6)
check("named and foldered for Plex and Jellyfin", files,
      ["nas/Media/TV/The Expanse/Season 02/The Expanse - S02E%02d.mkv" % n
       for n in range(1, 7)])

print("the episodes are in the disc's order, not MakeMKV's")
check("title 3 is episode 1, as the play-all says",
      [(e["title_index"], e["episode"]) for e in plan["episodes"]],
      [(3, 1), (1, 2), (5, 3), (0, 4), (4, 5), (2, 6)])

print("the next disc of the same season continues the numbering")
job2 = run(new_job("THE_EXPANSE_S02_D02"))
plan2 = db.episode_plan(job2)
check("disc two starts at episode 7", plan2["episodes"][0]["episode"], 7)
check("and runs to twelve", plan2["episodes"][-1]["episode_last"], 12)
check("twelve files in the library now", len(share_files()), 12)

# ── the film path is unchanged ───────────────────────────────────────────────

print("a film disc still rips as a film")
os.environ["RIPARR_MOCK_CONTENT"] = "movie"
fresh_share()
mjob = new_job("BLADE_RUNNER")
s = rip._settings()
mjob = rip._identify(mjob, s)
check("not taken by the season path", mjob is not None, True)
check("still a movie", mjob.get("kind") or "movie", "movie")
path = rip._rip(mjob, s, threading.Event())
transport, name, path = rip._transfer(mjob, s, path, threading.Event())
rip._finish(mjob, s, transport, name, path)
check("one file, in the movie folder", share_files(),
      ["nas/Media/Movies/Blade Runner/Blade Runner.mkv"])
os.environ["RIPARR_MOCK_CONTENT"] = "tv"

# ── the question, and the answer ─────────────────────────────────────────────

print("an unlabelled season disc stops and asks")
fresh_share()
db.set("on_season_disc", "unsure")
ask_job = new_job("DISC_1")            # no season anywhere
s = rip._settings()
check("parked, not ripped", rip._identify(ask_job, s), None)
ask_job = db.get_job(ask_job["id"])
check("waiting for a human", ask_job["state"], "needs_input")
contains("and says why", ask_job["question"], "nothing says which season")
proposed = db.episode_plan(ask_job)
check("with a plan already drawn up", len(proposed["episodes"]), 6)
check("no season on it yet", proposed["season"], None)

print("the answer is applied, and remembered")
ok, msg = rip.answer(ask_job["id"], season=3, first_episode=4, name="Twin Peaks")
check("accepted", ok, True)
answered = db.get_job(ask_job["id"])
check("back in the queue", answered["state"], "queued")
apl = db.episode_plan(answered)
check("season set", apl["season"], 3)
check("numbering starts where asked",
      [e["episode"] for e in apl["episodes"]], [4, 5, 6, 7, 8, 9])
disc = db.get_disc("fp-DISC_1")
check("and the disc remembers it, so it never asks twice",
      (disc["season"], disc["first_episode"]), (3, 4))

answered["_device"] = "/dev/sr0"
answered["_plan"] = apl
base = rip._rip_season(answered, rip._settings(), threading.Event())
tr, folder, base = rip._transfer_season(answered, rip._settings(), base,
                                        threading.Event())
rip._finish_season(answered, rip._settings(), tr, folder, base)
check("filed under the season the human gave it", share_files(),
      ["nas/Media/TV/Twin Peaks/Season 03/Twin Peaks - S03E%02d.mkv" % n
       for n in range(4, 10)])

print("unticking an episode renumbers the rest")
fresh_share()
j = new_job("SOMETHING_S01_D01")
rip._identify(j, rip._settings())
j = db.get_job(j["id"])
if j["state"] != "needs_input":
    db.update_job(j["id"], state="needs_input")
plan_before = db.episode_plan(db.get_job(j["id"]))
keep = [e["title_index"] for e in plan_before["episodes"]][2:]     # drop the first two
ok, _ = rip.answer(j["id"], season=1, first_episode=1, include=keep)
after = db.episode_plan(db.get_job(j["id"]))
kept = [e for e in after["episodes"] if e.get("include", True)]
check("four episodes kept", len(kept), 4)
check("numbered from one, with no gap",
      [e["episode"] for e in kept], [1, 2, 3, 4])
check("the dropped ones are marked skipped",
      [e["state"] for e in after["episodes"] if not e.get("include", True)],
      ["skipped", "skipped"])

# ── direct mode ──────────────────────────────────────────────────────────────

print("direct mode keeps the rest of the season")
# The bug this guards: `_place_directly` tidies the whole job directory when it is
# done, which on a season disc deletes every episode still waiting to be sent. The
# first episode landed and the other five vanished between stages.
fresh_share()
db.set("transfer_mode", "direct")
db.set("on_season_disc", "auto")
dj = run(new_job("DIRECT_SHOW_S01_D01"))
check("finished", dj["state"], "done")
check("all six survived the transfer", len(share_files()), 6)
db.set("transfer_mode", "burst")

# ── specials ─────────────────────────────────────────────────────────────────

print("season zero goes where specials go")
fresh_share()
sj = new_job("SPECIALS_S00_D01")
rip._identify(sj, rip._settings())
sj = db.get_job(sj["id"])
if sj["state"] == "needs_input":
    rip.answer(sj["id"], season=0, first_episode=1)
    sj = db.get_job(sj["id"])
splan = db.episode_plan(sj)
check("rendered into Season 00",
      "/Season 00/" in rip._episode_name(sj, rip._settings(), splan,
                                         splan["episodes"][0]), True)
db.set("tv_specials_folder", "Specials")
check("or into Specials when that is the setting",
      "/Specials/" in rip._episode_name(sj, rip._settings(), splan,
                                        splan["episodes"][0]), True)

print("")
shutil.rmtree(_tmp, ignore_errors=True)
shutil.rmtree(SH.MOCK_SHARE_ROOT, ignore_errors=True)
if failures:
    print("%d FAILED: %s" % (len(failures), ", ".join(failures)))
    sys.exit(1)
print("all good")
