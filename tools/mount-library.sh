#!/bin/bash
# Mount every share a rip could be written to, so MakeMKV can write straight to it.
#
# Why this exists: on the reference board the SD card is the *slowest* thing in the
# pipeline. Measured, 600 MB, 2026-08-26:
#
#     write to the card         9.4 MB/s
#     write through this mount  18.0 MB/s
#     read back through it      17.8 MB/s
#
# So writing the rip straight to the NAS is not a workaround for a small card, it is
# roughly twice as fast -- and it removes the card as a size limit, which is the only
# reason a 22 GiB Blu-ray could not be ripped on a 32 GB card.
#
# Why *every* share: films and television can be sent to different machines. Mounting
# only the default one meant the second destination silently lost the fast path and
# staged every rip on the card, with nothing anywhere saying so. The default share
# keeps /srv/library -- that path is baked into installed unit files and into every box
# already running -- and each additional share is mounted at /srv/library-<id> beside
# it, matching platform.library_mount().
#
# Run by systemd as root via `ExecStartPre=+`, because mounting needs privileges the
# service account does not have and must never be given. Idempotent: an existing mount
# is left alone. Never fails the unit -- a box that will not start because a NAS is
# asleep is worse than a box that falls back to the card.
set -uo pipefail

DB="${RIPARR_DB:-/var/lib/riparr/riparr.db}"
MOUNT="${RIPARR_LIBRARY_MOUNT:-/srv/library}"
PY="${RIPARR_PYTHON:-/opt/riparr/.venv/bin/python}"

log() { echo "riparr-mount: $*"; }

[ -x "$PY" ] || PY=python3
command -v mount.cifs >/dev/null || { log "cifs-utils is not installed; skipping"; exit 0; }

# One line per share to mount: id<TAB>is_default<TAB>host<TAB>share<TAB>user<TAB>domain.
# Only shares something is actually configured to write to -- mounting a share nothing
# points at is pointless, and every mount is a socket to a NAS we would rather let
# sleep.
# Read-only, and not at all if the database is not there yet. This runs as root, and a
# plain sqlite3.connect() *creates* a missing file: on a fresh box apply-system.sh starts
# this unit before riparr.service has ever run, so the database came into existence
# empty and owned by root, and the service -- which runs as riparr -- died on its first
# write with "attempt to write a readonly database". Every fresh install since this unit
# was started at install time, on every platform.
[ -f "$DB" ] || { echo "riparr-mount: no database yet; skipping"; exit 0; }
ROWS=$("$PY" - "$DB" <<'PYEOF'
import json, sqlite3, sys

def setting(c, key, default=None):
    try:
        row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default
    except Exception:
        return default

try:
    c = sqlite3.connect("file:%s?mode=ro" % sys.argv[1], uri=True)
    c.row_factory = sqlite3.Row
    shares = {r["id"]: r for r in c.execute("SELECT * FROM shares")}
    default = c.execute(
        "SELECT id FROM shares ORDER BY is_default DESC, id LIMIT 1").fetchone()
except Exception:
    raise SystemExit(0)
if not shares or not default:
    raise SystemExit(0)
default_id = default["id"]

wanted = []
# Every destination kind in db.KINDS. Music was added after films and television, and a
# list that names them by hand is a list that silently stops mounting the next one.
for key in ("movie_share_id", "tv_share_id", "music_share_id"):
    sid = setting(c, key)
    sid = sid if sid in shares else default_id
    if sid not in wanted:
        wanted.append(sid)
if default_id not in wanted:
    wanted.append(default_id)

for sid in wanted:
    r = shares[sid]
    share = (r["path"] or "").strip("/").split("/")[0]
    user, dom = r["username"] or "", "-"
    for sep in ("\\", "/"):
        if sep in user:
            dom, _, user = user.partition(sep); break
    else:
        if "@" in user:
            user, _, dom = user.partition("@")
    if not (r["host"] and share):
        continue
    print("\t".join([str(sid), "1" if sid == default_id else "0",
                     r["host"], share, user or "-", dom or "-"]))
PYEOF
) || { log "could not read the shares"; exit 0; }

[ -n "$ROWS" ] || { log "no share configured; skipping"; exit 0; }

