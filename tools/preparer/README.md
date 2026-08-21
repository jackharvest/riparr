# Riparr Preparer

A native macOS window that prepares an SD card for a Riparr appliance.

```sh
~/riparr-build/prepare
```

That's the whole invocation. No terminal interaction, no `sudo` prompt, no arrow-key
menus — macOS asks for your password once, in its own dialog, and that single
authorization covers writing the image, applying your settings and ejecting the card.

## What it is

A real `NSWindow` hosting a `WKWebView`, via PyObjC. The only new dependency is
`pyobjc-framework-WebKit` (51 KB) — WebKit itself already ships with macOS. There is no
Electron, no bundled browser, and nothing to download.

## What it does that Raspberry Pi Imager doesn't

**It knows which bands the board can actually reach.** A live CoreWLAN scan gives SSID,
band and signal for every nearby network, and anything the radio cannot join is listed
greyed out with the reason inline rather than silently offered. The board is an Orange Pi
Zero 2W — dual-band WiFi 5 — so 2.4 and 5 GHz are both offered and 5 GHz is preferred,
because upload is the binding constraint in the streaming design. Only 6 GHz is refused.

> Earlier versions of this file said the board was 2.4 GHz only and that 5 GHz could not
> be selected. That was true while the hardware was believed to be a Raspberry Pi Zero
> 2 W, whose radio genuinely is single-band. See `PI_BANDS` in `core.py`.

**It can take the Wi-Fi password out of your keychain.** A mistyped passphrase is the
most expensive mistake this tool allows: nothing detects it, the card writes perfectly,
the box boots perfectly, and it never appears — and the only recovery is writing the card
again. For any network macOS has saved, one button fills it in from the login keychain,
with the OS asking permission in its own dialog.

**It never writes a plaintext Wi-Fi password to the card.** The passphrase becomes a
PBKDF2-HMAC-SHA1 PSK — the same derivation `wpa_passphrase(8)` performs — before anything
touches the FAT32 partition, which is readable by anyone who picks up the card. Verified
against both IEEE 802.11i Annex H.4 reference vectors.

**It hashes the account password correctly.** macOS `crypt(3)` has no `$6$` support and
*silently returns a DES hash* when asked for one, producing a card you cannot log into at
the console — discoverable only after flashing and booting. The preparer uses passlib and
refuses to run without it.

**It refuses to write to the wrong disk — by classifying it, not by capping its size.**
There used to be a 4–70 GB filter here, and it was doing safety's job: the removability
test underneath it was an *or* (`Ejectable or RemovableMedia`) that a USB SSD passes on
the first term, so capacity was the only thing standing between the user and a photo
backup drive. It also hid every card this project recommends — `storage-sizing.md` says
128 or 256 GB for Blu-ray.

`classify_disk()` now separates the two flags. `RemovableMedia` is the SCSI
removable-medium bit — the *medium* leaves the *device*, which is what a card reader is —
while `Ejectable` only says the whole device can be detached, which every USB disk
claims. Add the bus protocol, the IOKit media icon macOS uses to draw an SD card, and a
Rufus-style score over the device name, and cards separate from drives. Drives are shown
behind **"Show other removable disks"** with the reason they were demoted, not hidden,
and writing to one is refused unless it was revealed and picked deliberately. Your
startup disk is never in the list at all. Prior art and the full rule: **D24**.

**It tells you what a card size actually buys.** Under D11 every size rips every disc,
4K included — what a bigger card buys is early eject and batch feeding. The panel on the
card screen is computed by `core.size_guide()` from the same constants as the rest of the
design, so it cannot drift away from `docs/design/storage-sizing.md`.

**It verifies the settings file after writing it.** A FAT32 write returning success is not
proof of a good file, and `custom.toml` is the difference between a box that joins your
network and a card that has to be redone.

## Layout

| File | Purpose |
|---|---|
| `app.py` | The window, and the JavaScript↔Python bridge |
| `core.py` | Wi-Fi scan, PSK derivation, `$6$` hashing, the `custom.toml` schema, disk guards |
| `writer.py` | Runs as root. Image write, provisioning, verification, eject |
| `ui/` | The interface |

