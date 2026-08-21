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

**It refuses to write to the wrong disk.** Only external, removable, physical media
between 4 GB and 70 GB is offered, and the chosen disk is re-validated immediately before
writing. Your startup disk is never in the list.

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
twenty-five things a person does between picking up a card and having a browser open on
their box, with a verdict against the code for each. Four are still open; the largest by
far is that there is no `.app` bundle.

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

Screens: `handoff`, `setup`, `done`, `done-skipped`.

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
