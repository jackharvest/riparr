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
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

_tmp = tempfile.mkdtemp(prefix="riparr-tv-test-")
os.environ["RIPARR_DB"] = os.path.join(_tmp, "test.db")
os.environ["RIPARR_MOCK_CONTENT"] = "tv"
os.environ["RIPARR_APPLIANCE"] = "0"
os.environ["RIPARR_MOCK_FAST"] = "1"   # skip the mock rip's deliberate pacing

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


def run_all(job):
    """As `run`, but the way the worker does it: a season held for its naming stops
    after the rip, with the disc out, instead of going on to the library."""
    os.environ["RIPARR_MOCK_LABEL"] = job.get("disc_label") or ""
    s = rip._settings()
    job = rip._identify(job, s)
    if job is None:
        return None
    base = rip._rip_season(job, s, threading.Event())
    if job["_plan"].get("hold"):
        rip._hold_after_rip(job)
        return db.get_job(job["id"])
    transport, folder, base = rip._transfer_season(job, s, base, threading.Event())
    rip._finish_season(job, s, transport, folder, base)
    return db.get_job(job["id"])


def send(job_id):
    """What the sender thread does with a released job."""
    j = db.get_job(job_id)
    check("released to the sender", j["state"], "transferring")
    rip._send_one(j)
    return db.get_job(job_id)


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

# ── "unsure": stop once per season, not once per disc ────────────────────────

print("the default does not stop on a disc it can read and name")
# The order comes off the disc, and with no episode names on the files there is no
# broadcast-order question to ask. The first disc used to stop here regardless, which
# parked every unattended box set on its first disc.
fresh_share()
db.set("on_season_disc", "unsure")
d1 = run_all(new_job("SOME_SHOW_S05_D01"))
check("first disc rips straight through", d1["state"], "done")
d2 = run_all(new_job("SOME_SHOW_S05_D02"))
check("and so does the second", d2["state"], "done")
check("carrying on from seven", db.episode_plan(d2)["episodes"][0]["episode"], 7)

print("a season on DVD is ripped, then held for a person, every disc")
# No real DVD season has been through the planner, and a DVD has no segment map. So the
# default trusts it as far as the rip, and no further.
fresh_share()
os.environ["RIPARR_MOCK_DISC"] = "dvd"
v1 = run_all(new_job("DVD_SHOW_S01_D01"))
check("read and ejected, waiting to be named", (v1["state"], bool(v1["local_path"])),
      ("needs_input", True))
check("held because it is a DVD", db.episode_plan(v1)["hold"]["reason"], "dvd")
contains("and saying why", v1["question"], "on DVD")
check("nothing in the library yet", share_files(), [])
ok, _msg = rip.answer(v1["id"])
check("an empty answer accepts it", ok, True)
check("and it is filed", (send(v1["id"])["state"], len(share_files())), ("done", 6))
db.set("on_season_disc", "auto")
v2 = run_all(new_job("DVD_SHOW_S01_D02"))
check("\"Never\" still means never, even on DVD", v2["state"], "done")
db.set("on_season_disc", "unsure")
os.environ.pop("RIPARR_MOCK_DISC")

# Names on, through stand-ins for TVmaze. What settles broadcast order is TVmaze's
# DVD-release list: none (an empty answer) means disc order is aired order.
_real = (rip.tv.search_series, rip.tv.episodes, rip.tv.dvd_order)
rip.tv.search_series = lambda name, limit=6: [{"id": 77, "name": "Named Show",
                                               "year": "2004", "score": 1.0}]
rip.tv.episodes = lambda sid, include_specials=True: [
    {"season": 1, "number": n, "name": "Episode %d" % n, "runtime": 42, "special": False}
    for n in range(1, 13)]
db.set("tv_metadata", True)

print("a show TVmaze says has no other order rips without asking")
fresh_share()
rip.tv.dvd_order = lambda sid: {}
n1 = run_all(new_job("NAMED_SHOW_S01_D01"))
check("done, nobody asked", n1["state"], "done")
check("named from TVmaze", share_files()[0],
      "nas/Media/TV/Named Show (2004)/Season 01/Named Show - S01E01 - Episode 1.mkv")

print("when TVmaze can't be asked, it rips anyway and holds only the naming")
fresh_share()
rip.tv.dvd_order = lambda sid: None
db.conn().execute("DELETE FROM jobs WHERE title='Named Show'")
db.conn().execute("DELETE FROM discs")   # a fresh copy of the set, never seen before
db.conn().commit()
h = run_all(new_job("NAMED_SHOW_S01_D01"))
check("read, and waiting to be named", (h["state"], bool(h["local_path"])),
      ("needs_input", True))
contains("saying so", h["question"], "Ripped and ejected")
check("which does not hold the drive", (db.drive_busy() or {}).get("id") != h["id"], True)
check("nothing in the library yet", share_files(), [])
ok, msg = rip.answer(h["id"])
check("an empty answer accepts it", ok, True)
h = send(h["id"])
check("and it is filed", (h["state"], len(share_files())), ("done", 6))

print("a show released in production order is named from the DVD list")
# Firefly's shape: DVD position 1 is the episode the guides call E11.
fresh_share()
db.conn().execute("DELETE FROM jobs WHERE title='Named Show'")
db.conn().execute("DELETE FROM discs")   # a fresh copy of the set, never seen before
db.conn().commit()
order = [11, 1, 2, 6, 7, 3, 4, 5, 8, 9, 10, 12]
rip.tv.dvd_order = lambda sid: {(1, i + 1): {"season": 1, "number": a,
                                             "name": "Episode %d" % a}
                                for i, a in enumerate(order)}
