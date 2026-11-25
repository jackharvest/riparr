"""Track selection (#27): settings in, MakeMKV selection string out.

Run: python3 server/tracks.test.py

The rules were checked against MakeMKV 2.0.0 and a real Blu-ray; what is asserted here is
that Riparr writes those rules, and above all that it never writes one that leaves a film
with no sound.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["RIPARR_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")

from riparr import tracks as T                                   # noqa: E402

bad = []


def check(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL ") + name + ("" if ok else "  got %r want %r" % (got, want)))
    if not ok:
        bad.append(name)


KEEP = {"audio_languages": [], "subtitle_languages": [], "keep_forced_subtitles": True,
        "keep_commentary": True}


def sel(tracks, **kw):
    return T.selection(tracks, dict(KEEP, **kw))[0]


BD = {"audio": [["eng", 1024], ["eng", 2304], ["fra", 0], ["ita", 1024], ["spa", 0]],
      "subtitle": [["eng", 0], ["eng", 6144], ["fra", 0], ["fra", 6144], ["zho", 0]]}

print("languages as people type them")
for w, c in (("eng", "eng"), ("en", "eng"), ("English", "eng"), ("ger", "deu"),
             ("de", "deu"), ("Deutsch", "deu"), ("fre", "fra"), ("chi", "zho"),
             ("zzz", "zzz"), ("klingon", None), ("", None)):
    check(repr(w), T.code(w), c)

print("the defaults change nothing")
check("no selection string", sel(BD), "")
check("a title with no track list", sel(None, audio_languages=["eng"]), "")

print("audio")
check("one language", sel(BD, audio_languages=["eng"]),
      "-sel:all,+sel:video,+sel:(audio&(eng)),+sel:subtitle")
check("typed another way", sel(BD, audio_languages=["English"]),
      sel(BD, audio_languages=["eng"]))
check("several", sel(BD, audio_languages=["fre", "eng"]),
      "-sel:all,+sel:video,+sel:(audio&(eng|fra)),+sel:subtitle")
check("ONE THE DISC DOESN'T HAVE KEEPS ALL THE AUDIO", sel(BD, audio_languages=["jpn"]), "")
check("half there", sel(BD, audio_languages=["jpn", "ita"]),
      "-sel:all,+sel:video,+sel:(audio&(ita)),+sel:subtitle")
check("only nonsense typed keeps everything", sel(BD, audio_languages=["klingon"]), "")
UN = {"audio": [["", 0], ["eng", 0], ["fra", 0]], "subtitle": []}
check("an unlabelled track stays with a wanted one", sel(UN, audio_languages=["eng"]),
      "-sel:all,+sel:video,+sel:(audio&(nolang|eng))")
check("but doesn't stand in for one", sel(UN, audio_languages=["jpn"]), "")
check("'none' can't silence a film", sel(BD, audio_languages=["none"]), "")

print("one language, two spellings")
DVDFR = {"audio": [["eng", 0], ["fre", 0]], "subtitle": [["eng", 0], ["fre", 0], ["ger", 0]]}
check("a DVD's French is French", sel(DVDFR, audio_languages=["French"]),
      "-sel:all,+sel:video,+sel:(audio&(fre)),+sel:subtitle")
check("subtitles too, however it's typed",
      sel(DVDFR, subtitle_languages=["fra", "de"]),
      "-sel:all,+sel:video,+sel:audio,+sel:(subtitle&(fre|ger))")

print("commentary")
DVD = {"audio": [["eng", 0], ["eng", 1], ["fra", 0]], "subtitle": [["eng", 0]]}
check("dropped when marked", sel(DVD, keep_commentary=False),
      "-sel:all,+sel:video,+sel:audio,-sel:(audio&special),+sel:subtitle")
check("nothing marked, nothing written", sel(BD, keep_commentary=False), "")
ONLY = {"audio": [["eng", 1]], "subtitle": []}
check("kept when it is the only audio", sel(ONLY, keep_commentary=False), "")
MIX = {"audio": [["eng", 1], ["fra", 0]], "subtitle": []}
check("kept when it is the only audio in your language",
      sel(MIX, keep_commentary=False, audio_languages=["eng"]),
      "-sel:all,+sel:video,+sel:(audio&(eng))")

print("subtitles")
check("one language", sel(BD, subtitle_languages=["fr"]),
      "-sel:all,+sel:video,+sel:audio,+sel:(subtitle&(fra))")
check("one the disc lacks means none", sel(BD, subtitle_languages=["jpn"]),
      "-sel:all,+sel:video,+sel:audio")
check("none, but the forced ones for your audio",
      sel(BD, subtitle_languages=["none"], audio_languages=["eng"]),
      "-sel:all,+sel:video,+sel:(audio&(eng)),+sel:(subtitle&forced&(eng))")
check("none at all", sel(BD, subtitle_languages=["none"], keep_forced_subtitles=False),
      "-sel:all,+sel:video,+sel:audio")
check("forced off", sel(BD, keep_forced_subtitles=False),
      "-sel:all,+sel:video,+sel:audio,+sel:subtitle,-sel:(subtitle&forced)")

print("every combination leaves some audio")
import itertools                                                 # noqa: E402
for disc in (BD, DVD, UN, ONLY, MIX):
    for a, s, f, c in itertools.product(
            (["eng"], ["jpn"], ["none"], ["fra", "jpn"], []),
            (["eng"], ["none"], ["jpn"], []), (True, False), (True, False)):
        r = sel(disc, audio_languages=a, subtitle_languages=s,
                keep_forced_subtitles=f, keep_commentary=c)
        if r and "+sel:audio" not in r and "+sel:(audio&" not in r:
            check("audio with %r" % ((disc, a, s, f, c),), r, "some audio")

print("reading a scan")
SCAN = '''SINFO:1,0,1,6201,"Video"
SINFO:1,0,22,0,"0"
SINFO:1,1,1,6202,"Audio"
SINFO:1,1,3,0,"eng"
SINFO:1,1,22,0,"1024"
SINFO:1,2,1,6203,"Subtitles"
SINFO:1,2,3,0,"fra"
SINFO:1,2,22,0,"6144"
'''
check("languages and flags", T.parse(SCAN),
      {1: {"audio": [["eng", 1024]], "subtitle": [["fra", 6144]]}})

print("settings saved before they did anything")
from riparr import db                                            # noqa: E402
db.init()
for k, v in (("audio_languages", ["eng"]), ("subtitle_languages", ["eng"]),
             ("keep_commentary", False)):
    db.set(k, v)
db.conn().execute("DELETE FROM settings WHERE key='tracks_live'")
db.conn().commit()
db.init()
check("the old defaults, saved by the page, don't start dropping tracks",
      (db.get("audio_languages"), db.get("subtitle_languages"), db.get("keep_commentary")),
      ([], [], True))
db.set("audio_languages", ["eng"])
db.init()
check("and English chosen after that stays chosen", db.get("audio_languages"), ["eng"])
db.conn().execute("DELETE FROM settings WHERE key='tracks_live'")
db.set("audio_languages", ["deu", "eng"])
db.init()
check("a list somebody typed is kept", db.get("audio_languages"), ["deu", "eng"])

print("settings.conf")
from riparr import makemkv as MK                                 # noqa: E402
d = tempfile.mkdtemp()
MK.SETTINGS_CONF = os.path.join(d, "settings.conf")
open(MK.SETTINGS_CONF, "w").write('app_Key = "T-x"\napp_UpdateEnabled = "0"\n')
MK.set_selection("-sel:all,+sel:video")
check("written beside the key", open(MK.SETTINGS_CONF).read(),
      'app_Key = "T-x"\napp_UpdateEnabled = "0"\n'
      'app_DefaultSelectionString = "-sel:all,+sel:video"\n')
MK.set_selection("-sel:all")
check("replaced, not repeated", open(MK.SETTINGS_CONF).read().count("Selection"), 1)
MK.set_selection("")
check("and taken out again", open(MK.SETTINGS_CONF).read(),
      'app_Key = "T-x"\napp_UpdateEnabled = "0"\n')

print()
if bad:
    print("%d failed" % len(bad))
    sys.exit(1)
print("all good")
