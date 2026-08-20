"""
Riparr preparer — headless core.

Every security-critical primitive lives here exactly once: Wi-Fi PSK derivation,
SHA-512 crypt, the custom.toml schema, and the removable-disk guards. Both the GUI
(app.py) and the scripted TUI (../flasher/riparr-flash.py) import from this module so
the two can never drift apart on the parts that are dangerous to get wrong.

Stdlib only, except passlib. No AppKit here — this module must stay importable and
testable without a window server.
"""
import hashlib
import json
import os
import plistlib
import re
import secrets
import string
import subprocess
import urllib.error
import urllib.request

# The official repository. The updater and the image manifest both hang off this.
RIPARR_REPO = "jackharvest/riparr"
GITHUB_API = "https://api.github.com"

# A Pi Zero 2W's radio is 2.4 GHz only. This is not a preference, it is silicon.
PI_BANDS = {"2.4"}

# Removable-media guard rails. A card outside this range is not an SD card we wrote.
MIN_DISK_BYTES = 4_000_000_000
MAX_DISK_BYTES = 70_000_000_000


# ───────────────────────────────── Wi-Fi ─────────────────────────────────

def _band_for_channel(ch_band):
    return {1: "2.4", 2: "5", 3: "6"}.get(ch_band)


def scan_corewlan():
    """Live scan via CoreWLAN: SSID, band, RSSI, security.

    This is the only scan method that returns unredacted SSIDs without a Location
    Services grant. `airport` was removed in macOS 26 and `system_profiler` redacts.
    """
    import CoreWLAN
    iface = CoreWLAN.CWWiFiClient.sharedWiFiClient().interface()
    if iface is None:
        return {}
    nets, _ = iface.scanForNetworksWithSSID_error_(None, None)
    agg = {}
    for n in nets or []:
        ssid = n.ssid()
        if not ssid:
            continue
        ch = n.wlanChannel()
        band = _band_for_channel(ch.channelBand() if ch else 0)
        try:
            secure = not n.supportsSecurity_(0)  # kCWSecurityNone
        except Exception:
            secure = True
        rssi = n.rssiValue()
        e = agg.get(ssid)
        if e is None:
            agg[ssid] = {
                "ssid": ssid,
                "bands": [band] if band else [],
                "rssi": rssi,
                "secure": secure,
                "saved": False,
                "seen": True,
            }
        else:
            if band and band not in e["bands"]:
                e["bands"].append(band)
            if rssi is not None and (e["rssi"] is None or rssi > e["rssi"]):
                e["rssi"] = rssi
    return agg


def scan_system_profiler():
    """Fallback scan. SSIDs come back '<redacted>' without Location Services."""
    try:
        out = subprocess.run(
            ["system_profiler", "SPAirPortDataType"],
            capture_output=True, text=True, timeout=45).stdout
    except Exception:
        return {}
    agg, cur = {}, None
    for line in out.splitlines():
        m = re.match(r"^\s{12}([^\s:][^:]*):\s*$", line)
        if m:
            cur = m.group(1).strip()
            if cur.startswith("<"):
                cur = None
            elif cur not in agg:
                agg[cur] = {"ssid": cur, "bands": [], "rssi": None,
                            "secure": True, "saved": False, "seen": True}
            continue
        if cur and "Channel:" in line:
            b = re.search(r"\((2|5|6)GHz", line)
            if b:
                band = {"2": "2.4", "5": "5", "6": "6"}[b.group(1)]
                if band not in agg[cur]["bands"]:
                    agg[cur]["bands"].append(band)
        if cur and "Security:" in line and "None" in line:
            agg[cur]["secure"] = False
    return agg


def saved_networks():
    """Preferred networks, unredacted, but with no band or signal information."""
    try:
        out = subprocess.run(
            ["networksetup", "-listpreferredwirelessnetworks", "en0"],
            capture_output=True, text=True, timeout=15).stdout
        return [l.strip() for l in out.splitlines()[1:] if l.strip()]
    except Exception:
        return []


def pi_can_join(net):
    """Unknown band is allowed through and warned about; 5/6 GHz only is not."""
    return (not net["bands"]) or ("2.4" in net["bands"])


