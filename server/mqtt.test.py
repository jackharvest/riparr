#!/usr/bin/env python3
"""MQTT: what the box says on a broker, and the two things a broker may ask of it.

Two halves. The first needs nothing: what a job looks like as topics, and that the
Home Assistant entities are well formed. The second talks to a real broker --
`mosquitto` if it is installed, or one already running at RIPARR_TEST_MQTT=host:port
-- because the things that go wrong here (retained messages, the last will, a command
arriving on reconnect) only exist on a wire.

Run: python3 server/mqtt.test.py
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

_tmp = tempfile.mkdtemp(prefix="riparr-mqtt-test-")
os.environ["RIPARR_DB"] = os.path.join(_tmp, "test.db")
os.environ["RIPARR_APPLIANCE"] = "0"

from riparr import db, mqtt as MQ, platform as P, system as SY, updater  # noqa: E402

failures = []


def check(name, got, want):
    if got == want:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s: got %r, wanted %r" % (name, got, want))
        failures.append(name)


db.init()
SY.init()
# Never GitHub from a test: pretend the scheduled check already answered.
updater.last_result = {"status": "update", "latest": "99.0.0", "url": "http://x/rel"}

# ─────────────────────────────── without a broker ───────────────────────────────

print("A job, as topics")
v = MQ._job_values()
check("nothing in flight is idle", v["job/state"], "idle")
check("idle has no title", v["job/title"], None)
check("idle is 0 percent, not unknown", v["job/progress"], 0.0)

rip = db.create_job(title="Arthur Christmas", state="ripping", kind="movie",
                    started_at=int(time.time()))
db.update_job(rip, stage_pct=0.4321)
v = MQ._job_values()
check("the rip is the job", (v["job/state"], v["job/title"]), ("ripping", "Arthur Christmas"))
check("progress is a percentage", v["job/progress"], 43.2)

send = db.create_job(title="Megamind", state="transferring", kind="movie",
                     started_at=int(time.time()) + 1, bytes_total=1000, bytes_sent=250)
v = MQ._job_values()
check("the disc in the drive outranks the upload", v["job/title"], "Arthur Christmas")
check("the upload is still counted", v["job/attributes"]["sending"], 1)

db.update_job(rip, state="done")
v = MQ._job_values()
check("then the upload is the job", (v["job/state"], v["job/progress"]),
      ("transferring", 25.0))

db.update_job(send, state="failed", error="The share went away")
v = MQ._job_values()
check("a failure stays visible", (v["job/state"], v["job/title"]), ("failed", "Megamind"))
check("and says why", v["job/attributes"].get("error"), "The share went away")
check("every state published is one Home Assistant was told about",
      v["job/state"] in MQ.JOB_STATES, True)

nxt = db.create_job(title="Next", state="identifying", started_at=int(time.time()) + 2)
check("until the next disc replaces it", MQ._job_values()["job/state"], "identifying")
db.update_job(nxt, state="cancelled")
db.update_job(send, state="done")
check("a done job with no finish time is just idle", MQ._job_values()["job/state"], "idle")

print("Done, then idle")
now = int(time.time())
db.update_job(send, finished_at=now - 5)
v = MQ._job_values()
check("a rip that just finished reads done", (v["job/state"], v["job/title"], v["job/progress"]),
      ("done", "Megamind", 100.0))
check("done is a state Home Assistant was told about", "done" in MQ.JOB_STATES, True)
db.update_job(send, finished_at=now - 601)
check("ten minutes on it is idle again", MQ._job_values()["job/state"], "idle")
db.set("mqtt_done_seconds", 30)
db.update_job(send, finished_at=now - 20)
check("the timeout is a setting: inside it", MQ._job_values()["job/state"], "done")
db.update_job(send, finished_at=now - 40)
check("the timeout is a setting: outside it", MQ._job_values()["job/state"], "idle")
db.set("mqtt_done_seconds", 0)
db.update_job(send, finished_at=now)
check("0 skips done", MQ._job_values()["job/state"], "idle")
db.set("mqtt_done_seconds", 600)
db.update_job(send, finished_at=now)
nxt = db.create_job(title="Another", state="identifying", started_at=now + 3)
check("a new disc ends done at once", MQ._job_values()["job/state"], "identifying")
db.update_job(nxt, state="cancelled")
db.update_job(send, finished_at=None)

print("A failure that ends after a later disc")
now = int(time.time())
db.conn().execute("DELETE FROM jobs")
db.conn().commit()
a = db.create_job(title="Upload A", state="transferring", started_at=now - 1800)
b = db.create_job(title="Disc B", state="done", started_at=now - 1500, finished_at=now - 1200)
db.update_job(a, state="failed", finished_at=now - 60, error="The share went away")
v = MQ._job_values()
check("an upload that fails after the next disc finished still reads failed",
      (v["job/state"], v["job/title"]), ("failed", "Upload A"))
c = db.create_job(title="Disc C", state="cancelled", started_at=now - 30, finished_at=now - 20)
check("and a disc put in since then clears it", MQ._job_values()["job/state"], "idle")
db.conn().execute("DELETE FROM jobs")
db.conn().commit()

print("Topics and entities")
db.set("mqtt_base_topic", "/home/+/rip#/")
check("wildcards never reach a published topic", MQ.base_topic(), "home/rip")
db.set("mqtt_base_topic", "")
check("the default base names the box", MQ.base_topic(),
      "riparr/" + MQ._slug(P.hostname()))
ents = MQ._entities()
check("entity keys are unique", len({(c, k) for c, k, _ in ents}), len(ents))
check("everything but the button reports a state",
      [k for c, k, conf in ents if "state_topic" not in conf], ["eject"])

# ─────────────────────────────── against a broker ───────────────────────────────

broker = None
target = os.environ.get("RIPARR_TEST_MQTT")
if not target and shutil.which("mosquitto"):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    conf = os.path.join(_tmp, "mosquitto.conf")
    with open(conf, "w") as f:
        f.write("listener %d 127.0.0.1\nallow_anonymous true\n" % port)
    broker = subprocess.Popen(["mosquitto", "-c", conf], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL)
    target = "127.0.0.1:%d" % port
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)

if not target:
    print("SKIP the broker half: no mosquitto, and RIPARR_TEST_MQTT is not set")
else:
    import paho.mqtt.client as paho

    host, _, port = target.partition(":")
    port = int(port or 1883)
    base = "riparr-test/%d" % os.getpid()
    prefix = "ha-test-%d" % os.getpid()

    seen, lock = {}, threading.Lock()

    def on_message(client, userdata, msg):
        with lock:
            seen[msg.topic] = msg.payload.decode()

    watcher = paho.Client(paho.CallbackAPIVersion.VERSION2)
    watcher.on_message = on_message
    watcher.connect(host, port)
    watcher.subscribe([(base + "/#", 1), (prefix + "/#", 1)])
    watcher.loop_start()

    def got(suffix, root=base):
        with lock:
            return seen.get("%s/%s" % (root, suffix))

    def wait(name, fn, want, timeout=10):
        end = time.time() + timeout
        while time.time() < end and fn() != want:
            time.sleep(0.05)
        check(name, fn(), want)

    ready = {"ready": True}
    ejected = []
    P.eject = lambda *a, **k: ejected.append(1) or {"ok": True, "message": "Tray ejected"}

    def set_autorip(on):
        if on and not ready["ready"]:
            raise ValueError("Auto Rip isn't ready yet")
        db.set("auto_rip", bool(on))

    MQ.FAST = 0.2
    for k, val in {"mqtt_host": host, "mqtt_port": port, "mqtt_base_topic": base,
                   "mqtt_discovery_prefix": prefix, "mqtt_enabled": True}.items():
        db.set(k, val)

    print("The settings page's test button")
    check("a broker that answers", MQ.test()["ok"], True)
    db.set("mqtt_port", 1)
    check("a broker that is not there", MQ.test()["ok"], False)
    db.set("mqtt_port", port)

    print("Publishing")
    MQ.start(autorip_state=lambda: {"enabled": bool(db.get("auto_rip")) and ready["ready"]},
             set_autorip=set_autorip,
             capacity=lambda free: {"mode": "burst"})
    wait("says it is online", lambda: got("availability"), "online")
    wait("job state", lambda: got("job/state"), "idle")
    wait("an absent value is None, not an empty payload", lambda: got("job/title"), "None")
    wait("auto rip", lambda: got("autorip/state"), "OFF")
    wait("drive", lambda: got("drive/detected"), "ON")
    wait("health is one word", lambda: got("system/health") in ("ok", "warning", "error"),
         True)
    wait("the temperature is a number", lambda: float(got("system/cpu_temperature") or 0) > 0,
         True)
    wait("update", lambda: json.loads(got("update/state") or "{}").get("latest_version"),
         "99.0.0")
    check("status agrees", MQ.status()["connected"], True)

    print("Home Assistant discovery")
    dev = MQ.device_id()
    wait("the switch is announced",
         lambda: got("switch/%s/autorip/config" % dev, prefix) is not None, True)
    sw = json.loads(got("switch/%s/autorip/config" % dev, prefix))
    check("with absolute topics", (sw["state_topic"], sw["command_topic"]),
          (base + "/autorip/state", base + "/autorip/set"))
    check("and an availability topic", sw["availability_topic"], base + "/availability")
    with lock:
        configs = [t for t in seen if t.startswith(prefix + "/")]
    check("one config per entity", len(configs), len(MQ._entities()))

    print("Commands")
    watcher.publish(base + "/autorip/set", "ON", qos=1)
    wait("ON turns Auto Rip on", lambda: got("autorip/state"), "ON")
    check("in the database too", db.get("auto_rip"), True)
    watcher.publish(base + "/autorip/set", "OFF", qos=1)
    wait("OFF turns it off", lambda: got("autorip/state"), "OFF")

    ready["ready"] = False
    with lock:
        seen.pop(base + "/autorip/state")
    watcher.publish(base + "/autorip/set", "ON", qos=1)
    wait("a refused ON is answered with OFF", lambda: got("autorip/state"), "OFF")
    check("and nothing was switched", db.get("auto_rip"), False)
    ready["ready"] = True

    watcher.publish(base + "/drive/eject", "PRESS", qos=1)
    wait("eject opens the tray", lambda: len(ejected), 1)
    busy = db.create_job(title="Busy", state="ripping", started_at=int(time.time()) + 5)
    wait("the rip shows up by itself", lambda: got("job/state"), "ripping")
    watcher.publish(base + "/drive/eject", "PRESS", qos=1)
    time.sleep(1)
    check("but not while a rip holds the disc", len(ejected), 1)
    db.update_job(busy, state="done")
    wait("and back to idle", lambda: got("job/state"), "idle")

    print("Changing settings while it runs")
    db.set("mqtt_discovery", False)
    MQ.poke()
    wait("turning discovery off takes the entities back",
         lambda: got("switch/%s/autorip/config" % dev, prefix), "")
    wait("and it comes back online", lambda: got("availability"), "online")

    db.set("mqtt_enabled", False)
    MQ.poke()
    wait("turning MQTT off says offline", lambda: got("availability"), "offline")
    check("status agrees", MQ.status()["connected"], False)

    print("Reconnecting")
    # A retained command is what a careless automation leaves behind, and the broker
    # hands it to every new subscription. It must not open the tray on each reconnect.
    watcher.publish(base + "/drive/eject", "PRESS", qos=1, retain=True)
    time.sleep(0.5)
    db.set("mqtt_enabled", True)
    MQ.poke()
    wait("back online", lambda: got("availability"), "online")
    time.sleep(1)
    check("a retained eject was not obeyed", len(ejected), 1)
    watcher.publish(base + "/drive/eject", "", qos=1, retain=True)

    MQ.stop()
    # Leave the broker as it was found: it may be somebody's real one.
    with lock:
        topics = [t for t, p in seen.items() if p != ""]
    for t in topics:
        watcher.publish(t, "", qos=1, retain=True)
    time.sleep(0.5)
    watcher.loop_stop()
    watcher.disconnect()

if broker:
    broker.terminate()
shutil.rmtree(_tmp, ignore_errors=True)

print()
if failures:
    print("FAILED: %d check(s)" % len(failures))
    for f in failures:
        print("  - " + f)
    sys.exit(1)
print("All MQTT checks passed.")
