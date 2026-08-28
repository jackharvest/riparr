"""macOS. The original implementation, moved rather than rewritten.

Everything here was `core.py` until the Windows and Linux ports needed a seam. It is
byte-for-byte the code that has been writing cards on this platform, which is the point:
a port is not the moment to also re-derive the parts that already work.

Sources, in the order they are trusted: CoreWLAN for the scan (the only method that
returns unredacted SSIDs without a Location Services grant -- `airport` was removed in
macOS 26 and `system_profiler` redacts), `security` for saved passphrases, `diskutil`
and `ioreg` for disks.
"""
import errno
import os
import plistlib
import re
import shlex
import shutil
import tempfile
import subprocess
import time

from ._dd import DDSink

NAME = "macOS"

# The keychain prompt is answered by a human, and macOS never dismisses it on its own.
# Long enough that walking away and coming back still works; finite so a wedged
# `security` cannot hang the interface forever.
KEYCHAIN_TIMEOUT = 900


# ───────────────────────────────── Wi-Fi ─────────────────────────────────

def _band_for_channel(ch_band):
    return {1: "2.4", 2: "5", 3: "6"}.get(ch_band)


def _band_from_number(ch):
    """Band from the channel number, when the band enum comes back unknown.

    Belt and braces for the same answer from a second direction: the enum has been seen
    to be 0 on scan results that still carry a usable channel number.
    """
    if not ch:
        return None
    if 1 <= ch <= 14:
        return "2.4"
    if 32 <= ch <= 177:
        return "5"
    return None


# CLAuthorizationStatus
_LOC_OK = (3, 4)          # authorizedAlways, authorizedWhenInUse


def location_status():
    """(status_int, name). macOS gates scan SSIDs behind Location Services."""
    names = {0: "notDetermined", 1: "restricted", 2: "denied",
             3: "authorizedAlways", 4: "authorizedWhenInUse"}
    try:
        import CoreLocation
        st = CoreLocation.CLLocationManager.authorizationStatus()
        return st, names.get(st, "unknown")
    except Exception:
        return None, "unavailable"


def request_location(timeout=12):
    """Ask for Location Services, because a Wi-Fi scan is useless without it.

    macOS returns every SSID as nil from `scanForNetworksWithSSID:` unless the calling
    application is authorised for location. The scan still reports channels, bands and
    signal -- just not *which network* any of it belongs to -- so the Preparer fell back
    to the list of saved networks, which has names and nothing else. The visible symptom
    was every network saying "band unknown", on a screen whose whole purpose is telling
    2.4 GHz from 5 GHz.

    Returns True if authorised. Never raises: an unauthorised scan is degraded, not fatal.
    """
    try:
        import CoreLocation
    except Exception:
        return False
    try:
        st = CoreLocation.CLLocationManager.authorizationStatus()
        if st in _LOC_OK:
            return True
        if st != 0:                       # denied or restricted: asking again does nothing
            return False
        mgr = CoreLocation.CLLocationManager.alloc().init()
        mgr.requestWhenInUseAuthorization()
        # The answer arrives on the run loop. Poll rather than block it.
        deadline = time.time() + timeout
        while time.time() < deadline:
            st = CoreLocation.CLLocationManager.authorizationStatus()
            if st in _LOC_OK:
                return True
            if st not in (0,):
                return False
            time.sleep(0.25)
    except Exception:
        return False
    return False


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
        band = (_band_for_channel(ch.channelBand() if ch else 0)
                or _band_from_number(ch.channelNumber() if ch else 0))
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


# ────────────────────────────── The card write ──────────────────────────────
#
# Everything below was `writer.py`'s body until the Linux and Windows ports needed the
# same shapes. It is the same code, moved, not rewritten -- this is the only one of the
# three that has written a card that then booted, and it does not get quietly changed
# while porting the other two.

def valid_device_id(dev):
    """diskN and nothing else. The last line of defence before a raw write."""
    return bool(re.fullmatch(r"disk\d+", dev or ""))


def block_device(dev):
    return "/dev/%s" % dev


def raw_device(dev):
    """The character device. Markedly faster than the buffered one for a bulk write."""
    return "/dev/r%s" % dev


def partition_devices(dev, partno):
    """Candidates for a partition, best first.

    Raw is much faster for the MakeMKV copy, but raw nodes on macOS demand aligned IO,
    so the buffered node stays as a fallback rather than being the single point of
    failure.
    """
    return ["/dev/r%ss%d" % (dev, partno), "/dev/%ss%d" % (dev, partno)]


