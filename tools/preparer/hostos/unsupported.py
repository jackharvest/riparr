"""The honest backend for an operating system nobody has ported yet.

Empty answers rather than exceptions: the Preparer should open, say it cannot see any
cards, and let someone read the screen -- not fail at import with a traceback.
"""
import sys

NAME = sys.platform


def list_block_devices():
    return []


def scan_wifi():
    return [], "none"


def saved_network_password(ssid):
    return None, "Riparr cannot read saved Wi-Fi passwords on %s." % sys.platform


def open_url(url):
    return None


def reveal(path):
    return None


def keep_awake_command(pid):
    return None


# ────────────────────────────── The card write ──────────────────────────────
#
# Refuse, rather than half-answering. `core.host_capabilities` reads CAN_WRITE and greys
# the card route out on the first screen, so nothing below should ever be reached -- but
# if it is, it stops before the card is touched instead of after.

CAN_WRITE = False


def valid_device_id(dev):
    return False


def block_device(dev):
    return dev


def raw_device(dev):
    return dev


def partition_devices(dev, partno):
    return []


def _refuse():
    raise RuntimeError("Riparr cannot write an SD card on %s." % sys.platform)


def unmount_disk(dev):
    return False, "Riparr cannot write an SD card on %s." % sys.platform


def open_sink(dev, total=0):
    _refuse()


def open_reader(dev):
    _refuse()


def flush():
    return None


def rescan_partitions(dev):
    return None


def eject(dev):
    return None


def mount_boot(dev, partno):
    return None, (lambda: None), "Riparr cannot write an SD card on %s." % sys.platform


def explain_write_error(err, xerr, rc, dev):
    return err or xerr or "the writer exited %s." % rc


def elevate(argv, rundir, progress_path=""):
    return 1, "Riparr cannot write an SD card on %s." % sys.platform, False


def probe_writable(dev):
    return False, "Riparr cannot write an SD card on %s." % sys.platform
