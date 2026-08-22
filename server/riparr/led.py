"""
The status LED: the only thing the box can say without a browser.

Every headless scenario in `docs/design/scenarios.md` leans on this, the guide ships a
printable reference card for it, and `01-what-you-need.md` calls it "not optional --
it's the only way the box can tell you something failed." It had no implementation at
all. This is it.

## One writer, polling, no callbacks

The LED is derived from state that already exists rather than pushed to from every
place that changes something. A `db.set()` scattered through the rip engine and the
setup flow and the share code would be a dozen call sites that each have to remember,
and the first one that forgets leaves the box lying about what it is doing -- with no
screen to contradict it. So: one thread, one second, reads the world, decides a colour.

The cost is up to a second of lag on a transition, against a rip that takes three
hours. That is the correct trade.

## Nothing here can take the box down

The driver is optional and its absence is not an error. A board with no LED wired, no
`spidev` node, no permission on it, or an SPI controller that is not enabled in the
device tree all end up in the same place: `available()` is False, the thread still
runs, and every write is dropped. The appliance is fully functional without an LED --
it is just less pleasant to own.

## The hardware path is UNVERIFIED

WS2812 timing is done by **SPI**, not by the `rpi_ws281x` route, which is Broadcom
PWM/DMA and does not exist on an Allwinner H618. Each WS2812 bit becomes three SPI
bits at 2.4 MHz -- `110` for a 1, `100` for a 0 -- which lands each bit at 1.25 us,
inside the part's tolerance. This is the standard technique on non-Broadcom SoCs and
it is written from the datasheet, **not confirmed against a real LED**, because there
was no board with one wired when it was written.

Two things to check on the first board that has one, in this order:

  1. `ls /dev/spidev*` -- SPI must be enabled in the device tree first
     (`armbian-config` -> System -> Hardware -> `spi-spidev`), and this file cannot
     do that for you.
  2. `POST /api/system/led/test` -- walks red, green, blue, off, one second each.
     If the colours are wrong but present, `ORDER` below is the fix; WS2812 is GRB
     and some near-identical parts are RGB.
"""
import os
import threading
import time

from . import db, platform as P, system as SY

log = SY.component("LED")

# The device the LED hangs off. SPI1 is the Zero 2W's exposed bus; a board that puts
# it elsewhere sets RIPARR_LED_SPI rather than editing this.
SPI_DEV = os.environ.get("RIPARR_LED_SPI", "/dev/spidev1.0")

# WS2812 is green-red-blue on the wire. Every colour below is written in the order a
# person reads it and reordered once, here, at the point of transmission.
ORDER = (1, 0, 2)

# Full brightness on a WS2812 inside a small sealed box is unpleasant to sit next to
# and tells you nothing extra. Scaled once, centrally, so no state has to think about it.
BRIGHTNESS = float(os.environ.get("RIPARR_LED_BRIGHTNESS", "0.35"))


# ─────────────────────────────── the vocabulary ───────────────────────────────

RED = (255, 40, 40)
GREEN = (40, 220, 70)
BLUE = (50, 120, 255)
AMBER = (255, 150, 20)
PURPLE = (180, 70, 220)
WHITE = (200, 200, 200)
OFF = (0, 0, 0)

# name -> (colour, pattern). Patterns are interpreted by `_level()` below.
#
# This table is the same one printed in docs/guide/led-reference.md. If one changes,
# the other is wrong -- there is no way to make a printed card import a constant, so
# the next best thing is that they sit under the same names.
STATES = {
    "booting":   (WHITE, "pulse-slow"),   # or waiting for setup
    "joining":   (BLUE, "blink"),         # joining Wi-Fi
    "ready":     (GREEN, "solid"),        # feed it a disc
    "ripping":   (BLUE, "breathe"),
    "uploading": (AMBER, "pulse"),        # the one that matters: still working
    "verifying": (AMBER, "pulse"),
    "done":      (GREEN, "flash"),        # then back to ready
    "failed":    (RED, "solid"),
    "duplicate": (PURPLE, "solid"),
    "needs_you": (AMBER, "blink"),
    "no_share":  (AMBER, "blink"),        # paused, can't reach the library
    "no_wifi":   (AMBER, "blink"),        # couldn't join -- the AP-mode case
}

# How long "done" and "duplicate" hold before falling back to whatever is true. They
# are transitions, not states, and a green box that has been flashing since Tuesday
# is not telling anybody anything.
TRANSIENT_SECONDS = 20
_transient = {"name": None, "until": 0}


def announce(name):
    """Show a momentary state -- "done", "duplicate" -- for a few seconds.

    Called from the rip engine at the two points where the *event* is the message and
    the state afterwards is unremarkable. Everything else is derived.
    """
    if name in STATES:
        _transient["name"] = name
        _transient["until"] = time.time() + TRANSIENT_SECONDS


# ─────────────────────────────── what is true ───────────────────────────────

def current_state():
    """The one state that best describes the box right now.

    Order is priority, and it is a judgement: a failure outranks activity, activity
    outranks readiness, and "I need a human" outranks all of them, because it is the
    only state where nothing happens until somebody acts.
    """
    now = time.time()
    if _transient["name"] and now < _transient["until"]:
        return _transient["name"]

    try:
        job = db.active_job()
    except Exception:
        job = None

    if job:
        state = job.get("state")
        if state == "needs_input":
            return "needs_you"
        if state in ("ripping", "identifying"):
            return "ripping"
        if state == "transferring":
            return "uploading"
        if state == "verifying":
            return "verifying"

    try:
        if not P.wifi_status().get("connected"):
            return "no_wifi"
        if not db.get("setup_complete"):
            return "booting"
        if not db.default_share():
            return "no_share"
    except Exception:
        return "booting"
    return "ready"