def unmount_disk(dev):
    """Unmount every volume on the disk, and confirm it actually happened.

    diskutil returning is not the same as the volumes having released. Writing to the
    raw device while anything is still mounted fails with EBUSY, which previously
    surfaced only as a dead `dd` and no explanation.
    """
    node = block_device(dev)
    p = subprocess.run(["diskutil", "unmountDisk", node], capture_output=True, text=True)
    if p.returncode != 0:
        p = subprocess.run(["diskutil", "unmountDisk", "force", node],
                           capture_output=True, text=True)
        if p.returncode != 0:
            return False, ((p.stderr or p.stdout).strip()
                           + "\n\nClose anything using the card and try again.")

    for _ in range(20):
        mounts = subprocess.run(["mount"], capture_output=True, text=True).stdout
        if not any(("/dev/%s" % dev) in line for line in mounts.splitlines()):
            break
        time.sleep(0.5)
    else:
        return False, ("Volumes on %s are still mounted after ten seconds." % node)

    # Disappearing from `mount` is NOT the same as the device being free. On macOS 26
    # FAT volumes are served by a userspace FSKit extension
    # (com.apple.fskit.msdos.appex), which keeps /dev/rdiskNs1 open and can outlive the
    # unmount by a few seconds. The old check passed here and dd then died on EBUSY.
    # The only honest test of "can we write this" is to open it.
    rdev = raw_device(dev)
    last = ""
    for _ in range(30):
        try:
            fd = os.open(rdev, os.O_WRONLY)
            os.close(fd)
            return True, ""
        except OSError as e:
            last = str(e)
            if e.errno == errno.EBUSY:
                time.sleep(0.5)     # something still holds it; give it a moment
                continue
            # Not busy, but not openable either -- a permission refusal, most likely.
            # The unmount genuinely did succeed, and saying "could not be unmounted"
            # here would send the user hunting the wrong problem. Hand off to the
            # pre-open probe in writer.py, which routes the real errno through
            # explain_write_error().
            return True, ""
    return False, ("%s is still busy fifteen seconds after unmounting.\n\n"
                   "Something still has the card open. Ejecting it in Finder and "
                   "re-inserting it clears this.\n\n%s" % (rdev, last))


def open_sink(dev, total=0):
    """dd, kept.

    It handles the block alignment the raw device demands, and on the one platform that
    has actually written a booting card it is not worth replacing with something that
    has not. Windows, which has no dd, writes through its own sink; see hostos/windows.
    """
    return DDSink(raw_device(dev))


def open_reader(dev):
    return open(raw_device(dev), "rb")


def flush():
    subprocess.run(["sync"])


def rescan_partitions(dev):
    """Nudge the kernel into re-reading the partition table after a raw write."""
    subprocess.run(["diskutil", "list", block_device(dev)], capture_output=True)


def eject(dev):
    subprocess.run(["diskutil", "eject", block_device(dev)], capture_output=True)


def _looks_like_pi_boot(path):
    """A Raspberry Pi boot partition always carries these. An unrelated FAT volume won't."""
    return (os.path.exists(os.path.join(path, "config.txt"))
            and os.path.exists(os.path.join(path, "cmdline.txt")))


def mount_boot(dev, partno):
    """Where the FAT boot partition landed. (path, release, detail).

    diskutil remounts it on its own once the write settles, so there is nothing to
    mount and nothing to release -- hence a no-op `release`. The other two platforms
    have to do the mounting themselves and give back a real one.
    """
    for _ in range(40):
        mounts = subprocess.run(["mount"], capture_output=True, text=True).stdout
        for line in mounts.splitlines():
            if "msdos" in line and "/Volumes/" in line and dev in line:
                return line.split(" on ")[1].split(" (")[0], (lambda: None), ""
        time.sleep(1.5)

    # Some readers omit the device from the mount line, so fall back to scanning FAT
    # volumes -- but ONLY ones that actually look like a freshly written Pi boot
    # partition. Taking any msdos volume would, on a machine with a camera card or USB
    # stick attached, write custom.toml (containing the derived Wi-Fi PSK and the
    # account password hash) onto unrelated removable media.
    mounts = subprocess.run(["mount"], capture_output=True, text=True).stdout
    for line in mounts.splitlines():
        if "msdos" not in line or "/Volumes/" not in line:
            continue
        cand = line.split(" on ")[1].split(" (")[0]
        if _looks_like_pi_boot(cand):
            return cand, (lambda: None), ""
    return None, (lambda: None), ("Unplug and replug the card, then use "
                                  "Apply settings only.")