f1 = run_all(new_job("NAMED_SHOW_S01_D01"))
check("rips without asking", f1["state"], "done")
fp = db.episode_plan(f1)
check("positions are disc order", [e["position"] for e in fp["episodes"]],
      [1, 2, 3, 4, 5, 6])
check("file numbers are the broadcast ones", [e["episode"] for e in fp["episodes"]],
      [11, 1, 2, 6, 7, 3])
contains("and the user is told", fp["warnings"][0], "TVmaze's DVD order")
check("the first file is S01E11 with its own name",
      "nas/Media/TV/Named Show (2004)/Season 01/Named Show - S01E11 - Episode 11.mkv"
      in share_files(), True)
f2 = run_all(new_job("NAMED_SHOW_S01_D02"))
check("disc two carries on in disc order: positions 7-12",
      [e["position"] for e in db.episode_plan(f2)["episodes"]][::5], [7, 12])
check("named 4, 5, 8, 9, 10, 12",
      [e["episode"] for e in db.episode_plan(f2)["episodes"]], [4, 5, 8, 9, 10, 12])
check("twelve distinct files", len(set(share_files())), 12)

rip.tv.search_series, rip.tv.episodes, rip.tv.dvd_order = _real
db.set("tv_metadata", False)
db.set("on_season_disc", "auto")

# ── discs put in out of order ───────────────────────────────────────────────

def finish(job_id):
    j = db.get_job(job_id)
    j["_device"], j["_plan"] = "/dev/sr0", db.episode_plan(j)
    b = rip._rip_season(j, rip._settings(), threading.Event())
    tr, fo, b = rip._transfer_season(j, rip._settings(), b, threading.Event())
    rip._finish_season(j, rip._settings(), tr, fo, b)
    return db.get_job(job_id)


print("disc 3 before disc 2 does not push disc 2 to the end")
# The box set is open on the sofa and disc 2 is in the other player. Numbering used to
# be "after the highest episode so far", so disc 2 became E13-E18 behind disc 3.
fresh_share()
db.set("on_season_disc", "auto")
o1 = run(new_job("OUT_OF_ORDER_S01_D01"))
check("disc one is 1-6", [db.episode_plan(o1)["episodes"][i]["episode"] for i in (0, -1)],
      [1, 6])
o3 = run_all(new_job("OUT_OF_ORDER_S01_D03"))
check("disc three, with disc two missing, is read and held, even under auto",
      (o3["state"], bool(o3["local_path"])), ("needs_input", True))
p3 = db.episode_plan(o3)
check("proposing 13 from the size of disc one", p3["episodes"][0]["episode"], 13)
contains("and saying it is a guess", p3["warnings"][0], "can't know where")
check("without holding the drive for disc two",
      (db.drive_busy() or {}).get("id") != o3["id"], True)
o2 = run_all(new_job("OUT_OF_ORDER_S01_D02"))
check("disc two does not ask -- disc one says where it starts", o2["state"], "done")
check("and it is 7-12, not 19-24",
      [db.episode_plan(o2)["episodes"][i]["episode"] for i in (0, -1)], [7, 12])
o3 = send(o3["id"])
check("disc three then files itself, nobody asked", o3["state"], "done")
check("as 13-18", [db.episode_plan(o3)["episodes"][i]["episode"] for i in (0, -1)],
      [13, 18])
check("eighteen files", len(share_files()), 18)

print("disc 2 after disc 3 alone counts back from disc 3")
fresh_share()
b3 = run_all(new_job("COUNT_BACK_S02_D03"))
ok, _ = rip.answer(b3["id"], season=2, first_episode=13)
check("a human can settle a held disc's numbering", ok, True)
send(b3["id"])
b2 = run(new_job("COUNT_BACK_S02_D02"))
check("ends right before disc three", db.episode_plan(b2)["episodes"][-1]["episode"], 12)
check("so starts at 7", db.episode_plan(b2)["episodes"][0]["episode"], 7)

# ── extras ──────────────────────────────────────────────────────────────────

print("a ticked extra lands in Featurettes, beside the seasons")
fresh_share()
db.set("auto_tv", "show")
xj = new_job("EXTRAS_SHOW_S01_D01")
rip._identify(xj, rip._settings())
xplan = db.episode_plan(db.get_job(xj["id"]))
check("only the featurette is offered -- not the duplicate playlists",
      [x["title_index"] for x in xplan["extras"]], [20])
check("and it starts unticked", xplan["extras"][0]["include"], False)
ok, _ = rip.answer(xj["id"], season=1, first_episode=1,
                   extras={"20": "Behind the Scenes"})
check("accepted", ok, True)
xj = finish(xj["id"])
check("the job finished", xj["state"], "done")
files = share_files()
check("six episodes and one extra", len(files), 7)
check("the extra is in the show's Featurettes folder",
      "nas/Media/TV/Extras Show/Featurettes/Behind the Scenes.mkv" in files, True)
xj2 = new_job("EXTRAS_SHOW_S01_D02")
rip._identify(xj2, rip._settings())
check("unnamed extras are named for their season",
      db.episode_plan(db.get_job(xj2["id"]))["extras"][0]["name"], "Season 1 Extra 1")
