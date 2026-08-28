"""Linux.

The parsing is separated from the shelling-out on purpose. Every `parse_*` function
below takes text and returns data, so the awkward half can be exercised on any machine
against captured output -- which is how these were tested, since the machine that wrote
them runs macOS.

Sources: `lsblk` for disks, `nmcli` for Wi-Fi. Both are on every desktop distribution
that ships NetworkManager, which is all of them; `iw`/`wpa_cli` would work on a machine
without it, and are not worth the second code path until somebody has one.
"""
import errno
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time

from ._dd import DDSink

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


# ────────────────────────────── The card write ──────────────────────────────
#
# Linux is the easy one of the two ports: the device is a plain block node, `dd` is
# present on every system, and there is no equivalent of macOS's removable-media consent
# to be refused by. What it does have that macOS does not is a desktop that automounts a
# card the instant its partitions reappear, which is why `unmount_disk` is called a
# second time before the filesystem is provisioned.

# A whitelist, not a pattern, because this is the last thing standing between a typo and
# somebody's root disk. Every family Linux names a whole disk after and nothing else.
_DEV_RE = re.compile(r"(sd[a-z]{1,3}|hd[a-z]{1,3}|vd[a-z]{1,3}|mmcblk\d+|nvme\d+n\d+"
                     r"|loop\d+)$")


def valid_device_id(dev):
    return bool(dev) and "/" not in dev and bool(_DEV_RE.fullmatch(dev))


def block_device(dev):
    return "/dev/%s" % dev


def raw_device(dev):
    """There is no separate character device on Linux; the block node is the raw one."""
    return "/dev/%s" % dev


def partition_devices(dev, partno):
    """sdb -> sdb1, but mmcblk0 -> mmcblk0p1 and nvme0n1 -> nvme0n1p1.

    The `p` exists so that a name already ending in a digit does not become ambiguous.
    Getting this wrong on an SD card in a native reader -- which is exactly the hardware
    this tool is for -- means provisioning a device that is not there.
    """
    sep = "p" if dev[-1:].isdigit() else ""
    return ["/dev/%s%s%d" % (dev, sep, partno)]


def parse_mounts(text, dev):
    """`/proc/mounts` → every mountpoint that lives on this disk, longest path first.

    Longest first so that a nested mount is released before the one it sits inside.

    The match has to be exact about where the disk name ends: `/dev/sdb` owns `/dev/sdb1`
    but emphatically does not own `/dev/sdb1x` or, more to the point, `/dev/sdbb`. A
    prefix test alone would unmount a second card in a second reader, which is the kind
    of mistake that is only noticed after the fact.
    """
    node = "/dev/%s" % dev
    found = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        src, point = parts[0], parts[1]
        if src != node:
            tail = src[len(node):] if src.startswith(node) else None
            if tail is None or not tail:
                continue
            if tail[0] == "p":
                tail = tail[1:]
            if not tail or not tail.isdigit():
                continue
        # /proc/mounts octal-escapes spaces and friends.
        found.append(point.encode().decode("unicode_escape"))
    # Longest first; ties broken by name so the order is the same on every run rather
    # than whatever the set happens to yield.
    return sorted(set(found), key=lambda point: (-len(point), point))


def _mounts_for(dev):
    try:
        with open("/proc/mounts") as f:
            return parse_mounts(f.read(), dev)
    except OSError:
        return []


def _swaps_for(dev):
    node = block_device(dev)
    out = []
    try:
        with open("/proc/swaps") as f:
            for line in f.read().splitlines()[1:]:
                src = line.split()[0] if line.split() else ""
                if src.startswith(node):
                    out.append(src)
    except OSError:
        pass
    return out


