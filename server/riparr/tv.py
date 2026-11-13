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
to be credited, which the interface does. A TMDB key is therefore optional; with one, TVmaze
still finds the show, and TMDB supplies the numbers and names (`tmdb_episodes`).
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

# Nothing shorter than this is an ordinary episode. A ten-minute featurette is common
# on a season disc; the *clustering* decides what an episode is above this line.
EPISODE_MIN_SECONDS = 900

# ...but short episodes exist, and pressed to disc constantly: every eleven-minute
# cartoon (SpongeBob, Adventure Time, Regular Show), seven-minute shorts collections,
# webisodes. A hard floor at fifteen minutes threw those whole discs away as "extras",
# one of the commonest TV complaints on the MakeMKV forums. So titles from five to
# fifteen minutes are a second tier: they are the episodes only when there is no group
# of longer ones, or when they hold far more of the disc than the longer group does --
# see `_episode_cluster`. Ranking everything together by count let ten 8-minute
# featurettes outvote six real episodes; ranking by running time let a five-hour
# play-all outvote the thirteen episodes it contains (the real Samurai Jack disc).
SHORT_EPISODE_MIN_SECONDS = 300
SHORT_TIER_MIN_COUNT = 4
SHORT_TIER_DOMINANCE = 2.0

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

# Without a segment map, a play-all is recognised by adding up: it runs about as long
# as the episodes it strings together. Play-alls drop the per-episode recap and credits,
# so they come in somewhat short; a film is nowhere near the sum of its featurettes.
PLAY_ALL_SUM_LOW, PLAY_ALL_SUM_HIGH = 0.80, 1.10

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

    Between copies of the same footage, the one with more audio tracks, then more
    chapters, is the episode. Avatar Book Two disc 1 carries episode 6 three times over
    one stream file: 00254.mpls with a single track (the commentary), 00606.mpls with the
    four normal tracks, and 01606.mpls with those four and the commentary after them.
    00254 and 00606 are the same length, and the lower title number won the tie: the
    season would have been filed with commentary in place of sound.
    """
    if not _dedup_signal(candidates):
        return list(candidates)
    kept = []
    for t in sorted(candidates, key=lambda x: (-x["seconds"], -x.get("audio", 0),
                                               -x.get("chapters", 0), x["index"])):
        segs = set(parse_segments(t.get("segments")))
        src = (t.get("source") or "").strip().lower()
        dup = False
        for i, k in enumerate(kept):
            ksegs = set(parse_segments(k.get("segments")))
            if segs and ksegs:
                if segs & ksegs:
                    # The same footage exactly: a second of rounding must not let the
                    # commentary copy beat the one with the sound.
                    if segs == ksegs and ((t.get("audio", 0), t.get("chapters", 0))
                                          > (k.get("audio", 0), k.get("chapters", 0))):
                        kept[i] = t
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
    best, best_key = [], None
    for anchor in titles:
        lo = anchor["seconds"] * (1 - CLUSTER_TOLERANCE)
        hi = anchor["seconds"] * (1 + CLUSTER_TOLERANCE)
        group = [t for t in titles if lo <= t["seconds"] <= hi]
        # Bigger cluster wins; a tie goes to the longer runtime, because a disc with
        # three 42-minute episodes and three 8-minute featurettes should resolve to the
        # episodes. (Featurettes rarely cluster this tightly, but ties are cheap to
        # break correctly and expensive to get wrong.)
        key = (len(group), anchor["seconds"])
        if not best or key > best_key:
            best, best_key = group, key
    return best


def _episode_cluster(titles):
    """The episodes' cluster, with short episodes as a second tier. See
    SHORT_EPISODE_MIN_SECONDS for why it is two tiers rather than one ranking."""
    long_ = _largest_cluster([t for t in titles if t["seconds"] >= EPISODE_MIN_SECONDS])
    short = _largest_cluster([t for t in titles
                              if SHORT_EPISODE_MIN_SECONDS <= t["seconds"] < EPISODE_MIN_SECONDS])
    if len(short) < MIN_EPISODES:
        return long_
    if len(long_) < MIN_EPISODES:
        return short
    if (len(short) >= SHORT_TIER_MIN_COUNT and sum(t["seconds"] for t in short)
            > SHORT_TIER_DOMINANCE * sum(t["seconds"] for t in long_)):
        return short
    return long_


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


def _furniture(titles):
    """Segments shared by so many titles they cannot belong to any one episode.

    An episode's own segment turns up in a handful of titles at most: the episode, a
    second cut of it, the play-all. Something in four or more titles, and in at least
    two-fifths of them, is a logo or a bumper.
    """
    count = {}
    for t in titles:
        for s in set(parse_segments(t.get("segments"))):
            count[s] = count.get(s, 0) + 1
    need = max(4, int(0.4 * len(titles) + 0.999))
    return {s for s, n in count.items() if n >= need}


def _without(title, furniture):
    """A copy of `title` with the furniture segments taken out of its map -- unless
    that would leave it with none, in which case it is left as it was."""
    segs = [s for s in parse_segments(title.get("segments")) if s not in furniture]
    if not segs or len(segs) == len(parse_segments(title.get("segments"))):
        return title
    out = dict(title)
    out["segments"] = ",".join(str(s) for s in segs)
    return out


def _held(title, others):
    """The other titles whose segments all lie inside this one's segment map."""
    segs = set(parse_segments(title.get("segments")))
    if not segs:
        return []
    out = []
    for o in others:
        if o["index"] == title["index"]:
            continue
        osegs = set(parse_segments(o.get("segments")))
        if osegs and osegs <= segs and osegs != segs:
            out.append(o)
    return out


