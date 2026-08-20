#!/bin/bash
# Run FIRST after SSH-ing in. Captures the volatile first-boot journal before
# it is lost, then makes logging persistent for every boot after this one.
set -uo pipefail
OUT=/home/riparr/validation
mkdir -p "$OUT"

echo "=== 1. Rescuing first-boot journal (volatile, lost on reboot) ==="
sudo journalctl -b -o short-precise > "$OUT/firstboot-journal.log" 2>&1
sudo dmesg > "$OUT/firstboot-dmesg.log" 2>&1
echo "  saved $(wc -l < "$OUT/firstboot-journal.log") journal lines"

echo "=== 2. Enabling PERSISTENT logging ==="
sudo mkdir -p /var/log/journal
sudo systemd-tmpfiles --create --prefix /var/log/journal
sudo mkdir -p /etc/systemd/journald.conf.d
sudo tee /etc/systemd/journald.conf.d/99-riparr.conf >/dev/null <<'EOF'
[Journal]
Storage=persistent
SystemMaxUse=200M
SystemMaxFileSize=20M
MaxRetentionSec=1month
ForwardToConsole=no
EOF
sudo systemctl restart systemd-journald
echo "  journald storage: $(sudo journalctl --header 2>/dev/null | grep -c 'File path: /var/log/journal' || echo 0) persistent files"

echo "=== 3. Hardware inventory ==="
{
  echo "### uname";        uname -a
  echo; echo "### model";  cat /proc/device-tree/model 2>/dev/null; echo
  echo; echo "### os";     cat /etc/os-release
  echo; echo "### arch";   dpkg --print-architecture
  echo; echo "### memory"; free -h
  echo; echo "### cpu";    lscpu 2>/dev/null | head -20
  echo; echo "### blocks"; lsblk -o NAME,SIZE,FSTYPE,MOUNTPOINT
  echo; echo "### disk";   df -h
  echo; echo "### usb";    lsusb 2>/dev/null || echo "usbutils not installed"
  echo; echo "### optical drives"; ls -la /dev/sr* /dev/cdrom 2>/dev/null || echo "NO OPTICAL DRIVE DETECTED"
  echo; echo "### wifi";   iwconfig 2>/dev/null | head -20; ip -br addr
  echo; echo "### sd card"; cat /sys/block/mmcblk0/device/name 2>/dev/null; \
        cat /sys/block/mmcblk0/device/cid 2>/dev/null
  echo; echo "### throttle"; vcgencmd get_throttled 2>/dev/null; vcgencmd measure_temp 2>/dev/null
} > "$OUT/hardware-inventory.txt" 2>&1
cat "$OUT/hardware-inventory.txt"

echo "=== 4. Installing validation tools ==="
sudo apt-get update -qq
sudo apt-get install -y -qq usbutils strace hdparm sysstat ethtool iperf3 \
    build-essential pkg-config libssl-dev libexpat1-dev zlib1g-dev \
    libavcodec-dev libavutil-dev libavformat-dev \
    less vim curl wget git 2>&1 | tail -3
# NOTE: qtbase5-dev is deliberately absent. MakeMKV needs it only for the GUI,
# which we skip with ./configure --disable-gui. It is a large install for nothing.

echo "=== 5. Enabling zram (R1 mitigation: 512MB is tight) ==="
sudo apt-get install -y -qq zram-tools 2>&1 | tail -1
echo "ALGO=lz4" | sudo tee /etc/default/zramswap >/dev/null
echo "PERCENT=50" | sudo tee -a /etc/default/zramswap >/dev/null
sudo systemctl restart zramswap 2>/dev/null || true
free -h

echo
echo "=== BOOTSTRAP COMPLETE ==="
echo "Logs are now persistent across reboots. Artifacts in $OUT"
