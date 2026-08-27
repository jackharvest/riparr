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
import ctypes
import json
import os
import re
import subprocess
import time

try:
    from ctypes import wintypes
except (ImportError, ValueError):
    # `ctypes.wintypes` exists only on Windows. Importing this module elsewhere still
    # has to work: every parse_* function above is exercised on macOS against captured
    # output, and losing that would mean the localisation handling could only be tested
    # on the platform nobody developing this is sitting at.
    wintypes = None

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


# ────────────────────────────── The card write ──────────────────────────────
#
# The one platform where none of the shape carries over. There is no `dd`, no
# `/dev/rdiskN`, and -- the part that actually bites -- no equivalent of "unmount the
# disk and the kernel lets go". Windows keeps a *volume* mounted independently of the
# disk beneath it, and a raw write to `\\.\PHYSICALDRIVEn` while any volume on it is
# live is either refused or, worse, silently interleaved with the filesystem driver's
# own writes.
#
# So the sequence is fixed and it is not optional:
#
#   1. find every volume sitting on this physical disk        (by device number, exactly)
#   2. FSCTL_LOCK_VOLUME each one                             (fails if anything has a file open)
#   3. FSCTL_DISMOUNT_VOLUME each one                         (the filesystem lets go)
#   4. hold those handles open for the whole write            (closing one remounts it)
#   5. FSCTL_ALLOW_EXTENDED_DASD_IO on the disk handle        (or writes stop at the partition)
#   6. write whole sectors, out of page-aligned memory
#   7. IOCTL_DISK_UPDATE_PROPERTIES                           (re-read the partition table)
#
# Step 4 is the one that is easy to get wrong: the lock is a property of the handle, not
# of the volume, so a `with` block that tidies up early hands the card back to Windows
# halfway through the write.

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
FILE_FLAG_NO_BUFFERING = 0x20000000
FILE_FLAG_WRITE_THROUGH = 0x80000000

FSCTL_LOCK_VOLUME = 0x00090018
FSCTL_UNLOCK_VOLUME = 0x0009001C
FSCTL_DISMOUNT_VOLUME = 0x00090020
FSCTL_ALLOW_EXTENDED_DASD_IO = 0x00090083
IOCTL_DISK_UPDATE_PROPERTIES = 0x00070140
IOCTL_STORAGE_EJECT_MEDIA = 0x002D4808
IOCTL_STORAGE_GET_DEVICE_NUMBER = 0x002D1080

MEM_COMMIT = 0x00001000
MEM_RESERVE = 0x00002000
MEM_RELEASE = 0x00008000
PAGE_READWRITE = 0x04

# 4096 rather than 512 so the same buffer is legal on a 4Kn card as on a 512e one.
SECTOR = 4096
CHUNK = 4 << 20


# Every one of these gets an explicit restype, and that is not decoration.
#
# ctypes defaults a function's return to `c_int`. On 64-bit Windows a HANDLE is 64 bits
# wide, so the default silently truncates every handle CreateFileW returns -- and the
# INVALID_HANDLE_VALUE check, which compares against the full-width 0xFFFF...FFFF, then
# never matches. The failure mode is a *failed open that reports success*, followed by
# writes into a handle that is not there. Same for VirtualAlloc's pointer.
_BOUND = {}


def _bind():
    """Load kernel32 once, with prototypes. Returns the DLL object."""
    if "k32" in _BOUND:
        return _BOUND["k32"]
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateFileW.restype = wintypes.HANDLE
    k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                              ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                              wintypes.HANDLE]
    k.DeviceIoControl.restype = wintypes.BOOL
    k.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p,
                                  wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                  ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    k.CloseHandle.restype = wintypes.BOOL
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    k.WriteFile.restype = wintypes.BOOL
    k.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                            ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    k.ReadFile.restype = wintypes.BOOL
    k.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                           ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    k.FlushFileBuffers.restype = wintypes.BOOL
    k.FlushFileBuffers.argtypes = [wintypes.HANDLE]
    k.GetLogicalDrives.restype = wintypes.DWORD
    k.GetLogicalDrives.argtypes = []
    k.VirtualAlloc.restype = ctypes.c_void_p
    k.VirtualAlloc.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD,
                               wintypes.DWORD]
    k.VirtualFree.restype = wintypes.BOOL
    k.VirtualFree.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD]
    k.WaitForSingleObject.restype = wintypes.DWORD
    k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k.GetExitCodeProcess.restype = wintypes.BOOL
    k.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    _BOUND["k32"] = k
    return k


