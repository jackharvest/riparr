r"""Write over a card that has been used before -- issue #7.

Run by CI on the Windows runner, as administrator, on an attached VHD that diskpart has
partitioned the way used cards are: one volume with a drive letter, and one mounted
volume without one. Windows guards a mounted volume's sectors whether or not it has a
letter. The writer found volumes by letter only, so the second one was never dismounted
and the write was refused with "Access is denied" -- until somebody wiped the card by
hand, which is the workaround both people in the issue found.

    python test-used-card.py <disk-number>
"""
import hashlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "tools", "preparer"))

import hostos   # noqa: E402

dev = r"\\.\PHYSICALDRIVE%d" % int(sys.argv[1])
ok, why = hostos.unmount_disk(dev)
print("unmount_disk:", ok, why)
if not ok:
    sys.exit(1)

total, h = 200 << 20, hashlib.sha256()
sink = hostos.open_sink(dev, total)
done = 0
try:
    while done < total:
        chunk = os.urandom(3 << 20)[:total - done]
        # No boot signature: what lands must not look like a partition table.
        if done == 0:
            chunk = b"\0" * 1024 + chunk[1024:]
        try:
            sink.write(chunk)
        except OSError as e:
            sys.exit("refused after %d MB: %s" % (done >> 20, e))
        h.update(chunk)
        done += len(chunk)
finally:
    rc, err = sink.close()
if rc:
    sys.exit("closing: %s" % err)

got, left = hashlib.sha256(), total
with hostos.open_reader(dev) as r:
    while left:
        chunk = r.read(min(4 << 20, left))
        if not chunk:
            break
        got.update(chunk)
        left -= len(chunk)
if left or got.hexdigest() != h.hexdigest():
    sys.exit("what was read back is not what was written (%d left)" % left)
print("ok  wrote %d MB over a used card and read it back" % (total >> 20))