rip.answer(xj2["id"], extras={"20": "Behind the Scenes"})
finish(xj2["id"])
check("a second disc's extra with the same name sits beside the first, not over it",
      sorted(f for f in share_files() if "Featurettes" in f),
      ["nas/Media/TV/Extras Show/Featurettes/Behind the Scenes (2).mkv",
       "nas/Media/TV/Extras Show/Featurettes/Behind the Scenes.mkv"])
check("and it did not move the next disc's numbering",
      db.next_episode(None, 1, series_name="Extras Show"), 13)
db.set("auto_tv", "rip")
db.set("on_season_disc", "auto")

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

print("an unlabelled season disc is ripped, then asks for its season")
fresh_share()
db.set("on_season_disc", "unsure")
ask_job = run_all(new_job("DISC_1"))   # no season anywhere
check("read, ejected, and waiting for a human",
      (ask_job["state"], bool(ask_job["local_path"])), ("needs_input", True))
contains("and says why", ask_job["question"], "nothing on the disc says which season")
proposed = db.episode_plan(ask_job)
check("with a plan already drawn up", len(proposed["episodes"]), 6)
check("no season on it yet", proposed["season"], None)

print("the answer is applied, and remembered")
ok, msg = rip.answer(ask_job["id"], season=3, first_episode=4, name="Twin Peaks")
check("accepted", ok, True)
answered = db.get_job(ask_job["id"])
check("straight to the sender, not back to the drive", answered["state"], "transferring")
apl = db.episode_plan(answered)
check("season set", apl["season"], 3)
check("numbering starts where asked",
      [e["episode"] for e in apl["episodes"]], [4, 5, 6, 7, 8, 9])
disc = db.get_disc("fp-DISC_1")
check("and the disc remembers it, so it never asks twice",
      (disc["season"], disc["first_episode"]), (3, 4))

send(answered["id"])
check("filed under the season the human gave it", share_files(),
      ["nas/Media/TV/Twin Peaks/Season 03/Twin Peaks - S03E%02d.mkv" % n
       for n in range(4, 10)])

print("a folder name typed over a matched series is used")
fresh_share()
db.set("auto_tv", "show")
rj = new_job("RENAMED_S01_D01")
rip._identify(rj, rip._settings())
rplan = db.episode_plan(db.get_job(rj["id"]))
rip.answer(rj["id"], name=rplan["series"])
check("an untouched box changes nothing", db.episode_plan(db.get_job(rj["id"]))["series"],
      rplan["series"])
db.update_job(rj["id"], state="needs_input")
rip.answer(rj["id"], name="Renamed By Hand")
check("an edited one is the folder", db.get_job(rj["id"])["title"], "Renamed By Hand")
db.set("auto_tv", "rip")
db.set("on_season_disc", "unsure")

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

print("MakeMKV's own message is what a failure says")
# Found on Samurai Jack season 2: the NAS stalled 800 MB into episode 12, and the job
# failed with "Saving to MKV file" -- the phase label, not the reason.
real = ("Error 'Internal error - Operation result is incorrect (178)' occurred while "
        "writing data to '/srv/library/RiparrDumps/.riparr-incoming/job-2/ep-12/x.mkv' "
        "at offset '838860800'")
check("a write failure is the library's", rip._makemkv_failure(real)[0], "write")
contains("and says so", rip._makemkv_failure(real)[1], "Couldn't write to your library")
check("routine commentary is not a failure",
      rip._makemkv_failure("Title #00821.mpls has length of 66 seconds which is less "
                           "than minimum title length"), None)

_real_mock_rip = rip._mock_rip_episode


def failing(title, times, where):
    """The mock rip, failing `times` times on one title the way MakeMKV does."""
    left = {"n": times}

    def rip_episode(job, out_dir, cancel_ev, row, progress, first_byte):
        if int(row["title_index"]) == title and left["n"] > 0:
            left["n"] -= 1
            err = rip.RipFailed("Couldn't write to your library: it stopped accepting "
                                "data partway through." if where == "write" else
                                "The drive couldn't read part of the disc.")
            err.where = where
            raise err
        return _real_mock_rip(job, out_dir, cancel_ev, row, progress, first_byte)
    return rip_episode


print("a share that stalls once costs one retry, onto the card")
fresh_share()
db.set("transfer_mode", "direct")
rip._mock_rip_episode = failing(0, 1, "write")
st = run(new_job("STALL_SHOW_S01_D01"))
check("the season still finished", st["state"], "done")
check("all six filed", len(share_files()), 6)
retried = [e for e in db.episode_plan(st)["episodes"] if e["title_index"] == 0][0]
check("the retried episode was read onto the card",
      retried["path"].startswith(rip.STAGING), True)

print("an episode that can't be read loses that episode, not the season")
fresh_share()
rip._mock_rip_episode = failing(5, 99, "read")
bad = run(new_job("SCRATCHED_S01_D01"))
check("the job says something needs doing", bad["state"], "failed")
check("five episodes filed anyway", len(share_files()), 5)
contains("naming the missing one", bad["error"], "S01E03 couldn't be ripped")
contains("and what to do", bad["error"], "Put the disc back in")
check("the disc is not recorded as ripped, so it can go back in",
      rip._already_have(db.get_disc("fp-SCRATCHED_S01_D01")), False)

