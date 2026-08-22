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

from . import (__version__, db, makemkv as MK, notify as NT, platform as P,
               rip as RIP, shares as SH, system as SY, updater)

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
    st = P.makemkv_status()
    share = db.default_share()
    return {
        "has_users": db.has_users(),
        "complete": bool(db.get("setup_complete")),
        "makemkv": st,
        "share": share,
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
WINDOW_BYTES = 4 * 2**30          # the streaming window (D11)


DISC_NAMES = {"uhd": ("4K UHD disc", "4K UHD discs"),
              "bluray": ("Blu-ray", "Blu-rays"),
              "dvd": ("DVD", "DVDs")}
DISC_ORDER = ("uhd", "bluray", "dvd")


def _capacity(free_bytes):
    """Capacity, in the terms D11 actually operates in.

    Under adaptive streaming, a buffer too small for the next disc does NOT mean the
    disc is refused — it means the rip runs in stream mode instead of burst. Reporting
    "no room" here would contradict the whole design and push people toward buying a
    larger card for a benefit that does not exist.

    "Room for 1 more disc" was true and useless: a disc is anywhere from 8 to 66 GB,
    so the number silently meant Blu-ray and was wrong by a factor of eight for a DVD.
    Count each kind and say which is which.
    """
    by_kind = {k: max(0, int(free_bytes // v)) for k, v in DISC_BYTES.items()}
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
    else:
        mode, phrase = "stream", "Streaming — discs are never refused for space"

    return {"discs_free": discs, "by_kind": by_kind, "mode": mode, "phrase": phrase,
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
        "drives": P.optical_drives(),
        "share": db.default_share(),
        "setup_complete": bool(db.get("setup_complete")),
        "autorip": _autorip_state(),
    }


@app.post("/api/drive/eject")
def drive_eject(user=Depends(require_user)):
    return P.eject()


# ─────────────────────────────── auto rip ───────────────────────────────

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

    # 3. Something to read discs with, in hardware.
    if drives:
        d = drives[0]
        name = " ".join(x for x in (d.get("vendor"), d.get("model")) if x)
        check("A drive to read them in", "ok",
              "%s · %s" % (name or "Optical drive", d["device"]))
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
    return {"networks": P.wifi_scan(),
            "note": "Only 2.4 GHz networks are listed — this hardware has no 5 GHz radio."}


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
    return j


@app.get("/api/queue")
def queue(user=Depends(require_user)):
    return {"jobs": [_job_out(j) for j in db.list_jobs(states=db.ACTIVE_STATES)]}


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

    `docs/guide/06-ripping-discs.md` has told people to "force a re-rip from the web
    page if you meant it" for as long as the guide has existed, and until now there was
    no such control -- only "Forget", which is a different thing wearing a name that
    does not match the sentence the guide taught them.
    """
    drives = P.optical_drives()
    d = next((x for x in drives if x.get("present")), None)
    if not d:
        raise HTTPException(status_code=400,
                            detail="Put the disc back in the tray first.")
    if RIP.fingerprint(d) != fingerprint:
        raise HTTPException(
            status_code=400,
            detail="The disc in the tray isn't that one. Insert it and try again.")
    job_id, why = RIP.enqueue(force=True)
    if not job_id:
        raise HTTPException(status_code=400, detail=why)
    return {"ok": True, "job_id": job_id}


@app.get("/api/history")
def history(user=Depends(require_user)):
    return {"jobs": db.list_jobs(states=["done", "failed"], limit=100)}


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

app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


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
