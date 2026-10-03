#!/usr/bin/env python3
"""Checks for the parts of the card write that do not need the card.

Run it anywhere: `python3 selftest.py`.

The Preparer writes SD cards on three operating systems and can only be run on one of
them at a time, so the pieces most likely to corrupt a card silently -- sector
arithmetic, device-name matching, partition classification -- are written as functions
that take data and return data, and are checked here against tables. That convention
already existed for the Wi-Fi and disk parsers; this extends it across the write.

**What this does not do is prove a card boots.** It cannot, on any platform. It is the
floor, not the ceiling: it catches the arithmetic, and hardware catches the rest.
"""
import io
import subprocess
import sys

import hostos
from hostos import linux, windows

FAILED = []


def check(name, got, want):
    if got != want:
        FAILED.append("%s\n     got  %r\n     want %r" % (name, got, want))


def group(title):
    print("\n%s" % title)


# ───────────────────────────── Device identifiers ─────────────────────────────
# The last thing standing between a typo and somebody's root disk, so both halves
# matter: what it accepts, and what it refuses.

def test_device_ids():
    group("device identifiers")
    from hostos import darwin
    table = [
        (darwin, "disk4", True), (darwin, "disk11", True),
        (darwin, "disk4s1", False), (darwin, "rdisk4", False),
        (darwin, "/dev/disk4", False), (darwin, "", False), (darwin, "sdb", False),

        (linux, "sdb", True), (linux, "mmcblk0", True), (linux, "nvme0n1", True),
        (linux, "sdb1", False), (linux, "mmcblk0p1", False), (linux, "/dev/sdb", False),
        (linux, "sd", False), (linux, "", False), (linux, "disk4", False),

        (windows, r"\\.\PHYSICALDRIVE2", True), (windows, r"\\.\physicaldrive2", True),
        (windows, r"\\.\PHYSICALDRIVE", False), (windows, "C:", False),
        (windows, r"\\.\PHYSICALDRIVE2 ", False), (windows, "", False),
    ]
    for mod, dev, want in table:
        check("%s.valid_device_id(%r)" % (mod.NAME, dev), mod.valid_device_id(dev), want)
    print("  %d identifiers" % len(table))


# ───────────────────────────── Partition naming ─────────────────────────────
# sdb1 but mmcblk0p1: a name already ending in a digit takes a `p`. Getting this wrong
# on a card in a native reader -- the exact hardware this tool is for -- provisions a
# device that is not there.

def test_partition_names():
    group("partition names")
    table = [("sdb", "/dev/sdb1"), ("sdc", "/dev/sdc1"),
             ("mmcblk0", "/dev/mmcblk0p1"), ("nvme0n1", "/dev/nvme0n1p1")]
    for dev, want in table:
        check("linux.partition_devices(%r)" % dev,
              linux.partition_devices(dev, 1), [want])
    from hostos import darwin
    check("darwin.partition_devices('disk4')", darwin.partition_devices("disk4", 1),
          ["/dev/rdisk4s1", "/dev/disk4s1"])
    check("windows.partition_devices", windows.partition_devices(r"\\.\PHYSICALDRIVE2", 1),
          [])
    print("  %d layouts" % (len(table) + 2))


# ───────────────────────────── Which mounts are ours ─────────────────────────────
# /dev/sdb owns /dev/sdb1. It does not own /dev/sdbb1, and unmounting that would mean
# yanking a second card out from under whatever is using it.

def test_mount_matching():
    group("mount matching")
    text = (
        "sysfs /sys sysfs rw 0 0\n"
        "/dev/sda2 / ext4 rw 0 0\n"
        "/dev/sdb1 /media/jack/boot vfat rw 0 0\n"
        "/dev/sdb2 /media/jack/boot/nested ext4 rw 0 0\n"
        "/dev/sdbb1 /media/jack/OTHER-CARD vfat rw 0 0\n"
        "/dev/mmcblk0p1 /media/jack/card vfat rw 0 0\n"
        "/dev/sdb10 /media/jack/with\\040space vfat rw 0 0\n"
    )
    # Nested mounts first, so a child is released before its parent.
    check("sdb", linux.parse_mounts(text, "sdb"),
          ["/media/jack/boot/nested", "/media/jack/with space", "/media/jack/boot"])
    check("sdbb (not sdb's)", linux.parse_mounts(text, "sdbb"),
          ["/media/jack/OTHER-CARD"])
    check("mmcblk0", linux.parse_mounts(text, "mmcblk0"), ["/media/jack/card"])
    check("sda", linux.parse_mounts(text, "sda"), ["/"])
    check("sdz (nothing)", linux.parse_mounts(text, "sdz"), [])
    print("  5 cases, including the /dev/sdbb1 trap and an escaped space")


# ───────────────────────────── The Windows command line ─────────────────────────────

