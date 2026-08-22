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

```sh
~/riparr-build/prepare
```

That is the whole command. A window opens and walks you through it:

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

> **Writing a card is macOS-only for now.** Everything else in the Preparer — the board
> dropdown, the OS download, the Wi-Fi scan, and setting up a box whose card is already
> written — works on Windows and Linux; only the raw card write is still being ported
> ([cross-platform.md](../design/cross-platform.md)). On Windows or Linux today, download
> the image for your board from the vendor and write it with your preferred imager, then
> use the Preparer's *"It's plugged in"* route to finish setup over the network.

---

## What happens on first boot

You don't need to watch this, but so you know: the box joins your Wi-Fi, sizes itself to
your card, announces itself as `riparr.local`, and starts the web interface. **Give it a
few minutes** the first time — about four from power-on.

---

[← What you need](01-what-you-need.md) · [Guide index](README.md) · [Next: First boot →](03-first-boot.md)