**`core.py` is the single source of truth** for everything dangerous to get subtly wrong.
[`tools/flasher/riparr-flash.py`](../flasher/README.md) — the scripted path — delegates to
it rather than keeping a second copy, so the two cannot drift.

## The assets directory

Defaults to `~/riparr-build`. Pass `--assets DIR` to change it.

| File | Purpose |
|---|---|
| `*.img.xz` | The OS image. Newest is auto-selected. |
| `riparr_key.pub` | Optional. Embedded into `authorized_keys` for passwordless SSH. |
| `user_password.txt` | Account password. Generated on first run if absent. |

Keep that directory somewhere durable — it holds the SSH private key and the account
password for every box you flash. A temp directory is the wrong home for it.

## Setup

```sh
python3 -m venv .venv
./.venv/bin/pip install -r ../flasher/requirements.txt pyobjc-framework-WebKit
./.venv/bin/python app.py --assets ~/riparr-build
```

## If you need to script it

Use [`tools/flasher/`](../flasher/README.md) instead. It takes `--ssid`, `--disk` and
`--yes`, and shares this directory's `core.py`. It needs a TTY.

## Scenarios

[`docs/design/scenarios-preparer.md`](../../docs/design/scenarios-preparer.md) walks
thirty-five things a person does between opening the app and having a browser open on
their box, with a verdict against the code for each. Four are still open; the largest by
far is that there is no `.app` bundle.

## Platform

**This is a macOS application, and always has been.** It is not portable and nothing in
it is written to be: the window is `NSWindow` + `WKWebView` through PyObjC, the Wi-Fi
scan is CoreWLAN, disks come from `diskutil` and `ioreg`, elevation is `sudo -A` driven
by an `osascript` dialog, and the card is written with `dd` to `/dev/rdiskN`. The
appliance is Linux and the web interface is a browser, so **the Preparer is the only
component with an operating system in it** — which also makes it the only thing standing
between a non-Mac owner and a working Riparr.

| | |
|---|---|
| **Runs on** | macOS only |
| **Floor** | macOS 13 Ventura — set by CSS `color-mix()` (WebKit 16.2) in `ui/app.css`. macOS 12 works only with a current Safari, since WKWebView uses the system WebKit. **Derived from feature availability, not tested** — this has only ever run on macOS 26. |
| **Architecture** | Intel and Apple Silicon. Both Homebrew prefixes are searched; no arch-specific paths. |
| **Python** | 3.9, which is what macOS ships. Every module compiles under it, so no user-installed Python is needed. |
| **Verified on** | macOS 26.6.1, Apple Silicon |

### One tool macOS does not ship

`debugfs` (e2fsprogs) is **Homebrew-only**, and keg-only, so it is not even linked onto
`PATH`. It is needed because the Armbian image is a single ext4 partition and macOS
cannot mount ext4 — see `armbian.py`.

`xz` used to be here too, and is gone: both uses moved to the **stdlib `lzma` module**,
which links the same liblzma macOS already ships inside libarchive.

| | |
|---|---|
| `xz -dc` in the write pipeline | `lzma.open()`. Verified byte-identical on the real image — same SHA-256, same length. |
| `xz --robot --list` for the progress total | `core.uncompressed_size()` parses the `.xz` stream footer and index directly. Cross-checked against `xz --robot --list`: exact match. |

> **It is five times slower and that does not matter.** Measured on the real image:
> `xz -dc` 2.1 s, `lzma.open()` 10.5 s — 735 vs 147 MB/s. The SD card takes ~20 MB/s, so
> the decompressor keeps seven times the headroom it needs and the write stays gated on
> the card exactly as before.

`debugfs` worked for a year because the app is launched from a shell and inherits a
developer's `PATH`. `launchctl getenv PATH` is empty, so a Finder-launched `.app` gets the
launchd default — `/usr/bin:/bin:/usr/sbin:/sbin` — and it **disappears.** Bundling this
as an `.app`, the largest item on the backlog, would have broken card writing on the
machine it was developed on.

It resolves by absolute path now (`armbian.find_debugfs`) with `PATH` as a fallback
rather than the mechanism, and `core.missing_tools()` refuses the write **before** the
authorization dialog, with the reason and the command that fixes it.