def _really_plays_all(play_all, episodes, by_length=True):
    """Is this long title actually the episodes end to end, or just something long?

    `_find_play_all` picks the longest title well past episode length, which on a
    season disc is the play-all -- and on a *film* disc with two or three featurettes is
    the film. Taken at its word, a film plus a 25-minute making-of and a 26-minute
    documentary read as a two-episode season with the film excluded as its play-all
    (shipped in 0.6.1, found 2026-10-12 testing against a real season disc). So the
    claim has to be checked against the thing it claims to contain:

    * its segment map holds at least two of the episodes' segments (Blu-ray), or
    * it runs about as long as the episodes added together (a DVD has no segment map).
    """
    if not play_all or not episodes:
        return False
    segs = set(parse_segments(play_all.get("segments")))
    if segs:
        held = sum(1 for e in episodes
                   if set(parse_segments(e.get("segments"))) and
                   set(parse_segments(e.get("segments"))) <= segs)
        if held >= 2:
            return True
    if not by_length:
        return False
    total = sum(e["seconds"] for e in episodes)
    return bool(total) and (PLAY_ALL_SUM_LOW * total <= play_all["seconds"]
                            <= PLAY_ALL_SUM_HIGH * total)


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


def _beyond_agrees(ordered, beyond):
    """Whether the playlist numbers back up where the segment numbers put the episodes
    past the play-all: all playlists, numbered upward in that order, after the last of
    the play-all's own run in the same block."""
    if not beyond:
        return False
    if not all((t.get("source") or "").lower().endswith(".mpls") for t in beyond):
        return False
    nums = [_source_number(t) for t in beyond]
    if None in nums or nums != sorted(nums) or len(set(nums)) != len(nums):
        return False
    run = [n for n in (_source_number(t) for t in ordered if t not in beyond)
           if n is not None and n // 100 == nums[0] // 100]
    return bool(run) and nums[0] > max(run)


def _order_past_play_all(episodes, play_all):
    """Order a disc whose play-all holds only some of its episodes, or None.

    Samurai Jack season 5: the play-all runs episodes 1-8 and stops; 9 and 10 sit after
    it. When the play-all lists its segments in ascending order -- the disc was authored
    with segment numbers in broadcast order -- and every episode is one segment, the
    segment numbers themselves are that order, so episodes the play-all leaves out slot
    in where their numbers fall. Anything less regular than that is not trusted.
    """
    if not play_all:
        return None
    order = parse_segments(play_all.get("segments"))
    if not order or order != sorted(order) or len(set(order)) != len(order):
        return None
    keys = []
    for t in episodes:
        segs = parse_segments(t.get("segments"))
        if len(segs) != 1:
            return None
        keys.append((segs[0], t))
    if len({k for k, _ in keys}) != len(keys):
        return None
    if not any(k in set(order) for k, _ in keys):
        return None
    return [t for _, t in sorted(keys, key=lambda x: x[0])]


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

    # A segment map only compares across titles when it numbers one shared pool of
    # stream files, which is what a Blu-ray playlist's map does. A DVD title has no
    # source file, and its map counts that title's own cells from 1, so a DVD season disc
    # reads "1,2-3,4-5..." on every title. Compared, every title "holds" the shorter
    # ones and "overlaps" the rest, which collapsed a premiere-and-two-episodes disc into
    # a film and a four-episode disc into two episodes. So those maps are
    # dropped here and the disc is treated as having told us nothing.
    titles = [dict(t, segments="") if t.get("segments") and not t.get("source") else t
              for t in titles]
    floor = max(int(min_seconds or 0), SHORT_EPISODE_MIN_SECONDS)
    candidates = [t for t in titles if t.get("seconds", 0) >= floor]
    if len(candidates) < MIN_EPISODES:
        return None
    # Segments every other title starts with -- a studio logo, a one-second bumper --
    # are the disc's furniture, not part of any episode, and are taken out of every
    # map before anything reads them. Avatar: The Last Airbender's Blu-ray puts segment
    # 1100 (one second) in front of every episode's "with the opening" version and at
    # the head of the play-all; left in, it made every one of those versions "overlap"
    # every other, so de-duplication kept a random mix, and the play-all placed
    # whichever one it kept first as episode one.
    furniture = _furniture(candidates)
    if furniture:
        candidates = [_without(t, furniture) for t in candidates]

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
    rough = _episode_cluster(candidates)
    if len(rough) < MIN_EPISODES:
        return None
    play_all = _find_play_all(candidates, _median([t["seconds"] for t in rough]))
    if play_all and not _really_plays_all(play_all, _dedup(rough)):
        # Something long that is not the episodes strung together. Longer than every
        # "episode" put together, it is the reason the disc exists -- a film, and the
        # cluster is its featurettes. Shorter, it is a bonus feature sitting on a real
        # season disc (a documentary beside six episodes) and simply is not the play-all.
        if play_all["seconds"] > sum(t["seconds"] for t in _dedup(rough)):
            return None
        play_all = None

    # A title whose segment map wholly holds two or more other titles is a compilation
    # of them, at any length: the play-all of a two-episode disc (2x, under the ratio
    # above), or a "premiere movie" sold beside its own parts. It must go before the
    # merge, for the same reason the play-all does -- it overlaps everything it holds
    # and, as the longest, would absorb them. Found when a two-episode play-all at
    # exactly 2x came back from analyse() as no season at all.
    compilations = [t for t in candidates
                    if t is not play_all and len(_held(t, candidates)) >= 2]
    if not play_all and compilations:
        play_all = max(compilations, key=lambda t: t["seconds"])
    gone = {t["index"] for t in compilations} | {(play_all or {}).get("index")}
    remaining = [t for t in candidates if t["index"] not in gone]
    unique = _dedup(remaining)
    cluster = _episode_cluster(unique)
    if len(cluster) < MIN_EPISODES:
        return None
    # Film-length "episodes" that all run to the same second are one film in several
    # versions -- English and original title cards, say -- not a season. A film DVD
    # with two 88-minute titles that differ by two cells read as two episodes, because a
    # DVD's maps are dropped above and nothing else here can see they are the same film.
    # A season of feature-length episodes runs to different lengths.
    lengths = [t["seconds"] for t in cluster]
    if min(lengths) >= BONUS_FILM_SECONDS and max(lengths) - min(lengths) <= 2:
        return None
    episode_seconds = _median([t["seconds"] for t in cluster])

    # A title at twice the episode length, sitting alongside a normal cluster, is a
    # double-length premiere or finale rather than a play-all. Fold it back in; it gets
    # numbered as a two-episode file further down.
    doubles = [t for t in unique
               if t["index"] != (play_all or {}).get("index")
               and t not in cluster
               and DOUBLE_LOW <= t["seconds"] / float(episode_seconds) <= DOUBLE_HIGH]
    # The last disc of a season often carries two episodes and a play-all, and that
    # play-all is exactly twice an episode -- the same length as a double-length
    # premiere. It sits below PLAY_ALL_RATIO, so it arrives here. If it holds the other
    # episodes' segments, or (with no segment map) runs as long as both of them, it is
    # the play-all, and ripping it as E03-E04 would put both episodes on disk twice.
    #
    # With no segment map, length cannot tell them apart: a 90-minute premiere beside
    # two 45-minute episodes adds up exactly like a play-all. Excluding a real
    # episode loses it silently, and ripping a play-all is one visible unticked row on
    # a plan that is held for confirmation anyway, so such a title stays in and
    # `plan_warnings` says why. The same argument `_dedup` makes for erring towards
    # duplicates.
    unsure_doubles = []
    for t in list(doubles):
        if _really_plays_all(t, cluster, by_length=False):
            doubles.remove(t)
            if not play_all:
                play_all = t
        elif not parse_segments(t.get("segments")) and _really_plays_all(t, cluster,
                                                                         by_length=len(cluster) == 2):
            unsure_doubles.append(t["index"])
    episodes = sorted(cluster + doubles, key=lambda t: t["index"])

    # The play-all also says which titles are the season. An episode-length title with
    # a segment map and none of it in the play-all is something else the disc carries --
    # a bonus episode, a pilot, a 21-minute documentary -- so it is offered as an extra
    # rather than numbered. Found on Samurai Jack season 2: a bare 02630.m2ts counted as
    # a fourteenth episode, which also cost the disc its segment order, and the fallback
    # (playlist numbering) filed episode 12 as E01. Only when the play-all holds most of
    # the cluster, and runs as long as the titles it holds: one that is longer than its
    # map accounts for has a map we can't read fully, and then nothing is left out.
    #
    # But not a *sibling* of the episodes: a playlist numbered in the same run as them.
    # Samurai Jack season 5's play-all stops at episode 8, and episodes 9 and 10 are
    # 00808.mpls and 00809.mpls, right after 00800-00807. Excluding everything outside
    # the play-all filed them as unticked extras, with high confidence, unattended. A
    # bonus feature is the other shape: a bare .m2ts (season 2's 02630.m2ts), or a
    # playlist from another block.
    outside = []
    pa_segs = set(parse_segments((play_all or {}).get("segments")))
    if pa_segs:
        inside_blocks = [n // 100 for n in (_source_number(e) for e in episodes
                         if set(parse_segments(e.get("segments"))) & pa_segs)
                         if n is not None]
        block = max(set(inside_blocks), key=inside_blocks.count) if inside_blocks else None

        def sibling(e):
            n = _source_number(e)
            return ((e.get("source") or "").lower().endswith(".mpls")
                    and n is not None and block is not None and n // 100 == block)
        outs = [e for e in episodes if parse_segments(e.get("segments"))
                and not set(parse_segments(e.get("segments"))) & pa_segs
                and not sibling(e)]
        # What the play-all actually holds -- not its siblings beyond it, which it
        # cannot be expected to run as long as.
        held = sum(e["seconds"] for e in episodes
                   if set(parse_segments(e.get("segments"))) & pa_segs)
        if (outs and len(episodes) - len(outs) >= max(MIN_EPISODES, len(outs) + 1)
                and PLAY_ALL_SUM_LOW <= play_all["seconds"] / float(held or 1)
                <= PLAY_ALL_SUM_HIGH):
            outside = outs
            episodes = [e for e in episodes if e not in outs]
            doubles = [d for d in doubles if d not in outs]

    ordered = _order_by_segments(episodes, play_all)
    order_source = "segments"
    confidence = None
    beyond = []
    if ordered is None:
        ordered = _order_past_play_all(episodes, play_all)
        if ordered is not None:
            pa = set(parse_segments(play_all.get("segments")))
            beyond = [t for t in ordered
                      if not set(parse_segments(t.get("segments"))) & pa]
            # Trusted as far as the play-all only if the playlist numbers say the same
            # thing as the segment numbers: every episode past it a playlist, numbered
            # upward in the order placed, after the play-all's own run. Two independent
            # records agreeing is a fact; one alone is a good guess, and is shown.
            confidence = "high" if _beyond_agrees(ordered, beyond) else "medium"
            beyond = [t["index"] for t in beyond]
    if ordered is None:
        ordered = _order_by_source(episodes)
        order_source = "source"
    if ordered is None:
        ordered = sorted(episodes, key=lambda t: t["index"])
        order_source = "index"

    chosen = {t["index"] for t in ordered}
    if play_all:
        chosen.add(play_all["index"])
    # Duplicate playlists and compilations are the episodes again, not extras. Listed
    # as extras, the six "without the trailer" copies of a season appeared as six
    # forty-minute bonus features -- harmless while extras could only be looked at, and
    # a way to rip the season twice once they could be ticked.
    chosen |= {t["index"] for t in remaining if t not in unique}
    chosen |= {t["index"] for t in compilations}
    # Extras are listed at the user's own floor, not the episode floor: a ten-minute
    # featurette is exactly the thing somebody wants to see was found and skipped.
    extras = [t for t in titles
              if t["index"] not in chosen
              and t.get("seconds", 0) >= max(int(min_seconds or 0), 120)]

    return {
        "episodes": ordered,
        "play_all": play_all,
        "extras": extras,
        "duplicates": [t for t in remaining if t not in unique]
                      + [t for t in compilations if t is not play_all],
        "dedup_signal": _dedup_signal(remaining),
        "doubles": [t["index"] for t in doubles],
        "unsure_doubles": unsure_doubles,
        "outside_play_all": [t["index"] for t in outside],
        "beyond_play_all": beyond,
        "order_source": order_source,
        "confidence": confidence or ORDER_TRUST[order_source],
        "episode_seconds": int(episode_seconds),
    }


# Past this, the longest title is the reason the disc exists: a film sold in the box set,
# which belongs on the film path. A bonus disc's documentaries run well short of it.
BONUS_FILM_SECONDS = 75 * 60


def bonus_disc(titles, min_seconds=120, allow_long=False):
    """Read a title list as a box set's bonus disc: `analyse`'s shape, or None.

    Only worth asking of a disc `analyse` has turned down and whose label belongs to a set
    already filed. A bonus disc of 18, 14, 10 and 5 minutes plus ten shorts is too uneven
    to be a season, so the film path kept the longest title and dropped the other thirteen.
    Every title stays, in disc order, because nothing here can say which ones matter.

    `allow_long` keeps a disc whose longest title is film-length. It is for the safety
    net: a disc of a filed set that nothing else could read is held for a person, who
    can file it as episodes, specials or a film, instead of going to Movies unasked.
    """
    keep = [t for t in titles or []
            if t.get("seconds", 0) >= max(int(min_seconds or 0), 120)]
    if not keep or (max(t["seconds"] for t in keep) >= BONUS_FILM_SECONDS
                    and not allow_long):
        return None
    return {"episodes": sorted(keep, key=lambda t: t["index"]),
            "order_source": "index", "confidence": "low", "doubles": [], "extras": [],
            "episode_seconds": int(_median([t["seconds"] for t in keep]))}


def finale_disc(titles, episode_list, season, first_episode, min_seconds=120):
    """Read a box set's last disc as one long episode and its extras: `analyse`'s shape,
    or None.

    A box set's last disc can hold a 91-minute series finale and fourteen featurettes.
    One episode is too few for a season and too long for a bonus disc, so it was filed as
    a film. Only worth asking of a disc whose set already has a filed disc of this season:
    the guide's next episode (from `first_episode`) must run as long as the one long
    title -- or, in a guide that splits it, the next two together.
    """
    keep = [t for t in titles or []
            if t.get("seconds", 0) >= max(int(min_seconds or 0), 120)]
    long_ones = [t for t in keep if t["seconds"] >= BONUS_FILM_SECONDS]
    if len(long_ones) != 1 or season in (None, 0) or not first_episode:
        return None
    by_number = {e["number"]: e for e in episode_list or []
                 if e.get("season") == season and not e.get("special")}

    def fits(*numbers):
        runtime = sum((by_number.get(n) or {}).get("runtime") or 0 for n in numbers)
        minutes = long_ones[0]["seconds"] / 60.0
        return (all(n in by_number for n in numbers) and runtime > 0
                and abs(runtime - minutes) <= 0.2 * minutes)

    if fits(first_episode):
        doubles = []
    elif fits(first_episode, first_episode + 1):
        doubles = [long_ones[0]["index"]]
    else:
        return None
    return {"episodes": long_ones, "order_source": "index", "confidence": "low",
            "doubles": doubles, "unsure_doubles": [],
            "extras": sorted((t for t in keep if t is not long_ones[0]),
                             key=lambda t: t["index"]),
            "episode_seconds": int(long_ones[0]["seconds"])}


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

_NUMBER_WORDS = {w: n for n, w in enumerate(
    "one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen twenty".split(), 1)}
_ORDINALS = {w: n for n, w in enumerate(
    "first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth "
    "thirteenth fourteenth fifteenth sixteenth seventeenth eighteenth nineteenth "
    "twentieth".split(), 1)}
_NUM = r"(\d{1,2}|%s)" % "|".join(_NUMBER_WORDS)
# "Season", and the other words a box set uses for one: Avatar's are "Book One", the
# BBC's "Series 3", anime and older US sets "Volume 2". `S01` keeps its short form.
_SEASON_RE = re.compile(
    r"(?:^|[\s_\-:.])(?:s(?:eason)?|series|book|volume|vol)[\s_\-.]*%s"
    r"(?=$|[\s_\-:.,]|d\d)" % _NUM, re.I)
# ...and the ordinal form disc names use: "The Complete First Season".
_ORDINAL_RE = re.compile(r"\b(%s)[\s_\-]+(?:season|series|book|volume)\b"
                         % "|".join(_ORDINALS), re.I)
_DISC_RE = re.compile(r"(?:^|[\s_\-])d(?:isc|isk)?[\s_\-]*(\d{1,2})(?=$|[\s_\-])", re.I)
# Run together on the end of the show's name, as in "EXS1D1". Only the two markers
# together count: a lone S1 or D1 inside a word is far more likely to be part of a name.
_COMPACT_RE = re.compile(r"(?<=[A-Za-z0-9])s(\d{1,2})d(\d{1,2})(?=$|[\s_\-.])", re.I)


def _number(word):
    w = word.lower()
    return int(w) if w.isdigit() else _NUMBER_WORDS.get(w) or _ORDINALS.get(w)


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
    season = _SEASON_RE.search(s) or _ORDINAL_RE.search(s)
    disc = _DISC_RE.search(s)
    if not season and not disc:
        compact = _COMPACT_RE.search(s)
        if compact:
            return int(compact.group(1)), int(compact.group(2))
    return (_number(season.group(1)) if season else None,
            int(disc.group(1)) if disc else None)


def series_name_from_label(label):
    """The label with the season/disc markers taken off, for searching.

    `pretty_label` in rip.py does the general cleanup; this only removes the parts that
    are specific to a season disc and would otherwise poison a title search.
    """
    s = (label or "").strip()
    if not (_SEASON_RE.search(s) or _ORDINAL_RE.search(s) or _DISC_RE.search(s)):
        compact = _COMPACT_RE.search(s)
        if compact:
            s = s[:compact.start()]
    # Everything before the season marker is the show: "Avatar: The Last Airbender Book
    # One: Water Disc 1" is "Avatar: The Last Airbender", and the book's subtitle after
    # it is not part of the name. Taking the marker out and keeping the rest searched
    # TVmaze for "Avatar Book 1 Water" and found nothing.
    m = _SEASON_RE.search(s) or _ORDINAL_RE.search(s)
    if m and m.start() > 0:
        s = s[:m.start()]
        s = re.sub(r"[\s_\-:]*(?:the[\s_]+)?complete[\s_]*$", "", s, flags=re.I)
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
            "runtime": show.get("averageRuntime") or show.get("runtime") or 0,
        })
    with _cache_lock:
        _show_cache[key] = (time.time(), out)
    return out


