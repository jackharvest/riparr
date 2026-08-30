"""Television discs: is this a season disc, and which title is which episode.

A film disc asks one question -- *which title is the film* -- and `rip.choose_title`
answers it with longest-wins. A season disc asks a harder one, because every candidate
is the same length as every other candidate and the runtime tells you nothing. Six
titles of forty-two minutes is not an ambiguity to be resolved, it is six episodes, and
the job is to put them in the right order and give them the right numbers.

Four things on a real disc break the naive version of this, and all four are on the
MakeMKV forums with people working around them by hand:

**Title order is not episode order.** MakeMKV renumbers titles during its analysis pass,
so title 0 is routinely episode 4. Sorting by index produces a season in scrambled
order, which is the single most common complaint about ripping box sets.

**There is usually a "play all" title.** It is every episode end to end, so it is the
longest title on the disc and longest-wins picks it every time -- which is how a season
disc silently becomes one four-hour file. It has to be found and excluded. It is also,
happily, the best thing on the disc: see `_order_by_segments`.

**Episodes appear twice.** Discs are commonly authored with two playlists per episode --
one with the "next time on" trailer and recap, one without, for the play-all path. Six
episodes therefore present as twelve titles, and ripping all of them gives you every
episode twice at slightly different lengths.

**The disc's order is not the broadcast order.** This one cannot be solved here at all,
only respected. Firefly is the standard example: the disc opens with "Serenity", TVmaze
and TVDB both number "The Train Job" as S01E01 because that is what aired first. So
this module takes the *sequence* from the disc and only the *names* from the metadata,
and never lets the metadata reorder anything. Where they disagree, the disc wins and the
user gets one control to shift the mapping. See `build_plan`.

The ordering itself comes from the disc, in descending order of how much it can be
trusted -- `segments`, then `source`, then `index`. Each is a real technique people use
by hand; `ORDER_TRUST` records which one answered so the interface can say how sure it
is rather than presenting a guess as a fact.

Metadata is TVmaze, for the same reason `artwork.py` uses Wikipedia rather than TMDB:
no API key. A box that needs its owner to register for a developer account before it can
name an episode is a box that does not work out of the box. TVmaze is CC BY-SA and asks
to be credited, which the interface does.
"""
import json
import logging
import os
import re
import threading
import time
import urllib.parse
import urllib.request

log = logging.getLogger("riparr.tv")

UA = "riparr/0.1 (+https://github.com/jackharvest/riparr) python-urllib"
API = "https://api.tvmaze.com"

# ── what counts as an episode ────────────────────────────────────────────────

# Nothing shorter than this is ever an episode. A ten-minute featurette is common on a
# season disc and a ten-minute episode essentially is not, outside of shorts nobody
# presses to Blu-ray. Deliberately below the 22 minutes of a half-hour sitcom so that
# the *clustering* decides what an episode is, not this number.
EPISODE_MIN_SECONDS = 900

# Two titles this far apart in runtime are still the same show. Episodes of one season
# vary by a minute or two either side; the tolerance has to cover that without being so
# wide it swallows a 20-minute featurette into a cluster of 42-minute episodes.
CLUSTER_TOLERANCE = 0.18

# A cluster smaller than this is not a season disc. Two is deliberate rather than three:
# the last disc of a season very often carries exactly two episodes, and refusing to
# recognise it would break the tail of every box set.
MIN_EPISODES = 2

# A title this many times the episode length is the play-all, not an episode.
PLAY_ALL_RATIO = 2.5

# Past this, one title is not one film. See `looks_like_blob`.
BLOB_MIN_SECONDS = 12600

# ...unless it is almost exactly twice, in which case it is a double-length episode --
# a premiere or a finale, which air as one file and are named S01E01-E02. The window is
# tight because a genuine two-parter is 2x within a couple of minutes, where a play-all
# on a six-episode disc is 6x.
DOUBLE_LOW, DOUBLE_HIGH = 1.82, 2.18

# How much the interface should trust the answer, by where the order came from.
ORDER_TRUST = {
    # The play-all's segment map *is* the disc's own ordered list of its episodes.
    # There is no better answer available and no reason to ask.
    "segments": "high",
    # .mpls filenames sort into episode order on almost every Blu-ray. "Almost" is
    # doing real work -- Firefly and Vikings pressings are documented exceptions -- so
    # this is worth showing the user before it is committed to.
    "source": "medium",
    # MakeMKV's title order, which is not episode order and is not claimed to be.
    "index": "low",
}


