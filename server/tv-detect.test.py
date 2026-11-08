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
from riparr.rip import _mock_titles, _renumbered_by_hand  # noqa: E402

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
    old = os.environ.get("RIPARR_MOCK_CONTENT")
    os.environ["RIPARR_MOCK_CONTENT"] = kind
    try:
        return _mock_titles()
    finally:
        if old is None:
            os.environ.pop("RIPARR_MOCK_CONTENT", None)
        else:
            os.environ["RIPARR_MOCK_CONTENT"] = old


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

print("a film with featurettes is not a season with the film as its play-all")
# Shipped in 0.6.1: two or three featurettes of similar length made a "cluster", and the
# film -- the longest title -- was taken for the play-all and excluded. The film is not
# the featurettes strung together, by segments or by length, so it is a film.
check("film plus two 25-minute featurettes",
      tv.analyse([t(0, 120, 8, "00800.mpls", "1"), t(1, 25, 1, "00801.mpls", "2"),
                  t(2, 26, 1, "00802.mpls", "3")], FLOOR), None)
check("film plus three featurettes, DVD, no segment map",
      tv.analyse([t(0, 110), t(1, 16), t(2, 18), t(3, 20)], FLOOR), None)
check("film plus four featurettes",
      tv.analyse([t(0, 120), t(1, 20), t(2, 19.7), t(3, 20.3), t(4, 20.2)], FLOOR), None)
DVD_PLAYALL = [t(0, 22), t(1, 21.7), t(2, 22.2), t(3, 21.8), t(4, 85.8)]
f_dvd = tv.analyse(DVD_PLAYALL, FLOOR)
check("a DVD play-all with no segment map is recognised by adding up",
      (len(f_dvd["episodes"]), f_dvd["play_all"]["index"]), (4, 4))
BONUS_DOC = [t(i, 42 + i * 0.2, source="0080%d.mpls" % i, segments=str(i)) for i in range(6)] + [
    t(9, 100, 6, "00900.mpls", "50")]
f_doc = tv.analyse(BONUS_DOC, FLOOR)
check("a feature-length bonus on a real season disc is an extra, not a play-all",
      (len(f_doc["episodes"]), f_doc["play_all"], [e["index"] for e in f_doc["extras"]]),
      (6, None, [9]))

print("the last disc's two episodes and their play-all are two episodes")
# The play-all of a two-episode disc is exactly twice an episode, which is the length of
# a double-length premiere. Read as one, both episodes went to disk twice.
TWO = [t(0, 44, source="00800.mpls", segments="1"), t(1, 43.3, source="00801.mpls", segments="2"),
       t(2, 85, 7.8, source="00802.mpls", segments="1,2", chapters=12)]
f_two = tv.analyse(TWO, FLOOR)
check("two episodes", [e["index"] for e in f_two["episodes"]], [0, 1])
check("and the 2x title is the play-all", f_two["play_all"]["index"], 2)
TWO_DVD = [t(0, 44), t(1, 43.3), t(2, 85)]
f_two_dvd = tv.analyse(TWO_DVD, FLOOR)
check("on a DVD length can't say, so the 2x title is kept rather than lost",
      ([e["index"] for e in f_two_dvd["episodes"]], f_two_dvd["play_all"],
       f_two_dvd["doubles"], f_two_dvd["unsure_doubles"]), ([0, 1, 2], None, [2], [2]))

print("a DVD's segment maps are per title and say nothing across titles (real discs)")
def dvd(i, seconds, segments, chapters=8):
    return {"index": i, "seconds": seconds, "bytes": 2 * 10**9, "name": "", "file": "",
            "source": "", "segments": segments, "chapters": chapters, "audio": 2}
DVD_PREMIERE = [dvd(0, 5429, "1,2,3,4,5-7,8,9,10-12,13-14,15-16,17,18-19,20-22,23-24,25", 18),
          dvd(1, 2718, "1,2-3,4-5,6-7,8,9,10-11,12,13"),
          dvd(2, 2715, "1,2-3,4-5,6-7,8-9,10-11,12-13,14-15,16")]
DVD_FOUR = [dvd(0, 2714, "1,2,3-4,5-6,7-9,10-11,12-13,14,15"),
          dvd(1, 2716, "1,2,3-4,5-7,8-9,10-11,12-14,15,16"),
          dvd(2, 2721, "1,2,3-5,6-7,8-10,11-12,13-14,15,16"),
          dvd(3, 2709, "1,2,3-5,6-7,8,9-11,12-14,15,16")]
