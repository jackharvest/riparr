"""
Everything that differs between the appliance and a development Mac.

The rest of the codebase talks to this module and never shells out directly, so the
whole app runs on a laptop with realistic fake data and runs unchanged on the Pi.
`IS_APPLIANCE` is the only switch.
"""
import os
import platform as _p
import re
import shutil
import socket
import subprocess
import threading
import time

from . import drives as DRV, optical as OPT

def _is_appliance():
    """Are we running on the box, or on a development machine?

    This used to require the string "Raspberry Pi" in the device-tree model, which was
    wrong the moment the hardware turned out to be an Orange Pi Zero 2W (Allwinner
    H618). The failure was silent and nasty: the service would have come up in MOCK
    mode on real hardware and served fabricated discs and fake Wi-Fi to a UI that
    looked entirely correct.

    A device-tree model node is the actual thing being tested for -- every ARM SBC has
    one and no ordinary desktop or VM does. RIPARR_APPLIANCE overrides either way.
    """
    env = os.environ.get("RIPARR_APPLIANCE")
    if env is not None:
        return env not in ("", "0", "no", "false")
    return (_p.system() == "Linux"
            and os.path.exists("/proc/device-tree/model")
            and bool(open("/proc/device-tree/model", errors="ignore").read().strip("\x00").strip()))


IS_APPLIANCE = _is_appliance()
MOCK = not IS_APPLIANCE


def hostname():
    return socket.gethostname().split(".")[0]


# ─────────────────────────────── system ───────────────────────────────

def system_status():
    if MOCK:
        return {
            "model": "OrangePi Zero2W (simulated)",
            "os": "Armbian 25.x (Debian trixie), minimal",
            "kernel": "6.12.0-current-sunxi64",
            "uptime_seconds": 48213,
            "memory_total_mb": 2048,
            "memory_used_mb": 214,
            "cpu_temp_c": 46.8,
            "throttled": False,
            "board": "orangepizero2w",
            "mock": True,
        }
    model = _read("/proc/device-tree/model").strip("\x00").strip()
    mem = {}
    for line in _read("/proc/meminfo").splitlines():
        k, _, v = line.partition(":")
        mem[k] = int(v.strip().split()[0]) if v.strip().split() else 0
    total = mem.get("MemTotal", 0) // 1024
    avail = mem.get("MemAvailable", 0) // 1024
    # vcgencmd is Broadcom firmware; Allwinner boards expose the same things through
    # the kernel thermal zones instead. Try the generic path when vcgencmd is absent.
    temp = _run(["vcgencmd", "measure_temp"]) if shutil.which("vcgencmd") else None
    if not temp:
        raw = _read("/sys/class/thermal/thermal_zone0/temp").strip()
        temp = str(int(raw) / 1000.0) if raw.isdigit() else ""
    m = re.search(r"([\d.]+)", temp or "")
    thr = (_run(["vcgencmd", "get_throttled"]) or "") if shutil.which("vcgencmd") else ""
    return {
        "model": model,
        "os": _osname(),
        "kernel": _p.release(),
        "uptime_seconds": int(float(_read("/proc/uptime").split()[0] or 0)),
        "memory_total_mb": total,
        "memory_used_mb": total - avail,
        "cpu_temp_c": float(m.group(1)) if m else None,
        # No vcgencmd means no throttle telemetry, which is not the same as throttled.
        "throttled": ("0x0" not in thr) if thr else False,
        # The board the Preparer prepared this card for, recorded in riparr.conf and
        # loaded into the service environment. `model` is what the hardware reports it
        # actually is; a mismatch between the two is a support signal worth seeing.
        "board": os.environ.get("RIPARR_BOARD") or None,
        "mock": False,
    }


def storage_status():
    """Staging capacity, expressed so the UI can talk in discs rather than gigabytes.

    Falls back to whatever filesystem holds the staging path when the dedicated staging
    partition (D4) does not exist. Stock Raspberry Pi OS resizes rootfs to fill the card,
    so on any image that is not yet a Riparr image there is no /srv/staging — and a
    statvfs on a missing path would otherwise 500 the entire status page.
    """
    if MOCK:
        total, free = 22.8 * 2**30, 16.1 * 2**30
        return {"total_bytes": int(total), "free_bytes": int(free),
                "used_bytes": int(total - free), "path": STAGING, "dedicated": True}

    path, dedicated = STAGING, True
    if not os.path.isdir(path):
        path, dedicated = "/", False
    try:
        st = os.statvfs(path)
    except OSError:
        return {"total_bytes": 0, "free_bytes": 0, "used_bytes": 0,
                "path": path, "dedicated": False, "error": "staging is not readable"}
    total = st.f_blocks * st.f_frsize
    free = st.f_bavail * st.f_frsize
    return {"total_bytes": int(total), "free_bytes": int(free),
            "used_bytes": int(total - free), "path": path, "dedicated": dedicated}


STAGING = "/srv/staging" if IS_APPLIANCE else "/tmp/riparr-staging"


# ─────────────────────────────── optical ───────────────────────────────

