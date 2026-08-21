#!/bin/sh
# Riparr first-boot provisioning.
#
# Reads /boot/riparr.conf -- one KEY=value file the Preparer wrote onto the FAT boot
# partition -- and turns it into the system configuration a headless appliance needs:
# hostname, Wi-Fi, mDNS, log persistence and the SSH key that lets the Preparer back in.
#
# WHY THIS EXISTS ON THE BOARD RATHER THAN IN THE PREPARER (D25)
#
# It used to be the Mac's job. `tools/preparer/armbian.py` wrote eleven files straight
# into the image's ext4 root through `debugfs`, because the Armbian image is a single
# ext4 partition and macOS cannot mount ext4. That worked, and it is unportable: Windows
# cannot mount ext4 either and has no debugfs worth shipping.
#
# So the work moved to the only machine that was always going to be Linux. The Preparer
# now writes ONE file to a FAT partition -- something every operating system can do with
# no special tooling -- and the board configures itself. Same files, same contents,
# written by something that natively understands the filesystem it is writing to.
#
# Armbian's own autoconfig could not do this job: it is read from `/root/.not_logged_in_yet`
# (a rootfs path, so it needs ext4 access to place -- the exact problem), it has no
# hostname directive, it stores the Wi-Fi key in plaintext, and its SSH key option takes
# a URL, which needs the network we are trying to bring up.
#
# POSIX sh on purpose: Armbian minimal is not guaranteed to ship Python, and a first-boot
# provisioner that cannot run is a box that never appears on the network.
#
# Deliberately NOT `set -e`. A half-provisioned box is the worst outcome this script can
# produce -- Wi-Fi written but no SSH key, say, giving a box that joins the network and
# refuses the only credential that can reach it. Every step is independent, so run them
# all and report, rather than stopping at the first one that stumbles. `set -u` stays:
# an unset variable here is a bug in the script, not a condition to survive.
set -u

# Everything is written under $R. Empty on the board, which is the whole point; set to a
# scratch directory to exercise this without one. Guests of the running system --
# systemctl, hostnamectl -- are skipped when it is set, because they would be talking to
# the wrong machine.
R="${RIPARR_FIRSTBOOT_ROOT:-}"
sysctl_() { [ -n "$R" ] || systemctl "$@" 2>/dev/null || true; }

CONF=""
for c in "$R/boot/riparr.conf" "$R/boot/firmware/riparr.conf"; do
    [ -f "$c" ] && CONF="$c" && break
done
[ -n "$CONF" ] || { echo "riparr-firstboot: no riparr.conf on the boot partition"; exit 0; }

BOOTDIR=$(dirname "$CONF")
STAMP="$BOOTDIR/.riparr-provisioned"
[ -f "$STAMP" ] && { echo "riparr-firstboot: already provisioned"; exit 0; }

log() { echo "riparr-firstboot: $*"; }

# Parsed, never sourced.
#
# Two reasons, and the first one bit immediately. `. "$CONF"` treats the file as shell,
# so `RIPARR_WIFI_SSID=Harvest House` runs `House` as a command and the SSID is lost --
# and an SSID with a space in it is the common case, not the exotic one. The failure it
# produces is the worst one this product has: a card that writes perfectly, a box that
# boots perfectly, and a network it never joins.
#
# The second reason is that this file lives on a FAT partition that anybody holding the
# card can edit, and sourcing it would execute whatever they put there as root at boot.
#
# So: value is everything after the first `=`, verbatim to end of line. No quoting rules
# to get wrong on either side, and nothing is evaluated. Trailing CR is stripped, which
# matters because a Windows Preparer may well write this file with CRLF endings -- a
# stray \r turns a country code into a broken one and a PSK into a wrong one, silently.
conf_get() {
    sed -n "s/^[[:space:]]*$1=//p" "$CONF" 2>/dev/null \
        | tail -n 1 | tr -d '\r' | sed -e 's/[[:space:]]*$//'
}

HOSTNAME_=$(conf_get RIPARR_HOSTNAME); HOSTNAME_=${HOSTNAME_:-riparr}
COUNTRY=$(conf_get RIPARR_COUNTRY);    COUNTRY=${COUNTRY:-US}
SSID=$(conf_get RIPARR_WIFI_SSID)
PSK=$(conf_get RIPARR_WIFI_PSK)
HIDDEN=$(conf_get RIPARR_WIFI_HIDDEN); HIDDEN=${HIDDEN:-0}
SSHKEY=$(conf_get RIPARR_SSH_KEY)
TZ_=$(conf_get RIPARR_TIMEZONE)

log "provisioning as '$HOSTNAME_' from $CONF"

# ── hostname ─────────────────────────────────────────────────────────────────
mkdir -p "$R/etc"
echo "$HOSTNAME_" > "$R/etc/hostname"
cat > "$R/etc/hosts" <<EOF
127.0.0.1	localhost
127.0.1.1	$HOSTNAME_
::1		localhost ip6-localhost ip6-loopback
ff02::1		ip6-allnodes
ff02::2		ip6-allrouters
EOF
[ -n "$R" ] || hostnamectl set-hostname "$HOSTNAME_" 2>/dev/null || true