def unmount_disk(dev):
    """Release every volume on the disk, and confirm it actually happened.

    `udisksctl` is tried first when we are not root, so an unprivileged rescan can still
    tidy up; under pkexec plain `umount` is what works and udisksctl is the one that
    refuses.
    """
    root = (os.geteuid() == 0)

    for src in _swaps_for(dev):
        subprocess.run(["swapoff", src], capture_output=True)

    last = ""
    for point in _mounts_for(dev):
        done = False
        attempts = [["umount", point]] if root else [
            ["udisksctl", "unmount", "-b", point], ["umount", point]]
        attempts.append(["umount", "-l", point])   # lazy, as the last resort
        for cmd in attempts:
            p = subprocess.run(cmd, capture_output=True, text=True)
            if p.returncode == 0:
                done = True
                break
            last = (p.stderr or p.stdout or "").strip()
        if not done:
            return False, ("%s could not be unmounted.\n\nClose anything using the "
                           "card and try again.\n\n%s" % (point, last))

    for _ in range(20):
        if not _mounts_for(dev):
            break
        time.sleep(0.5)
    else:
        return False, ("Volumes on %s are still mounted after ten seconds."
                       % block_device(dev))

    # As on macOS, the only honest test of "can we write this" is to open it. A device
    # held by a stale loop mount or by udisks still fails EBUSY after /proc/mounts is
    # clean.
    node = block_device(dev)
    last = ""
    for _ in range(30):
        try:
            fd = os.open(node, os.O_WRONLY | os.O_EXCL)
            os.close(fd)
            return True, ""
        except OSError as e:
            last = str(e)
            if e.errno in (errno.EBUSY, errno.EAGAIN):
                time.sleep(0.5)
                continue
            # Not busy but not openable: almost always a permission refusal, and the
            # unmount genuinely did succeed. Let the pre-open probe in writer.py name
            # the real errno rather than blaming the unmount.
            return True, ""
    return False, ("%s is still busy fifteen seconds after unmounting.\n\n"
                   "Something still has the card open. Remove it and re-insert it.\n\n%s"
                   % (node, last))


def open_sink(dev, total=0):
    """dd, same as macOS. `conv=fsync` makes the exit code mean the data landed.

    Without it dd returns success once the writes are in the page cache, and a card
    pulled seconds later is silently incomplete -- the write reports done and the box
    never boots.
    """
    return DDSink(block_device(dev), ibs="1M", obs="4M", conv="fsync")


def open_reader(dev):
    return open(block_device(dev), "rb")


def flush():
    subprocess.run(["sync"])


def rescan_partitions(dev):
    """Make the new partition nodes appear after a whole-disk write.

    Three tools because distributions disagree about which is installed: partprobe is
    parted's, blockdev is util-linux's, and `udevadm settle` is what actually waits for
    /dev to catch up once the kernel has re-read the table.
    """
    node = block_device(dev)
    for cmd in (["partprobe", node], ["blockdev", "--rereadpt", node]):
        if subprocess.run(cmd, capture_output=True).returncode == 0:
            break
    subprocess.run(["udevadm", "settle", "--timeout=15"], capture_output=True)


def eject(dev):
    """Flush the buffers, then ask politely.

    `eject` is not installed everywhere and a card reader often refuses it; the flush is
    the part that matters, and a refused eject is not a failed write.
    """
    node = block_device(dev)
    subprocess.run(["blockdev", "--flushbufs", node], capture_output=True)
    subprocess.run(["eject", node], capture_output=True)


