"""
The Riparr service. API-first: the web UI is just the first client of this API (D2),
which is what makes Homepage widgets and multi-unit setups nearly free later.
"""
import json
import os
import threading
import time

from fastapi import (FastAPI, File, HTTPException, Request, Response, Depends,
                     UploadFile)
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from itsdangerous import URLSafeTimedSerializer, BadSignature

from . import (__version__, artwork as ART, db, drives as DRV, led as LED,
               makemkv as MK,
               notify as NT, platform as P, rip as RIP, shares as SH, system as SY,
               updater)

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
COOKIE = "riparr_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 30       # 30 days, matched to the cookie's Max-Age

# The interactive API docs and the schema they read describe every endpoint, so on the
# shipped box — which sits on an untrusted LAN behind one login — they are turned off
# rather than handed to anyone who asks before signing in. They stay on in development
# (MOCK mode) where they are useful and the box is a laptop, not an appliance.
_DOCS = None if P.IS_APPLIANCE else "/api/docs"
_OPENAPI = None if P.IS_APPLIANCE else "/api/openapi.json"

app = FastAPI(title="Riparr", version=__version__, docs_url=_DOCS,
              openapi_url=_OPENAPI)


def _secret():
    s = db.get("session_secret")
    if not s:
        s = db.set("session_secret", os.urandom(32).hex())
    return s


def _rotate_secret():
    """Mint a fresh session secret, which invalidates every cookie signed with the old
    one. This is how a password change logs out other devices: the sessions are
    stateless (nothing to delete), so revocation is done by changing the key they were
    signed under."""
    return db.set("session_secret", os.urandom(32).hex())


# ─────────────────────────────── password recovery ───────────────────────────────

RESET_FILENAMES = ("riparr-reset", "riparr-reset.txt")


def _check_password_reset():
    """Honour a reset file left on the boot partition.

    A forgotten password used to cost a re-flash of a perfectly healthy box: there is
    no console, no email, and opening the reset to the network would be a hole in the
    one thing standing between this appliance and everyone else on the Wi-Fi.

    A file on the boot partition is the right key for this lock. It requires the card
    in your hand, which is the same proof of ownership as re-flashing it and vastly
    less destructive -- settings, shares and disc history all survive. The file is
    deleted as it is honoured, so a card that is put back keeps working normally.
    """
    d = P.boot_dir()
    if not d:
        return
    for name in RESET_FILENAMES:
        path = os.path.join(d, name)
        if not os.path.exists(path):
            continue
        try:
            os.unlink(path)               # consumed first: never loop on a failure
        except OSError as e:
            SY.component("Setup").error("Found %s but couldn't remove it: %s", path, e)
            return
        db.clear_users()
        db.set("setup_complete", False)
        SY.component("Setup").warning(
            "Password reset requested from %s. The account has been cleared; the next "
            "person to open the web interface will be asked to create one.", path)
        return


@app.on_event("startup")
def _startup():
    db.init()
    _secret()
    SY.init()
    _check_password_reset()
    SY.start_scheduler()
    RIP.start()
    LED.start()


# ─────────────────────────────── auth ───────────────────────────────

def _serializer():
    return URLSafeTimedSerializer(_secret(), salt="riparr-session")


def current_user(request: Request):
    raw = request.cookies.get(COOKIE)
    if not raw:
        return None
    try:
        # max_age turns the timestamp the token already carried — and never checked —
        # into a real expiry: a cookie older than the window is refused as a bad
        # signature, so a leaked one does not stay valid forever. SignatureExpired is a
        # subclass of BadSignature, so both land here.
        return _serializer().loads(raw, max_age=SESSION_MAX_AGE).get("u")
    except BadSignature:
        return None


def require_user(request: Request):
    """During first run there is no account yet, so the API stays open until there is.

    Once an account exists the box is closed — it must never sit on someone's network
    unprotected after setup.
    """
    if not db.has_users():
        return "__setup__"
    u = current_user(request)
    if not u:
        raise HTTPException(status_code=401, detail="Not signed in")
    return u


class Login(BaseModel):
    username: str
    password: str


# ── login throttle ──
# The box is single-user and sits on a LAN, so a wrong password is either a typo or a
# guessing run. This slows the second case without punishing the first: the delay is
# applied *before* the response and grows with the run of recent failures, so a few
# fat-fingered tries cost nothing perceptible while a script hits an escalating wall.
#
# It is a delay, not a lockout, and on purpose. A hard lockout on the only account of a
# headless appliance is a denial-of-service an attacker can trigger against the owner;
# making them wait a few seconds cannot lock anyone out of their own box.
_LOGIN_LOCK = threading.Lock()
_login_fails = 0            # consecutive failures across the box; reset on any success
_LOGIN_DELAY_CAP = 5.0      # seconds; the longest anyone ever waits
_LOGIN_ALERT_AT = 5         # failures before it becomes a logged security event


def _login_delay():
    with _LOGIN_LOCK:
        n = _login_fails
    if n <= 0:
        return 0.0
    return min(_LOGIN_DELAY_CAP, 0.5 * (2 ** (n - 1)))


def _login_failed(username):
    global _login_fails
    with _LOGIN_LOCK:
        _login_fails += 1
        n = _login_fails
    if n == _LOGIN_ALERT_AT:
        SY.component("Auth").warning(
            "%d failed sign-ins in a row (most recent for '%s'). If this wasn't you, "
            "someone on the network may be guessing the password.", n, username)


def _login_succeeded():
    global _login_fails
    with _LOGIN_LOCK:
        _login_fails = 0


@app.post("/api/auth/login")
def login(body: Login, response: Response):
    time.sleep(_login_delay())            # pay the accumulated cost before answering
    if not db.verify_user(body.username, body.password):
        _login_failed(body.username)
        raise HTTPException(status_code=401, detail="Wrong username or password")
    _login_succeeded()
    token = _serializer().dumps({"u": body.username, "t": int(time.time())})
    response.set_cookie(COOKIE, token, httponly=True, samesite="lax",
                        max_age=SESSION_MAX_AGE)
    return {"ok": True, "username": body.username}


