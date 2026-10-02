"""Configure a small ext4 image the way the Windows Preparer configures a card.

Run by the release workflow on the Windows runner, with Windows Python and the debugfs
that was just built -- the combination the app ships. It exercises the parts that only
go wrong on Windows: CRLF creeping into config files or into the debugfs script, paths
with backslashes and spaces, and Cygwin's handling of `image?offset=N`.

    python test-image-config.py <mke2fs.exe> [<fixtures.json>]
"""
import lzma
import os
import subprocess
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "tools", "preparer"))

import armbian  # noqa: E402
import writer   # noqa: E402

MKE2FS = sys.argv[1]
OFFSET = 4 << 20
SIZE = 96 << 20

# A space in the directory is the point: real Windows user names have them.
work = tempfile.mkdtemp(prefix="riparr test ")
img = os.path.join(work, "card.img")
with open(img, "wb") as f:
    f.truncate(SIZE)

# The same feature set as the Armbian image, so a debugfs that cannot handle one of them
# fails here rather than on somebody's card.
env = dict(os.environ, CYGWIN="nodosfilewarning")
subprocess.run([MKE2FS, "-q", "-F", "-t", "ext4", "-L", "armbi_root",
                "-O", "metadata_csum,metadata_csum_seed,64bit,extent,flex_bg,huge_file",
                "-E", "offset=%d" % OFFSET, img.replace("\\", "/"),
                "%dk" % ((SIZE - OFFSET) // 1024)], check=True, env=env)

# One Linux partition in the MBR, as Armbian's images have.
with open(img, "r+b") as f:
    entry = bytes([0, 0, 0, 0, 0x83, 0, 0, 0]) + (OFFSET // 512).to_bytes(4, "little") \
        + ((SIZE - OFFSET) // 512).to_bytes(4, "little")
    f.seek(0x1BE)
    f.write(entry)
    f.seek(510)
    f.write(b"\x55\xaa")

dfs = armbian.find_debugfs()
assert dfs, "no bundled debugfs found next to armbian.py"
target = armbian.image_target(img, OFFSET)
# What debugfs itself sees, so a failure here explains itself in the log.
print("debugfs:", dfs)
print("target: ", target)
stats = armbian.debugfs_run([dfs, "-R", "stats", target])
print("\n".join(stats.splitlines()[:8]))
if "Filesystem features" not in stats:
    sys.exit("debugfs could not open the test image")
# The directories a real root filesystem already has.
print(armbian.debugfs_script(dfs, target, "".join(
    "mkdir %s\n" % d for d in (
        "/etc", "/etc/wpa_supplicant", "/etc/systemd", "/etc/systemd/network",
        "/etc/systemd/system", "/etc/systemd/system/multi-user.target.wants",
        "/etc/default", "/root", "/boot", "/lib", "/lib/systemd", "/lib/systemd/system"))))

xz = img + ".xz"
with open(img, "rb") as s, lzma.open(xz, "wb", preset=0) as d:
    d.write(s.read())

toml = os.path.join(work, "custom.toml")
with open(toml, "w", encoding="utf-8", newline="\n") as f:
    f.write('[system]\nhostname = "riparr"\n'
            '[user]\nauthorized_keys = [ "ssh-ed25519 AAAATEST riparr" ]\n'
            '[wlan]\nssid = "Café Net"\npassword = "%s"\ncountry = "US"\n' % ("a" * 64))
conf = os.path.join(work, "riparr.conf")
with open(conf, "w", encoding="utf-8", newline="\n") as f:
    f.write("RIPARR_PORT=9898\nRIPARR_HOSTNAME=riparr\n")
mkv = os.path.join(work, "make mkv")
os.makedirs(mkv)
with open(os.path.join(mkv, "makemkv-oss-0.0.0.tar.gz"), "wb") as f:
    f.write(os.urandom(200000))

args = types.SimpleNamespace(image=xz, total=SIZE, toml=toml, conf=conf, makemkv=mkv,
                             progress=os.path.join(work, "progress.json"))
done = writer._configure_image(args, args.progress)
if not done:
    print(open(args.progress, encoding="utf-8").read())
    sys.exit("configuring the image failed")

with open(done, "rb") as f:
    _, off = writer._first_partition(f)
t = armbian.image_target(done, off)

def cat(path):
    return armbian.debugfs_run([dfs, "-R", "cat %s" % path, t], stdout_only=True)

bad = []
host = cat("/etc/hostname")
if host != "riparr\n":
    bad.append("hostname is %r" % host)
for path in ("/etc/wpa_supplicant/wpa_supplicant-wlan0.conf",
             "/etc/systemd/network/10-wlan0.network", "/boot/riparr.conf"):
    if "\r" in cat(path):
        bad.append("%s has CRLF line endings" % path)
if 'ssid="Café Net"' not in cat("/etc/wpa_supplicant/wpa_supplicant-wlan0.conf"):
    bad.append("SSID did not survive as UTF-8")
if "RIPARR_PORT=9898" not in cat("/boot/riparr.conf"):
    bad.append("riparr.conf port missing")
listing = armbian.debugfs_run([dfs, "-R", "ls /root/makemkv", t], stdout_only=True)
if "makemkv-oss-0.0.0.tar.gz" not in listing:
    bad.append("MakeMKV tarball not in /root/makemkv")
if any(n.endswith("\r") for n in listing.split()):
    bad.append("a file name ends in a carriage return")

if bad:
    sys.exit("image configuration is wrong on Windows:\n  " + "\n  ".join(bad))
print("ok  configured an ext4 image from Windows Python: hostname, Wi-Fi, mDNS, SSH key, "
      "riparr.conf and MakeMKV all read back, no CRLF")

# Hand the fixtures to test-write-disk.py, which writes them to a real disk.
if len(sys.argv) > 2:
    import json
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        json.dump({"image": xz, "toml": toml, "conf": conf, "makemkv": mkv,
                   "size": SIZE}, f)