# ── Wi-Fi ────────────────────────────────────────────────────────────────────
# An unquoted psk is a 64-hex derived key, so no passphrase is stored here and none
# ever touched the card. The Preparer derives it with PBKDF2 before writing.
if [ -n "$SSID" ]; then
    mkdir -p "$R/etc/wpa_supplicant"
    {
        echo "ctrl_interface=DIR=/run/wpa_supplicant GROUP=netdev"
        echo "update_config=1"
        echo "country=$COUNTRY"
        echo ""
        echo "network={"
        printf '\tssid="%s"\n' "$SSID"
        if [ -n "$PSK" ]; then printf '\tpsk=%s\n' "$PSK"; else printf '\tkey_mgmt=NONE\n'; fi
        [ "$HIDDEN" = "1" ] && printf '\tscan_ssid=1\n'
        echo "}"
    } > "$R/etc/wpa_supplicant/wpa_supplicant-wlan0.conf"
    chmod 600 "$R/etc/wpa_supplicant/wpa_supplicant-wlan0.conf"

    # systemd-networkd is the stack on this image; Netplan renders to it.
    mkdir -p "$R/etc/systemd/network"
    cat > "$R/etc/systemd/network/10-wlan0.network" <<'EOF'
[Match]
Name=wlan0

[Network]
DHCP=yes
MulticastDNS=yes

[DHCPv4]
UseDomains=yes
EOF

    # wlan0 does not exist until the driver has loaded, and on this board that happens
    # after wpa_supplicant would otherwise be started. Drop the device requirement and
    # let it retry rather than fail once and stay failed.
    mkdir -p "$R/etc/systemd/system/wpa_supplicant@wlan0.service.d"
    cat > "$R/etc/systemd/system/wpa_supplicant@wlan0.service.d/10-riparr-retry.conf" <<'EOF'
[Unit]
# The stock unit binds to sys-subsystem-net-devices-wlan0.device, which is not there yet.
BindsTo=
After=

[Service]
Restart=always
RestartSec=5
EOF
    sysctl_ enable wpa_supplicant@wlan0.service
    sysctl_ enable systemd-networkd.service
fi

# ── mDNS, so <hostname>.local resolves without anyone configuring a thing ────
mkdir -p "$R/etc/systemd/resolved.conf.d"
cat > "$R/etc/systemd/resolved.conf.d/10-riparr-mdns.conf" <<'EOF'
[Resolve]
# Shipped commented out, so the default is whatever this build happens to choose.
# Being explicit removes that coupling: the box must answer to its own name.
MulticastDNS=yes
EOF
sysctl_ enable systemd-resolved.service

# ── logs that survive a pulled cable ─────────────────────────────────────────
# D4 says the power gets pulled. armbian-ramlog keeps the journal in RAM, so the boot
# that failed is exactly the one with no record of why.
# Rewrite through a temp file rather than `sed -i`: the -i flag is spelled differently
# on GNU and BSD, and an in-place edit interrupted halfway leaves the file truncated.
if [ -f "$R/etc/default/armbian-ramlog" ]; then
    sed 's/^ENABLED=.*/ENABLED=false/' "$R/etc/default/armbian-ramlog" \
        > "$R/etc/default/armbian-ramlog.riparr" \
        && mv "$R/etc/default/armbian-ramlog.riparr" "$R/etc/default/armbian-ramlog" \
        || rm -f "$R/etc/default/armbian-ramlog.riparr"
fi
mkdir -p "$R/etc/systemd/journald.conf.d"
cat > "$R/etc/systemd/journald.conf.d/10-riparr-persistent.conf" <<'EOF'
[Journal]
# "auto" means volatile until /var/log/journal exists. Say what is meant, and cap it so
# it cannot eat the card.
Storage=persistent
SystemMaxUse=64M
SystemMaxFileSize=16M
EOF
mkdir -p "$R/var/log/journal"

# ── the key the Preparer comes back in with ──────────────────────────────────
if [ -n "$SSHKEY" ]; then
    mkdir -p "$R/root/.ssh"
    chmod 700 "$R/root/.ssh"
    printf '%s\n' "$SSHKEY" > "$R/root/.ssh/authorized_keys"
    chmod 600 "$R/root/.ssh/authorized_keys"
    [ -n "$R" ] || systemctl enable ssh.service 2>/dev/null || systemctl enable sshd.service 2>/dev/null || true
fi

[ -n "$TZ_" ] && [ -z "$R" ] && timedatectl set-timezone "$TZ_" 2>/dev/null || true

# Armbian's interactive first-login wizard blocks an unattended box: it waits at the
# console for a human who is not there.
rm -f "$R/root/.not_logged_in_yet"

# ── take the credentials back off the FAT partition ──────────────────────────
# The PSK is a derived key rather than the passphrase, but FAT is readable by anyone who
# picks the card up, and it has done its job. Leave everything the running service still
# needs -- riparr.service reads this same file for the port.
if [ -n "$PSK" ] || [ -n "$SSHKEY" ]; then
    if sed -e '/^[[:space:]]*RIPARR_WIFI_PSK=/d' \
           -e '/^[[:space:]]*RIPARR_SSH_KEY=/d' "$CONF" > "$CONF.riparr"; then
        mv "$CONF.riparr" "$CONF" && log "removed the Wi-Fi key and SSH key from $CONF"
    else
        rm -f "$CONF.riparr"
        log "warning: could not strip credentials from $CONF"
    fi
fi

date -u +"%Y-%m-%dT%H:%M:%SZ" > "$STAMP" 2>/dev/null || : > "$STAMP"
log "done; rebooting into the configured system is not required"
