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
