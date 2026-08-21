"""macOS. The original implementation, moved rather than rewritten.

Everything here was `core.py` until the Windows and Linux ports needed a seam. It is
byte-for-byte the code that has been writing cards on this platform, which is the point:
a port is not the moment to also re-derive the parts that already work.

Sources, in the order they are trusted: CoreWLAN for the scan (the only method that
returns unredacted SSIDs without a Location Services grant -- `airport` was removed in
macOS 26 and `system_profiler` redacts), `security` for saved passphrases, `diskutil`
and `ioreg` for disks.
"""
import plistlib
import re
import subprocess

NAME = "macOS"

# The keychain prompt is answered by a human, and macOS never dismisses it on its own.
# Long enough that walking away and coming back still works; finite so a wedged
# `security` cannot hang the interface forever.
KEYCHAIN_TIMEOUT = 900


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


def wifi_device():
    """The Wi-Fi interface's BSD name. Not always en0.

    On a Mac with Thunderbolt Ethernet, or any machine where the ports enumerate
    differently, en0 is the wired interface and the Wi-Fi questions asked of it come
    back empty -- which reads as "you have no saved networks" rather than "we asked the
    wrong device".
    """
    try:
        out = subprocess.run(["networksetup", "-listallhardwareports"],
                             capture_output=True, text=True, timeout=15).stdout
        block = None
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("Hardware Port:"):
                block = line.split(":", 1)[1].strip()
            elif line.startswith("Device:") and block in ("Wi-Fi", "AirPort"):
                return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return "en0"


def saved_networks():
    """Preferred networks, unredacted, but with no band or signal information."""
    try:
        out = subprocess.run(
            ["networksetup", "-listpreferredwirelessnetworks", wifi_device()],
            capture_output=True, text=True, timeout=15).stdout
        return [l.strip() for l in out.splitlines()[1:] if l.strip()]
    except Exception:
        return []


def keychain_wifi_password(ssid):
    """The Wi-Fi passphrase this Mac already knows, from the login keychain.

    A mistyped Wi-Fi password is the single most expensive mistake available in this
    tool: nothing detects it, the card writes perfectly, the box boots perfectly, and
    it never appears on the network -- and the only recovery is to write the card
    again. Meanwhile the correct passphrase is, in the overwhelmingly common case,
    sitting in the keychain of the machine running the Preparer.

    macOS shows its own authorization dialog the first time. That prompt is a feature:
    it is the OS asking on the user's behalf, in a dialog they recognise, and no
    passphrase is read without their consent. Returns (password, error).
    """
    if not ssid:
        return "", "no network chosen"
    try:
        p = subprocess.run(
            ["security", "find-generic-password",
             "-D", "AirPort network password", "-a", ssid, "-w"],
            capture_output=True, text=True, timeout=KEYCHAIN_TIMEOUT)
    except subprocess.TimeoutExpired:
        # This waits on a *person*, not on a computer. The old 60 seconds was a guess
        # at how long a dialog takes to answer, and it is wrong for anybody who walked
        # away from the machine -- which, given this tool then spends ten unattended
        # minutes writing a card, is most of them.
        return "", ("The macOS password dialog wasn't answered, so nothing was read. "
                    "Press the button again when you're ready to approve it.")
    except Exception as e:
        # Never surface a raw exception here. This is the most consequential dialog in
        # the tool, and `Command '[...]' timed out after 60 seconds` is the least
        # actionable sentence it could possibly end with.
        return "", "macOS wouldn't hand over the saved password (%s)." % type(e).__name__
    if p.returncode == 0:
        return p.stdout.rstrip("\n"), ""
    err = (p.stderr or "").strip()
    if "could not be found" in err or p.returncode == 44:
        return "", "This Mac hasn't saved a password for that network."
    if "User canceled" in err or "-128" in err:
        return "", "Cancelled."
    return "", err or "The keychain wouldn't give it up."


def _media_icons():
    """{bsd name: IOKit icon resource} -- the signal drivelist calls `isCard`.

    `SD.icns` means the media is in a real card slot. Read from `ioreg` so this needs
    no DiskArbitration binding: pyobjc-framework-DiskArbitration is not a dependency
    and should not become one for a single dictionary lookup.
    """
    try:
        out = subprocess.run(["ioreg", "-a", "-c", "IOMedia", "-r", "-l"],
                             capture_output=True).stdout
        root = plistlib.loads(out)
    except Exception:
        return {}
    icons, stack = {}, [root]
    while stack:
        node = stack.pop()
        if isinstance(node, list):
            stack.extend(node)
            continue
        if not isinstance(node, dict):
            continue
        bsd, icon = node.get("BSD Name"), node.get("IOMediaIcon")
        if bsd and isinstance(icon, dict):
            icons[bsd] = icon.get("IOBundleResourceFile", "")
        stack.extend(node.get("IORegistryEntryChildren") or [])
    return icons


def scan_wifi():
    """(networks, method). Degrades rather than raising; never the caller's problem.

    Returns raw networks. Whether the *board* can join one is a rule, not a fact about
    this Mac, so `pi_ok` is added by core.scan_networks().
    """
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
    return list(agg.values()), (method or "none")


def saved_network_password(ssid):
    return keychain_wifi_password(ssid)


# ───────────────────────────────── Disks ─────────────────────────────────

def _truthy_removable(value):
    """`RemovableMedia` is a bool on current macOS and a string on older ones."""
    if isinstance(value, str):
        return value.strip().lower() == "removable"
    return bool(value)


def list_block_devices():
    """External physical media, as facts. Classification is core's job.

    `diskutil list external physical` already excludes the startup disk; the internal
    and virtual checks below are belt and braces for a machine that reports oddly.
    """
    try:
        out = subprocess.run(
            ["diskutil", "list", "-plist", "external", "physical"],
            capture_output=True).stdout
        disks = plistlib.loads(out).get("AllDisksAndPartitions", [])
    except Exception:
        return []
    icons = _media_icons()
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
        res.append({
            "id": ident,
            "size": info.get("TotalSize", 0),
            "name": (info.get("MediaName") or "?").strip(),
            "model": (info.get("IORegistryEntryName") or "").strip(),
            "protocol": info.get("BusProtocol", ""),
            "removable_media": _truthy_removable(info.get("RemovableMedia")),
            "ejectable": bool(info.get("Ejectable", False)),
            "internal": bool(info.get("Internal", False))
                        or bool(info.get("SystemImage", False)),
            "virtual": info.get("VirtualOrPhysical") == "Virtual",
            "icon": icons.get(ident, ""),
        })
    return res


# ─────────────────── Desktop integration ───────────────────

def open_url(url):
    """Hand a URL to the user's browser."""
    subprocess.run(["open", url])


def reveal(path):
    subprocess.run(["open", "-R", path])


def keep_awake_command(pid):
    """`caffeinate -i` holds only the idle-sleep assertion, so the screen still dims
    and locks normally. Closing the lid sleeps regardless; nothing in userspace
    prevents that, which is what the setup screen's copy has to be honest about."""
    return ["caffeinate", "-i", "-w", str(pid)]
