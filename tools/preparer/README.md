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

**It knows the Pi Zero 2W is 2.4 GHz only.** A live CoreWLAN scan gives SSID, band and
signal for every nearby network. 5 GHz networks are listed but **cannot be selected**,
with the reason stated inline. This is the single most common cause of a box that never
appears on the network, and the preparer makes it unrepresentable rather than merely
documented.

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

## Status

Verified end to end on macOS 26.6.1, Apple Silicon: all screens render, the live scan
returns real networks with correct band classification, and the write guards reject
malformed device identifiers. **It has not yet written a physical card** — see
[`JOURNAL.md`](../../JOURNAL.md).