@app.post("/api/auth/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE)
    return {"ok": True}


@app.get("/api/auth/me")
def me(request: Request):
    return {"username": current_user(request), "has_users": db.has_users()}


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


@app.post("/api/auth/password")
def change_password(body: PasswordChange, request: Request, response: Response,
                    user=Depends(require_user)):
    if not db.verify_user(user, body.current_password):
        raise HTTPException(status_code=400, detail="Current password is wrong")
    if len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="Use at least 8 characters")
    db.set_password(user, body.new_password)
    # Changing the password logs out everywhere else. Rotating the signing secret
    # invalidates every existing cookie; re-issuing this one keeps the person who just
    # changed it signed in on this device, which is the expected behaviour.
    _rotate_secret()
    token = _serializer().dumps({"u": user, "t": int(time.time())})
    response.set_cookie(COOKIE, token, httponly=True, samesite="lax",
                        max_age=SESSION_MAX_AGE)
    return {"ok": True}


# ─────────────────────────── first run ───────────────────────────

class SetupUser(BaseModel):
    username: str
    password: str


@app.get("/api/setup/state")
def setup_state():
    """What the setup wizard needs to know before anyone has signed in.

    Unauthenticated by necessity -- it is what the page asks to find out whether an
    account exists yet. That makes everything it returns public to anything that can
    reach the port, so the share comes back as the four fields the wizard actually
    draws. It used to return `db.default_share()` whole, which includes the SMB
    **password in the clear**: an unauthenticated GET handed out the credentials to
    the user's NAS.
    """
    share = db.default_share()
    return {
        "has_users": db.has_users(),
        "complete": bool(db.get("setup_complete")),
        "makemkv": P.makemkv_status(),
        "share": None if not share else {
            "id": share["id"], "name": share["name"],
            "host": share["host"], "path": share["path"],
            "verified_at": share["verified_at"],
        },
        "hostname": P.hostname(),
        "version": __version__,
    }


@app.post("/api/setup/user")
def setup_user(body: SetupUser, response: Response):
    if db.has_users():
        raise HTTPException(status_code=400, detail="An account already exists")
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="Use at least 8 characters")
    db.create_user(body.username, body.password)
    token = _serializer().dumps({"u": body.username, "t": int(time.time())})
    response.set_cookie(COOKIE, token, httponly=True, samesite="lax",
                        max_age=SESSION_MAX_AGE)
    return {"ok": True}


@app.post("/api/setup/complete")
def setup_complete(user=Depends(require_user)):
    db.set("setup_complete", True)
    return {"ok": True}


# ─────────────────────────────── status ───────────────────────────────

# Typical payload of the largest disc of each kind, for expressing capacity in discs.
DISC_BYTES = {"dvd": 8 * 2**30, "bluray": 25 * 2**30, "uhd": 66 * 2**30}
WINDOW_BYTES = RIP.WINDOW_BYTES   # the streaming window (D11) — one definition, not two


DISC_NAMES = {"uhd": ("4K UHD disc", "4K UHD discs"),
              "bluray": ("Blu-ray", "Blu-rays"),
              "dvd": ("DVD", "DVDs")}
DISC_ORDER = ("uhd", "bluray", "dvd")