def _k32():
    return _bind()


def _invalid_handle():
    return ctypes.c_void_p(-1).value


class _StorageDeviceNumber(ctypes.Structure):
    _fields_ = [("DeviceType", wintypes.DWORD),
                ("DeviceNumber", wintypes.DWORD),
                ("PartitionNumber", wintypes.DWORD)]


class _Aligned:
    r"""A page-aligned scratch buffer, because FILE_FLAG_NO_BUFFERING demands one.

    Unbuffered IO on Windows has three requirements, and only two of them are about
    sizes: the byte count must be a multiple of the sector size, the file offset must
    be too, **and the memory address must be sector-aligned as well**. Python's own
    buffers -- `bytes`, `bytearray`, `create_string_buffer` -- guarantee nothing about
    their address, so passing one straight to WriteFile fails with ERROR_INVALID_
    PARAMETER on some machines and works on others, which is the worst possible way for
    this to behave.

    VirtualAlloc hands back memory aligned to the allocation granularity, comfortably
    more than any sector size, so everything goes through here.

    The unbuffered handle is not negotiable on the read side in particular: the card is
    read back to catch counterfeit and failing cards, and a cached read would return the
    bytes we just wrote out of RAM and pass every time.
    """

    def __init__(self, size):
        self.size = size
        k = _k32()
        self.addr = k.VirtualAlloc(None, ctypes.c_size_t(size),
                                   MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE)
        if not self.addr:
            raise MemoryError("could not allocate an aligned %d-byte buffer" % size)

    def put(self, data):
        ctypes.memmove(self.addr, bytes(data), len(data))

    def take(self, count):
        return ctypes.string_at(self.addr, count)

    def free(self):
        if self.addr:
            _k32().VirtualFree(ctypes.c_void_p(self.addr), ctypes.c_size_t(0),
                               MEM_RELEASE)
            self.addr = None


def valid_device_id(dev):
    r"""\\.\PHYSICALDRIVEn and nothing else."""
    return bool(re.fullmatch(r"\\\\\.\\PHYSICALDRIVE\d+", (dev or "").upper()))


def _drive_number(dev):
    m = re.search(r"(\d+)$", dev or "")
    if not m:
        raise ValueError("not a physical drive identifier: %r" % dev)
    return int(m.group(1))


def block_device(dev):
    return dev


def raw_device(dev):
    return dev


def _open(path, access, share=FILE_SHARE_READ | FILE_SHARE_WRITE, flags=0):
    h = _k32().CreateFileW(path, access, share, None, OPEN_EXISTING, flags, None)
    if not h or h == _invalid_handle():
        raise OSError(0, ctypes.FormatError(ctypes.get_last_error()).strip(), path)
    return h


def _close(handle):
    if handle:
        _k32().CloseHandle(handle)


def _ioctl(handle, code, out_size=0):
    """DeviceIoControl with no input buffer, which is all of the ones used here."""
    buf = ctypes.create_string_buffer(out_size) if out_size else None
    returned = wintypes.DWORD()
    ok = _k32().DeviceIoControl(handle, code, None, 0, buf,
                                out_size, ctypes.byref(returned), None)
    if not ok:
        raise OSError(0, ctypes.FormatError(ctypes.get_last_error()).strip())
    return buf.raw[:returned.value] if buf else b""


def _volumes_on(number):
    r"""Every drive letter whose volume sits on physical disk `number`.

    Matched on the device number the storage stack reports, not on anything the user can
    see or that a label could collide with. On the platform with the most removable media
    plugged in at once, "which disk is this volume on" has to be an exact answer.
    """
    out = []
    mask = _k32().GetLogicalDrives()
    for i in range(26):
        if not (mask >> i) & 1:
            continue
        letter = "%s:" % chr(ord("A") + i)
        try:
            h = _open(r"\\.\%s" % letter, GENERIC_READ)
        except OSError:
            continue
        try:
            raw = _ioctl(h, IOCTL_STORAGE_GET_DEVICE_NUMBER,
                         ctypes.sizeof(_StorageDeviceNumber))
            sdn = _StorageDeviceNumber.from_buffer_copy(raw)
            if sdn.DeviceNumber == number:
                out.append((letter, sdn.PartitionNumber))
        except OSError:
            pass
        finally:
            _close(h)
    return out


