#!/bin/bash
# Builds and installs MakeMKV on the Pi. Run ON the Pi, after tools/bootstrap.sh.
#
# makemkvcon links against libmakemkv.so.1 and libdriveio.so.0, which only exist
# in the OSS package -- so the OSS build cannot be skipped even though an official
# aarch64 makemkvcon binary ships in makemkv-bin.
#
# Usage:  ./makemkv-install.sh --accept-eula [--jobs N] [--srcdir DIR]
set -euo pipefail

JOBS=2                      # 512MB RAM: high -j will OOM during C++ compilation
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

echo "=== 1. Build OSS libraries (libmakemkv, libdriveio) — the slow part ==="
cd "$OSS"
# --disable-gui avoids pulling in Qt5, which is a large and pointless install headless
./configure --disable-gui --prefix=/usr 2>&1 | tail -15
/usr/bin/time -v make -j"$JOBS" 2> "$LOG/makemkv-oss-build.time" || {
    echo "BUILD FAILED — check $LOG/makemkv-oss-build.time"
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
