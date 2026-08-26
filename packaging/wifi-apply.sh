#!/bin/bash
# Write out every saved Wi-Fi network, in priority order, and make wpa_supplicant
# reload.
#
# Why this exists: the box is meant to be carried. Taking it to somebody else's house
# means their SSID and password have to be in it *before* it gets there -- it has no
# screen and no keyboard, and you cannot type a password into a box that cannot reach
# the network your browser is on. One network is therefore the wrong shape; a phone has
# kept an ordered list since about 2007 and so does this.
#
# Run by systemd as root through riparr-wifi.path, because /etc/wpa_supplicant is not
# writable by the service account and must never become so. The request file that
# triggers it carries nothing: the list is read from the database, which only the web
# service writes and only behind authentication. So there is nothing here to parse from
# an untrusted source and nothing to trust.
#
# Never leaves the box off the network. If nothing associates, the previous config is
# put back -- a Wi-Fi change that strands a headless box is unrecoverable without a
# card reader.
set -uo pipefail

DB="${RIPARR_DB:-/var/lib/riparr/riparr.db}"
PY="${RIPARR_PYTHON:-/opt/riparr/.venv/bin/python}"
IFACE="${RIPARR_WIFI_IFACE:-}"
CONF_DIR=/etc/wpa_supplicant
STATE=/run/riparr/wifi.state
IMPORT=/run/riparr/wifi.merged.json

[ -x "$PY" ] || PY=python3

say() {   # phase, message, detail
  mkdir -p /run/riparr
  "$PY" - "$1" "$2" "${3:-}" > "$STATE" <<'PYEOF'
import json, sys, time
print(json.dumps({"phase": sys.argv[1], "message": sys.argv[2],
                  "detail": sys.argv[3], "at": int(time.time())}))
PYEOF
  chmod 0644 "$STATE" 2>/dev/null || true
  echo "riparr-wifi: $2 ${3:-}"
}

# exit 0 on failure: a unit stuck in `failed` helps nobody, and the interface reads the
# state file rather than the exit code.
fail() { say error "$1" "${2:-}"; exit 0; }

# The interface is whatever has a wireless directory, not a hardcoded wlan0 -- boards
# differ, and a renamed interface would mean silently writing a config nothing reads.
if [ -z "$IFACE" ]; then
  for d in /sys/class/net/*/wireless; do
    [ -e "$d" ] || continue
    IFACE=$(basename "$(dirname "$d")"); break
  done
fi
[ -n "$IFACE" ] || fail "No wireless interface on this box." ""

CONF="$CONF_DIR/wpa_supplicant-$IFACE.conf"
[ -f "$CONF" ] || fail "There is no wpa_supplicant config to update." "$CONF"

# Keep the regulatory domain that is already there. Losing country= costs the 5 GHz
# channels and does it silently, and this script has no business deciding it.
COUNTRY=$(sed -n 's/^country=\([A-Za-z][A-Za-z]\).*/\1/p' "$CONF" | head -1)
[ -n "$COUNTRY" ] || COUNTRY=US

say writing "Writing the network list" "$CONF"

NEW=$(mktemp "$CONF_DIR/.riparr-wpa.XXXXXX") || fail "Could not write to $CONF_DIR" ""
chmod 0600 "$NEW"
trap 'rm -f "$NEW"' EXIT

"$PY" - "$DB" "$COUNTRY" "$CONF" "$IMPORT" > "$NEW" <<'@I@'
import json, os, re, sqlite3, sys

db, country, conf, importfile = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
try:
    c = sqlite3.connect(db)
    row = c.execute("SELECT value FROM settings WHERE key='wifi_networks'").fetchone()
    nets = json.loads(row[0]) if row else []
except Exception:
    nets = []
if not isinstance(nets, list):
    nets = []


def existing(path):
    """Networks already in the live config, best priority first.

    This is how the network the Preparer wrote onto the card survives the first time
    anybody adds a second one. Without it, saving a friend's Wi-Fi would write a config
    containing only the friend's Wi-Fi -- and the box would come back up at home,
    unable to see any network it knows, with no screen to say so. That is the one
    failure this whole feature must not have.
    """
    try:
        with open(path) as f:
            text = f.read()
    except OSError:
        return []
    found = []
    for block in re.findall(r"network=\{(.*?)\}", text, re.S):
        m = re.search(r'^\s*ssid="(.*?)"\s*$', block, re.M)
        if not m:
            continue
        psk = re.search(r"^\s*psk=([0-9a-fA-F]{64})\s*$", block, re.M)
        pri = re.search(r"^\s*priority=(-?\d+)\s*$", block, re.M)
        found.append({"ssid": m.group(1).replace('\\"', '"').replace("\\\\", "\\"),
                      "psk": psk.group(1) if psk else "",
                      "_pri": int(pri.group(1)) if pri else 0})
    found.sort(key=lambda n: -n["_pri"])
    return found