def _capacity(free_bytes):
    """Capacity, in the terms the engine actually operates in.

    This used to describe D11 as designed rather than as built, and the two disagree.
    D11 says a buffer too small for the next disc means stream mode, not refusal — but
    follow-copy is not built (D22), so `rip._plan_transfer()` **refuses**. This
    function was reporting `mode: "stream"` and the sentence "discs are never refused
    for space" in precisely the case where the engine would refuse the next disc put
    in the tray. A status page that contradicts the engine is worse than one that says
    something disappointing.

    So the mode is now read off the same seam `_plan_transfer` branches on, and the
    two cannot drift: when `supports_follow_copy` goes True, both switch together.

    The window is also subtracted before counting discs, which it never was. On a card
    with 16 GB free the old arithmetic promised two DVDs and the engine allowed one.

    "Room for 1 more disc" was true and useless: a disc is anywhere from 8 to 66 GB,
    so the number silently meant Blu-ray and was wrong by a factor of eight for a DVD.
    Count each kind and say which is which.
    """
    streaming = SH.Transport.supports_follow_copy
    usable = max(0, free_bytes - WINDOW_BYTES)
    by_kind = {k: int(usable // v) for k, v in DISC_BYTES.items()}
    discs = by_kind["bluray"]

    if free_bytes < WINDOW_BYTES:
        mode, phrase = "degraded", "Not enough room to rip safely"
    elif any(by_kind[k] for k in DISC_ORDER):
        mode = "burst"
        parts = []
        for k in DISC_ORDER:
            n = by_kind[k]
            if not n:
                continue
            one, many = DISC_NAMES[k]
            parts.append("%d %s" % (n, one if n == 1 else many))
        phrase = "Room for " + parts[0]
        if len(parts) > 1:
            phrase += " — or " + ", or ".join(parts[1:])
    elif streaming:
        mode, phrase = "stream", "Streaming — discs are never refused for space"
    else:
        # Room to work in, but not room for a whole disc of any kind, and no
        # follow-copy to rescue it. Say what will happen, because it is about to.
        mode, phrase = "full", "Not enough room for another disc — let the queue drain"

    return {"discs_free": discs, "by_kind": by_kind, "mode": mode, "phrase": phrase,
            "streaming": streaming,
            "disc_names": {k: list(v) for k, v in DISC_NAMES.items()},
            "window_bytes": WINDOW_BYTES}


@app.get("/api/status")
def status(user=Depends(require_user)):
    storage = P.storage_status()
    # The user should never see a gigabyte: capacity is expressed in discs and mode.
    return {
        "version": __version__,
        "hostname": P.hostname(),
        "system": P.system_status(),
        "storage": dict(storage, **_capacity(storage["free_bytes"])),
        "optical": P.optical_diagnosis(),
        "clock": P.clock_status(),
        "wifi": P.wifi_status(),
        "makemkv": P.makemkv_status(),
        "drives": _drive_report(),
        # Four fields, not the row. The row carries the SMB password, and the browser
        # has never needed it -- same argument as /api/setup/state, which was handing
        # it out to anyone at all.
        "share": _share_out(db.default_share()),
        "setup_complete": bool(db.get("setup_complete")),
        "autorip": _autorip_state(),
        # A refused duplicate leaves no job and no file, so this is the only trace of
        # it. The page turns it into "you already ripped this, here it is".
        "duplicate": RIP.pending_duplicate(),
        "library": P.library_status(),
        "led": {"detected": LED.available(), "state": LED.current_state(),
                "device": LED.SPI_DEV},
    }


def _share_out(share):
    if not share:
        return None
    return {"id": share["id"], "name": share["name"], "host": share["host"],
            "path": share["path"], "verified_at": share["verified_at"]}


@app.post("/api/drive/eject")
def drive_eject(user=Depends(require_user)):
    return P.eject()


@app.post("/api/drive/close")
def drive_close(user=Depends(require_user)):
    ok, message = P.close_tray()
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    return {"ok": True, "message": message}


class SpeedTest(BaseModel):
    pass


@app.post("/api/storage/speedtest")
def storage_speedtest(body: SpeedTest = SpeedTest(), user=Depends(require_user)):
    """Measure the card, and recommend a transfer mode from what comes back.

    The recommendation is the point. "Direct is probably better" is an opinion; "your
    card writes at 9.4 MB/s and your library takes 18, so staging on the card makes
    every rip slower" is a reason. Somebody with a fast card and a weak link gets the
    opposite advice from the same code.
    """
    if db.active_job():
        raise HTTPException(status_code=400,
                            detail="Riparr is working on a disc — this would fight it "
                                   "for the card. Try again when it has finished.")
    card = P.card_speed()
    lib = P.library_status()
    result = {"card": card, "library": lib}
    db.set("card_speed", card)

    w = card.get("write_mbs")
    if not w:
        result["recommend"] = None
        result["why"] = "Riparr couldn't measure the card."
        return result
    if not lib.get("mounted"):
        result["recommend"] = "auto"
        result["why"] = ("Your card writes at about %s MB/s. Writing straight to your "
                         "library needs the share mounted, which it isn't yet." % w)
        return result
    # No network measurement here: it would mean writing a test file into somebody's
    # library, and the honest comparison is against what this box has actually done.
    result["recommend"] = "direct" if w < 15 else "auto"
    result["why"] = (
        ("Your card writes at about %s MB/s, which is slower than this box's network. "
         "Writing straight to your library will be faster, and it removes the card as "
         "a size limit." % w)
        if w < 15 else
        ("Your card writes at about %s MB/s, which is quick enough that staging on it "
         "costs little — and caching means a rip survives the network dropping out "
         "mid-disc." % w))
    return result


@app.post("/api/duplicate/ack")
def duplicate_ack(user=Depends(require_user)):
    """The interface has shown the user their already-ripped disc. Stop pointing."""
    RIP.ack_duplicate()
    return {"ok": True}


class SignalTest(BaseModel):
    mode: str = "flash"


@app.post("/api/drive/signal-test")
def drive_signal_test(body: SignalTest = SignalTest(), user=Depends(require_user)):
    """Fire the "already ripped" signal on demand, with a disc in the tray.

    Nobody can see the drive's light from inside the software, so the only way to know
    whether the blink works on a given drive is for a person to watch it happen. This
    is the button that lets them, without having to find a duplicate disc first.
    """
    if body.mode not in ("flash", "tray", "both"):
        raise HTTPException(status_code=400, detail="Unknown signal.")
    if db.active_job():
        raise HTTPException(status_code=400,
                            detail="Riparr is working on a disc — this would fight it "
                                   "for the drive. Try again when it has finished.")
    d = next((x for x in P.optical_drives() if x.get("present")), None)
    if body.mode in ("flash", "both") and not d:
        raise HTTPException(status_code=400,
                            detail="Put a disc in first — the light is blinked by "
                                   "reading one.")
    r = P.duplicate_signal((d or {}).get("device") or "/dev/sr0", mode=body.mode)
    return {"ok": bool(r.get("ok")), "message": r.get("message")}


@app.post("/api/system/led/test")
def led_test(user=Depends(require_user)):
    """Walk the LED through its primaries so a fresh build can be proved in ten seconds.

    Reports `detected: false` rather than a cheerful success when there is nothing
    wired up — "I ran the test and the box said OK and the LED stayed dark" is the
    least debuggable outcome this feature could offer.
    """
    return LED.self_test()


@app.get("/api/drives/guide")
def drives_guide(user=Depends(require_user)):
    """Which drive to buy — the list `docs/guide/01-what-you-need.md` has promised.

    Served from the same registry the running box identifies its own drive against,
    so the advice and the diagnosis can never disagree.
    """
    return {"drives": DRV.buying_guide(), "libredrive_list": DRV.LIBREDRIVE_LIST}


# ─────────────────────────────── auto rip ───────────────────────────────

def _reads_phrase(drive):
    """What this drive reads, in the words a person shopping for one would use.

    4K is named separately from Blu-ray on purpose. They are one checkbox on a
    retail listing and two entirely different pieces of hardware (`drives.py`), and
    collapsing them here would reproduce the exact confusion the drive registry
    exists to prevent.
    """
    parts = []
    if drive.get("reads_dvd"):
        parts.append("DVD")
    if drive.get("reads_bluray"):
        parts.append("Blu-ray")
    if not parts:
        return "capability unknown"
    if drive.get("uhd") == "yes" or drive.get("libredrive") == "enabled":
        parts.append("4K UHD")
    return ", ".join(parts)


def _drive_report():
    """The drives, plus the two things that are too expensive for the disc watcher.

    `optical_drives()` runs every three seconds and stays cheap. LibreDrive costs a
    `makemkvcon` run and the UHD label is derived from it, so both are attached here,
    on the status request a human made.
    """
    out = []
    busy = db.active_job() is not None
    for d in P.optical_drives():
        d = dict(d)
        # Ask MakeMKV about LibreDrive only when it cannot get in the way. The probe is
        # a two-minute `makemkvcon` run that holds the drive, and rip.py:136 already
        # states the rule -- "LibreDrive is asked only for a 4K disc" -- which this
        # ignored, probing on every status poll for any Blu-ray-capable drive. With a
        # DVD in the tray that meant a background probe owned /dev/sr0 and the rip's own
        # makemkvcon queued behind it, so POST /api/rip simply hung and the user saw a
        # button that did nothing. Idle tray, or a disc that actually raises the UHD
        # question: otherwise leave it None and let the chip stay unlit.
        ask = not busy and (not d.get("present") or RIP.disc_family(d) == "uhd")
        d["libredrive"] = P.libredrive_status(d) if ask else None
        # MakeMKV outranks the registry in both directions. The registry is what to
        # expect of a drive you have not bought; MakeMKV is what this drive does.
        verdict = {"enabled": "yes", "no": "no"}.get(d["libredrive"]) or d.get("uhd")
        d["uhd_label"] = DRV.UHD_LABEL.get(verdict)
        d["reads"] = _reads_phrase(d)
        # Family and refusal come from the engine rather than being re-derived in the
        # browser. "Is this a UHD disc" has a subtle answer (rip.disc_family) and the
        # frontend having its own copy of it is how two screens start disagreeing.
        family = RIP.disc_family(d)
        d["disc_family"] = family
        d["disc_word"] = RIP.DISC_WORD.get(family)
        d["cannot_read"] = (RIP.unreadable_reason(d, d["libredrive"])
                            if d.get("present") else None)
        d["space_warning"] = _space_warning(d) if d.get("present") else None
        out.append(d)
    return out


def _space_warning(drive):
    """"This disc is bigger than the room you have", said before the button is pressed.

    Preflight already refuses a title that does not fit (`rip._plan_transfer`), but it
    can only do that *after* reading the disc, which is a minute in and past the point
    the user committed. The disc's own size is known the moment it spins up, so the
    tray can say it up front.

    Hedged on purpose: this compares the whole **disc**, and what actually gets written
    is the main title, which is smaller by an unknown amount. So it is a caution and
    never a refusal — the certainty stays where the real number is.
    """
    if SH.Transport.supports_follow_copy:
        return None                      # streaming, so size stopped mattering
    size = drive.get("size_bytes") or 0
    free = P.storage_status().get("free_bytes") or 0
    if not size or size + WINDOW_BYTES <= free:
        return None
    return ("This is a %d GB disc and there's %d GB free on the card. The film itself "
            "is smaller than the whole disc, so it may still fit — Riparr will say "
            "for certain once it has read it."
            % (size // 2 ** 30, free // 2 ** 30))


def _autorip_state():
    """Auto Rip's prerequisites, every one of them, whether or not it is met.

    This used to return only the things that were wrong. That is the right shape for
    refusing to enable the switch and the wrong shape for the question people actually
    arrive with, which is "it isn't auto ripping, why not" — asked most often when the
    switch is ON and something downstream of it has since broken. A list that is empty
    when things are fine cannot answer that; a checklist can.

    Three states, and only `fail` blocks:

      ok    met, with `detail` naming what met it
      warn  Auto Rip still runs, but a disc put in right now may not get ripped --
            the card is full, or the key dies this week
      fail  Auto Rip cannot work at all and the switch stays unavailable
    """
    mk = P.makemkv_status()
    share = db.default_share()
    drives = P.optical_drives()
    cap = _capacity(P.storage_status()["free_bytes"])
    warn_days = int(db.get("warn_key_days") or 7)
    checks = []

    def check(what, state, detail, why=None, where=None):
        checks.append({"what": what, "state": state, "detail": detail,
                       "why": why, "where": where})

    # 1. Something to read discs with, in software...
    if mk.get("installed"):
        check("Riparr can read discs", "ok",
              "MakeMKV %s" % (mk.get("version") or "installed"))
    else:
        check("Riparr can read discs", "fail", "MakeMKV isn't installed",
              "Riparr has no way to read a disc without it.", "#/settings/general")

    # 2. ...and a licence for it. Kept separate from the install because a key that
    #    lapsed last week is a working install and a dead appliance, and those two
    #    facts want separate lines.
    days = mk.get("days_left")
    if days is not None and not P.trust_dates():
        days = None                       # a day count computed against a wrong clock
    if not mk.get("installed"):
        check("The MakeMKV key is current", "fail", "Nothing installed to key yet",
              "Install MakeMKV first.", "#/settings/general")
    elif not db.get("makemkv_key"):
        check("The MakeMKV key is current", "fail", "No key entered",
              "Encrypted discs won't decode without one.", "#/settings/general")
    elif days is not None and days <= 0:
        check("The MakeMKV key is current", "fail", "Expired",
              "Every rip will fail until it's replaced.", "#/settings/general")
    elif days is not None and days <= warn_days:
        check("The MakeMKV key is current", "warn",
              "%s key, %d day%s left" % ((mk.get("key_type") or "Beta").capitalize(),
                                         days, "" if days == 1 else "s"),
              "Rips start failing the day it lapses.", "#/settings/general")
    else:
        check("The MakeMKV key is current", "ok",
              "%s key%s" % ((mk.get("key_type") or "Licence").capitalize(),
                            ", %d days left" % days if days is not None else ""))

    # 3. Something to read discs with, in hardware. What it *reads* belongs on this
    #    row too: "a drive is attached" and "that drive can read the discs on your
    #    shelf" are the same prerequisite asked one level deeper, and the second is
    #    the one that ruins an evening.
    if drives:
        d = drives[0]
        name = " ".join(x for x in (d.get("vendor"), d.get("model")) if x)
        check("A drive to read them in", "ok",
              "%s · %s" % (name or "Optical drive", _reads_phrase(d)))
    else:
        check("A drive to read them in", "fail", "No optical drive detected",
              "A working USB bridge appears here even with no disc in the tray.",
              "#/system/status")

    # 4. Somewhere for the finished file to go. Configured and *tested* are one row:
    #    an untested share is not a second problem, it is the same problem earlier.
    if not share:
        check("Somewhere to put the files", "fail", "No library share",
              "Finished rips would have nowhere to go.", "#/settings/library")
    elif not share.get("verified_at"):
        check("Somewhere to put the files", "fail", "Share hasn't been tested",
              "Riparr writes a test file before it will trust a share with a rip.",
              "#/settings/library")
    else:
        check("Somewhere to put the files", "ok",
              "//%s/%s" % (share["host"], share["path"]))

    # 5. Room to work. Not a blocker: under D11 a small buffer means stream mode, not
    #    a refused disc. "degraded" is the one case where a disc really is turned away,
    #    which is a switch that looks on and a box that looks broken.
    if cap["mode"] == "degraded":
        check("Room to work", "warn", cap["phrase"],
              "Discs are refused before they start rather than failing at 90%.",
              "#/system/status")
    else:
        check("Room to work", "ok", cap["phrase"])

    # Kept in the shape the enable endpoint and older API callers expect: the headline
    # of a failing check is its `detail`, which is the specific thing that is wrong.
    blockers = [{"what": c["detail"], "why": c["why"], "where": c["where"]}
                for c in checks if c["state"] == "fail"]

    ready = not blockers
    enabled = bool(db.get("auto_rip")) and ready
    return {"enabled": enabled, "ready": ready, "blockers": blockers,
            "checks": checks, "requested": bool(db.get("auto_rip"))}


class AutoRip(BaseModel):
    enabled: bool


@app.get("/api/autorip")
def autorip(user=Depends(require_user)):
    return _autorip_state()


@app.post("/api/autorip")
def autorip_set(body: AutoRip, user=Depends(require_user)):
    st = _autorip_state()
    if body.enabled and not st["ready"]:
        raise HTTPException(
            status_code=400,
            detail="Auto Rip isn't ready yet — %s." % st["blockers"][0]["what"])
    db.set("auto_rip", body.enabled)
    return _autorip_state()


# ─────────────────────────────── settings ───────────────────────────────

# Settings that are credentials. They go out to the browser as a placeholder and come
# back the same way when untouched, which is what makes "save" on a page you did not
# retype your SMTP password into not wipe it. `list_shares` established the precedent
# of never returning a stored password at all; these follow it.
SECRET_SETTINGS = ("smtp_password", "ntfy_token")
SECRET_MASK = "\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022"


def _redact(s):
    s = dict(s)
    s.pop("session_secret", None)
    for k in SECRET_SETTINGS:
        if s.get(k):
            s[k] = SECRET_MASK
    return s


@app.get("/api/settings")
def get_settings(user=Depends(require_user)):
    return _redact(db.all_settings())


@app.put("/api/settings")
async def put_settings(request: Request, user=Depends(require_user)):
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Expected an object")
    body.pop("session_secret", None)
    for k, v in body.items():
        if k in SECRET_SETTINGS and v == SECRET_MASK:
            continue                      # unchanged; do not overwrite with the mask
        db.set(k, v)
    return _redact(db.all_settings())


@app.get("/api/notifications")
def notifications(user=Depends(require_user)):
    return {"events": [{"key": k, "label": label, "default": on} for k, label, on in NT.EVENTS],
            "enabled": NT.enabled_events(),
            "configured": NT.configured()}


class NotifyTest(BaseModel):
    channel: str


@app.post("/api/notifications/test")
def notifications_test(body: NotifyTest, user=Depends(require_user)):
    r = NT.test(body.channel)
    if not r.get("ok"):
        raise HTTPException(status_code=400, detail=r.get("error"))
    return r


class DiscordCheck(BaseModel):
    url: str = ""


@app.post("/api/notifications/discord/check")
def notifications_discord_check(body: DiscordCheck = DiscordCheck(),
                                user=Depends(require_user)):
    """Confirm a Discord webhook URL points at something real, and say what.

    Deliberately not an error: a bad URL here is the normal outcome of pasting the
    wrong half of something, and the page shows the reason next to the field rather
    than as a failure.
    """
    return NT.discord_check(body.url or None)


# ─────────────────────────────── shares ───────────────────────────────

class ShareQuery(BaseModel):
    host: str
    username: str = ""
    password: str = ""


class ShareTest(BaseModel):
    host: str
    share: str
    path: str = ""
    username: str = ""
    password: str = ""


class ShareCreate(ShareTest):
    name: str = ""


@app.exception_handler(SH.SmbToolMissing)
def _smb_tool_missing(request, exc):
    """A missing smbclient is a fixable setup problem, not a server fault.

    Unhandled it becomes a 500, which tells the user "Request failed (500)" and puts
    the only useful sentence — FileNotFoundError: 'smbclient' — in a log they will
    never read.
    """
    return JSONResponse(status_code=503, content={"detail": exc.message})


@app.get("/api/shares")
def shares_list(user=Depends(require_user)):
    return {"shares": db.list_shares()}


@app.post("/api/shares/discover")
def shares_discover(user=Depends(require_user)):
    return {"hosts": SH.discover()}


@app.post("/api/shares/browse")
def shares_browse(body: ShareQuery, user=Depends(require_user)):
    return SH.list_shares(body.host, body.username, body.password)


@app.post("/api/shares/test")
def shares_test(body: ShareTest, user=Depends(require_user)):
    return SH.test_write(body.host, body.share, body.path, body.username, body.password)


@app.post("/api/shares")
def shares_create(body: ShareCreate, user=Depends(require_user)):
    result = SH.test_write(body.host, body.share, body.path,
                           body.username, body.password)
    if not result.get("ok"):
        raise HTTPException(status_code=400,
                            detail=result.get("error", "The write test failed"))
    sid = db.add_share(body.name or ("%s/%s" % (body.host, body.share)),
                       body.host, "%s/%s" % (body.share, body.path.strip("/")),
                       body.username, body.password)
    db.mark_share_verified(sid)
    return {"ok": True, "id": sid, "test": result}


@app.delete("/api/shares/{share_id}")
def shares_delete(share_id: int, user=Depends(require_user)):
    db.delete_share(share_id)
    return {"ok": True}


# ─────────────────────────────── wi-fi ───────────────────────────────

class WifiConnect(BaseModel):
    ssid: str
    password: str = ""


@app.get("/api/wifi")
def wifi(user=Depends(require_user)):
    return P.wifi_status()


@app.post("/api/wifi/scan")
def wifi_scan(user=Depends(require_user)):
    nets = P.wifi_scan()
    # Only claim a band limit the hardware actually has. Most supported boards are
    # dual-band (some Wi-Fi 6); the Raspberry Pi Zero 2 W is the 2.4 GHz-only exception,
    # and even then the honest signal is "nothing on 5 GHz came back", not a promise.
    dual = any(n.get("band") in ("5", "6") for n in nets)
    note = ("5 GHz is faster and the better pick when the box and router are close."
            if dual else
            "Only 2.4 GHz networks were found — either this board has no 5 GHz radio, "
            "or none are in range.")
    return {"networks": nets, "note": note}


@app.post("/api/wifi/connect")
def wifi_connect(body: WifiConnect, user=Depends(require_user)):
    return P.wifi_connect(body.ssid, body.password)


# ─────────────────────────────── queue ───────────────────────────────

def _job_out(j):
    """Shape a job row for the interface: parse the JSON column, drop the noise."""
    j = dict(j)
    if j.get("titles"):
        try:
            j["titles"] = json.loads(j["titles"])
        except (ValueError, TypeError):
            j["titles"] = []
    # Which stage is running and since when. The queue's counting timer is built from
    # this against the medians: the two slowest stages of a rip -- the disc scan and
    # the decrypt pass -- can report no progress at all, so "this box usually takes
    # nine minutes and you are four minutes in" is the only number available.
    try:
        raw = json.loads(j.get("stages") or "[]")
    except (ValueError, TypeError):
        raw = []
    j["stages"] = db.job_stages(j)
    open_now = next((st for st in reversed(raw) if st.get("ended") is None), None)
    j["stage_name"] = open_now.get("name") if open_now else None
    j["stage_started"] = open_now.get("started") if open_now else None
    return j


@app.get("/api/queue")
def queue(user=Depends(require_user)):
    jobs = [_job_out(j) for j in db.list_jobs(states=db.ACTIVE_STATES)]
    # What a rip usually costs on this machine, from this machine's own history. The
    # slow half of a rip cannot report progress at all -- see db.typical_job_seconds --
    # so a fuzzy "usually done by" beats an empty space, provided it is labelled as the
    # guess it is and only offered once there is something to average.
    # Estimates are per disc family. A Blu-ray and a DVD are not the same job wearing
    # different labels -- Megamind saved for 10m40s and Arthur Christmas is four times
    # the data -- so a median that mixes them describes neither. `kind` is taken from
    # whatever is in flight, falling back to the tray, so the numbers on screen are
    # about the disc on screen.
    # Jobs that have given the disc back and are still crossing the network. Split out
    # so the page can show them as a quiet strip rather than as competing rip panels --
    # the user is watching the disc that is in the drive now, not the one on its way.
    sending = [j for j in jobs if j.get("state") in db.SENDING_STATES]
    active = next((j for j in jobs if j.get("disc_family")), None)
    kind = active.get("disc_family") if active else _tray_family()
    typical, samples = db.typical_job_seconds(kind=kind)
    stages = db.typical_stage_seconds(kind=kind)
    # Per-stage medians as well as the total. The total answers "when will this be
    # done"; the stages answer "should the fact that nothing has moved for six minutes
    # worry me", which is the question that actually gets asked.
    return {"jobs": [j for j in jobs if j.get("state") not in db.SENDING_STATES],
            "sending": sending,
            "drive_busy": bool(db.drive_busy()),
            "typical_seconds": typical, "typical_samples": samples,
            "typical_stages": stages, "typical_kind": kind,
            "stage_labels": db.stage_labels(db.get("transfer_mode") == "direct"),
            "stage_order": db.STAGE_ORDER}


def _tray_family():
    """What kind of disc is loaded, so an idle queue still estimates the right thing."""
    try:
        d = next((x for x in P.optical_drives() if x.get("present")), None)
        return RIP.disc_family(d) if d else None
    except Exception:
        return None


class RipRequest(BaseModel):
    force: bool = False


@app.post("/api/rip")
def rip_now(body: RipRequest = RipRequest(), user=Depends(require_user)):
    """Rip the disc that is in the tray, right now.

    The product had no manual verb at all before this: the only way to rip anything
    was to turn Auto Rip on and re-insert the disc. A page that correctly names the
    disc it can see and offers no way to act on it is the worst kind of broken,
    because everything on it looks like it is working.
    """
    job_id, why = RIP.enqueue(force=body.force)
    if not job_id:
        raise HTTPException(status_code=400, detail=why)
    return {"ok": True, "job_id": job_id}


@app.post("/api/queue/{job_id}/cancel")
def rip_cancel(job_id: int, user=Depends(require_user)):
    ok, message = RIP.cancel(job_id)
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    return {"ok": True, "message": message}


@app.post("/api/queue/{job_id}/retry")
def rip_retry(job_id: int, user=Depends(require_user)):
    ok, message = RIP.resume_transfer(job_id)
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    return {"ok": True, "message": message}


class VerifyRequest(BaseModel):
    mode: str = "quick"


@app.post("/api/queue/{job_id}/verify")
def rip_reverify(job_id: int, body: VerifyRequest = VerifyRequest(),
                 user=Depends(require_user)):
    """Check a finished job's file against the share again, without re-ripping.

    The read-back is the one stage that can fail for reasons that have nothing to do
    with the disc -- a NAS that went to sleep, a share that filled up, a box that ran
    out of RAM writing the temporary copy. Re-running it should not cost the forty
    minutes the rip did, and until now the only retry offered was the transfer.
    """
    if body.mode not in ("quick", "deep"):
        raise HTTPException(status_code=400, detail="Unknown verification mode.")
    ok, message = RIP.reverify(job_id, mode=body.mode)
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    return {"ok": True, "message": message}


class DiscAnswer(BaseModel):
    title_index: int = None
    name: str = ""
    skip: bool = False


@app.post("/api/queue/{job_id}/answer")
def rip_answer(job_id: int, body: DiscAnswer, user=Depends(require_user)):
    """The other end of `on_unknown_disc: ask`, which has been the default setting
    since the beginning with nothing anywhere that could do the asking."""
    ok, message = RIP.answer(job_id, title_index=body.title_index,
                             name=(body.name or "").strip(), skip=body.skip)
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    return {"ok": True, "message": message}


@app.post("/api/discs/{fingerprint}/rerip")
def disc_rerip(fingerprint: str, user=Depends(require_user)):
    """Re-rip a disc Riparr already knows.

    The button is pressed at the exact moment the disc is sitting on an open tray,
    because that is where a refused duplicate leaves it -- so this closes the tray
    rather than telling the user to. `_start_rerip` carries the rest.
    """
    return _start_rerip(fingerprint)


@app.post("/api/queue/{job_id}/rerip")
def rip_rerip(job_id: int, user=Depends(require_user)):
    """Read the disc again from the start, for a job whose rip is gone.

    Distinct from the disc-fingerprint form only in where the fingerprint comes from.
    A job that died before identification never got one, in which case this rips
    whatever is in the tray -- which is the best available reading of "try that again"
    when nobody, including Riparr, ever found out what that disc was.
    """
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="No such job.")
    return _start_rerip(job.get("fingerprint") or None,
                        what=job.get("title") or job.get("disc_label"))


def _start_rerip(fingerprint, what=None):
    """Pull the tray in if it is open, then queue this disc past the duplicate check.

    Two things have to be true at once and they fight each other. The duplicate check
    must not refuse this disc -- and the *disc watcher* may well get to it first, three
    seconds after the tray closes, with no idea a human asked for it. So the
    authorisation is armed on the disc rather than passed to one call: whichever path
    reaches `enqueue` first consumes it, and the loser is turned away by the
    already-working-on-a-disc guard rather than by ejecting the disc.
    """
    RIP.arm_force(fingerprint)
    d = next((x for x in P.optical_drives() if x.get("present")), None)
    if not d:
        ok, message = P.close_tray()
        if not ok:
            raise HTTPException(
                status_code=400,
                detail="Put %s in the tray and press Re-rip again. (%s)"
                       % (what or "the disc", message))
    job_id, why = RIP.enqueue(force=True, expect=fingerprint)
    if not job_id:
        # The watcher beat us to it. That is a success wearing an error's clothes:
        # the disc it picked up is the one that was armed, so it is already being
        # ripped and the only correct answer is to point at that job.
        active = db.active_job()
        if active and (not fingerprint or active.get("fingerprint") in (fingerprint, "")):
            return {"ok": True, "job_id": active["id"]}
        raise HTTPException(status_code=400, detail=why)
    return {"ok": True, "job_id": job_id}


@app.get("/api/history")
def history(user=Depends(require_user)):
    """Every finished job, with what each stage cost and what can still be retried.

    History is the data page: the question it answers is "what happened, how long did
    each part take, and what can I do about it". The four retry verbs are computed
    here rather than in the browser, because whether a retry is possible depends on
    something only the box can see -- whether the staged file is still on the card.
    """
    jobs = []
    for j in db.list_jobs(states=["done", "failed", "cancelled"], limit=100):
        j = _job_out(j)                       # this is what parses `stages`
        local = j.get("local_path")
        j["local_exists"] = bool(local and os.path.exists(local))
        j["retries"] = _retries_for(j)
        jobs.append(j)
    # History spans every kind of disc, so its key is per family rather than one
    # blended median that is wrong about all of them.
    return {"jobs": jobs,
            "typical_by_kind": {k: db.typical_stage_seconds(kind=k)
                                for k in ("dvd", "bluray", "uhd")},
            "typical_stages": db.typical_stage_seconds(),
            "stage_order": db.STAGE_ORDER, "stage_labels": db.STAGE_LABEL}


def _retries_for(j):
    """Which of the four retry verbs apply to this job, and why.

    Each one is offered only when it would actually do something:

    * **Retry rip** -- the rip itself is gone or was never made, so the disc has to go
      back in. Always available on a job that did not finish; it needs the disc.
    * **Retry upload** -- the file is still staged on the card, so the expensive half
      is already paid for and this is a re-copy, not a re-rip.
    * **Retry fast verification** / **Retry deep verification** -- the file reached
      the share, so it can be checked again without touching the disc. Deep needs the
      staged copy to compare against; fast only needs the size, so it needs the staged
      copy too (that is what the size is compared *to*).
    """
    out = []
    state, local = j.get("state"), j.get("local_exists")
    landed = bool(j.get("dest_path"))
    if state != "done":
        if local:
            out.append({"action": "upload", "label": "Retry upload",
                        "why": "The rip is still on the card, so this is a re-copy "
                               "rather than a re-read of the disc."})
        out.append({"action": "rip", "label": "Retry rip", "needs_disc": True,
                    "why": "Put the disc back in the tray and Riparr will read it "
                           "again from the start."})
    if landed and local:
        done_mode = j.get("verified_mode")
        out.append({"action": "verify-quick", "label": "Retry fast verification",
                    "why": "Compares the size on your library against the rip. "
                           "Seconds, and it catches a truncated transfer."})
        out.append({"action": "verify-deep", "label": "Retry deep verification",
                    "why": ("Reads the whole file back and hashes it. Slow, and it "
                            "needs as much free space again as the film."
                            + (" This one has only ever been size-checked."
                               if done_mode == "quick" else ""))})
    return out


@app.get("/api/discs")
def discs(user=Depends(require_user)):
    return {"discs": db.list_discs()}


@app.delete("/api/discs/{fingerprint}")
def disc_forget(fingerprint: str, user=Depends(require_user)):
    db.forget_disc(fingerprint)
    return {"ok": True}


# ─────────────────────────────── makemkv ───────────────────────────────

class MakeMKVKey(BaseModel):
    key: str


class MakeMKVInstall(BaseModel):
    accept_eula: bool = False


@app.get("/api/makemkv")
def makemkv(user=Depends(require_user)):
    return MK.info()


@app.post("/api/makemkv/sites")
def makemkv_sites(user=Depends(require_user)):
    """Re-probe MakeMKV's site and forum now, rather than serving the cached answer."""
    return {"sites": MK.site_status(force=True)}


@app.post("/api/makemkv/install")
def makemkv_install(body: MakeMKVInstall, user=Depends(require_user)):
    """Refuses without consent. MakeMKV's EULA is between the user and GuinpinSoft (D14)."""
    r = MK.start_install(body.accept_eula)
    if not r.get("ok"):
        raise HTTPException(status_code=400, detail=r["error"])
    db.set("makemkv_eula_accepted_at", int(time.time()))
    return r


@app.get("/api/makemkv/install")
def makemkv_install_status(user=Depends(require_user)):
    return MK.install_status()


class PowerAction(BaseModel):
    action: str


@app.post("/api/system/power")
def system_power(body: PowerAction, user=Depends(require_user)):
    """Restart or shut down the box.

    The enclosure has no power button, so without this the only way to stop the box is
    to pull the cable — which is how a running Linux system loses a filesystem.
    """
    ok, message = P.power_action(body.action)
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    return {"ok": True, "action": body.action, "message": message}


@app.get("/api/artwork")
def artwork_lookup(label: str = "", user=Depends(require_user)):
    """Cover art for a disc label, only when the match is beyond doubt.

    Returns `{"ok": false}` far more often than not, and that is the intended
    behaviour: a confidently wrong poster is worse than a plain background.
    """
    if not db.get("disc_artwork", True):
        return {"ok": False, "reason": "disabled"}
    hit = ART.look_up(label)
    if not hit:
        return {"ok": False}
    return {"ok": True, "title": hit["title"], "confidence": hit["confidence"],
            "image": "/api/artwork/image/%s" % hit["token"]}


@app.get("/api/artwork/image/{token}")
def artwork_image(token: str, user=Depends(require_user)):
    """Proxy the matched image.

    The caller passes a token this process issued, never a URL: an endpoint that
    fetches whatever it is handed is an open proxy sitting inside somebody's LAN.
    Cached in the browser for a day -- it is decoration, and the disc will be gone
    long before it goes stale.
    """
    blob, ctype = ART.image_bytes(token)
    if not blob:
        raise HTTPException(status_code=404, detail="No image for that token.")
    return Response(content=blob, media_type=ctype,
                    headers={"Cache-Control": "private, max-age=86400"})


@app.post("/api/system/usb-host")
def system_usb_host(user=Depends(require_user)):
    """Make both USB-C sockets able to host the drive, then restart.

    The board has two sockets that look identical and only one can host. The other
    enumerates nothing and logs nothing, so it reads as a dead drive rather than a
    wrong port. Rather than explain that, offer to fix it.
    """
    ok, message = P.usb_host_fix()
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    return {"ok": True, "message": message}


@app.get("/api/makemkv/beta-key")
def makemkv_beta_key(refresh: bool = False, user=Depends(require_user)):
    """The beta key GuinpinSoft publishes, fetched so the user does not have to.

    MakeMKV is free during beta behind a key that rolls over roughly monthly. Reading
    it off a forum and noticing when it lapses is a chore the box is better placed to
    do than its owner.
    """
    return MK.beta_key(force=refresh)


@app.post("/api/makemkv/key")
def makemkv_key(body: MakeMKVKey, user=Depends(require_user)):
    db.set("makemkv_key", body.key.strip())
    return {"ok": True}


# ─────────────────────────────── updates ───────────────────────────────

@app.get("/api/update")
def update_check(user=Depends(require_user)):
    return updater.check()


@app.post("/api/update/install")
def update_install(user=Depends(require_user)):
    return updater.install()


# ─────────────────────────── config backup ───────────────────────────

@app.get("/api/config/export")
def config_export(user=Depends(require_user)):
    s = db.all_settings()
    s.pop("session_secret", None)
    return JSONResponse(
        {"version": __version__, "exported_at": int(time.time()),
         "settings": s, "shares": db.list_shares()},
        headers={"Content-Disposition": 'attachment; filename="riparr-config.json"'})


@app.post("/api/config/import")
async def config_import(request: Request, user=Depends(require_user)):
    body = await request.json()
    for k, v in (body.get("settings") or {}).items():
        if k != "session_secret":
            db.set(k, v)
    return {"ok": True}


# ──────────────────── system: tasks, events, logs, backups ────────────────────

@app.get("/api/system/tasks")
def system_tasks(user=Depends(require_user)):
    return {"scheduled": SY.task_list(), "queue": SY.task_history(limit=20)}


@app.post("/api/system/tasks/{name}")
def system_task_run(name: str, user=Depends(require_user)):
    r = SY.run_task(name, trigger="manual")
    if r is None:
        raise HTTPException(status_code=404, detail="No such task")
    return r


@app.get("/api/system/events")
def system_events(limit: int = 50, offset: int = 0, levels: str = "",
                  user=Depends(require_user)):
    wanted = [l for l in levels.split(",") if l] or None
    return SY.events(limit=min(limit, 200), offset=offset, levels=wanted)


@app.delete("/api/system/events")
def system_events_clear(user=Depends(require_user)):
    SY.clear_events()
    return {"ok": True}


@app.get("/api/system/logs")
def system_logs(user=Depends(require_user)):
    return {"path": SY.LOG_DIR, "files": SY.log_files()}


@app.get("/api/system/logs/{name}")
def system_log_download(name: str, user=Depends(require_user)):
    path = SY.log_path(name)
    if not path:
        raise HTTPException(status_code=404, detail="No such log file")
    return FileResponse(path, media_type="text/plain", filename=name)


@app.delete("/api/system/logs")
def system_logs_clear(user=Depends(require_user)):
    return {"ok": True, "deleted": SY.delete_log_files()}


@app.get("/api/system/backups")
def system_backups(user=Depends(require_user)):
    return {"path": SY.BACKUP_DIR, "keep": SY.BACKUP_KEEP, "backups": SY.backups()}


@app.post("/api/system/backups/upload")
async def system_backup_upload(file: UploadFile = File(...), user=Depends(require_user)):
    r = SY.import_backup(file.filename or "upload", await file.read())
    if not r.get("ok"):
        raise HTTPException(status_code=400, detail=r.get("error", "Restore failed"))
    return r


@app.post("/api/system/backups")
def system_backup_create(user=Depends(require_user)):
    return SY.create_backup(kind="manual")


@app.get("/api/system/backups/{name}")
def system_backup_download(name: str, user=Depends(require_user)):
    path = SY.backup_path(name)
    if not path:
        raise HTTPException(status_code=404, detail="No such backup")
    return FileResponse(path, media_type="application/zip", filename=name)


@app.post("/api/system/backups/{name}/restore")
def system_backup_restore(name: str, user=Depends(require_user)):
    r = SY.restore_backup(name)
    if not r.get("ok"):
        raise HTTPException(status_code=404, detail=r.get("error", "Restore failed"))
    return r


@app.delete("/api/system/backups/{name}")
def system_backup_delete(name: str, user=Depends(require_user)):
    if not SY.delete_backup(name):
        raise HTTPException(status_code=404, detail="No such backup")
    return {"ok": True}


# ─────────────────────────────── static ───────────────────────────────

class RevalidatingStatic(StaticFiles):
    """Static files the browser must revalidate rather than assume.

    With no Cache-Control at all, browsers apply a heuristic freshness lifetime and
    happily serve a stale app.js for hours -- so an upgraded box keeps showing the old
    interface and the user is told to "hard refresh", which is not an answer an
    appliance gets to give. `no-cache` does not mean "do not store": the file is still
    cached, it is just revalidated, so the normal case is a 304 with no body. On a LAN
    that is free, and a plain reload always shows the version that is installed.
    """

    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


app.mount("/static", RevalidatingStatic(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"),
                        headers={"Cache-Control": "no-cache"})


@app.get("/{path:path}")
def spa(path: str):
    """Client-side routing: unknown paths return the shell, not a 404.

    The file lookup is contained to STATIC. `os.path.join` on an attacker path with
    `../` in it happily walks out of the static directory, and FileResponse would then
    serve any file the service account can read — the database (session secret, password
    hashes, the share password) included, with no login. So resolve the real path and
    refuse anything that is not inside STATIC, the same guard system.py already uses for
    log and backup downloads. Falling through to the shell is the right refusal here:
    a traversal attempt is not a route, so it gets the same answer any other non-file
    path does rather than a 403 that confirms the file exists.
    """
    if path.startswith("api/"):
        raise HTTPException(status_code=404, detail="No such endpoint")
    if path:
        candidate = os.path.realpath(os.path.join(STATIC, path))
        root = os.path.realpath(STATIC)
        if (candidate == root or candidate.startswith(root + os.sep)) \
                and os.path.isfile(candidate):
            return FileResponse(candidate)
    return FileResponse(os.path.join(STATIC, "index.html"))