print("putting it back in reads only what's missing")
rip._mock_rip_episode = _real_mock_rip
read = []


def counting(job, out_dir, cancel_ev, row, progress, first_byte):
    read.append(int(row["title_index"]))
    return _real_mock_rip(job, out_dir, cancel_ev, row, progress, first_byte)


rip._mock_rip_episode = counting
again = run(new_job("SCRATCHED_S01_D01"))
check("done", again["state"], "done")
check("only the missing episode was read", read, [5])
check("all six in the library, under the first attempt's numbers", share_files(),
      ["nas/Media/TV/Scratched/Season 01/Scratched - S01E%02d.mkv" % n
       for n in range(1, 7)])
rip._mock_rip_episode = _real_mock_rip
db.set("transfer_mode", "burst")

print("a box that dies mid-season keeps what it read, and carries on")
# 2026-10-12: the Wi-Fi chip froze the kernel seven episodes into a disc. Recovery on
# the next boot used to delete those seven.
fresh_share()
db.set("transfer_mode", "direct")
cut = new_job("POWER_CUT_S01_D01")
os.environ["RIPARR_MOCK_LABEL"] = "POWER_CUT_S01_D01"
cj = rip._identify(cut, rip._settings())
stop_after = {"n": 0}


def dies_after_three(job, out_dir, cancel_ev, row, progress, first_byte):
    stop_after["n"] += 1
    if stop_after["n"] > 3:
        raise KeyboardInterrupt          # the power goes
    return _real_mock_rip(job, out_dir, cancel_ev, row, progress, first_byte)


rip._mock_rip_episode = dies_after_three
try:
    rip._rip_season(cj, rip._settings(), threading.Event())
except KeyboardInterrupt:
    pass
rip._mock_rip_episode = _real_mock_rip
rip.recover()
dead = db.get_job(cj["id"])
check("marked failed", dead["state"], "failed")
contains("saying what it kept", dead["error"], "The 3 episodes it had read are kept")
read.clear()
rip._mock_rip_episode = counting
back = run(new_job("POWER_CUT_S01_D01"))
rip._mock_rip_episode = _real_mock_rip
check("the disc going back in finishes it", back["state"], "done")
check("reading only the three it hadn't", len(read), 3)
check("six episodes filed", len([f for f in share_files() if "Power Cut" in f]), 6)
db.set("transfer_mode", "burst")

# ── interrupted, then resumed ────────────────────────────────────────────────

print("a season interrupted mid-transfer resumes where it stopped")
# What a reboot leaves behind: some episodes in the library, some still on the card,
# and a plan that says which is which. The bug this guards is that a season job's
# `local_path` is a *directory*, and `_send_one` used to hand it to the single-file
# transfer -- which takes the size of a directory entry and uploads that.
fresh_share()
db.set("transfer_mode", "burst")
rj = run(new_job("INTERRUPTED_S04_D01"))
check("all six landed first time", len(share_files()), 6)

rplan = db.episode_plan(rj)
for e in rplan["episodes"][3:]:                 # pretend the last three never went
    os.remove(os.path.join(SH.MOCK_SHARE_ROOT, "nas", "Media", e["remote_name"]))
    e["state"] = "ripped"
    e.pop("remote_name", None)
db.update_job(rj["id"], episode_plan=rplan, state="transferring",
              finished_at=None, error=None)
check("three files left in the library", len(share_files()), 3)

rip._send_one(db.get_job(rj["id"]))
resumed = db.get_job(rj["id"])
check("the job finished on the second pass", resumed["state"], "done")
check("and all six are there again", len(share_files()), 6)
check("every row marked done",
      [e["state"] for e in db.episode_plan(resumed)["episodes"]], ["done"] * 6)

print("re-verification checks every episode, not a directory")
ok, msg = rip.reverify(rj["id"], mode="quick")
check("accepted", ok, True)
contains("and says how many", msg, "6 episodes")
for _ in range(80):
    if db.get_job(rj["id"])["state"] in ("done", "failed"):
        break
    time.sleep(0.05)
check("and they all pass", db.get_job(rj["id"])["state"], "done")

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

print("a disputed show can be picked, and the rest of the set follows it")
fresh_share()
os.environ["RIPARR_MOCK_DISC"] = "dvd"
os.environ["RIPARR_WIKIDATA"] = "1"
db.set("tv_metadata", True)
db.set("on_season_disc", "unsure")
_saved = (rip.tv.search_series, rip.tv.episodes, rip.tv.dvd_order, rip.tv.show,
          rip.WD.search_series, rip.WD.series_facts)
OTHER = {"id": 3292, "name": "Other Show", "year": "1993", "score": 0.31, "runtime": 61}
EXAMPLE = {"id": 101, "name": "Example Show", "year": "1993", "network": "Syndication",
       "score": 0, "runtime": 60}
rip.tv.search_series = lambda name, limit=6: [EXAMPLE] if "Example" in name else [OTHER]
rip.tv.show = lambda sid: EXAMPLE if sid == 101 else None
rip.tv.dvd_order = lambda sid: {}
rip.tv.episodes = lambda sid, include_specials=True: [
    {"season": 1, "number": n, "name": "%s %d" % ("EX" if sid == 101 else "SQ", n),
     "runtime": 60, "special": False} for n in range(1, 21)]
