# 1. What You Need

[← Guide index](README.md) · [Next: Prepare the SD card →](02-prepare-sd-card.md)

---

## Parts

| Part | Notes |
|---|---|
| **A supported board** | The **Orange Pi Zero 2W** (Allwinner H618) is the tested board. Riparr also runs on a family of boards in the same footprint — Banana Pi BPI-M4 Zero, Radxa Zero 3W/3E and others, marked *beta* until confirmed. See [board support](../design/board-support.md). Whichever you pick, the Preparer downloads the right OS for it. |
| **Optical drive** | See [which drive](#which-drive) below — this is the choice that matters most. |
| **microSD card** | 32GB is enough. See [which card](#which-card). |
| **USB-C power supply** | 30W for a Slim build, 100W PD for a Full build. |
| **Enclosure** | 3D printed. **PETG or ASA — not PLA.** A drive plus a Pi in a sealed box gets hot enough to soften PLA. |
| **WS2812 RGB LED** | ~$2. Not optional — it's the only way the box can tell you something failed. |

**[unresolved]** The published parts list and printable enclosure files aren't finalized.
Riparr Slim (5V only, smaller, simpler) and Riparr Full (5V + 12V, larger, cheaper drive)
are likely to ship as two builds.

## Which drive

**This is the expensive mistake to avoid.** Buying the wrong drive is not recoverable in
software.

| You want to rip | Drive |
|---|---|
| DVDs only | Any DVD drive. Slim (laptop-style) drives are 5V-only and make the build much simpler. |
| Blu-ray (1080p) | Any Blu-ray reader. |
| **4K / UHD Blu-ray** | **Specific models with specific firmware only.** Most Blu-ray drives cannot read UHD discs at all. |

**[unresolved]** A supported-drive list will be published before release. If you're buying
for UHD, wait for it.

Slim drives are 5V-only, which means no 12V rail, a simpler harness, and a smaller box.
Full-size 5.25" drives are cheaper and faster but need the 12V build. Both DVD and Blu-ray
come in slim form, so this is a size-and-simplicity choice, not a capability one.

## Which card

**32GB is genuinely enough — for DVD, Blu-ray, and 4K alike.**

Riparr streams each rip out to your library as it's created, so the card never fills up.
It cannot run out of space in normal use.

A larger card buys you exactly one thing: **the disc ejects sooner.** Riparr uses spare
room to rip at full speed and upload afterward, so you can load the next disc without
waiting.

| Card | What it's good for |
|---|---|
| **32 GB** | Perfect if you feed it one disc at a time. Handles every disc type. |
| **128 GB** | Load two Blu-rays back to back without waiting. |
| **256 GB** | Sit down with a stack and load six in an evening. |

**Buy for how you feed it, not for what you rip.**

Two card notes:

- **Get a high-endurance card** if you'll be doing Blu-rays regularly — SanDisk Max
  Endurance or Samsung Pro Endurance. Every disc writes ~46GB through the card, and
  ordinary cards aren't built for that much sustained writing. For DVDs only, any decent
  card is fine.
- **Do not buy an SD Express card.** They cost 3–4× more, these boards can't use the
  fast interface at all, and they run hotter inside the box.

---

[← Guide index](README.md) · [Next: Prepare the SD card →](02-prepare-sd-card.md)
