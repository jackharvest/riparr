#!/usr/bin/env bash
# Find the Riparr box on the local network when riparr.local does not resolve.
#
# mDNS is the normal way in, but it is not guaranteed: the Armbian image ships no
# avahi, so the responder is systemd-resolved, and that only answers if provisioning
# turned it on. A card written before that fix will boot fine and still be nameless.
#
# This sweeps the subnet, finds hosts with SSH open, and asks each one whether it
# accepts the Riparr key. Only the box the Preparer wrote will say yes. Everything
# else refuses the key -- which does leave a failed-auth line in their logs, so this
# is a deliberate tool, not something to run casually on a network you do not own.
#
#   tools/find-riparr.sh [subnet] [keyfile]
#   tools/find-riparr.sh 192.168.3 ~/riparr-build/riparr_key

set -uo pipefail

KEY="${2:-$HOME/riparr-build/riparr_key}"

if [ -n "${1:-}" ]; then
    NET="$1"
else
    IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null)
    [ -z "$IP" ] && { echo "No IPv4 address on en0/en1; pass the subnet explicitly." >&2; exit 1; }
    NET="${IP%.*}"
fi

[ -r "$KEY" ] || { echo "No readable key at $KEY" >&2; exit 1; }

echo "Sweeping ${NET}.0/24 for SSH, using $KEY"

hosts=$(mktemp); trap 'rm -f "$hosts"' EXIT

# Batched rather than 254-at-once: saturating the socket table produces false
# negatives, which read as "the board is not there" when it is.
for base in $(seq 0 31 223); do
    for i in $(seq $((base + 1)) $((base + 32))); do
        [ "$i" -gt 254 ] && continue
        ( nc -z -G 2 -w 2 "${NET}.$i" 22 2>/dev/null && echo "${NET}.$i" >> "$hosts" ) &
    done
    wait
done

n=$(wc -l < "$hosts" | tr -d ' ')
echo "$n host(s) with SSH open; testing the key against each"

found=""
while read -r ip; do
    if ssh -i "$KEY" -o BatchMode=yes -o StrictHostKeyChecking=no \
           -o UserKnownHostsFile=/dev/null -o ConnectTimeout=4 \
           -o LogLevel=ERROR "root@$ip" \
           'cat /etc/hostname 2>/dev/null; cat /boot/riparr.conf 2>/dev/null' \
           2>/dev/null | grep -q RIPARR_PORT; then
        echo
        echo "FOUND: root@$ip"
        ssh -i "$KEY" -o BatchMode=yes -o StrictHostKeyChecking=no \
            -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR "root@$ip" \
            'echo "  hostname: $(cat /etc/hostname)"
             echo "  uptime:   $(uptime -p 2>/dev/null || uptime)"
             echo "  wlan0:    $(ip -4 -br addr show wlan0 2>/dev/null || echo down)"
             echo "  mDNS:     $(resolvectl mdns wlan0 2>/dev/null || echo unknown)"
             grep RIPARR /boot/riparr.conf 2>/dev/null | sed "s/^/  /"'
        found="$ip"
        break
    fi
done < <(sort -t. -k4 -n "$hosts")

if [ -z "$found" ]; then
    echo
    echo "No host on ${NET}.0/24 accepted the Riparr key."
    echo "Either the board is not on this subnet, or it never joined the network."
    echo "Put the card back in the Mac and read the mount count -- see JOURNAL.md."
    exit 1
fi

echo
echo "  ssh -i $KEY root@$found"