# A drive's own capabilities never change while it is plugged in, and asking costs a
# SCSI command that spins an idle drive up. Asked once per device identity and kept.
_caps_cache = {}


def _capabilities(device, vendor, model):
    key = (device, vendor, model)
    if key not in _caps_cache:
        _caps_cache[key] = OPT.capabilities(device)
    return _caps_cache[key]


# Per-insertion cache of the things that cost a subprocess: media kind, volume label
# and size. Cleared the moment the tray reports empty, which is the only way a disc can
# be swapped. See the comment inside optical_drives().
_disc_cache = {}


def optical_drives():
    """Every optical drive, what it can read, and what is in it right now.

    `present`, `media` and `label` used to be hardcoded to False/None/None on real
    hardware -- nothing ever filled them in, and the mock branch was the only place a
    disc was ever reported as loaded. Everything that starts a rip keys off `present`,
    so on the appliance there was no way to start one: the watcher never fired, Auto
    Rip was inert, and the "Rip this disc" button never rendered. `optical.py` is what
    makes these fields answer from the hardware.

    Kept cheap on purpose. The disc watcher calls this every three seconds forever, so
    what happens per call is one ioctl the kernel answers from state it already has,
    plus -- only when a disc is actually loaded -- the media and label lookups. The
    drive's own capability list is cached, and MakeMKV is not consulted at all; that is
    `libredrive_status()`, which is far too expensive to sit in a poll loop.
    """
    if MOCK:
        return _mock_drives()
    out = []
    for dev in sorted(_glob("/dev/sr*")):
        name = os.path.basename(dev)
        # The names are in sysfs; reading them saves showing a nameless drive.
        vendor = _read("/sys/block/%s/device/vendor" % name).strip()
        model = _read("/sys/block/%s/device/model" % name).strip()

        caps = _capabilities(dev, vendor, model)
        present, tray = OPT.tray_status(dev)
        uhd, known = DRV.expectation(vendor, model, can_read_bluray=caps.get("bluray"))

        d = {"device": dev, "vendor": vendor, "model": model,
             "present": present, "tray": tray,
             "media": None, "label": None, "size_bytes": 0,
             "reads_dvd": bool(caps.get("dvd")), "reads_bluray": bool(caps.get("bluray")),
             "uhd": uhd, "known_as": known["name"] if known else None,
             "form": known["form"] if known else None}

        if present:
            # Read the disc's identity once per insertion, not once per call. The
            # label lookup shells out to `blkid`, which opens the drive -- and this
            # function is called by the 3s disc watcher AND by every /api/status, which
            # the browser polls once a second while a rip runs. That had `blkid`
            # reopening the drive underneath MakeMKV continuously: makemkvcon spent
            # two minutes spinning on futexes having read 6 MB, and it looked like
            # "reading the disc is slow". A closed tray cannot change discs, so the
            # answer cannot go stale; `present` going False is the invalidation.
            cached = _disc_cache.get(dev)
            if cached is None:
                profile, _ = OPT.get_configuration(dev)
                cached = {
                    "media": OPT.PROFILES.get(profile) if profile else None,
                    "media_kind": OPT.profile_kind(profile) if profile else None,
                    "label": OPT.volume_label(dev) or None,
                    "size_bytes": OPT.disc_size_bytes(dev),
                }
                _disc_cache[dev] = cached
            d.update(cached)
        else:
            _disc_cache.pop(dev, None)
        out.append(d)
    return out


# The mock drive and the mock disc are separate knobs because the interesting cases
# are the mismatches -- a UHD disc in a Blu-ray drive is the whole reason `drives.py`
# exists, and it has to be reachable without owning either.
#
#   RIPARR_MOCK_DRIVE = uhd | bluray | dvd | none
#   RIPARR_MOCK_DISC  = uhd | bluray | dvd | none
_MOCK_DRIVES = {
    "uhd": {"vendor": "HL-DT-ST", "model": "BD-RE BU40N",
            "reads_dvd": True, "reads_bluray": True},
    "bluray": {"vendor": "Pioneer", "model": "BDR-XD08U",
               "reads_dvd": True, "reads_bluray": True},
    "dvd": {"vendor": "HL-DT-ST", "model": "DVDRAM GP65NB60",
            "reads_dvd": True, "reads_bluray": False},
}

_MOCK_DISCS = {
    "uhd": {"media": "BD-ROM", "media_kind": "bluray", "label": "DUNE_UHD",
            "size_bytes": 62 * 2 ** 30},
    "bluray": {"media": "BD-ROM", "media_kind": "bluray", "label": "THE_MATRIX",
               "size_bytes": 24 * 2 ** 30},
    "dvd": {"media": "DVD-ROM", "media_kind": "dvd", "label": "THE_MATRIX",
            "size_bytes": 7 * 2 ** 30},
}


