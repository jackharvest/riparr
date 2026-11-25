"""Which audio and subtitle tracks go in the MKV.

MakeMKV chooses tracks from a "selection string", a small rule language it reads from
`app_DefaultSelectionString` in settings.conf. `makemkvcon mkv` obeys it -- checked on a
real Blu-ray, 62 tracks by default and 11 with English only.

Three things it does that decide the shape of this module, all seen on that disc:

* **Ask for a language the disc doesn't have and it selects nothing.** `+sel:(audio&jpn)`
  on a disc with no Japanese is a silent film, with no error. So the rules are built from
  the tracks the title really has, and a title with none of the wanted languages keeps
  all of its audio.
* **A language code it doesn't know is an error for that rule,** printed once per track.
  Building from the disc's own codes avoids that too.
* **Nothing chosen means everything.** No selection string is what every rip did before
  these settings worked, and is what the defaults still do.
"""
import re

ALL = "all"
NONE = "none"

# One language has several codes and discs don't agree on which: MakeMKV reports French
# as "fra" on a Blu-ray and "fre" on a DVD. People type "fr" or "French". All of them mean
# the same thing, so both sides go through this before they are compared, and the rules
# are written in the disc's own spelling. Codes not listed pass through as typed, if they
# look like a code.
_NAMES = {
    "ara": "ar arabic", "bul": "bg bulgarian", "cat": "ca catalan",
    "ces": "cs cze czech", "dan": "da danish", "deu": "de ger german deutsch",
    "ell": "el gre greek", "eng": "en english", "est": "et estonian",
    "eus": "eu baq basque", "fas": "fa per persian farsi", "fin": "fi finnish",
    "fra": "fr fre french français francais", "heb": "he hebrew", "hin": "hi hindi",
    "hrv": "hr croatian", "hun": "hu hungarian", "hye": "hy arm armenian",
    "ind": "id indonesian", "isl": "is ice icelandic", "ita": "it italian italiano",
    "jpn": "ja jp japanese", "kat": "ka geo georgian", "kor": "ko korean",
    "lav": "lv latvian", "lit": "lt lithuanian", "mkd": "mk mac macedonian",
    "msa": "ms may malay", "nld": "nl dut dutch nederlands", "nor": "no nb norwegian",
    "pol": "pl polish polski", "por": "pt portuguese português", "ron": "ro rum romanian",
    "rus": "ru russian", "slk": "sk slo slovak", "slv": "sl slovenian",
    "spa": "es spanish español espanol", "sqi": "sq alb albanian", "srp": "sr serbian",
    "swe": "sv swedish svenska", "tha": "th thai", "tur": "tr turkish",
    "ukr": "uk ukrainian", "vie": "vi vietnamese", "zho": "zh chi chinese",
}
_LOOKUP = {}
for _code, _words in _NAMES.items():
    for _w in [_code] + _words.split():
        _LOOKUP[_w] = _code

# Stream attribute 22 in robot output, MakeMKV's AP_AVStreamFlag: director's comments,
# and an alternate set of them. Many discs mark neither: a real DVD with a commentary
# track (English stereo beside English 5.1) had nothing to say so.
_COMMENTARY = 1 | 2
_FORCED = 4096

_SINFO = re.compile(r'^SINFO:(\d+),(\d+),(\d+),(\d+),"(.*)"\s*$')
_KIND = {"6201": "video", "6202": "audio", "6203": "subtitle"}


def code(word):
    """One language as typed -> the code a disc uses, or None if it isn't one."""
    w = (word or "").strip().lower()
    if not w:
        return None
    if w in _LOOKUP:
        return _LOOKUP[w]
    return w if re.fullmatch(r"[a-z]{3}", w) else None


def wanted(value):
    """A language setting -> (codes, nothing). `None` for codes means every language.

    An empty box and the word "all" keep every language; "none" keeps none of them.
    """
    if isinstance(value, str):
        value = re.split(r"[,;\s]+", value)
    words = [str(v).strip().lower() for v in value or [] if str(v).strip()]
    if NONE in words:
        return [], True
    if not words or ALL in words:
        return None, False
    out = []
    for w in words:
        c = code(w)
        if c and c not in out:
            out.append(c)
    return (out or None), False


