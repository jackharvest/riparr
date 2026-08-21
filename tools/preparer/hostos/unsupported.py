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
