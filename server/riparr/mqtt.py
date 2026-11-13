"""
The box's state on an MQTT broker, and two things a broker may ask of it.

Notifications (`notify.py`) say that something *happened*. This says what the box *is
doing*, continuously, so that something else -- Home Assistant, Node-RED, a script --
can decide for itself what is worth a notification, a light or a dashboard tile.

Every value has its own retained topic under one base topic, so a client that connects
at any moment sees the whole picture without waiting for the next change:

    <base>/availability            online | offline   (offline is the broker's last will)
    <base>/job/state               idle | queued | identifying | ripping | transferring
                                   | verifying | needs_input | done | failed
                                   (done lasts mqtt_done_seconds after a finished rip,
                                   then idle; a new disc replaces it at once)
    <base>/job/title               what is being ripped
    <base>/job/progress            0-100, through the current stage (0 when idle, 100 when done)
    <base>/job/attributes          JSON: id, kind, phase, eta, error ...
    <base>/autorip/state           ON | OFF
    <base>/drive/detected          ON | OFF
    <base>/drive/disc_present      ON | OFF
    <base>/drive/disc_type         dvd | bluray | uhd | cd
    <base>/drive/attributes        JSON: vendor, model, tray, label
    <base>/system/health           ok | warning | error
    <base>/system/attributes       JSON: the problems behind that word
    <base>/system/cpu_temperature  degrees C
    <base>/storage/free            GiB free on the card
    <base>/storage/total           GiB
    <base>/library/state           ready | error | not_configured
    <base>/library/free            GiB free on the share, when it is mounted
    <base>/library/attributes      JSON: which share, and what is wrong with it
    <base>/network/connected       ON | OFF
    <base>/network/ssid, /signal, /ip
    <base>/update/state            JSON: installed_version, latest_version
    <base>/update/available        ON | OFF

A value with nothing to report is the literal `None`, not an empty payload: an empty
retained message is how MQTT *deletes* a topic, and Home Assistant reads `None` as
"unknown", which is what it is.

Two topics are listened to. Retained commands are ignored on both, because a retained
"eject" would open the tray every time the box reconnected:

    <base>/autorip/set             ON | OFF
    <base>/drive/eject             anything

paho-mqtt is the one dependency this adds. It is pure Python, so there is no wheel to
fail to build on the board, and it is imported lazily: a box whose update could not
fetch it still starts, and says on the Connect page what is missing.
"""
import functools
import hashlib
import json
import os
import queue
import ssl
import threading
import time

from . import __version__, db, platform as P, rip as RIP, system as SY, updater

log = SY.component("MQTT")

FAST = 5            # seconds between looks at the job and the tray
SLOW = 30           # seconds between looks at everything that costs more than a query
TEST_TIMEOUT = 8
NONE = "None"
NO_LIBRARY = "The MQTT library (paho-mqtt) isn't installed on this box."

# Derived, so a state the engine gains is one Home Assistant has been told about.
JOB_STATES = ["idle"] + db.ACTIVE_STATES + ["done", "failed"]

_stop = threading.Event()
_wake = threading.Event()
_cmds = queue.Queue()
_thread = None
# What main.py lends this module, so it does not have to import main to ask whether
# Auto Rip is ready: autorip_state(), set_autorip(bool), capacity(free_bytes). See start().
_hooks = {}


class _State:
    cfg = None          # the settings the current client was built from
    client = None
    connected = False
    session = 0         # counts (re)connections
    announced = 0       # the session everything was last published in full for
    error = None        # why it is not connected, in words
    sent = {}           # topic -> last payload, so only changes go out
    slow = {}
    slow_at = 0
    update_asked = False


_S = _State()


def _paho():
    try:
        import paho.mqtt.client as paho
        return paho
    except ImportError:
        return None


# ─────────────────────────────── configuration ───────────────────────────────

@functools.lru_cache(maxsize=1)
def device_id():
    """Stable for the life of the install, and not the hostname.

    Home Assistant keys every entity on this. A hostname is something people change,
    and changing it would orphan every entity and create a second set beside them.
    """
    for path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            with open(path) as f:
                raw = f.read().strip()
        except OSError:
            continue
        if raw:
            return "riparr_" + hashlib.sha1(raw.encode()).hexdigest()[:10]
    return "riparr_" + _slug(P.hostname())