# ─────────────────────────────── the patterns ───────────────────────────────

def _level(pattern, t):
    """0.0-1.0 brightness for a pattern at time `t`, in seconds since anything.

    Kept as pure arithmetic on the clock rather than a sequence of sleeps, so the
    thread has exactly one timing loop and a state change lands within a tick instead
    of after whatever animation was mid-flight.
    """
    if pattern == "solid":
        return 1.0
    if pattern == "blink":
        return 1.0 if (t % 1.0) < 0.5 else 0.0
    if pattern == "flash":
        return 1.0 if (t % 0.4) < 0.2 else 0.0
    if pattern == "pulse":
        return 0.25 + 0.75 * _triangle(t, 1.6)
    if pattern == "pulse-slow":
        return 0.15 + 0.60 * _triangle(t, 3.0)
    if pattern == "breathe":
        # Slow and shallow. "Breathing" has to read as resting, not as urgency --
        # this runs for three hours and somebody is in the room.
        return 0.30 + 0.55 * _triangle(t, 4.0)
    return 1.0


def _triangle(t, period):
    """A 0-1-0 ramp. Linear, not sinusoidal: an LED's perceived brightness is already
    non-linear in its duty cycle, and stacking a sine on top of that reads as a pause
    at both ends rather than a smooth swing."""
    x = (t % period) / period
    return 2 * x if x < 0.5 else 2 * (1 - x)


# ─────────────────────────────── the wire ───────────────────────────────

_LUT = {0: 0b100, 1: 0b110}

# Logged once, not every tick: a box with no LED must not fill its own event log.
_warned = False


def _encode(colour):
    """One WS2812 pixel as SPI bytes.

    24 colour bits become 72 SPI bits, which is 9 whole bytes -- the reason 3 bits per
    bit is the encoding everyone uses rather than 4.
    """
    bits = 0
    for idx in ORDER:
        for shift in range(7, -1, -1):
            bits = (bits << 3) | _LUT[(colour[idx] >> shift) & 1]
    return bits.to_bytes(9, "big")


def available():
    return (not P.MOCK) and os.path.exists(SPI_DEV) and os.access(SPI_DEV, os.W_OK)


def _write(colour):
    """Push one colour out, or quietly do nothing.

    The trailing zero bytes are the WS2812 reset: the part latches on a low period of
    at least 50 us, and without it the first pixel keeps the last frame's data.
    """
    if not available():
        return False
    try:
        with open(SPI_DEV, "wb", buffering=0) as f:
            f.write(_encode(colour) + b"\x00" * 42)
        return True
    except OSError as e:
        # Once, not every second: a missing LED must not fill the event log.
        global _warned
        if not _warned:
            _warned = True
            log.warning("The status LED could not be written (%s). Riparr works "
                        "without it; the web interface is the fallback.", e)
        return False


def _scaled(colour, level):
    k = BRIGHTNESS * max(0.0, min(1.0, level))
    return tuple(int(round(c * k)) for c in colour)


# ─────────────────────────────── the thread ───────────────────────────────

_stop = threading.Event()
_thread = None
_last = {"state": None}

# Fast enough that a breathe reads as smooth, slow enough to be free on a board that
# is doing something else with its one useful core.
TICK = 0.05


def _loop():
    started = time.time()
    while not _stop.wait(TICK):
        try:
            name = current_state()
            if name != _last["state"]:
                log.debug("LED -> %s", name)
                _last["state"] = name
            colour, pattern = STATES.get(name, (OFF, "solid"))
            _write(_scaled(colour, _level(pattern, time.time() - started)))
        except Exception:
            # A thread that dies takes the LED with it for the rest of the uptime,
            # and nothing else notices. Carrying on with a wrong colour beats that.
            pass


def start():
    """Always started, even with no LED attached.

    The alternative -- probe once at boot and skip the thread -- makes plugging an LED
    into a running box do nothing until it is restarted, and "I wired it up and it
    stayed dark" is the least debuggable outcome available.
    """
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="riparr-led", daemon=True)
    _thread.start()
    log.info("Status LED %s", ("active on %s" % SPI_DEV) if available()
             else "not detected — the web interface is the only status channel")


def stop():
    _stop.set()
    _write(OFF)


def self_test():
    """Walk the primaries so somebody who just wired one up can prove it in ten seconds.

    Returns what it did, including whether there was anything there to do it to, so
    the interface can say "no LED detected" rather than claiming success at a dark box.
    """
    if not available():
        return {"ok": False, "detected": False,
                "message": "No status LED detected at %s. SPI has to be enabled in "
                           "the device tree before the device node exists." % SPI_DEV}
    for colour in (RED, GREEN, BLUE, WHITE):
        _write(_scaled(colour, 1.0))
        time.sleep(0.6)
    _write(OFF)
    _last["state"] = None                 # force the loop to redraw the real state
    return {"ok": True, "detected": True,
            "message": "Walked red, green, blue, white. If the colours came out in a "
                       "different order the LED is RGB rather than GRB."}
