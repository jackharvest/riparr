#!/usr/bin/env python3
"""Real season discs, from real drives, through the real parser and planner.

`tv-detect.test.py` uses a mock disc built from forum folklore. These are the scans of
actual discs, taken with `makemkvcon -r --cache=1 --minlength=0 info disc:0` on the
reference box and saved under fixtures/discs, next to TVmaze's episode list for the show
as it was on the day. Each one encodes what the right answer turned out to be, checked
against the disc, so a change to the planner that breaks a disc people own fails here.

Add a disc: save its scan and its TVmaze list, then write down what it must produce.

Run: python3 server/tv-realdisc.test.py
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("RIPARR_DB", os.path.join(tempfile.mkdtemp(), "t.db"))
os.environ["RIPARR_APPLIANCE"] = "0"

from riparr import rip, tv  # noqa: E402

FIX = os.path.join(HERE, "fixtures", "discs")
# Riparr scans with MakeMKV's default minimum title length; the fixtures were taken
# with none, so they hold the menu loops too. Apply the floor the box would have.
MAKEMKV_MIN = 120
failures = []


def check(name, got, want):
    if got == want:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s: got %r, wanted %r" % (name, got, want))
        failures.append(name)


def load(scan, tvmaze):
    titles = rip.parse_info(open(os.path.join(FIX, scan), encoding="utf-8").read())
    titles = [t for t in titles if t["seconds"] >= MAKEMKV_MIN]
    raw = json.load(open(os.path.join(FIX, tvmaze), encoding="utf-8"))
    eps = [{"season": e["season"], "number": e["number"], "name": e.get("name") or "",
            "runtime": e.get("runtime") or 0,
            "special": (e.get("type") or "regular") != "regular"}
           for e in raw if e.get("season") is not None and e.get("number") is not None]
    return titles, eps


# ── Samurai Jack, The Complete First Season (Blu-ray, 2026-10-12) ────────────

print("Samurai Jack season 1: one disc, the whole season")
titles, eps = load("samurai-jack-s1-bluray.info.txt", "tvmaze-1603-samurai-jack.json")
check("the parser reads the segment map MakeMKV really prints",
      tv.parse_segments(next(t for t in titles if t["index"] == 15)["segments"])[:3],
      [2609, 2610, 2611])
found = tv.analyse(titles, MAKEMKV_MIN)
check("read as a season", found is not None, True)
check("thirteen episodes", len(found["episodes"]), 13)
check("the five-hour play-all is found", found["play_all"]["index"], 15)
check("and orders the disc, so it is trusted",
      (found["order_source"], found["confidence"]), ("segments", "high"))
check("no duplicates invented", found["duplicates"], [])
check("no double-length episodes invented", found["doubles"], [])
check("the 5:40 and 7:10 extras are extras",
      sorted(t["index"] for t in found["extras"]), [4, 19])
# The trap this disc sets: episode 7 is 00100.mpls, because MakeMKV dropped 00806 as
# identical to it. Sorting by playlist name would have filed it first.
check("00100.mpls is episode 7, where the play-all puts it, not episode 1",
      [t["index"] for t in found["episodes"]],
      [0, 1, 2, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14])
check("ordering by source name alone would have got this disc wrong",
      tv._order_by_source(found["episodes"])[0]["source"], "00100.mpls")

label = "Samurai Jack Season 1"
check("the label says season 1 and no disc number", tv.season_from_label(label), (1, None))
check("and names the show", tv.series_name_from_label(label), "Samurai Jack")

plan = tv.build_plan(found, season=1, first_episode=1,
                     series={"id": 1603, "name": "Samurai Jack", "year": "2001"},
                     episode_list=eps)
check("named in order",
      [(e["episode"], e["episode_title"]) for e in plan["episodes"]][:3] + [
          (plan["episodes"][6]["episode"], plan["episodes"][6]["episode_title"]),
          (plan["episodes"][-1]["episode"], plan["episodes"][-1]["episode_title"])],
      [(1, "The Beginning (1)"), (2, "The Samurai Called Jack (2)"),
       (3, "The First Fight (3)"), (7, "Jack and the Three Blind Archers"),
       (13, "Aku's Fairy Tales")])
check("nothing to warn about but the play-all",
      len(tv.plan_warnings(plan, found, eps)), 1)


# ── Samurai Jack, The Complete Second Season (Blu-ray, 2026-10-12) ───────────

print("")
print("Samurai Jack season 2: the same shape, plus a 21-minute title outside the play-all")
titles, eps = load("samurai-jack-s2-bluray.info.txt", "tvmaze-1603-samurai-jack.json")
found = tv.analyse(titles, MAKEMKV_MIN)
check("thirteen episodes, not fourteen", len(found["episodes"]), 13)
check("the bare 02630.m2ts is offered as an extra, not numbered",
      (found["outside_play_all"], sorted(t["index"] for t in found["extras"])),
      ([21], [4, 19, 21]))
check("so the play-all still orders the disc",
      (found["play_all"]["index"], found["order_source"]), (15, "segments"))
# The same trap as season 1, one disc on: 00101.mpls is episode 12. With title 21
# counted the segment order failed and playlist numbering filed it as E01.
check("00101.mpls is episode 12",
      [t["index"] for t in found["episodes"]],
      [0, 1, 2, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14])
check("the label says season 2", tv.season_from_label("Samurai Jack Season 2"), (2, None))
plan = tv.build_plan(found, season=2, first_episode=1,
                     series={"id": 1603, "name": "Samurai Jack", "year": "2001"},
                     episode_list=eps)
check("named in order",
      [(e["episode"], e["episode_title"]) for e in plan["episodes"]][:2] + [
          (plan["episodes"][11]["episode"], plan["episodes"][11]["episode_title"])],
      [(1, "Jack Learns to Jump Good"), (2, "Jack Tales"), (12, "Jack and the Spartans")])
warnings = tv.plan_warnings(plan, found, eps)
check("the user is told about the left-out title",
      any("Title 21 is episode-length (21 min)" in w for w in warnings), True)


# ── Samurai Jack, The Complete Third Season (Blu-ray, 2026-10-12) ────────────

print("")
print("Samurai Jack season 3: the plain one -- everything where it should be")
titles, eps = load("samurai-jack-s3-bluray.info.txt", "tvmaze-1603-samurai-jack.json")
found = tv.analyse(titles, MAKEMKV_MIN)
check("thirteen episodes in playlist order, confirmed by the play-all",
      ([t["index"] for t in found["episodes"]], found["order_source"]),
      ([0, 1, 2, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14], "segments"))
check("three extras, the 4:56 one included",
      sorted(t["index"] for t in found["extras"]), [4, 19, 21])
check("nothing left outside the play-all", found["outside_play_all"], [])
plan = tv.build_plan(found, season=3, first_episode=1,
                     series={"id": 1603, "name": "Samurai Jack", "year": "2001"},
                     episode_list=eps)
check("named in order",
      [plan["episodes"][i]["episode_title"] for i in (0, 10, 12)],
      ["Chicken Jack", "The Birth of Evil (1)", "Jack and the Labyrinth"])


# ── Samurai Jack, The Complete Fourth Season (Blu-ray, 2026-10-12) ───────────

print("")
print("Samurai Jack season 4: plain again, with one extra")
titles, eps = load("samurai-jack-s4-bluray.info.txt", "tvmaze-1603-samurai-jack.json")
found = tv.analyse(titles, MAKEMKV_MIN)
check("thirteen episodes in playlist order, confirmed by the play-all",
      ([t["index"] for t in found["episodes"]], found["order_source"]),
      ([0, 1, 2, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14], "segments"))
check("one extra", [t["index"] for t in found["extras"]], [4])


# ── Samurai Jack, Season 5 (Blu-ray, 2026-10-12) ─────────────────────────────

print("")
print("Samurai Jack season 5: the play-all stops at episode 8")
titles, eps = load("samurai-jack-s5-bluray.info.txt", "tvmaze-1603-samurai-jack.json")
found = tv.analyse(titles, MAKEMKV_MIN)
# Ten episodes. The play-all holds 1-8; 9 and 10 are 00808.mpls and 00809.mpls, on
# segments 2624 and 2625, right after it. Season 2's rule (outside the play-all means
# extra) filed them as unticked extras, at high confidence, unattended.
check("ten episodes, not eight",
      [t["index"] for t in found["episodes"]], [0, 1, 2, 5, 6, 7, 8, 9, 13, 10])
check("9 and 10 placed past the play-all, and trusted",
      (found["beyond_play_all"], found["confidence"]), ([13, 10], "high"))
check("00100.mpls is episode 7 again",
      found["episodes"][6]["source"], "00100.mpls")
check("the five long bonus .m2ts files are extras, not episodes",
      sorted(t["index"] for t in found["extras"]), [4, 16, 18, 19, 20, 21, 22])
plan = tv.build_plan(found, season=5, first_episode=1,
                     series={"id": 1603, "name": "Samurai Jack", "year": "2001"},
                     episode_list=eps)
check("named through to the end",
      [(e["episode"], e["episode_title"]) for e in plan["episodes"]][-2:],
      [(9, "Episode C"), (10, "Episode CI")])


# ── Avatar: The Last Airbender, Book One: Water, disc 1 (Blu-ray, 2026-10-13) ─

print("")
print("Avatar Book One disc 1: a logo on every episode, and a name the label can't hold")
titles, eps = load("avatar-book1-disc1-bluray.info.txt",
                   "tvmaze-555-avatar-the-last-airbender.json")
label = "Avatar_Book_1_Disc_1"
disc_name = titles[0]["disc_name"]
check("the disc's own name is read", disc_name,
      "Avatar: The Last Airbender Book One: Water Disc 1")
check("\"Book One\" is season 1, disc 1", tv.season_from_label(disc_name), (1, 1))
check("and so is the label's \"Book_1\"", tv.season_from_label(label), (1, 1))
check("the show's name comes from the disc, not the label",
      tv.series_name_from_label(disc_name), "Avatar: The Last Airbender")
found = tv.analyse(titles, MAKEMKV_MIN)
# Every "with the opening" version (00601-00608) starts with segment 1100, a one-second
# logo that is also the head of the play-all. Left in, 00607 became episode one.
check("eight episodes, the versions with the opening, in disc order",
      [t["source"] for t in found["episodes"]],
      ["00601.mpls", "00602.mpls", "00603.mpls", "00604.mpls",
       "00605.mpls", "00606.mpls", "00607.mpls", "00608.mpls"])
check("the versions without it are duplicates, not extras",
      sorted(t["source"] for t in found["duplicates"]),
      ["01601.mpls", "01610.mpls", "01617.mpls", "01618.mpls",
       "01619.mpls", "01620.mpls", "01621.mpls", "01622.mpls"])
check("ordered by the play-all, trusted", (found["order_source"], found["confidence"]),
      ("segments", "high"))
# TVmaze's search, as it answered on the day: two shows with this exact name.
options = [{"id": 38753, "name": "Avatar: The Last Airbender", "year": "2024",
            "score": 1.5997504, "runtime": 59},
           {"id": 555, "name": "Avatar: The Last Airbender", "year": "2005",
            "score": 1.5994667, "runtime": 30}]
check("22-minute episodes are the 2005 cartoon, not the hour-long remake",
      tv.pick_series(options, found["episode_seconds"])["id"], 555)
plan = tv.build_plan(found, season=1, first_episode=1,
                     series={"id": 555, "name": "Avatar: The Last Airbender",
                             "year": "2005"}, episode_list=eps)
check("named in order",
      [plan["episodes"][i]["episode_title"] for i in (0, 7)],
      ["Book One: Water - Chapter One: The Boy in the Iceberg",
       "Book One: Water - Chapter Eight: Avatar Roku (Winter Solstice, Part 2)"])


print("")
print("Avatar Book One discs 2 and 3: the same authoring, and a third copy of each episode")
for n, want, dups in ((2, ["00601.mpls", "00602.mpls", "00603.mpls", "00604.mpls",
                            "00605.mpls", "00606.mpls", "00607.mpls", "00608.mpls"], 8),
                      # Disc 3's no-opening copies also carry the commentary, as a
                      # fifth track after the normal four: the most complete copies.
                      (3, ["01601.mpls", "01602.mpls", "01603.mpls", "01604.mpls"], 8)):
    t_n, _ = load("avatar-book1-disc%d-bluray.info.txt" % n,
                  "tvmaze-555-avatar-the-last-airbender.json")
    f_n = tv.analyse(t_n, MAKEMKV_MIN)
    check("disc %d: season 1, disc %d, from the disc's own name" % (n, n),
          tv.season_from_label(t_n[0]["disc_name"]), (1, n))
    check("disc %d: the most complete copy of each episode, in order" % n,
          [x["source"] for x in f_n["episodes"]], want)
    # Disc 3 carries each episode three times: with the opening, without, and with
    # commentary (00254-00257). All copies of the episodes, none of them extras.
    check("disc %d: every other copy is a duplicate, not an extra" % n,
          len(f_n["duplicates"]), dups)

# ── Avatar Book Two: Earth, disc 1 (Blu-ray, 2026-10-13) ─────────────────────

print("")
print("Avatar Book Two disc 1: a commentary-only copy as long as the episode")
b2, _ = load("avatar-book2-disc1-bluray.info.txt", "tvmaze-555-avatar-the-last-airbender.json")
check("Book Two is season 2, disc 1", tv.season_from_label(b2[0]["disc_name"]), (2, 1))
by_src = {t["source"]: t for t in b2}
check("the parser counts audio tracks: commentary-only, normal, normal plus commentary",
      [by_src[s]["audio"] for s in ("00254.mpls", "00606.mpls", "01606.mpls")], [1, 4, 5])
f_b2 = tv.analyse(b2, MAKEMKV_MIN)
# Episode 6 is one stream file under three playlists. 00254.mpls (one track, the
# commentary) and 00606.mpls (the four normal tracks) are both 24:32, and the lower
# title number won the tie: episodes 6 and 8 would have been ripped as commentary alone.
check("no episode is a commentary-only copy",
      [t["audio"] >= 4 for t in f_b2["episodes"]], [True] * 8)
check("episodes 6 and 8 are the copies with every track, commentary last",
      [t["source"] for t in f_b2["episodes"]][5:],
      ["01606.mpls", "00607.mpls", "01608.mpls"])
# Episodes 1-5 keep the opening in a segment of its own (00601 = 1093,1094,1099) and
# 6-8 have it in the episode's file. Both shapes are one episode each.
check("eight episodes, ordered by the play-all",
      (len(f_b2["episodes"]), f_b2["play_all"]["source"], f_b2["order_source"],
       f_b2["confidence"]), (8, "00600.mpls", "segments", "high"))
check("the 24-minute bare stream outside the play-all is an extra",
      [t["source"] for t in f_b2["extras"]], ["00013.m2ts"])
plan_b2 = tv.build_plan(f_b2, season=2, first_episode=1,
                        series={"id": 555, "name": "Avatar: The Last Airbender",
                                "year": "2005"}, episode_list=eps)
check("named in order",
      [plan_b2["episodes"][i]["episode_title"] for i in (0, 7)],
      ["Book Two: Earth - Chapter One: The Avatar State",
       "Book Two: Earth - Chapter Eight: The Chase"])

# ── Avatar Book Two: Earth, disc 2 (Blu-ray, 2026-10-13) ─────────────────────

print("")
print("Avatar Book Two disc 2: two episodes welded into one stream, mid-season")
b2d2, _ = load("avatar-book2-disc2-bluray.info.txt", "tvmaze-555-avatar-the-last-airbender.json")
check("season 2, disc 2", tv.season_from_label(b2d2[0]["disc_name"]), (2, 2))
f_d2 = tv.analyse(b2d2, MAKEMKV_MIN)
# Segment 1088 is "The Serpent's Pass" and "The Drill" as one 45-minute stream, in the
# middle of the play-all. It can't be split without remuxing, so it is a double.
check("seven titles in play-all order, the 46-minute one fourth",
      [t["source"] for t in f_d2["episodes"]],
      ["00601.mpls", "00602.mpls", "00603.mpls", "00604.mpls",
       "00605.mpls", "00606.mpls", "00607.mpls"])
check("trusted", (f_d2["order_source"], f_d2["confidence"]), ("segments", "high"))
check("the commentary-only copy of episode 14 is a duplicate",
      "00250.mpls" in [t["source"] for t in f_d2["duplicates"]], True)
plan_d2 = tv.build_plan(f_d2, season=2, first_episode=9,
                        series={"id": 555, "name": "Avatar: The Last Airbender",
                                "year": "2005"}, episode_list=eps)
check("numbered E09-E16 with the double as E12-E13",
      [(r["episode"], r["episode_last"]) for r in plan_d2["episodes"]],
      [(9, 9), (10, 10), (11, 11), (12, 13), (14, 14), (15, 15), (16, 16)])
check("the double carries both names",
      plan_d2["episodes"][3]["episode_title"],
      "Book Two: Earth - Chapter Twelve: The Serpent's Pass + "
      "Book Two: Earth - Chapter Thirteen: The Drill")
check("and the warning says which episodes it holds",
      any("(E12-E13)" in w for w in tv.plan_warnings(plan_d2, f_d2, eps)), True)

# ── Avatar Book Two: Earth, disc 3 (Blu-ray, 2026-10-13) ─────────────────────

print("")
print("Avatar Book Two disc 3: the season ends on a double")
b2d3, _ = load("avatar-book2-disc3-bluray.info.txt", "tvmaze-555-avatar-the-last-airbender.json")
f_d3 = tv.analyse(b2d3, MAKEMKV_MIN)
plan_d3 = tv.build_plan(f_d3, season=2, first_episode=17,
                        series={"id": 555, "name": "Avatar: The Last Airbender",
                                "year": "2005"}, episode_list=eps)
check("E17, E18, then the finale as E19-E20",
      [(r["episode"], r["episode_last"]) for r in plan_d3["episodes"]],
      [(17, 17), (18, 18), (19, 20)])
check("the commentary-carrying copies, trusted",
      ([t["source"] for t in f_d3["episodes"]], f_d3["confidence"]),
      (["01601.mpls", "01602.mpls", "00603.mpls"], "high"))
check("the bonus streams are extras", len(f_d3["extras"]), 7)

# ── the second witness: Wikidata, as it answered on 2026-10-13 ───────────────

print("")
print("Wikidata as a second witness")
from riparr import wikidata as WD  # noqa: E402
wd = json.load(open(os.path.join(FIX, "wikidata-2026-10-13.json"), encoding="utf-8"))
facts = WD.parse_series_facts(wd["series_facts"])
check("it knows both Avatars apart: seasons and years",
      [(facts[i]["seasons"], facts[i]["year"]) for i in (555, 38753)],
      [(3, 2005), (2, 2024)])
check("and Book One's episode count", facts[555]["season_episodes"].get(1), 20)
# What its own name search found for "Avatar: The Last Airbender" (both shows).
named_avatar = [{"qid": "Q11572", "label": "Avatar: The Last Airbender", "tvmaze": [555]},
                {"qid": "Q109608844", "label": "Avatar: The Last Airbender",
                 "tvmaze": [38753]}]
check("both witnesses, tied on the name: the episode length decides",
      tv.pick_series(options, found["episode_seconds"], 1, facts, named_avatar)["id"], 555)
check("a disc of Book Three can't be the two-season remake, even with no episode length",
      tv.pick_series(options, None, 3, facts, named_avatar)["id"], 555)
other = [{"id": 999, "name": "Samurai Jack: The Movie", "score": 2.0, "runtime": 90},
         {"id": 1603, "name": "Samurai Jack", "score": 1.4, "runtime": 30}]
check("a name match Wikidata doesn't know as this show is passed over",
      tv.pick_series(other, 1350, 1, {}, [{"qid": "Q694101", "label": "Samurai Jack",
                                           "tvmaze": [1603]}])["id"], 1603)

avatar_titles = WD.parse_episode_titles(wd["avatar_s1"])
verdict = tv.cross_check(plan["episodes"], eps, avatar_titles)
check("Avatar: the two sources agree on all eight names",
      (verdict["verdict"], verdict["agree"]), ("agree", 8))
v_b2 = tv.cross_check(plan_b2["episodes"], eps, WD.parse_episode_titles(wd["avatar_s2"]))
check("and on Book Two's first eight",
      (v_b2["verdict"], v_b2["agree"]), ("agree", 8))
v_d2 = tv.cross_check(plan_d2["episodes"], eps, WD.parse_episode_titles(wd["avatar_s2"]))
check("and on disc 2's seven files", (v_d2["verdict"], v_d2["agree"]), ("agree", 7))

sj_titles = WD.parse_episode_titles(wd["samurai_jack_s1"])
sj_titles_by_n = dict(sj_titles)
check("Samurai Jack's own house style is \"Episode I\"", sj_titles_by_n[1], "Episode I")
sj_titles_eps = json.load(open(os.path.join(FIX, "tvmaze-1603-samurai-jack.json")))
sj_eps = [{"season": e["season"], "number": e["number"], "name": e.get("name") or "",
           "special": (e.get("type") or "regular") != "regular"}
          for e in sj_titles_eps if e.get("season") is not None and e.get("number")]
sj_rows = [{"episode": n, "season": 1, "include": True,
            "episode_title": next(e["name"] for e in sj_eps
                                  if e["season"] == 1 and e["number"] == n)}
           for n in range(1, 14)]
v2 = tv.cross_check(sj_rows, sj_eps, sj_titles)
check("different naming is not disagreement: agreement where both have a name",
      (v2["verdict"], v2["agree"] >= 1), ("agree", True))

# One source counting a special as episode 2 shifts every name after it by one.
shifted_eps = [{"season": 1, "number": 1, "name": "The Boy in the Iceberg", "special": False},
               {"season": 1, "number": 2, "name": "Unaired Pilot", "special": False}] + [
    {"season": 1, "number": n + 1, "name": avatar_titles[n], "special": False}
    for n in range(2, 9)]
shifted_rows = [{"episode": n, "season": 1, "include": True,
                 "episode_title": next(e["name"] for e in shifted_eps if e["number"] == n)}
                for n in range(1, 9)]
v3 = tv.cross_check(shifted_rows, shifted_eps, avatar_titles)
check("a season the two number differently is caught, and by how much",
      (v3["verdict"], v3["offset"]), ("shifted", 1))
blank = [{"episode": 1, "season": 1, "include": True, "episode_title": ""}]
tv.cross_check(blank, [], avatar_titles)
check("a name TVmaze lacks is taken from the witness", blank[0]["episode_title"],
      "The Boy in the Iceberg")

# ── Kiki's Delivery Service (Blu-ray, 2026-11-14) ────────────────────────────
# Title list as the box parsed it (no raw scan was kept). The film, 00800.mpls, is
# seamless-branched from 01001/01003/01005.m2ts, which MakeMKV also lists bare. It
# used to be taken for a play-all of its parts, leaving three 7-9 minute bonus pieces
# filed as a three-episode season.

print("Kiki's Delivery Service: a film with seamless branching is not a season")
titles = json.load(open(os.path.join(FIX, "kikis-delivery-service-bluray.titles.json"),
                        encoding="utf-8"))
check("not read as a season", tv.analyse(titles, MAKEMKV_MIN), None)
check("the film is the 105-minute, six-audio-track playlist",
      rip.choose_title(titles, MAKEMKV_MIN)["source"], "00800.mpls")

print("")
if failures:
    print("%d FAILED: %s" % (len(failures), ", ".join(failures)))
    sys.exit(1)
print("all good")
