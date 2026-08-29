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
for s in wifi-apply.sh usbhost-fix.sh makemkv-run.sh netwatch.sh; do
    [ -f "$PKG/$s" ] || continue
    install -o root -g root -m 0755 "$PKG/$s" "$LIB/$s"
done

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
         riparr-poweroff.service riparr-poweroff.path \
         riparr-reboot.service riparr-reboot.path \
         riparr-usbhost.service riparr-usbhost.path \
         riparr-wifi.service riparr-wifi.path \
         riparr-netwatch.service \
         riparr-provision.service riparr-provision.path \
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
for p in riparr-makemkv riparr-poweroff riparr-reboot riparr-usbhost riparr-wifi \
         riparr-provision riparr-remount; do
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
    else
        say "Wi-Fi watchdog already running (left alone)"
    fi
fi

say "System units are up to date"
