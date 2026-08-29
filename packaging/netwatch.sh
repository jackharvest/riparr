#!/bin/bash
# Riparr Wi-Fi watchdog.
#
# Why this exists, measured on the reference box 2026-08-29:
#
# The Orange Pi Zero 2W's radio is a UNISOC AW859A driven by `sprdwl_ng` /
# `uwe5622_bsp_sdio` -- a vendor blob with no health reporting. When its firmware
# wedges it leaves the carrier asserted and the association nominally intact, so
# nothing upstream ever learns the link is dead:
#
#   * the box stayed up 17.5 hours with an unbroken hourly heartbeat
#   * the kernel logged NOTHING about wlan0 in a seven-day boot
#   * systemd-networkd logged 14 lines, all from boot -- no carrier loss, no
#     re-association, no DHCP complaint
#   * the only evidence was one line: networkd-wait-online timing out
#
# So there is no event to subscribe to and nothing to react to. The only way to know
# the radio is dead is to send a packet and see whether anything comes back. Hence a
# poller rather than a hook.
#
# Escalation, cheapest first, because each rung costs more than the last:
#
#   1. restart wpa_supplicant   -- re-associates; fixes a lost/stale association
#   2. reload the driver module -- rebuilds firmware state; fixes a wedged blob
#   3. reboot                   -- fixes whatever the first two did not
#
# A rung is only climbed after the one below it was given a fair chance to work.
#
# THE REBOOT NEVER INTERRUPTS A RIP. system.py:356 already argues that a machine
# deciding the moment is right to interrupt somebody's disc is not acceptable, and a
# watchdog is exactly the kind of machine that would. A rip in progress defers the
# reboot indefinitely -- the box is already unreachable, so waiting costs nothing that
# is not already lost, while rebooting would additionally destroy 40 minutes of work.

set -u

IFACE="${RIPARR_NETWATCH_IFACE:-wlan0}"
MODULE="${RIPARR_NETWATCH_MODULE:-sprdwl_ng}"

# Every INTERVAL seconds, one probe. 60s is a compromise: often enough that a dead
# radio is caught in minutes rather than hours, rare enough to be invisible.
INTERVAL="${RIPARR_NETWATCH_INTERVAL:-60}"

DB="${RIPARR_DB:-/var/lib/riparr/riparr.db}"
PY="${RIPARR_PYTHON:-/opt/riparr/.venv/bin/python}"
[ -x "$PY" ] || PY=python3
READER="$(dirname "$0")/netwatch-settings.py"

# Consecutive failures before each rung, read from the database on every probe so a
# change in the web interface takes effect within the minute with nothing to restart.
# One small query against a file already in page cache; a config-reload path would be
# more machinery than the thing it manages.
#
# These defaults match db.DEFAULTS and are what applies if the database cannot be read
# at all -- during first boot, say. A watchdog that refuses to run because it could not
# find a preference is worse than one running on sensible numbers.
#
# Deliberately not tighter than a few minutes: a router rebooting, or a firmware upgrade
# on the access point, is a normal event that resolves itself, and a watchdog that
# reboots the appliance over it is worse than the fault it is guarding against.
ENABLED=1
FAIL_ASSOC="${RIPARR_NETWATCH_FAIL_ASSOC:-3}"
FAIL_RELOAD="${RIPARR_NETWATCH_FAIL_RELOAD:-6}"
FAIL_REBOOT="${RIPARR_NETWATCH_FAIL_REBOOT:-12}"

load_settings() {
    [ -f "$READER" ] || return 0
    local out
    out="$("$PY" "$READER" "$DB" 2>/dev/null)" || return 0
    [ -n "$out" ] && eval "$out"
    return 0
}

log() { logger -t riparr-netwatch -p daemon.notice -- "$@"; echo "riparr-netwatch: $*"; }
warn() { logger -t riparr-netwatch -p daemon.warning -- "$@"; echo "riparr-netwatch: $*" >&2; }

# The gateway is looked up every probe rather than cached at start. A box that is
# carried between networks -- which is the whole point of the saved-network list --
# gets a different gateway at each one, and a cached address would make this report a
# permanent outage on a network that is working perfectly.
gateway() {
    ip route show default dev "$IFACE" 2>/dev/null |
        awk '/default/ {print $3; exit}'
}