def scan_networks():
    """Returns (networks, method). Degrades gracefully; never raises."""
    agg, method = {}, None
    try:
        agg = scan_corewlan()
        if agg:
            method = "live"
    except Exception:
        agg = {}
    if not agg:
        agg = scan_system_profiler()
        if agg:
            method = "system_profiler"
    for s in saved_networks():
        if s in agg:
            agg[s]["saved"] = True
        else:
            agg[s] = {"ssid": s, "bands": [], "rssi": None, "secure": True,
                      "saved": True, "seen": False}
    if agg and not method:
        method = "saved"
    nets = list(agg.values())
    for n in nets:
        n["pi_ok"] = pi_can_join(n)
    nets.sort(key=lambda n: (not n["pi_ok"], n["rssi"] is None,
                             -(n["rssi"] or -100), n["ssid"].lower()))
    return nets, (method or "none")


def derive_psk(ssid, passphrase):
    """WPA-PSK, exactly as wpa_passphrase(8) derives it.

    Doing this here means the plaintext Wi-Fi passphrase never reaches the FAT32
    partition, which is readable by anyone who picks up the card.
    """
    return hashlib.pbkdf2_hmac(
        "sha1", passphrase.encode(), ssid.encode(), 4096, 32).hex()


# ───────────────────────────────── Disks ─────────────────────────────────

def list_disks():
    """External, removable, physical media only. The boot drive can never appear."""
    try:
        out = subprocess.run(
            ["diskutil", "list", "-plist", "external", "physical"],
            capture_output=True).stdout
        disks = plistlib.loads(out).get("AllDisksAndPartitions", [])
    except Exception:
        return []
    res = []
    for d in disks:
        ident = d.get("DeviceIdentifier")
        if not ident:
            continue
        try:
            info = plistlib.loads(subprocess.run(
                ["diskutil", "info", "-plist", ident], capture_output=True).stdout)
        except Exception:
            continue
        if info.get("VirtualOrPhysical") == "Virtual":
            continue
        if not info.get("Ejectable", False) and not info.get("RemovableMedia", False):
            continue
        if info.get("Internal", False):
            continue
        size = info.get("TotalSize", 0)
        if not (MIN_DISK_BYTES < size < MAX_DISK_BYTES):
            continue
        res.append({
            "id": ident,
            "size": size,
            "size_gb": round(size / 1e9, 1),
            "name": (info.get("MediaName") or "?").strip(),
            "protocol": info.get("BusProtocol", ""),
        })
    return res


def validate_disk(ident):
    """Re-check a disk immediately before writing. Never trust a cached list."""
    for d in list_disks():
        if d["id"] == ident:
            return d
    return None


# ──────────────────────────── Provisioning ────────────────────────────

def sha512_crypt(pw):
    """macOS crypt(3) has no $6$ support and silently returns a DES hash instead.

    crypt.crypt('test', '$6$...') yields '$6asQOJRqB1i2' — a DES hash wearing a
    prefix. That produces a card whose console login cannot work, discoverable only
    after flashing and booting. passlib is a hard requirement for this reason.
    """
    from passlib.hash import sha512_crypt as s
    return s.using(rounds=5000).hash(pw)


def _esc(s):
    return s.replace("\\", "\\\\").replace('"', '\\"')


