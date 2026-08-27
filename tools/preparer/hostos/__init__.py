"""Facts about the machine the Preparer is running on.

`core.py` decides; this package gathers. The split exists because the *rules* -- which
device is a card (D24), what a card size buys, how a PSK is derived -- are the same on
every operating system, while the way you find out what is plugged in is not.

Each backend answers the same three questions and returns the same shapes:

    list_block_devices()        -> [dict, ...]   normalised, unclassified
    scan_wifi()                 -> ([dict, ...], method)
    saved_network_password(ssid)-> (password_or_None, error_or_None)

`list_block_devices()` returns raw facts only. It must never decide whether something is
a card -- that is `core.classify_disk`, and keeping it in one place is what stops the
three platforms drifting into three different answers to the same question. The keys:

    id                 device identifier: "disk4", "sdb", "\\\\.\\PHYSICALDRIVE2"
    size               bytes
    name               model or media name, as the OS reports it
    protocol           bus: "USB", "Secure Digital", "SATA", ...
    removable_media    the SCSI removable-medium bit -- the *medium* can leave the
                       device. This is what a card reader is, and the single most
                       important field here
    ejectable          the whole device can be detached. Every USB disk claims it
    internal           built into the machine
    virtual            not real hardware
    icon               optional platform hint; macOS gives "SD.icns" for a card slot

`scan_wifi()` and `saved_network_password()` answer in the same shapes, documented at
their definitions.

────────────────────────────── The write side ──────────────────────────────

The same bargain applies to writing a card: `writer.py` owns the *sequence* -- verify
the image, unmount, write, verify the card, provision, eject -- and each backend owns
the operations that sequence is made of. Every one of them is required, and
`unsupported.py` implements them by refusing rather than by doing nothing.

    CAN_WRITE                       whether this platform can write a card at all
    valid_device_id(dev)            the last check before a raw write. A whitelist
    block_device(dev)               the node the OS names the whole disk by
    raw_device(dev)                 what gets written to; the same thing except on macOS
    partition_devices(dev, n)       candidate nodes for partition n, best first
    unmount_disk(dev)               -> (ok, detail). Releases every volume, and *verifies*
    probe_writable(dev)             -> (ok, detail). Asked before anything is opened
    open_sink(dev, total)           -> object with .write(bytes) and .close() -> (rc, err)
    open_reader(dev)                -> file-like, for reading the card back
    flush()                         settle writes to the medium
    rescan_partitions(dev)          make the new partition nodes appear after a write
    mount_boot(dev, n)              -> (path, release, detail) for the FAT boot partition
    eject(dev)                      best effort; a refused eject is not a failed write
    explain_write_error(...)        -> a sentence naming the cause and the fix
    elevate(argv, rundir)           -> (rc, stderr, cancelled). Runs argv as root
    UPDATE_SUFFIX                   the release asset this platform installs
    update_target(executable)       what to replace, or None if not a built app
    swap_and_relaunch(...)          -> (ok, detail). Replaces it after we exit

`open_sink` is where the three genuinely part company. macOS and Linux both hand the
bytes to `dd`; Windows has no dd and no raw device, and writes through a locked handle
of its own. The interface is small enough that `writer.py` never learns which.

Adding a platform means adding a module here and nothing else.
"""
import sys

if sys.platform == "darwin":
    from . import darwin as _impl
elif sys.platform.startswith("linux"):
    from . import linux as _impl
elif sys.platform in ("win32", "cygwin"):
    from . import windows as _impl
else:                                       # pragma: no cover - no such machine yet
    from . import unsupported as _impl

NAME = _impl.NAME

list_block_devices = _impl.list_block_devices
scan_wifi = _impl.scan_wifi
saved_network_password = _impl.saved_network_password

open_url = _impl.open_url
reveal = _impl.reveal
keep_awake_command = _impl.keep_awake_command

CAN_WRITE = _impl.CAN_WRITE

valid_device_id = _impl.valid_device_id
block_device = _impl.block_device
raw_device = _impl.raw_device
partition_devices = _impl.partition_devices
unmount_disk = _impl.unmount_disk
probe_writable = _impl.probe_writable
open_sink = _impl.open_sink
open_reader = _impl.open_reader
flush = _impl.flush
rescan_partitions = _impl.rescan_partitions
mount_boot = _impl.mount_boot
eject = _impl.eject
explain_write_error = _impl.explain_write_error
elevate = _impl.elevate

UPDATE_SUFFIX = _impl.UPDATE_SUFFIX
update_target = _impl.update_target
swap_and_relaunch = _impl.swap_and_relaunch
