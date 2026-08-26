#!/bin/bash
# Mount the configured library share so MakeMKV can write straight to it.
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
# Run by systemd as root via `ExecStartPre=+`, because mounting needs privileges the
# service account does not have and must never be given. Idempotent: an existing mount
# is left alone. Never fails the unit -- a box that will not start because a NAS is
# asleep is worse than a box that falls back to the card.
set -uo pipefail

DB="${RIPARR_DB:-/var/lib/riparr/riparr.db}"
MOUNT="${RIPARR_LIBRARY_MOUNT:-/srv/library}"
PY="${RIPARR_PYTHON:-/opt/riparr/.venv/bin/python}"

log() { echo "riparr-mount: $*"; }

if mountpoint -q "$MOUNT"; then
  log "already mounted at $MOUNT"; exit 0
fi
[ -x "$PY" ] || PY=python3
command -v mount.cifs >/dev/null || { log "cifs-utils is not installed; skipping"; exit 0; }

# The share row holds the password, so it is read here and written only to a 0600 file.
# Never on a command line: /proc/*/cmdline is world-readable.
read -r HOST SHARE USER DOMAIN < <("$PY" - "$DB" <<'PYEOF'
import sqlite3, sys
try:
    c = sqlite3.connect(sys.argv[1]); c.row_factory = sqlite3.Row
    r = c.execute("SELECT * FROM shares ORDER BY is_default DESC, id LIMIT 1").fetchone()
except Exception:
    r = None
if not r:
    print(" ".join(["-"] * 4)); raise SystemExit
share = (r["path"] or "").strip("/").split("/")[0]
user, dom = r["username"] or "", "-"
for sep in ("\\", "/"):
    if sep in user:
        dom, _, user = user.partition(sep); break
else:
    if "@" in user:
        user, _, dom = user.partition("@")
print(r["host"] or "-", share or "-", user or "-", dom or "-")
PYEOF
) || { log "could not read the share"; exit 0; }

if [ "$HOST" = "-" ] || [ "$SHARE" = "-" ]; then
  log "no share configured; skipping"; exit 0
fi

CRED=$(mktemp /run/riparr-cred.XXXXXX) || exit 0
chmod 600 "$CRED"
trap 'rm -f "$CRED"' EXIT
"$PY" - "$DB" >>"$CRED" <<'PYEOF'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1]); c.row_factory = sqlite3.Row
r = c.execute("SELECT * FROM shares ORDER BY is_default DESC, id LIMIT 1").fetchone()
user = r["username"] or ""
for sep in ("\\", "/"):
    if sep in user:
        _, _, user = user.partition(sep); break
else:
    if "@" in user:
        user = user.partition("@")[0]
print("username=%s" % user)
print("password=%s" % (r["password"] or ""))
PYEOF
[ "$DOMAIN" != "-" ] && echo "domain=$DOMAIN" >> "$CRED"

UID_R=$(id -u riparr 2>/dev/null || echo 0)
GID_R=$(id -g riparr 2>/dev/null || echo 0)
mkdir -p "$MOUNT"

# `soft` so a NAS that goes away returns an error instead of wedging the rip in
# uninterruptible sleep -- D4 says the cable gets pulled, and a hung mount is the one
# state this appliance cannot recover from on its own.
BASE="credentials=$CRED,uid=$UID_R,gid=$GID_R,file_mode=0664,dir_mode=0775,soft,noatime"
for VERS in 3.1.1 3.0 2.1; do
  if mount -t cifs "//$HOST/$SHARE" "$MOUNT" -o "$BASE,vers=$VERS" 2>/dev/null; then
    log "mounted //$HOST/$SHARE at $MOUNT (SMB $VERS)"; exit 0
  fi
done
log "could not mount //$HOST/$SHARE — rips will stage on the card instead"
exit 0
