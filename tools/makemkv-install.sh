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

OSS=$(ls -d "$SRC"/makemkv-oss-*/ 2>/dev/null | head -1)
BIN=$(ls -d "$SRC"/makemkv-bin-*/ 2>/dev/null | head -1)
if [ -z "$OSS" ] || [ -z "$BIN" ]; then
  echo "Extracting tarballs in $SRC"
  cd "$SRC"; for f in makemkv-*.tar.gz; do tar xzf "$f"; done
  OSS=$(ls -d "$SRC"/makemkv-oss-*/ | head -1)
  BIN=$(ls -d "$SRC"/makemkv-bin-*/ | head -1)
fi
VER=$(basename "$BIN" | sed 's/makemkv-bin-//;s#/##')

if [ "$ACCEPT" != "1" ]; then
  cat <<EOF

MakeMKV is proprietary software with its own licence.

  Licence text: ${BIN}src/eula_en_linux.txt
  Read it, then re-run with --accept-eula to confirm you accept it.

This script does not accept the licence on your behalf.

EOF
  exit 1
fi

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