def _mock_drives():
    which = os.environ.get("RIPARR_MOCK_DRIVE", "bluray")
    if which == "none":
        return []
    spec = _MOCK_DRIVES.get(which, _MOCK_DRIVES["bluray"])
    caps = {"dvd": spec["reads_dvd"], "bluray": spec["reads_bluray"]}
    uhd, known = DRV.expectation(spec["vendor"], spec["model"],
                                 can_read_bluray=caps["bluray"])
    d = {"device": "/dev/sr0", "vendor": spec["vendor"], "model": spec["model"],
         "present": False, "tray": "empty",
         "media": None, "media_kind": None, "label": None, "size_bytes": 0,
         "reads_dvd": caps["dvd"], "reads_bluray": caps["bluray"],
         "uhd": uhd, "known_as": known["name"] if known else None,
         "form": known["form"] if known else None}

    disc = os.environ.get("RIPARR_MOCK_DISC", "bluray")
    if disc != "none" and disc in _MOCK_DISCS:
        # A drive that cannot read the medium still reports the tray as loaded -- that
        # is what the hardware does, and pretending otherwise would hide the exact
        # mismatch this knob exists to exercise.
        d.update(_MOCK_DISCS[disc], present=True, tray="loaded")
    return [d]


# ─────────────────────── UHD: the question only MakeMKV can answer ───────────────────────

# Probing costs a makemkvcon run, so the answer is kept for the life of the process
# per drive identity. A drive does not gain LibreDrive support while plugged in, and
# the one thing that would change the answer -- installing MakeMKV -- restarts nothing
# but is caught by the `installed` guard below returning `None` until it is there.
_libredrive_cache = {}
# key -> Event, set when that key's probe finishes. Without this, every poll that
# arrives during the ~2 minutes a probe takes starts its own `makemkvcon`: the cache is
# only written *after* the run, so it cannot suppress a stampede. Nine of them piled up
# on the first real drive we ever attached.
_libredrive_inflight = {}
_libredrive_lock = threading.Lock()


def libredrive_status(drive, block=False):
    """Whether MakeMKV can get underneath this drive's firmware -- the UHD question.

    `drives.py` explains why nothing else can answer it: there is no MMC profile for
    UHD, so neither the drive nor the disc will say. MakeMKV's LibreDrive is the only
    route to a UHD disc on this hardware, and MakeMKV is the only thing that knows
    whether it has one.

    Returns "enabled", "possible", "no", or None for "not asked / cannot tell".

    Costs a ~2 minute `makemkvcon` run against a real drive, so by default it is started
    in the background and this returns None until the answer is cached. `block=True` is
    for the rip path, where the refusal actually has to be correct and waiting is fine.
    **Never block a polled endpoint on this** -- `/api/status` did, and the dashboard
    polls it, so signing in hung on the first drive we ever attached.
    """
    if MOCK:
        return {"uhd": "enabled", "bluray": "no", "dvd": None}.get(
            os.environ.get("RIPARR_MOCK_DRIVE", "bluray"))
    if not drive or not drive.get("reads_bluray"):
        return None                      # a DVD drive has no UHD question to ask
    if not makemkv_status().get("installed"):
        return None

    key = (drive.get("vendor"), drive.get("model"))
    if key in _libredrive_cache:
        return _libredrive_cache[key]

    with _libredrive_lock:
        ev = _libredrive_inflight.get(key)
        first = ev is None
        if first:
            ev = _libredrive_inflight[key] = threading.Event()

    if first:
        if block:
            _libredrive_probe(key, drive, ev)
            return _libredrive_cache.get(key)
        threading.Thread(target=_libredrive_probe, args=(key, drive, ev),
                         daemon=True).start()
    elif block:
        ev.wait(timeout=150)

    # None already means "not asked / cannot tell" to every caller, so a probe still
    # running is reported the same way and the answer appears on a later poll.
    return _libredrive_cache.get(key)


def _libredrive_probe(key, drive, ev):
    """Ask MakeMKV, once per drive model, off the request path."""
    try:
        binary = shutil.which("makemkvcon") or "/usr/local/bin/makemkvcon"
        m = re.search(r"sr(\d+)$", drive.get("device") or "")
        out = _run([binary, "-r", "--cache=1", "info",
                    "disc:%s" % (m.group(1) if m else "0")], timeout=120) or ""
        _libredrive_cache[key] = DRV.parse_libredrive(out)
    except Exception:
        # A probe that blew up must not be retried on every poll for ever, but it also
        # must not poison the answer permanently -- leave the cache empty and let the
        # next caller start a fresh one.
        pass
    finally:
        ev.set()
        with _libredrive_lock:
            _libredrive_inflight.pop(key, None)


def _usb_devices():
    """Everything on the USB bus that is not a root hub.

    Read from sysfs rather than by shelling out to lsusb, which is a separate package
    and not installed by default.
    """
    devs = []
    for path in _glob("/sys/bus/usb/devices/*"):
        base = os.path.basename(path)
        if ":" in base or base.startswith("usb"):
            continue                      # interfaces and root hubs
        vid = _read(os.path.join(path, "idVendor")).strip()
        pid = _read(os.path.join(path, "idProduct")).strip()
        if not vid:
            continue
        name = " ".join(x for x in (
            _read(os.path.join(path, "manufacturer")).strip(),
            _read(os.path.join(path, "product")).strip()) if x)
        devs.append({"id": "%s:%s" % (vid, pid), "name": name or "unnamed device"})
    return devs


