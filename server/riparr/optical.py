"""
What the drive is, and what is in it -- asked of the hardware directly.

`optical_drives()` in `platform.py` used to report `present: False` and `media: None`
unconditionally on real hardware, because nothing ever filled them in. Only the mock
branch said a disc was loaded. Every consumer keys off `present` -- the disc watcher,
`enqueue()`, the re-rip endpoint, the "Rip this disc" button -- so on the actual
appliance there was no path to start a rip at all. This module is what makes those
fields true.

## Three questions, three different answers

**Is there a disc?** One ioctl. `CDROM_DRIVE_STATUS` is answered by the kernel's own
cdrom layer from state it already tracks, costs nothing, and does not spin the disc --
which matters because the watcher asks it every three seconds forever.

**What kind of disc, and what can this drive read?** SCSI MMC `GET CONFIGURATION`
(46h), sent through `SG_IO`. The drive reports a *current profile* (what is loaded)
and a *profile list* (everything it can do). This is the same call `sg_get_config`
makes; it is inlined here rather than taken as a dependency because sg3-utils is not
on the image and one 10-byte CDB is not worth an apt package.

**Is this a 4K UHD drive?** Not answerable here, and pretending otherwise is the
expensive mistake this whole module exists to prevent. **There is no MMC profile for
UHD Blu-ray.** A UHD disc reports `BD-ROM` (0x40) exactly like a 1080p Blu-ray, and a
drive that will never decrypt one advertises `BD-ROM` in its profile list exactly like
one that will. The difference is AACS 2.0 and drive firmware, and the only software
that knows is MakeMKV. See `drives.py`.

## Everything here fails soft

A drive that does not answer a SCSI command, a kernel without the cdrom module, a
device node that vanished when someone pulled USB mid-poll -- all of them return "I do
not know" rather than raising. The rest of the app degrades to what it did before this
module existed, which was still a running appliance.
"""
import ctypes
import fcntl
import os
import subprocess


# ─────────────────────────────── is there a disc ───────────────────────────────

CDROM_DRIVE_STATUS = 0x5326

# linux/cdrom.h. NO_INFO is what a drive returns when it has not been asked to find
# out yet; it is not the same as an empty tray and must not be reported as one.
CDS_NO_INFO = 0
CDS_NO_DISC = 1
CDS_TRAY_OPEN = 2
CDS_DRIVE_NOT_READY = 3
CDS_DISC_OK = 4

TRAY_STATE = {
    CDS_NO_INFO: "unknown",
    CDS_NO_DISC: "empty",
    CDS_TRAY_OPEN: "open",
    CDS_DRIVE_NOT_READY: "busy",
    CDS_DISC_OK: "loaded",
}


def tray_status(device):
    """`(present, state)` -- state being one of TRAY_STATE's values.

    Opened O_NONBLOCK deliberately: opening an optical device without it blocks until
    the disc is ready, which on a drive that is still spinning up is several seconds
    of a stalled poll loop.
    """
    fd = _open(device)
    if fd is None:
        return False, "unknown"
    try:
        code = fcntl.ioctl(fd, CDROM_DRIVE_STATUS, 0)
    except OSError:
        return False, "unknown"
    finally:
        os.close(fd)
    return code == CDS_DISC_OK, TRAY_STATE.get(code, "unknown")