class _LockedVolumes:
    """Locked and dismounted volumes, held open for as long as this object lives."""

    def __init__(self, number):
        self.handles = []
        self.failed = []
        for letter, _partno in _volumes_on(number):
            try:
                h = _open(r"\\.\%s" % letter, GENERIC_READ | GENERIC_WRITE)
            except OSError as e:
                self.failed.append((letter, str(e)))
                continue
            try:
                _ioctl(h, FSCTL_LOCK_VOLUME)
                _ioctl(h, FSCTL_DISMOUNT_VOLUME)
                self.handles.append(h)
            except OSError as e:
                _close(h)
                self.failed.append((letter, str(e)))

    def release(self):
        for h in self.handles:
            try:
                _ioctl(h, FSCTL_UNLOCK_VOLUME)
            except OSError:
                pass
            _close(h)
        self.handles = []


def unmount_disk(dev):
    """Lock and dismount every volume, and report which one refused if any did.

    The handles are deliberately released again here. This is the pre-flight check --
    "could this card be taken over at all" -- and it runs before the image is even
    opened. The write takes its own locks and keeps them; see `open_sink`.
    """
    try:
        number = _drive_number(dev)
    except ValueError as e:
        return False, str(e)
    locked = _LockedVolumes(number)
    failed = list(locked.failed)
    locked.release()
    if failed:
        return False, ("Windows would not release %s on this card.\n\n"
                       "Close any window, program or antivirus scan that is looking at "
                       "the card and try again.\n\n%s"
                       % (", ".join(l for l, _ in failed),
                          "\n".join("%s — %s" % (l, why) for l, why in failed)))
    return True, ""


