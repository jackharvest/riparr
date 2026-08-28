# 1. What You Need

[← Guide index](README.md) · [Next: Prepare the SD card →](02-prepare-sd-card.md)

---

## Parts

| Part | Notes |
|---|---|
| **A supported board** | The **Orange Pi Zero 2W** (Allwinner H618) is the tested board. Riparr also runs on a family of boards in the same footprint — Banana Pi BPI-M4 Zero, Radxa Zero 3W/3E and others, marked *beta* until confirmed. Whichever you pick, the Preparer downloads the right OS for it. |
| **Optical drive** | See [which drive](#which-drive) below — this is the choice that matters most. |
| **microSD card** | 8GB runs it. Bigger only buys staging room — see [which card](#which-card). |
| **USB-C power supply** | 30W for a Slim build, 100W PD for a Full build. |
| **Enclosure** | 3D printed. **PETG or ASA — not PLA.** A drive plus a Pi in a sealed box gets hot enough to soften PLA. |

**[unresolved]** The published parts list and printable enclosure files aren't finalized.
Riparr Slim (5V only, smaller, simpler) and Riparr Full (5V + 12V, larger, cheaper drive)
are likely to ship as two builds.

## Which drive

**This is the expensive mistake to avoid.** Buying the wrong drive is not recoverable in
software, and the listing you buy from will not tell you what you need to know.

There are two separate questions, and shops answer neither clearly.

### 1. What does it read?

| You want to rip | Drive |
|---|---|
| **DVDs only** | Any DVD drive. Nothing special, nothing to check. |
| **Blu-ray (1080p)** | Any Blu-ray reader. Also nothing special — 1080p Blu-ray is not firmware-sensitive, and every working BD drive does DVDs too. |
| **4K / UHD Blu-ray** | **Specific models, often on specific firmware.** Most Blu-ray drives cannot rip a UHD disc at all, and they do not say so anywhere on the box. |

**Why 4K is different, briefly.** UHD discs use a newer copy protection (AACS 2.0). The
official way past it needs licensed player software on an Intel CPU with a feature called
SGX, running Windows — Riparr is an ARM board running Linux, so that route does not exist
here at all. The route that *does* work is MakeMKV's **LibreDrive**, which talks to the
drive's own chipset underneath its firmware, and that works on a specific, finite list of
drives.

So there is no clever software fix available to us, and no drive advertises this. It is a
buying decision.

### 2. How is it powered?

**Slim (laptop-style) drives are 5V-only. Full-size 5.25" drives also need 12V.** That is
what decides whether you are building **Riparr Slim** (smaller box, simpler harness, 30W
supply) or **Riparr Full** (bigger box, cheaper and faster drive, 100W PD supply).

This is *independent* of the first question. There are slim 4K drives and full-size DVD
drives. Buying a full-size UHD drive for a Slim enclosure gets you the right capability in
a box it will not fit.

### The list

| Drive | Size | Reads | Notes |
|---|---|---|---|
| **LG BU40N** | Slim | DVD · Blu-ray · **4K UHD** | **The one to buy for a Slim 4K build.** The most commonly confirmed LibreDrive UHD reader in this form factor. |
| **LG BU50N** | Slim | DVD · Blu-ray · 4K on the right firmware | The BU40N's successor, same shape. Newer units ship firmware that closes LibreDrive — check before buying, not after. |
| **LG WH16NS40 / WH16NS60** | Full | DVD · Blu-ray · 4K on the right firmware | The full-size answer, and what most of the UHD ripping community runs. Usually needs crossflashing first. Needs the 12V rail. |
| **ASUS BW-16D1HT** | Full | DVD · Blu-ray · 4K on the right firmware | Firmware-dependent in the same way. The external BW-16D1X-U is the same drive in a shell. |
| **Any Blu-ray reader** | Either | DVD · Blu-ray | The cheap half of the shelf, and the whole product for most people. |
| **Any DVD drive** | Either | DVD | Cheapest possible build. Slim DVD drives make everything simpler. |

**Firmware versions are deliberately not printed here.** They change, and a stale version
number stated with confidence is worse than a pointer to the live one. Before buying for
4K, check MakeMKV's own compatibility list:

> <https://forum.makemkv.com/forum/viewtopic.php?f=19&t=19634>

**Riparr checks this for you once the drive is plugged in.** The Queue page tags the drive
with what it reads — `DVD` `Blu-ray` `4K UHD`, lit or not — and **System → Status** says
plainly whether 4K will work, asking MakeMKV directly rather than guessing. If you put a
disc in a drive that cannot read it, Riparr says so and ejects it instead of failing forty
minutes later. The same list lives in `server/riparr/drives.py`, so the advice above and
the box's own diagnosis cannot disagree.

### If you use a USB-to-SATA adapter

**"USB to SATA" on a listing says nothing about optical-drive support.** Many adapters in
this class carry an explicit *"Do NOT support BLU-RAY, CD-ROM, DVD-ROM"* warning. The
bridge must **name** optical/ATAPI support.
A drive that is natively USB skips this problem entirely.

## Which card

**Two answers, and the cheap one is probably right.**

Riparr itself uses about 2.3 GB, so **an 8 GB card runs the box**. What card size actually
buys you is *staging* — room to hold a rip on the box while it uploads.

**If you turn on “straight to your library”** (Queue page → *Each rip goes*), nothing is
staged at all. MakeMKV writes onto your share through the mount. On the reference board
that's about 18 MB/s against the card's 9.4, so it's faster as well as cheaper, and card
size stops mattering entirely. The trade is that the rip needs the network for its whole
length instead of only at the end — a NAS that goes to sleep mid-disc costs you the rip.

**If you leave it staging on the card** (the default), the card has to hold one whole
title plus working room:

| Card | Staging room | Comfortably handles |
|---|---|---|
| **8 GB** | ~4 GiB | Runs fine. DVD main titles. |
| **16 GB** | ~11 GiB | DVDs easily, one Blu-ray main title. |
| **32 GB** | ~26 GiB | Blu-rays one at a time. |
| **128 GB** | ~116 GiB | Blu-rays back to back, 4K. |

The Preparer tells you what the card you've plugged in buys you, in discs, before it
writes anything.

Two notes if you're going to stage Blu-rays on the card:

- **Get a high-endurance card** — SanDisk Max Endurance or Samsung Pro Endurance. Every
  disc pushes tens of gigabytes through it, and ordinary cards aren't built for that much
  sustained writing. Going straight to your library avoids this entirely. For DVDs only,
  any decent card is fine.
- **Don't buy an SD Express card.** They cost 3–4× more, these boards can't use the fast
  interface at all, and they run hotter inside the box.

---

[← Guide index](README.md) · [Next: Prepare the SD card →](02-prepare-sd-card.md)