def _responsible_app():
    """The application macOS holds responsible for what this process does.

    TCC attributes consent to the nearest enclosing .app bundle up the process tree, so
    that is the one the user has to grant -- naming it saves them guessing which of the
    dozen entries in the list matters.
    """
    pid, found = os.getpid(), None
    for _ in range(12):
        out = subprocess.run(["ps", "-o", "ppid=,comm=", "-p", str(pid)],
                             capture_output=True, text=True).stdout.strip()
        if not out:
            break
        parent, _, comm = out.partition(" ")
        if ".app/Contents/MacOS/" in comm:
            # Keep walking rather than returning here: an interpreter run from a
            # terminal sits inside Python.app, which owns no consent. The application
            # that matters is the outermost one, nearest launchd.
            found = comm.split(".app/")[0].rsplit("/", 1)[-1] + ".app"
        try:
            pid = int(parent)
        except ValueError:
            break
        if pid <= 1:
            break
    return found or "the application you launched this from"


def explain_write_error(err, xerr, rc, rdev):
    """Turn a terse dd failure into something the user can act on."""
    blob = (str(err) + " " + str(xerr)).lower()
    if "operation not permitted" in blob or "not permitted" in blob:
        return ("macOS blocked access to %s.\n\n"
                "This is a privacy refusal, not a file permission — running as root "
                "does not lift it. macOS grants disk access to the *application* the "
                "write is attributed to, which here is %s.\n\n"
                "Grant it in System Settings > Privacy & Security > Full Disk Access, "
                "quit that application completely, reopen it and run this again.\n\n%s"
                % (rdev, _responsible_app(), err or "(no detail)"))
    if "resource busy" in blob or "busy" in blob or "errno 16" in blob:
        return ("%s is still in use.\n\nThe card had not finished unmounting. Eject it "
                "in Finder, re-insert it, and try again.\n\n%s" % (rdev, err or ""))
    if "no such file" in blob:
        return ("%s disappeared.\n\nThe card was removed or the reader dropped it.\n\n%s"
                % (rdev, err or ""))
    return (err or xerr or "the writer exited %s having written nothing." % rc)


# ───────────────────────────────── Elevation ─────────────────────────────────

CAN_WRITE = True


def elevate(argv, rundir, progress_path=""):
    """One authorization dialog covers write, provision and eject. (rc, stderr, cancelled).

    Elevation is `sudo -A`, deliberately, and not osascript's `with administrator
    privileges`. The latter runs the helper through security_authtrampoline, which
    re-parents it away from the launching application — and macOS attributes disk and
    removable-volume consent to the *responsible* application, not to the user and not to
    root. A trampolined helper therefore inherits no consent at all and is refused with
    EPERM on /dev/rdiskN even as root, while the terminal it was launched from can write
    the very same card. sudo keeps the writer a direct descendant, so the consent that is
    already granted still applies.
    """
    script = os.path.join(rundir, "write.sh")
    with open(script, "w") as f:
        f.write("#!/bin/sh\nexec %s\n" % " ".join(shlex.quote(str(a)) for a in argv))
    os.chmod(script, 0o700)

    # sudo asks for the password up to three times. The sentinel makes a cancelled
    # dialog cancel the whole write instead of asking twice more.
    cancel = os.path.join(rundir, "cancelled")
    if os.path.exists(cancel):
        os.remove(cancel)
    askpass = os.path.join(rundir, "askpass.sh")
    with open(askpass, "w") as f:
        f.write(
            '#!/bin/sh\n'
            '[ -f %s ] && exit 1\n'
            'pw=$(osascript -e \'display dialog "Riparr needs permission to write '
            'to your SD card." with title "Riparr Preparer" default answer "" '
            'with hidden answer with icon caution\' -e \'text returned of result\' '
            '2>/dev/null) || { : > %s; exit 1; }\n'
            'printf %%s "$pw"\n' % (shlex.quote(cancel), shlex.quote(cancel)))
    os.chmod(askpass, 0o700)

    env = dict(os.environ, SUDO_ASKPASS=askpass)
    p = subprocess.run(["sudo", "-A", "/bin/sh", script],
                       capture_output=True, text=True, env=env)
    return p.returncode, (p.stderr or "").strip(), os.path.exists(cancel)


def probe_writable(dev):
    """Can this device be opened for writing right now? (ok, detail).

    Run before the image is opened and before anything is decompressed, so that a
    refusal names its real cause instead of being inferred from a child process that has
    already exited.
    """
    rdev = raw_device(dev)
    try:
        fd = os.open(rdev, os.O_WRONLY)
        os.close(fd)
        return True, ""
    except OSError as e:
        return False, explain_write_error(str(e), "", e.errno, rdev)


# ───────────────────────────── Updating itself ─────────────────────────────
#
# An app cannot replace itself while it is running, so none of this happens in this
# process. A shell script is written out, launched detached, and told the PID to wait
# for; this process then quits, the script swaps the bundle and opens the new one. The
# user sees the window close and come back.

UPDATE_SUFFIX = ".dmg"


