"""
Headless provisioning for Raspberry Pi OS Trixie and later: cloud-init seed files.

## Why this exists, 2026-10-03

Raspberry Pi OS Bookworm configured a headless card from `custom.toml` on the boot
partition, read at first boot by raspberrypi-sys-mods' `init_config`. **Trixie removed
`init_config`.** It ships cloud-init instead, reading the NoCloud seed -- `user-data`,
`network-config` and `meta-data` -- from `/boot/firmware`, and Raspberry Pi's own OS list
marks the image `init_format: cloudinit-rpi`. A card provisioned with only `custom.toml`
therefore boots to a stock image: no user, no Wi-Fi, no SSH, and nothing anywhere saying
why. Every Pi card written against a current image was that card.

So the FAT path now writes both. `custom.toml` stays, because Trixie ignores it and an
older Bookworm image a user supplies still reads it; the cloud-init files are what a
current image acts on.

## What is copied from Raspberry Pi Imager, and why copied

The shape of every file follows Raspberry Pi Imager 2.0.11's `customization_generator.cpp`
and `downloadthread.cpp`, read for this rather than remembered. Imager is the only writer
Raspberry Pi tests against its own images, and each oddity below is one of its fixes:

  * singular `user:`, so the account inherits the distro default user's groups (sudo,
    video, cdrom...) instead of becoming a group-less account (Imager #1601);
  * `runcmd: systemctl enable --now ssh`, because cloud-init writes keys but never
    starts sshd, and Raspberry Pi OS ships it disabled;
  * the Wi-Fi country in `network-config` *and* on the kernel command line, which is
    what lifts the radio's rfkill block on a fresh image;
  * a `meta-data` instance-id that is unique per write, mirrored onto the command line
    as `ds=nocloud;i=...`, so cloud-init treats the card as new and does not re-run
    discovery on every boot.

One deliberate difference: `disable_root: false`. Riparr's Setup step logs in as root
with the Preparer's key -- the same as on Armbian -- and the image's cloud.cfg default of
`disable_root: true` would write that key into root's authorized_keys behind a "please
log in as the user" refusal.

The source of truth is the Preparer's own custom.toml, parsed back the way armbian.py
does it, so the privileged writer's command line did not have to change on any of the
three operating systems.
"""
import re
import time


def _toml_unescape(s):
    """Undo core._esc: backslash and double quote are the only escapes it writes."""
    out, i = [], 0
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s):
            out.append(s[i + 1])
            i += 2
            continue
        out.append(s[i])
        i += 1
    return "".join(out)


def _section(src, name):
    m = re.search(r"^\[%s\]\s*$(.*?)(?=^\[|\Z)" % re.escape(name), src, re.S | re.M)
    return m.group(1) if m else ""


def _str(body, key):
    m = re.search(r'^%s\s*=\s*"((?:[^"\\]|\\.)*)"\s*$' % re.escape(key), body, re.M)
    return _toml_unescape(m.group(1)) if m else None


def _bool(body, key, default=False):
    m = re.search(r"^%s\s*=\s*(true|false)\s*$" % re.escape(key), body, re.M)
    return (m.group(1) == "true") if m else default


def parse_custom_toml(src):
    """The fields cloud-init needs, out of the Preparer's custom.toml."""
    system, user = _section(src, "system"), _section(src, "user")
    ssh, wlan, locale = _section(src, "ssh"), _section(src, "wlan"), _section(src, "locale")
    km = re.search(r"^authorized_keys\s*=\s*\[(.*?)\]", ssh, re.M | re.S)
    keys = re.findall(r'"((?:[^"\\]|\\.)*)"', km.group(1)) if km else []
    return {
        "hostname": _str(system, "hostname") or "riparr",
        "user": _str(user, "name") or "riparr",
        "pw_hash": _str(user, "password") or "",
        "ssh_password_auth": _bool(ssh, "password_authentication", True),
        "authorized_keys": [_toml_unescape(k) for k in keys if k.strip()],
        "ssid": _str(wlan, "ssid") or "",
        "psk": _str(wlan, "password") or "",
        "hidden": _bool(wlan, "hidden", False),
        "country": (_str(wlan, "country") or "US").upper(),
        "keymap": _str(locale, "keymap") or "us",
        "timezone": _str(locale, "timezone") or "UTC",
    }


