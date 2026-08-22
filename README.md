# Riparr

**An *arr-style appliance for ripping Blu-rays and DVDs.**

Sonarr manages TV. Radarr manages movies. Lidarr manages music. **Riparr manages the
physical-to-digital step nobody automated well: getting discs off the shelf and into the
library.**

A small 3D-printed box houses an optical drive and an Orange Pi Zero 2W. **One USB-C
cable** powers and runs the whole thing. You prepare the SD card in a small native app
(WiFi pre-configured, fully headless, no terminal), browse to `riparr.local`, set
credentials, run a short first-run wizard — and then never touch the settings again.

After that the loop is: **insert disc → close tray → walk away → disc ejects when done.**
No keyboard, no screen, no clicking. The finished MKV lands on your network share,
correctly named — **streamed out as it rips, so a 32GB card is all you ever need.**

---

## Start here

**→ Building or using one?** [**User Guide**](docs/guide/README.md) — SD card to first rip,
in order. Five minutes of actual effort.

**→ Working on Riparr itself?** [**DECISIONS.md**](DECISIONS.md) — every settled decision
and why. Then [**JOURNAL.md**](JOURNAL.md) — current state, next actions, and findings
that cost time to discover. Those two reload context fastest.

---

## Status

**Early implementation.** The SD preparer and the Riparr service both exist and run.
**The board boots.** On 2026-08-20 the Orange Pi Zero 2W came up on Armbian, joined Wi-Fi,
took a DHCP lease and synced its clock over the internet — about four minutes from
power-on. That is the first-boot path, end to end.

Getting there took discovering that **the board is an Orange Pi Zero 2W (Allwinner H618),
not a Raspberry Pi** — a different company's product with a nearly identical name, on
which Raspberry Pi OS cannot boot (D17). It then took a second session to notice it had
been working the whole time: `riparr.local` never resolved because the image ships no
avahi and mDNS was off on the link, and `armbian-ramlog` kept every log in a ramdisk that
a power pull erased. Both are fixed. [`JOURNAL.md`](JOURNAL.md) has the full account.

Last working session: **2026-08-20**

| | |
|---|---|
| **Prepare a card** | `~/riparr-build/prepare` — native macOS window, no terminal |
| **Set the box up** | the same window, once it's plugged in — no terminal either ([how](tools/preparer/README.md)) |
| **Find the box** | `tools/find-riparr.sh` — when `riparr.local` will not resolve |
| **Diagnose a card** | `sudo bash tools/card-report.sh` — **from Terminal.app** ([why](JOURNAL.md)) |
| **Install by hand** | copy the repo across, then `sudo bash tools/install.sh` |
| **Reach it** | `http://riparr.local:9797` — [why 9797](DECISIONS.md) |
| **Run it here** | `server/run.sh` → <http://localhost:8000> (mock mode off-board) |
| **Needs** | `brew install e2fsprogs` — the Preparer writes config into ext4 with `debugfs` |

**Riparr runs on the board, and is set up.** The whole path — write a card, plug it in,
let the Preparer find the box and install itself, then finish in a browser — works end
to end with no terminal at any point. MakeMKV installs from a button. Account, share
and first-run wizard are all done on real hardware.

**The one thing missing is a working USB-to-SATA adapter** — the one on hand had been
dead for a year. Nothing in the software or the board is outstanding. The bridge is a
BOM decision, not a cable: it must **name** optical/ATAPI support
([why](docs/design/hardware.md)).

**After that, the last open design risk** in
[`docs/design/risks.md`](docs/design/risks.md):

1. ~~**Does MakeMKV run on an Allwinner H618 with 1 GB?** (R1)~~ — **retired
   2026-08-20.** It builds and runs: 307 MB peak RSS, 3:57 wall clock, no swap touched.
2. **Does MakeMKV write MKV linearly?** (test 1b / R8) Gates the streaming design below,
   and is now the only unanswered design risk. Needs a disc and a working drive.

**What is not built:** the rip engine. Queue, history and disc history read real tables
that nothing populates yet — so **Auto Rip has nothing behind it**, even though the switch
and its readiness checks are real.

**The GitHub repo is not published yet**, so the install one-liner has nothing to fetch.
Installing from a local checkout works today.

## Documents

### For users
| Doc | What's in it |
|---|---|
| [`docs/guide/`](docs/guide/README.md) | The full setup-to-first-rip path, in order |
| [`docs/guide/led-reference.md`](docs/guide/led-reference.md) | Printable LED card |
| [`docs/guide/08-troubleshooting.md`](docs/guide/08-troubleshooting.md) | Organized by symptom, not by subsystem |
| [`tools/preparer/`](tools/preparer/README.md) | **SD preparer** — native macOS window. One authorization prompt, band-aware Wi-Fi scan, PSK derivation, disk guards, and headless provisioning for both Raspberry Pi and Allwinner card layouts |
| [`tools/flasher/`](tools/flasher/README.md) | The same thing for scripts and CI. Needs a TTY. |