def _open(device):
    try:
        return os.open(device, os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        return None


# ─────────────────────────────── SG_IO ───────────────────────────────

SG_IO = 0x2285
SG_DXFER_FROM_DEV = -3


class _SgIoHdr(ctypes.Structure):
    """`struct sg_io_hdr_t` from scsi/sg.h.

    Laid out with ctypes rather than `struct` so the padding the ABI inserts before
    each pointer is the compiler's problem and not ours -- the same source builds
    right on the 64-bit board and on whatever anyone develops on.
    """
    _fields_ = [
        ("interface_id", ctypes.c_int),
        ("dxfer_direction", ctypes.c_int),
        ("cmd_len", ctypes.c_ubyte),
        ("mx_sb_len", ctypes.c_ubyte),
        ("iovec_count", ctypes.c_ushort),
        ("dxfer_len", ctypes.c_uint),
        ("dxferp", ctypes.c_void_p),
        ("cmdp", ctypes.c_void_p),
        ("sbp", ctypes.c_void_p),
        ("timeout", ctypes.c_uint),
        ("flags", ctypes.c_uint),
        ("pack_id", ctypes.c_int),
        ("usr_ptr", ctypes.c_void_p),
        ("status", ctypes.c_ubyte),
        ("masked_status", ctypes.c_ubyte),
        ("msg_status", ctypes.c_ubyte),
        ("sb_len_wr", ctypes.c_ubyte),
        ("host_status", ctypes.c_ushort),
        ("driver_status", ctypes.c_ushort),
        ("resid", ctypes.c_int),
        ("duration", ctypes.c_uint),
        ("info", ctypes.c_uint),
    ]


def _scsi_in(device, cdb, length, timeout_ms=8000):
    """Send one data-in SCSI command. Returns the bytes read, or None.

    None means "the question could not be asked" for any reason -- no device, no
    permission, a drive that refused the command. The caller's job is to carry on
    without the answer, never to distinguish which.
    """
    fd = _open(device)
    if fd is None:
        return None
    try:
        buf = ctypes.create_string_buffer(length)
        cmd = ctypes.create_string_buffer(bytes(cdb), len(cdb))
        sense = ctypes.create_string_buffer(32)
        hdr = _SgIoHdr(
            interface_id=ord("S"),
            dxfer_direction=SG_DXFER_FROM_DEV,
            cmd_len=len(cdb),
            mx_sb_len=ctypes.sizeof(sense),
            dxfer_len=length,
            dxferp=ctypes.cast(buf, ctypes.c_void_p),
            cmdp=ctypes.cast(cmd, ctypes.c_void_p),
            sbp=ctypes.cast(sense, ctypes.c_void_p),
            timeout=timeout_ms,
        )
        fcntl.ioctl(fd, SG_IO, hdr)
        # A non-zero SCSI status means the drive understood the question and declined
        # to answer it. The buffer is then undefined, not empty, so it must not be read.
        if hdr.status != 0 or hdr.host_status != 0:
            return None
        got = length - max(0, hdr.resid)
        return buf.raw[:got]
    except OSError:
        return None
    finally:
        os.close(fd)


# ─────────────────────────── GET CONFIGURATION (46h) ───────────────────────────

# MMC-6 Table 244. Only the ones a video disc can actually be are named; anything
# else is reported by number so an unknown drive is still described honestly.
PROFILES = {
    0x0008: "CD-ROM", 0x0009: "CD-R", 0x000A: "CD-RW",
    0x0010: "DVD-ROM", 0x0011: "DVD-R", 0x0012: "DVD-RAM",
    0x0013: "DVD-RW", 0x0014: "DVD-RW", 0x0015: "DVD-R DL", 0x0016: "DVD-R DL",
    0x0017: "DVD-RW DL", 0x001A: "DVD+RW", 0x001B: "DVD+R",
    0x002A: "DVD+RW DL", 0x002B: "DVD+R DL",
    0x0040: "BD-ROM", 0x0041: "BD-R", 0x0042: "BD-R", 0x0043: "BD-RE",
    0x0050: "HD DVD-ROM", 0x0051: "HD DVD-R", 0x0052: "HD DVD-RAM",
}

# Which of Riparr's three disc families a profile belongs to. `kind` is what the rest
# of the app reasons about; the profile name is what it shows the user.
_CD = tuple(range(0x0008, 0x000B))
_DVD = tuple(range(0x0010, 0x0030))
_BD = tuple(range(0x0040, 0x0044))


def profile_kind(profile):
    if profile in _CD:
        return "cd"
    if profile in _DVD:
        return "dvd"
    if profile in _BD:
        return "bluray"
    return None


def get_configuration(device):
    """`(current_profile, [supported profiles])`, or `(None, [])`.

    Asked in one call with RT=0, which returns the header (carrying the current
    profile) followed by every feature descriptor, of which Feature 0000h -- the
    Profile List -- is always first and is the only one read here.

    The allocation length is fixed at 512 bytes rather than the usual ask-then-reask
    dance. Feature 0000h is first in the response by specification, so a truncated
    reply still contains all of it; a second SCSI command to collect features nobody
    reads would be round trips spent on nothing.
    """
    data = _scsi_in(device, [0x46, 0x00, 0, 0, 0, 0, 0, 0x02, 0x00, 0], 512)
    if not data or len(data) < 8:
        return None, []

    current = int.from_bytes(data[6:8], "big")
    current = current or None                 # 0x0000 is "no current profile"

    profiles = []
    body = data[8:]
    # Feature descriptor: code(2) version/persistent/current(1) additional length(1),
    # then `additional length` bytes of payload.
    while len(body) >= 4:
        code = int.from_bytes(body[0:2], "big")
        extra = body[3]
        payload = body[4:4 + extra]
        if code == 0x0000:                    # Profile List
            for i in range(0, len(payload) - 3, 4):
                profiles.append(int.from_bytes(payload[i:i + 2], "big"))
            break
        body = body[4 + extra:]

    return current, profiles


def capabilities(device):
    """What families of disc this drive can read, from its own profile list.

    Reading is all Riparr ever does, so a drive's writer profiles are collapsed into
    the family they imply: a BD-RE burner reads BD-ROM, and saying "BD-RE" to somebody
    asking "can it do Blu-rays" is an interface describing its own data model.
    """
    _, profiles = get_configuration(device)
    kinds = {profile_kind(p) for p in profiles}
    return {"cd": "cd" in kinds, "dvd": "dvd" in kinds, "bluray": "bluray" in kinds,
            "profiles": profiles}


# ─────────────────────────────── the disc itself ───────────────────────────────

def disc_size_bytes(device):
    """How big the loaded disc is, from the block layer's own count.

    `/sys/block/sr0/size` is in 512-byte sectors regardless of the medium's real 2048,
    which is the kernel's convention everywhere and is easy to get wrong by a factor
    of four.
    """
    name = os.path.basename(device)
    try:
        with open("/sys/block/%s/size" % name) as f:
            return int(f.read().strip()) * 512
    except (OSError, ValueError):
        return 0


def volume_label(device):
    """The disc's volume name.

    `blkid` first, because it is the only thing here that understands **UDF**, and
    Blu-ray is UDF 2.50 with no ISO 9660 bridge to fall back on -- a hand-rolled PVD
    read gets a label for every DVD and nothing at all for the discs the product is
    named after. util-linux is on the image; reimplementing UDF's descriptor sequence
    to avoid calling it would be a lot of parsing to arrive somewhere worse.

    The PVD read stays as the fallback for a drive `blkid` cannot open, and because it
    needs no subprocess at all.
    """
    label = _blkid_label(device)
    if label:
        return label
    return _iso9660_label(device)


def _blkid_label(device):
    try:
        p = subprocess.run(["blkid", "-o", "value", "-s", "LABEL", device],
                           capture_output=True, text=True, timeout=10)
    except Exception:
        return ""
    return (p.stdout or "").strip()


def _iso9660_label(device):
    """Volume identifier out of the ISO 9660 Primary Volume Descriptor.

    Sector 16, 2048 bytes in: type byte 1, the string `CD001`, and the 32-byte
    space-padded volume identifier at offset 40. No mount, no filesystem driver.
    """
    fd = _open(device)
    if fd is None:
        return ""
    try:
        os.lseek(fd, 16 * 2048, os.SEEK_SET)
        pvd = os.read(fd, 2048)
    except OSError:
        return ""
    finally:
        os.close(fd)
    if len(pvd) < 72 or pvd[0] != 1 or pvd[1:6] != b"CD001":
        return ""
    return pvd[40:72].decode("ascii", "ignore").strip().strip("\x00").strip()