def parse_segments(value):
    """MakeMKV's segment map into a list of ints.

    The attribute is a comma-separated list, sometimes with ranges ("3-7"), sometimes
    empty, and on a DVD sometimes absent entirely. Anything unparseable is dropped
    rather than raising: a missing segment map costs us the best ordering signal, which
    is a downgrade to the next one, not a failure.
    """
    out = []
    for chunk in (value or "").replace(" ", "").split(","):
        if not chunk:
            continue
        m = re.match(r"^(\d+)-(\d+)$", chunk)
        if m:
            lo, hi = int(m.group(1)), int(m.group(2))
            if hi - lo <= 1000:
                out.extend(range(lo, hi + 1))
            continue
        if chunk.isdigit():
            out.append(int(chunk))
    return out


def _source_number(title):
    """The numeric part of a source filename, for ordering. None if there isn't one.

    `00800.mpls` -> 800. On a DVD the source is a VTS reference rather than a playlist
    and the number means something different, so this is only trusted where every
    candidate has one and they are distinct -- see `_order_by_source`.
    """
    m = re.search(r"(\d+)", os.path.basename(title.get("source") or ""))
    return int(m.group(1)) if m else None


# ── finding the episodes ─────────────────────────────────────────────────────

def _median(values):
    v = sorted(values)
    if not v:
        return 0
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2.0


def _dedup_signal(titles):
    """Whether the disc told us enough to recognise a duplicate playlist at all."""
    return any(t.get("segments") or t.get("source") for t in titles)


def _dedup(candidates):
    """Collapse the two playlists per episode into one, keeping the longer.

    Two titles are the same episode when their segment maps overlap at all: an episode's
    video lives in one set of stream segments, and the with-trailer and without-trailer
    playlists are two wrappers around that same footage. Overlap rather than equality,
    because the wrappers differ by exactly the segments that make them different.

    The longer one is kept because it is the complete broadcast episode -- recap, titles
    and trailer included. That is a judgement call and it is the one a person makes by
    hand for the same reason: the shorter cut exists to make the play-all flow smoothly,
    not to be watched on its own.

    Where there is no segment map, two titles built from the *same source file* are the
    same content, which is the same argument one level coarser and is what a DVD gives
    you when it gives you nothing else.

    Where there is neither, **nothing is merged.** Runtime and size were the obvious
    third fallback and they are not usable: two playlists of one episode differ by the
    twenty seconds of trailer between them, and two different episodes of one series
    differ by about the same amount. There is no threshold that separates those, so a
    runtime-based merge is a coin toss on every disc that reaches it -- and the two ways
    it can land are not equally bad. Ripping an episode twice is visible in the plan
    before it runs and costs one unticked box. Merging two distinct episodes deletes one
    of them from the season silently, and the user finds out much later, with the disc
    back in its box. So this errs towards duplicates, and `plan_warnings` says it did.
    """
    if not _dedup_signal(candidates):
        return list(candidates)
    kept = []
    for t in sorted(candidates, key=lambda x: (-x["seconds"], x["index"])):
        segs = set(parse_segments(t.get("segments")))
        src = (t.get("source") or "").strip().lower()
        dup = False
        for k in kept:
            ksegs = set(parse_segments(k.get("segments")))
            if segs and ksegs:
                if segs & ksegs:
                    dup = True
                    break
            elif src and src == (k.get("source") or "").strip().lower():
                dup = True
                break
        if not dup:
            kept.append(t)
    return kept


def _largest_cluster(titles):
    """The biggest group of titles that are all about the same length.

    That group is the episodes. Everything on a season disc that is not an episode --
    featurettes, a gag reel, the play-all -- is either much shorter or much longer, and
    crucially there are never many of them at one identical length. So "the largest set
    of similar runtimes" is a better episode detector than any absolute threshold, and
    it works the same for a 22-minute sitcom and a 55-minute drama without being told
    which it is looking at.
    """
    best = []
    for anchor in titles:
        lo = anchor["seconds"] * (1 - CLUSTER_TOLERANCE)
        hi = anchor["seconds"] * (1 + CLUSTER_TOLERANCE)
        group = [t for t in titles if lo <= t["seconds"] <= hi]
        # Bigger cluster wins; a tie goes to the longer runtime, because a disc with
        # three 42-minute episodes and three 8-minute featurettes should resolve to the
        # episodes. (Featurettes rarely cluster this tightly, but ties are cheap to
        # break correctly and expensive to get wrong.)
        if (len(group), anchor["seconds"]) > (len(best), best[0]["seconds"] if best else 0):
            best = group
    return best


