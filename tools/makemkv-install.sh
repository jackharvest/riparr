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

mkdir -p "$SRC" || { echo "Could not create $SRC"; exit 2; }

# ── fetching, when the tarballs are not already here ──
#
# This used to not exist, and the omission was invisible: makemkv-run.sh printed
# "Downloading MakeMKV from makemkv.com" and then ran this script, which looked in
# $SRC, found nothing and exited 2. The one path that reaches a first-time user
# through the web interface -- no Preparer copy on the card -- could not install
# MakeMKV at all, and announced a download while it did so.
#
# Sources and checksums come from packaging/makemkv-manifest.json, the same file the
# service reads, so the two can never disagree. Sources are tried in order and the
# first whose bytes match the pinned sha256 wins; a mirror serving the wrong file is
# rejected by the hash rather than trusted because it answered.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MANIFEST="${RIPARR_MAKEMKV_MANIFEST:-$HERE/../packaging/makemkv-manifest.json}"

manifest_rows() {
  # name<TAB>sha256<TAB>where<TAB>url, one line per source.
  python3 - "$MANIFEST" <<'PYEOF'
import json, sys
try:
    m = json.load(open(sys.argv[1]))
except Exception:
    raise SystemExit(0)
for pkg in m.get("packages", []):
    for u in pkg.get("urls", []):
        if u.get("url"):
            print("\t".join([pkg["name"], pkg["sha256"],
                             u.get("where") or u["url"], u["url"]]))
PYEOF
}

sha_of() { sha256sum "$1" | cut -d" " -f1; }

fetch_missing() {
  local rows names
  rows=$(manifest_rows) || return 0
  [ -n "$rows" ] || { echo "No download manifest at $MANIFEST"; return 0; }

  names=$(printf '%s\n' "$rows" | cut -f1 | awk '!seen[$0]++')
  while IFS= read -r name; do
    [ -n "$name" ] || continue
    local want dest got
    want=$(printf '%s\n' "$rows" | awk -F'\t' -v n="$name" '$1==n{print $2; exit}')
    dest="$SRC/$name"
    if [ -s "$dest" ] && [ "$(sha_of "$dest")" = "$want" ]; then
      echo "Already have $name (checksum matches)"
      continue
    fi
    got=0
    while IFS=$'\t' read -r n _ where url; do
      [ "$n" = "$name" ] || continue
      echo "Downloading $name from $where"
      # --location: Launchpad answers with a 303 to its file host.
      # --compressed: makemkv.com serves the .tar.gz with Content-Encoding: gzip on
      #   top of the gzip that is already the file, and anything replaying its
      #   response does the same. Without this the file arrives doubly compressed --
      #   valid gzip, wrong contents, failed checksum, and no hint as to why.
      if curl -fsSL --compressed --retry 2 --connect-timeout 20 --max-time 900 \
              -o "$dest.part" "$url"; then
        if [ "$(sha_of "$dest.part")" = "$want" ]; then
          mv "$dest.part" "$dest"; got=1; echo "  ok - checksum matches"; break
        fi
        echo "  $where served a file that did not match its checksum; trying the next"
      else
        echo "  $where did not answer; trying the next"
      fi
      rm -f "$dest.part"
    done <<< "$rows"
    if [ "$got" != "1" ]; then
      echo "Could not download $name from any source."
      echo "Copy it to $SRC by hand and run this again."
      exit 2
    fi
  done <<< "$names"
  return 0
}

# find, not globbing: an unmatched glob here previously left `ls -d` with no argument,
# which lists "." and set OSS/BIN to a directory that is not MakeMKV at all.
find_pkg() { find "$SRC" -maxdepth 1 -mindepth 1 -type d -name "makemkv-$1-*" | head -1; }

OSS=$(find_pkg oss)
BIN=$(find_pkg bin)
if [ -z "$OSS" ] || [ -z "$BIN" ]; then
  archives=$(find "$SRC" -maxdepth 1 -type f -name 'makemkv-*.tar.gz' | sort)
  if [ -z "$archives" ]; then
    fetch_missing
    archives=$(find "$SRC" -maxdepth 1 -type f -name 'makemkv-*.tar.gz' | sort)
  fi
  [ -n "$archives" ] || { echo "No makemkv-*.tar.gz found in $SRC"; exit 2; }
  echo "Extracting tarballs in $SRC"
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
BIN_PATH="$(command -v makemkvcon || echo /usr/bin/makemkvcon)"
[ -x "$BIN_PATH" ] || { echo "makemkvcon was not installed"; exit 1; }
echo "    $BIN_PATH"
ldd "$BIN_PATH" | sed 's/^/    /'

# makemkvcon has no --version and no --help: it exits non-zero and prints usage, which
# under `set -e` used to abort the script *after* a completely successful build. And
# there is no cheap way to ask a running makemkvcon either — `-r info disc:99` blocks
# for 20+ seconds enumerating drives, and the version strings inside the binary belong
# to bundled libraries, not to MakeMKV.
#
# So record it here, where it is already known from the tarball, for anything that
# wants to display it without paying that cost.
install -d -m 0755 /usr/local/lib/riparr
printf '%s\n' "$VER" > /usr/local/lib/riparr/makemkv.version
chmod 0644 /usr/local/lib/riparr/makemkv.version

# Reaching this line required --accept-eula, so this file records the fact that the
# installed binary was installed under an accepted licence. The old check looked for
# ~/.MakeMKV/eula_accepted, which MakeMKV does not create — the bin package's Makefile
# gate is a different file in the build directory — so it was always false, and the
# interface kept asking for consent that had already been given.
printf 'accepted %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  > /usr/local/lib/riparr/makemkv.eula
chmod 0644 /usr/local/lib/riparr/makemkv.eula
echo "    version $VER (recorded in /usr/local/lib/riparr/makemkv.version)"
printf '%s\n' "$VER" > "$LOG/makemkv-version.txt"

echo
echo "=== 4. Peak memory of a real operation (R1 measurement) ==="
echo "With a disc inserted, run:"
echo "    /usr/bin/time -v makemkvcon -r info disc:0 2> $LOG/makemkv-info.time"
echo "    grep 'Maximum resident' $LOG/makemkv-info.time"
echo
echo "DONE. Build artifacts logged in $LOG"
