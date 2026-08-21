"""Windows.

As with the Linux backend, every `parse_*` function takes text and returns data, so the
awkward half can be exercised anywhere. These were tested against captured output, not
on Windows.

Disks come from CIM through PowerShell as JSON, which is structured and not localised.
Wi-Fi comes from `netsh`, which is **localised** -- and that is the trap this module is
mostly written around. On a Windows installed in German or Japanese, "Signal", "Channel"
and "Authentication" are all translated. So nothing here keys on a translated word:

  * `SSID n :` and `BSSID n :` are acronyms and are not translated in any locale
  * `WPA2-Personal`, `WPA3-SAE`, `Open` are standard names carried through untranslated
  * a percentage and a channel number are digits, and digits do not localise

The cost is that a network whose security line is genuinely missing is assumed secure,
which is the safe direction: it asks for a password that is not needed rather than
silently trying to join a protected network without one.
"""
import json
import re
import subprocess

NAME = "Windows"

# Keeps a console window from flashing up behind the app on every call.
_NOWINDOW = 0x08000000


def _run(cmd, timeout=25):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           creationflags=_NOWINDOW)
        return p.stdout or ""
    except Exception:
        return ""


def _powershell(script, timeout=30):
    return _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                timeout=timeout)


# ───────────────────────────────── Disks ─────────────────────────────────

# MSFT_Disk gives BusType and IsSystem/IsBoot; Win32_DiskDrive gives MediaType, which is
# where the removable-medium bit surfaces on this platform. Neither has both, so join
# them on the disk number. ConvertTo-Json collapses a single result to an object rather
# than an array, hence the forced @().
_DISK_PS = r"""
$d = Get-CimInstance -Namespace root/Microsoft/Windows/Storage -ClassName MSFT_Disk |
     Select-Object Number,Size,FriendlyName,BusType,IsSystem,IsBoot,IsOffline
$w = Get-CimInstance -ClassName Win32_DiskDrive |
     Select-Object Index,Model,MediaType,InterfaceType
$out = foreach ($x in $d) {
  $m = $w | Where-Object { $_.Index -eq $x.Number } | Select-Object -First 1
  [pscustomobject]@{
    Number=$x.Number; Size=$x.Size; FriendlyName=$x.FriendlyName; BusType=$x.BusType;
    IsSystem=$x.IsSystem; IsBoot=$x.IsBoot;
    Model=$m.Model; MediaType=$m.MediaType; InterfaceType=$m.InterfaceType
  }
}
@($out) | ConvertTo-Json -Compress
"""

# MSFT_Disk.BusType is an integer in some Windows builds and a string in others.
_BUS_TYPES = {
    1: "SCSI", 2: "ATAPI", 3: "ATA", 4: "1394", 5: "SSA", 6: "Fibre Channel",
    7: "USB", 8: "RAID", 9: "iSCSI", 10: "SAS", 11: "SATA", 12: "SD", 13: "MMC",
    15: "File Backed Virtual", 16: "Storage Spaces", 17: "NVMe",
}


def parse_disks(text):
    """The joined CIM JSON → the normalised shape core.classify_disk consumes."""
    try:
        blob = json.loads(text)
    except Exception:
        return []
    if isinstance(blob, dict):
        blob = [blob]
    out = []
    for d in blob or []:
        num = d.get("Number")
        if num is None:
            continue
        bus = d.get("BusType")
        bus = _BUS_TYPES.get(bus, str(bus)) if isinstance(bus, int) else (bus or "")
        media = (d.get("MediaType") or "")
        name = (d.get("FriendlyName") or d.get("Model") or "?").strip() or "?"
        out.append({
            # The form the raw-write path needs; there is no /dev here.
            "id": r"\\.\PHYSICALDRIVE%d" % int(num),
            "number": int(num),
            "size": int(d.get("Size") or 0),
            "name": name,
            "model": (d.get("Model") or "").strip(),
            "protocol": bus,
            # "Removable Media" is Win32_DiskDrive's spelling of the same removable-
            # medium bit macOS calls RemovableMedia and Linux exposes as /sys rm.
            "removable_media": "removable" in media.lower(),
            # USB and 1394 devices can be unplugged; that is all `ejectable` claims.
            "ejectable": bus in ("USB", "1394", "SD", "MMC"),
            "internal": bool(d.get("IsSystem")) or bool(d.get("IsBoot")),
            "virtual": bus in ("File Backed Virtual", "Storage Spaces"),
            "icon": "",
        })
    return out


def list_block_devices():
    return parse_disks(_powershell(_DISK_PS))


# ───────────────────────────────── Wi-Fi ─────────────────────────────────