def _find_play_all(titles, episode_seconds):
    """The title that is every episode end to end, or None.

    Two independent tests, because the two facts it should satisfy are independent and
    a disc that fails one can still be read by the other:

    * it is much longer than one episode (`PLAY_ALL_RATIO`), and
    * its segment map contains the segments of several other titles.

    The segment test is the strong one and it also produces the ordering, so a title
    that passes only the length test is still returned but will not be trusted to order
    anything -- `_order_by_segments` checks the map itself rather than assuming.
    """
    if not episode_seconds:
        return None
    best, best_hits = None, 0
    for t in titles:
        if t["seconds"] < episode_seconds * PLAY_ALL_RATIO:
            continue
        segs = set(parse_segments(t.get("segments")))
        hits = 0
        if segs:
            for other in titles:
                if other["index"] == t["index"]:
                    continue
                osegs = set(parse_segments(other.get("segments")))
                if osegs and osegs <= segs:
                    hits += 1
        # Longest wins among candidates; segment evidence breaks a tie.
        if (hits, t["seconds"]) > (best_hits, best["seconds"] if best else 0):
            best, best_hits = t, hits
    return best


def _order_by_segments(episodes, play_all):
    """Order the episodes the way the play-all plays them, or None if it can't say.

    This is the one authoritative answer a disc gives about its own episode order. The
    play-all is a playlist over the same stream segments the individual episodes use,
    in broadcast order -- so if its map reads 8,9,10,21 then the title built from
    segment 8 is the first episode and the title built from 21 is the fourth. It is
    right even on the discs where .mpls numbering lies, and it costs nothing to read.

    Returns None unless every episode is actually locatable in the play-all's map.
    A partial answer here would be worse than no answer, because it would look like the
    good one: half a season in the right order and half appended arbitrarily is harder
    to notice, and harder to fix, than an order that is obviously unsorted.
    """
    if not play_all:
        return None
    order = parse_segments(play_all.get("segments"))
    if not order:
        return None
    position = {seg: i for i, seg in enumerate(order)}
    placed = []
    for t in episodes:
        segs = parse_segments(t.get("segments"))
        spots = [position[s] for s in segs if s in position]
        if not spots:
            return None
        placed.append((min(spots), t))
    if len({p for p, _ in placed}) != len(placed):
        # Two episodes claiming the same slot means the map is not what we think it is.
        return None
    return [t for _, t in sorted(placed, key=lambda x: x[0])]


def _order_by_source(episodes):
    """Order by .mpls number, or None if the filenames don't support it.

    Requires every episode to have a number and all of them to be distinct. A disc where
    two episodes share a source filename is telling us the number means something other
    than what we are about to assume.
    """
    numbers = [_source_number(t) for t in episodes]
    if any(n is None for n in numbers) or len(set(numbers)) != len(numbers):
        return None
    return [t for _, t in sorted(zip(numbers, episodes), key=lambda x: x[0])]


