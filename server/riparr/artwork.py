"""Cover art for the disc in the tray, when we are sure enough to show it.

Why this exists: the box knows the disc's volume label and nothing else. Putting the
matching poster faintly behind the page is the difference between "a disc is present"
and "I know what you just put in" -- the same trick Plex uses, and it costs nothing.

Three decisions worth keeping:

**Wikipedia, not TMDB.** TMDB has better artwork and wide backdrops, but it needs an API
key, which means every owner of this box would have to register for one before a
decorative background worked. Wikipedia's search API needs no key, is a single
well-known endpoint, and hands back a canonical article title -- which is what makes the
confidence check below possible at all. `pilicense=any` is required: film posters are
non-free, so the default free-only filter returns nothing.

**A wrong poster is much worse than no poster.** "The Boss Baby" and "The Boss Baby:
Family Business" are one search apart, and showing the sequel's art behind the original
is the kind of detail that makes a product feel careless. So the normalised article
title has to match the normalised disc label to THRESHOLD, and anything short of that
shows nothing at all. Silence is a perfectly good answer here.

**The image is proxied, never linked.** Handing the browser a remote URL would leak the
viewer's address to Wikimedia and tell them what film is being ripped. Fetching it on
the box costs one request and keeps the page self-contained. The client never gets to
name the URL -- it gets an opaque token for a URL *we* resolved -- because an endpoint
that fetches whatever a caller asks for is an open proxy sitting inside the LAN.
"""
import difflib
import io
import os
import re
import threading
import time
import urllib.parse
import urllib.request

SEARCH = "https://en.wikipedia.org/w/api.php"
UA = "riparr/0.1 (+https://github.com/jackharvest/riparr) python-urllib"

# Below this, show nothing. Deliberately strict -- see the module docstring.
THRESHOLD = 0.95

# Only these hosts may ever be fetched by the image proxy.
ALLOWED_HOSTS = {"upload.wikimedia.org"}

# Volume labels that identify nothing. Searching for these returns confident nonsense.
GENERIC = {
    "dvd_video", "dvdvideo", "dvd", "bluray", "blu_ray", "bd_rom", "bdrom",
    "logical_volume_id", "untitled", "unknown", "video_ts", "no_label", "disc",
    "movie", "film", "cd_rom", "cdrom", "data", "video", "video_ts", "audio_ts",
}

# Junk that rides along on retail volume labels.
# Years are deliberately NOT stripped. "Blade Runner 2049" and "1917" carry one in the
# title, and removing it turned the first into "blade runner", which then matched the
# 1982 film at full confidence -- a confidently wrong poster, the exact failure this
# feature must not have. Leaving the year in costs a few false negatives (a label like
# THE_MATRIX_1999 now scores too low to show anything) and that is the safe direction.
_STRIP = re.compile(
    r"\b(disc|disk|d)\s*\d+\b|"
    r"\b(ntsc|pal|widescreen|fullscreen|ws|fs|se|ce|uncut|unrated|rated|extended|"
    r"special|collectors?|edition|anniversary|remastered|bonus|feature|main|"
    # No bare-number alternative here either: it ate "2049" and reduced "1917" to
    # nothing. Disc numbers are already handled by the first branch.
    r"dvd|bluray|blu|ray|bd|uhd|4k|hd|sd|region)\b", re.I)


def normalize(label):
    """A retail volume label into something searchable.

    `THE_BOSS_BABY` -> `the boss baby`. Labels are upper-case, underscore-separated and
    frequently carry disc numbers and edition noise, none of which helps a search and
    all of which drags the match score down.
    """
    s = re.sub(r"[_\.\-]+", " ", (label or "")).strip()
    s = _STRIP.sub(" ", s)
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


def _score(a, b):
    """How alike two titles are, ignoring a leading article and punctuation."""
    def key(x):
        x = re.sub(r"^(the|a|an)\s+", "", x.strip().lower())
        return re.sub(r"[^a-z0-9]+", " ", x).strip()
    return difflib.SequenceMatcher(None, key(a), key(b)).ratio()


def _get(url, timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(6 * 1024 * 1024), r.headers.get("Content-Type", "")


# token -> (url, content_type, bytes, fetched_at). Bounded; this is decoration, and a
# box that rips discs all day must not grow a picture cache in RAM for ever.
_images = {}
_images_lock = threading.Lock()
# Sized for the rips gallery rather than the one disc in the tray. Posters are ~70-150
# KB each, so this is a couple of megabytes at worst on a box with 969 MB.
MAX_IMAGES = 48
IMAGE_TTL = 6 * 3600


def _remember(url):
    token = "%08x" % (abs(hash(url)) & 0xFFFFFFFF)
    with _images_lock:
        now = time.time()
        for k in [k for k, v in _images.items() if now - v[3] > IMAGE_TTL]:
            _images.pop(k, None)
        while len(_images) >= MAX_IMAGES:
            _images.pop(min(_images, key=lambda k: _images[k][3]), None)
        _images.setdefault(token, (url, None, None, time.time()))
    return token


def image_bytes(token):
    """(bytes, content_type) for a token this process issued, or (None, None).

    The fetch happens here rather than at match time so a page that never renders the
    backdrop costs no bandwidth.
    """
    with _images_lock:
        entry = _images.get(token)
    if not entry:
        return None, None
    url, ctype, blob, at = entry
    if blob is not None:
        return blob, ctype
    host = urllib.parse.urlparse(url).hostname or ""
    if host not in ALLOWED_HOSTS:
        return None, None
    try:
        blob, ctype = _get(url, timeout=20)
    except Exception:
        return None, None
    if not ctype.startswith("image/"):
        return None, None
    with _images_lock:
        _images[token] = (url, ctype, blob, time.time())
    return blob, ctype


_lookup_cache = {}


def look_up(label):
    """Best artwork match for a disc label, or None.

    Returns {"title", "confidence", "token"} only when confident.
    """
    name = normalize(label)
    if not name or name.replace(" ", "_") in GENERIC or name in GENERIC or len(name) < 3:
        return None
    if name in _lookup_cache:
        return _lookup_cache[name]

    params = {
        "action": "query", "generator": "search",
        "gsrsearch": "%s film" % name, "gsrlimit": "5",
        "prop": "pageimages", "piprop": "original|thumbnail",
        "pithumbsize": "1000", "pilicense": "any",
        "format": "json", "formatversion": "2",
    }
    try:
        body, _ = _get("%s?%s" % (SEARCH, urllib.parse.urlencode(params)))
        import json
        pages = json.loads(body.decode("utf-8", "replace")).get(
            "query", {}).get("pages") or []
    except Exception:
        return None                      # offline, rate-limited: show nothing

    best = None
    for p in pages:
        title = re.sub(r"\s*\(.*?\)\s*$", "", p.get("title") or "")   # drop "(film)"
        src = ((p.get("original") or {}).get("source")
               or (p.get("thumbnail") or {}).get("source") or "")
        if not src or src.lower().endswith(".svg"):
            continue                     # logos, not posters
        s = _score(name, title)
        if best is None or s > best[0]:
            best = (s, title, src.split("?")[0])

    if not best or best[0] < THRESHOLD:
        _lookup_cache[name] = None
        return None
    result = {"title": best[1], "confidence": round(best[0], 3),
              "token": _remember(best[2])}
    _lookup_cache[name] = result
    return result
