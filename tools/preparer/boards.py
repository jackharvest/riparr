"""
The boards Riparr runs on -- every SBC in the Orange Pi Zero 2W footprint that clears
Riparr's real requirements (arm64 for MakeMKV, a USB host for the drive, enough RAM, and
a maintained OS image). This is the single source of truth the Preparer's hardware
dropdown reads: it is what turns "which board is this?" into "which OS image to fetch,
and what to tell the user about it."

The survey behind the list -- which boards qualify, which are excluded and why -- is in
docs/design/board-support.md. Two things it settles are baked in here:

  * **Support tier is honest.** `verified` is the one board Riparr has actually booted on
    (the Orange Pi Zero 2W). Every other board is `beta`: the image and the layout-driven
    provisioning are the same mechanism, but nobody has confirmed the whole path on that
    hardware yet. Beta boards get a plain badge in the UI and are confirmed by users after
    release -- said plainly rather than hidden.

  * **Order is cost-first.** Cheapest viable board at the top, which is also the default
    selection, because catering to cost is the point of supporting more than one board.
"""

# Armbian serves a durable per-board handle that always resolves to the *current* image,
# so the registry stores a slug + release rather than a versioned URL that would rot:
#
#   https://dl.armbian.com/<slug>/<release>        -> the .img.xz          (a redirect)
#   https://dl.armbian.com/<slug>/<release>.sha    -> "<sha256>  <filename>"
#
# Verified live 2026-08-22: every slug below resolves with this release token.
ARMBIAN_DL = "https://dl.armbian.com/%s/%s"
# Debian trixie, minimal (no desktop), current kernel -- the build Riparr targets (D17).
ARMBIAN_RELEASE = "Trixie_current_minimal"

# Raspberry Pi publishes its own durable "latest" handle with a .sha256 companion.
#
# One image for every Raspberry Pi here. Raspberry Pi OS Lite (64-bit) carries the
# device trees and kernels for the Zero 2 W, 3, 4, 400 and 5 on one card -- their own OS
# list files it under pi3-64bit, pi4-64bit and pi5-64bit at the same URL -- so the
# Preparer downloads it once and any Pi board reuses it. Checked 2026-10-03 against the
# 2026-09-15 Trixie image's boot partition. Its first-boot setup is cloud-init, not
# custom.toml: see rpi_cloudinit.py.
RASPIOS_URL = "https://downloads.raspberrypi.com/raspios_lite_arm64_latest"
RASPIOS_SHA = RASPIOS_URL + ".sha256"


