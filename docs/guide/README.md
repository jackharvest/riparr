# Riparr — User Guide

Insert a disc. Walk away. It ends up in your library, correctly named.

This guide takes you from a blank SD card to a working appliance in about five minutes of
actual effort. **Read it in order the first time.** After that, you should never need it
again — that's the whole point of the product.

> ⚠️ **Pre-release.** Riparr is not built yet. This guide describes the target product and
> is written first on purpose, so the experience is designed before the code is. Anything
> still unresolved is marked **[unresolved]** inline.

---

## Setup — do this once

| | | Time |
|---|---|---|
| **1** | [What you need](01-what-you-need.md) — parts list, and which card to buy | — |
| **2** | [Prepare the SD card](02-prepare-sd-card.md) — the Preparer: pick your board, WiFi, headless | 3 min |
| **3** | [First boot](03-first-boot.md) — find the box, set your password | 1 min |
| **4** | [Connect your library](04-connect-your-library.md) — pick a share, test the write | 1 min |
| **5** | [Library layout & naming](05-library-layout.md) — Plex/Jellyfin conventions | 1 min |

## Then, forever after

| | |
|---|---|
| **6** | [Ripping discs](06-ripping-discs.md) — the daily loop, and what the box is doing |
| **7** | [Settings reference](07-settings-reference.md) — every setting, and why you'd touch it |
| **8** | [Troubleshooting](08-troubleshooting.md) — organized by what you actually observed |

## Quick reference

- 🔦 **[LED reference card](led-reference.md)** — what each color means. Print it, tape it
  inside the lid.
- 🔑 **[MakeMKV key](07-settings-reference.md#makemkv-key)** — the one thing that expires.
  Riparr warns you before it bites.

---

## Things worth knowing before you start

**You do not need a big SD card.** A 32GB card handles everything, including 4K discs.
Riparr streams the rip out to your library as it goes, so it can't fill up. A bigger card
only makes the disc eject sooner, which matters if you like loading several discs in one
sitting. See [what you need](01-what-you-need.md#which-card).

**The disc ejecting means it's done.** On a 32GB-class card, the eject happens when the
file has finished landing in your library. On a large card the box ejects early to let you
load the next disc, and keeps uploading in the background — the LED tells you which is
happening, and the web page always says.

**There is no screen and no off switch.** That's deliberate. Riparr expects the cable to
get yanked and is built to survive it. If something goes wrong, the LED tells you *that*
it went wrong and `riparr.local` tells you *why*.

**Ripping takes as long as it takes.** A DVD is roughly half an hour, a Blu-ray a few
hours, a 4K disc most of a working day. Nothing you buy makes this faster — the limit is
WiFi, not the drive.