def analyse(titles, min_seconds=120):
    """Read a title list as a television season disc. None if it isn't one.

    The return is everything the rest of the system needs to show the user what it found
    and let them correct it: the episodes in order, what was excluded and why, where the
    order came from and how much that is worth trusting.
    """
    if not titles:
        return None

    floor = max(int(min_seconds or 0), EPISODE_MIN_SECONDS)
    candidates = [t for t in titles if t.get("seconds", 0) >= floor]
    if len(candidates) < MIN_EPISODES:
        return None

    # Order matters here and it is not the obvious order. Deduplicating first looks
    # right and is wrong: the play-all's segment map contains every episode's segments,
    # so it overlaps all of them, and as the longest title it survives the merge and
    # eats the entire season. Six episodes become one four-hour "episode" and the
    # detector reports a tidy, confident, completely wrong answer.
    #
    # So the play-all is identified and removed *before* anything is merged. Clustering
    # to find its yardstick is safe to do on the raw list, because duplicates cluster
    # with the episodes they duplicate -- they make that cluster larger and its median
    # no less correct.
    rough = _largest_cluster(candidates)
    if len(rough) < MIN_EPISODES:
        return None
    play_all = _find_play_all(candidates, _median([t["seconds"] for t in rough]))

    remaining = [t for t in candidates
                 if not play_all or t["index"] != play_all["index"]]
    unique = _dedup(remaining)
    cluster = _largest_cluster(unique)
    if len(cluster) < MIN_EPISODES:
        return None
    episode_seconds = _median([t["seconds"] for t in cluster])

    # A title at twice the episode length, sitting alongside a normal cluster, is a
    # double-length premiere or finale rather than a play-all. Fold it back in; it gets
    # numbered as a two-episode file further down.
    doubles = [t for t in unique
               if t["index"] != (play_all or {}).get("index")
               and t not in cluster
               and DOUBLE_LOW <= t["seconds"] / float(episode_seconds) <= DOUBLE_HIGH]
    episodes = sorted(cluster + doubles, key=lambda t: t["index"])

    ordered = _order_by_segments(episodes, play_all)
    order_source = "segments"
    if ordered is None:
        ordered = _order_by_source(episodes)
        order_source = "source"
    if ordered is None:
        ordered = sorted(episodes, key=lambda t: t["index"])
        order_source = "index"

    chosen = {t["index"] for t in ordered}
    if play_all:
        chosen.add(play_all["index"])
    # Extras are listed at the user's own floor, not the episode floor: a ten-minute
    # featurette is exactly the thing somebody wants to see was found and skipped.
    extras = [t for t in titles
              if t["index"] not in chosen
              and t.get("seconds", 0) >= max(int(min_seconds or 0), 120)]

    return {
        "episodes": ordered,
        "play_all": play_all,
        "extras": extras,
        "duplicates": [t for t in remaining if t not in unique],
        "dedup_signal": _dedup_signal(remaining),
        "doubles": [t["index"] for t in doubles],
        "order_source": order_source,
        "confidence": ORDER_TRUST[order_source],
        "episode_seconds": int(episode_seconds),
    }


def looks_like_blob(titles, min_seconds=120):
    """One title holding a whole season, which this box cannot split.

    Standard-definition box sets on Blu-ray do this, and so do some DVDs where MakeMKV's
    analysis pass welds the episodes together. Splitting it needs mkvmerge, which is not
    installed and is not a dependency worth adding to an appliance for a case this rare.
    So the honest move is to detect it and say so -- the rip still works, it just lands
    as one file the user will want to cut up elsewhere.

    The signature is a single very long title with a lot of chapters and nothing else of
    comparable length to make a cluster with.

    The threshold is three and a half hours rather than the eighty minutes this first
    used, because at eighty minutes an ordinary film disc with no extras on it matches
    perfectly: one long title, thirty-two chapters, nothing else. Chapters cannot
    separate them -- a two-hour film has about as many as six episodes do. Only length
    can, and only at a point past where films stop.

    Above three and a half hours the remaining collisions are real films: the epics, and
    the odd concert disc. They are rare, and being wrong about one costs a sentence of
    advice the user can ignore, because this only ever produces a warning -- it never
    changes what gets ripped.
    """
    long_ones = [t for t in titles
                 if t.get("seconds", 0) >= max(int(min_seconds or 0), BLOB_MIN_SECONDS)]
    if len(long_ones) != 1:
        return False
    t = long_ones[0]
    others = [o for o in titles if o["index"] != t["index"] and o.get("seconds", 0) >= 600]
    return t.get("chapters", 0) >= 12 and not others


# ── what the disc calls itself ───────────────────────────────────────────────

_SEASON_RE = re.compile(r"(?:^|[\s_\-])s(?:eason)?[\s_\-]*(\d{1,2})(?=$|[\s_\-]|d\d)", re.I)
_DISC_RE = re.compile(r"(?:^|[\s_\-])d(?:isc|isk)?[\s_\-]*(\d{1,2})(?=$|[\s_\-])", re.I)