# The database is the order the user chose; anything the config knows about and the
# database does not goes on the end rather than being dropped.
known = {n.get("ssid") for n in nets if isinstance(n, dict)}
adopted = []
for n in existing(conf):
    if n["ssid"] in known:
        continue
    known.add(n["ssid"])
    adopted.append({"ssid": n["ssid"], "psk": n["psk"], "open": not n["psk"]})
nets = list(nets) + adopted

# Hand the merged list back to the service, which owns the database file. Writing to
# the database from a root process would leave root-owned -wal and -shm files beside it
# and lock the service out of its own data.
#
# The keys travel with it, because a name without its key is worse than useless here:
# the service would adopt the home network as an *open* one and the next apply would
# write `key_mgmt=NONE` for it. 0640 root:riparr -- the service can read it, nothing
# else can, and it holds nothing the database does not already hold.
try:
    with open(importfile, "w") as f:
        json.dump([{"ssid": n.get("ssid"), "psk": n.get("psk") or ""}
                   for n in nets if isinstance(n, dict) and n.get("ssid")], f)
    os.chmod(importfile, 0o640)
    try:
        import grp
        os.chown(importfile, 0, grp.getgrnam("riparr").gr_gid)
    except Exception:
        pass
except OSError:
    pass

out = ["ctrl_interface=DIR=/run/wpa_supplicant GROUP=netdev",
       "update_config=1",
       "country=%s" % country, ""]

# Higher priority wins, and the list arrives in the order the user put it in, so the
# first entry gets the largest number. Spaced by ten so a later "nudge this one up" has
# somewhere to go without renumbering everything.
seen = set()
rank = 10 * len(nets)
for n in nets:
    ssid = (n.get("ssid") or "").strip()
    if not ssid or ssid in seen:
        continue
    seen.add(ssid)
    body = ['\tssid="%s"' % ssid.replace("\\", "\\\\").replace('"', '\\"')]
    psk = (n.get("psk") or "").strip()
    if psk:
        # Unquoted: a bare 64-hex value is the PSK itself, so no passphrase is ever
        # written to disk.
        body.append("\tpsk=%s" % psk)
    else:
        body.append("\tkey_mgmt=NONE")
    # A hidden network is not in a passive scan and is never joined without this.
    body.append("\tscan_ssid=1")
    body.append("\tpriority=%d" % rank)
    rank -= 10
    out.append("network={")
    out.extend(body)
    out.append("}")
    out.append("")

if not seen:
    raise SystemExit(3)          # refuse to write a config with no networks in it
print("\n".join(out))
PYEOF
rc=$?
if [ "$rc" != "0" ]; then
  if [ "$rc" = "3" ]; then
    fail "There are no saved networks, so nothing was changed." \
         "Riparr will not write a Wi-Fi config with no networks in it."
  fi
  fail "Could not read the saved networks." "exit $rc"
fi

BACKUP="$CONF.riparr-previous"
cp -a "$CONF" "$BACKUP" 2>/dev/null || true
cat "$NEW" > "$CONF"
chmod 0600 "$CONF"; chown root:root "$CONF"

say joining "Reloading Wi-Fi" "$IFACE"

# reconfigure re-reads the file in place, which keeps an existing association if it is
# still the best one. Restarting the unit is the fallback for a supplicant whose
# control socket cannot be reached.
if ! (command -v wpa_cli >/dev/null && wpa_cli -i "$IFACE" reconfigure >/dev/null 2>&1); then
  systemctl restart "wpa_supplicant@$IFACE.service" >/dev/null 2>&1 || true
fi

# Give it time to associate, then say what actually happened rather than "reloaded".
# The difference matters: this is the one setting that can put the box somewhere the
# browser cannot follow.
JOINED=""
for _ in $(seq 1 20); do
  sleep 1
  JOINED=$(iw dev "$IFACE" link 2>/dev/null | sed -n 's/^[[:space:]]*SSID: //p' | head -1)
  [ -n "$JOINED" ] && break
done

if [ -n "$JOINED" ]; then
  say done "Connected to $JOINED" ""
else
  cp -a "$BACKUP" "$CONF" 2>/dev/null || true
  wpa_cli -i "$IFACE" reconfigure >/dev/null 2>&1 || \
    systemctl restart "wpa_supplicant@$IFACE.service" >/dev/null 2>&1 || true
  say error "None of the saved networks answered, so the previous settings were put back." \
      "Check the password, and that the network is in range -- and on 2.4 GHz if this board has no 5 GHz radio."
fi
exit 0