def yaml_quote(value):
    """A YAML double-quoted scalar. SSIDs are arbitrary bytes from the air and
    passwords hashes contain `$` and `/`; quoting everything is cheaper than deciding
    which of them YAML would have read as something else."""
    out = ['"']
    for ch in value:
        o = ord(ch)
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif o < 0x20 or o == 0x7f:
            out.append("\\x%02x" % o)
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def user_data(c):
    lines = [
        "#cloud-config",
        "# Written by the Riparr Preparer. Shaped after Raspberry Pi Imager 2.0's output;",
        "# see tools/preparer/rpi_cloudinit.py for what each part is for.",
        "manage_resolv_conf: false",
        "",
        "hostname: %s" % yaml_quote(c["hostname"]),
        "manage_etc_hosts: true",
        "timezone: %s" % yaml_quote(c["timezone"]),
        "keyboard:",
        "  model: pc105",
        "  layout: %s" % yaml_quote(c["keymap"]),
        "",
        "user:",
        "  name: %s" % yaml_quote(c["user"]),
        "  shell: /bin/bash",
    ]
    if c["pw_hash"]:
        lines += ["  lock_passwd: false", "  passwd: %s" % yaml_quote(c["pw_hash"])]
    else:
        lines += ["  lock_passwd: true"]
    if c["authorized_keys"]:
        lines.append("  ssh_authorized_keys:")
        lines += ["    - %s" % yaml_quote(k) for k in c["authorized_keys"]]
        # Top-level keys go to root as well as the user once disable_root is off.
        lines += ["", "disable_root: false", "ssh_authorized_keys:"]
        lines += ["  - %s" % yaml_quote(k) for k in c["authorized_keys"]]
    lines += [
        "",
        "ssh_pwauth: %s" % ("true" if c["ssh_password_auth"] else "false"),
        "",
        "runcmd:",
        "  - [ systemctl, enable, --now, ssh ]",
        "",
    ]
    return "\n".join(lines)


def network_config(c):
    if not c["ssid"]:
        return ""
    lines = [
        "network:",
        "  version: 2",
        # Imager keeps wired Ethernet working alongside Wi-Fi: a network-config replaces
        # the distro default, and a Pi 3B/4/5 on a cable would otherwise lose it.
        "  ethernets:",
        "    eth0:",
        "      dhcp4: true",
        "      dhcp6: true",
        "      optional: true",
        "  wifis:",
        "    wlan0:",
        "      dhcp4: true",
        "      regulatory-domain: %s" % yaml_quote(c["country"]),
        "      access-points:",
        "        %s:" % yaml_quote(c["ssid"]),
    ]
    if c["hidden"]:
        lines.append("          hidden: true")
    if c["psk"]:
        # The 64-hex PBKDF2 PSK core.derive_psk produced. netplan takes it as the raw
        # key, so the passphrase itself never touches the card.
        lines.append("          password: %s" % yaml_quote(c["psk"]))
    else:
        lines += ["          auth:", "            key-management: none"]
    lines += ["      optional: true", ""]
    return "\n".join(lines)


def build(toml_text, now_ms=None):
    """-> {"user-data", "network-config", "meta-data", "instance_id", "country"}."""
    c = parse_custom_toml(toml_text)
    iid = "riparr-%d" % (now_ms if now_ms is not None else int(time.time() * 1000))
    return {
        "user-data": user_data(c),
        "network-config": network_config(c),
        "meta-data": "instance-id: %s\n" % iid,
        "instance_id": iid,
        "country": c["country"],
    }


# Tokens this module owns on the kernel command line. Removed before adding, so writing
# the same card twice leaves one of each rather than two disagreeing.
_OWNED = re.compile(r"^(?:ds=nocloud(?:;.*)?|cfg80211\.ieee80211_regdom=\S*)$")


def patch_cmdline(cmdline, instance_id, country):
    """cmdline.txt is one line, and the firmware reads only the first."""
    first = (cmdline.splitlines() or [""])[0]
    tokens = [t for t in first.split() if not _OWNED.match(t)]
    if country:
        tokens.append("cfg80211.ieee80211_regdom=%s" % country)
    tokens.append("ds=nocloud;i=%s" % instance_id)
    return " ".join(tokens) + "\n"
