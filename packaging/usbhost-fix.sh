#!/usr/bin/env bash
# Root half of "make both USB-C sockets work". Started by riparr-usbhost.service,
# which fires when the web service drops /run/riparr/usbhost.request.
#
# THIS FILE MUST LIVE OUTSIDE /opt/riparr -- that tree is owned by the riparr account,
# and a root unit executing from it would let the service run anything as root.
#
# The problem it solves: this board has two identical-looking USB-C sockets and only
# one can host a device. The other ships dr_mode="peripheral", which enumerates nothing
# and -- the part that costs people an evening -- logs nothing at all when you plug
# something in. A device-tree overlay makes it a host too, so either socket works and
# the trap stops existing.
set -uo pipefail

RUN=/run/riparr
STATE="$RUN/usbhost.state"
mkdir -p "$RUN"

say() {   # phase, message
  printf '{"phase":"%s","message":%s}\n' "$1" \
    "$(python3 -c 'import json,sys;print(json.dumps(sys.argv[1]))' "$2")" > "$STATE.tmp"
  mv -f "$STATE.tmp" "$STATE"; chmod 0644 "$STATE"
}
trap 'say error "The change did not complete. The box was left as it was."' ERR

BOOT=""
for d in /boot/firmware /boot; do
  [ -f "$d/armbianEnv.txt" ] && { BOOT="$d"; break; }
done
if [ -z "$BOOT" ]; then
  say error "This board does not use armbianEnv.txt, so Riparr cannot change it safely."
  exit 1
fi

# Only touch a controller that is actually a peripheral. Re-running must be harmless.
NODE=""
for n in /proc/device-tree/soc/usb@*; do
  [ -e "$n/dr_mode" ] || continue
  if [ "$(tr -d '\0' < "$n/dr_mode")" = peripheral ]; then
    NODE=$(basename "$n"); break
  fi
done
if [ -z "$NODE" ]; then
  say done "Both sockets can already host a drive. Nothing needed changing."
  exit 0
fi

say working "Reconfiguring the second USB-C socket"
cp -a "$BOOT/armbianEnv.txt" "$BOOT/armbianEnv.txt.riparr-bak"

cat > /root/usb-otg-host.dts <<DTS
/dts-v1/;
/plugin/;
/ {
    fragment@0 {
        target-path = "/soc/$NODE";
        __overlay__ {
            dr_mode = "host";
            status = "okay";
        };
    };
};
DTS

if ! armbian-add-overlay /root/usb-otg-host.dts >/dev/null 2>&1; then
  # Restore rather than leave a half-applied boot configuration behind.
  cp -a "$BOOT/armbianEnv.txt.riparr-bak" "$BOOT/armbianEnv.txt"
  say error "The overlay could not be compiled. Nothing was changed."
  exit 1
fi

sync
say rebooting "Restarting so the change takes effect. This takes about a minute."
sleep 3
systemctl reboot