def build_toml(cfg):
    """Generate custom.toml.

    Schema verified against raspberrypi-sys-mods/init_config, not from memory.
    `password_encrypted` defaults to TRUE for both [user] and [wlan], so a plaintext
    value supplied without explicitly setting it false fails in a way that is close to
    undiagnosable on a headless box.
    """
    lines = [
        "# Generated by the Riparr Preparer",
        "# Schema verified against raspberrypi-sys-mods/init_config",
        "config_version = 1",
        "",
        "[system]",
        'hostname = "%s"' % _esc(cfg["hostname"]),
        "",
        "[user]",
        'name = "%s"' % _esc(cfg["user"]),
        'password = "%s"' % cfg["pw_hash"],
        "password_encrypted = true",
        "",
        "[ssh]",
        "enabled = true",
        "password_authentication = true",
    ]
    if cfg.get("authorized_key"):
        lines.append('authorized_keys = [ "%s" ]' % _esc(cfg["authorized_key"]))
    lines += ["", "[wlan]", 'ssid = "%s"' % _esc(cfg["ssid"])]
    if cfg.get("secure"):
        lines += ['password = "%s"' % derive_psk(cfg["ssid"], cfg["wifi_pw"]),
                  "password_encrypted = true"]
    lines += ["hidden = %s" % ("true" if cfg.get("hidden") else "false"),
              'country = "%s"' % _esc(cfg.get("country", "US")),
              "",
              "[locale]",
              'keymap = "%s"' % _esc(cfg.get("keymap", "us")),
              'timezone = "%s"' % _esc(cfg.get("timezone", "UTC")),
              ""]
    return "\n".join(lines)


def host_timezone():
    if os.path.islink("/etc/localtime"):
        return os.readlink("/etc/localtime").split("zoneinfo/")[-1]
    return "UTC"


# ────────────────────────────── Assets ──────────────────────────────

def find_images(assets):
    """Every .img.xz in the assets dir, newest first."""
    try:
        names = [f for f in os.listdir(assets) if f.endswith(".img.xz")]
    except OSError:
        return []
    out = []
    for n in names:
        p = os.path.join(assets, n)
        out.append({"name": n, "path": p,
                    "size": os.path.getsize(p),
                    "mtime": os.path.getmtime(p)})
    out.sort(key=lambda d: d["mtime"], reverse=True)
    return out


def uncompressed_size(path):
    """Read the real expanded size out of the xz index, for an honest progress bar."""
    try:
        out = subprocess.run(["xz", "--robot", "--list", path],
                             capture_output=True, text=True).stdout
        for line in out.splitlines():
            f = line.split("\t")
            if f[0] == "file":
                return int(f[4])
    except Exception:
        pass
    return 0


def ensure_password(assets):
    """Read the saved account password, generating one on first run."""
    pwfile = os.path.join(assets, "user_password.txt")
    if os.path.exists(pwfile):
        return open(pwfile).read().strip(), False
    pw = "".join(secrets.choice(string.ascii_letters + string.digits)
                 for _ in range(20))
    with open(pwfile, "w") as f:
        f.write(pw + "\n")
    os.chmod(pwfile, 0o600)
    return pw, True


def public_key(assets):
    p = os.path.join(assets, "riparr_key.pub")
    return open(p).read().strip() if os.path.exists(p) else None


# ────────────────────────────── Updates ──────────────────────────────

def check_for_update(current_version, repo=RIPARR_REPO, timeout=6):
    """Ask GitHub for the newest release.

    Returns a dict the UI can render directly. Never raises — a machine with no
    internet must still be able to flash a card.
    """
    url = "%s/repos/%s/releases/latest" % (GITHUB_API, repo)
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "riparr-preparer/%s" % current_version,
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"status": "no-releases", "repo": repo}
        return {"status": "error", "detail": "HTTP %s" % e.code}
    except Exception as e:
        return {"status": "offline", "detail": str(e)}

    tag = (data.get("tag_name") or "").lstrip("v")
    assets = [{"name": a.get("name"), "url": a.get("browser_download_url"),
               "size": a.get("size")} for a in data.get("assets", [])]
    return {
        "status": "update" if _newer(tag, current_version) else "current",
        "version": tag,
        "current": current_version,
        "url": data.get("html_url"),
        "notes": data.get("body") or "",
        "published": data.get("published_at"),
        "assets": assets,
        "repo": repo,
    }


def _newer(a, b):
    """Compare dotted versions. Non-numeric segments sort as 0 rather than crashing."""
    def parts(v):
        return [int(x) if x.isdigit() else 0
                for x in re.split(r"[.\-+]", v or "") if x != ""]
    pa, pb = parts(a), parts(b)
    n = max(len(pa), len(pb))
    pa += [0] * (n - len(pa))
    pb += [0] * (n - len(pb))
    return pa > pb
