#!/usr/bin/env python3
"""Which title is the film, and when does Riparr stop to ask.

This exists because a 3D Blu-ray walked into `choose_title` and came out wrong. The
2D and 3D cuts of one film are the same length, so longest-wins picks the 3D one --
roughly twice the size, and most players will not use it.

The first attempt at a fix broke something else: it took the *smallest* title of any
runtime tie, which on an obfuscated disc means picking a decoy playlist over the real
film. That regression is the reason this file is a test and not a scratch script, and
`obfuscated_decoys_still_pick_longest` is the case that caught it.

Run: python3 server/choose-title.test.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from riparr.rip import (choose_title, looks_obfuscated, _looks_like_3d,  # noqa: E402
                        _identify_question)

GB = 2 ** 30
FLOOR = 120                       # the shipped min_title_seconds

failures = []


def check(name, got, want):
    if got == want:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s: got %r, wanted %r" % (name, got, want))
        failures.append(name)


def title(index, minutes, gb):
    return {"index": index, "seconds": int(minutes * 60), "bytes": int(gb * GB),
            "name": "", "file": ""}


# A 3D Blu-ray. Both cuts run 95 minutes; the MVC one is a bit over twice the size.
# Index 2 is a second 2D playlist, which real discs do carry.
THREE_D = [title(0, 95, 28.4), title(1, 95, 12.1), title(2, 94.97, 11.9),
           title(5, 4, 0.3)]

# An obfuscated disc: many long decoys, seconds apart, all within a rounding error of
# each other's size. This is what the size tiebreak must NOT fire on.
DECOYS = [{"index": i, "seconds": 6000 - i, "bytes": 24 * GB - i * 1000,
           "name": "", "file": ""} for i in range(8)]

# An ordinary disc: one main title, a trailer, a menu loop.
PLAIN = [title(0, 131, 7.8), title(1, 2.2, 0.4), title(2, 1, 0.09)]

# MakeMKV reporting no sizes at all. Must not divide by zero, must still choose.
NO_SIZES = [{"index": 0, "seconds": 7860, "bytes": 0, "name": "", "file": ""},
            {"index": 1, "seconds": 7860, "bytes": 0, "name": "", "file": ""}]


print("3D disc picks the 2D cut")
check("default picks 2D, and the longest of the two 2D playlists",
      choose_title(THREE_D, FLOOR)["index"], 1)
check("title_3d=3d picks the MVC one",
      choose_title(THREE_D, FLOOR, prefer_3d=True)["index"], 0)
check("recognised as a 2D/3D pair", _looks_like_3d(THREE_D, FLOOR), True)

print("obfuscated decoys still pick longest")
check("longest wins, as it did before the tiebreak existed",
      choose_title(DECOYS, FLOOR)["index"], 0)
check("and 3D preference changes nothing here",
      choose_title(DECOYS, FLOOR, prefer_3d=True)["index"], 0)
check("not mistaken for a 3D pair", _looks_like_3d(DECOYS, FLOOR), False)
check("still flagged ambiguous", looks_obfuscated(DECOYS, FLOOR), True)

print("ordinary disc is untouched")
check("main title wins", choose_title(PLAIN, FLOOR)["index"], 0)
check("not ambiguous", looks_obfuscated(PLAIN, FLOOR), False)
check("not a 3D pair", _looks_like_3d(PLAIN, FLOOR), False)

print("degenerate input")
check("no sizes reported: picks the lowest index, does not divide by zero",
      choose_title(NO_SIZES, FLOOR)["index"], 0)
check("everything under the floor: falls back rather than returning None",
      choose_title([title(3, 0.5, 0.1)], FLOOR)["index"], 3)
check("no titles at all", choose_title([], FLOOR), None)

print("the prompt says which thing is unclear")
check("an unnamed disc asks about the name",
      _identify_question(True, THREE_D, FLOOR),
      "Riparr couldn't work out what this disc is.")
check("a 3D disc is not accused of hiding anything",
      "2D and the 3D cut" in _identify_question(False, THREE_D, FLOOR), True)
check("an obfuscated disc still is",
      "hiding the real one" in _identify_question(False, DECOYS, FLOOR), True)

print()
if failures:
    print("%d check(s) failed: %s" % (len(failures), ", ".join(failures)))
    sys.exit(1)
print("All checks passed.")
