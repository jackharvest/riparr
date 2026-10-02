"""The status files the Preparer's processes talk through, written one way.

The GUI polls a JSON file several times a second; the card writer, the download and the
box setup each replace it as they go. Three copies of the same four lines did that, and
on Windows each was a crash waiting to happen: `os.replace` onto a file another process
has open fails with PermissionError there, where macOS and Linux simply swap the inode.

It was found by losing a whole setup. The poll and a replace collided mid-install, the
PermissionError killed the setup thread, the thread's cleanup closed the ssh pipe, and
the box hung up on install.sh halfway through -- while the window went on saying
"Installing Riparr" with nothing left behind it to say otherwise.

So: one function, a retry for the sharing violation, and a status write that can never
take down the work it is reporting on. A progress snapshot that cannot be written is
dropped -- the next one replaces it a moment later. A final one (done, error, cancelled)
is retried for much longer, because nothing comes after it.
"""
import json
import os
import time

FINAL = ("done", "error", "cancelled")


def publish(status_file, **kw):
    """Atomically replace `status_file` with `kw` as JSON. Never raises for a busy file."""
    if not status_file:
        return False
    tmp = status_file + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(kw, f)
    tries = 400 if kw.get("phase") in FINAL else 40         # ~10 s, or ~1 s
    for _ in range(tries):
        try:
            os.replace(tmp, status_file)
            return True
        except PermissionError:
            time.sleep(0.025)
    return False