def _slug(s):
    out = "".join(ch if ch.isalnum() else "_" for ch in (s or "").lower()).strip("_")
    return out or "riparr"


def base_topic(raw=None):
    """`riparr/<hostname>` unless told otherwise, so two boxes do not share topics."""
    if raw is None:
        raw = db.get("mqtt_base_topic")
    # Wildcards are legal in a subscription and never in a topic that is published to.
    t = (raw or "").replace("#", "").replace("+", "")
    t = "/".join(p.strip() for p in t.split("/") if p.strip())
    return t or "riparr/%s" % _slug(P.hostname())


def _config():
    # One query, not one per key: this runs every five seconds whether MQTT is on or not.
    s = db.all_settings()
    host = (s.get("mqtt_host") or "").strip()
    return {
        "enabled": bool(s.get("mqtt_enabled")) and bool(host),
        "host": host,
        "port": int(s.get("mqtt_port") or 1883),
        "tls": bool(s.get("mqtt_tls")),
        "tls_insecure": bool(s.get("mqtt_tls_insecure")),
        "username": (s.get("mqtt_username") or "").strip(),
        "password": s.get("mqtt_password") or "",
        "base": base_topic(s.get("mqtt_base_topic") or ""),
        "discovery": bool(s.get("mqtt_discovery")),
        "prefix": (s.get("mqtt_discovery_prefix") or "homeassistant").strip().strip("/"),
    }


def _new_client(cfg, client_id):
    paho = _paho()
    c = paho.Client(paho.CallbackAPIVersion.VERSION2, client_id=client_id)
    if cfg["username"]:
        c.username_pw_set(cfg["username"], cfg["password"] or None)
    if cfg["tls"]:
        if cfg["tls_insecure"]:
            # A broker on a home network is nearly always behind a self-signed
            # certificate. Encrypted and unverified still beats the plain-text
            # password that is the realistic alternative.
            c.tls_set(cert_reqs=ssl.CERT_NONE)
            c.tls_insecure_set(True)
        else:
            c.tls_set()
    return c


# ─────────────────────────────── connection ───────────────────────────────

def _connect(cfg):
    if not _paho():
        _S.error = NO_LIBRARY + " Updating Riparr from System → Updates installs it."
        log.error(_S.error)
        return
    c = _new_client(cfg, "riparr-" + device_id())
    c.will_set(cfg["base"] + "/availability", "offline", qos=1, retain=True)
    c.reconnect_delay_set(min_delay=2, max_delay=120)

    def on_connect(client, userdata, flags, rc, props=None):
        if getattr(rc, "is_failure", False):
            _S.connected = False
            _S.error = "The broker refused the connection: %s" % rc
            log.warning(_S.error)
            return
        client.subscribe([(cfg["base"] + "/autorip/set", 1),
                          (cfg["base"] + "/drive/eject", 1)])
        _S.connected, _S.error = True, None
        _S.session += 1
        log.info("Connected to %s:%d as %s", cfg["host"], cfg["port"], cfg["base"])
        _wake.set()

    def on_disconnect(client, userdata, flags, rc, props=None):
        if _S.connected:
            log.warning("Lost the broker (%s). Reconnecting by itself.", rc)
        _S.connected = False
        if getattr(rc, "is_failure", False):
            _S.error = "Disconnected: %s" % rc

    def on_connect_fail(client, userdata):
        _S.connected = False
        _S.error = "Couldn't reach %s:%d" % (cfg["host"], cfg["port"])

    def on_message(client, userdata, msg):
        if msg.retain:
            return
        _cmds.put((msg.topic[len(cfg["base"]) + 1:],
                   msg.payload.decode("utf-8", "replace").strip()))
        _wake.set()

    c.on_connect, c.on_disconnect = on_connect, on_disconnect
    c.on_connect_fail, c.on_message = on_connect_fail, on_message
    try:
        # Async, so a broker that is down at boot is retried by paho's own thread
        # rather than being a reason this one never starts.
        c.connect_async(cfg["host"], cfg["port"], keepalive=60)
        c.loop_start()
    except Exception as e:
        _S.error = "Couldn't start the connection to %s: %s" % (cfg["host"], e)
        log.error(_S.error)
        return
    _S.client = c


