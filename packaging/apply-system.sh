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

# ── units ──
# Same argument: an explicit list, so this cannot install something unreviewed.
#
# riparr-library.service is deliberately NOT here. It exists in packaging/ but no
# installer has ever copied it, and it is absent from the reference box -- so adding it
# would start mounting shares on every existing unit as a side effect of a Wi-Fi fix.
# If it is meant to be live, that is its own change with its own testing.
for u in riparr.service \
         riparr-makemkv.service riparr-makemkv.path \
         riparr-poweroff.service riparr-poweroff.path \
         riparr-reboot.service riparr-reboot.path \
         riparr-usbhost.service riparr-usbhost.path \
         riparr-wifi.service riparr-wifi.path \
         riparr-netwatch.service \
         riparr-provision.service riparr-provision.path; do
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
         riparr-provision; do
    [ -f "/etc/systemd/system/$p.path" ] || continue
    systemctl enable --quiet "$p.path" 2>/dev/null || true
    systemctl start "$p.path" 2>/dev/null || true
done

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