def mount_boot(dev, partno):
    """Mount the FAT boot partition ourselves. (path, release, detail).

    Nothing mounts it for us: running under pkexec we are root, and udisks does not
    automount for root. If the desktop got there first -- a rescan run unprivileged --
    that mount is used as it stands and left alone.
    """
    part = partition_devices(dev, partno)[0]
    for _ in range(30):
        if os.path.exists(part):
            break
        time.sleep(0.5)
    else:
        return None, (lambda: None), ("Expected %s to appear. Unplug and replug the "
                                      "card, then try again." % part)

    try:
        with open("/proc/mounts") as f:
            for line in f:
                p = line.split()
                if len(p) >= 2 and p[0] == part:
                    point = p[1].encode().decode("unicode_escape")
                    return point, (lambda: None), ""
    except OSError:
        pass

    point = tempfile.mkdtemp(prefix="riparr-boot-")
    p = subprocess.run(["mount", "-t", "vfat", "-o", "rw,sync,umask=000", part, point],
                       capture_output=True, text=True)
    if p.returncode != 0:
        os.rmdir(point)
        return None, (lambda: None), (
            "%s could not be mounted.\n\n%s" % (part, (p.stderr or p.stdout).strip()))

    def release():
        subprocess.run(["umount", point], capture_output=True)
        try:
            os.rmdir(point)
        except OSError:
            pass

    return point, release, ""


def explain_write_error(err, xerr, rc, node):
    blob = (str(err) + " " + str(xerr)).lower()
    if "permission denied" in blob or "errno 13" in blob or "not permitted" in blob:
        return ("Riparr was not allowed to write to %s.\n\n"
                "The write runs through pkexec and should already be root. If the "
                "authorisation dialog was dismissed, or this system has no polkit "
                "agent running, that is the cause.\n\n%s" % (node, err or "(no detail)"))
    if "busy" in blob or "errno 16" in blob:
        return ("%s is still in use.\n\nThe card had not finished unmounting — your "
                "desktop may have remounted it. Remove the card, re-insert it, and try "
                "again.\n\n%s" % (node, err or ""))
    if "no such file" in blob or "no medium" in blob:
        return ("%s disappeared.\n\nThe card was removed or the reader dropped it.\n\n%s"
                % (node, err or ""))
    if "no space" in blob or "errno 28" in blob:
        return ("The card is too small for this image.\n\nNothing usable was written.\n\n%s"
                % (err or ""))
    return (err or xerr or "the writer exited %s having written nothing." % rc)


# ───────────────────────────────── Elevation ─────────────────────────────────

CAN_WRITE = True


def elevate(argv, rundir, progress_path=""):
    """pkexec first, sudo second. (rc, stderr, cancelled).

    pkexec is the right answer on a desktop: polkit raises the dialog the user already
    recognises, the prompt names the program, and no password crosses this process. It
    exits 126 when the user dismisses it and 127 when it is not installed, which is the
    whole reason those two numbers are distinguished below.

    pkexec also **sanitises the environment**, which is why every path handed to it is
    absolute and why nothing here depends on PATH or on a virtualenv being active.

    The sudo fallback is for systems with no polkit agent -- a bare window manager, or a
    session started outside a desktop. It needs a graphical askpass, and if there is not
    one, saying so beats a password prompt on a terminal the user cannot see.
    """
    argv = [str(a) for a in argv]

    if shutil.which("pkexec"):
        p = subprocess.run(["pkexec"] + argv, capture_output=True, text=True)
        if p.returncode == 126:
            return p.returncode, (p.stderr or "").strip(), True
        if p.returncode != 127:
            return p.returncode, (p.stderr or "").strip(), False
        # 127 is pkexec's "could not run it at all"; fall through and try sudo.

    if not shutil.which("sudo"):
        return 1, ("This system has neither pkexec nor sudo, so Riparr cannot get "
                   "permission to write to the card."), False

    asker = next((t for t in ("zenity", "kdialog", "ssh-askpass",
                              "/usr/lib/ssh/x11-ssh-askpass") if shutil.which(t)), None)
    if not asker:
        return 1, ("No polkit agent is running and there is no graphical password "
                   "prompt available, so Riparr cannot ask for permission. Install "
                   "policykit-1 (or zenity) and try again."), False

    askpass = os.path.join(rundir, "askpass.sh")
    if asker.endswith("zenity"):
        body = ('exec zenity --password '
                '--title="Riparr needs permission to write to your SD card"\n')
    elif asker.endswith("kdialog"):
        body = ('exec kdialog --password '
                '"Riparr needs permission to write to your SD card."\n')
    else:
        body = 'exec %s "Riparr needs permission to write to your SD card."\n' % asker
    with open(askpass, "w") as f:
        f.write("#!/bin/sh\n" + body)
    os.chmod(askpass, 0o700)

    env = dict(os.environ, SUDO_ASKPASS=askpass)
    p = subprocess.run(["sudo", "-A"] + argv, capture_output=True, text=True, env=env)
    err = (p.stderr or "").strip()
    # A dismissed zenity exits 1 and sudo reports no password was supplied.
    cancelled = p.returncode != 0 and ("no password was provided" in err.lower()
                                       or "cancel" in err.lower())
    return p.returncode, err, cancelled


