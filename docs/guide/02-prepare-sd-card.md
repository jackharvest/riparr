# 2. Prepare the SD Card

[← What you need](01-what-you-need.md) · [Guide index](README.md) · [Next: First boot →](03-first-boot.md)

**About 3 minutes.** You will not need a monitor, a keyboard, or an SSH session. Riparr is
set up entirely from a browser.

---

## Two ways to do this

**The Riparr Flasher** *(recommended)* — an interactive wizard that scans for your Wi-Fi,
shows only the networks a Pi Zero 2W can actually join, and writes the card in one pass.
It cannot pick a 5 GHz network, cannot write to your boot drive, and cannot produce a
mistyped share of the settings below. See [`tools/flasher/`](../../tools/flasher/README.md).

```sh
./riparr-flash.py --assets /path/to/build/dir
```

**Raspberry Pi Imager** — the manual path, described below. Nothing to install beyond
Imager itself. Use this if you'd rather not run a script.

> **[unresolved]** The flasher currently needs a Python virtualenv, which is not yet the
> five-minute experience the rest of this guide promises. Shipping it as a signed app, or
> getting Riparr listed in Imager's OS list, is the eventual answer.

---

## What you're doing

Raspberry Pi Imager can write your WiFi details onto the card *before* it ever boots.
Riparr is built around this — it's why the box appears on your network by itself the first
time you plug it in.

## Steps

**1. Install Raspberry Pi Imager**

Download from [raspberrypi.com/software](https://www.raspberrypi.com/software/).
Available for macOS, Windows, and Linux.

**2. Insert your SD card**

Anything on it will be erased.

**3. Choose the device**

Set **Raspberry Pi Device** to **Raspberry Pi Zero 2 W**.

**4. Choose the Riparr image**

**[unresolved]** Riparr aims to be listed directly in Imager's OS list. Until then:
**Choose OS → Use custom**, and select the `riparr-x.y.z.img.xz` file you downloaded.
Don't decompress it first — Imager reads `.img.xz` directly.

**5. Choose your SD card**

Check this twice. Imager will happily erase an external drive.

**6. Fill in the customization dialog — this is the important part**

Imager asks whether you want to apply OS customization settings. **Say yes**, then set:

| Field | Set it to |
|---|---|
| **Hostname** | `riparr` — this is how you'll reach it. Building more than one? Use `riparr-office`, `riparr-den`, etc. |
| **Username and password** | Your login for the box itself. **Write these down.** This is separate from the Riparr web password you'll set next. |
| **Configure wireless LAN** | Your WiFi name and password. **Must be a 2.4GHz network** — the Zero 2W cannot see 5GHz networks at all. |
| **Wireless LAN country** | Required, or WiFi silently won't start. |
| **Set locale settings** | Your timezone — this makes timestamps and logs readable. |
| **Enable SSH** | Optional. Not needed. Turn it on if you want a way in when something goes badly wrong. |

> ⚠️ **The 2.4GHz thing catches people.** If your router broadcasts one name for both
> bands, that's fine. If 5GHz has its own name, don't use it — the Pi Zero 2W has no 5GHz
> radio.

**7. Write, and wait**

Imager writes and verifies. A few minutes. When it says you can remove the card, do.

## What happens on first boot

You don't need to watch this, but so you know: the box joins your WiFi, sizes itself to
your card, announces itself as `riparr.local`, and starts the web interface. **Give it
about two minutes** the first time.

---

[← What you need](01-what-you-need.md) · [Guide index](README.md) · [Next: First boot →](03-first-boot.md)
