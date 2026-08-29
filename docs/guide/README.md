# Build one

One box. One cable. Discs go in, files land on your NAS.

**This is a "put that old optical drive to good use" project.** Start from a drive you
already own — out of a laptop you retired, or a tower in the garage. Everything you add
around it is a few dollars, apart from the board.

<!-- TODO(mike): hero photo of the finished unit, ideally with a disc going in.
     Drop it at docs/img/unit-hero.jpg and uncomment.
<img src="../img/unit-hero.jpg" alt="The finished Riparr box">
-->

---

## What it costs

Roughly. If the drive came out of something you already owned, the board is the biggest
line here. Prices move and vary by where you are — treat these as ballpark, not a quote.

| Part | What I used | Roughly |
|---|---|---|
| Optical drive | The one out of a dead laptop or tower — [read this before you buy one](01-what-you-need.md#which-drive) | $0–90 |
| Board | Orange Pi Zero 2W. The 1 GB model is plenty | $40–50 |
| SD card | 8 GB. Films go to your NAS, not the card — bigger buys nothing | $5 |
| USB-C PD trigger | Solderless, DIP-switch selected. Set it to 15 V or 20 V | $10 for four |
| Buck converters | Two: one down to 12 V, one down to 5 V | $10 each |
| USB-C pigtail | Red-and-black bare wires to a USB-C plug. Powers the board | $6 |
| USB-C PD brick | 45 W or more. Probably a laptop charger you already own | $0–20 |
| SATA bridge + cable | Only if the drive is bare SATA — [and both are easy to buy wrong](01-what-you-need.md#if-your-drive-is-sata) | $12 |
| Cable | One USB-C, that's the whole external interface | $5 |
| Filament | A few hundred grams | $5 |

**The board got expensive.** I paid $21 for the Orange Pi Zero 2W last August; it's mid
$40s now and the listing I bought from is dead. It's still sourceable, just not a
bargain any more. Everything else on this list is a few dollars.

**4K UHD costs more**, and it's a *drive* decision you make once at purchase — not a
setting you can flip later. [Read this first](01-what-you-need.md#which-drive).

---

## What's inside

<!-- TODO(mike): two internal shots — one with the case open showing the drive and board,
     one close on the power board / USB-C entry. docs/img/unit-internal-*.jpg
<img src="../img/unit-internal-1.jpg" alt="Inside the case: drive above, board below">
<img src="../img/unit-internal-2.jpg" alt="USB-C entry and power conversion">
-->

A drive, a board, and about $20 of power conversion. That's it.

```
             ┌───────────────────────────────────────────────────┐
             │                    the case                       │
             │                                                   │
  USB-C ─────┤  ┌────────────┐                                   │
   the only  │  │ PD trigger │ 15V or 20V                        │
   cable,    │  │ DIP switch ├──────┬─────────────┐              │
   in or out │  └────────────┘      │             │              │
             │                 ┌────▼────┐   ┌────▼────┐         │
             │                 │  buck   │   │  buck   │         │
             │                 │  → 12V  │   │  → 5V   │         │
             │                 └────┬────┘   └──┬───┬──┘         │
             │                      │           │   │            │
             │              ┌───────▼───────────▼┐  │ 5V, via a  │
             │              │   optical drive    │  │ USB-C      │
             │              └─────────┬──────────┘  │ pigtail    │
             │                        │ USB data    │            │
             │                   ┌────▼─────────────▼───┐        │
             │                   │ board (Pi Zero 2W)   │        │
             │                   └──────────────────────┘        │
             └───────────────────────────────────────────────────┘
```

**One USB-C in.** Nothing else leaves the box — no second cable, no network cable, no
screen. A slim drive drops the 12V buck; everything else is the same.

**Three things that will bite you**, all worth reading before you order parts:

- **You're buying volts, not watts.** The whole box peaks at 18 W, but the trigger board
  has to be able to ask your brick for 15 V or 20 V, and a charger only carries those if
  it's rated for them — **45 W or more** guarantees both. And don't set the trigger to
  12 V to skip a buck: 12 V isn't a standard USB-C voltage at all.
  [The table that explains it](01-what-you-need.md#how-its-powered).
- **A bare SATA drive needs a data-only USB bridge that names optical support**, plus a
  right-angle 22-pin cable to get out of the drive's way. Both are a few dollars and both
  are easy to buy wrong. [Which ones](01-what-you-need.md#if-your-drive-is-sata).
- **A drive tray that opens has proved its power rail and nothing about its data path.**
  If the tray works but the box never sees a disc, suspect the USB bridge or the cable,
  not the drive.

---

## Build it

<!-- TODO(mike): the case isn't published yet. When the STLs exist, link them here and
     add print settings (layer height, infill, supports, orientation, material). -->

> **The case files aren't published yet.** Everything below the enclosure works today —
> the software half is done and running. If you want to build one now, any box that fits
> a drive and a board and gets power to both will do.

1. **Print the case.** *(files to come)*
2. **Wire it** as above. Trigger to 15 V or 20 V, a buck each for the drive and
   the board, drive data to the board's USB port.
3. **[Write the SD card](02-prepare-sd-card.md)** — about three minutes with the Preparer.
4. **Plug it in.** The Preparer waits until it sees the box on your network, then installs
   everything over SSH. Ten minutes, hands off.
5. **Open `riparr.local`**, point it at your share, put a disc in.

That's the build. The rest of this guide is reference for when something surprises you.

---
---

# The rest of it

You shouldn't need these. They're here for when you do.

| | |
|---|---|
| [What you need](01-what-you-need.md) | Parts in detail, and **which drive to buy** — the part people get wrong |
| [Prepare the SD card](02-prepare-sd-card.md) | The Preparer, step by step |
| [First boot](03-first-boot.md) | Finding the box, setting your password |
| [Connect your library](04-connect-your-library.md) | Shares, and the write test |
| [Library layout](05-library-layout.md) | Naming that Plex and Jellyfin understand |
| [Ripping discs](06-ripping-discs.md) | Auto Rip, queue, what each stage means |
| [Settings reference](07-settings-reference.md) | Every setting, and why it's there |
| [Troubleshooting](08-troubleshooting.md) | When it sulks |

**Pre-1.0.** It rips discs end to end on real hardware and updates itself. It has also
only run on one board, with one drive, against one NAS — so other hardware is where the
surprises will be. [Issues](../../../issues) are genuinely useful.
