#!/bin/bash
# Writes Raspberry Pi OS Lite to the SD card and applies custom.toml provisioning.
# Refuses to run against anything that is not a small, external, removable disk.
set -euo pipefail
cd "$(dirname "$0")"

DISK=${1:-disk4}
IMG=raspios-lite-arm64.img.xz

echo "==> Safety checks on /dev/$DISK"
INFO=$(diskutil info "/dev/$DISK")
grep -q "Device Location:.*External"     <<<"$INFO" || { echo "REFUSING: $DISK is not External"; exit 1; }
grep -qE "Removable Media:.*(Removable|Yes)" <<<"$INFO" || { echo "REFUSING: $DISK is not Removable"; exit 1; }
grep -q "Virtual:.*No"                   <<<"$INFO" || { echo "REFUSING: $DISK is virtual"; exit 1; }
SIZE=$(diskutil info -plist "/dev/$DISK" | plutil -extract TotalSize raw -)
(( SIZE > 8000000000 && SIZE < 70000000000 )) || { echo "REFUSING: $DISK is $SIZE bytes, outside 8-70GB"; exit 1; }
[ -f custom.toml ] || { echo "REFUSING: custom.toml not generated yet -- run gen_custom_toml.py first"; exit 1; }
echo "    OK: external, removable, physical, $((SIZE/1000000000))GB"
diskutil list "/dev/$DISK" | sed 's/^/    /'

echo
read -rp "Type ERASE to write the image to /dev/$DISK: " C
[ "$C" = "ERASE" ] || { echo "aborted"; exit 1; }

echo "==> Unmounting"
diskutil unmountDisk "/dev/$DISK"

echo "==> Writing image (several minutes; ctrl-T shows progress)"
xz -dc "$IMG" | sudo dd of="/dev/r$DISK" bs=4m
sync

echo "==> Waiting for boot partition to mount"
for i in $(seq 1 30); do
  BOOT=$(mount | awk '/msdos/ && /Volumes/ {print $3; exit}')
  [ -n "${BOOT:-}" ] && break
  sleep 2
done
[ -n "${BOOT:-}" ] || { echo "boot partition did not mount; replug the card"; exit 1; }
echo "    mounted at $BOOT"

echo "==> Applying custom.toml provisioning"
cp custom.toml "$BOOT/custom.toml"
ls -la "$BOOT/custom.toml"
echo "    verifying it parses as TOML:"
python3 - "$BOOT/custom.toml" <<'PY'
import sys
try:
    import tomllib; d=tomllib.load(open(sys.argv[1],'rb'))
except ModuleNotFoundError:
    import re; txt=open(sys.argv[1]).read()
    print("    (tomllib unavailable; sanity-checking keys)")
    for k in ["config_version","hostname","name","enabled","ssid","country"]:
        assert re.search(rf'^{k}\s*=', txt, re.M), f"missing {k}"
    print("    all required keys present"); sys.exit()
print("    parsed OK. hostname=%s user=%s ssh=%s ssid=%s country=%s" % (
    d['system']['hostname'], d['user']['name'], d['ssh']['enabled'],
    d['wlan']['ssid'], d['wlan']['country']))
PY

sync
echo "==> Ejecting"
diskutil eject "/dev/$DISK"
echo
echo "DONE. Put the card in the Pi and plug in USB-C. Allow ~2 minutes for first boot."
