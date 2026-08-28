# 1. What You Need

[← Guide index](README.md) · [Next: Prepare the SD card →](02-prepare-sd-card.md)

---

## Parts

**Start with the drive you already own.** This project exists to give an old optical
drive somewhere to live. Everything else on this list is either cheap or something
that's been in a drawer since 2014.

| Part | Notes |
|---|---|
| **Optical drive** | See [which drive](#which-drive) below — this is the choice that matters most, and the only one you can get expensively wrong. |
| **A supported board** | The **Orange Pi Zero 2W** (Allwinner H618) is the tested board — the **1 GB** model is plenty. Riparr also runs on a family of boards in the same footprint — Banana Pi BPI-M4 Zero, Radxa Zero 3W/3E and others, marked *beta* until confirmed. Whichever you pick, the Preparer downloads the right OS for it. |
| **microSD card** | 8GB runs it. Bigger only buys staging room — see [which card](#which-card). |
| **USB-C PD brick** | **45W or more.** It's the volts that matter, not the watts — see [how it's powered](#how-its-powered). |
| **The power bits inside** | A PD trigger board and two buck converters. About $20 all in, and [the list is below](#how-its-powered). |
| **Enclosure** | 3D printed. **PETG or ASA — not PLA.** A drive plus a board in a sealed box gets hot enough to soften PLA. |

**The board is the one that got expensive.** The Orange Pi Zero 2W was $21 in August
2025 and is mid $40s now, when you can find it in stock at all — it goes in and out on
every seller. It's still the right board: it's as low-power as you dare go for this and
it does the job. Just don't expect last year's price.

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

### 2. What size is it?

**Slim (laptop-style) drives are 5V-only. Full-size 5.25" drives also need 12V.** So the
size of the drive decides how much is inside the box: a slim drive skips the 12V buck
entirely, and a full-size one needs it. Either way the outside is the same single USB-C
cable — see [how it's powered](#how-its-powered).

This is *independent* of the first question. There are slim 4K drives and full-size DVD
drives. A full-size drive is bigger, usually cheaper, and usually faster. A slim one
makes a smaller box with less wiring in it. Pick the box you want to print, then buy a
drive that fits it *and* reads what you want.

### The list

| Drive | Size | Reads | Notes |
|---|---|---|---|
| **LG BU40N** | Slim | DVD · Blu-ray · **4K UHD** | **The one to buy for a slim 4K build.** The most commonly confirmed LibreDrive UHD reader in this form factor. |
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

### If your drive is SATA

A drive that is natively USB plugs straight into the board and you can skip this whole
section. A bare SATA drive out of a PC needs two more parts, and both of them are fussier
than they look.

**The bridge: "USB to SATA" on a listing says nothing about optical-drive support.** Many
adapters in this class carry an explicit *"Do NOT support BLU-RAY, CD-ROM, DVD-ROM"*
warning. The bridge must **name** optical/ATAPI support, and it must be one that *doesn't*
try to power the drive — the drive gets its power from the bucks, not from the adapter.
That combination is oddly rare. The one that works here is a plain
[USB 3.0 to SATA converter](https://www.amazon.com/dp/B0CS5XV3LM) (~$10, JMicron chipset,
data only, no bundled brick).

It's a no-name part with mixed reviews, and one of mine died on the bench. Buy it
somewhere that takes returns and test it before you close the case — because when it does
fail, it fails confusingly: **a drive whose tray opens has proved its power rail and
nothing at all about its data path.** If the tray works but Riparr never sees a disc,
suspect the bridge or the cable, not the drive.

**The cable, which matters more than it sounds:** a
[22-pin SATA male-to-female left-angle extension](https://www.amazon.com/dp/B00A9LUBKO),
about $1.50. It carries data *and* power in one run, and the right-angle end takes the
connector off the back of the drive sideways instead of straight out, which is the
difference between a drive that fits the case and one that doesn't. At the far end you get
the same 22-pin connector a foot away, where there's room to plug in the bridge's data lead
and the 15-pin power lead side by side.

This is a full-size-drive part. Slim drives use a slimline connector instead, and need a
slimline-SATA adapter in the same role.

## How it's powered

**One USB-C cable goes in and nothing else does.** Inside, that gets turned into the 12V
and 5V the drive and the board actually want.

```
  USB-C brick          PD trigger                    bucks
  45W or more   ──▶   set to 15V or 20V   ──┬──▶   down to 12V  ──▶  drive: motor + laser
                       (DIP switch)         │
                                            └──▶   down to  5V  ──┬─▶ drive: logic
                                                                  └─▶ board, via a
                                                                      USB-C pigtail
```

**The whole box peaks at 18W** — drive spinning up, board busy, everything at once. It is
not a hungry machine.

### It's the volts you need, not the watts

18W sounds like any phone charger will do. It won't, and this is the one place people get
stuck.

The trigger board's job is to *ask* the brick for a voltage, and the bucks want something
comfortably above 12V to work with. A brick can only offer what it was built to offer,
and USB-C ties that to its wattage — the more powerful it is, the more voltages it has
to carry:

| Brick | Voltages it must offer |
|---|---|
| Up to 15W | 5V |
| 15–27W | 5V, 9V |
| 27–45W | 5V, 9V, **15V** |
| 45W and up | 5V, 9V, **15V, 20V** |

So **45W or more** and you're certain of both. Any laptop-class charger you already own
is almost certainly fine — you're buying its voltage list, not its wattage.

**Set the trigger to 15V or 20V.** Both bucks are happy anywhere from 15V to 32V in, so
either works.

**Don't set it to 12V and skip a buck.** It's the obvious shortcut and it's the one that
fails. Look at the table again: **12V isn't on it.** It's optional in USB-C, plenty of
bricks don't do it at all, and the reviews on every trigger board on the market are full
of people who discovered that with a multimeter. 15V and 20V you can count on.

### Why not just require a 12V brick?

Because it would be restrictive and buy you nothing. USB-C chargers are everywhere and
you probably have a spare in a drawer; a 12V barrel supply is one more specific thing to
order and one more thing to lose. Given the price of drives, storage and boards right
now, spending $20 on trigger-and-bucks to turn "this exact brick" into "any laptop
charger" is a good trade.

### The power parts

| Part | What it is | Roughly |
|---|---|---|
| **PD trigger** | [Solderless board, voltage set with a DIP switch](https://www.amazon.com/dp/B0DPHHX2ZT). No soldering iron needed | $10 for four |
| **Buck → 12V** | [12V 5A, takes 15–32V in](https://www.amazon.com/dp/B0CSPK54RC). Drive motor and laser. **Skip this on a slim drive** | $10 |
| **Buck → 5V** | [5V 5A, takes 12–32V in](https://www.amazon.com/dp/B0CSPTCT2H). Drive logic and the board | $10 |
| **USB-C pigtail** | [Bare red-and-black to a USB-C plug](https://www.amazon.com/dp/B0DCGN8ZG3). Runs from the 5V buck to the board's power port | $6 |

Both bucks are rated 5A, so at 18W total the box is loafing — which is what you want,
because cheap converters run hot and unhappy near their ratings.

**One wiring rule.** Feed the drive and the board from **separate buck outputs**, not off
one shared node, and put some bulk capacitance on the 5V rail. Optical drives pull a
sharp surge when they spin up, and if the board is sharing an unbuffered rail with it,
that surge reboots the board — at disc insertion, which makes it look like a software
bug. It isn't.

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
