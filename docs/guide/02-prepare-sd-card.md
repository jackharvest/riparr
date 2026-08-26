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

**Download it from [Releases](../../../../releases)** and open it — there is no installer
and nothing to configure. Pick the file for your computer:

| | |
|---|---|
| macOS | `riparr-preparer-macos.zip` |
| Windows | `riparr-preparer-windows.zip` |
| Linux | `riparr-preparer-linux.tar.gz` |

From a checkout instead, `python3 tools/preparer/shell.py` is the same app.

A window opens and walks you through it:

**1. Pick your board.** The first thing on the card screen is a **Board** dropdown. Choose
the one you have. It decides which operating system gets written — the Orange Pi Zero 2W is
the default and the tested board; the others are marked **beta** (they should work, and
you'd be helping confirm them). See [board support](../design/board-support.md) for the
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
it), then write. macOS asks for your password **once**, and the Preparer writes the image,
provisions your settings, verifies what it wrote, and ejects the card.

> **Writing a card is macOS-only for now**, and the app says so on its first screen
> rather than letting you get as far as choosing a disk and typing a Wi-Fi password. The
> card write speaks `diskutil`, `/dev/rdiskN` and macOS's rules about which application
> owns removable-media consent; none of that has a tested equivalent elsewhere yet
> ([cross-platform.md](../design/cross-platform.md)).
>
> **On Windows or Linux**, do stage 1 with a tool you already trust — [Raspberry Pi
> Imager](https://www.raspberrypi.com/software/) or
> [balenaEtcher](https://etcher.balena.io/) — writing the image for your board from
> [armbian.com](https://www.armbian.com/) or
> [raspberrypi.com](https://www.raspberrypi.com/software/operating-systems/). Then open
> the Preparer and take the **"Set up a box that already has a card"** route, which finds
> the box on your network and installs Riparr onto it. That is the longer half and it
> works the same on every system.
>
> One thing you lose that way: the card will not have your Wi-Fi on it. Raspberry Pi
> Imager can write Wi-Fi settings for a Pi; for an Armbian board, plug the box into
> Ethernet for the first boot, or edit
> `/etc/wpa_supplicant/wpa_supplicant-wlan0.conf` on the card before you eject it.

---

## What happens on first boot

You don't need to watch this, but so you know: the box joins your Wi-Fi, sizes itself to
your card, announces itself as `riparr.local`, and starts the web interface. **Give it a
few minutes** the first time — about four from power-on.

---

[← What you need](01-what-you-need.md) · [Guide index](README.md) · [Next: First boot →](03-first-boot.md)