`core.image_layout()` decides whether a given image needs `debugfs` at all by reading its
MBR through `lzma` — no external tool, so the check works on the machine being diagnosed.

> **`debugfs` has no stdlib equivalent**, and it is the hardest thing to carry to Windows.
> [`docs/design/cross-platform.md`](../../docs/design/cross-platform.md) sets out the
> three ways out, of which giving the image a FAT boot partition is the one that deletes
> the problem rather than porting it.

## Status

Verified end to end on macOS 26.6.1, Apple Silicon: all screens render, the live scan
returns real networks with correct band classification, and the write guards reject
malformed device identifiers. **It has not yet written a physical card** — see
[`JOURNAL.md`](../../JOURNAL.md).

---

## The second half: `finish.py`

Writing a card used to be where the Preparer stopped. Everything after it — waiting for
the box to appear, copying Riparr across, installing it — was a terminal session the
user had to know how to drive, which is exactly where an appliance stops feeling like
one.

`finish.py` performs that session instead of describing it. Six steps, each phrased
twice: once as a sentence for the window, once as the command it actually runs, in a
log behind a disclosure triangle. **It is deliberately not a console with buttons.**
Someone setting up a disc ripper should be told what is happening, and be *able* to
read the raw output — not obliged to.

| Step | What it does |
|---|---|
| `find` | Resolves `<host>.local`; if mDNS is silent, sweeps the subnet and offers the SSH key to every host with port 22 open. Only the box accepts it. |
| `connect` | Confirms the key works and the hostname matches |
| `copy` | `tar` over the open SSH connection — no rsync needed at either end |
| `bootstrap` | `tools/bootstrap.sh` |
| `install` | `tools/install.sh` — the long pole, minutes of pip on a 1 GB A53 |
| `verify` | Asks the service **from the Mac**, by name and by address |

That last step matters more than it looks: `install.sh` already checks `127.0.0.1` on
the box, which proves the process runs. It does not prove the address we are about to
put in front of the user works from *their* machine. Those are different claims.

No elevation and no password prompt — everything happens over SSH with the key the
card already carries. That is the whole reason it can be automatic.

**It does not need a card write to have happened.** `Finisher` takes a hostname, a port
and the SSH key from the build folder — none of which come from writing a card. That was
always true and was never reachable: until the welcome screen existed, the only route to
setup was finishing a write in the same sitting, so anyone who closed the app after
ejecting the card was stuck. **Set up a box that already has a card** on the welcome
screen is the same code path entered on its own.

Three ways in, one code path:

```sh
python3 finish.py --find-only            # just locate the box
python3 finish.py --host riparr          # the whole thing, with a live log
python3 finish.py --progress out.json    # what the GUI polls
```

**Host keys.** A freshly written card has a brand-new host key, and re-writing the card
changes it again — so pinning to your global `known_hosts` would fail on every reflash
with a man-in-the-middle warning that is both alarming and wrong. Instead there is a
`known_hosts` of our own next to the key, cleared for the target at the start of a run
(the card was *just* written; a new key is expected), then `accept-new`. Pinned for the
rest of the session, and a genuine mid-session substitution is still refused.

## Looking at a screen without driving the whole flow

```sh
~/riparr-build/prepare --shot setup --shot-out /tmp/setup.png
~/riparr-build/prepare --shot setup --eval "document.querySelectorAll('.task').length"
```

Screens: `welcome`, `card`, `card-other`, `connect`, `wifi`, `handoff`, `setup`, `done`,
`done-skipped`, `failed`.

Two things this had to work around, both of which look like bugs in the page and are
not:

- **`screencapture -l <windowid>` returns an empty backing store for an occluded
  window.** This uses WKWebView's own `takeSnapshotWithConfiguration:` instead, which
  renders regardless.
- **A WKWebView in a window that was never brought to the front does not run CSS
  animations.** `.screen` starts at `opacity: 0` and depends on `animation: rise …
  forwards` to appear, so the pane snapshots blank while the sidebar renders perfectly.
  `--shot` injects a stylesheet killing animation first, which also makes shots stable
  between runs.