rip.WD.search_series = lambda name: [{"qid": "Q1", "label": "Example Show",
                                      "tvmaze": [101]}]
rip.WD.series_facts = lambda ids: {}
d1 = run_all(new_job("EXS1D1"))
p1 = db.episode_plan(d1)
check("held for the dispute", (d1["state"], p1["witness"]["show"]), ("needs_input", "disputed"))
check("TVmaze's pick and Wikidata's are both offered",
      [o["id"] for o in p1["series_options"]], [3292, 101])
ok, _msg = rip.answer(d1["id"], season=1, first_episode=1, series_id=101,
                      name=p1["series"])
check("picking Wikidata's is accepted", ok, True)
check("and filed under it, with its names",
      (send(d1["id"])["state"], db.episode_plan(db.get_job(d1["id"]))["series"],
       db.episode_plan(db.get_job(d1["id"]))["episodes"][0]["episode_title"]),
      ("done", "Example Show", "EX 1"))
n1 = max(e["episode"] for e in db.episode_plan(db.get_job(d1["id"]))["episodes"])
d2 = run_all(new_job("EXS1D2"))
p2 = db.episode_plan(d2)
check("disc 2 needs no answer", d2["state"], "done")
check("disc 2 has the same show", (d2["series_id"], p2["series"]),
      (101, "Example Show"))
check("and carries on the numbering", p2["episodes"][0]["episode"], n1 + 1)
contains("and says where that came from", " ".join(p2["warnings"]), "carried on from EXS1D1")
check("a label with no set marker follows nothing", rip.tv.set_key("EX"), "")

print("a bonus disc of a filed set goes to specials, every title of it")
# A real bonus disc's title list. It used to be filed as a film of its longest title.
_read_titles = rip.read_titles
_bonus = [{"index": i, "seconds": n, "bytes": n * 1000, "segments": "1,2", "source": "",
           "chapters": 2}
          for i, n in enumerate([1086, 860, 609, 287, 177, 323, 194, 161, 144, 135, 143, 176,
                                 136, 162])]
rip.read_titles = lambda *a, **k: [dict(t) for t in _bonus]
db.set("tv_specials_folder", "Season 00")
b1 = run_all(new_job("EXS1D6"))
pb = db.episode_plan(b1)
check("not a film", (b1["kind"], b1["season"], b1["title"]),
      ("tv", 0, "Example Show"))
check("held for a person", (b1["state"], pb["hold"]["reason"]), ("needs_input", "bonus"))
check("every title is kept, in disc order",
      [e["title_index"] for e in pb["episodes"]], list(range(14)))
check("numbered from one, and named for the disc",
      (pb["episodes"][0]["episode"], pb["episodes"][0]["episode_title"]),
      (1, "S1 Disc 6 Feature 01"))
ok, _msg = rip.answer(b1["id"], season=0, first_episode=1, name=pb["series"])
check("checking it is accepted", ok, True)
check("and it files", send(b1["id"])["state"], "done")
_named = [f for f in share_files() if "Season 00" in f]
check("fourteen specials in the library", len(_named), 14)
contains("under the show, with the disc's names", _named[0],
         "Example Show (1993)/Season 00/Example Show - S00E01 - S1 Disc 6 Feature 01")
check("nothing went to Movies", [f for f in share_files() if f.startswith("Movies/")], [])
again = run_all(new_job("EXS1D6"))
check("a re-rip keeps its own numbers, so it replaces its own files",
      db.episode_plan(again)["episodes"][0]["episode"], 1)
rip.answer(again["id"], skip=True)
b2 = run_all(new_job("EXS2D7"))
check("the next bonus disc carries on the numbers",
      db.episode_plan(b2)["episodes"][0]["episode"], 15)
rip.answer(b2["id"], skip=True)
check("a bonus disc tells the next disc nothing about its season",
      db.filed_set_disc(rip.tv.set_key, "ex")["season"], 1)
_saved_episodes = rip.tv.episodes
rip.tv.episodes = lambda sid, include_specials=True: [
    {"season": 0, "number": 30, "name": "A real special", "runtime": 60, "special": True}]
b3 = run_all(new_job("EXS3D7"))
check("a guide's own specials keep their numbers",
      db.episode_plan(b3)["episodes"][0]["episode"], 31)
rip.answer(b3["id"], skip=True)
rip.tv.episodes = _saved_episodes
b5 = run_all(new_job("EXS5D7"))
rip.answer(b5["id"], season=1, first_episode=1, name=db.episode_plan(b5)["series"])
check("corrected to a real season, it takes the guide's names",
      db.episode_plan(db.get_job(b5["id"]))["episodes"][0]["episode_title"], "EX 1")
send(b5["id"])
_film_job = rip._identify(new_job("HEAT"), rip._settings())
check("the same titles with no set stay a film",
      (_film_job["kind"] if _film_job else "parked", _film_job["chosen_title"]), (None, 0))
_bonus[0]["seconds"] = 2 * 3600
b4 = run_all(new_job("EXS4D7"))
p4 = db.episode_plan(b4)
check("a film-length title in a filed set is held, not sent to Movies",
      (b4["kind"], b4["state"], p4["hold"]["reason"]), ("tv", "needs_input", "unsure"))
check("with the longest title offered as the film",
      p4["film_option"], {"title_index": 0, "name": rip.pretty_label("EXS4D7")})