def usb_host_ports():
    """What the USB controllers on this board are actually configured to do.

    A controller with dr_mode=peripheral is a USB *device*: it never enumerates
    anything plugged into it, supplies no VBUS, and -- the part that costs people a
    day -- logs absolutely nothing when you plug something in. No error, no
    over-current, silence. Reading it here is what turns "no drive detected" from a
    dead end into an instruction.
    """
    host, peripheral = [], []
    for node in sorted(_glob("/proc/device-tree/soc/usb@*")):
        mode = _read(os.path.join(node, "dr_mode")).replace("\0", "").strip()
        status = _read(os.path.join(node, "status")).replace("\0", "").strip()
        if status and status != "okay":
            continue
        name = os.path.basename(node)
        (peripheral if mode == "peripheral" else host).append(name)
    return {"host": host, "peripheral": peripheral,
            "overlay_applied": "usb-otg-host" in _read_boot_env().get("user_overlays", "")}


def _read_boot_env():
    """armbianEnv.txt as a dict, or empty when this is not an Armbian board."""
    d = boot_dir()
    env = {}
    if not d:
        return env
    for line in _read(os.path.join(d, "armbianEnv.txt")).splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    return env


def optical_diagnosis():
    """Why there is no drive — not merely that there isn't one.

    "No optical drive detected" is true and unhelpful: it cannot distinguish a drive
    that is unplugged from a drive plugged into a port that physically cannot host it.
    On this board that distinction is the whole answer, because one of the two USB-C
    sockets is wired dr_mode=peripheral and will never enumerate anything.

    `fixable` says the box can do something about it itself, and the interface offers a
    button rather than a paragraph.
    """
    drives = optical_drives()
    ports = usb_host_ports()
    if drives:
        return {"drives": drives, "usb": [], "hint": None, "ports": ports,
                "fixable": None}

    usb = _usb_devices()
    fixable = None
    if usb:
        hint = ("Something is attached to USB, but nothing is presenting itself as an "
                "optical drive: %s. If that is the drive's adapter, it may need its own "
                "power, or it may be in a mode that hides the disc."
                % ", ".join("%s (%s)" % (d["name"], d["id"]) for d in usb))
    else:
        hint = ("Nothing at all is attached to the USB bus — not the drive, not "
                "anything else. The drive having power and a working tray does not mean "
                "the data connection is up: a USB-to-SATA adapter announces itself even "
                "with no drive attached, so silence here is about the cable or the "
                "socket, never about the drive.")
        if ports["peripheral"] and not ports["overlay_applied"]:
            # The single most likely cause on this board, and the one with a real fix.
            hint += (
                " **Most likely: the data cable is in the wrong USB-C socket.** This "
                "board has two that look identical, and only one of them can host a "
                "device — the other is wired as a USB peripheral and stays silent no "
                "matter what you plug in. There are only two, so swap them: put power "
                "where the data cable is and the data cable where power was. Riparr can "
                "also reconfigure the second socket so that either one works.")
            fixable = "usb-host"
        elif ports["peripheral"]:
            hint += (
                " The second USB-C socket has already been reconfigured to host, so "
                "either socket should work. That points at the cable or the adapter: a "
                "charge-only USB-C cable carries no data and looks exactly like a dead "
                "drive, and a USB-C-to-USB-C cable can read as a device — a USB-A "
                "adapter in the middle is what makes that work.")
        else:
            hint += (
                " Check the cable first: a charge-only USB-C cable carries no data and "
                "looks exactly like a dead drive.")
    return {"drives": drives, "usb": usb, "hint": hint, "ports": ports,
            "fixable": fixable}


# Restart and shut down go through the same request-file bridge the MakeMKV install
# uses: this process is unprivileged with NoNewPrivileges=yes and cannot call
# systemctl, but it can create a file that a root path unit is watching. The action is
# decided by which file is created, so there is nothing here the root side has to trust.
# ─────────────────────────────── the clock ───────────────────────────────

# The Preparer writes riparr.conf to whichever of these exists; the same directory is
# where a person with the card in a laptop can leave a file for the box to find.
BOOT_DIRS = ("/boot/firmware", "/boot")

# Nothing this box does can legitimately believe it is earlier than the day the code
# was written. An unsynchronised Pi comes up in 1970, or at whatever the last written
# timestamp on the filesystem was.
CLOCK_FLOOR = 1755000000        # 2025-08-12


def boot_dir():
    for d in BOOT_DIRS:
        if os.path.isdir(d):
            return d
    return None


def clock_status():
    """Whether the time can be trusted, on a board with no RTC.

    This matters more here than it looks. The MakeMKV key expiry is "N days left"
    computed against now; the scheduler's due-ness is computed from a stored `last_end`
    (D19); every "3 days ago" in the interface is a subtraction. A box that thinks it
    is 1970 reports all of them confidently and all of them wrong -- and D4 says
    losing power is the expected operating condition, not an edge case.
    """
    now = int(time.time())
    if MOCK:
        return {"now": now, "synced": True, "plausible": True, "source": "simulated"}

    synced = None
    if os.path.exists("/run/systemd/timesync/synchronized"):
        synced = True
    else:
        out = _run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"]) or ""
        if out.strip():
            synced = out.strip().lower() in ("yes", "true", "1")
    plausible = now >= CLOCK_FLOOR
    return {"now": now, "synced": synced, "plausible": plausible,
            "source": "ntp" if synced else "unknown"}


