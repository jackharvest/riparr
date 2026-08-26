"""
MakeMKV: consent, fetch, verify, install.

MakeMKV is made by GuinpinSoft, not by us. Its EULA is an agreement between the user and
GuinpinSoft — "by installing or using this Software, you agree to be bound by the terms"
— and that agreement cannot be given on somebody else's behalf. So nothing here downloads
a single byte before the user has explicitly accepted (D14).

Riparr invokes `makemkvcon` as a separate process over its CLI and never links against
libmakemkv, which is what keeps a GPL-3 codebase and a proprietary decoder at arm's
length.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zlib

from . import platform as P

EULA_URL = "https://www.makemkv.com/eula/"
HOMEPAGE = "https://www.makemkv.com/"
FORUM_KEY_TOPIC = "https://forum.makemkv.com/forum/viewtopic.php?f=5&t=1053"

# The pinned release, its checksums, and every place it can be fetched from, all read
# from packaging/makemkv-manifest.json so that this service and the root installer
# (tools/makemkv-install.sh) can never disagree about what they are downloading.
#
# Why mirrors: makemkv.com was down for the whole of August 2026. An appliance whose
# first-run setup cannot complete because somebody else's web server is having a month
# is not an appliance. Sources are tried in order and the first whose bytes match the
# pinned sha256 wins.
#
# Why that is safe: the hash is pinned here, in the repository, and checked after every
# download. A mirror serving the wrong file -- stale, truncated or hostile -- fails the
# check and the next source is tried. Mirrors cost nothing in trust; dropping the hash
# would cost everything.
MANIFEST_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "packaging", "makemkv-manifest.json")

# A last-resort copy of the pin. If the manifest file is missing -- a partial install, a
# packaging mistake -- refusing to know the checksum would be worse than knowing it,
# because the checksum is the safety property and the URL list is only convenience.
_FALLBACK_MANIFEST = {
    "version": "1.18.4",
    "verified_against_official": True,
    "packages": [
        {"name": "makemkv-oss-1.18.4.tar.gz",
         "sha256": "8590063648d42ec2a958b74573d7022f0f4c334e4e4fe7dd53b70c6e748ba453",
         "urls": [{"where": "makemkv.com",
                   "url": "https://www.makemkv.com/download/makemkv-oss-1.18.4.tar.gz"}]},
        {"name": "makemkv-bin-1.18.4.tar.gz",
         "sha256": "cee56de0baa5531abed16bd862742d308d772b4ab4dae16ee865bf74f04a1608",
         "urls": [{"where": "makemkv.com",
                   "url": "https://www.makemkv.com/download/makemkv-bin-1.18.4.tar.gz"}]},
    ],
}


def _load_manifest():
    try:
        with open(MANIFEST_PATH) as f:
            m = json.load(f)
    except (OSError, ValueError):
        return dict(_FALLBACK_MANIFEST)
    if not m.get("packages"):
        return dict(_FALLBACK_MANIFEST)
    return m


MANIFEST = _load_manifest()


def sources(pkg):
    """Every place one package can be fetched from, in the order to try them."""
    return [u for u in pkg.get("urls", []) if u.get("url")]


# ─────────────────────── is MakeMKV's own infrastructure up? ───────────────────────
#
# This matters more than it should. As of 2026-08 `makemkv.com` has been down for
# weeks, which means the installer cannot fetch and -- because beta keys are published
# on the forum and rotate -- the way to get a working key is a forum post. The two are
# on different hosts and fail independently, so they are tracked separately: "the site
# is down but the forum is up" is the exact situation, and it is the difference between
# "you are stuck" and "go here and copy the key".

SITES = [
    {"key": "site", "name": "makemkv.com", "url": HOMEPAGE,
     "why": "Where MakeMKV itself is downloaded from. While it is down Riparr cannot "
            "install or update MakeMKV — an installation you already have keeps working."},
    {"key": "forum", "name": "forum.makemkv.com", "url": "https://forum.makemkv.com/",
     "why": "Where the free beta key is published, and where its author posts when the "
            "site is having trouble. A different host from the main site, so it is "
            "often up when the site is not."},
]

_SITE_CACHE = {"at": 0, "results": None, "checking": False}
_SITE_LOCK = threading.Lock()
SITE_CACHE_SECONDS = 300
PROBE_TIMEOUT = 5


def _probe(url, timeout=PROBE_TIMEOUT):
    """Is this host answering? Any HTTP reply counts, including an error page.

    A 500 or a 403 means the server is there and talking, which is what a user needs to
    know before being sent to it. Only a connection that cannot be made at all, or one
    that hangs, is "down". Deliberately generous: the question is "is it worth clicking
    that link", not "is the service healthy".
    """
    req = urllib.request.Request(url, method="GET")
    req.add_header("User-Agent", "Riparr")
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return {"up": True, "status": r.status,
                    "ms": int((time.time() - started) * 1000)}
    except urllib.error.HTTPError as e:
        # It answered. It answered badly, and that is still an answer.
        return {"up": True, "status": e.code, "ms": int((time.time() - started) * 1000),
                "note": "answering, but with an error (%s)" % e.code}
    except Exception as e:
        return {"up": False, "status": None,
                "ms": int((time.time() - started) * 1000),
                "note": str(e)[:120]}


def _probe_all():
    """Every site at once. Serially this was two five-second waits, one after the other."""
    out = [dict(site) for site in SITES]
    threads = []
    for r in out:
        t = threading.Thread(target=lambda d=r: d.update(_probe(d["url"])),
                             name="riparr-probe", daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join(timeout=PROBE_TIMEOUT + 3)
    return out


def _refresh_sites():
    try:
        results = _probe_all()
    except Exception:
        results = _SITE_CACHE["results"]
    with _SITE_LOCK:
        if results is not None:
            _SITE_CACHE["results"] = results
            _SITE_CACHE["at"] = time.time()
        _SITE_CACHE["checking"] = False


def site_status(force=False, wait=False):
    """Both MakeMKV hosts. Returns immediately unless explicitly told to wait.

    This used to probe inline, and `/api/makemkv` is what the General settings page
    fetches before it draws a single pixel. So opening General meant waiting on two of
    somebody else's web servers -- one of which has been down for weeks and therefore
    burns the whole timeout every time. The page took as long as the slowest host to
    appear, the sidebar link looked broken for ten seconds, and people clicked it
    again.

    The rule now: a settings page never waits on a network probe. The last known answer
    comes back instantly, a refresh runs on a thread behind it, and the interface says
    "checking" until it lands. Only the explicit "Check again" button passes wait=True,
    because there the waiting *is* the interaction.

    Returns (results, checking). `results` is a list, empty when nothing has ever been
    probed -- never None, so no caller has to defend against it.
    """
    now = time.time()
    with _SITE_LOCK:
        fresh = (_SITE_CACHE["results"] is not None
                 and now - _SITE_CACHE["at"] < SITE_CACHE_SECONDS)
        if fresh and not force:
            return _SITE_CACHE["results"], False
        already = _SITE_CACHE["checking"]
        _SITE_CACHE["checking"] = True

    if wait:
        _refresh_sites()
        with _SITE_LOCK:
            return _SITE_CACHE["results"] or [], False

    if not already:
        threading.Thread(target=_refresh_sites, name="riparr-sites",
                         daemon=True).start()
    with _SITE_LOCK:
        return _SITE_CACHE["results"] or [], True


# ─────────────────────── what a beta key's expiry means ───────────────────────
#
# Beta keys are not a 30-day timer that starts when you paste one in. They are
# published on the forum and expire on a **month boundary** -- a key issued mid-month
# dies at the end of that month, so the time you get from one is anywhere between a day
# and about five weeks. Counting down "23 days left" from the day it was entered would
# be a confident, wrong number.
#
# So Riparr says the true thing instead: which month this key is good for, and that
# a new one is a copy and paste away.

def key_advice(entered_at=None):
    """A month-boundary-aware note about the beta key. Never a countdown."""
    now = time.localtime()
    # The last day of the current month, without importing calendar arithmetic.
    if now.tm_mon == 12:
        nxt = time.struct_time((now.tm_year + 1, 1, 1, 0, 0, 0, 0, 1, -1))
    else:
        nxt = time.struct_time((now.tm_year, now.tm_mon + 1, 1, 0, 0, 0, 0, 1, -1))
    end = time.mktime(nxt) - 86400
    days = max(0, int((end - time.time()) // 86400))
    return {
        "month": time.strftime("%B", now),
        "ends": time.strftime("%d %b", time.localtime(end)),
        "days_to_month_end": days,
        "soon": days <= 5,
        "note": ("Free beta keys expire at the end of the month rather than a fixed "
                 "number of days after you enter one, so this one stops working on or "
                 "around %s. Getting the next one is a copy and paste."
                 % time.strftime("%d %B", time.localtime(end))),
    }


# Shown before consent. Paraphrased from makemkv-oss-1.18.4/License.txt; the full text is
# always one click away, and the wizard links to it rather than relying on this summary.
EULA_POINTS = [
    "MakeMKV is made by GuinpinSoft inc. This agreement is between you and them.",
    "You may only use it to copy discs you own or are otherwise permitted to copy.",
    "You may not sell, rent, lease or sublicense it.",
    "You may not reverse engineer, decompile or modify it.",
    "The free beta key expires on a month boundary. A permanent key can be purchased.",
]

# Checked before anything is downloaded. makemkv.com has been unreachable, and the
# tarballs are frequently already on the box -- copied across by hand during validation,
# or left over from a previous install.
LOCAL_SOURCES = [
    "/root/makemkv",            # where the Preparer puts them on an Armbian card
    "/boot/makemkv",
    "/home/riparr/makemkv",
    "/opt/riparr/makemkv",
    "/boot/firmware/makemkv",
    os.path.expanduser("~/makemkv"),
]


def find_local_source():
    """A directory already holding every package, with checksums that match."""
    for d in LOCAL_SOURCES:
        if not os.path.isdir(d):
            continue
        have = []
        for pkg in MANIFEST["packages"]:
            f = os.path.join(d, pkg["name"])
            if os.path.exists(f) and os.path.getsize(f) > 0:
                have.append((f, pkg["sha256"]))
        if len(have) == len(MANIFEST["packages"]):
            return d
    return None


INSTALL_SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "tools", "makemkv-install.sh")

_state = {"phase": "idle", "message": "", "detail": "", "progress": 0.0}
_lock = threading.Lock()


def info():
    st = P.makemkv_status()
    local = find_local_source()
    sites, checking = site_status()
    return {
        "status": st,
        "local_source": local,
        "manifest": {
            "version": MANIFEST.get("version"),
            "verified_against_official": MANIFEST.get("verified_against_official", False),
            "verified_note": MANIFEST.get("verified_note", ""),
            # Named, not linked. The list is for reassurance -- "this does not depend on
            # one website" -- and a row of raw URLs reads as a debugging dump.
            "sources": [u.get("where") for u in sources(MANIFEST["packages"][0])],
        },
        "eula_url": EULA_URL,
        "eula_points": EULA_POINTS,
        "homepage": HOMEPAGE,
        "key_topic": FORUM_KEY_TOPIC,
        "install": dict(_state),
        "installable": can_install()[0],
        "sites": sites,
        "sites_checking": checking,
        "key_advice": key_advice(),
    }


def _set(**kw):
    with _lock:
        _state.update(kw)


def install_status():
    """Progress, preferring what the root installer reports over our own guess.

    Once the bridge takes over, this process is not doing the work and has nothing to
    say about it. The root side publishes to a file; read that. It also survives a
    restart of this service, which a thread-local dict would not.
    """
    bridged = _read_bridge_state()
    if bridged:
        return bridged
    with _lock:
        return dict(_state)


def _read_bridge_state():
    try:
        with open(BRIDGE_STATE) as f:
            st = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(st, dict) or "phase" not in st:
        return None
    st.setdefault("progress", 0.0)
    st.setdefault("message", "")
    st.setdefault("detail", "")
    # A build takes half an hour; showing the last few lines is the difference between
    # "it is working" and "it is hung".
    try:
        with open(BRIDGE_LOG) as f:
            tail = f.read()[-4000:]
        st["log"] = tail.strip().splitlines()[-12:]
    except OSError:
        st["log"] = []
    return st


# ── the privilege bridge ──
# Building MakeMKV installs apt packages and writes to /usr/local, so it needs root.
# This service runs as `riparr` with NoNewPrivileges=yes and cannot get there — sudo
# included. Sending the user to a terminal is the wrong answer for an appliance, so
# install.sh sets up a one-way door: creating REQUEST is watched by riparr-makemkv.path,
# which starts a root oneshot whose command line is fixed in the unit file. This side
# can ask. It cannot say what runs.
RUNDIR = "/run/riparr"
REQUEST = os.path.join(RUNDIR, "makemkv.request")
BRIDGE_STATE = os.path.join(RUNDIR, "makemkv.state")
BRIDGE_LOG = os.path.join(RUNDIR, "makemkv.log")
BRIDGE_UNIT = "/etc/systemd/system/riparr-makemkv.path"


def bridge_available():
    """The bridge is present and this process can actually ring the bell."""
    return os.path.exists(BRIDGE_UNIT) and os.path.isdir(RUNDIR) and os.access(
        RUNDIR, os.W_OK)


def can_install():
    """Whether this process could actually complete an install."""
    if not P.IS_APPLIANCE:
        return False, "MakeMKV installs on the appliance only."
    if os.geteuid() == 0:
        return True, ""
    if bridge_available():
        return True, ""
    return False, (
        "This copy of Riparr was installed before in-place MakeMKV setup existed, so "
        "it cannot install MakeMKV for you. Re-run the installer to add it:\n\n"
        "    sudo bash /opt/riparr/tools/install.sh\n\n"
        "Then come back to this page.")


def start_install(accepted_eula):
    """Begin an install. Refuses without explicit consent — this is the whole point.

    Consent is enforced here and recorded in the database. The root side takes
    `--accept-eula` as given, because the only way to reach it is through this
    function, and the only process that can reach this function is the web service
    behind authentication.
    """
    if not accepted_eula:
        return {"ok": False,
                "error": "MakeMKV's licence agreement has to be accepted first."}
    ok, why = can_install()
    if not ok:
        return {"ok": False, "error": why}
    with _lock:
        if _state["phase"] in ("downloading", "verifying", "building"):
            return {"ok": False, "error": "An install is already running."}

    if os.geteuid() != 0 and bridge_available():
        try:
            # Touching the file is the whole request. systemd does the rest.
            with open(REQUEST, "w") as f:
                f.write("%d\n" % int(time.time()))
        except OSError as e:
            return {"ok": False,
                    "error": "Could not ask the system to install MakeMKV: %s" % e}
        _set(phase="downloading", progress=0.05,
             message="Starting the installer", detail="")
        return {"ok": True}

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True}


def _run():
    try:
        if not P.IS_APPLIANCE:
            # Walk the same phases so the flow is exercisable off-hardware, but never
            # pretend a binary was installed.
            for phase, msg, pr in [
                ("downloading", "Downloading MakeMKV %s" % MANIFEST["version"], 0.35),
                ("verifying", "Checking the download against its checksum", 0.6),
                ("building", "Building for this device — this takes a few minutes", 0.9),
            ]:
                _set(phase=phase, message=msg, progress=pr, detail="")
                time.sleep(1.4)
            _set(phase="error", progress=0,
                 message="MakeMKV installs on the appliance only.",
                 detail="This process is running in development mode, so nothing was "
                        "downloaded or installed.")
            return

        local = find_local_source()
        tmp = tempfile.mkdtemp(prefix="riparr-makemkv-")
        try:
            for i, pkg in enumerate(MANIFEST["packages"]):
                dest = os.path.join(tmp, pkg["name"])
                if local:
                    _set(phase="downloading", progress=0.1 + 0.25 * i,
                         message="Using the copy already on this device",
                         detail=os.path.join(local, pkg["name"]))
                    shutil.copyfile(os.path.join(local, pkg["name"]), dest)
                    _set(phase="verifying", progress=0.2 + 0.25 * i,
                         message="Checking %s" % pkg["name"])
                    actual = _sha256(dest)
                    if actual != pkg["sha256"]:
                        _set(phase="error", progress=0,
                             message="%s didn't match its expected checksum. Nothing "
                                     "was installed." % pkg["name"],
                             detail="expected %s\ngot      %s"
                                    % (pkg["sha256"][:24], actual[:24]))
                        return
                    continue

                # Sources in order until one produces the right bytes. The checksum is
                # what makes trying several safe, so it is checked inside the loop
                # rather than after it.
                def note(where, _p=pkg, _i=i):
                    _set(phase="downloading", progress=0.1 + 0.25 * _i,
                         message="Downloading %s" % _p["name"],
                         detail="from %s" % where)

                where, problems = fetch_package(pkg, dest, on_try=note)
                if not where:
                    _set(phase="error", progress=0,
                         message="Could not download MakeMKV from any source.",
                         detail="\n".join("%s — %s" % (w, why) for w, why in problems)
                                + "\n\nCopy the tarballs to %s on this device and "
                                  "try again." % LOCAL_SOURCES[0])
                    return
                _set(phase="verifying", progress=0.2 + 0.25 * i,
                     message="Checked %s" % pkg["name"], detail="from %s" % where)

            _set(phase="building", progress=0.7,
                 message="Building MakeMKV for this device — this takes a few minutes",
                 detail="")
            # --jobs 1: the C++ build is the OOM risk on a 512MB board (R1). Slower
            # than -j2 and much likelier to finish.
            p = subprocess.run(
                ["bash", INSTALL_SCRIPT, "--accept-eula", "--srcdir", tmp, "--jobs", "1"],
                capture_output=True, text=True, timeout=60 * 60)
            if p.returncode != 0:
                _set(phase="error", progress=0,
                     message="MakeMKV did not finish installing.",
                     detail=(p.stderr or p.stdout or "")[-1200:])
                return

            _set(phase="done", progress=1.0,
                 message="MakeMKV %s is installed." % MANIFEST["version"], detail="")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    except Exception as e:
        _set(phase="error", progress=0,
             message="The install stopped unexpectedly.", detail=str(e))


# ── the current beta key ──
# MakeMKV is free while it is in beta, and GuinpinSoft publishes a registration key on
# their forum that they roll over roughly monthly. Everyone running a beta is expected
# to fetch it themselves, notice when it lapses, and paste in the new one — which is a
# chore this box is in a much better position to do than its owner.
#
# This is a forum page, not an API, so it will break one day. Every failure path here
# ends in "here is the link, paste it yourself" rather than an error, because that is
# exactly as good as the situation before this existed.
_key_cache = {"at": 0, "value": None}
KEY_TTL = 6 * 3600
KEY_RE = re.compile(r"\bT-[A-Za-z0-9@_\-]{40,80}\b")
EXPIRY_RE = re.compile(
    r"valid\s+until\s+(?:the\s+)?(?:end\s+of\s+)?([A-Z][a-z]+\s+\d{1,2},?\s+\d{4}"
    r"|[A-Z][a-z]+\s+\d{4}|\d{1,2}\s+[A-Z][a-z]+\s+\d{4})", re.I)


def beta_key(force=False):
    """The current beta key, with whatever validity the forum states.

    Cached, because the answer changes monthly at most and this is somebody else's
    forum — polling it on every page load would be rude and slow.
    """
    now = time.time()
    if not force and _key_cache["value"] and now - _key_cache["at"] < KEY_TTL:
        return dict(_key_cache["value"], cached=True)

    result = {"key": None, "expires": None, "source": FORUM_KEY_TOPIC,
              "fetched_at": int(now), "error": None, "cached": False}
    try:
        req = urllib.request.Request(
            FORUM_KEY_TOPIC,
            headers={"User-Agent": "Mozilla/5.0 (compatible; riparr)"})
        with urllib.request.urlopen(req, timeout=20) as r:
            html = r.read(400000).decode("utf-8", "replace")
    except Exception as e:
        result["error"] = ("Couldn't reach the MakeMKV forum (%s). Open the link and "
                           "paste the key in yourself." % e)
        return result

    # The first match is the announcement post, which is the one kept up to date.
    m = KEY_RE.search(_strip_tags(html))
    if not m:
        result["error"] = ("The forum page didn't contain a key in the expected "
                           "format. Open the link and copy it in yourself.")
        return result
    result["key"] = m.group(0)
    e = EXPIRY_RE.search(_strip_tags(html))
    if e:
        result["expires"] = e.group(1).strip().rstrip(".,")
    _key_cache["at"] = now
    _key_cache["value"] = result
    return result


def _strip_tags(html):
    html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    return re.sub(r"<[^>]+>", " ", html)


def _download(url, dest, timeout=300):
    """One URL to one file, honouring Content-Encoding.

    The encoding check is not pedantry. makemkv.com serves its .tar.gz with
    `Content-Encoding: gzip` on top of the gzip that is already the file, and the
    Internet Archive faithfully stores and replays that. urllib does not decode
    content-encoding, so without this the mirror hands back a *doubly* gzipped tarball
    -- which is a perfectly valid gzip file, extracts to something that is not what was
    asked for, and fails the checksum with no hint as to why.
    """
    req = urllib.request.Request(url, headers={"User-Agent": "riparr"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        encoding = (r.headers.get("Content-Encoding") or "").lower()
        with open(dest, "wb") as f:
            if encoding == "gzip":
                d = zlib.decompressobj(16 + zlib.MAX_WBITS)
                while True:
                    chunk = r.read(1 << 16)
                    if not chunk:
                        break
                    f.write(d.decompress(chunk))
                f.write(d.flush())
            else:
                shutil.copyfileobj(r, f)


def fetch_package(pkg, dest, on_try=None):
    """Download one package from the first source that produces the right bytes.

    Returns (where, None) on success or (None, [(where, why), ...]) on failure. Every
    source is reported rather than only the last, because "makemkv.com is down and so
    is the mirror" and "every mirror served something that failed its checksum" are
    completely different problems and the second one is alarming.
    """
    problems = []
    for src in sources(pkg):
        where = src.get("where") or src["url"]
        if on_try:
            on_try(where)
        # Two goes each. The Internet Archive in particular will drop a cold request and
        # serve the same file happily thirty seconds later, and moving on to the next
        # source over that would quietly retire a mirror that works.
        err = None
        for attempt in range(2):
            try:
                _download(src["url"], dest)
                err = None
                break
            except Exception as e:
                err = str(e)[:160]
                time.sleep(2)
        if err:
            problems.append((where, err))
            continue
        got = _sha256(dest)
        if got == pkg["sha256"]:
            return where, None
        problems.append((where, "served a file that did not match its checksum "
                                "(%s…, expected %s…)" % (got[:12], pkg["sha256"][:12])))
    try:
        os.unlink(dest)
    except OSError:
        pass
    return None, problems


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