rip.answer(b4["id"], skip=True)
rip.read_titles = _read_titles
(rip.tv.search_series, rip.tv.episodes, rip.tv.dvd_order, rip.tv.show,
 rip.WD.search_series, rip.WD.series_facts) = _saved
os.environ.pop("RIPARR_WIKIDATA")
os.environ.pop("RIPARR_MOCK_DISC")
db.set("tv_metadata", False)

print("with a TMDB key, numbers follow TMDB")
fresh_share()
db.conn().execute("DELETE FROM jobs"); db.conn().execute("DELETE FROM discs"); db.conn().commit()
_tv_real = rip.tv.tmdb_episodes
def _tmdb(sid, key):
    names = ["Pilot", "Second", "Third", "Fourth", "Fifth", "Sixth", "Seventh"]
    return [{"season": 1, "number": i + 1, "name": n, "special": False,
             "runtime": 90 if i == 0 else 45} for i, n in enumerate(names)]
rip.tv.tmdb_episodes = _tmdb
db.set("tmdb_api_key", "test-key")
db.set("on_season_disc", "auto")
_found = {"episodes": [{"index": 0, "seconds": 5429}, {"index": 1, "seconds": 2718},
                       {"index": 2, "seconds": 2715}], "doubles": [0],
          "order_source": "index", "confidence": "low"}
fp = rip.tv.build_plan(_found, season=1, first_episode=1, series=EXAMPLE,
                       episode_list=_tmdb(101, "k"), fit_runtimes=True)
check("the 90-minute opener is one episode",
      [(e["episode"], e["episode_last"], e["episode_title"]) for e in fp["episodes"]],
      [(1, 1, "Pilot"), (2, 2, "Second"), (3, 3, "Third")])
split = [dict(e, runtime=45) for e in _tmdb(101, "k")]
sp = rip.tv.build_plan(_found, season=1, first_episode=1, series=EXAMPLE, episode_list=split,
                       fit_runtimes=True)
check("a guide listing two parts keeps the double", sp["episodes"][0]["episode_last"], 2)
check("a list without a key's guide is left alone",
      rip.tv.build_plan(_found, season=1, series=EXAMPLE, episode_list=_tmdb(101, "k")
                        )["episodes"][0]["episode_last"], 2)
check("TMDB answered", rip._guide(101, "k")[1], "tmdb")
rip.tv.tmdb_episodes = lambda sid, key: []
check("TMDB failing falls back to TVmaze", rip._guide(101, "k")[1], "tvmaze")
check("no key never asks TMDB", rip._guide(101, "")[1], "tvmaze")

print("a disc filed from TMDB, and the next one when TMDB can't answer")
_saved = (rip.tv.search_series, rip.tv.episodes, rip.tv.dvd_order, rip.tv._get, rip.tv._tmdb_get)
rip.tv.search_series = lambda name, limit=6: [dict(EXAMPLE, name="Named Show", id=78)]
rip.tv.episodes = lambda sid, include_specials=True: [
    {"season": 1, "number": n, "name": "Maze %d" % n, "runtime": 60, "special": False}
    for n in range(1, 21)]
rip.tv.dvd_order = lambda sid: {}
db.set("tv_metadata", True)
db.set("on_season_disc", "unsure")
rip.tv.tmdb_episodes = lambda sid, key: [
    {"season": 1, "number": n, "name": "Tmdb %d" % n, "runtime": 45, "special": False}
    for n in range(1, 21)]
t1 = run_all(new_job("NAMED_SHOW_S01_D01"))
tp = db.episode_plan(t1)
check("filed with TMDB's names", (t1["state"], tp["numbering"], tp["episodes"][0]["episode_title"]),
      ("done", "tmdb", "Tmdb 1"))
rip.tv.tmdb_episodes = lambda sid, key: []
t2 = run_all(new_job("NAMED_SHOW_S01_D02"))
check("TVmaze's numbers wait for a person, even carried on",
      (t2["state"], db.episode_plan(t2)["numbering"], db.episode_plan(t2)["hold"]["reason"]),
      ("needs_input", "tvmaze", "numbering"))
rip.tv.tmdb_episodes = _tv_real

print("TMDB's answers are read into the same shape as TVmaze's")
rip.tv._get = lambda url, timeout=12: {"externals": {"thetvdb": 72073, "imdb": "tt0106145"}}
_calls = []
def _fake_tmdb(path, key):
    _calls.append(path)
    if path.startswith("/find/72073?external_source=tvdb_id"):
        return {"tv_results": [{"id": 580}]}
    if path == "/tv/580":
        return {"seasons": [{"season_number": 0}, {"season_number": 1}]}
    return {"season/0": {"episodes": [{"episode_number": 1, "name": "Extra"}]},
            "season/1": {"episodes": [{"episode_number": 1, "name": "Pilot", "runtime": 90}]}}
rip.tv._tmdb_get = _fake_tmdb
rip.tv._tmdb_cache.clear()
check("special flagged, runtime kept", rip.tv.tmdb_episodes(101, "k"),
      [{"season": 0, "number": 1, "name": "Extra", "runtime": 0, "special": True},
       {"season": 1, "number": 1, "name": "Pilot", "runtime": 90, "special": False}])
def _boom(path, key):
    raise OSError("401")
