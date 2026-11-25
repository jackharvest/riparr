#!/usr/bin/env bash
# Install or refresh the parts of Riparr that live outside /opt/riparr.
#
# Why this is its own script, 2026-08-29:
#
# There were two ways to get a new version onto a box and only one of them applied
# system changes. `tools/install.sh` copies the systemd units and helper scripts into
# place; the in-app updater (updater.install) swaps /opt/riparr, pip-installs and
# restarts the service -- it never touches /etc/systemd/system or /usr/local/lib. So a
# release that added a unit shipped the file and installed nothing, on every box that
# updated from the web page. The unit sat in /opt/riparr/packaging looking installed.
#
# That is the same shape as every bug in JOURNAL.md worth remembering: something that
# reads as implemented and does nothing. The fix is one definition with two callers --
# install.sh runs it, and the provisioning door (riparr-provision.path) runs it after
# an in-app update.
#
# Must be idempotent: it runs on every install and after every update.

set -euo pipefail

INSTALL_DIR="${RIPARR_INSTALL_DIR:-/opt/riparr}"
PKG="$INSTALL_DIR/packaging"
LIB=/usr/local/lib/riparr

[ "$(id -u)" = 0 ] || { echo "apply-system.sh must run as root" >&2; exit 1; }
[ -d "$PKG" ] || { echo "No packaging directory at $PKG" >&2; exit 1; }

say() { printf '  %s\n' "$*"; }

install -d -m 0755 "$LIB"

# ── helper scripts ──
# Every root-side helper the one-way doors call. Listed rather than globbed so a stray
# file in packaging/ cannot become a privileged script by being dropped there.
netwatch_changed=0
thermal_changed=0
for s in wifi-apply.sh usbhost-fix.sh makemkv-run.sh netwatch.sh dvdtools-install.sh \
         cdtools-install.sh thermal.sh; do
    [ -f "$PKG/$s" ] || continue
    if [ "$s" = netwatch.sh ] && ! cmp -s "$PKG/$s" "$LIB/$s"; then
        netwatch_changed=1
    fi
    if [ "$s" = thermal.sh ] && ! cmp -s "$PKG/$s" "$LIB/$s"; then
        thermal_changed=1
    fi
    install -o root -g root -m 0755 "$PKG/$s" "$LIB/$s"
done

# netwatch.sh looks for this beside itself. Not executable -- it is run as an argument
# to the interpreter, and a 0644 file is one fewer thing that can be invoked directly.
if [ -f "$PKG/netwatch-settings.py" ]; then
    install -o root -g root -m 0644 "$PKG/netwatch-settings.py" \
            "$LIB/netwatch-settings.py"
fi

# The MakeMKV installer and the manifest it reads go together, and both are root's.
# The installer used to be placed only by install.sh, so a web update never refreshed
# it; and it looked for the manifest at ../packaging beside itself -- which, run from
# here, is a directory that does not exist. With no manifest it had nothing to fetch,
# so installing MakeMKV from the web page worked only when the Preparer had already
# put the tarballs on the card.
#
# Copied rather than read out of /opt/riparr at install time, like every other part
# here: what root runs between provisions is root's own copy.
if [ -f "$INSTALL_DIR/tools/makemkv-install.sh" ]; then
    install -o root -g root -m 0755 "$INSTALL_DIR/tools/makemkv-install.sh" \
            "$LIB/makemkv-install.sh"
fi
if [ -f "$PKG/makemkv-manifest.json" ]; then
    install -o root -g root -m 0644 "$PKG/makemkv-manifest.json" \
            "$LIB/makemkv-manifest.json"
fi

# mount-library.sh lives in tools/ rather than packaging/, but it is a root-side helper
# like the rest and belongs under /usr/local/lib for the same reason: riparr-library
# used to execute it straight out of /opt/riparr, which the riparr account owns.
if [ -f "$INSTALL_DIR/tools/mount-library.sh" ]; then
    install -o root -g root -m 0755 "$INSTALL_DIR/tools/mount-library.sh" \
            "$LIB/mount-library.sh"
fi