def show(series_id):
    """One show by its TVmaze id, in the shape `search_series` returns. None on failure."""
    try:
        s = _get("%s/shows/%d" % (API, int(series_id))) or {}
    except Exception as e:
        log.info("TVmaze lookup of show %s failed: %s", series_id, e)
        return None
    if not s.get("id"):
        return None
    return {"id": s["id"], "name": s.get("name") or "",
            "year": (s.get("premiered") or "")[:4] or None,
            "network": ((s.get("network") or s.get("webChannel") or {}) or {}).get("name") or "",
            "score": 0, "runtime": s.get("averageRuntime") or s.get("runtime") or 0}


def set_key(label):
    """What the discs of one box set share: the show part of a label that also says
    which season or disc it is. Empty for a label with no such marker, because two films
    or two discs called "DVD_VIDEO" are not a set."""
    if label is None or season_from_label(label) == (None, None):
        return ""
    return series_name_from_label(label).casefold()


def pick_series(options, episode_seconds=None, season=None, facts=None, named=None):
    """The best of TVmaze's candidates for a disc, or None.

    TVmaze's score is a text match, and two shows with the same name score the same:
    "Avatar: The Last Airbender" is both the 2005 cartoon and Netflix's 2024 remake, at
    1.6 each, and taking the first filed the cartoon's Blu-ray under the remake. Among
    the candidates within a whisker of the best score, the one whose usual episode
    length is nearest the disc's wins -- 22-minute episodes are not an hour-long show.
    """
    if not options:
        return None
    # The second witness first. What Wikidata finds by the same name, on its own: when
    # some of TVmaze's candidates are among those shows, the others are not this one.
    if named:
        ids = {i for s in named for i in s.get("tvmaze") or []}
        both = [o for o in options if o["id"] in ids]
        options = both or options
    # A show Wikidata says has fewer seasons than the disc's
    # season number is not this show, however well its name matched. Only ever used to
    # rule out -- if it would rule out everything, it is the witness that is wrong.
    if facts and season:
        possible = [o for o in options
                    if not (facts.get(o["id"]) or {}).get("seasons")
                    or facts[o["id"]]["seasons"] >= season]
        options = possible or options
    best = options[0].get("score") or 0
    close = [o for o in options if (o.get("score") or 0) >= best - 0.05]
    if len(close) < 2 or not episode_seconds:
        return options[0]
    minutes = episode_seconds / 60.0

    def miss(o):
        rt = o.get("runtime") or 0
        return abs(rt - minutes) / minutes if rt else 9e9
    return min(close, key=miss)


