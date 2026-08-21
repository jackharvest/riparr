"""Linux.

The parsing is separated from the shelling-out on purpose. Every `parse_*` function
below takes text and returns data, so the awkward half can be exercised on any machine
against captured output -- which is how these were tested, since the machine that wrote
them runs macOS.

Sources: `lsblk` for disks, `nmcli` for Wi-Fi. Both are on every desktop distribution
that ships NetworkManager, which is all of them; `iw`/`wpa_cli` would work on a machine
without it, and are not worth the second code path until somebody has one.
"""
import json
import subprocess

NAME = "Linux"


def _run(cmd, timeout=20):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.stdout if p.returncode == 0 else ""
    except Exception:
        return ""


# ───────────────────────────────── Disks ─────────────────────────────────

def parse_lsblk(text, root_disk=""):
    """`lsblk -J -b -O` JSON → the normalised shape core.classify_disk consumes.

    `rm` is the kernel's removable flag, read straight from `/sys/block/*/removable`,
    which is the same SCSI removable-medium bit macOS calls `RemovableMedia` and
    Windows calls a "Removable Media" MediaType. Keeping the *name* different per
    platform and the *meaning* identical is the whole point of this layer.

    `hotplug` is the weaker claim -- the device can be unplugged -- and maps to
    `ejectable`, exactly as macOS's `Ejectable` does.
    """
    try:
        blob = json.loads(text)
    except Exception:
        return []
    out = []
    for d in blob.get("blockdevices", []) or []:
        if d.get("type") != "disk":
            continue
        name = d.get("name") or ""
        if not name:
            continue
        tran = (d.get("tran") or "").strip()
        subsystems = (d.get("subsystems") or "")
        # An SD card in a native reader arrives on the mmc bus, which is unambiguous in
        # a way USB never is.
        if tran in ("", "mmc") and "mmc" in subsystems:
            tran = "mmc"
        out.append({
            "id": name,
            "size": int(d.get("size") or 0),
            "name": (d.get("model") or d.get("vendor") or "?").strip() or "?",
            "model": " ".join(x for x in ((d.get("vendor") or "").strip(),
                                          (d.get("model") or "").strip()) if x),
            "protocol": tran,
            "removable_media": _flag(d.get("rm")),
            "ejectable": _flag(d.get("hotplug")),
            # The disk carrying / is the one mistake that cannot be walked back.
            "internal": (name == root_disk) or not _flag(d.get("hotplug")),
            "virtual": tran in ("loop", "zram") or name.startswith(("loop", "zram", "dm-")),
            "icon": "",
        })
    return out


def _flag(v):
    """lsblk reports booleans as true/false, "1"/"0" or 1/0 depending on version."""
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes")
    return bool(v)


def root_disk():
    """The whole-disk device that / lives on, so it can never be offered."""
    text = _run(["lsblk", "-J", "-b", "-o", "NAME,MOUNTPOINT,TYPE"])
    try:
        blob = json.loads(text)
    except Exception:
        return ""

    def walk(node, top):
        if node.get("mountpoint") == "/":
            return top
        for c in node.get("children", []) or []:
            hit = walk(c, top)
            if hit:
                return hit
        return ""

    for d in blob.get("blockdevices", []) or []:
        hit = walk(d, d.get("name") or "")
        if hit:
            return hit
    return ""


def list_block_devices():
    return parse_lsblk(_run(["lsblk", "-J", "-b", "-O"]), root_disk())


# ───────────────────────────────── Wi-Fi ─────────────────────────────────

def _band_for_freq(mhz):
    try:
        mhz = int(str(mhz).split()[0])
    except Exception:
        return None
    if 2400 <= mhz < 2500:
        return "2.4"
    if 5100 <= mhz < 5900:
        return "5"
    if 5925 <= mhz <= 7125:
        return "6"
    return None