def _disconnect():
    c, cfg = _S.client, _S.cfg
    _S.client, _S.connected, _S.sent = None, False, {}
    if not c:
        return
    try:
        if c.is_connected() and cfg:
            # A clean disconnect does not fire the last will, so say it ourselves.
            c.publish(cfg["base"] + "/availability", "offline", qos=1,
                      retain=True).wait_for_publish(timeout=3)
    except Exception as e:
        log.debug("Could not say offline: %s", e)
    # Whatever happened above, the old client must not be left running: it would go on
    # holding the client id the next one is about to connect with.
    try:
        c.disconnect()
        c.loop_stop()
    except Exception as e:
        log.debug("Disconnect: %s", e)


def _publish(suffix, payload, absolute=False):
    """Send a retained value, if it differs from the last one sent."""
    topic = suffix if absolute else "%s/%s" % (_S.cfg["base"], suffix)
    if isinstance(payload, (dict, list)):
        payload = json.dumps(payload, sort_keys=True)
    elif payload is None:
        payload = NONE
    elif isinstance(payload, bool):
        payload = "ON" if payload else "OFF"
    else:
        payload = str(payload)
    if _S.sent.get(topic) != payload:
        _S.client.publish(topic, payload, qos=1, retain=True)
        _S.sent[topic] = payload


# ─────────────────────────────── what the box is doing ───────────────────────────────

def _gib(n):
    return round(n / 2.0**30, 1) if n is not None else None


def _done_seconds():
    try:
        return max(0, int(db.get("mqtt_done_seconds")))
    except (TypeError, ValueError):
        return 600


def _began(job):
    return job.get("started_at") or job.get("queued_at") or 0


def _ended(job):
    return job.get("finished_at") or _began(job)


def _standing_failure(finals, active):
    """The latest failure, unless a disc has gone in since it happened.

    Latest by when it *ended*, not by when the job started. An upload can fail long
    after the next disc has started and finished, and the list from the database is in
    start order -- in which that later disc comes first and hides the failure.
    """
    failed = [j for j in finals if j["state"] == "failed"]
    if not failed:
        return None
    last = max(failed, key=_ended)
    if any(_began(j) > _ended(last) for j in finals + active if j["id"] != last["id"]):
        return None
    return last


def _job_values():
    active = db.list_jobs(states=db.ACTIVE_STATES)
    # The disc in the drive is the job somebody is watching. One that has given its
    # disc back and is crossing the network comes second, and is counted either way.
    sending = [j for j in active if j["state"] in db.SENDING_STATES]
    drive = next((j for j in active if j["state"] not in db.SENDING_STATES), None)
    job = drive or (sending[0] if sending else None)
    state = job["state"] if job else "idle"
    if not drive:
        # A failure stays on the topic until the next disc replaces it. "idle" a
        # second after a rip died would be true and would hide the only thing an
        # automation is watching for. An earlier rip still uploading does not replace
        # it either: the disc that just failed is the news, not the upload behind it.
        finals = db.list_jobs(states=db.FINAL_STATES, limit=10)
        failure = _standing_failure(finals, active)
        if failure:
            job, state = failure, "failed"
        elif not job:
            # A finished rip reads "done" for a while, then "idle". Newest by finish time,
            # not start: an upload that outlived the next rip's start is the later "done".
            # A new disc needs no code here: its job is active, so this is never reached.
            dones = [j for j in finals if j["state"] == "done" and j.get("finished_at")]
            last = max(dones, key=lambda j: j["finished_at"], default=None)
            if last and 0 <= time.time() - last["finished_at"] < _done_seconds():
                job, state = last, "done"

    pct = None
    if state == "idle":
        pct = 0.0           # a gauge with nothing to do reads 0, not unknown
    elif job and state == "done":
        pct = 100.0
    elif job and state != "failed":
        if isinstance(job.get("stage_pct"), (int, float)):
            pct = job["stage_pct"] * 100
        elif state == "transferring" and job.get("bytes_total"):
            pct = 100.0 * (job.get("bytes_sent") or 0) / job["bytes_total"]
        if pct is not None:
            pct = round(max(0.0, min(100.0, pct)), 1)

    attrs = {"sending": len(sending)}
    if job:
        for k in ("id", "kind", "phase", "disc_label", "disc_family", "eta_seconds",
                  "error", "warning"):
            if job.get(k) not in (None, ""):
                attrs[k] = job[k]
    return {"job/state": state,
            "job/title": (job.get("title") or job.get("disc_label")) if job else None,
            "job/progress": pct,
            "job/attributes": attrs}