# `bands` is listed only for a board whose radio cannot do 5 GHz. Absent means 2.4 and
# 5, which is every other board here; the Wi-Fi step greys out what the radio can't see.
BOARDS = [
    {
        "id": "orangepizero2w",
        "name": "Orange Pi Zero 2W",
        "soc": "Allwinner H618",
        "ram": "1 – 4 GB",
        "tier": "verified",
        "os": "armbian",
        "armbian_slug": "orangepizero2w",
        "cost": "cheapest",
        "note": "The reference board. This is the one Riparr is developed and tested on. "
                "Its two USB-C sockets look identical but only one can host the drive "
                "\u2014 if the drive is not found, swap the power and data cables over. "
                "Riparr can also reconfigure the second socket so either one works.",
    },
    {
        "id": "bananapim4zero",
        "name": "Banana Pi BPI-M4 Zero",
        "soc": "Allwinner H618",
        "ram": "2 / 4 GB",
        "tier": "beta",
        "os": "armbian",
        "armbian_slug": "bananapim4zero",
        "cost": "budget",
        "note": "Same H618 SoC as the Orange Pi, so the image and provisioning are the "
                "same. Its USB-C and mini-HDMI sit in different places, so check the "
                "enclosure's port cutouts.",
    },
    {
        "id": "radxa-zero3",
        "name": "Radxa Zero 3W / 3E",
        "soc": "Rockchip RK3566",
        "ram": "1 – 8 GB",
        "tier": "beta",
        "os": "armbian",
        "armbian_slug": "radxa-zero3",
        "cost": "mid",
        "note": "One Armbian image covers both: the 3W has Wi-Fi 6, the 3E has Gigabit "
                "Ethernet + PoE. Both add USB 3.0 -- a faster link to the drive.",
    },
    {
        "id": "radxa-zero",
        "name": "Radxa Zero",
        "soc": "Amlogic S905Y2",
        "ram": "1 – 4 GB",
        "tier": "beta",
        "os": "armbian",
        "armbian_slug": "radxa-zero",
        "cost": "mid",
        "note": "Take a 2 GB or 4 GB model -- Wi-Fi is omitted on the 512 MB version.",
    },
    {
        "id": "raspberrypizero2w",
        "name": "Raspberry Pi Zero 2 W",
        "soc": "Broadcom BCM2710A1",
        "ram": "512 MB",
        "tier": "beta",
        "os": "raspios",
        "ram_warn": "512 MB is the tight case for MakeMKV -- the build leans on zram and "
                    "a real rip's peak memory is still unmeasured. It will work; it has "
                    "the least headroom of any supported board.",
        "cost": "budget",
        "bands": ["2.4"],
        "note": "The namesake, and a guaranteed mechanical fit. Boots Raspberry Pi OS "
                "rather than Armbian. Its Wi-Fi is 2.4 GHz only.",
    },

    # ── full-size Raspberry Pis ──
    #
    # Not the Zero footprint, so none of these fit the printed enclosure -- they are here
    # because people already own them, and the software side costs nothing: the same
    # Raspberry Pi OS image and the same cloud-init provisioning as the Zero 2 W. All are
    # arm64 with a USB host, which is the whole of Riparr's hardware requirement. The
    # Pi 1, 2 and Zero / Zero W are absent for the reason board-support.md gives: no
    # 64-bit CPU, so no MakeMKV.
    #
    # Power is the user's to sort out (micro-USB on the 3s, USB-C on the 4 and 5) and is
    # mentioned only where it can quietly stop a drive working.
    {
        "id": "raspberrypi3aplus",
        "name": "Raspberry Pi 3 Model A+",
        "soc": "Broadcom BCM2837B0",
        "ram": "512 MB",
        "tier": "beta",
        "os": "raspios",
        "cost": "budget",
        "ram_warn": "512 MB is the tight case for MakeMKV, the same as the Zero 2 W. It "
                    "will work; it has the least headroom of any supported board.",
        "note": "Full-size A+ board, so it won't fit the Zero-sized enclosure. One USB-A "
                "port for the drive and dual-band Wi-Fi.",
    },
    {
        "id": "raspberrypi3b",
        "name": "Raspberry Pi 3 Model B",
        "soc": "Broadcom BCM2837",
        "ram": "1 GB",
        "tier": "beta",
        "os": "raspios",
        "cost": "budget",
        "bands": ["2.4"],
        "note": "Full-size board, so it won't fit the Zero-sized enclosure. Its Wi-Fi is "
                "2.4 GHz only — or plug in Ethernet.",
    },
    {
        "id": "raspberrypi3bplus",
        "name": "Raspberry Pi 3 Model B+",
        "soc": "Broadcom BCM2837B0",
        "ram": "1 GB",
        "tier": "beta",
        "os": "raspios",
        "cost": "budget",
        "note": "Full-size board, so it won't fit the Zero-sized enclosure. Dual-band "
                "Wi-Fi, and Ethernet if you'd rather plug it in.",
    },
    {
        "id": "raspberrypi4",
        "name": "Raspberry Pi 4 Model B / 400",
        "soc": "Broadcom BCM2711",
        "ram": "1 – 8 GB",
        "tier": "beta",
        "os": "raspios",
        "cost": "mid",
        "note": "Full-size board, so it won't fit the Zero-sized enclosure. Plug the drive "
                "into a blue USB 3.0 port, and Gigabit Ethernet will beat Wi-Fi to your "
                "library.",
    },
    {
        "id": "raspberrypi5",
        "name": "Raspberry Pi 5",
        "soc": "Broadcom BCM2712",
        "ram": "1 – 16 GB",
        "tier": "beta",
        "os": "raspios",
        "cost": "mid",
        "note": "Full-size board, so it won't fit the Zero-sized enclosure. On a power "
                "supply under 5 A the Pi 5 limits its USB ports to 600 mA, which a "
                "bus-powered drive can exceed — use the official 27 W supply or a "
                "drive with its own power.",
    },
]


def all_boards():
    """The list as the UI wants it -- no internal URL fields, just what it displays."""
    fields = ("id", "name", "soc", "ram", "tier", "os", "cost", "note", "ram_warn",
              "bands")
    return [{k: b[k] for k in fields if k in b} for b in BOARDS]


def get(board_id):
    for b in BOARDS:
        if b["id"] == board_id:
            return b
    return None


def default_id():
    """Cheapest viable board, which is the first in the list."""
    return BOARDS[0]["id"]


def image_source(board_id):
    """Where to fetch this board's OS image, and where its checksum lives.

    Returns {"url", "sha_url", "sha_kind"} or None for an unknown board. `sha_kind` says
    how to read the checksum file: Armbian's `.sha` and Raspberry Pi's `.sha256` are both
    "<hex>  <filename>", so they parse the same way -- the field is there for the day one
    of them is not.
    """
    b = get(board_id)
    if not b:
        return None
    if b["os"] == "raspios":
        return {"url": RASPIOS_URL, "sha_url": RASPIOS_SHA, "sha_kind": "sha256sum"}
    slug = b["armbian_slug"]
    base = ARMBIAN_DL % (slug, ARMBIAN_RELEASE)
    return {"url": base, "sha_url": base + ".sha", "sha_kind": "sha256sum"}
