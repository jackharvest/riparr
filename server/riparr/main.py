"""
The Riparr service. API-first: the web UI is just the first client of this API (D2),
which is what makes Homepage widgets and multi-unit setups nearly free later.
"""
import os
import time

from fastapi import FastAPI, HTTPException, Request, Response, Depends
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from itsdangerous import URLSafeSerializer, BadSignature

from . import __version__, db, makemkv as MK, platform as P, shares as SH, updater

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
COOKIE = "riparr_session"

app = FastAPI(title="Riparr", version=__version__, docs_url="/api/docs",
              openapi_url="/api/openapi.json")


def _secret():
    s = db.get("session_secret")
    if not s:
        s = db.set("session_secret", os.urandom(32).hex())
    return s


@app.on_event("startup")
def _startup():
    db.init()
    _secret()


# ─────────────────────────────── auth ───────────────────────────────

def _serializer():
    return URLSafeSerializer(_secret(), salt="riparr-session")


def current_user(request: Request):
    raw = request.cookies.get(COOKIE)
    if not raw:
        return None
    try:
        return _serializer().loads(raw).get("u")
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


@app.post("/api/auth/login")
def login(body: Login, response: Response):
    if not db.verify_user(body.username, body.password):
        raise HTTPException(status_code=401, detail="Wrong username or password")
    token = _serializer().dumps({"u": body.username, "t": int(time.time())})
    response.set_cookie(COOKIE, token, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30)
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
def change_password(body: PasswordChange, request: Request, user=Depends(require_user)):
    if not db.verify_user(user, body.current_password):
        raise HTTPException(status_code=400, detail="Current password is wrong")
    if len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="Use at least 8 characters")
    db.set_password(user, body.new_password)
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
                        max_age=60 * 60 * 24 * 30)
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
    """Auto Rip is only offered once it could actually succeed.

    Every blocker names the thing to fix and where to fix it, because a switch that
    silently does nothing is worse than one that is honestly unavailable.
    """
    mk = P.makemkv_status()
    share = db.default_share()
    blockers = []
    if not mk.get("installed"):
        blockers.append({"what": "MakeMKV isn't installed",
                         "why": "Riparr needs it to read discs.",
                         "where": "#/settings/general"})
    elif not db.get("makemkv_key"):
        blockers.append({"what": "No MakeMKV key",
                         "why": "Encrypted discs won't decode without one.",
                         "where": "#/settings/general"})
    elif mk.get("days_left") is not None and mk["days_left"] <= 0:
        blockers.append({"what": "The MakeMKV key has expired",
                         "why": "Rips will fail until it's replaced.",
                         "where": "#/settings/general"})
    if not share:
        blockers.append({"what": "No library share",
                         "why": "Finished rips would have nowhere to go.",
                         "where": "#/settings/library"})
    elif not share.get("verified_at"):
        blockers.append({"what": "The share hasn't been tested",
                         "why": "Riparr writes a test file before trusting it.",
                         "where": "#/settings/library"})
    if not P.optical_drives():
        blockers.append({"what": "No optical drive detected",
                         "why": "Nothing to read discs with.",
                         "where": "#/system/status"})

    ready = not blockers
    enabled = bool(db.get("auto_rip")) and ready
    return {"enabled": enabled, "ready": ready, "blockers": blockers,
            "requested": bool(db.get("auto_rip"))}


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

@app.get("/api/settings")
def get_settings(user=Depends(require_user)):
    s = db.all_settings()
    s.pop("session_secret", None)
    return s


@app.put("/api/settings")
async def put_settings(request: Request, user=Depends(require_user)):
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Expected an object")
    body.pop("session_secret", None)
    for k, v in body.items():
        db.set(k, v)
    return db.all_settings()


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

@app.get("/api/queue")
def queue(user=Depends(require_user)):
    return {"jobs": db.list_jobs(states=["queued", "ripping", "transferring", "verifying"])}


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


# ─────────────────────────────── static ───────────────────────────────

app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


@app.get("/{path:path}")
def spa(path: str):
    """Client-side routing: unknown paths return the shell, not a 404."""
    if path.startswith("api/"):
        raise HTTPException(status_code=404, detail="No such endpoint")
    candidate = os.path.join(STATIC, path)
    if path and os.path.isfile(candidate):
        return FileResponse(candidate)
    return FileResponse(os.path.join(STATIC, "index.html"))