_SSID_RE = re.compile(r"^\s*SSID\s+\d+\s*:\s*(.*)$")
_BSSID_RE = re.compile(r"^\s*BSSID\s+\d+\s*:", re.I)
_PCT_RE = re.compile(r"(\d{1,3})\s*%")
_SEC_RE = re.compile(r"\b(WPA3[\w-]*|WPA2[\w-]*|WPA[\w-]*|WEP|RSNA)\b", re.I)
_OPEN_RE = re.compile(r"\bOpen\b", re.I)
_CHAN_RE = re.compile(r":\s*(\d{1,3})\s*$")


def _band_for_channel(ch):
    if 1 <= ch <= 14:
        return "2.4"
    if 32 <= ch <= 177:
        return "5"
    return None


def parse_netsh_networks(text):
    """`netsh wlan show networks mode=bssid` → aggregate by SSID.

    Keyed on structure and on untranslated tokens only -- see the module docstring.
    """
    agg, cur = {}, None
    for line in text.splitlines():
        m = _SSID_RE.match(line)
        if m:
            ssid = m.group(1).strip()
            cur = ssid or None                 # a blank SSID is a hidden network
            if cur and cur not in agg:
                agg[cur] = {"ssid": cur, "bands": [], "rssi": None,
                            "secure": True, "saved": False, "seen": True}
            continue
        if not cur:
            continue
        e = agg[cur]
        if _SEC_RE.search(line):
            e["secure"] = True
        elif _OPEN_RE.search(line) and ":" in line:
            e["secure"] = False
        p = _PCT_RE.search(line)
        if p:
            try:
                # netsh reports 0-100 quality. Same conversion as the Linux backend, so
                # the bars mean the same thing on both.
                rssi = int(p.group(1)) / 2.0 - 100
                if e["rssi"] is None or rssi > e["rssi"]:
                    e["rssi"] = rssi
            except Exception:
                pass
        elif not _BSSID_RE.match(line):
            c = _CHAN_RE.search(line)
            if c:
                band = _band_for_channel(int(c.group(1)))
                if band and band not in e["bands"]:
                    e["bands"].append(band)
    return agg


def parse_netsh_profiles(text):
    """`netsh wlan show profiles` → saved profile names.

    The label before the colon is localised; the name after it is not. Taking
    everything after the last colon on lines that have one is locale-proof, and the
    header lines it also matches are filtered by requiring a non-empty remainder that
    is not itself a heading.
    """
    names = []
    for line in text.splitlines():
        if ":" not in line:
            continue
        left, _, right = line.partition(":")
        name = right.strip()
        # Profile lines are indented under a heading; headings are not.
        if name and line[:1].isspace() and "---" not in name:
            names.append(name)
    return names


def parse_netsh_key(text):
    """`netsh wlan show profile name="X" key=clear` → the passphrase.

    "Key Content" is localised, so it is found by shape: the only line in that output
    whose value is a bare 8-63 character passphrase sitting under the security block.
    Falls back to nothing rather than guessing wrong.
    """
    best = None
    for line in text.splitlines():
        if ":" not in line:
            continue
        left, _, right = line.partition(":")
        v = right.strip()
        if not v or not line[:1].isspace():
            continue
        # Key Content is the only field that is free-form text of passphrase length and
        # is neither a standard name nor a number.
        if 8 <= len(v) <= 63 and not _SEC_RE.fullmatch(v) and not v.isdigit():
            low = left.strip().lower()
            if "key" in low or "clave" in low or "schl" in low or "cl\u00e9" in low:
                return v
            if best is None:
                best = v
    return best or ""


def scan_wifi():
    agg = parse_netsh_networks(_run(["netsh", "wlan", "show", "networks", "mode=bssid"]))
    method = "live" if agg else None
    for name in parse_netsh_profiles(_run(["netsh", "wlan", "show", "profiles"])):
        if name in agg:
            agg[name]["saved"] = True
        else:
            agg[name] = {"ssid": name, "bands": [], "rssi": None, "secure": True,
                         "saved": True, "seen": False}
    if agg and not method:
        method = "saved"
    return list(agg.values()), (method or "none")


def saved_network_password(ssid):
    """Windows will hand over a saved key, but only to an administrator.

    Unlike the macOS keychain there is no consent dialog: it either works because the
    Preparer is already elevated, or it fails. The caller treats an empty answer as
    "type it yourself", which is the correct outcome either way.
    """
    if not ssid:
        return "", "no network chosen"
    out = _run(["netsh", "wlan", "show", "profile", "name=%s" % ssid, "key=clear"],
               timeout=45)
    key = parse_netsh_key(out)
    if key:
        return key, ""
    return "", ("Windows didn't return a saved password for that network. "
                "Reading one needs administrator rights.")


# ─────────────────── Desktop integration ───────────────────

def open_url(url):
    import os
    os.startfile(url)                      # noqa: S606 - the documented Windows way


def reveal(path):
    _run(["explorer", "/select,", path], timeout=10)


def keep_awake_command(pid):
    """Windows has no command-line equivalent, so this is done in-process instead --
    see shell.py, which calls SetThreadExecutionState through ctypes. Returning None
    is how a backend says "not by running something"."""
    return None