class _WinSink:
    r"""A writable handle on \\.\PHYSICALDRIVEn that takes arbitrary-sized chunks.

    Three things the caller should not have to know about:

    * **Whole sectors only.** Unbuffered IO means every write must be a multiple of the
      sector size, so short chunks are held back and joined to the next one. The tail is
      padded with zeroes -- which land on the card past the end of the image, where
      nothing reads them -- and the caller's own byte count stays unpadded, so the
      verify hash still lines up.
    * **Aligned memory.** Every byte goes out through `_Aligned`; see why there.
    * **The partition boundary.** Without FSCTL_ALLOW_EXTENDED_DASD_IO a write to a disk
      handle is clipped at the first partition, so the image would land truncated and
      the card would not boot.
    """

    def __init__(self, dev):
        self.number = _drive_number(dev)
        self.buf = bytearray()
        self.err = ""
        self.handle = None
        self.scratch = None
        self.locked = _LockedVolumes(self.number)
        try:
            self.handle = _open(dev, GENERIC_READ | GENERIC_WRITE,
                                flags=FILE_FLAG_NO_BUFFERING | FILE_FLAG_WRITE_THROUGH)
            _ioctl(self.handle, FSCTL_ALLOW_EXTENDED_DASD_IO)
            self.scratch = _Aligned(CHUNK)
        except BaseException:
            self._teardown()
            raise

    def _teardown(self):
        if self.scratch is not None:
            self.scratch.free()
            self.scratch = None
        if self.handle is not None:
            _close(self.handle)
            self.handle = None
        self.locked.release()

    def _emit(self, data):
        """Write `data`, which is already a whole number of sectors."""
        k = _k32()
        off = 0
        while off < len(data):
            piece = data[off:off + CHUNK]
            self.scratch.put(piece)
            done = wintypes.DWORD()
            ok = k.WriteFile(self.handle, ctypes.c_void_p(self.scratch.addr),
                             len(piece), ctypes.byref(done), None)
            if not ok:
                raise OSError(0, ctypes.FormatError(ctypes.get_last_error()).strip())
            if done.value == 0:
                raise OSError(0, "the card accepted no data")
            # A short write on an unbuffered handle is still sector-aligned, so
            # continuing from here stays legal.
            off += done.value

    def write(self, chunk):
        self.buf += chunk
        if len(self.buf) >= CHUNK:
            n = (len(self.buf) // SECTOR) * SECTOR
            if n:
                self._emit(bytes(self.buf[:n]))
                del self.buf[:n]

    def close(self):
        """Returns (returncode, error). Pads the tail out to a whole sector first."""
        rc = 0
        try:
            if self.buf:
                pad = (-len(self.buf)) % SECTOR
                self._emit(bytes(self.buf) + b"\x00" * pad)
                self.buf = bytearray()
            _k32().FlushFileBuffers(self.handle)
            # Deliberately *not* IOCTL_DISK_UPDATE_PROPERTIES here. Telling Windows to
            # re-read the partition table makes it mount the volume it finds, and the
            # very next thing that happens is reading the card back to check it -- with
            # a live filesystem driver writing its own housekeeping onto the card
            # underneath the read. `writer.py` calls `rescan_partitions()` when it is
            # actually ready, which is after the verify.
        except OSError as e:
            self.err = str(e)
            rc = 1
        finally:
            # Only now: releasing the volume locks earlier hands the card back to
            # Windows mid-write.
            self._teardown()
        return rc, self.err


class _WinReader:
    """Sector-aligned, unbuffered reads, presented as an ordinary `read(n)`.

    Unbuffered on purpose. This is what the card is read back through to catch failing
    and counterfeit cards, and a cached read would hand back the bytes that were just
    written, out of RAM, and agree with itself every time.
    """

    def __init__(self, dev):
        # Lock and dismount first. Between the sink closing and this opening, Windows
        # may have mounted the filesystem it now finds on the card -- and a mounted FAT
        # volume writes to itself unprompted. Reading underneath one would compare the
        # image against the image *plus* whatever Windows just did, and fail a card that
        # is perfectly good.
        self.locked = _LockedVolumes(_drive_number(dev))
        self.handle = None
        self.scratch = None
        try:
            self.handle = _open(dev, GENERIC_READ, flags=FILE_FLAG_NO_BUFFERING)
            self.scratch = _Aligned(CHUNK)
        except BaseException:
            self.close()
            raise
        self.spare = b""

    def read(self, n):
        out = self.spare[:n]
        self.spare = self.spare[len(out):]
        k = _k32()
        while len(out) < n:
            want = min(CHUNK, ((n - len(out) + SECTOR - 1) // SECTOR) * SECTOR)
            got = wintypes.DWORD()
            ok = k.ReadFile(self.handle, ctypes.c_void_p(self.scratch.addr),
                            want, ctypes.byref(got), None)
            if not ok:
                raise OSError(0, ctypes.FormatError(ctypes.get_last_error()).strip())
            if got.value == 0:
                break
            data = self.scratch.take(got.value)
            take = min(n - len(out), len(data))
            out += data[:take]
            self.spare = data[take:]
        return out

    def close(self):
        if self.scratch is not None:
            self.scratch.free()
            self.scratch = None
        if self.handle is not None:
            _close(self.handle)
            self.handle = None
        self.locked.release()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def open_sink(dev, total=0):
    return _WinSink(dev)


def open_reader(dev):
    return _WinReader(dev)


def flush():
    pass          # the sink writes through and flushes on close; there is no `sync`


def rescan_partitions(dev):
    try:
        h = _open(dev, GENERIC_READ | GENERIC_WRITE)
    except OSError:
        return
    try:
        _ioctl(h, IOCTL_DISK_UPDATE_PROPERTIES)
    except OSError:
        pass
    finally:
        _close(h)


def eject(dev):
    """Best effort. Most card readers refuse this outright, and that is not a failure --
    the data is already flushed and the card is safe to pull."""
    try:
        h = _open(dev, GENERIC_READ)
    except OSError:
        return
    try:
        _ioctl(h, IOCTL_STORAGE_EJECT_MEDIA)
    except OSError:
        pass
    finally:
        _close(h)


def partition_devices(dev, partno):
    r"""There is no raw partition node to hand to debugfs on Windows.

    Returning nothing is the honest answer, and it is why `core.missing_tools` refuses
    an ext4-root image on this platform *before* anything is unmounted rather than
    letting the write finish and the provisioning fail. See D26.
    """
    return []


def mount_boot(dev, partno):
    """The drive letter Windows gives the FAT boot partition. (path, release, detail).

    Windows automounts it once the partition table is re-read, so the work here is
    waiting rather than mounting -- but the match is on physical device number *and*
    partition number, never on a label or a guess at which letter is new. The letter is
    returned as `X:\\` so os.path.join builds a real path from it.
    """
    number = _drive_number(dev)
    for attempt in range(40):
        for letter, part in _volumes_on(number):
            if part == partno:
                return letter + "\\", (lambda: None), ""
        if attempt == 4:
            rescan_partitions(dev)
        time.sleep(1.5)
    return None, (lambda: None), (
        "Windows never gave the card's boot partition a drive letter. Unplug and "
        "replug the card, then use Apply settings only.")


def explain_write_error(err, xerr, rc, dev):
    blob = (str(err) + " " + str(xerr)).lower()
    if "access is denied" in blob or "denied" in blob:
        return ("Windows denied access to %s.\n\n"
                "Writing a card needs Administrator rights, and the elevation prompt "
                "either did not appear or was dismissed. Antivirus software that "
                "guards removable drives also produces this.\n\n%s"
                % (dev, err or "(no detail)"))
    if "in use" in blob or "being used" in blob or "sharing violation" in blob:
        return ("%s is still in use.\n\nClose any Explorer window showing the card, "
                "stop anything scanning it, then try again.\n\n%s" % (dev, err or ""))
    if "device is not ready" in blob or "no media" in blob or "cannot find" in blob:
        return ("%s is not there any more.\n\nThe card was removed or the reader "
                "dropped it.\n\n%s" % (dev, err or ""))
    if "not enough space" in blob or "disk is full" in blob:
        return ("The card is too small for this image.\n\nNothing usable was written."
                "\n\n%s" % (err or ""))
    return (err or xerr or "the writer exited %s having written nothing." % rc)


# ───────────────────────────────── Elevation ─────────────────────────────────

CAN_WRITE = True

SEE_MASK_NOCLOSEPROCESS = 0x00000040
SEE_MASK_NOASYNC = 0x00000100
SW_HIDE = 0
ERROR_CANCELLED = 1223
INFINITE = 0xFFFFFFFF


def _shellexecuteinfo():
    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD),
                    ("fMask", ctypes.c_ulong),
                    ("hwnd", wintypes.HANDLE),
                    ("lpVerb", ctypes.c_wchar_p),
                    ("lpFile", ctypes.c_wchar_p),
                    ("lpParameters", ctypes.c_wchar_p),
                    ("lpDirectory", ctypes.c_wchar_p),
                    ("nShow", ctypes.c_int),
                    ("hInstApp", wintypes.HINSTANCE),
                    ("lpIDList", ctypes.c_void_p),
                    ("lpClass", ctypes.c_wchar_p),
                    ("hkeyClass", wintypes.HKEY),
                    ("dwHotKey", wintypes.DWORD),
                    ("hIcon", wintypes.HANDLE),
                    ("hProcess", wintypes.HANDLE)]
    return SHELLEXECUTEINFOW


def cmdline(args):
    r"""A list of arguments as the single command line Windows actually takes.

    `subprocess.list2cmdline` is exactly this and is already in the standard library:
    it implements the `CommandLineToArgvW` rules, which are not the obvious ones -- a
    backslash is literal *except* in a run immediately before a quote, where the run
    doubles. Getting that wrong mangles `C:\Program Files` and every user profile
    belonging to somebody with two names, so it is not a place to have opinions.

    Wrapped rather than called inline only so the reason is written down once, here,
    instead of being rediscovered the next time this looks like a missing feature.
    """
    return subprocess.list2cmdline([str(a) for a in args])


def elevate(argv, rundir, progress_path=""):
    r"""UAC, through ShellExecuteExW with the `runas` verb. (rc, stderr, cancelled).

    There is no `sudo` here and no askpass: UAC is a secure-desktop prompt that this
    process cannot see, drive, or read a password from. That is a better bargain than
    the other two platforms get -- nothing sensitive passes through Riparr at all -- but
    it costs the one thing a pipe would have given us, which is the child's output.
    Hence `SEE_MASK_NOCLOSEPROCESS`: it hands back a process handle, so the exit code
    can still be waited for. Everything the user needs to read comes back through the
    progress file the elevated writer publishes, which is exactly why that file exists.

    A dismissed or refused prompt is ERROR_CANCELLED, and is reported as a cancellation
    rather than a failure -- the card has not been touched at that point.
    """
    argv = [str(a) for a in argv]
    exe, params = argv[0], argv[1:]

    SHELLEXECUTEINFOW = _shellexecuteinfo()
    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(SHELLEXECUTEINFOW)
    info.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC
    info.lpVerb = "runas"
    info.lpFile = exe
    info.lpParameters = cmdline(params)
    info.nShow = SW_HIDE

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        code = ctypes.get_last_error()
        if code == ERROR_CANCELLED:
            return 1, "", True
        return 1, ("Windows would not start the card writer: %s"
                   % ctypes.FormatError(code).strip()), False

    k = _k32()
    k.WaitForSingleObject(info.hProcess, INFINITE)
    rc = wintypes.DWORD()
    k.GetExitCodeProcess(info.hProcess, ctypes.byref(rc))
    _close(info.hProcess)
    return int(rc.value), "", False


def probe_writable(dev):
    """Can this device be opened for writing right now? (ok, detail).

    Opened without FILE_FLAG_NO_BUFFERING: this only asks whether Windows will hand over
    a writable handle at all, and the alignment rules are the sink's problem.
    """
    try:
        h = _open(dev, GENERIC_READ | GENERIC_WRITE)
    except OSError as e:
        return False, explain_write_error(str(e), "", 0, dev)
    _close(h)
    return True, ""


# ───────────────────────────── Updating itself ─────────────────────────────

UPDATE_SUFFIX = ".exe"


def update_target(executable):
    r"""The running .exe itself -- the Windows build is --onefile.

    `sys.executable` is python.exe when running from source, and the only honest answer
    then is None: replacing a system Python with a Riparr build would be a remarkable
    thing to do to somebody.
    """
    exe = os.path.abspath(executable)
    if os.path.basename(exe).lower() in ("python.exe", "pythonw.exe"):
        return None
    return exe


def swap_and_relaunch(archive, target, pid, rundir):
    r"""Wait for this process to exit, replace the .exe, start it again.

    Windows holds an executable open for as long as it is running and will not let it be
    replaced, which is the whole reason this is a separate process that waits. PowerShell
    rather than a .bat: quoting a path with spaces in cmd is its own small tragedy, and
    `C:\Users\Firstname Lastname\Downloads` is the normal case here.
    """
    parent = os.path.dirname(target) or "."
    if not os.access(parent, os.W_OK):
        return False, ("Riparr Preparer cannot update itself because %s is not "
                       "writable by you.\n\nMove it somewhere you own, or download the "
                       "new version yourself." % parent)

    script = os.path.join(rundir, "update.ps1")
    ps = r'''
$ErrorActionPreference = "Stop"
$pidToWait = %(pid)d
$src    = %(src)s
$target = %(target)s

# Wait for the app to exit. Get-Process throws when the process is gone, which is the
# signal we are waiting for rather than an error.
for ($i = 0; $i -lt 200; $i++) {
  if (-not (Get-Process -Id $pidToWait -ErrorAction SilentlyContinue)) { break }
  Start-Sleep -Milliseconds 300
}

# Even after the process is gone the file can stay locked for a moment. Retry rather
# than failing the update on a race that resolves itself.
$backup = "$target.old"
Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue
for ($i = 0; $i -lt 40; $i++) {
  try {
    Move-Item -LiteralPath $target -Destination $backup -Force
    Move-Item -LiteralPath $src -Destination $target -Force
    break
  } catch { Start-Sleep -Milliseconds 250 }
}

if (-not (Test-Path -LiteralPath $target)) {
  # The swap did not complete; put the old one back so the user still has an app.
  if (Test-Path -LiteralPath $backup) { Move-Item -LiteralPath $backup -Destination $target -Force }
}
Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue
Start-Process -FilePath $target
''' % {"pid": pid, "src": _ps_quote(archive), "target": _ps_quote(target)}
    with open(script, "w", encoding="utf-8") as f:
        f.write(ps)

    subprocess.Popen(
        ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
         "-ExecutionPolicy", "Bypass", "-File", script],
        creationflags=_NOWINDOW | 0x00000008,      # DETACHED_PROCESS
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return True, ""


def _ps_quote(s):
    """A PowerShell single-quoted string; the only escape inside one is a doubled quote."""
    return "'" + str(s).replace("'", "''") + "'"
