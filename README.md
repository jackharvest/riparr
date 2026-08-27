<div align="center">

<img src="server/static/img/riparr-mark.png" width="96" alt="">

# Riparr

**An *arr-style appliance for ripping Blu-rays and DVDs.**

Insert a disc. Walk away. It ends up on your library share, correctly named.

[Get started](#the-three-stages) · [User guide](docs/guide/README.md) · [Which drive to buy](docs/guide/01-what-you-need.md#which-drive) · [Troubleshooting](docs/guide/08-troubleshooting.md)

</div>

---

Sonarr manages TV. Radarr manages films. Lidarr manages music. **Riparr manages the
physical-to-digital step nobody automated well: getting discs off the shelf and into the
library.**

A small 3D-printed box houses an optical drive and a single-board computer. **One USB-C
cable** powers and runs the whole thing. There is no screen, no keyboard and no off
switch. Once it is set up, the entire interaction is:

> **insert disc → close tray → walk away → disc ejects when it is done**

The finished MKV lands on your network share, named the way Plex and Jellyfin expect. A
status LED on the box says what it is doing when you are not at a browser, and a web
interface at `riparr.local` says it in words when you are.

---

## The three stages

Riparr is three separate things and it is worth knowing which one you are in. They happen
in this order, once, and then you are done with all of them.

### 1 · Preparation — on your own computer, about 5 minutes

You run the **Preparer**, a small app you download from
[Releases](../../releases). It writes Riparr's operating system onto an SD card and puts
your Wi-Fi, your hostname and your account on it, so the box can join your network the
first time it is powered on. Nothing is typed into a terminal.

There is no screen on the box, so everything it needs to reach your network has to be on
the card *before* it boots. That is the whole reason this stage exists.

| Your computer | Write the card | Set the box up | Proven on real hardware |
|---|---|---|---|
| **macOS** | Yes | Yes | Yes |
| **Linux** | Yes | Yes | Not yet |
| **Windows** | Yes, except for images that keep their settings in a Linux filesystem — the app says so before it touches the card | Yes | Not yet |

Your computer asks for permission once — your password on macOS, polkit on Linux, UAC on
Windows — and that covers writing, provisioning and ejecting. If the Preparer can't write
a card from your system it says so on its first screen, before you choose anything.

You can also write the card with [Raspberry Pi
Imager](https://www.raspberrypi.com/software/) or
[balenaEtcher](https://etcher.balena.io/) and come back for setup — but the box has **no
Ethernet socket**, so a card written that way needs its Wi-Fi added by hand or it will
boot and never appear.
→ [Preparing the card, in detail](docs/guide/02-prepare-sd-card.md)

### 2 · Setup — the box installs itself, about 10 minutes unattended

Put the card in, plug the cable in, and the box boots and joins your Wi-Fi. The Preparer
finds it on the network and installs Riparr onto it over SSH — you watch a progress bar.
Then it hands you a browser window at `http://riparr.local:9797`, where a short wizard
asks for four things:

1. **A password** for the web interface
2. **MakeMKV** — accept its licence and press install. It is built on the device, which
   takes about half an hour and needs no attention
3. **Your library share** — Riparr finds the servers on your network, you pick a share,
   and it writes a real test file and reads it back before saving. A share it cannot
   write to fails *now*, in front of you, instead of at 3am on your first rip
4. **Where things go** — a folder for films, a folder for television. They can be on the
   same share or on two different machines

→ [First boot](docs/guide/03-first-boot.md) · [Connecting your library](docs/guide/04-connect-your-library.md)

### 3 · Using it — forever, from a browser or not at all

The daily loop needs no browser at all: put a disc in, take it out when it comes back.

The web interface is there for the times you want it. **Queue** shows what is happening
now, stage by stage, with real times rather than a bar that does not move. **History**
and **Discs** are the record of what has been ripped. **Settings** is five pages you will
mostly read once.

Riparr can also tell you when it is finished, or when it needs you, through **ntfy,
Discord, email or a webhook** — because a box whose entire promise is "walk away" needs a
way to reach somebody who did.

→ [Ripping discs](docs/guide/06-ripping-discs.md) · [Settings reference](docs/guide/07-settings-reference.md) · [LED card](docs/guide/led-reference.md)

---

## What you need

| | |
|---|---|
| **A board** | Orange Pi Zero 2W is the reference. Others are supported, some still marked beta |
| **An optical drive** | A USB one, or an internal one plus a bridge that **names** optical/ATAPI support. [This is the part people get wrong](docs/guide/01-what-you-need.md#which-drive) |
| **An SD card** | 32 GB is enough for DVDs. Blu-ray wants more, or write straight to your library instead |
| **A network share** | SMB. Any NAS, or a folder shared from a computer that is usually on |
| **4K UHD?** | That is a *drive* decision, not a setting. [Read this first](docs/guide/01-what-you-need.md#which-drive) |

---

## Status

**Riparr rips discs, end to end, on real hardware.** On 2026-08-26 a Blu-ray went in and a
verified MKV came out on the library share, unattended. Getting there took eleven
interface bugs in one session, all of them now fixed.

**Riparr updates itself, once you say so.** Both halves check for a new release, tell
you when there is one, and — on a click — download it, verify it against the checksum
published with the release, replace themselves and restart. Nothing installs on its own,
and neither half will install anything it cannot verify. The checking is on by default
and can be turned off in one place on each side.

**This is still pre-1.0.** It has run on one board, with one drive, against one NAS. The
parts most likely to surprise you are the ones that have met the fewest configurations:
other boards, other drives, and **card writing from Windows or Linux** — which is written
and checked but has not yet produced a card that booted a board. If you are the first to
try one, an issue with your OS version and card reader is genuinely useful.

**Known and deliberate:**

- **The MakeMKV beta key expires monthly.** That is GuinpinSoft's decision, not ours.
  Riparr fetches the current key, tells you when the one you have lapses, and makes
  replacing it one click.
- **No transcoding.** A board this size would take days and the result would be poor.
  Riparr can write to a watch folder instead, for Tdarr or Unmanic to pick up.
- **Adaptive streaming (D11) is designed, not built.** A rip is transferred once it is
  complete rather than as it is written. Writing *straight* to your library — which is
  faster than the card on the reference board, and removes the card as a size limit — is
  built and is a setting.

---

## Running it

```sh
# On the box, from a checkout
sudo bash tools/install.sh          # → http://riparr.local:9797

# On your own machine, to look at the interface
server/run.sh                       # → http://localhost:8000, simulated hardware
```

Off the board, `riparr/platform.py` reports a simulated system, a simulated drive with a
disc in it and discoverable shares, so the whole interface is exercisable on a laptop.

| Tool | For |
|---|---|
| [`tools/preparer/`](tools/preparer/README.md) | The Preparer — the app in stage 1 |
| `tools/find-riparr.sh` | Finding the box when `riparr.local` will not resolve |
| `sudo bash tools/card-report.sh` | Diagnosing a card that will not boot |
| [`tools/flasher/`](tools/flasher/README.md) | The Preparer's job from a terminal, for scripts and CI |

---

## Documents

**For users** — [`docs/guide/`](docs/guide/README.md) is the whole path in order, and
[troubleshooting](docs/guide/08-troubleshooting.md) is organised by what you actually
observed rather than by subsystem.

**For anyone working on it:**

| Where | What's in it |
|---|---|
| [`server/`](server/) | The appliance service — FastAPI + SQLite, one process |
| [`tools/preparer/`](tools/preparer/) | The desktop app that writes the card and sets the box up |
| [`packaging/`](packaging/) | systemd units and the first-boot provisioner |

The design notes, decision log and working journal are kept privately rather than
published. If a choice here looks arbitrary and you want the reasoning behind it, ask in
an issue — the answer exists, it just isn't a file in this repository.

---

## Support

Riparr is free and GPL-3.0. If it saved you a shelf of discs and you feel like it:
[**buy me a coffee**](https://buymeacoffee.com/jackharvest). That link is also the mug
icon in the top right of the web interface — the one and only ask anywhere in the product.

Bugs and ideas: [Issues](../../issues).

---

## Licence

**Riparr is [GPL-3.0](LICENSE)** — the same licence as Sonarr, Radarr, Lidarr, Prowlarr,
Readarr and Bazarr, and for the same reason: the interface derives from Sonarr's design
tokens, and copyleft propagates.

You may use, modify and redistribute Riparr. If you redistribute it, modified or not, you
must ship source under GPL-3.0 too.

**Third-party components:**

| | |
|---|---|
| Design tokens | [Sonarr](https://github.com/Sonarr/Sonarr) — GPL-3.0 |
| Themes | [theme.park](https://github.com/themepark-dev/theme.park) — MIT |
| Icons | [Font Awesome Free](https://fontawesome.com/license/free) — CC BY 4.0 · brand marks from [Simple Icons](https://simpleicons.org/) — CC0 |
| Wordmark face | [Russo One](server/static/fonts/RussoOne-OFL.txt) — SIL OFL 1.1 |
| Window shell | [pywebview](https://pywebview.flowrl.com/) — BSD-3-Clause |
| Disc reading | [MakeMKV](https://www.makemkv.com/) — proprietary, by GuinpinSoft. **Not distributed with Riparr**; fetched and accepted during setup |

Riparr is not affiliated with, endorsed by, or connected to Sonarr, Radarr, GuinpinSoft
or any other project named here. Those names and logos belong to their respective owners.

---

## AI usage

Riparr was written with substantial help from an AI coding assistant, working from my
design decisions and against real hardware. I am saying so plainly because I would want
to know, and because "was any of this written by a machine" is a fair question to ask of
any project you are about to run on your own network.

What that does and does not mean here:

- **Every decision is a human one.** I keep a decision log and a working journal, and
  each entry in them was settled by me, not generated. Where the reasoning was mine and
  the prose was not, the reasoning is still mine.
- **Nothing ships on the strength of looking correct.** The claims in this repository are
  the ones that were run: checksums verified against two independent sources, PSK
  derivation checked against the IEEE 802.11i reference vectors, the write test that
  reads a file back before a share is trusted — and the wrong turns are on the record
  too, alongside the right ones.
- **The bugs are mine.** If something here breaks your setup, that is on the person who
  shipped it. [Open an issue](../../issues).

If you would rather not run software written this way, that is entirely reasonable, and
this section is here so you can make that call before you install anything.