def trust_dates():
    """Whether anything derived from the clock is worth showing."""
    c = clock_status()
    return bool(c["plausible"] and c["synced"] is not False)


POWER_ACTIONS = {"reboot", "poweroff"}
POWER_REQUEST = "/run/riparr/%s.request"


def power_available():
    return all(os.path.exists("/etc/systemd/system/riparr-%s.path" % a)
               for a in POWER_ACTIONS) and os.access("/run/riparr", os.W_OK)


def power_action(action):
    """Ask the system to restart or shut down. Returns (ok, message)."""
    if action not in POWER_ACTIONS:
        return False, "Unknown action."
    if MOCK:
        return True, "%s (simulated)" % action
    if not power_available():
        return False, ("This copy of Riparr was installed before restart and shutdown "
                       "existed. Re-run sudo bash /opt/riparr/tools/install.sh to add "
                       "them.")
    try:
        with open(POWER_REQUEST % action, "w") as f:
            f.write("%d\n" % int(time.time()))
    except OSError as e:
        return False, "Could not ask the system to %s: %s" % (action, e)
    return True, ("Restarting" if action == "reboot" else "Shutting down")


USBHOST_REQUEST = "/run/riparr/usbhost.request"


def usb_host_available():
    return (os.path.exists("/etc/systemd/system/riparr-usbhost.path")
            and os.access("/run/riparr", os.W_OK))


def usb_host_fix():
    """Ask the root side to make both USB-C sockets host a drive. (ok, message).

    Same one-way door as `power_action`: this process cannot edit the boot
    configuration, but it can create one file that a root path unit is watching.
    """
    if MOCK:
        return True, "Reconfiguring both USB-C sockets (simulated)"
    if not usb_host_available():
        return False, ("This copy of Riparr was installed before the USB-C fix existed. "
                       "Re-run sudo bash /opt/riparr/tools/install.sh to add it.")
    if not usb_host_ports()["peripheral"]:
        return False, "Both sockets can already host a drive — nothing to change."
    try:
        with open(USBHOST_REQUEST, "w") as f:
            f.write("%d\n" % int(time.time()))
    except OSError as e:
        return False, "Could not ask the system to change the socket: %s" % e
    return True, "Reconfiguring the second USB-C socket, then restarting"


# ─────────────────── saying something with the drive itself ───────────────────
#
# The status LED on the board is the documented way the box speaks without a browser,
# and on a board with nothing wired to SPI it says nothing at all. The optical drive
# has a light on the front of it and it is always there, so it is worth using.
#
# **There is no way to address that light.** MMC -- the command set every optical drive
# speaks -- has no LED control command, and the vendor-specific ones that exist are
# per-manufacturer guesses that do not belong anywhere near a stranger's hardware.
#
# What the light actually does is report *media access*. So Riparr does not switch it
# on; it gives the drive something to do, in a rhythm. Reads are issued with O_DIRECT
# so the page cache cannot answer them -- a cached read lights nothing -- and from a
# fresh offset each time so the head has to move. The result is a real blink pattern
# out of nothing but ordinary reads, on any drive, with no vendor knowledge at all.

# A DVD sector is 2048 bytes, so every offset and length here is a multiple of it;
# O_DIRECT on a block device rejects anything else.
_SECTOR = 2048
_FLASH_CHUNK = 256 * 1024          # big enough that one read is audible head movement

# (on_seconds, off_seconds) per blink, then the gap before the group repeats. Three
# short flashes is deliberately unlike the steady flicker of a rip in progress.
DUPLICATE_PATTERN = {"blinks": 3, "on": 0.22, "off": 0.22, "gap": 0.55, "groups": 3}