def probe_writable(dev):
    """Can this device be opened for writing right now? (ok, detail)."""
    node = block_device(dev)
    try:
        fd = os.open(node, os.O_WRONLY)
        os.close(fd)
        return True, ""
    except OSError as e:
        return False, explain_write_error(str(e), "", e.errno, node)


# ───────────────────────────── Updating itself ─────────────────────────────

UPDATE_SUFFIX = ".tar.gz"


def update_target(executable):
    """The directory PyInstaller's onedir build lives in.

    The layout is `Riparr Preparer/Riparr Preparer` plus an `_internal/` beside it, so
    the thing to replace is the parent directory -- and only when that parent actually
    looks like the bundle, never a directory somebody happens to be running from.
    """
    root = os.path.dirname(os.path.abspath(executable))
    if not os.path.isdir(os.path.join(root, "_internal")):
        return None
    return root


def swap_and_relaunch(archive, target, pid, rundir):
    """Unpack beside the install, swap the directory, exec the new binary."""
    parent = os.path.dirname(target) or "."
    if not os.access(parent, os.W_OK):
        return False, ("Riparr Preparer cannot update itself because %s is not "
                       "writable by you.\n\nMove it somewhere you own, or download the "
                       "new version yourself." % parent)

    script = os.path.join(rundir, "update.sh")
    name = os.path.basename(target)
    with open(script, "w") as f:
        f.write("""#!/bin/sh
n=0
while kill -0 %(pid)d 2>/dev/null && [ $n -lt 200 ]; do sleep 0.3; n=$((n+1)); done

tmp=$(mktemp -d) || exit 1
tar xzf %(archive)s -C "$tmp" || { rm -rf "$tmp"; exit 1; }
# The tarball holds one top-level directory; take whatever it is called rather than
# assuming, so a rename upstream does not break updating.
new=$(find "$tmp" -mindepth 1 -maxdepth 1 -type d -print -quit)
[ -n "$new" ] || { rm -rf "$tmp"; exit 1; }
[ -x "$new/%(name)s" ] || { rm -rf "$tmp"; exit 1; }

rm -rf %(target)s.old
mv %(target)s %(target)s.old 2>/dev/null
mv "$new" %(target)s || { mv %(target)s.old %(target)s; rm -rf "$tmp"; exit 1; }
rm -rf %(target)s.old "$tmp" %(archive)s

exec %(target)s/%(name)s
""" % {"pid": pid, "archive": shlex.quote(archive),
       "target": shlex.quote(target), "name": name})
    os.chmod(script, 0o700)
    subprocess.Popen(["/bin/sh", script], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return True, ""


# ─────────────────────── Location, and why there is none ───────────────────────
#
# Part of the hostos contract because macOS needs it, not because this platform does.
# A scan here names networks and reports bands without asking anyone's permission, so
# the honest answer is "the question does not arise" -- which is a different answer from
# "we tried and could not find out", and the Wi-Fi screen shows a different thing for
# each. Returning the same shape from every backend is what stops core.py guessing.

def location_status():
    """(None, "not-required"). Scans here are not gated on a location permission."""
    return None, "not-required"


def request_location(timeout=12):
    """Nothing to ask for. See location_status."""
    return False