def _norm_title(s):
    s = re.sub(r"\(\s*(?:part\s*)?\d+\s*\)", " ", (s or "").lower())
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def _same_title(a, b):
    """Whether two sources' names for an episode are the same name. Containment either
    way, because one source's "Book One: Water - Chapter One: The Boy in the Iceberg"
    is the other's "The Boy in the Iceberg"; and a floor, so "Jack" is no evidence."""
    a, b = _norm_title(a), _norm_title(b)
    if len(a) < 6 or len(b) < 6:
        return False
    if a == b or a in b or b in a:
        return True
    # The same words in another order: "Winter Solstice, Part 1: The Spirit World"
    # against "The Spirit World (Winter Solstice, Part 1)". Every word of the shorter,
    # bar the little ones, in the longer -- and at least two of them.
    small = {"the", "a", "an", "of", "and", "in", "on", "to", "part"}
    wa, wb = set(a.split()) - small, set(b.split()) - small
    short, long_ = (wa, wb) if len(wa) <= len(wb) else (wb, wa)
    return len(short) >= 2 and short <= long_


def cross_check(rows, episode_list, witness_titles):
    """Hold the plan's numbering up against a second source's episode titles.

    For each planned episode the witness has a title for, one of three things is true:
    it names the same episode TVmaze does (agreement); it names an episode TVmaze has
    at a *different* number (a shift -- the disc is not where the plan puts it); or it
    names nothing TVmaze has, which is the two sources' house styles differing ("Episode
    I" against "The Beginning (1)") and says nothing either way.

    Returns {"verdict": "agree" | "shifted" | "silent", "agree": n, "compared": n,
    "offset": k or None, "filled": n}, and fills in any title TVmaze left empty.
    """
    out = {"verdict": "silent", "agree": 0, "compared": 0, "offset": None, "filled": 0}
    if not witness_titles:
        return out
    by_number = {}
    for e in episode_list or []:
        if not e.get("special"):
            by_number[(e["season"], e["number"])] = e.get("name") or ""
    shifts = []
    for r in rows:
        if r.get("extra") or not r.get("include", True):
            continue
        n, season = r.get("episode"), r.get("season")
        w = witness_titles.get(n)
        if not w:
            continue
        if not r.get("episode_title"):
            r["episode_title"] = w
            out["filled"] += 1
            continue
        if _same_title(w, by_number.get((season, n), "")):
            out["agree"] += 1
            continue
        match = [num for (s, num), name in by_number.items()
                 if s == season and num != n and _same_title(w, name)]
        if len(match) == 1:
            shifts.append(match[0] - n)
    out["compared"] = out["agree"] + len(shifts)
    if shifts and len(shifts) >= 2 and len(set(shifts)) == 1 and len(shifts) > out["agree"]:
        out["verdict"], out["offset"] = "shifted", shifts[0]
    elif out["agree"] and len(shifts) <= out["agree"] // 3:
        out["verdict"] = "agree"
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