def test_cmdline():
    group("windows command line")
    cases = [
        [r"C:\Program Files\Riparr\writer.py"],
        ["C:\\dir\\"],
        ["--dev", r"\\.\PHYSICALDRIVE2"],
        ["a b", 'c"d', "e\\\\f"],
        ["--sha256", ""],
    ]
    for c in cases:
        check("cmdline(%r)" % c, windows.cmdline(c), subprocess.list2cmdline(c))
    print("  %d command lines, against subprocess.list2cmdline" % len(cases))


# ───────────────────────────── Sector arithmetic ─────────────────────────────
# The Windows sink is the only one of the three that does its own block handling, and it
# is the one that cannot be tried out here. Every byte handed to it must reach the card
# in order, and the tail must be padded up to a whole sector -- a short final write is
# rejected outright on an unbuffered handle, which would fail the write at 99%.

def test_sink_blocking():
    group("windows sector arithmetic")

    class Fake(windows._WinSink):
        def __init__(self):
            self.buf = bytearray()
            self.err = ""
            self.handle = 1
            self.scratch = None
            self.out = bytearray()

        def _emit(self, data):
            assert len(data) % windows.SECTOR == 0, "unaligned write of %d" % len(data)
            self.out += data

        def _teardown(self):
            pass

    # close() also flushes the handle and asks Windows to re-read the partition table.
    # Neither exists here, so kernel32 is stubbed for the duration -- the point of this
    # test is the arithmetic, and stubbing is what lets it run at all off-platform.
    class FakeK32:
        def FlushFileBuffers(self, handle):
            return 1

    real_k32, real_ioctl = windows._k32, windows._ioctl
    windows._k32 = lambda: FakeK32()
    windows._ioctl = lambda *a, **k: b""
    try:
        _run_sink_cases(Fake)
    finally:
        windows._k32, windows._ioctl = real_k32, real_ioctl
    print("  6 chunk patterns, each byte-compared end to end")


def _run_sink_cases(Fake):
    for label, chunks in [
        ("4 MiB chunks, exact", [b"A" * (4 << 20)] * 3),
        ("ragged chunks", [b"B" * 100003] * 40),
        ("one short write", [b"C" * 17]),
        ("nothing at all", []),
        ("exactly one sector", [b"D" * windows.SECTOR]),
        ("a sector minus one", [b"E" * (windows.SECTOR - 1)]),
    ]:
        s = Fake()
        for c in chunks:
            s.write(c)
        rc, err = s.close()
        source = b"".join(chunks)
        pad = (-len(source)) % windows.SECTOR
        check("%s: rc" % label, (rc, err), (0, ""))
        check("%s: bytes reach the card in order" % label,
              bytes(s.out), source + b"\x00" * pad)


# ───────────────────────────── Image classification ─────────────────────────────
# Which provisioning route a written card takes. Reading this wrong means a card that
# boots and does nothing -- which is how the first night of this project was lost.

def test_partition_layout():
    group("image classification")
    import writer

    def mbr(types):
        b = bytearray(512)
        for i, t in enumerate(types):
            b[0x1BE + 16 * i + 4] = t
        b[510:512] = b"\x55\xaa"
        return bytes(b)

    table = [
        ("Raspberry Pi (FAT32 LBA + Linux)", [0x0c, 0x83], ("fat-boot", 1)),
        ("Raspberry Pi (FAT32 CHS)", [0x0b, 0x83], ("fat-boot", 1)),
        ("Armbian / Allwinner (one ext4)", [0x83], ("single-ext4", 1)),
        ("ext4 plus a second partition", [0x83, 0x83], ("unknown", None)),
        ("an empty table", [], ("unknown", None)),
    ]
    real = hostos.open_reader
    try:
        for label, types, want in table:
            data = mbr(types)
            hostos.open_reader = lambda dev, _d=data: io.BytesIO(_d)
            writer.hostos.open_reader = hostos.open_reader
            check(label, writer._partition_layout("disk9"), want)
        # A device that will not open at all must classify as unknown, not crash.
        def boom(dev):
            raise OSError(5, "Input/output error")
        hostos.open_reader = boom
        writer.hostos.open_reader = boom
        check("an unreadable card", writer._partition_layout("disk9"), ("unknown", None))
    finally:
        hostos.open_reader = real
        writer.hostos.open_reader = real
    print("  %d layouts" % (len(table) + 1))


# ───────────────────────────── The platform contract ─────────────────────────────
# hostos/__init__.py promises that adding a platform means adding one module. This is
# what makes that promise checkable rather than aspirational.

CONTRACT = [
    "CAN_WRITE", "PROVISION_IN_IMAGE", "valid_device_id", "block_device", "raw_device", "partition_devices",
    "unmount_disk", "probe_writable", "open_sink", "open_reader", "flush",
    "rescan_partitions", "mount_boot", "eject", "explain_write_error", "elevate",
    "list_block_devices", "scan_wifi", "saved_network_password",
    "open_url", "reveal", "keep_awake_command", "NAME",
]


def test_contract():
    group("platform contract")
    import importlib
    for name in ("darwin", "linux", "windows", "unsupported"):
        mod = importlib.import_module("hostos." + name)
        missing = [f for f in CONTRACT if not hasattr(mod, f)]
        check("hostos.%s implements the contract" % name, missing, [])
    print("  %d names across 4 backends" % len(CONTRACT))


