#!/usr/bin/env bash
# Read a Riparr card's evidence directly, without mounting it.
#
# macOS cannot mount ext4, but debugfs reads it without a mount and without a kernel
# extension. This is how you find out what the board actually did.
#
# MUST be run from Terminal.app (or another terminal with Full Disk Access): macOS TCC
# gates removable-volume reads on the *calling application*, so from anywhere else
# debugfs returns "Operation not permitted" even under sudo.
#
#   sudo bash tools/card-report.sh [partition] [outfile]

set -uo pipefail

D=${DEBUGFS:-/opt/homebrew/opt/e2fsprogs/sbin/debugfs}
P=${1:-/dev/disk4s1}
OUT=${2:-/tmp/riparr-card-report.txt}

[ -x "$D" ] || { echo "debugfs not at $D -- brew install e2fsprogs" >&2; exit 1; }

cat() { command cat "$@"; }
dbg() { "$D" -R "$1" "$P" 2>&1; }

{
echo "=== card-report $(date) -- $P ==="
echo
echo "=== PARTITION TABLE ==="
echo "The image writes a 1.54 GB partition. Bigger than that means Armbian's"
echo "first-boot resize ran, which means the board booted."
diskutil list "${P%s*}"

echo
echo "=== SUPERBLOCK ==="
dbg "stats -h" | grep -iE "mount count|last mount|last write|created|block count|state|Filesystem" | head -20

echo
echo "=== DID OUR PROVISIONING SURVIVE? ==="
for f in /etc/hostname /etc/systemd/network/10-wlan0.network /boot/riparr.conf \
         /etc/systemd/resolved.conf.d/10-riparr-mdns.conf; do
  echo "--- $f"; dbg "cat $f"
done
echo "--- wpa_supplicant conf (ssid only; the PSK stays off the screen)"
dbg "cat /etc/wpa_supplicant/wpa_supplicant-wlan0.conf" | grep -i ssid

echo
echo "=== WHERE THE LOGS ACTUALLY ARE ==="
echo "Armbian mounts /var/log as a zstd ramdisk (armbian-ramlog) and syncs it down to"
echo "/var/log.hdd. So /var/log on the card is the pristine build-time copy and tells"
echo "you nothing -- /var/log.hdd is the real one, and it only has content if a sync"
echo "or a clean shutdown happened. Pulling the power loses everything since the last."
echo "--- /var/log (ramdisk mountpoint; expect build-time files only)"
dbg "ls -l /var/log" | head -30
echo "--- /var/log.hdd (the persisted copy)"
dbg "ls -l /var/log.hdd" | head -30

for L in /var/log.hdd/syslog /var/log.hdd/messages /var/log.hdd/daemon.log \
         /var/log.hdd/kern.log /var/log.hdd/armbian-firstrun.log; do
  echo
  echo "--- $L (tail)"
  dbg "cat $L" | tail -40
done

echo
echo "=== NETWORK LINES, ANY PERSISTED LOG ==="
echo "The question is whether wpa_supplicant ever started. sprdwl_ng is the Wi-Fi driver."
for L in /var/log.hdd/syslog /var/log.hdd/daemon.log /var/log.hdd/kern.log; do
  dbg "cat $L" 2>/dev/null | grep -iE "wlan0|wpa_suppl|sprdwl|uwe5622|dhcp|networkd|resolved|aw859a"
done | tail -60

echo
echo "=== PERSISTENT JOURNAL ==="
dbg "ls -l /var/log.hdd/journal" | head
} > "$OUT" 2>&1

echo "Wrote $OUT"
echo "Show it to Claude with:  cat $OUT"