TMDB_API = "https://api.themoviedb.org/3"
_tmdb_cache = {}


def _tmdb_get(path, key):
    """One TMDB request, with either kind of credential its settings page hands out: the
    short v3 key goes in the query, the long v4 read token in a header. Exceptions carry
    no URL, so logging one can't leak the key."""
    headers = {"User-Agent": UA, "Accept": "application/json"}
    url = TMDB_API + path
    if key.startswith("eyJ"):
        headers["Authorization"] = "Bearer " + key
    else:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode({"api_key": key})
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=12) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _tmdb_show_id(series_id, key):
    """The TMDB id of a TVmaze show, through the ids TVmaze records for it. None if
    neither finds a show: guessing by name is how a disc gets another show's numbers."""
    ext = (_get("%s/shows/%d" % (API, int(series_id))) or {}).get("externals") or {}
    for source, value in (("tvdb_id", ext.get("thetvdb")), ("imdb_id", ext.get("imdb"))):
        if not value:
            continue
        found = _tmdb_get("/find/%s?external_source=%s"
                          % (urllib.parse.quote(str(value)), source), key)
        rows = (found or {}).get("tv_results") or []
        if rows:
            return int(rows[0]["id"])
    return None


def tmdb_episodes(series_id, key):
    """Every episode of a TVmaze show as TMDB numbers them, shaped like `episodes`.
    Empty on any failure, which the caller reports: unlike a missing name, numbers from
    the wrong guide put files where the media server shows the wrong episode.

    The same show can be counted differently: TVmaze can list a 90-minute premiere as
    S01E01 and E02 where TMDB has one S01E01, and everything after it is one number apart.
    Plex's and Jellyfin's default agents use TMDB, so its count is the one they read.
    """
    if not series_id or not key:
        return []
    sid = int(series_id)
    with _cache_lock:
        hit = _tmdb_cache.get(sid)
        if hit and time.time() - hit[0] < CACHE_TTL:
            return hit[1]
    try:
        tid = _tmdb_show_id(sid, key)
        if tid is None:
            log.info("TMDB has no show for TVmaze %s", sid)
            return []
        info = _tmdb_get("/tv/%d" % tid, key) or {}
        numbers = [int(x["season_number"]) for x in info.get("seasons") or []
                   if x.get("season_number") is not None]
        out = []
        # TMDB allows twenty appended seasons per request.
        for i in range(0, len(numbers), 20):
            part = numbers[i:i + 20]
            raw = _tmdb_get("/tv/%d?append_to_response=%s" % (
                tid, ",".join("season/%d" % n for n in part)), key) or {}
            for n in part:
                for e in (raw.get("season/%d" % n) or {}).get("episodes") or []:
                    if e.get("episode_number") is None:
                        continue
                    out.append({"season": n, "number": int(e["episode_number"]),
                                "name": e.get("name") or "",
                                "runtime": e.get("runtime") or 0,
                                "special": n == 0})
    except Exception as e:
        log.warning("TMDB episodes for TVmaze %s failed: %s", sid, e)
        return []
    with _cache_lock:
        _tmdb_cache[sid] = (time.time(), out)
    return out


