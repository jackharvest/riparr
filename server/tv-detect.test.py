#!/usr/bin/env python3
"""Is this a season disc, which title is which episode, and what do the files get called.

This exists because the failure modes here are all *silent*. A film disc that picks the
wrong title gives you a four-minute menu loop and you know within a second. A season
disc that gets it wrong gives you six files with plausible names, in the wrong order,
and you find out in episode three of a rewatch a year later.

Every case below is a real disc-authoring pattern, taken from the MakeMKV forums, and
each one broke an earlier version of `tv.py`:

* `play_all_does_not_eat_the_season` is the one that mattered. The play-all's segment
  map contains every episode's segments, so deduplicating before removing it merged the
  entire disc into a single four-hour "episode" -- confidently, with no warning.
* `duplicate_playlists` is the twelve-titles-for-six-episodes disc.
* `disc_order_beats_broadcast_order` is Firefly: the metadata is *not* allowed to
  reorder the disc, only to name what the disc already decided.
* `film_disc_is_not_a_season_disc` is the regression guard. Every one of these
  heuristics is a chance to start seeing television in a film, and a film disc that
  suddenly rips as "Season None" is a worse bug than no TV support at all.

Run: python3 server/tv-detect.test.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from riparr import tv  # noqa: E402
from riparr.rip import _mock_titles  # noqa: E402

GB = 2 ** 30
FLOOR = 120                       # the shipped min_title_seconds

failures = []


def check(name, got, want):
    if got == want:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s: got %r, wanted %r" % (name, got, want))
        failures.append(name)


def t(index, minutes, gb=3.9, source="", segments="", chapters=6, name=""):
    return {"index": index, "seconds": int(minutes * 60), "bytes": int(gb * GB),
            "name": name, "file": "", "source": source, "segments": segments,
            "chapters": chapters}


def mock(kind):
    old = os.environ.get("RIPARR_MOCK_DISC")
    os.environ["RIPARR_MOCK_DISC"] = kind
    try:
        return _mock_titles()
    finally:
        if old is None:
            os.environ.pop("RIPARR_MOCK_DISC", None)
        else:
            os.environ["RIPARR_MOCK_DISC"] = old


# ── the disc the mock drive holds ────────────────────────────────────────────

print("play all does not eat the season")
found = tv.analyse(mock("tv"), FLOOR)
check("recognised as a season disc", found is not None, True)
check("six episodes, not one", len(found["episodes"]), 6)
check("the play-all was found", found["play_all"]["index"], 21)
check("and excluded from the episodes",
      21 in [e["index"] for e in found["episodes"]], False)
check("its segment map gave the order", found["order_source"], "segments")
check("which we say we trust", found["confidence"], "high")
check("episodes in broadcast order, not title order",
      [e["index"] for e in found["episodes"]], [3, 1, 5, 0, 4, 2])

print("duplicate playlists")
check("six duplicates ignored", len(found["duplicates"]), 6)
check("and the longer cut of each was the one kept",
      sorted(e["seconds"] for e in found["episodes"]),
      [2528, 2534, 2540, 2545, 2549, 2551])
check("the featurette is not an episode",
      20 in [e["index"] for e in found["episodes"]], False)
check("but it is reported as an extra", 20 in [e["index"] for e in found["extras"]], True)

print("no play-all: fall back to playlist numbering")
found2 = tv.analyse(mock("tv-noplayall"), FLOOR)
check("still a season disc", len(found2["episodes"]), 6)
check("ordered by .mpls number", found2["order_source"], "source")
check("which we are less sure about", found2["confidence"], "medium")
check("same answer as the segment map gave",
      [e["index"] for e in found2["episodes"]], [3, 1, 5, 0, 4, 2])

print("one welded title is detected, not split")
blob = mock("tv-blob")
check("flagged as a blob", tv.looks_like_blob(blob, FLOOR), True)
check("and not offered as a season", tv.analyse(blob, FLOOR), None)
check("an ordinary film disc is not a blob",
      tv.looks_like_blob(mock("movie"), FLOOR), False)

# ── regression: television must not be seen in a film ────────────────────────

print("film disc is not a season disc")
check("the mock film disc", tv.analyse(mock("movie"), FLOOR), None)
# A film plus its extras: making-of, deleted scenes, gag reel. Three longish titles,
# but nothing like each other in length, so there is no cluster.
FILM_WITH_EXTRAS = [t(0, 131, 7.8, "00001.mpls", "1", 32),
                    t(1, 47, 1.9, "00002.mpls", "20", 4),
                    t(2, 22, 0.9, "00003.mpls", "21", 2),
                    t(3, 16, 0.6, "00004.mpls", "22", 1)]
check("film plus three featurettes", tv.analyse(FILM_WITH_EXTRAS, FLOOR), None)
# The 3D case from choose-title.test.py: two cuts of one film at the same length. That
# is a cluster of two, and it must not read as a two-episode disc.
THREE_D = [t(0, 95, 28.4, "00001.mpls", "1"), t(1, 95, 12.1, "00002.mpls", "1"),
           t(2, 94.97, 11.9, "00003.mpls", "1"), t(5, 4, 0.3, "00010.mpls", "9")]
check("a 3D film's two cuts are not two episodes", tv.analyse(THREE_D, FLOOR), None)

print("obfuscated film disc is not a season disc")
# ~100 near-identical decoys is the shape this detector is most likely to misread:
# a huge tight cluster of long titles. They are all the same film.
DECOYS = [{"index": i, "seconds": 6000 - i, "bytes": 24 * GB - i * 1000,
           "name": "", "file": "", "source": "%05d.mpls" % (800 + i),
           "segments": "1", "chapters": 20} for i in range(8)]
check("decoys share a segment and collapse to one", tv.analyse(DECOYS, FLOOR), None)

# ── ordering ─────────────────────────────────────────────────────────────────

print("segment map beats playlist numbering when they disagree")
# The documented Firefly/Vikings case: .mpls order is not episode order, but the
# play-all knows. Titles are authored 803, 800, 801 for episodes 1, 2, 3.
DISAGREE = [t(0, 42, segments="5", source="00803.mpls"),
            t(1, 42, segments="6", source="00800.mpls"),
            t(2, 42, segments="7", source="00801.mpls"),
            t(9, 126, 11.0, segments="5,6,7", source="00700.mpls", chapters=18)]
f3 = tv.analyse(DISAGREE, FLOOR)
check("the play-all is authoritative", f3["order_source"], "segments")
check("so the .mpls order is overruled", [e["index"] for e in f3["episodes"]], [0, 1, 2])

print("a partial segment map is refused, not half-used")
# One episode is not in the play-all's map. Half a season in the right order and half
# appended arbitrarily is harder to spot than an obviously unsorted one.
PARTIAL = [t(0, 42, segments="5", source="00801.mpls"),
           t(1, 42, segments="6", source="00802.mpls"),
           t(2, 42, segments="99", source="00803.mpls"),
           t(9, 126, 11.0, segments="5,6", source="00700.mpls", chapters=18)]
f4 = tv.analyse(PARTIAL, FLOOR)
check("falls through to playlist numbering", f4["order_source"], "source")
check("and gets the right answer anyway",
      [e["index"] for e in f4["episodes"]], [0, 1, 2])

print("no signal at all admits it")
BARE = [t(0, 42), t(1, 42), t(2, 42)]
f5 = tv.analyse(BARE, FLOOR)
check("order source is the title index", f5["order_source"], "index")
check("and we say we do not trust it", f5["confidence"], "low")

# ── double-length episodes ───────────────────────────────────────────────────

print("a double-length premiere takes two episode numbers")
DOUBLE = [t(0, 84, 7.8, segments="1,2", source="00800.mpls", chapters=12),
          t(1, 42, segments="3", source="00801.mpls"),
          t(2, 42, segments="4", source="00802.mpls"),
          t(3, 42, segments="5", source="00803.mpls")]
f6 = tv.analyse(DOUBLE, FLOOR)
check("four titles, all episodes", len(f6["episodes"]), 4)
check("the long one is marked a double", f6["doubles"], [0])
plan = tv.build_plan(f6, season=1, first_episode=1)
check("it spans E01-E02", (plan["episodes"][0]["episode"],
                           plan["episodes"][0]["episode_last"]), (1, 2))
check("and the next episode is E03", plan["episodes"][1]["episode"], 3)
check("the last is E05", plan["episodes"][-1]["episode"], 5)

# ── labels ───────────────────────────────────────────────────────────────────

print("season and disc out of the volume label")
for label, want in [("THE_WIRE_S01_D03", (1, 3)),
                    ("BREAKING_BAD_SEASON_2_DISC_3", (2, 3)),
                    ("FIREFLY_DISC_1", (None, 1)),
                    ("GOT_S3_D2", (3, 2)),
                    ("BLADE_RUNNER", (None, None))]:
    check(label, tv.season_from_label(label), want)
check("series name is cleaned of the markers",
      tv.series_name_from_label("BREAKING_BAD_SEASON_2_DISC_3"), "Breaking Bad")
check("...and of the disc marker alone",
      tv.series_name_from_label("THE_WIRE_S01_D03"), "The Wire")

# ── the disc decides order, the metadata only names ──────────────────────────

print("disc order beats broadcast order")
# Firefly. The disc opens with "Serenity"; TVmaze numbers "The Train Job" as S01E01
# because that is what aired first. The rule is that the disc's first title is E01 and
# it takes E01's *name* -- wrong, visibly wrong, and fixable with one control. What must
# never happen is the file being reordered to match the metadata, because then the name
# and the contents disagree and nothing records which is which.
EPISODES = [{"season": 1, "number": 1, "name": "The Train Job", "runtime": 60,
             "special": False},
            {"season": 1, "number": 2, "name": "Bushwhacked", "runtime": 60,
             "special": False},
            {"season": 1, "number": 3, "name": "Our Mrs. Reynolds", "runtime": 60,
             "special": False}]
f7 = tv.analyse([t(0, 42, segments="1", source="00801.mpls"),
                 t(1, 42, segments="2", source="00802.mpls"),
                 t(2, 42, segments="3", source="00803.mpls")], FLOOR)
p7 = tv.build_plan(f7, season=1, first_episode=1, episode_list=EPISODES)
check("first title on the disc is still E01", p7["episodes"][0]["title_index"], 0)
check("and takes E01's name from the metadata",
      p7["episodes"][0]["episode_title"], "The Train Job")
check("nothing was reordered",
      [e["title_index"] for e in p7["episodes"]], [0, 1, 2])
# Shifting the start is the fix a user applies when the disc is not aired order.
p8 = tv.build_plan(f7, season=1, first_episode=2, episode_list=EPISODES)
check("shifting the start renumbers and renames together",
      [(e["episode"], e["episode_title"]) for e in p8["episodes"]],
      [(2, "Bushwhacked"), (3, "Our Mrs. Reynolds"), (4, "")])

print("warnings say what is uncertain")
w = tv.plan_warnings(p7, f7, EPISODES)
check("running past the end of the season is called out",
      any("runs past the end" in x for x in
          tv.plan_warnings(tv.build_plan(f7, season=1, first_episode=3,
                                         episode_list=EPISODES), f7, EPISODES)), True)
check("a low-confidence order is called out",
      any("often not broadcast order" in x
          for x in tv.plan_warnings(tv.build_plan(f5, season=1), f5)), True)
check("a missing season is called out",
      any("No season number" in x for x in
          tv.plan_warnings(tv.build_plan(f7, season=None), f7)), True)
check("the play-all exclusion is explained",
      any("play all" in x for x in tv.plan_warnings(plan, f6)), False)

print("")
if failures:
    print("%d FAILED: %s" % (len(failures), ", ".join(failures)))
    sys.exit(1)
print("all good")
