"""Write a card on Windows, end to end, onto a real disk -- an attached VHD.

Run by the release workflow on the Windows runner, as administrator, after
test-image-config.py has left its fixtures behind. A VHD attached with diskpart is a
\\\\.\\PHYSICALDRIVEn like any card reader, so this runs the writer exactly as the app
does: lock and dismount, the raw unbuffered write, the read-back hash, and the image
configured before any of it. Nothing short of this exercises hostos/windows.py's write
path, and nothing did -- which is how a writer that could not open a disk shipped.

    python test-write-disk.py <fixtures.json> <disk-number>
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PREP = os.path.join(HERE, "..", "..", "tools", "preparer")
sys.path.insert(0, PREP)

import armbian  # noqa: E402
import hostos   # noqa: E402

fx = json.load(open(sys.argv[1], encoding="utf-8"))
dev = r"\\.\PHYSICALDRIVE%d" % int(sys.argv[2])
progress = os.path.join(tempfile.mkdtemp(prefix="riparr disk "), "progress.json")

rc = subprocess.run([sys.executable, os.path.join(PREP, "writer.py"),
                     "--image", fx["image"], "--dev", dev, "--toml", fx["toml"],
                     "--conf", fx["conf"], "--makemkv", fx["makemkv"],
                     "--progress", progress, "--total", str(fx["size"]),
                     "--verify"]).returncode
st = json.load(open(progress, encoding="utf-8"))
print("writer exited", rc, "->", st)
if rc != 0 or st.get("phase") != "done":
    sys.exit("the Windows writer did not finish: %s" % st)

# Read what landed back off the disk and look inside it, rather than trusting the
# writer's own verdict on itself.
copy = os.path.join(os.path.dirname(progress), "readback.img")
with hostos.open_reader(dev) as r, open(copy, "wb") as out:
    left = fx["size"]
    while left > 0:
        chunk = r.read(min(4 << 20, left))
        if not chunk:
            break
        out.write(chunk)
        left -= len(chunk)
with open(copy, "rb") as f:
    mbr = f.read(512)
offset = int.from_bytes(mbr[0x1BE + 8:0x1BE + 12], "little") * 512
t = armbian.image_target(copy, offset)
dfs = armbian.find_debugfs()
host = armbian.debugfs_run([dfs, "-R", "cat /etc/hostname", t], stdout_only=True)
wpa = armbian.debugfs_run([dfs, "-R", "cat /etc/wpa_supplicant/wpa_supplicant-wlan0.conf",
                           t], stdout_only=True)
if host != "riparr\n" or 'ssid="Caf\u00e9 Net"' not in wpa:
    sys.exit("the disk does not hold the settings: hostname=%r" % host)
print("ok  wrote a configured card to %s, read it back, settings present" % dev)