def season_from_label(label):
    """(season, disc) parsed out of a volume label. Either may be None.

    Retail season discs label themselves properly far more often than films do --
    `THE_WIRE_S01_D03`, `BREAKING_BAD_SEASON_2_DISC_3` -- because the label has to
    distinguish six discs in one box. It is the cheapest signal available and it is
    usually right, so it seeds the answer; it never overrides the user.
    """
    s = (label or "").strip()
    if not s:
        return None, None
    season = _SEASON_RE.search(s)
    disc = _DISC_RE.search(s)
    return (int(season.group(1)) if season else None,
            int(disc.group(1)) if disc else None)


def series_name_from_label(label):
    """The label with the season/disc markers taken off, for searching.

    `pretty_label` in rip.py does the general cleanup; this only removes the parts that
    are specific to a season disc and would otherwise poison a title search.
    """
    s = (label or "").strip()
    s = _SEASON_RE.sub(" ", s)
    s = _DISC_RE.sub(" ", s)
    s = s.replace("_", " ").replace(".", " ")
    s = re.sub(r"\b(complete|season|series|collection|box\s*set|bd|dvd|uhd|4k)\b", " ",
               s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip(" -")
    return s.title() if (s.isupper() or s.islower()) else s


# ── TVmaze ───────────────────────────────────────────────────────────────────

_cache_lock = threading.Lock()
_show_cache = {}
_episode_cache = {}
CACHE_TTL = 24 * 3600

# TVmaze asks for 20 calls per 10 seconds at most. Riparr makes two per disc, so this
# is a courtesy rather than a constraint -- but a retry storm against a free service
# somebody else pays for is not a thing to ship.
_last_call = [0.0]
MIN_INTERVAL = 0.6


def _get(url, timeout=12):
    with _cache_lock:
        wait = MIN_INTERVAL - (time.time() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def search_series(name, limit=6):
    """Candidate shows for a name. Empty list on any failure.

    Never raises. Metadata is an improvement to a rip, not a precondition for one: a
    box with no internet must still rip the disc and file it under a name taken from the
    label, so every failure here degrades to "no episode titles" rather than stopping.
    """
    q = (name or "").strip()
    if not q:
        return []
    key = q.lower()
    with _cache_lock:
        hit = _show_cache.get(key)
        if hit and time.time() - hit[0] < CACHE_TTL:
            return hit[1]
    try:
        raw = _get("%s/search/shows?%s" % (API, urllib.parse.urlencode({"q": q})))
    except Exception as e:
        log.info("TVmaze search for %r failed: %s", q, e)
        return []
    out = []
    for row in (raw or [])[:limit]:
        show = row.get("show") or {}
        if not show.get("id"):
            continue
        out.append({
            "id": show["id"],
            "name": show.get("name") or "",
            "year": (show.get("premiered") or "")[:4] or None,
            "network": ((show.get("network") or show.get("webChannel") or {})
                        or {}).get("name") or "",
            "score": row.get("score") or 0,
        })
    with _cache_lock:
        _show_cache[key] = (time.time(), out)
    return out


def episodes(series_id, include_specials=True):
    """Every episode of a series, in TVmaze's airing order. Empty list on failure.

    Specials are included because a season disc very often carries one and Plex and
    Jellyfin both file them as season 00 -- but they are *flagged*, not numbered
    alongside the regular episodes, because a special sitting in the middle of the
    numbering would shift every episode after it by one.
    """
    if not series_id:
        return []
    key = (int(series_id), bool(include_specials))
    with _cache_lock:
        hit = _episode_cache.get(key)
        if hit and time.time() - hit[0] < CACHE_TTL:
            return hit[1]
    url = "%s/shows/%d/episodes" % (API, int(series_id))
    if include_specials:
        url += "?specials=1"
    try:
        raw = _get(url)
    except Exception as e:
        log.info("TVmaze episodes for %s failed: %s", series_id, e)
        return []
    out = []
    for e in raw or []:
        if e.get("season") is None or e.get("number") is None:
            # A special with no number. It cannot be placed, so it is not offered.
            continue
        out.append({
            "season": int(e["season"]),
            "number": int(e["number"]),
            "name": e.get("name") or "",
            "runtime": e.get("runtime") or 0,
            "special": (e.get("type") or "regular") != "regular",
        })
    with _cache_lock:
        _episode_cache[key] = (time.time(), out)
    return out


# ── turning all of that into a list of files ─────────────────────────────────

def build_plan(found, season=None, first_episode=1, series=None, episode_list=None):
    """The list of episodes to rip, numbered and named.

    Sequence comes from the disc (`found["episodes"]`, already ordered); numbers come
    from `season` and `first_episode`; names come from the metadata, looked up *by the
    number we assigned*, never the other way round. That direction is the whole point.
    If TVmaze thinks episode 1 is "The Train Job" and the disc opens with "Serenity",
    the file is still the first title on the disc and it is still numbered E01 -- it
    just gets a name that is wrong, which the user fixes by shifting `first_episode`
    or by correcting the row. Letting the metadata reorder the disc instead would
    produce files whose names and contents disagree, which is unfixable after the fact
    because nothing records what went where.

    A double-length title consumes two episode numbers and is named `S01E01-E02`, which
    is what Plex and Jellyfin both read as a two-episode file.
    """
    by_number = {}
    for e in (episode_list or []):
        by_number[(e["season"], e["number"])] = e

    plan = []
    number = int(first_episode or 1)
    for t in found["episodes"]:
        span = 2 if t["index"] in (found.get("doubles") or []) else 1
        numbers = list(range(number, number + span))
        meta = by_number.get((season, numbers[0])) if season is not None else None
        plan.append({
            "title_index": t["index"],
            "seconds": t["seconds"],
            "bytes": t.get("bytes") or 0,
            "season": season,
            "episode": numbers[0],
            "episode_last": numbers[-1],
            "episode_title": (meta or {}).get("name") or "",
            "source": t.get("source") or "",
        })
        number += span

    return {
        "series": (series or {}).get("name") or "",
        "series_id": (series or {}).get("id"),
        "series_year": (series or {}).get("year"),
        "season": season,
        "order_source": found["order_source"],
        "confidence": found["confidence"],
        "play_all_index": (found.get("play_all") or {}).get("index"),
        "episodes": plan,
    }


def plan_warnings(plan, found, episode_list=None):
    """Everything about this plan a person would want to be told before it runs.

    Separate from `build_plan` because a warning is not a failure and must not change
    what gets ripped -- these are shown next to the plan, and the plan is still correct
    if the user reads none of them.
    """
    out = []
    if found["order_source"] == "index":
        out.append("This disc doesn't say what order its episodes are in, so they're "
                   "numbered in the order MakeMKV found them — which is often not "
                   "broadcast order. Check the first episode before you accept this.")
    elif found["order_source"] == "source":
        out.append("Episode order is taken from the disc's playlist numbering, which "
                   "is right on almost every disc but not all of them. Worth a glance.")
    if not found.get("dedup_signal", True):
        out.append("This disc reports no playlist information, so if it carries each "
                   "episode twice — some do — both copies are listed. Untick any row "
                   "that looks like a repeat.")
    if found.get("duplicates"):
        out.append("%d duplicate playlist%s ignored — this disc carries each episode "
                   "twice, once with the “next time” trailer and once without."
                   % (len(found["duplicates"]),
                      "" if len(found["duplicates"]) == 1 else "s"))
    if found.get("play_all"):
        out.append("The “play all” title was excluded. It's every episode in "
                   "one file, and it's what gave us the episode order.")
    if found.get("doubles"):
        out.append("%d title%s twice the usual length, numbered as a two-episode file "
                   "(S01E01-E02), which is how a premiere or finale is normally shipped."
                   % (len(found["doubles"]), " is" if len(found["doubles"]) == 1
                      else "s are"))
    if plan.get("season") is None:
        out.append("No season number — the disc label didn't say. Set it before this "
                   "runs or the files land without one.")
    if episode_list is not None and plan.get("season") is not None:
        known = [e for e in episode_list if e["season"] == plan["season"]
                 and not e["special"]]
        if known:
            last = max(e["number"] for e in known)
            over = [e for e in plan["episodes"] if e["episode_last"] > last]
            if over:
                out.append("This numbering runs past the end of season %d, which "
                           "TVmaze says has %d episodes. The starting episode is "
                           "probably wrong." % (plan["season"], last))
    if not any(e["episode_title"] for e in plan["episodes"]):
        out.append("No episode titles were found, so the files are numbered but not "
                   "named. Plex and Jellyfin will still match them.")
    return out