_dvd_cache = {}


def dvd_order(series_id):
    """TVmaze's DVD-release order for a series, or {} if it has none, or None if that
    couldn't be found out.

    The one question about a season disc that reading the disc cannot settle is whether
    the disc runs in broadcast order. Most shows do; a few were released in production
    order instead -- Firefly opens with "Serenity", which every guide numbers S01E11.
    TVmaze publishes that second order as an "alternate list" (no account, no key), so
    it is looked up rather than asked. Returns {(dvd season, dvd number): episode} where
    episode is the broadcast-order {"season", "number", "name"} that position holds.

    {} and None are different answers and the caller treats them differently: {} is
    "TVmaze knows this show and it has no other order", which settles the question; None
    is "couldn't ask", which leaves it open.
    """
    if not series_id:
        return None
    sid = int(series_id)
    with _cache_lock:
        hit = _dvd_cache.get(sid)
        if hit and time.time() - hit[0] < CACHE_TTL:
            return hit[1]
    try:
        lists = _get("%s/shows/%d/alternatelists" % (API, sid)) or []
        chosen = next((x for x in lists if x.get("dvd_release")), None)
        out = {}
        if chosen:
            raw = _get("%s/alternatelists/%d/alternateepisodes?embed=episodes"
                       % (API, int(chosen["id"]))) or []
            for a in raw:
                aired = ((a.get("_embedded") or {}).get("episodes") or [None])[0]
                if (a.get("season") is None or a.get("number") is None or not aired
                        or aired.get("season") is None or aired.get("number") is None):
                    continue
                out[(int(a["season"]), int(a["number"]))] = {
                    "season": int(aired["season"]), "number": int(aired["number"]),
                    "name": aired.get("name") or a.get("name") or ""}
    except Exception as e:
        log.info("TVmaze DVD order for %s failed: %s", series_id, e)
        return None
    with _cache_lock:
        _dvd_cache[sid] = (time.time(), out)
    return out