# ───────────────────────────── Raspberry Pi first boot ─────────────────────────────
# Trixie dropped custom.toml for cloud-init, and a card whose seed files are wrong boots
# to a stock image with nothing saying why. What can be checked without a Pi is that the
# Preparer's own settings come out the other end intact -- the awkward SSID, the hash,
# the key going to root -- and that writing a card twice does not stack cmdline tokens.
# The files were also validated against the image's own cloud-init 25.2 schema when this
# was written (see rpi_cloudinit.py); that needs cloud-init itself, so it is not here.

def test_cloud_init():
    group("raspberry pi first boot (cloud-init)")
    import os
    import tempfile
    import core
    import rpi_cloudinit as CI
    import writer
    ssid = 'Mike\'s "Wi-Fi" \\ caf\u00e9'
    key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExample riparr-preparer"
    toml = core.build_toml({
        "hostname": "riparr", "user": "riparr", "pw_hash": "$6$salt$Zx/y.hash",
        "ssid": ssid, "secure": True, "wifi_pw": "hunter22hunter", "hidden": True,
        "country": "gb", "timezone": "America/Chicago", "keymap": "us",
        "authorized_key": key})
    c = CI.parse_custom_toml(toml)
    check("the SSID survives custom.toml", c["ssid"], ssid)
    check("the hash survives", c["pw_hash"], "$6$salt$Zx/y.hash")
    check("the PSK is the derived key, not the passphrase", len(c["psk"]), 64)
    check("the country is upper-cased", c["country"], "GB")
    check("the key is read", c["authorized_keys"], [key])

    seed = CI.build(toml, now_ms=7)
    ud, nc = seed["user-data"], seed["network-config"]
    check("user-data is a cloud-config", ud.splitlines()[0], "#cloud-config")
    check("root gets the key without a refusal", "disable_root: false" in ud, True)
    check("sshd is started", "[ systemctl, enable, --now, ssh ]" in ud, True)
    check("the SSID is YAML-escaped", '"Mike\'s \\"Wi-Fi\\" \\\\ caf\u00e9":' in nc, True)
    check("hidden is carried", "hidden: true" in nc, True)
    check("instance id", seed["meta-data"], "instance-id: riparr-7\n")

    open_net = CI.build(core.build_toml({
        "hostname": "r", "user": "r", "pw_hash": "x", "ssid": "Cafe", "secure": False,
        "country": "US", "authorized_key": key}))["network-config"]
    check("an open network says so", "key-management: none" in open_net, True)

    base = "console=tty1 root=PARTUUID=4d8fd085-02 rootwait resize\n"
    once = CI.patch_cmdline(base, "riparr-1", "GB")
    twice = CI.patch_cmdline(once, "riparr-2", "US")
    check("cmdline keeps what was there", once.split()[:4], base.split())
    check("writing twice leaves one of each", twice,
          "console=tty1 root=PARTUUID=4d8fd085-02 rootwait resize "
          "cfg80211.ieee80211_regdom=US ds=nocloud;i=riparr-2\n")

    d = tempfile.mkdtemp()
    with open(os.path.join(d, "cmdline.txt"), "w") as f:
        f.write(base)
    check("the writer reports every file read back", writer._write_cloud_init(d, toml), None)
    check("and all four are on the card",
          sorted(n for n in os.listdir(d)),
          ["cmdline.txt", "meta-data", "network-config", "user-data"])
    print("  settings round-trip, open and hidden networks, idempotent cmdline")


def test_boards():
    group("board registry")
    import boards
    ids = [b["id"] for b in boards.BOARDS]
    check("ids are unique", len(ids), len(set(ids)))
    check("the default is the tested board", boards.get(boards.default_id())["tier"],
          "verified")
    pis = [b for b in boards.BOARDS if b["os"] == "raspios"]
    check("every Pi uses the one Raspberry Pi OS image",
          {boards.image_source(b["id"])["url"] for b in pis}, {boards.RASPIOS_URL})
    for b in boards.BOARDS:
        check("%s has bands it can name" % b["id"],
              set(b.get("bands", ["2.4", "5"])) <= {"2.4", "5", "6"}, True)
    check("the 2.4 GHz-only boards say so",
          sorted(b["id"] for b in boards.BOARDS if b.get("bands") == ["2.4"]),
          ["raspberrypi3b", "raspberrypizero2w"])
    print("  %d boards, %d of them Raspberry Pi" % (len(ids), len(pis)))


def main():
    print("Riparr Preparer selftest — host is %s, card writing %s"
          % (hostos.NAME, "supported" if hostos.CAN_WRITE else "not supported"))
    for fn in (test_contract, test_device_ids, test_partition_names,
               test_mount_matching, test_cmdline, test_sink_blocking,
               test_partition_layout, test_cloud_init, test_boards):
        fn()
    print()
    if FAILED:
        print("FAILED (%d):\n" % len(FAILED))
        for f in FAILED:
            print("  " + f)
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