f_d1 = tv.analyse(DVD_PREMIERE, FLOOR)
check("a DVD premiere and two episodes are not a film",
      ([e["index"] for e in f_d1["episodes"]], f_d1["play_all"], f_d1["doubles"], f_d1["duplicates"]),
      ([0, 1, 2], None, [0], []))
f_d2 = tv.analyse(DVD_FOUR, FLOOR)
check("a four-episode DVD keeps all four episodes",
      ([e["index"] for e in f_d2["episodes"]], f_d2["duplicates"]), ([0, 1, 2, 3], []))
check("the disc's own label is read", tv.season_from_label("EXS1D1"), (1, 1))
check("and the show's name kept", tv.series_name_from_label("EXS1D1"), "Ex")
check("a lone S1 inside a word is not a season", tv.season_from_label("CLASS1"), (None, None))
check("nor a name ending in a D number", tv.season_from_label("APOLLO13D1"), (None, None))

print("eleven-minute cartoons are episodes, not extras")
CARTOONS = [t(i, 11 + (i % 3) * 0.2, 0.6, "%05d.mpls" % (800 + i), str(i)) for i in range(20)] + [
    t(40, 4, 0.2, "00900.mpls", "90"), t(41, 6, 0.3, "00901.mpls", "91")]
f_c = tv.analyse(CARTOONS, FLOOR)
check("twenty of them", f_c and len(f_c["episodes"]), 20)
SHORTS = [t(i, 7, 0.4, "%05d.mpls" % (800 + i), str(i)) for i in range(12)]
check("seven-minute shorts too", len(tv.analyse(SHORTS, FLOOR)["episodes"]), 12)
DELETED = [t(0, 118, 30, "00800.mpls", "1")] + [
    t(i, 6 + i * 0.1, 0.3, "%05d.mpls" % (900 + i), str(10 + i)) for i in range(1, 7)]
check("a film with six short deleted scenes is still a film", tv.analyse(DELETED, FLOOR), None)
print("many short featurettes do not outvote fewer, longer episodes")
MIXED = [t(i, 42 + i * 0.3, 3.9, "%05d.mpls" % (800 + i), str(i)) for i in range(6)] + [
    t(20 + i, 8 + i * 0.1, 0.5, "%05d.mpls" % (900 + i), str(20 + i)) for i in range(10)]
f_m = tv.analyse(MIXED, FLOOR)
check("the six episodes win on running time", sorted(e["index"] for e in f_m["episodes"]),
      list(range(6)))
check("and the ten featurettes are extras", len(f_m["extras"]), 10)

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

print("a play-all that stops short orders the rest by segment, when the disc agrees")
# Samurai Jack season 5's shape: the play-all holds episodes 1-2 and stops; episode 3
# is a playlist numbered after them, on the segment after theirs. The playlist numbers
# and the segment numbers are two separate records, and they agree.
PARTIAL = [t(0, 42, segments="5", source="00801.mpls"),
           t(1, 42, segments="6", source="00802.mpls"),
           t(2, 42, segments="99", source="00803.mpls"),
           t(9, 126, 11.0, segments="5,6", source="00700.mpls", chapters=18)]
f4 = tv.analyse(PARTIAL, FLOOR)
check("placed past the play-all, trusted",
      ([e["index"] for e in f4["episodes"]], f4["order_source"], f4["confidence"]),
      ([0, 1, 2], "segments", "high"))
check("and the user is told", f4["beyond_play_all"], [2])

print("...and only shows its guess when the two records disagree")
# The same, but the episode past the play-all has a playlist number from before the
# run. One record says "last", the other "first": not a fact, so not trusted.
DISAGREE2 = [t(0, 42, segments="5", source="00801.mpls"),
             t(1, 42, segments="6", source="00802.mpls"),
             t(2, 42, segments="99", source="00800.mpls"),
             t(9, 126, 11.0, segments="5,6", source="00700.mpls", chapters=18)]
f4d = tv.analyse(DISAGREE2, FLOOR)
check("still all three episodes", sorted(e["index"] for e in f4d["episodes"]), [0, 1, 2])
check("but at medium confidence, so it is checked", f4d["confidence"], "medium")

print("a map whose segments aren't in order is not extended")
SCRAMBLED = [t(0, 42, segments="7", source="00801.mpls"),
             t(1, 42, segments="5", source="00802.mpls"),
             t(2, 42, segments="99", source="00803.mpls"),
             t(9, 126, 11.0, segments="7,5", source="00700.mpls", chapters=18)]
f4s = tv.analyse(SCRAMBLED, FLOOR)
check("falls through to playlist numbering", f4s["order_source"], "source")

