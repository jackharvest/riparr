# Riparr Flasher

An interactive SD-card wizard for building a Riparr appliance. It replaces the
"fill in Raspberry Pi Imager's customization dialog correctly" step with something that
cannot be filled in wrong.

```
./riparr-flash.py --assets ~/riparr-build
```

## What it does that Imager doesn't

**It knows the Pi Zero 2W is 2.4 GHz only.** A live CoreWLAN scan gives SSID, band, and
signal for every nearby network. 5 GHz networks are shown greyed out and **cannot be
selected**, with the reason stated inline. This is the single most common cause of a box
that never appears on the network, and the wizard makes it unrepresentable.

**It never writes a plaintext Wi-Fi password to the card.** The passphrase is converted to
a PBKDF2-HMAC-SHA1 PSK (the same derivation `wpa_passphrase(8)` performs) before it
touches the FAT32 partition.

**It hashes the account password correctly on macOS.** macOS `crypt(3)` does not support
`$6$` and *silently returns a DES hash* when asked for one — producing a card you cannot
log into at the console. The wizard uses passlib and refuses to run without it.

**It refuses to write to the wrong disk.** Only external, removable, physical media
between 4 GB and 70 GB is offered. Your boot drive is never in the list.

## It needs a real terminal

Arrow-key navigation, hidden password entry and the `sudo` prompt all require a TTY. Piped
stdin gets a clear message instead of a traceback. Run it from Terminal.app or iTerm — not
through an editor's command runner or a CI step.

For scripted use there is a no-prompt path:

```sh
export RIPARR_WIFI_PASSWORD='...'
./riparr-flash.py --ssid 'YourNetwork' --disk disk4 --yes
```

`sudo` still needs to authenticate, so even this wants an interactive shell unless
credentials are already cached.

## A launcher keeps the invocation short

Put the image, keys and venv in one directory and drop a two-line script beside them:

```sh
#!/bin/bash
cd "$(dirname "$0")"
exec ./.venv/bin/python /path/to/tools/flasher/riparr-flash.py --assets "$(pwd)" "$@"
```

Keep that directory somewhere durable — it holds the SSH private key and the account
password for every box you flash. A temp directory is the wrong home for it.

## Setup

```sh
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
./.venv/bin/python riparr-flash.py --assets /path/to/build/dir
```

CoreWLAN is optional. Without it the wizard degrades to `system_profiler` (band info, but
macOS redacts SSIDs unless Location Services is granted), then to saved networks via
`networksetup`, then to typing the name by hand. It always works; it is just less magical.

## The assets directory

| File | Purpose |
|---|---|
| `*.img.xz` | The OS image. Newest one is auto-selected; override with `--image`. |
| `riparr_key.pub` | Optional. Embedded into `authorized_keys` for passwordless SSH. |
| `user_password.txt` | Account password. Generated on first run if absent. |
| `ssh_config` | Optional. Printed in the success message if present. |

## Flags

| Flag | Effect |
|---|---|
| `--assets DIR` | Where the image, SSH key and password file live. Default: next to the script. |
| `--image PATH` | Use a specific `.img.xz`. |
| `--disk diskN` | Skip the picker. Still validated as external/removable/in-range. |
| `--toml-only` | Generate `custom.toml` without writing a card — for a card that's already flashed. |
| `--hostname` / `--user` | Defaults for the prompts. |

## How the provisioning works

The wizard writes `custom.toml` to the boot partition. On first boot,
`raspberrypi-sys-mods/firstboot` finds it, hands it to `init_config`, and applies
hostname, user, SSH, Wi-Fi and locale — then deletes it.

The schema here was verified against that parser rather than written from memory. One
detail worth knowing if you edit the generator: **`password_encrypted` defaults to `true`**
for both `[user]` and `[wlan]`, so supplying a plaintext value without explicitly setting
it to `false` will fail in a way that is hard to diagnose on a headless box.

## Status

Built for the Riparr hardware-validation phase and used to flash the first test card. It
provisions stock Raspberry Pi OS today; once a Riparr image exists it will point at that
instead, with no interface change.