def _drive_values(drives):
    # The drive with a disc in it, if any has one; otherwise the first.
    d = next((x for x in drives if x.get("present")), drives[0] if drives else {})
    present = bool(d.get("present"))
    return {"drive/detected": bool(drives),
            "drive/disc_present": present,
            "drive/disc_type": RIP.disc_family(d) if present else None,
            "drive/attributes": {k: d.get(k) for k in ("vendor", "model", "tray", "label")
                                 if d.get(k)}}


def _health(clock, mk, drives, share, wifi, storage, mode):
    """The Health table on System → Status, as one word and a list.

    The page derives it in the browser because each line there links to the screen
    that fixes it. Nothing on a broker can follow a link, so the same checks are made
    here; if one is added to `healthMessages` in app.js it belongs here too.
    """
    bad, warn = [], []
    if not clock.get("plausible"):
        bad.append("The system clock is wrong, so dates and key expiry mean nothing.")
    elif clock.get("synced") is False:
        warn.append("The clock hasn't synchronised with a time server yet.")

    days = mk.get("days_left")
    if not mk.get("installed"):
        bad.append("MakeMKV is not installed, so no disc can be read.")
    elif days is not None and not clock.get("plausible"):
        warn.append("Can't tell whether the MakeMKV key is valid: the clock is wrong.")
    elif days is not None and days <= 0:
        bad.append("The MakeMKV key has expired.")
    elif mk.get("key_stale"):
        warn.append("A newer MakeMKV key has been published.")
    elif days is not None and days <= int(db.get("warn_key_days") or 7):
        warn.append("The MakeMKV key expires in %d day(s)." % days)

    if not drives:
        bad.append("No optical drive detected.")
    if not share:
        warn.append("No network share configured.")
    if not wifi.get("connected"):
        bad.append("Wi-Fi is not connected.")
    if storage.get("dedicated") is False:
        warn.append("Rips are staged on the system filesystem.")
    if mode == "degraded":
        warn.append("Not enough free space to rip safely.")
    return ("error" if bad else "warning" if warn else "ok"), bad + warn


def _library(share):
    if not share:
        return "not_configured", None, {"problem": "No library share is set up."}
    lib = P.library_status(share)
    attrs = {"share": "//%s/%s" % (share["host"], share["path"]),
             "mounted": bool(lib.get("mounted"))}
    state = "ready"
    if not share.get("verified_at"):
        state, attrs["problem"] = "error", "The share has not passed its write test."
    elif db.get("transfer_mode") == "direct" and not lib.get("mounted"):
        state, attrs["problem"] = "error", ("The share is not mounted, and rips are "
                                           "set to go straight to it.")
    return state, lib.get("free_bytes") if lib.get("mounted") else None, attrs


def _update_values():
    r = updater.last_result
    if r is None and not _S.update_asked and db.get("auto_check_updates", True):
        # The scheduled check may be hours away. Ask once, off this thread: a box
        # with no internet spends the whole timeout finding that out.
        _S.update_asked = True
        threading.Thread(target=updater.check, name="riparr-mqtt-update",
                         daemon=True).start()
    out = {"installed_version": __version__}
    if r:
        out["latest_version"] = r.get("latest") or __version__
        if r.get("url"):
            out["release_url"] = r["url"]
    return {"update/state": out,
            "update/available": (r.get("status") == "update") if r else None}