print("an episode-length title outside a complete play-all is an extra")
# Samurai Jack season 2's shape: the play-all runs exactly as long as the episodes it
# holds, so the map is whole and the title it leaves out is a bonus, not episode 4.
OUTSIDE = [t(0, 22, segments="5", source="00801.mpls"),
           t(1, 22, segments="6", source="00802.mpls"),
           t(2, 22, segments="7", source="00800.mpls"),
           t(3, 21, segments="99", source="02630.m2ts"),
           t(9, 66, 9.0, segments="5,6,7", source="00820.mpls", chapters=12)]
f4b = tv.analyse(OUTSIDE, FLOOR)
check("three episodes, ordered by the play-all",
      ([e["index"] for e in f4b["episodes"]], f4b["order_source"]), ([0, 1, 2], "segments"))
check("and the outsider is offered as an extra",
      (f4b["outside_play_all"], [e["index"] for e in f4b["extras"]]), ([3], [3]))

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
                    ("BLADE_RUNNER", (None, None)),
                    # The other words box sets use for a season, and number words.
                    ("Avatar_Book_1_Disc_1", (1, 1)),
                    ("Avatar: The Last Airbender Book One: Water Disc 1", (1, 1)),
                    ("Doctor_Who_Series_3_Disc_2", (3, 2)),
                    ("FUTURAMA_VOL_4", (4, None)),
                    ("Samurai Jack - The Complete First Season", (1, None)),
                    ("FRIENDS_S10", (10, None))]:
    check(label, tv.season_from_label(label), want)
check("the show is what comes before the season, subtitle and all left behind",
      tv.series_name_from_label("Avatar: The Last Airbender Book One: Water Disc 1"),
      "Avatar: The Last Airbender")
check("...and \"The Complete\" is not part of a name",
      tv.series_name_from_label("Samurai Jack - The Complete First Season"),
      "Samurai Jack")
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

print("a typed name that is a guide episode takes that episode's number")
GUIDE = ([{"season": 1, "number": n, "name": nm, "special": False, "runtime": 50}
          for n, nm in enumerate(["Story A Part 1", "Story A Part 2", "Story B Part 1",
                                  "Story B Part 2", "Story C"], 1)])


def rows3():
    return [{"title_index": 0, "seconds": 6000, "span": 2},
            {"title_index": 1, "seconds": 3000, "span": 1},
            {"title_index": 2, "seconds": 3000, "span": 1}]


r = rows3()
tv.number_rows(r, 1, 1, GUIDE)
check("untyped: counted from the start", [(x["episode"], x["episode_last"]) for x in r],
      [(1, 2), (3, 3), (4, 4)])
r = rows3()
r[0]["episode_title"], r[1]["episode_title"], r[2]["episode_title"] = (
    "Story C Part 1 & Part 2", "story b, part 1", "Story B Part 2")
tv.number_rows(r, 1, 1, GUIDE, typed={0, 1, 2})
check("typed: each row takes its guide number",
      [(x["episode"], x["episode_last"]) for x in r], [(5, 5), (3, 3), (4, 4)])
check("typed names are kept on the files", r[0]["episode_title"], "Story C Part 1 & Part 2")
check("positions still count the disc", [x["position"] for x in r], [1, 3, 4])
r = rows3()
r[1]["episode_title"] = "Story A Part 1"
tv.number_rows(r, 1, 1, GUIDE, typed={1})
check("a pin that would collide with a counted row is not applied",
      [(x["episode"], x["episode_last"]) for x in r], [(1, 2), (3, 3), (4, 4)])
r = rows3()
r[1]["episode_title"] = "Something else entirely"
tv.number_rows(r, 1, 1, GUIDE, typed={1})
check("a typed name the guide doesn't know keeps the counted number", r[1]["episode"], 3)

import json  # noqa: E402
print("a disc renumbered by hand stops the next disc from carrying on unseen")
check("counted in order: carry on",
      _renumbered_by_hand({"episode_plan": json.dumps({"episodes": [
          {"position": 1, "episode": 1}, {"position": 2, "episode": 2}]})}), False)
check("a row moved off its position: hold the next disc",
      _renumbered_by_hand({"episode_plan": json.dumps({"episodes": [
          {"position": 1, "episode": 5}, {"position": 2, "episode": 2}]})}), True)
check("an unticked row doesn't count",
      _renumbered_by_hand({"episode_plan": json.dumps({"episodes": [
          {"position": 1, "episode": 5, "include": False}]})}), False)
check("a DVD order is the guide's, not a correction",
      _renumbered_by_hand({"episode_plan": json.dumps({"dvd_map": {"1": {}}, "episodes": [
          {"position": 1, "episode": 5}]})}), False)

print("")
if failures:
    print("%d FAILED: %s" % (len(failures), ", ".join(failures)))
    sys.exit(1)
print("all good")