# ── units ──
# Same argument: an explicit list, so this cannot install something unreviewed.
#
# riparr-library.service IS here as of 0.3.3, and its absence was a real bug: nothing
# had ever installed it, so no box mounted its share at boot. /srv/library did not even
# exist on the reference unit. That is what made a configured share read as permanently
# lost, with no way back from the interface short of deleting it and adding it again.
for u in riparr.service \
         riparr-makemkv.service riparr-makemkv.path \
         riparr-dvdtools.service riparr-dvdtools.path \
         riparr-cdtools.service riparr-cdtools.path \
         riparr-poweroff.service riparr-poweroff.path \
         riparr-reboot.service riparr-reboot.path \
         riparr-usbhost.service riparr-usbhost.path \
         riparr-wifi.service riparr-wifi.path \
         riparr-netwatch.service \
         riparr-thermal.service \
         riparr-provision.service riparr-provision.path \
         riparr-restart.service riparr-restart.path \
         riparr-remount.service riparr-remount.path \
         riparr-library.service; do
    [ -f "$PKG/$u" ] || continue
    install -m 0644 "$PKG/$u" "/etc/systemd/system/$u"
done

systemctl daemon-reload

# ── enable what should be running ──
# `enable` without `--now` for the doors: they are path units and start themselves when
# the path appears. The watchdog is a plain service and does want starting, but only if
# it is not already running -- restarting it during an outage would reset the
# escalation counter and start the clock again from zero.
for p in riparr-makemkv riparr-dvdtools riparr-cdtools riparr-poweroff riparr-reboot riparr-usbhost riparr-wifi \
         riparr-provision riparr-remount riparr-restart; do
    [ -f "/etc/systemd/system/$p.path" ] || continue
    systemctl enable --quiet "$p.path" 2>/dev/null || true
    systemctl start "$p.path" 2>/dev/null || true
done

# The library mount is a oneshot, not a door. Enabling it is what makes a share come
# back on its own after a reboot; starting it now is what makes it come back today,
# without the user having to reboot to get the fix they just installed.
if [ -f /etc/systemd/system/riparr-library.service ]; then
    systemctl enable --quiet riparr-library.service 2>/dev/null || true
    systemctl start riparr-library.service 2>/dev/null || true
    if mountpoint -q /srv/library 2>/dev/null; then
        say "library share mounted at /srv/library"
    else
        say "library share not mounted yet — add or test a share in the web interface"
    fi
fi

if [ -f /etc/systemd/system/riparr-netwatch.service ]; then
    systemctl enable --quiet riparr-netwatch.service 2>/dev/null || true
    if ! systemctl is-active --quiet riparr-netwatch.service; then
        systemctl start riparr-netwatch.service 2>/dev/null || true
        say "Wi-Fi watchdog started"
    elif [ "$netwatch_changed" = 1 ]; then
        # A running watchdog keeps the old script until it restarts, so a fix to it
        # reached no box until its next reboot. Restarting resets its escalation count,
        # which is why it is otherwise left alone -- but an update arrived over this
        # network, so there is no outage in progress to lose track of.
        systemctl restart riparr-netwatch.service 2>/dev/null || true
        say "Wi-Fi watchdog restarted with the new version"
    else
        say "Wi-Fi watchdog already running (left alone)"
    fi
fi

# The thermal guard: unlike the Wi-Fi watchdog it has no escalation to lose, so a new
# version simply replaces the old one.
if [ -f /etc/systemd/system/riparr-thermal.service ]; then
    systemctl enable --quiet riparr-thermal.service 2>/dev/null || true
    if [ "$thermal_changed" = 1 ] && systemctl is-active --quiet riparr-thermal.service; then
        systemctl restart riparr-thermal.service 2>/dev/null || true
        say "Thermal guard restarted with the new version"
    elif ! systemctl is-active --quiet riparr-thermal.service; then
        systemctl start riparr-thermal.service 2>/dev/null || true
        say "Thermal guard started"
    fi
fi

# DVD backups need dvdbackup and libdvdcss (see packaging/dvdtools-install.sh). Asked
# for rather than done here: this unit has a two-minute budget, and apt plus a compile
# that may have to wait for a MakeMKV build's apt lock does not fit in it. Only when
# /run/riparr exists -- creating it here as root would leave the service unable to
# write its own runtime directory. On a fresh install it does not exist yet, and the
# service asks for itself once it is running (backup.start).
if [ -f /etc/systemd/system/riparr-dvdtools.path ] && [ -d /run/riparr ]; then
    if ! command -v dvdbackup >/dev/null 2>&1 \
       || ! ldconfig -p 2>/dev/null | grep -q 'libdvdcss\.so\.2'; then
        touch /run/riparr/dvdtools.request
        say "DVD backup tools requested (they install in the background)"
    fi