def drive_flash(device="/dev/sr0", blinks=3, on=0.22, off=0.22, gap=0.55, groups=3):
    """Blink the drive's own activity light by reading the disc in a rhythm.

    Returns {"ok", "message", "sectors"} -- `sectors` is how far the block layer says
    the drive actually read, which is the only evidence available that anything
    happened. Nobody here can see the light.

    Never raises. A drive that is busy, empty, or refuses O_DIRECT simply reports that
    it could not blink; this is a courtesy signal and nothing depends on it.
    """
    if MOCK:
        return {"ok": True, "message": "Drive light flashed (simulated)", "sectors": 0}

    before = _sr_sectors_read(device)
    fd = None
    buf = None
    try:
        import mmap
        # mmap gives page-aligned memory, which satisfies O_DIRECT's alignment rule
        # without hand-rolling an aligned allocator.
        buf = mmap.mmap(-1, _FLASH_CHUNK)
        flags = os.O_RDONLY | getattr(os, "O_DIRECT", 0)
        try:
            fd = os.open(device, flags)
        except OSError:
            # Some filesystems and some drives refuse O_DIRECT outright. Without it the
            # cache may answer and the light will not move, so say so rather than
            # pretending the signal was sent.
            if not getattr(os, "O_DIRECT", 0):
                return {"ok": False, "sectors": 0,
                        "message": "This system has no O_DIRECT, so reads would come "
                                   "from cache and the light would not move."}
            fd = os.open(device, os.O_RDONLY)

        offset = 0
        for _ in range(max(1, groups)):
            for _ in range(max(1, blinks)):
                # Hold the drive busy for the whole "on" period. One read is over in
                # milliseconds; a light that flickers for 4 ms is a light nobody sees.
                until = time.monotonic() + on
                while time.monotonic() < until:
                    try:
                        os.preadv(fd, [buf], offset)
                    except OSError:
                        # Past the end of a short disc, or a bad sector. Go back to the
                        # start rather than leaving the offset stranded -- otherwise
                        # every remaining flash fails instantly and the light, having
                        # blinked once, simply stops.
                        offset = 0
                        break
                    offset += _FLASH_CHUNK
                    # Stay inside the first 512 MB. Every real disc has data there, and
                    # seeking past the end of a short disc only earns I/O errors.
                    if offset > 512 * 1024 * 1024:
                        offset = 0
                time.sleep(off)
            time.sleep(gap)
    except Exception as e:
        return {"ok": False, "message": "Could not flash the drive light: %s" % e,
                "sectors": max(0, _sr_sectors_read(device) - before)}
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if buf is not None:
            try:
                buf.close()
            except Exception:
                pass

    read = max(0, _sr_sectors_read(device) - before)
    # Zero sectors means the reads never reached the device -- cache, or a drive that
    # ignored them -- so the light did not move and saying "sent" would be a lie.
    return {"ok": read > 0,
            "sectors": read,
            "message": ("Flashed the drive light (%d sectors read)." % read) if read
                       else "The reads never reached the drive, so the light did not move."}


def _sr_sectors_read(device="/dev/sr0"):
    """Sectors the block layer has read from this drive, or 0 if it cannot say.

    Field 3 of /sys/block/<dev>/stat. This is the proof that a `drive_flash` did
    something: MakeMKV's own reads go through /dev/sg0 and never appear here, so any
    movement in this counter is ours.
    """
    name = os.path.basename(device or "")
    try:
        with open("/sys/block/%s/stat" % name) as f:
            return int(f.read().split()[2])
    except (OSError, IndexError, ValueError):
        return 0


def close_tray(device="/dev/sr0", wait=25):
    """Pull the tray in and wait for the drive to work out what is on it.

    The counterpart to `eject`, and the reason Re-rip can be a single click: the disc
    is sitting on an open tray at exactly the moment somebody decides they meant it.
    `wait=0` closes the tray and says nothing about what is in it -- which is what
    `tray_gesture` wants, since it is only moving the tray to be looked at.
    Returns (ok, message).
    """
    if MOCK:
        return True, "Tray closed (simulated)"
    try:
        p = subprocess.run(["eject", "-t", device], capture_output=True, text=True)
    except OSError as e:
        return False, "Riparr could not close the tray (%s)." % e
    if p.returncode != 0:
        return False, ((p.stderr or p.stdout).strip()
                       or "The drive would not pull the tray in.")
    if not wait:
        return True, "Tray closed."
    # Closing returns long before the disc is readable -- a DVD takes 15-25 s to spin
    # up and report its table of contents, and asking too early gets "no medium".
    deadline = time.time() + wait
    while time.time() < deadline:
        time.sleep(2)
        d = next((x for x in optical_drives() if x.get("present")), None)
        if d:
            return True, "Tray closed."
    return False, ("The tray closed but the drive found no disc in it. "
                   "Put the disc in and try again.")


def tray_gesture(device="/dev/sr0", cycles=2):
    """Say something by opening and closing the tray -- the loudest thing this box owns.

    Unmissable across a room, and it is machinery, so it is deliberately restrained:
    a couple of cycles, not a drum solo. The tray is left **open** at the end, because
    whatever the box was trying to say, the user's disc is theirs to take back.
    """
    if MOCK:
        return {"ok": True, "message": "Tray gesture (simulated)"}
    ok = True
    for i in range(max(1, cycles)):
        r = eject(device)
        ok = ok and r.get("ok")
        time.sleep(1.2)
        if i < cycles - 1:
            done, _ = close_tray(device, wait=0)
            ok = ok and done
            time.sleep(1.2)
    return {"ok": ok, "message": "Opened the tray %d times." % max(1, cycles)}