### For developers
| Doc | What's in it |
|---|---|
| [`DECISIONS.md`](DECISIONS.md) | Locked decisions + rationale. **Read this first.** |
| [`JOURNAL.md`](JOURNAL.md) | Where we actually are, next actions, hard-won findings |
| [`docs/design/concept.md`](docs/design/concept.md) | Product vision, target UX, prior art, differentiator |
| [`docs/design/architecture.md`](docs/design/architecture.md) | Stack, partitions, the streaming rip pipeline, image build |
| [`docs/design/hardware.md`](docs/design/hardware.md) | BOM, power design, form-factor variants, thermal |
| [`docs/design/board-support.md`](docs/design/board-support.md) | Every board in the Zero 2W footprint, tiered by effort to support |
| [`docs/design/storage-sizing.md`](docs/design/storage-sizing.md) | What card size actually buys, all math shown |
| [`docs/design/risks.md`](docs/design/risks.md) | Known risks + the validation plan that retires them |
| [`docs/design/security.md`](docs/design/security.md) | Pen-test findings + threat model. **First pass remediated; re-review on real hardware pending.** |
| [`docs/design/backlog.md`](docs/design/backlog.md) | Feature ideas, roughly prioritized |
| [`docs/design/scenarios.md`](docs/design/scenarios.md) | Twenty-three layman scenarios walked against the code, and what they surfaced |
| [`docs/design/scenarios-preparer.md`](docs/design/scenarios-preparer.md) | The same walk for the Mac Preparer app |
| [`server/`](server/) | The appliance service — FastAPI + SQLite, *arr-style web UI |

---

## The 30-second summary of what's decided

- **OS:** **Armbian minimal (Debian trixie), 64-bit**, for `orangepizero2w` — Armbian
  ships only rolling community builds for this board, and there is no stable release to
  pin to (D17). aarch64 is the non-negotiable part; MakeMKV needs it
- **Stack:** Python / FastAPI + SQLite, single process, static frontend
- **Storage:** 3 partitions. Staging is isolated from rootfs so a stalled upload queue can
  never brick the box
- **Adaptive streaming (D11):** the uploader follows the file as MakeMKV writes it.
  **Any card size works — 32GB handles UHD.** A bigger card buys early eject and batch
  feeding, not throughput
- **No on-device transcoding.** Hand off to external workers
- **The bottleneck is WiFi, not the drive.** ~4 MB/s upload sets every throughput ceiling
  in the design — which is exactly why streaming is free

## Open questions carried into next session

1. Does `makemkvcon` actually run on aarch64? (R1) The board has **1 GB, not 512 MB**,
   and the arm64 binary is officially supported — so this is now a formality, not a blocker
2. Does MakeMKV write MKV linearly, or does it seek back to finalize headers? (**gates
   D11** — R8). If it rewrites, byte-level follow-copy dies and streaming falls back to
   title-level
3. Slim 5V-only drive vs. full-size 12V drive — this may fork the product into
   "Riparr Slim" and "Riparr Full" rather than "DVD" and "Blu-ray"
4. Verification read-back over WiFi costs as long as the upload — default-on or opt-in?
5. ~~Repo not yet `git init`'d~~ — done 2026-08-19
6. **D12 licensing** — the UI derives from Sonarr's GPL-3.0 theme tokens. Accept GPL-3.0,
   re-derive the palette, or drop to theme.park only? Exposure is deliberately confined to
   one file plus a handful of constants

---

## Support

Riparr is free and GPL-3.0. If it saved you a shelf of discs and you feel like it:
[**buy me a coffee**](https://buymeacoffee.com/jackharvest). That link is also the mug
icon in the top right of the web interface — the one and only ask anywhere in the
product.

---

## Licence

**Riparr is [GPL-3.0](LICENSE)** — the same licence as Sonarr, Radarr, Lidarr, Prowlarr,
Readarr and Bazarr, and for the same reason: the interface derives from Sonarr's design
tokens, and copyleft propagates. See [D12](DECISIONS.md).

You may use, modify and redistribute Riparr. If you redistribute it, modified or not, you
must ship source under GPL-3.0 too.

**Third-party components:**

| | |
|---|---|
| Design tokens | [Sonarr](https://github.com/Sonarr/Sonarr) — GPL-3.0 |
| Themes | [theme.park](https://github.com/themepark-dev/theme.park) — MIT |
| Disc reading | [MakeMKV](https://www.makemkv.com/) — proprietary, by GuinpinSoft. **Not distributed with Riparr**; fetched and accepted during setup ([D14](DECISIONS.md)) |

Riparr is not affiliated with, endorsed by, or connected to Sonarr, Radarr, or GuinpinSoft.
Those names and logos belong to their respective owners.