def _slow_values(drives):
    sysinfo = P.system_status()
    storage = P.storage_status()
    wifi = P.wifi_status()
    mk = P.makemkv_status()
    share = db.default_share()
    # Once, and shared: before the clock has synced this forks `timedatectl` per call.
    clock = P.clock_status()
    cap = _hooks["capacity"](storage["free_bytes"])

    health, problems = _health(clock, mk, drives, share, wifi, storage, cap.get("mode"))
    lib_state, lib_free, lib_attrs = _library(share)

    # "enabled" is the switch in the sense the page means it: asked for, and able to run.
    v = {"autorip/state": bool(_hooks["autorip_state"]().get("enabled")),
         "system/health": health,
         # No uptime: it differs on every read, and would republish this whole blob
         # every thirty seconds for a number nobody automates on.
         "system/attributes": {"problems": problems, "model": sysinfo.get("model"),
                               "version": __version__},
         "system/cpu_temperature": (round(sysinfo["cpu_temp_c"], 1)
                                    if sysinfo.get("cpu_temp_c") is not None else None),
         "storage/free": _gib(storage.get("free_bytes")),
         "storage/total": _gib(storage.get("total_bytes")),
         "library/state": lib_state,
         "library/free": _gib(lib_free),
         "library/attributes": lib_attrs,
         "network/connected": bool(wifi.get("connected")),
         "network/ssid": wifi.get("ssid") or None,
         "network/signal": wifi.get("signal"),
         "network/ip": wifi.get("ip") or None}
    v.update(_update_values())
    return v


def _values(force_slow=False):
    now = time.time()
    drives = P.optical_drives()
    if force_slow or not _S.slow or now - _S.slow_at >= SLOW:
        _S.slow, _S.slow_at = _slow_values(drives), now
    v = dict(_S.slow)
    v.update(_job_values())
    v.update(_drive_values(drives))
    return v


# ─────────────────────────────── Home Assistant discovery ───────────────────────────────

def _entities():
    """(component, key, config). Topics here are relative; `_discovery` roots them."""
    diag = {"entity_category": "diagnostic"}
    gib = {"unit_of_measurement": "GiB", "device_class": "data_size",
           "state_class": "measurement"}
    return [
        ("sensor", "job", {"name": "Job", "state_topic": "job/state",
                           "json_attributes_topic": "job/attributes",
                           "device_class": "enum", "options": JOB_STATES,
                           "icon": "mdi:disc-player"}),
        ("sensor", "title", {"name": "Current title", "state_topic": "job/title",
                             "icon": "mdi:movie-open"}),
        ("sensor", "progress", {"name": "Progress", "state_topic": "job/progress",
                                "unit_of_measurement": "%", "icon": "mdi:progress-clock"}),
        ("switch", "autorip", {"name": "Auto Rip", "state_topic": "autorip/state",
                               "command_topic": "autorip/set", "icon": "mdi:autorenew"}),
        ("button", "eject", {"name": "Eject disc", "command_topic": "drive/eject",
                             "icon": "mdi:eject"}),
        ("binary_sensor", "drive", {"name": "Drive", "state_topic": "drive/detected",
                                    "json_attributes_topic": "drive/attributes",
                                    "device_class": "plug", **diag}),
        ("binary_sensor", "disc", {"name": "Disc", "state_topic": "drive/disc_present",
                                   "icon": "mdi:disc"}),
        ("sensor", "disc_type", {"name": "Disc type", "state_topic": "drive/disc_type",
                                 "icon": "mdi:disc"}),
        ("sensor", "health", {"name": "Health", "state_topic": "system/health",
                              "json_attributes_topic": "system/attributes",
                              "device_class": "enum",
                              "options": ["ok", "warning", "error"],
                              "icon": "mdi:heart-pulse", **diag}),
        ("sensor", "cpu_temperature", {"name": "CPU temperature",
                                       "state_topic": "system/cpu_temperature",
                                       "unit_of_measurement": "°C",
                                       "device_class": "temperature",
                                       "state_class": "measurement", **diag}),
        ("sensor", "storage_free", {"name": "Card free", "state_topic": "storage/free",
                                    **gib, **diag}),
        ("sensor", "storage_total", {"name": "Card size", "state_topic": "storage/total",
                                     **gib, **diag, "enabled_by_default": False}),
        ("sensor", "library", {"name": "Library", "state_topic": "library/state",
                               "json_attributes_topic": "library/attributes",
                               "device_class": "enum",
                               "options": ["ready", "error", "not_configured"],
                               "icon": "mdi:nas"}),
        ("sensor", "library_free", {"name": "Library free",
                                    "state_topic": "library/free", **gib}),
        ("binary_sensor", "network", {"name": "Network",
                                      "state_topic": "network/connected",
                                      "device_class": "connectivity", **diag}),
        ("sensor", "wifi_ssid", {"name": "Wi-Fi network", "state_topic": "network/ssid",
                                 "icon": "mdi:wifi", **diag}),
        ("sensor", "wifi_signal", {"name": "Wi-Fi signal",
                                   "state_topic": "network/signal",
                                   "unit_of_measurement": "%",
                                   "state_class": "measurement",
                                   "icon": "mdi:wifi-strength-2", **diag}),
        ("sensor", "ip", {"name": "IP address", "state_topic": "network/ip",
                          "icon": "mdi:ip-network", **diag,
                          "enabled_by_default": False}),
        ("update", "update", {"name": "Riparr", "state_topic": "update/state",
                              "device_class": "firmware"}),
    ]


