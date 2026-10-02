"""Replace a status file while another thread keeps reading it, as the GUI does.

On Windows os.replace onto an open file raises PermissionError. One of those, raised
inside the setup thread, ended an install halfway through and left the window saying
"Installing Riparr" for ever. statusfile.publish must ride that out.
"""
import os
import sys
import tempfile
import threading

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "tools", "preparer"))
import statusfile  # noqa: E402

path = os.path.join(tempfile.mkdtemp(prefix="riparr status "), "progress.json")
statusfile.publish(path, phase="running", n=0)
stop = threading.Event()
reads = [0]


def poll():
    while not stop.is_set():
        try:
            with open(path, encoding="utf-8") as f:
                f.read()
                reads[0] += 1
        except OSError:
            pass


t = threading.Thread(target=poll, daemon=True)
t.start()
for i in range(3000):
    statusfile.publish(path, phase="running", n=i)       # must never raise
assert statusfile.publish(path, phase="done", n=-1), "a final status was dropped"
stop.set()
t.join()
print("ok  3000 replaces under %d concurrent reads; final status landed" % reads[0])