def update_target(executable):
    r"""The thing to replace: the .app bundle, found by walking up from the binary.

    A PyInstaller --windowed build puts the executable at
    `Riparr Preparer.app/Contents/MacOS/Riparr Preparer`, so the bundle is three levels
    up. Returning None means this is not a bundled app -- a source checkout -- and
    self-update refuses rather than moving somebody's working tree around.
    """
    marker = ".app/Contents/MacOS/"
    if marker not in executable:
        return None
    return executable.split(".app/Contents/MacOS/")[0] + ".app"


def swap_and_relaunch(archive, target, pid, rundir):
    """Mount the .dmg, copy the bundle over the old one, relaunch. (ok, detail)."""
    if not os.access(os.path.dirname(target) or "/", os.W_OK):
        return False, ("Riparr Preparer cannot update itself because %s is not "
                       "writable by you.\n\nMove the app somewhere you own, or "
                       "download the new version yourself."
                       % (os.path.dirname(target) or "/"))

    # Not in rundir: that directory belongs to the process being replaced. A swapper
    # whose own script and payload can be cleaned up underneath it fails in ways nobody
    # can reproduce afterwards.
    work = tempfile.mkdtemp(prefix="riparr-update-")
    dmg = os.path.join(work, os.path.basename(archive))
    shutil.copy2(archive, dmg)
    script = os.path.join(work, "update.sh")
    log = os.path.join(work, "update.log")
    with open(script, "w") as f:
        f.write("""#!/bin/sh
# Logged, because this runs after the app it is replacing has gone and has nowhere else
# to report a failure. The path is handed back to the caller and shown on screen.
exec >>%(log)s 2>&1
set -x
echo "swap started $(date)"

# Wait for the app to actually exit. Copying over a running bundle corrupts it.
#
# And then stop asking. The process being replaced is the least reliable participant in
# its own replacement -- for six releases its quit thread died on its first line -- and
# a swapper that only ever waits turns that into an app frozen on "Restarting" with no
# way forward and no way back. Escalating is safe here: install_update refuses to start
# while a card is being written or a box set up, so this process holds nothing by now
# that is worth saving.
gone() { ! kill -0 %(pid)d 2>/dev/null; }
waitgone() {  # $1 ticks of 0.2s, quiet -- traced, the poll buries the log in its own spam
  set +x
  n=0
  while [ $n -lt $1 ] && ! gone; do sleep 0.2; n=$((n+1)); done
  set -x
  gone
}

if ! waitgone 50; then
  echo "it did not quit when asked; sending TERM"
  kill -TERM %(pid)d 2>/dev/null
  if ! waitgone 25; then
    echo "still up after TERM; sending KILL"
    kill -KILL %(pid)d 2>/dev/null
    if ! waitgone 25; then
      echo "the old process survived SIGKILL; leaving the installed app alone"
      exit 1
    fi
  fi
fi
echo "the old process is gone; installing"

mnt=$(mktemp -d /tmp/riparr-update.XXXXXX) || exit 1
hdiutil attach %(dmg)s -nobrowse -quiet -mountpoint "$mnt" || exit 1
app=$(find "$mnt" -maxdepth 1 -name '*.app' -print -quit)
[ -n "$app" ] || { hdiutil detach "$mnt" -quiet; exit 1; }

# ditto, not cp: it keeps the bundle's structure and extended attributes intact.
rm -rf %(target)s.new
ditto "$app" %(target)s.new || { hdiutil detach "$mnt" -quiet; exit 1; }
hdiutil detach "$mnt" -quiet

# Fetched over HTTPS and checked against the release checksum before it got here, but it
# is still a bundle that came from the internet. Strip the quarantine flag so nobody is
# sent back to Security settings to re-approve an app they already approved.
xattr -dr com.apple.quarantine %(target)s.new 2>/dev/null

# Refuse to install something macOS would refuse to open. Keeping the working old app
# beats leaving a broken one where it used to be.
codesign --verify --deep --strict %(target)s.new || {
  echo "the downloaded app does not verify; keeping the installed one"
  rm -rf %(target)s.new
  exit 1
}

rm -rf %(target)s.old
mv %(target)s %(target)s.old 2>/dev/null
mv %(target)s.new %(target)s || { mv %(target)s.old %(target)s; exit 1; }
rm -rf %(target)s.old

# Tell Launch Services the bundle changed, so `open` starts the new one rather than a
# cached record of the old.
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f %(target)s 2>/dev/null

open %(target)s || echo "open failed"
echo "swap finished $(date)"
rm -f %(dmg)s
""" % {"pid": pid, "dmg": shlex.quote(dmg), "target": shlex.quote(target),
       "log": shlex.quote(log)})
    os.chmod(script, 0o700)
    subprocess.Popen(["/bin/sh", script], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return True, log