rip.tv._tmdb_get = _boom
rip.tv._tmdb_cache.clear()
check("a refused key is an empty answer, not a crash", rip.tv.tmdb_episodes(101, "bad"), [])
(rip.tv.search_series, rip.tv.episodes, rip.tv.dvd_order, rip.tv._get, rip.tv._tmdb_get) = _saved
db.set("tv_metadata", False)
db.set("tmdb_api_key", "")

print("a remembered start loses to a filed neighbour, and a held plan renames as it renumbers")
fresh_share()
os.environ["RIPARR_MOCK_DISC"] = "dvd"
_saved = (rip.tv.search_series, rip.tv.episodes, rip.tv.dvd_order)
SHOW = {"id": 79, "name": "Carry Show", "year": "1995", "score": 1.0, "runtime": 45}
rip.tv.search_series = lambda name, limit=6: [SHOW]
rip.tv.episodes = lambda sid, include_specials=True: [
    {"season": 1, "number": n, "name": "Name %d" % n, "runtime": 45, "special": False}
    for n in range(1, 31)]
rip.tv.dvd_order = lambda sid: {}
db.set("tv_metadata", True)
db.set("on_season_disc", "unsure")
c1 = run_all(new_job("CARRY_SHOW_S01_D01"))
check("disc 1 on DVD is held", c1["state"], "needs_input")
names = {str(e["title_index"]): e["episode_title"] for e in db.episode_plan(c1)["episodes"]}
check("the plan carries the season's names for the page",
      db.episode_plan(c1)["guide_names"]["3"], "Name 3")
first_ti = db.episode_plan(c1)["episodes"][0]["title_index"]
names[str(first_ti)] = "Typed By Hand"
ok, _m = rip.answer(c1["id"], season=1, first_episode=2, series_id=79,
                    name="Carry Show", episode_titles=names)
cp = db.episode_plan(db.get_job(c1["id"]))
check("renumbering renames what nobody typed, and keeps what was typed",
      [e["episode_title"] for e in cp["episodes"]][:3], ["Typed By Hand", "Name 3", "Name 4"])
send(c1["id"])
last = max(e["episode_last"] for e in cp["episodes"])
db.record_disc("fp-CARRY_SHOW_S01_D02", series_id=79, season=1, series_name="Carry Show",
               first_episode=last + 3)
c2 = run_all(new_job("CARRY_SHOW_S01_D02"))
p2 = db.episode_plan(c2)
check("a stale memory gives way to the filed disc, without asking",
      (c2["state"], p2["episodes"][0]["episode"]), ("done", last + 1))
contains("and says so", " ".join(p2["warnings"]), "Riparr remembered E%02d" % (last + 3))
last2 = max(e["episode_last"] for e in p2["episodes"])
db.record_disc("fp-CARRY_SHOW_S01_D03", series_id=79, season=1, series_name="Carry Show",
               first_episode=last2 + 1)
c3 = run_all(new_job("CARRY_SHOW_S01_D03"))
check("a memory that agrees is carried, not held",
      (c3["state"], db.episode_plan(c3)["episodes"][0]["episode"]), ("done", last2 + 1))
(rip.tv.search_series, rip.tv.episodes, rip.tv.dvd_order) = _saved
os.environ.pop("RIPARR_MOCK_DISC")
db.set("tv_metadata", False)
db.set("on_season_disc", "unsure")

print("a season's last disc, one long episode and its extras, is that episode")
fresh_share()
os.environ["RIPARR_MOCK_DISC"] = "dvd"
_saved = (rip.tv.search_series, rip.tv.episodes, rip.tv.dvd_order, rip.tv.tmdb_episodes)
SHOW = {"id": 81, "name": "Last Show", "year": "1993", "score": 1.0, "runtime": 45}
rip.tv.search_series = lambda name, limit=6: [SHOW]
rip.tv.episodes = lambda sid, include_specials=True: [
    {"season": 7, "number": n, "name": "Maze %d" % n, "runtime": 45, "special": False}
    for n in range(1, 7)]
rip.tv.dvd_order = lambda sid: {}
rip.tv.tmdb_episodes = lambda sid, key: [
    {"season": 7, "number": n, "name": "Last %d" % n, "runtime": 90 if n == 5 else 45,
     "special": False} for n in range(1, 6)]
db.set("tv_metadata", True)
db.set("tmdb_api_key", "k")
_four = [{"index": i, "seconds": 2700 + i, "bytes": 2700000, "segments": "1,2", "source": "",
          "chapters": 6} for i in range(4)]
rip.read_titles = lambda *a, **k: [dict(t) for t in _four]
f1 = run_all(new_job("LASTSHOW_S7D1"))
rip.answer(f1["id"], season=7, first_episode=1, series_id=81, name="Last Show")
check("disc 1 files E01-E04", send(f1["id"])["state"], "done")
# A real finale disc's title list: the 91-minute finale, four featurettes, ten shorts.
_last = [{"index": i, "seconds": n, "bytes": n * 1000, "segments": "1,2", "source": "",
          "chapters": 2}
         for i, n in enumerate([5499, 921, 782, 608, 852, 143, 157, 267, 172, 184, 254, 185,
                                208, 155, 259])]