fi

# Music CDs need cd-paranoia, flac and friends (packaging/cdtools-install.sh). Asked for
# the same way as the DVD tools and for the same reasons; only apt, so it is quick.
if [ -f /etc/systemd/system/riparr-cdtools.path ] && [ -d /run/riparr ]; then
    # Every always-installed tool, not just the first two: a release that adds an
    # encoder has to reach boxes that already have the others.
    for bin in cd-paranoia cd-info flac lame opusenc oggenc bsdtar; do
        if ! command -v "$bin" >/dev/null 2>&1; then
            touch /run/riparr/cdtools.request
            say "music CD tools requested (they install in the background)"
            break
        fi
    done
fi

# ── the hardware watchdog ──
# A box that freezes outright stays frozen until somebody pulls the plug -- found
# 2026-10-12, when the Orange Pi's Wi-Fi chip hung mid-rip and took the kernel with it:
# no network, no log after the hang, and nothing on the board was going to notice. Every
# supported board has a hardware watchdog; with systemd feeding it, a frozen box reboots
# itself within seconds and the interrupted rip carries on when the disc goes back in.
# 15 s is the longest both the Allwinner and the Broadcom watchdogs accept.
if [ -e /dev/watchdog ] || [ -e /dev/watchdog0 ]; then
    want='[Manager]
RuntimeWatchdogSec=15s
RebootWatchdogSec=10min'
    conf=/etc/systemd/system.conf.d/riparr-watchdog.conf
    if [ "$(cat "$conf" 2>/dev/null)" != "$want" ]; then
        mkdir -p /etc/systemd/system.conf.d
        printf '%s\n' "$want" > "$conf"
        systemctl daemon-reexec
        say "Hardware watchdog on: a frozen box reboots itself"
    fi
fi

# ── Bluetooth off on the Orange Pi's AW859A ──
# Riparr never uses Bluetooth, and on this chip it shares the radio with Wi-Fi. When the
# Wi-Fi firmware hung, the driver's own recovery tried to power-cycle the chip and was
# refused -- "can not power off, other module is on" -- because Bluetooth was holding it,
# so Wi-Fi never came back. Not starting hciattach is not enough: the driver's own
# sprdbt_tty powers the chip's Bluetooth side whenever the Bluetooth rfkill switch is
# unblocked, and the vendor unit unblocks everything ("power state:5" -- Wi-Fi and
# Bluetooth -- with no Bluetooth service running at all). So the radio is blocked as
# well. sunxi_addr still loads and Wi-Fi is still unblocked: the Wi-Fi side needs both.
if systemctl cat aw859a-bluetooth.service >/dev/null 2>&1; then
    want='[Service]
ExecStartPre=
ExecStartPre=/sbin/modprobe sunxi_addr
ExecStartPre=/usr/sbin/rfkill unblock wifi
ExecStartPre=/usr/sbin/rfkill block bluetooth
ExecStart=
ExecStart=/bin/true'
    conf=/etc/systemd/system/aw859a-bluetooth.service.d/riparr-no-bluetooth.conf
    if [ "$(cat "$conf" 2>/dev/null)" != "$want" ]; then
        mkdir -p "$(dirname "$conf")"
        printf '%s\n' "$want" > "$conf"
        systemctl daemon-reload
        rfkill block bluetooth 2>/dev/null || true
        say "Bluetooth off (it blocked Wi-Fi recovery)"
    fi
fi

# ── everything in /opt/riparr belongs to the service ──
# The web updater runs as that account. A folder in here that somebody else owns -- one
# copied in by hand as root, or that kept its owner from the machine it came from -- is
# one it cannot replace or clean up. -h so a symlink is re-owned, never followed.
owner=$(stat -c '%U:%G' "$INSTALL_DIR" 2>/dev/null || echo root:root)
if [ "${owner%%:*}" != root ] && [ "${owner%%:*}" != UNKNOWN ]; then
    if [ -n "$(find "$INSTALL_DIR" -xdev ! -user "${owner%%:*}" -print -quit 2>/dev/null)" ]; then
        chown -Rh "$owner" "$INSTALL_DIR" && say "Took back files in $INSTALL_DIR"
    fi
fi

say "System units are up to date"