def duplicate_signal(device="/dev/sr0", mode="flash"):
    """Tell somebody with no screen that this disc is already in their library.

    Ordering matters: the light can only be blinked while the disc is still in the
    drive, so that happens first and the eject follows. Returns a message describing
    what was actually done, which is not always what was asked -- a flash that never
    reached the drive falls back to the tray rather than silently doing nothing.
    """
    if mode == "off":
        return {"ok": True, "message": "No physical signal (turned off)."}
    out = []
    flashed = False
    if mode in ("flash", "both"):
        r = drive_flash(device, **DUPLICATE_PATTERN)
        flashed = r.get("ok")
        out.append(r.get("message"))
    if mode == "tray" or mode == "both" or (mode == "flash" and not flashed):
        if mode == "flash":
            out.append("Falling back to the tray.")
        out.append(tray_gesture(device).get("message"))
    return {"ok": True, "message": " ".join(x for x in out if x)}


def eject(device="/dev/sr0"):
    """Give the user their disc back. There is no physical button on the enclosure."""
    if MOCK:
        return {"ok": True, "message": "Tray ejected (simulated)"}
    try:
        p = subprocess.run(["eject", device], capture_output=True, text=True)
    except OSError as e:
        # Giving the disc back is a courtesy; it is never the point of the job. This
        # used to raise straight through the failure handler that called it, so a box
        # without /usr/bin/eject reported "No such file or directory: 'eject'" as the
        # reason a rip failed -- masking the real error entirely and costing a session.
        return {"ok": False, "message": "Riparr could not open the tray (%s)." % e}
    return {"ok": p.returncode == 0,
            "message": (p.stderr or p.stdout).strip() or "Tray ejected"}


# ─────────────────────────────── wi-fi ───────────────────────────────

def _wifi_iface():
    for path in _glob("/sys/class/net/*/wireless"):
        return path.split("/")[4]
    return None


def _dbm_to_quality(dbm):
    """dBm to a 0-100 bar, the way most wireless UIs do it.

    -50 and better is full, -100 is nothing, linear between. This is a presentation
    number and a lossy one, which is exactly why the dBm is reported alongside it
    rather than instead of it.
    """
    if dbm is None:
        return None
    return max(0, min(100, int(round(2 * (dbm + 100)))))


def wifi_status():
    """What the box is actually associated with.

    This used to ask `nmcli`, which does not exist here: the image runs
    systemd-networkd with wpa_supplicant and ships no NetworkManager at all. Every
    lookup therefore returned nothing, and the interface reported a perfectly healthy
    5 GHz link as "not connected" with no SSID. `iw` is present, and says more than
    nmcli would have — the real signal in dBm, the frequency, and the negotiated rate.
    """
    if MOCK:
        return {"connected": True, "ssid": "HomeNetwork", "signal": 78,
                "signal_dbm": -52, "band": "2.4", "freq_mhz": 2437,
                "bitrate_mbps": 72.2, "ip": "192.168.1.84",
                "iface": "wlan0", "mode": "client"}

    iface = _wifi_iface() or "wlan0"
    base = {"connected": False, "ssid": None, "signal": None, "signal_dbm": None,
            "band": None, "freq_mhz": None, "bitrate_mbps": None,
            "ip": _ip(), "iface": iface, "mode": "client"}

    out = _run(["iw", "dev", iface, "link"], timeout=5) or ""
    if "Connected to" not in out:
        return base

    def grab(pattern, cast=str):
        m = re.search(pattern, out)
        if not m:
            return None
        try:
            return cast(m.group(1))
        except (TypeError, ValueError):
            return None

    dbm = grab(r"signal:\s*(-?\d+)\s*dBm", int)
    freq = grab(r"freq:\s*(\d+)", int)
    base.update({
        "connected": True,
        "ssid": grab(r"SSID:\s*(.+)", lambda v: v.strip()),
        "signal_dbm": dbm,
        "signal": _dbm_to_quality(dbm),
        "freq_mhz": freq,
        # The band is worth surfacing: 5 GHz is several times the throughput the
        # design was originally sized against, and it is the single number that most
        # changes how long a rip takes to land on the share.
        "band": None if not freq else ("6" if freq >= 5925 else
                                       "5" if freq >= 4900 else "2.4"),
        "bitrate_mbps": grab(r"tx bitrate:\s*([\d.]+)", float),
    })
    return base


def _band_of(freq):
    """The band a frequency belongs to, or None. Matches wifi_status()'s derivation."""
    if not freq:
        return None
    return "6" if freq >= 5925 else "5" if freq >= 4900 else "2.4"