# Where a share's server actually is. mount.cifs asks DNS and nothing else, but the name
# people type is often a Windows network name -- `mothership`, `TOWER`, `DS920` -- that
# only answers a NetBIOS broadcast. smbclient asks both, so the share's Test button
# passed while this mount failed on every boot, silently, and the interface blamed a
# sleeping NAS. Found on the reference box 2026-10-12. So: DNS (which includes mDNS for
# .local), then NetBIOS, and mount by the address with the typed name kept in the UNC.
resolve() {
  local h="$1" a=""
  if [[ "$h" =~ ^[0-9.]+$ || "$h" == *:* ]]; then echo "$h"; return; fi
  a=$(getent ahostsv4 "$h" 2>/dev/null | awk 'NR==1{print $1}')
  if [ -z "$a" ]; then
    # A NetBIOS name query, asked directly. nmblookup would do it, but it lives in
    # samba-common-bin, which smbclient does not pull in and which brings Samba's whole
    # Python stack with it -- for one 50-byte broadcast.
    a=$("$PY" - "$h" 2>/dev/null <<'NBEOF'
import os, socket, struct, sys

def nbns(name, timeout=2.0):
    n = name.upper()[:15].ljust(15) + "\x00"
    enc = "".join(chr(0x41 + (ord(c) >> 4)) + chr(0x41 + (ord(c) & 15)) for c in n)
    tid = int.from_bytes(os.urandom(2), "big")
    q = (struct.pack(">HHHHHH", tid, 0x0110, 1, 0, 0, 0)
         + b"\x20" + enc.encode("latin-1") + b"\x00" + struct.pack(">HH", 0x20, 1))
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.settimeout(timeout)
    try:
        s.sendto(q, ("255.255.255.255", 137))
        while True:
            data, _ = s.recvfrom(1024)
            if len(data) < 62 or struct.unpack(">H", data[:2])[0] != tid:
                continue
            if not (data[2] & 0x80) or data[3] & 0x0F:     # not a response, or an error
                continue
            return socket.inet_ntoa(data[58:62])           # header 12 + name 34 + 10 + flags 2
    except OSError:
        return ""
    finally:
        s.close()

print(nbns(sys.argv[1]))
NBEOF
)
    # stderr: stdout is the answer, and the caller captures it.
    [ -n "$a" ] && log "$h is a network name, not a DNS one; found it at $a" >&2
  fi
  echo "$a"
}

while IFS=$'\t' read -r SID ISDEF HOST SHARE USER DOMAIN; do
  [ -n "${SID:-}" ] || continue
  if [ "$ISDEF" = "1" ]; then TARGET="$MOUNT"; else TARGET="$MOUNT-$SID"; fi

  if mountpoint -q "$TARGET"; then
    log "already mounted at $TARGET"; continue
  fi

  # The share row holds the password, so it is read here and written only to a 0600
  # file. Never on a command line: /proc/*/cmdline is world-readable.
  CRED=$(mktemp /run/riparr-cred.XXXXXX) || continue
  chmod 600 "$CRED"
  "$PY" - "$DB" "$SID" >> "$CRED" <<'PYEOF'
import sqlite3, sys
c = sqlite3.connect("file:%s?mode=ro" % sys.argv[1], uri=True); c.row_factory = sqlite3.Row
r = c.execute("SELECT * FROM shares WHERE id=?", (int(sys.argv[2]),)).fetchone()
user = (r["username"] or "") if r else ""
for sep in ("\\", "/"):
    if sep in user:
        _, _, user = user.partition(sep); break
else:
    if "@" in user:
        user = user.partition("@")[0]
print("username=%s" % user)
print("password=%s" % ((r["password"] if r else "") or ""))
PYEOF
  [ "$DOMAIN" != "-" ] && echo "domain=$DOMAIN" >> "$CRED"

  UID_R=$(id -u riparr 2>/dev/null || echo 0)
  GID_R=$(id -g riparr 2>/dev/null || echo 0)
  mkdir -p "$TARGET"

  # `soft` so a NAS that goes away returns an error instead of wedging the rip in
  # uninterruptible sleep -- D4 says the cable gets pulled, and a hung mount is the one
  # state this appliance cannot recover from on its own.
  #
  # `iocharset=utf8` because the kernel's default is not: Armbian builds cifs with
  # CONFIG_NLS_DEFAULT=iso8859-1, so every UTF-8 filename was read as Latin-1, one
  # character per byte. "Ein Münchner im Himmel" reached the NAS as "Ein MÃ¼nchner im
  # Himmel", and verification -- which goes through smbclient, in UTF-8 -- then could
  # not find the file it had just written and failed the rip.
  BASE="credentials=$CRED,uid=$UID_R,gid=$GID_R,file_mode=0664,dir_mode=0775,soft,noatime,iocharset=utf8"
  ADDR=$(resolve "$HOST")
  if [ -z "$ADDR" ]; then
    log "could not find $HOST by DNS or by network name — rips bound for it will stage on the card instead"
    rm -f "$CRED"; continue
  fi
  [ "$ADDR" != "$HOST" ] && BASE="$BASE,ip=$ADDR"

  DONE=0; ERR=""
  for VERS in 3.1.1 3.0 2.1; do
    # Keep the error: thrown away, every failure here looked like a NAS that was off.
    if ERR=$(mount -t cifs "//$HOST/$SHARE" "$TARGET" -o "$BASE,vers=$VERS" 2>&1); then
      log "mounted //$HOST/$SHARE at $TARGET (SMB $VERS, $ADDR)"; DONE=1; break
    fi
  done
  [ "$DONE" = "1" ] || log "could not mount //$HOST/$SHARE at $ADDR: ${ERR:-no error given} — rips bound for it will stage on the card instead"
  rm -f "$CRED"
done <<< "$ROWS"

exit 0