rip.read_titles = lambda *a, **k: [dict(t) for t in _last]
f2 = run_all(new_job("LASTSHOW_S7D2"))
fp = db.episode_plan(f2)
check("not a film", (f2["kind"], f2["season"]), ("tv", 7))
check("held for a person", (f2["state"], fp["hold"]["reason"]), ("needs_input", "finale"))
check("the long title is the next episode, by the guide's name",
      [(e["title_index"], e["episode"], e["episode_last"], e["episode_title"])
       for e in fp["episodes"]], [(0, 5, 5, "Last 5")])
check("the extras are offered, unticked",
      (len(fp["extras"]), any(x["include"] for x in fp["extras"])), (14, False))
rip.answer(f2["id"], season=7, first_episode=5, series_id=81, name="Last Show")
check("and it files", send(f2["id"])["state"], "done")
contains("into the season, not Movies", " ".join(share_files()),
         "Season 07/Last Show - S07E05 - Last 5")
check("nothing went to Movies", [f for f in share_files() if f.startswith("Movies/")], [])
rip.tv.tmdb_episodes = lambda sid, key: []
rip.tv.episodes = lambda sid, include_specials=True: [
    {"season": 7, "number": n, "name": "Maze %d" % n, "runtime": 45, "special": False}
    for n in range(1, 8)]
_split = run_all(new_job("LASTSHOW_S7D3"))
check("a guide that splits it into two parts files a double",
      [(e["episode"], e["episode_last"]) for e in db.episode_plan(_split)["episodes"]],
      [(6, 7)])
rip.answer(_split["id"], skip=True)
rip.tv.episodes = lambda sid, include_specials=True: [
    {"season": 7, "number": n, "name": "Maze %d" % n, "runtime": 45, "special": False}
    for n in range(1, 7)]
_held = run_all(new_job("LASTSHOW_S7D4"))
_hp = db.episode_plan(_held)
check("no episode left in the guide to match: held, not filed as anything",
      (_held["state"], _hp["hold"]["reason"], _hp["film_option"]["title_index"]),
      ("needs_input", "unsure", 0))
ok, _m = rip.answer(_held["id"], as_film=True, title_index=0, name="Last Film (1999)")
check("filing it as a film is accepted", (ok, db.get_job(_held["id"])["kind"]),
      (True, "movie"))
check("and the film goes to Movies", send(_held["id"])["state"], "done")
contains("under its name", " ".join(share_files()),
         "Movies/Last Film (1999)/Last Film (1999)")
check("only the chosen title is filed",
      [f.split("Movies/", 1)[1] for f in share_files() if "Movies/" in f]
      + [f for f in share_files() if "Last Show" in f and "Season 00" in f],
      ["Last Film (1999)/Last Film (1999).mkv"])
db.set("auto_tv", "show")
_aj = new_job("LASTSHOW_S7D5")
_ask = rip._identify(_aj, rip._settings())
_aj = db.get_job(_aj["id"])
check("asked before ripping, it waits", (_ask, _aj["state"]), (None, "needs_input"))
ok, _m = rip.answer(_aj["id"], as_film=True, name="Other Film")
_aj = db.get_job(_aj["id"])
check("answered as a film, it rips its film title",
      (ok, _aj["state"], _aj["kind"], _aj["chosen_title"]), (True, "queued", "movie", 0))
_again = rip._identify(_aj, rip._settings())
check("and the rip worker takes the film path",
      (_again["chosen_title"], db.get_job(_aj["id"])["kind"]), (0, "movie"))
db.set("auto_tv", "rip")
rip.read_titles = _read_titles
(rip.tv.search_series, rip.tv.episodes, rip.tv.dvd_order, rip.tv.tmdb_episodes) = _saved
os.environ.pop("RIPARR_MOCK_DISC")
db.set("tv_metadata", False)
db.set("tmdb_api_key", "")

print("one film in two versions is a film, not a two-episode season")
# A real film DVD carrying the film twice (two sets of title cards), as MakeMKV read it.
_two_cuts = [{"index": i, "seconds": n, "bytes": n * 1000, "segments": m, "source": "",
            "chapters": c}
           for i, (n, m, c) in enumerate([(5297, "7,8,10-18,19-26,27,29,30", 13),
                                          (5297, "7,9,10-18,19-26,28,29,30", 13),
                                          (299, "1-2,3,4-5,6-7,8", 5),
                                          (843, "1,2,3,4-5,6-7,8-9,10-11,12", 12),
                                          (139, "1-2,3", 2)])]
check("two versions of one film are not a season", rip.tv.analyse(_two_cuts), None)
_long_episodes = [dict(_two_cuts[0], index=i, seconds=n) for i, n in enumerate((5280, 5340, 5310))]
check("feature-length episodes of different lengths are still a season",
      len((rip.tv.analyse(_long_episodes) or {}).get("episodes") or []), 3)

print("the sender leaves alone a job the rip worker is filing")
_w = db.create_job(state="transferring", disc_label="RACE", fingerprint="fp-RACE",
                   local_path="/nowhere/race.mkv")
check("a transferring job is the sender's", (db.next_sending_job() or {}).get("id"), _w)
check("unless the worker has it", db.next_sending_job(exclude={_w}), None)
db.update_job(_w, state="cancelled")

print("")
shutil.rmtree(_tmp, ignore_errors=True)
shutil.rmtree(SH.MOCK_SHARE_ROOT, ignore_errors=True)
if failures:
    print("%d FAILED: %s" % (len(failures), ", ".join(failures)))
    sys.exit(1)
print("all good")