# One ICMP echo, 5s. Success means the radio moved a packet and something answered,
# which is the only claim this script makes or needs.
#
# The gateway rather than the internet: an appliance that writes to a NAS on the LAN
# needs the LAN, and an ISP outage is not a reason to reboot. It is also the one host
# guaranteed to exist on every network the box is carried to.
probe() {
    local gw
    gw="$(gateway)" || return 1
    [ -n "$gw" ] || return 1
    ping -c 1 -W 5 -I "$IFACE" "$gw" >/dev/null 2>&1
}

# A rip is any live makemkvcon. Checked by process rather than by asking the Riparr
# API, because the API needs a session and this runs as a system daemon -- and because
# the process is the thing that would actually be destroyed by a reboot.
ripping() {
    pgrep -x makemkvcon >/dev/null 2>&1 || pgrep -f 'makemkvcon ' >/dev/null 2>&1
}

reassociate() {
    log "Re-associating: restarting wpa_supplicant@$IFACE."
    systemctl restart "wpa_supplicant@$IFACE.service" 2>/dev/null ||
        warn "Could not restart wpa_supplicant@$IFACE."
    # networkd re-runs DHCP on carrier, but only if it notices the carrier moved.
    networkctl reconfigure "$IFACE" >/dev/null 2>&1 || true
}

reload_driver() {
    if ! lsmod 2>/dev/null | grep -q "^${MODULE} "; then
        warn "Module $MODULE is not loaded; skipping the reload rung."
        return 1
    fi
    log "Reloading the $MODULE driver."
    # wpa_supplicant holds the interface; stop it first or the rmmod is refused and
    # the rung silently does nothing, which reads in the log as "the reload didn't
    # help" when in fact it never happened.
    systemctl stop "wpa_supplicant@$IFACE.service" 2>/dev/null || true
    modprobe -r "$MODULE" 2>/dev/null || warn "rmmod $MODULE was refused."
    sleep 2
    modprobe "$MODULE" 2>/dev/null || warn "modprobe $MODULE failed."
    sleep 5
    systemctl start "wpa_supplicant@$IFACE.service" 2>/dev/null || true
    networkctl reconfigure "$IFACE" >/dev/null 2>&1 || true
}

main() {
    local fails=0 did_assoc=0 did_reload=0 deferred=0

    load_settings
    log "Watching $IFACE. Probe every ${INTERVAL}s; re-associate after $FAIL_ASSOC, reload after $FAIL_RELOAD, reboot after $FAIL_REBOOT."

    while :; do
        sleep "$INTERVAL"
        load_settings

        # Switched off mid-outage means stop, not finish the ladder. Somebody turning it
        # off while watching the box misbehave is asking it to stop now, not to carry on
        # to the reboot it had already decided on.
        if [ "$ENABLED" != "1" ]; then
            if [ "$fails" -gt 0 ]; then
                log "Watchdog switched off; abandoning recovery."
                fails=0; did_assoc=0; did_reload=0; deferred=0
            fi
            continue
        fi

        if probe; then
            if [ "$fails" -gt 0 ]; then
                log "$IFACE answered again after $fails failed probe(s)."
            fi
            fails=0; did_assoc=0; did_reload=0; deferred=0
            continue
        fi

        fails=$((fails + 1))
        warn "No answer from the gateway on $IFACE ($fails consecutive)."

        # Rungs are guarded by did_* rather than by equality on the counter, so a rung
        # fires once per outage instead of once per matching count -- and a longer
        # outage does not re-run the cheap fix forever.
        if [ "$fails" -ge "$FAIL_REBOOT" ]; then
            if ripping; then
                # Logged once, not every minute: the outage is already the headline
                # and a repeated line would bury it.
                if [ "$deferred" -eq 0 ]; then
                    deferred=1
                    warn "Would reboot, but a rip is in progress. Holding until it finishes — the disc is worth more than the downtime."
                fi
                continue
            fi
            warn "Still dead after $fails probes and both recovery attempts. Rebooting."
            systemctl reboot
            # If the reboot is refused, do not spin on it every minute.
            sleep 300
            continue
        fi

        if [ "$fails" -ge "$FAIL_RELOAD" ] && [ "$did_reload" -eq 0 ]; then
            did_reload=1
            reload_driver
            continue
        fi

        if [ "$fails" -ge "$FAIL_ASSOC" ] && [ "$did_assoc" -eq 0 ]; then
            did_assoc=1
            reassociate
            continue
        fi
    done
}

main "$@"