def parse_nmcli_wifi(text):
    """`nmcli -t -f SSID,FREQ,SIGNAL,SECURITY dev wifi` → aggregate by SSID.

    Terse mode escapes a literal colon inside a field as `\\:`, so splitting naively
    mangles any SSID containing one. Fields are split on unescaped colons only.
    """
    agg = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        f = _split_terse(line)
        if len(f) < 4:
            continue
        ssid, freq, signal, security = f[0], f[1], f[2], f[3]
        if not ssid:
            continue                      # a hidden network reports an empty SSID
        try:
            # nmcli reports 0-100 quality; the interface wants dBm, and this is the
            # conversion NetworkManager itself documents.
            rssi = int(signal) / 2.0 - 100
        except Exception:
            rssi = None
        band = _band_for_freq(freq)
        secure = bool(security.strip()) and security.strip() != "--"
        e = agg.get(ssid)
        if e is None:
            agg[ssid] = {"ssid": ssid, "bands": [band] if band else [],
                         "rssi": rssi, "secure": secure,
                         "saved": False, "seen": True}
        else:
            if band and band not in e["bands"]:
                e["bands"].append(band)
            if rssi is not None and (e["rssi"] is None or rssi > e["rssi"]):
                e["rssi"] = rssi
    return agg


def _split_terse(line):
    """Split nmcli terse output on colons that are not backslash-escaped."""
    out, cur, esc = [], "", False
    for ch in line:
        if esc:
            cur += ch
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == ":":
            out.append(cur)
            cur = ""
        else:
            cur += ch
    out.append(cur)
    return out


def parse_nmcli_saved(text):
    """`nmcli -t -f NAME,TYPE connection show` → saved Wi-Fi connection names."""
    names = []
    for line in text.splitlines():
        f = _split_terse(line.strip())
        if len(f) >= 2 and f[1].endswith("wireless") and f[0]:
            names.append(f[0])
    return names


def scan_wifi():
    _run(["nmcli", "dev", "wifi", "rescan"], timeout=25)
    agg = parse_nmcli_wifi(
        _run(["nmcli", "-t", "-f", "SSID,FREQ,SIGNAL,SECURITY", "dev", "wifi"], timeout=30))
    method = "live" if agg else None
    for name in parse_nmcli_saved(_run(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"])):
        if name in agg:
            agg[name]["saved"] = True
        else:
            agg[name] = {"ssid": name, "bands": [], "rssi": None, "secure": True,
                         "saved": True, "seen": False}
    if agg and not method:
        method = "saved"
    return list(agg.values()), (method or "none")


def saved_network_password(ssid):
    """NetworkManager holds it; `-s` is what makes it hand a secret over.

    Reading another user's secrets needs privilege, so an unprivileged desktop session
    gets a polkit prompt -- which is the same bargain macOS makes with its keychain
    dialog: the OS asks, in a dialog the user recognises, and nothing is read without
    their consent.
    """
    if not ssid:
        return "", "no network chosen"
    # Same reasoning as the macOS keychain: polkit raises a dialog and waits on a
    # person, and macOS's 60-second version of this timed out on a real user who had
    # stepped away. See KEYCHAIN_TIMEOUT in hostos/darwin.py.
    out = _run(["nmcli", "-s", "-g", "802-11-wireless-security.psk",
                "connection", "show", ssid], timeout=900).strip()
    if out:
        return out, ""
    return "", "This machine hasn't saved a password for that network."


# ─────────────────── Desktop integration ───────────────────

def open_url(url):
    subprocess.run(["xdg-open", url])


def reveal(path):
    # No universal "reveal in file manager", so open the containing directory, which
    # every desktop handles.
    import os
    subprocess.run(["xdg-open", os.path.dirname(path) or "."])


def keep_awake_command(pid):
    """systemd-inhibit is the portable answer on any systemd desktop, which is all of
    them now. `--what=idle:sleep` matches what caffeinate -i does on macOS."""
    return ["systemd-inhibit", "--what=idle:sleep", "--who=Riparr Preparer",
            "--why=Writing a card", "--mode=block",
            "sh", "-c", "while kill -0 %d 2>/dev/null; do sleep 5; done" % pid]