def wifi_scan():
    """Every network the radio can see, whatever band it is on.

    This used to drop anything at 5 GHz on the belief that the board was a Raspberry Pi
    Zero 2 W, whose radio is 2.4 GHz only. But the reference board is an Orange Pi Zero 2W
    (dual-band Wi-Fi 5), and the other supported boards go up to Wi-Fi 6 — so filtering by
    frequency hid networks the hardware can actually join, and 5 GHz is the band that most
    changes how fast a rip lands on the share. The right filter is the radio itself: a
    2.4-only board simply never returns a 5 GHz result, so no hardcoded assumption is
    needed or correct across boards.
    """
    if MOCK:
        return [
            {"ssid": "HomeNetwork", "signal": 82, "secure": True, "band": "2.4"},
            {"ssid": "HomeNetwork 5G", "signal": 74, "secure": True, "band": "5"},
            {"ssid": "Masons", "signal": 54, "secure": True, "band": "2.4"},
            {"ssid": "ROG 2G", "signal": 38, "secure": True, "band": "2.4"},
            {"ssid": "xr500", "signal": 21, "secure": True, "band": "5"},
        ]
    out = _run(["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY,FREQ", "dev", "wifi", "list"]) or ""
    nets = {}
    for line in out.splitlines():
        f = line.split(":")
        if len(f) < 4 or not f[0]:
            continue
        try:
            freq = int(re.sub(r"\D", "", f[3]) or 0)
        except ValueError:
            continue
        sig = int(f[1]) if f[1].isdigit() else 0
        # Keep the strongest sighting of an SSID, but never let a weak sighting on one
        # band erase the band of the strong one it is replacing.
        if f[0] not in nets or sig > nets[f[0]]["signal"]:
            nets[f[0]] = {"ssid": f[0], "signal": sig,
                          "secure": bool(f[2]), "band": _band_of(freq)}
    return sorted(nets.values(), key=lambda n: -n["signal"])


def wifi_connect(ssid, password):
    if MOCK:
        return {"ok": True, "message": "Connected (simulated)"}
    cmd = ["nmcli", "dev", "wifi", "connect", ssid]
    if password:
        cmd += ["password", password]
    p = subprocess.run(cmd, capture_output=True, text=True)
    return {"ok": p.returncode == 0,
            "message": (p.stdout or p.stderr).strip()}


# ─────────────────────────────── makemkv ───────────────────────────────

def makemkv_status():
    """The one setting that needs periodic attention, so it is reported first-class."""
    if MOCK:
        # RIPARR_MOCK_MAKEMKV lets the first-run and expiry paths be exercised off-Pi:
        #   missing  — not installed, so the licence + install flow shows
        #   expiring — installed, key nearly dead, so the warning paths show
        mode = os.environ.get("RIPARR_MOCK_MAKEMKV", "ready")
        if mode == "missing":
            return {"installed": False, "version": None, "eula_accepted": False,
                    "key_type": None, "key_expires": None, "days_left": None}
        if mode == "expiring":
            return {"installed": True, "version": "1.18.4", "eula_accepted": True,
                    "key_type": "beta", "key_expires": "2026-08-23", "days_left": 4}
        return {"installed": True, "version": "1.18.4", "eula_accepted": True,
                "key_type": "beta", "key_expires": "2026-10-14", "days_left": 56}
    binary = shutil.which("makemkvcon") or "/usr/local/bin/makemkvcon"
    installed = os.path.exists(binary)
    ver = _makemkv_version(binary) if installed else None
    return {"installed": installed, "version": ver,
            "eula_accepted": _makemkv_eula_accepted(),
            "key_type": None, "key_expires": None, "days_left": None}


_version_cache = {}


VERSION_FILE = "/usr/local/lib/riparr/makemkv.version"
EULA_FILE = "/usr/local/lib/riparr/makemkv.eula"


def _makemkv_eula_accepted():
    """Whether the installed MakeMKV was installed under an accepted licence.

    This used to look for ~/.MakeMKV/eula_accepted, which MakeMKV never creates — the
    bin package's Makefile gate is a differently named file inside the build directory,
    and it is gone once the build is. So this was always False, and the interface went
    on asking for consent that had already been given and recorded.

    Our installer cannot run without --accept-eula, so it writes EULA_FILE at the point
    the user's consent was acted on. The legacy path is still honoured for a MakeMKV
    that arrived some other way.
    """
    return (os.path.exists(EULA_FILE)
            or os.path.exists(os.path.expanduser("~/.MakeMKV/eula_accepted")))


def _makemkv_version(binary):
    """The version, without running makemkvcon at all.

    There is no cheap way to ask the binary. It has no --version and no --help — both
    print usage and exit non-zero. `-r info disc:99` does print the banner, but it
    enumerates every optical device first and blocks for 20+ seconds doing it, which is
    impossible for an endpoint the status page polls and would collide with a rip. The
    version strings inside the executable belong to bundled libraries, not to MakeMKV.

    So the installer records it at install time, when it is already known from the
    tarball name. A MakeMKV installed some other way has no such file; report the
    version as unknown rather than paying 20 seconds to find out.
    """
    try:
        key = (binary, os.path.getmtime(binary))
    except OSError:
        return None
    if key in _version_cache:
        return _version_cache[key]
    ver = None
    try:
        with open(VERSION_FILE) as f:
            m = re.match(r"\s*v?([\d.]+)", f.read())
            ver = m.group(1) if m else None
    except OSError:
        pass
    _version_cache.clear()
    _version_cache[key] = ver
    return ver


# ─────────────────────────────── helpers ───────────────────────────────

def _read(path):
    try:
        return open(path, errors="ignore").read()
    except OSError:
        return ""


def _run(cmd, timeout=10):
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout).stdout
    except Exception:
        return None


def _glob(pat):
    import glob
    return glob.glob(pat)


def _osname():
    for line in _read("/etc/os-release").splitlines():
        if line.startswith("PRETTY_NAME="):
            return line.split("=", 1)[1].strip('"')
    return "Linux"


def _ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None
