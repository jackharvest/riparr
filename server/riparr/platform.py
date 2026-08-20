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

IS_APPLIANCE = (
    _p.system() == "Linux"
    and os.path.exists("/proc/device-tree/model")
    and "Raspberry Pi" in open("/proc/device-tree/model", errors="ignore").read()
)
MOCK = not IS_APPLIANCE


def hostname():
    return socket.gethostname().split(".")[0]


# ─────────────────────────────── system ───────────────────────────────

def system_status():
    if MOCK:
        return {
            "model": "Raspberry Pi Zero 2 W (simulated)",
            "os": "Raspberry Pi OS Lite 64-bit (trixie)",
            "kernel": "6.12.0-rpi-arm64",
            "uptime_seconds": 48213,
            "memory_total_mb": 512,
            "memory_used_mb": 214,
            "cpu_temp_c": 46.8,
            "throttled": False,
            "mock": True,
        }
    model = _read("/proc/device-tree/model").strip("\x00").strip()
    mem = {}
    for line in _read("/proc/meminfo").splitlines():
        k, _, v = line.partition(":")
        mem[k] = int(v.strip().split()[0]) if v.strip().split() else 0
    total = mem.get("MemTotal", 0) // 1024
    avail = mem.get("MemAvailable", 0) // 1024
    temp = _run(["vcgencmd", "measure_temp"])
    m = re.search(r"([\d.]+)", temp or "")
    thr = _run(["vcgencmd", "get_throttled"]) or ""
    return {
        "model": model,
        "os": _osname(),
        "kernel": _p.release(),
        "uptime_seconds": int(float(_read("/proc/uptime").split()[0] or 0)),
        "memory_total_mb": total,
        "memory_used_mb": total - avail,
        "cpu_temp_c": float(m.group(1)) if m else None,
        "throttled": ("0x0" not in thr) if thr else False,
        "mock": False,
    }


def storage_status():
    """Staging capacity, expressed so the UI can talk in discs rather than gigabytes."""
    if MOCK:
        total, free = 22.8 * 2**30, 16.1 * 2**30
    else:
        st = os.statvfs(STAGING)
        total = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
    return {"total_bytes": int(total), "free_bytes": int(free),
            "used_bytes": int(total - free)}


STAGING = "/srv/staging" if IS_APPLIANCE else "/tmp/riparr-staging"


# ─────────────────────────────── optical ───────────────────────────────

def optical_drives():
    if MOCK:
        return [{"device": "/dev/sr0", "vendor": "Pioneer", "model": "BDR-XD08U",
                 "media": "BD-ROM", "label": "THE_MATRIX", "present": True}]
    out = []
    for dev in sorted(_glob("/dev/sr*")):
        out.append({"device": dev, "vendor": "", "model": "",
                    "media": None, "label": None, "present": False})
    return out


def eject(device="/dev/sr0"):
    """Give the user their disc back. There is no physical button on the enclosure."""
    if MOCK:
        return {"ok": True, "message": "Tray ejected (simulated)"}
    p = subprocess.run(["eject", device], capture_output=True, text=True)
    return {"ok": p.returncode == 0,
            "message": (p.stderr or p.stdout).strip() or "Tray ejected"}


# ─────────────────────────────── wi-fi ───────────────────────────────

def wifi_status():
    if MOCK:
        return {"connected": True, "ssid": "HomeNetwork", "signal": 78,
                "band": "2.4", "ip": "192.168.1.84", "mode": "client"}
    out = _run(["nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL", "dev", "wifi"]) or ""
    for line in out.splitlines():
        f = line.split(":")
        if f and f[0] == "yes":
            return {"connected": True, "ssid": f[1],
                    "signal": int(f[2]) if len(f) > 2 and f[2].isdigit() else None,
                    "band": "2.4", "ip": _ip(), "mode": "client"}
    return {"connected": False, "ssid": None, "signal": None,
            "band": None, "ip": _ip(), "mode": "client"}


def wifi_scan():
    """Only 2.4 GHz results are ever returned — the radio cannot see anything else."""
    if MOCK:
        return [
            {"ssid": "HomeNetwork", "signal": 82, "secure": True, "band": "2.4"},
            {"ssid": "Masons", "signal": 54, "secure": True, "band": "2.4"},
            {"ssid": "ROG 2G", "signal": 38, "secure": True, "band": "2.4"},
            {"ssid": "xr500", "signal": 21, "secure": True, "band": "2.4"},
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
        if freq >= 5000:          # the Zero 2W has no radio for this
            continue
        sig = int(f[1]) if f[1].isdigit() else 0
        if f[0] not in nets or sig > nets[f[0]]["signal"]:
            nets[f[0]] = {"ssid": f[0], "signal": sig,
                          "secure": bool(f[2]), "band": "2.4"}
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
        return {"installed": True, "version": "1.18.4", "eula_accepted": True,
                "key_type": "beta", "key_expires": "2026-10-14", "days_left": 56}
    binary = shutil.which("makemkvcon") or "/usr/local/bin/makemkvcon"
    installed = os.path.exists(binary)
    ver = None
    if installed:
        out = _run([binary, "-r", "info"]) or ""
        m = re.search(r"MakeMKV v([\d.]+)", out)
        ver = m.group(1) if m else None
    return {"installed": installed, "version": ver,
            "eula_accepted": os.path.exists(
                os.path.expanduser("~/.MakeMKV/eula_accepted")),
            "key_type": None, "key_expires": None, "days_left": None}


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
