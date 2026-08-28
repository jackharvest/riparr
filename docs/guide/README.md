# Build one

One box. One cable. Discs go in, files land on your NAS.

<!-- TODO(mike): hero photo of the finished unit, ideally with a disc going in.
     Drop it at docs/img/unit-hero.jpg and uncomment.
<img src="../img/unit-hero.jpg" alt="The finished Riparr box">
-->

---

## What it costs

Roughly, and it's mostly the drive. Prices move and vary by where you are — treat these
as ballpark, not a quote.

| Part | What I used | Roughly |
|---|---|---|
| Board | Orange Pi Zero 2W (2 GB) | $20–30 |
| Optical drive | USB Blu-ray writer — [read this before you buy](01-what-you-need.md#which-drive) | $30–90 |
| SD card | Any 8 GB or bigger. Riparr uses about 2.3 GB | $5 |
| USB-C PD supply | 30 W for a slim drive, 100 W for a full-size one | $10–20 |
| Cable | One USB-C, that's the whole external interface | $5 |
| Filament | A few hundred grams | $5 |

**4K UHD costs more**, and it's a *drive* decision you make once at purchase — not a
setting you can flip later. [Read this first](01-what-you-need.md#which-drive).

---

## What's inside

<!-- TODO(mike): two internal shots — one with the case open showing the drive and board,
     one close on the power board / USB-C entry. docs/img/unit-internal-*.jpg
<img src="../img/unit-internal-1.jpg" alt="Inside the case: drive above, board below">
<img src="../img/unit-internal-2.jpg" alt="USB-C entry and power conversion">
-->

A drive, a board, and a bit of power conversion. That's it.

```
                    ┌──────────────────────────────────┐
                    │            the case              │
   USB-C ───────────┤                                  │
  (power + nothing  │   ┌────────────┐                 │
   else leaves the  │   │  optical   │◀── data (USB) ─┐│
   box)             │   │   drive    │                ││
                    │   └─────▲──────┘                ││
                    │         │ power                 ││
                    │   ┌─────┴──────┐                ││
                    │   │   power    │                ││
                    │   │ conversion ├── 5V ──┐       ││
                    │   └────────────┘        ▼       ││
                    │                   ┌───────────┐ ││
                    │                   │   board   ├─┘│
                    │                   │  (Pi Zero │  │
                    │                   │    2W)    │  │
                    │                   └───────────┘  │
                    └──────────────────────────────────┘
```

**One USB-C in.** Nothing else leaves the box — no power brick, no second cable, no
network cable, no screen.

**Two things that will bite you**, both worth reading before you order parts:

- **A slim drive wants ~30 W. A full-size 5.25" drive wants 12 V *and* 5 V** through SATA
  power, so it needs a supply that can do both and a PD budget nearer 100 W.
- **A drive tray that opens has proved its power rail and nothing about its data path.**
  If the tray works but the box never sees a disc, suspect the USB bridge or the cable,
  not the drive. [More on which bridge to buy](01-what-you-need.md#which-drive).

---

## Build it

<!-- TODO(mike): the case isn't published yet. When the STLs exist, link them here and
     add print settings (layer height, infill, supports, orientation, material). -->

> **The case files aren't published yet.** Everything below the enclosure works today —
> the software half is done and running. If you want to build one now, any box that fits
> a drive and a board and gets power to both will do.

1. **Print the case.** *(files to come)*
2. **Wire it** as above. Power to both, drive data to the board's USB port.
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