def _discovery_topic(prefix, component, key):
    return "%s/%s/%s/%s/config" % (prefix, component, device_id(), key)


def _withdraw(prefix):
    for component, key, _ in _entities():
        _S.client.publish(_discovery_topic(prefix, component, key), "", qos=1,
                          retain=True)
    log.info("Removed the Home Assistant entities announced under %s/", prefix)


def _leaving(new):
    """Take the announcement back from a broker the box is about to stop talking to.

    `_discovery` only runs after a connect. Turning MQTT off, or naming a different
    broker, means there is no later connect to this one -- so anything announced here
    that the new settings do not keep has to be deleted now, while there is still a
    connection to delete it over.
    """
    old = _S.cfg
    if not (old and _S.client and _S.connected):
        return
    was = db.get("mqtt_discovery_published") or ""
    stays = new["enabled"] and (new["host"], new["port"]) == (old["host"], old["port"])
    want = new["prefix"] if stays and new["discovery"] else ""
    if was and was != want:
        _withdraw(was)
        db.set("mqtt_discovery_published", "")


def _discovery(values):
    """Announce the entities, or take back an announcement that is no longer wanted.

    What was announced, and under which prefix, is remembered in settings rather than
    in memory. Discovery messages are retained on the broker, so turning the switch
    off -- or changing the prefix -- has to delete them explicitly, and that has to
    still work after a restart.
    """
    cfg = _S.cfg
    was = db.get("mqtt_discovery_published") or ""
    want = cfg["prefix"] if cfg["discovery"] else ""
    if was and was != want:
        _withdraw(was)
    if was != want:
        db.set("mqtt_discovery_published", want)
    if not want:
        return

    dev = device_id()
    host = P.hostname()
    device = {"identifiers": [dev], "name": "Riparr" if host == "riparr"
              else "Riparr (%s)" % host,
              "manufacturer": "Riparr", "sw_version": __version__}
    model = (values["system/attributes"].get("model") or "").strip()
    if model:
        device["model"] = model
    ip = values["network/ip"]
    if ip:
        port = os.environ.get("RIPARR_PORT") or ""
        device["configuration_url"] = "http://%s%s" % (
            ip, ":" + port if port and port != "80" else "")

    for component, key, conf in _entities():
        conf = dict(conf, unique_id="%s_%s" % (dev, key), device=device,
                    availability_topic=cfg["base"] + "/availability")
        for k in ("state_topic", "command_topic", "json_attributes_topic"):
            if k in conf:
                conf[k] = "%s/%s" % (cfg["base"], conf[k])
        _publish(_discovery_topic(want, component, key), conf, absolute=True)


# ─────────────────────────────── commands ───────────────────────────────

_ON = ("on", "true", "1", "yes")
_OFF = ("off", "false", "0", "no")


def _handle(topic, payload):
    if topic == "autorip/set":
        word = payload.lower()
        if word not in _ON + _OFF:
            log.warning("Ignored %r on autorip/set: send ON or OFF.", payload)
            return
        try:
            _hooks["set_autorip"](word in _ON)
            log.info("Auto Rip turned %s over MQTT", "on" if word in _ON else "off")
        except Exception as e:
            log.warning("Auto Rip was not turned on over MQTT: %s", e)
        # Republish even when nothing changed. A refused "ON" has to be answered with
        # "OFF", or whatever sent it goes on believing the switch moved.
        _S.sent.pop(_S.cfg["base"] + "/autorip/state", None)
    elif topic == "drive/eject":
        busy = db.drive_busy()
        if busy:
            log.warning("Did not eject over MQTT: %s is still using the disc.",
                        busy.get("title") or busy.get("disc_label") or "a rip")
            return
        r = P.eject()
        log.info("Eject over MQTT: %s", r.get("message"))


