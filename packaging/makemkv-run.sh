#!/usr/bin/env bash
# Root half of the MakeMKV install. Started by riparr-makemkv.service, which is
# triggered by riparr-makemkv.path when the web service drops a request file.
#
# Why this exists: building MakeMKV installs apt packages and writes to /usr/local, so
# it needs root. The web service runs as `riparr` with NoNewPrivileges=yes, so it
# cannot become root and sudo cannot help it. Telling the user to open a terminal is
# the wrong answer for an appliance, so instead the unprivileged side gets exactly one
# capability: it can ask for this to run. It cannot say what runs, or with what
# arguments — that is fixed here, in a root-owned file the service account cannot write.
#
# THIS FILE MUST LIVE OUTSIDE /opt/riparr. That directory is owned by `riparr`; a root
# unit executing anything from it would let the service rewrite its own escalation path.
# install.sh puts it in /usr/local/lib/riparr/ as root:root 0755.
set -uo pipefail

RUN=/run/riparr
STATE="$RUN/makemkv.state"
LOG="$RUN/makemkv.log"
SCRIPT=/usr/local/lib/riparr/makemkv-install.sh

mkdir -p "$RUN"

# The web service polls these. World-readable on purpose: they carry progress, not
# secrets, and the reader is an unprivileged process.
say() {   # phase, progress (0..1), message, [detail]
  printf '{"phase":"%s","progress":%s,"message":%s,"detail":%s}\n' \
    "$1" "$2" \
    "$(python3 -c 'import json,sys;print(json.dumps(sys.argv[1]))' "$3")" \
    "$(python3 -c 'import json,sys;print(json.dumps(sys.argv[1]))' "${4:-}")" \
    > "$STATE.tmp"
  mv -f "$STATE.tmp" "$STATE"
  chmod 0644 "$STATE"
}

trap 'say error 0 "The install stopped unexpectedly." "See $LOG"' ERR

: > "$LOG"
chmod 0644 "$LOG"

# Where the Preparer leaves the tarballs. /root/makemkv is unreadable to the service
# account, which is exactly why this side has to do the looking.
SRC=""
for d in /root/makemkv /boot/makemkv /boot/firmware/makemkv /var/lib/riparr/makemkv; do
  if [ -f "$d/makemkv-oss-1.18.4.tar.gz" ] && [ -f "$d/makemkv-bin-1.18.4.tar.gz" ]; then
    SRC="$d"; break
  fi
done

if [ -n "$SRC" ]; then
  say downloading 0.15 "Using the copy already on this device" "$SRC"
else
  say downloading 0.15 "Downloading MakeMKV from makemkv.com" ""
fi

say building 0.35 "Building MakeMKV for this device" \
    "This is the long part — around half an hour on this board."

# --jobs 1: the C++ build is the memory risk (R1). Slower than -j2, far likelier to
# finish. The script verifies checksums and refuses to proceed on a mismatch.
if [ -n "$SRC" ]; then
  "$SCRIPT" --accept-eula --srcdir "$SRC" --jobs 1 >>"$LOG" 2>&1
else
  "$SCRIPT" --accept-eula --jobs 1 >>"$LOG" 2>&1
fi
rc=$?

if [ $rc -ne 0 ]; then
  say error 0 "MakeMKV didn't finish building." "$(tail -n 20 "$LOG")"
  exit $rc
fi

if command -v makemkvcon >/dev/null 2>&1 || [ -x /usr/local/bin/makemkvcon ]; then
  say done 1 "MakeMKV is installed." ""
else
  # The script exited 0 without producing a binary. Say so rather than reporting
  # success for something that is not there.
  say error 0 "The build finished but makemkvcon is missing." "$(tail -n 20 "$LOG")"
  exit 1
fi