def parse(text):
    """{title index: {"audio": [...], "subtitle": [...]}} from `makemkvcon -r info`.

    Each track is [language, flags]: enough to choose from, and small enough to ride
    along on the title list, which is stored with the job.
    """
    raw = {}
    for line in (text or "").splitlines():
        m = _SINFO.match(line)
        if not m:
            continue
        t, s, attr, num, value = m.groups()
        d = raw.setdefault((int(t), int(s)), {"kind": None, "lang": "", "flags": 0})
        if attr == "1":
            d["kind"] = _KIND.get(num)
        elif attr == "3":
            d["lang"] = value
        elif attr == "22":
            try:
                d["flags"] = int(value)
            except ValueError:
                pass
    out = {}
    for (t, _s), d in sorted(raw.items()):
        row = out.setdefault(t, {"audio": [], "subtitle": []})
        if d["kind"] in row:
            row[d["kind"]].append([d["lang"], d["flags"]])
    return out


def _langs(codes):
    return "(%s)" % "|".join(codes)


def _picker(langs):
    """Selection terms for these languages. "" is a track with no language, which
    MakeMKV calls `nolang`."""
    return _langs([l or "nolang" for l in sorted(langs)])


def _is(lang, codes):
    """Whether a disc's language is one of the wanted codes, whichever spelling it uses."""
    return (code(lang) or lang) in codes


def selection(tracks, settings):
    """(selection string, sentence for the log), or ("", "") to leave MakeMKV alone.

    `tracks` is one title's entry from `parse`.
    """
    audio = (tracks or {}).get("audio") or []
    subs = (tracks or {}).get("subtitle") or []
    if not audio and not subs:
        return "", ""

    want_a, _ = wanted(settings.get("audio_languages"))       # "none" is not offered
    want_s, no_subs = wanted(settings.get("subtitle_languages"))
    keep_forced = settings.get("keep_forced_subtitles", True)
    keep_comm = settings.get("keep_commentary", True)
    if want_a is None and want_s is None and not no_subs and keep_forced and keep_comm:
        return "", ""

    rules, said = ["-sel:all", "+sel:video"], []

    # Audio. A track with no language is kept beside the wanted ones: it is usually the
    # only soundtrack of an old or home-made disc, and dropping it on a guess is not.
    have = {l for l, _ in audio}
    pick = have if want_a is None else {l for l in have if _is(l, want_a) or l == ""}
    if audio and not any(l in pick for l, _ in audio):
        pick = have
        said.append("none of the audio is in a language you asked for, so all of it is kept")
    if want_a is not None and not any(_is(l, want_a) for l in pick) and pick != have:
        # Only unlabelled tracks matched; a labelled one may be the real soundtrack.
        pick = have
    if audio:
        rules.append("+sel:audio" if pick == have
                     else "+sel:(audio&%s)" % _picker(pick))
    comm = [t for t in audio if t[0] in pick and t[1] & _COMMENTARY]
    if not keep_comm and comm:
        if len(comm) < len([t for t in audio if t[0] in pick]):
            rules.append("-sel:(audio&special)")
            said.append("%d commentary track%s left out"
                        % (len(comm), "" if len(comm) == 1 else "s"))

    # Subtitles.
    shave = {l for l, _ in subs}
    if no_subs:
        spick = set()
    elif want_s is None:
        spick = shave
    else:
        spick = {l for l in shave if _is(l, want_s)}
    if spick:
        rules.append("+sel:subtitle" if spick == shave
                     else "+sel:(subtitle&%s)" % _picker(spick))
    has_forced = {l for l, f in subs if f & _FORCED}
    if not keep_forced:
        if has_forced & spick:
            rules.append("-sel:(subtitle&forced)")
    else:
        # The subtitles for a film's foreign dialogue, in whichever language you watch
        # it in -- which is the audio's, even with subtitles off altogether.
        extra = {l for l in has_forced - spick if _is(l, want_a or [])}
        if extra:
            rules.append("+sel:(subtitle&forced&%s)" % _picker(extra))

    out = ",".join(rules)
    everything = ["-sel:all", "+sel:video"] + (["+sel:audio"] if audio else []) \
        + (["+sel:subtitle"] if subs else [])
    if rules == everything:
        return "", "; ".join(said)
    return out, "; ".join(said)
