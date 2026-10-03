#!/usr/bin/env bash
# Root half of the DVD backup tools. Started by riparr-dvdtools.service, which is
# triggered by riparr-dvdtools.path when the web service -- or apply-system.sh after an
# update -- drops a request file.
#
# Why this exists: MakeMKV's `backup` command does Blu-ray and UHD only. A DVD backup
# (the whole VIDEO_TS folder, menus and all, decrypted) is `dvdbackup -M`, and dvdbackup
# reads through libdvdread, which decrypts CSS only when libdvdcss is present. dvdbackup
# is in Debian; libdvdcss is not, anywhere, so it is built here from VideoLAN's release
# tarball against a pinned checksum -- the same discipline as the MakeMKV build.
#
# Same shape as makemkv-run.sh: the unprivileged side can ask for this to run and
# cannot say what runs. THIS FILE MUST LIVE OUTSIDE /opt/riparr (apply-system.sh puts it
# in /usr/local/lib/riparr as root:root 0755).
#
# Idempotent: anything already installed is left alone, so a second request is cheap.
set -uo pipefail

RUN=/run/riparr
STATE="$RUN/dvdtools.state"
LOG="$RUN/dvdtools.log"

# libdvdcss, pinned. 1.5.0 moved the build from autotools to meson; the soname is still
# libdvdcss.so.2, which is what libdvdread dlopen()s. Checksum taken from VideoLAN's own
# .sha256 beside the tarball and confirmed against the downloaded bytes, 2026-10-03.
DVDCSS_VERSION=1.6.0
DVDCSS_SHA256=7ea556c846b7bfc32d47b41cae56d1863a6b6d5f706bb162778d6f298490977c
DVDCSS_URL="https://download.videolan.org/pub/videolan/libdvdcss/$DVDCSS_VERSION/libdvdcss-$DVDCSS_VERSION.tar.xz"

mkdir -p "$RUN"

say() {   # phase, progress (0..1), message, [detail]
  printf '{"phase":"%s","progress":%s,"message":%s,"detail":%s}\n' \
    "$1" "$2" \
    "$(python3 -c 'import json,sys;print(json.dumps(sys.argv[1]))' "$3")" \
    "$(python3 -c 'import json,sys;print(json.dumps(sys.argv[1]))' "${4:-}")" \
    > "$STATE.tmp"
  mv -f "$STATE.tmp" "$STATE"
  chmod 0644 "$STATE"
}

fail() {
  say error 0 "$1" "$(tail -n 20 "$LOG")"
  exit 1
}

has_lib() { ldconfig -p 2>/dev/null | grep -q 'libdvdcss\.so\.2'; }

: > "$LOG"
chmod 0644 "$LOG"

# The MakeMKV build may be running apt at the same moment -- on a fresh box both are
# asked for within the same minute. Waiting for the lock is right; failing on it was
# the default and would have read as "the tools could not be installed".
APT=(apt-get -o DPkg::Lock::Timeout=1200 -y -q --no-install-recommends)
updated=0
apt_install() {
  if [ "$updated" = 0 ]; then
    "${APT[@]}" update >>"$LOG" 2>&1 || true
    updated=1
  fi
  "${APT[@]}" install "$@" >>"$LOG" 2>&1
}

say starting 0.05 "Installing the DVD backup tools"

if ! command -v dvdbackup >/dev/null 2>&1; then
  say installing 0.15 "Installing dvdbackup"
  apt_install dvdbackup || fail "dvdbackup couldn't be installed."
fi

if ! has_lib; then
  need=()
  for p in build-essential meson ninja-build pkg-config xz-utils curl ca-certificates; do
    dpkg -s "$p" >/dev/null 2>&1 || need+=("$p")
  done
  if [ ${#need[@]} -gt 0 ]; then
    say installing 0.3 "Installing build tools" "${need[*]}"
    apt_install "${need[@]}" || fail "The build tools couldn't be installed."
  fi

  say building 0.55 "Building the DVD decryption library" "libdvdcss $DVDCSS_VERSION"
  work=$(mktemp -d)
  trap 'rm -rf "$work"' EXIT
  curl -fsSL --retry 3 --connect-timeout 20 -o "$work/libdvdcss.tar.xz" "$DVDCSS_URL" \
    >>"$LOG" 2>&1 || fail "libdvdcss couldn't be downloaded from videolan.org."
  echo "$DVDCSS_SHA256  $work/libdvdcss.tar.xz" | sha256sum -c - >>"$LOG" 2>&1 \
    || fail "The libdvdcss download didn't match its checksum, so it wasn't used."
  tar -xJf "$work/libdvdcss.tar.xz" -C "$work" >>"$LOG" 2>&1 || fail "libdvdcss didn't unpack."
  # --libdir=lib puts it in /usr/local/lib, which every Debian's ld.so.conf includes.
  # Left to itself meson picks a multiarch directory on Debian, which is also indexed
  # -- but "also" is a thing to verify, and this is a thing that is simply true.
  ( cd "$work/libdvdcss-$DVDCSS_VERSION" \
    && meson setup build --prefix=/usr/local --libdir=lib --buildtype=release \
    && ninja -C build \
    && ninja -C build install ) >>"$LOG" 2>&1 || fail "libdvdcss didn't build."
  ldconfig
fi

# Prove it, rather than trusting the steps above: the binary is on PATH and the loader
# can find the library by the name libdvdread will ask for.
if command -v dvdbackup >/dev/null 2>&1 && has_lib; then
  say done 1 "DVD backups are ready." ""
else
  fail "The install finished, but the tools still can't be found."
fi