def dvd_map_for(dvd, season):
    """The part of a DVD order that concerns one disc season, as {position: episode},
    or {} when there is nothing that differs from broadcast order.

    An identical list is dropped rather than carried: it changes no file, and a plan
    that says "named from the DVD order" when nothing moved is noise.
    """
    if not dvd or season is None:
        return {}
    m = {n: e for (s, n), e in dvd.items() if s == season}
    if all(e["season"] == season and e["number"] == n for n, e in m.items()):
        return {}
    return m


def _guide_says_one(row, season, number, by_number):
    """Whether the guide lists this double-length title as a single long episode: its
    entry at this number runs about as long as the title. Needs a guide with real
    per-episode runtimes, which is why only TMDB numbering asks."""
    minutes = float(row.get("seconds") or 0) / 60.0
    runtime = (by_number.get((season, number)) or {}).get("runtime") or 0
    return bool(minutes and runtime) and abs(runtime - minutes) <= 0.2 * minutes


def number_rows(rows, season, start, episode_list=None, dvd_map=None, typed=(),
                fit_runtimes=False):
    """Number and name the episodes that will be written, in the order given.

    Two numbers per row. `position` is where the episode sits in the season *as the
    discs run* -- E01 on the first disc, carried on by the next -- and is what disc-to-
    disc continuation and the "first episode" control count in. `episode` is the number
    the file is named with, which is the broadcast number: the same as `position` for
    almost every show, and, for a show whose DVD order differs, whatever broadcast
    number the DVD position holds. Plex, Jellyfin and every guide number episodes in
    broadcast order, so "Serenity" is S01E11 whichever disc slot it came from.

    The mapping is used only when it covers every position on this disc. Half a disc
    mapped and half counted would let two files claim one number.
    """
    by_number = {(e["season"], e["number"]): e for e in (episode_list or [])}
    dvd_map = {int(k): v for k, v in (dvd_map or {}).items()}
    kept = [r for r in rows if r.get("include", True)]
    n, spans = int(start or 1), []
    for r in kept:
        span = int(r.get("span") or 0) or (
            2 if int(r.get("episode_last") or r.get("episode") or 0)
            > int(r.get("episode") or 0) else 1)
        # What the disc suggested, kept so that picking another show can undo a fit.
        span = int(r.setdefault("disc_span", span))
        if fit_runtimes and not dvd_map and span > 1 and _guide_says_one(
                r, season, n, by_number):
            span = 1
        spans.append((r, n, span))
        n += span
    use_dvd = bool(dvd_map) and all(p + k in dvd_map for _, p, s in spans
                                    for k in range(s))
    for r, p, span in spans:
        r["span"] = span
        r["position"], r["position_last"] = p, p + span - 1
        if use_dvd:
            a, b = dvd_map[p], dvd_map[p + span - 1]
            r["season"], r["episode"] = a["season"], a["number"]
            r["episode_last"] = (b["number"] if b["season"] == a["season"]
                                 and b["number"] == a["number"] + span - 1
                                 else a["number"])
            name = a.get("name") or ""
        else:
            r["season"], r["episode"], r["episode_last"] = season, p, p + span - 1
            # A two-episode file carries both names, joined the way Sonarr joins them.
            # Avatar Book Two disc 2 has "The Serpent's Pass" and "The Drill" welded into
            # one stream; named after the first alone, the second had no name anywhere.
            names = [(by_number.get((season, k)) or {}).get("name") or ""
                     for k in range(p, p + span)] if season is not None else [""]
            name = " + ".join(x for x in names if x)
        if int(r.get("title_index", -1)) not in typed:
            r["episode_title"] = name
    if not use_dvd:
        _pin_typed_names(spans, season, episode_list, typed)
    return use_dvd


def _name_key(x):
    x = re.sub(r"^(the|a|an)\s+", "", (x or "").strip().lower())
    return re.sub(r"[^a-z0-9]+", " ", x).strip()


