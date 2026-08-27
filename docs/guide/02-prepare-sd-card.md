# 2. Prepare the SD Card

[← What you need](01-what-you-need.md) · [Guide index](README.md) · [Next: First boot →](03-first-boot.md)

**About 3 minutes.** You will not need a monitor, a keyboard, or an SSH session. Riparr is
set up entirely from a browser.

---

## The Riparr Preparer

The supported boards run **Armbian** (or, for the Raspberry Pi Zero 2 W, Raspberry Pi OS),
and the Preparer is the tool that prepares a card for them. It scans for your Wi-Fi, writes
your network in before the card ever boots, and downloads the right operating system for
whichever board you have — so the box comes up on your network by itself the first time you
plug it in.

**Download it from [Releases](../../../../releases)** and open it — there is nothing to
configure. Pick the file for your computer:

| | | |
|---|---|---|
| macOS | `riparr-preparer-macos.dmg` | Open it, drag **Riparr Preparer** to Applications |
| Windows | `riparr-preparer-windows-beta.exe` | Double-click it |
| Linux | `riparr-preparer-linux-beta.tar.gz` | Unpack it and run **Riparr Preparer** |

> **It isn't code-signed, so your computer will warn you the first time.** Riparr is a
> one-person project and a signing certificate is a yearly fee from Apple and from
> Microsoft. On macOS, right-click the app and choose **Open**. On Windows, click
> **More info** then **Run anyway**. Check the download against `SHA256SUMS.txt` on the
> release if you would rather verify it than trust it.

> **On a Mac, one thing may need installing first.** The card's settings live in a Linux
> filesystem that macOS cannot mount, so the Preparer writes them with `debugfs`, from
> Homebrew's `e2fsprogs`. If it isn't there, the Preparer says so on the card screen —
> before it touches anything — and the fix is one line:
>
> ```sh
> brew install e2fsprogs
> ```
>
> Nothing else is needed, and Linux users almost always have it already (`e2fsprogs` is
> in every distribution). The macOS download is **Apple Silicon only**; on an Intel Mac,
> run the Preparer from a checkout instead.

From a checkout instead, `python3 tools/preparer/shell.py` is the same app. It needs its
dependencies once:

```sh
python3 -m venv .venv
.venv/bin/pip install -r tools/preparer/requirements.txt
.venv/bin/python tools/preparer/shell.py
```

A window opens and walks you through it:

**1. Pick your board.** The first thing on the card screen is a **Board** dropdown. Choose
the one you have. It decides which operating system gets written — the Orange Pi Zero 2W is
the default and the tested board; the others are marked **beta** (they should work, and
you'd be helping confirm them). See the board list in the app for the
full list.

**2. Download the OS.** If the image for your board isn't already in your build folder, the
Preparer shows a **Download the OS** button. It fetches the current image straight from the
board vendor (armbian.com or raspberrypi.com) and checks it against the vendor's published
checksum before it will use it. This is a few hundred megabytes; it only happens once per
board.

**3. Pick your card.** Your startup disk can never appear in the list, and anything that
looks like an external drive rather than a card is tucked behind a separate reveal — so a
backup disk can't be picked by accident.

**4. Choose your Wi-Fi.** The Preparer scans and shows the networks in range. **If your
board is dual-band, 5 GHz networks are listed too and are the better pick** when the box
sits near the router — Wi-Fi is what sets how fast a rip lands on your library, so the
faster band matters here. The passphrase is turned into a derived key before it's written,
so your actual Wi-Fi password never touches the card.

**5. Name it and write.** Give it a hostname (`riparr` by default — that's how you'll reach
it), then write. Your computer asks for permission **once** — your password on macOS, a
polkit prompt on Linux, a UAC prompt on Windows — and the Preparer writes the image,
provisions your settings, verifies what it wrote, and ejects the card.

### Writing a card works on macOS, Linux and Windows

It used to be macOS-only. It isn't any more, and the app tells you on its first screen if
your system is one it can't write from, rather than letting you get as far as choosing a
disk and typing a Wi-Fi password.

> **Honest status:** the macOS path has written cards that went on to boot real boards.
> The Linux and Windows paths are complete and the parts that can be checked without a
> card are checked, but as of this writing **neither has yet been run against a real
> board**. If you hit something, an issue with your OS version and card reader is the
> most useful thing you can send.

**On Windows, one combination is refused up front:** an image whose settings live in a
Linux (ext4) filesystem. Windows can't write into one, so the Preparer says so before it
touches your card rather than spending ten minutes writing and failing at the end. Use a
Riparr image with a FAT boot partition, or prepare that card from macOS or Linux.

### If you'd rather use a tool you already trust

Any of this also works with [Raspberry Pi Imager](https://www.raspberrypi.com/software/)
or [balenaEtcher](https://etcher.balena.io/), writing the image for your board from
[armbian.com](https://www.armbian.com/) or
[raspberrypi.com](https://www.raspberrypi.com/software/operating-systems/). Then open the
Preparer and take the **"Set up a box that already has a card"** route, which finds the
box on your network and installs Riparr onto it.

One thing you lose that way: the card will not have your Wi-Fi on it, and **this board
has no Ethernet socket** — so a card with no Wi-Fi credentials produces a box that boots
perfectly and never appears on your network. Raspberry Pi Imager can write Wi-Fi settings
for a Pi; for an Armbian board, edit
`/etc/wpa_supplicant/wpa_supplicant-wlan0.conf` on the card before you eject it.

---

## What happens on first boot

You don't need to watch this, but so you know: the box joins your Wi-Fi, sizes itself to
your card, announces itself as `riparr.local`, and starts the web interface. **Give it a
few minutes** the first time — about four from power-on.

---

[← What you need](01-what-you-need.md) · [Guide index](README.md) · [Next: First boot →](03-first-boot.md)
