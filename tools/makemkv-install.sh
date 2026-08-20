#!/bin/bash
# Builds and installs MakeMKV on the appliance, after tools/bootstrap.sh.
#
# makemkvcon links against libmakemkv.so.1 and libdriveio.so.0, which only exist
# in the OSS package -- so the OSS build cannot be skipped even though an official
# aarch64 makemkvcon binary ships in makemkv-bin.
#
# Usage:  ./makemkv-install.sh --accept-eula [--jobs N] [--srcdir DIR]
set -euo pipefail

# Run from a systemd unit there may be no HOME at all, and `set -u` turns that into a
# fatal error on this line -- before any argument is parsed, so even an explicit
# --srcdir cannot save it.
: "${HOME:=/root}"
export HOME

JOBS=2                      # a high -j risks OOM during C++ compilation
SRC="$HOME/makemkv"
ACCEPT=0
while [ $# -gt 0 ]; do
  case "$1" in
    --accept-eula) ACCEPT=1 ;;
    --jobs) JOBS="$2"; shift ;;
    --srcdir) SRC="$2"; shift ;;
    *) echo "unknown option: $1"; exit 2 ;;
  esac; shift
done

# Consent is checked BEFORE anything is unpacked or built. MakeMKV's EULA is an
# agreement between the user and GuinpinSoft, and this script does not enter into it
# on anyone's behalf (D14).
if [ "$ACCEPT" != "1" ]; then
  cat <<EOF

MakeMKV is proprietary software made by GuinpinSoft inc, with its own licence.

  Read it:  https://www.makemkv.com/eula/
            (also at makemkv-bin-*/src/eula_en_linux.txt once unpacked)

  Then re-run with --accept-eula to confirm you accept it.

This script does not accept the licence on your behalf, and unpacks nothing until
you have.

EOF
  exit 1
fi

[ -d "$SRC" ] || { echo "No such source directory: $SRC"; exit 2; }

# find, not globbing: an unmatched glob here previously left `ls -d` with no argument,
# which lists "." and set OSS/BIN to a directory that is not MakeMKV at all.
find_pkg() { find "$SRC" -maxdepth 1 -mindepth 1 -type d -name "makemkv-$1-*" | head -1; }

OSS=$(find_pkg oss)
BIN=$(find_pkg bin)
if [ -z "$OSS" ] || [ -z "$BIN" ]; then
  echo "Extracting tarballs in $SRC"
  archives=$(find "$SRC" -maxdepth 1 -type f -name 'makemkv-*.tar.gz' | sort)
  [ -n "$archives" ] || { echo "No makemkv-*.tar.gz found in $SRC"; exit 2; }
  while IFS= read -r f; do
    [ -s "$f" ] || { echo "$(basename "$f") is empty -- download it again"; exit 2; }
    tar xzf "$f" -C "$SRC" || { echo "Could not unpack $(basename "$f")"; exit 2; }
  done <<< "$archives"
  OSS=$(find_pkg oss)
  BIN=$(find_pkg bin)
  [ -n "$OSS" ] && [ -n "$BIN" ] || {
    echo "Both makemkv-oss and makemkv-bin are required; found:"
    echo "  oss: ${OSS:-none}"
    echo "  bin: ${BIN:-none}"
    exit 2; }
fi

VER=$(basename "$BIN" | sed 's/makemkv-bin-//')

LOG="$HOME/validation"; mkdir -p "$LOG"
echo "=== MakeMKV $VER · $(uname -m) · $(nproc) cores · $(free -m | awk '/Mem:/{print $2}') MB RAM ==="

# ── build dependencies ──
# This has to stand on its own. It is reachable from the web interface, where nobody
# has run bootstrap.sh and there may be no compiler on the box at all, and the failure
# mode without this is a C++ build dying on a missing header several minutes in.
#
# `time` is the GNU one, from its own package -- Debian does not ship /usr/bin/time by
# default, and bash's `time` keyword is not a substitute for `-v`. Missing it used to
# abort the build before make ever started.
DEPS="build-essential pkg-config libssl-dev libexpat1-dev zlib1g-dev
      libavcodec-dev libavutil-dev libavformat-dev time"
MISSING=""
for pkg in $DEPS; do
  dpkg -s "$pkg" >/dev/null 2>&1 || MISSING="$MISSING $pkg"
done
if [ -n "$MISSING" ]; then
  echo "=== 0. Build tools:$MISSING ==="
  if [ "$(id -u)" = 0 ]; then
    DEBIAN_FRONTEND=noninteractive apt-get update -qq
    # shellcheck disable=SC2086
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq $MISSING \
      || { echo "Could not install build tools:$MISSING"; exit 2; }
  else
    echo "Not running as root; install these first:"
    echo "    sudo apt-get install -y$MISSING"
    exit 2
  fi
fi

# Qt5 is deliberately absent: MakeMKV needs it only for the GUI, which --disable-gui
# skips. It is a large install for nothing on a headless box.

echo "=== 1. Build OSS libraries (libmakemkv, libdriveio) — the slow part ==="
cd "$OSS"
# --disable-gui avoids pulling in Qt5, which is a large and pointless install headless
./configure --disable-gui --prefix=/usr 2>&1 | tail -15

# Peak RSS here is the R1 measurement, so measure it when we can -- but never let the
# measurement be the reason the build does not happen.
TIME=""
[ -x /usr/bin/time ] && TIME="/usr/bin/time -v"
# stderr carries both the timing report and any compiler errors, which is what makes
# this file worth printing on failure.
$TIME make -j"$JOBS" 2> "$LOG/makemkv-oss-build.time" || {
    echo "BUILD FAILED — last lines of $LOG/makemkv-oss-build.time:"
    tail -25 "$LOG/makemkv-oss-build.time" | sed 's/^/    /'
    grep -iE "maximum resident|Elapsed" "$LOG/makemkv-oss-build.time" || true
    echo "If this was an OOM: re-run with --jobs 1, or confirm zram is active (free -h)."
    exit 1
}
grep -E "Maximum resident set size|Elapsed \(wall" "$LOG/makemkv-oss-build.time" || true
sudo make install
sudo ldconfig

echo "=== 2. Install the arm64 makemkvcon binary ==="
cd "$BIN"
# The Makefile gates on tmp/eula_accepted, produced by an interactive `less` prompt
# that cannot run headlessly. --accept-eula above is the user's acceptance.
mkdir -p tmp && echo accepted > tmp/eula_accepted
sudo make install

echo "=== 3. Verify ==="
which makemkvcon
ldd "$(which makemkvcon)" | sed 's/^/    /'
makemkvcon --version 2>&1 | head -3 | tee "$LOG/makemkv-version.txt"

echo
echo "=== 4. Peak memory of a real operation (R1 measurement) ==="
echo "With a disc inserted, run:"
echo "    /usr/bin/time -v makemkvcon -r info disc:0 2> $LOG/makemkv-info.time"
echo "    grep 'Maximum resident' $LOG/makemkv-info.time"
echo
echo "DONE. Build artifacts logged in $LOG"