# ─────────────────────────────── the thread ───────────────────────────────

def _tick():
    cfg = _config()
    if cfg != _S.cfg:
        _leaving(cfg)
        _disconnect()
        _S.cfg, _S.error = cfg, None
        while not _cmds.empty():
            _cmds.get_nowait()
        if cfg["enabled"]:
            _connect(cfg)
    if not _S.client or not _S.connected:
        return

    acted = False
    while not _cmds.empty():
        _handle(*_cmds.get_nowait())
        acted = True

    # Just (re)connected, and not yet published in full for this connection. Marked
    # as done only at the end: if anything below raises, the next tick starts over
    # rather than leaving Home Assistant without its discovery until the next reconnect.
    session = _S.session
    fresh = _S.announced != session
    if fresh:
        # The broker may have lost its retained messages, or be a different broker.
        _S.sent = {}
        _publish("availability", "online")

    values = _values(force_slow=acted or fresh)
    if fresh:
        _discovery(values)
    for suffix, value in values.items():
        _publish(suffix, value)
    _S.announced = session


def _loop():
    while not _stop.is_set():
        try:
            _tick()
        except Exception as e:
            log.error("Tick failed: %s", e)
        _wake.wait(FAST)
        _wake.clear()
    _disconnect()


def start(autorip_state, set_autorip, capacity):
    """Always started. With MQTT off the thread costs one settings read every 5 s,
    and turning it on from the settings page then needs no restart."""
    global _thread
    _hooks.update(autorip_state=autorip_state, set_autorip=set_autorip,
                  capacity=capacity)
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="riparr-mqtt", daemon=True)
    _thread.start()


def stop():
    _stop.set()
    _wake.set()
    if _thread:
        _thread.join(timeout=6)


def poke():
    """Something changed that is worth publishing now rather than in five seconds."""
    _S.slow_at = 0
    _wake.set()


# ─────────────────────────────── for the settings page ───────────────────────────────

def status():
    cfg = _config()
    return {"available": _paho() is not None,
            "enabled": cfg["enabled"],
            "connected": bool(_S.connected),
            "error": _S.error,
            "base_topic": cfg["base"],
            # What an empty field means, which is not the same thing once a topic of
            # one's own is saved -- and is what the field's help has to name.
            "default_base_topic": base_topic("")}


def test():
    """Connect once with the saved settings and report what the broker said.

    Its own short-lived client rather than a look at the running one: the running one
    retries quietly forever, which is right for a broker that reboots and useless for
    somebody who has just typed a password and wants to know whether it was right.
    """
    cfg = _config()
    if not _paho():
        return {"ok": False, "error": NO_LIBRARY}
    if not cfg["host"]:
        return {"ok": False, "error": "Enter the broker's address first."}
    done = threading.Event()
    got = {}

    def on_connect(client, userdata, flags, rc, props=None):
        got["rc"] = rc
        done.set()

    c = _new_client(cfg, "riparr-%s-test" % device_id())
    c.on_connect = on_connect
    try:
        c.connect(cfg["host"], cfg["port"], keepalive=15)
    except Exception as e:
        return {"ok": False, "error": "Couldn't reach %s:%d — %s"
                                      % (cfg["host"], cfg["port"], e)}
    c.loop_start()
    try:
        if not done.wait(TEST_TIMEOUT):
            return {"ok": False,
                    "error": "%s:%d accepted the connection and then said nothing. "
                             "If the broker expects TLS on this port, turn TLS on."
                             % (cfg["host"], cfg["port"])}
        rc = got["rc"]
        if getattr(rc, "is_failure", False):
            return {"ok": False, "error": "The broker refused: %s." % rc}
        return {"ok": True,
                "message": "Connected to %s:%d. %s" % (
                    cfg["host"], cfg["port"],
                    "Riparr publishes under %s/." % cfg["base"] if cfg["enabled"]
                    else "Turn the switch on to start publishing.")}
    finally:
        try:
            c.disconnect()
            c.loop_stop()
        except Exception:
            pass