def _guide_number_for(name, season, episode_list):
    """The one guide episode a typed name names, as (season, number), or None.

    Exact after case and punctuation, with one allowance: "Example Story Part 1 & Part
    2" names the guide's single "Example Story", because a person who typed the parts
    was describing the file, not inventing two episodes. This season's episodes are
    looked at first; any other season's after, because box-set volumes don't follow the
    guide's seasons and a disc can hold the end of one season and the start of the next.
    """
    key = _name_key(name)
    if not key:
        return None
    hits = []
    for e in episode_list or []:
        if e.get("special"):
            continue
        g = _name_key(e.get("name"))
        if not g:
            continue
        if key == g or (" part" not in " " + g and re.fullmatch(
                re.escape(g) + r"( part[ 0-9and]*)+", key)):
            hits.append((e["season"], e["number"]))
    here = [h for h in hits if h[0] == season]
    for group in (here, hits):
        if len(group) == 1:
            return group[0]
        if group:
            return None
    return None


def _pin_typed_names(spans, season, episode_list, typed):
    """Give a row whose typed name is a guide episode that episode's number.

    A disc that isn't in guide order is corrected by naming what plays, and media
    servers read the number, not the name: a typed name filed under the counted number
    shows as the guide's episode at that number. All or nothing per disc: the pins are
    applied only if no two files would then claim one number.
    """
    pins = {}
    for r, _p, _span in spans:
        if int(r.get("title_index", -1)) in typed:
            n = _guide_number_for(r.get("episode_title"), season, episode_list)
            if n is not None:
                pins[id(r)] = n
    if not pins:
        return
    claimed = []
    for r, _p, _span in spans:
        if id(r) in pins:
            claimed.append(pins[id(r)])
        else:
            claimed.extend((r.get("season"), k)
                           for k in range(int(r["episode"]), int(r["episode_last"]) + 1))
    if len(claimed) != len(set(claimed)):
        return
    for r, _p, _span in spans:
        if id(r) in pins:
            r["season"], r["episode"] = pins[id(r)]
            r["episode_last"] = r["episode"]
            r["span"] = 1


# ── turning all of that into a list of files ─────────────────────────────────

def guide_names(episode_list, season):
    """{"number": name} for one season, so the page can rename rows as it renumbers them."""
    return {str(e["number"]): e.get("name") or "" for e in episode_list or []
            if season is not None and e["season"] == season and not e.get("special")}


def build_plan(found, season=None, first_episode=1, series=None, episode_list=None,
               dvd_map=None, fit_runtimes=False):
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
    plan = [{"title_index": t["index"],
             "seconds": t["seconds"],
             "bytes": t.get("bytes") or 0,
             "span": 2 if t["index"] in (found.get("doubles") or []) else 1,
             "source": t.get("source") or ""}
            for t in found["episodes"]]
    dvd_map = dvd_map or {}
    used = number_rows(plan, season, first_episode, episode_list, dvd_map,
                       fit_runtimes=fit_runtimes)

    return {
        # Kept in the plan, not re-fetched: renumbering after a correction, in the page
        # or on the box, has to map positions exactly as the first numbering did.
        "dvd_map": {str(k): v for k, v in dvd_map.items()} if used else {},
        "series": (series or {}).get("name") or "",
        "series_id": (series or {}).get("id"),
        "series_year": (series or {}).get("year"),
        "season": season,
        "order_source": found["order_source"],
        "confidence": found["confidence"],
        "play_all_index": (found.get("play_all") or {}).get("index"),
        "guide_names": guide_names(episode_list, season),
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
                   "twice (with and without the opening, or a “next time” trailer), "
                   "and the longer, complete version was kept."
                   % (len(found["duplicates"]),
                      "" if len(found["duplicates"]) == 1 else "s"))
    if found.get("play_all"):
        out.append("The “play all” title was excluded. It's every episode in "
                   "one file, and it's what gave us the episode order.")
    for idx in found.get("unsure_doubles") or []:
        out.append("Title %d is as long as the disc's other two episodes together. It "
                   "may be a double-length episode or a \u201cplay all\u201d, and this "
                   "disc can't say which, so it is kept. If it's a repeat of them, "
                   "untick it." % idx)
    if found.get("beyond_play_all"):
        n = len(found["beyond_play_all"])
        out.append("The disc's \u201cplay all\u201d stops short: %d episode%s after it %s "
                   "placed by where %s on the disc, which continues the same order."
                   % (n, "" if n == 1 else "s", "is" if n == 1 else "are",
                      "it sits" if n == 1 else "they sit"))
    for idx in found.get("outside_play_all") or []:
        secs = next((x.get("seconds", 0) for x in found.get("extras") or []
                     if x["index"] == idx), 0)
        out.append("Title %d is episode-length (%d min) but isn't part of the “play "
                   "all”, so it's listed under extras rather than numbered — usually a "
                   "bonus episode or a featurette. Tick it there to keep it."
                   % (idx, round(secs / 60.0)))
    for e in plan.get("episodes") or []:
        if int(e.get("span") or 1) > 1 and e.get("episode") is not None:
            out.append("Title %d runs two episodes long, so it is filed as one "
                       "two-episode file (E%02d-E%02d), which Plex and Jellyfin read as "
                       "both." % (e["title_index"], int(e["episode"]),
                                                   int(e["episode_last"])))
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
                           "the episode guide says has %d episodes. The starting episode is "
                           "probably wrong." % (plan["season"], last))
    if not any(e["episode_title"] for e in plan["episodes"]):
        out.append("No episode titles were found, so the files are numbered but not "
                   "named. Plex and Jellyfin will still match them.")
    return out
