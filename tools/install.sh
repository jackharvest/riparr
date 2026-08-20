#!/usr/bin/env bash
# Install or upgrade Riparr on a Raspberry Pi.
#
#   curl -fsSL https://raw.githubusercontent.com/jackharvest/riparr/main/tools/install.sh | sudo bash
#
# Safe to run again: re-running upgrades in place and keeps your database, settings and
# shares. Reads the port the preparer wrote to the boot partition, if it is there.
set -euo pipefail

REPO_URL="${RIPARR_REPO_URL:-https://github.com/jackharvest/riparr}"
BRANCH="${RIPARR_BRANCH:-main}"
INSTALL_DIR="${RIPARR_INSTALL_DIR:-/opt/riparr}"
DATA_DIR=/var/lib/riparr
SERVICE=riparr.service
RIPARR_USER=riparr

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
info() { printf '  %s\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
die()  { printf '\n\033[31m✗ %s\033[0m\n\n' "$*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || die "Run this with sudo:  curl -fsSL … | sudo bash"
[ "$(uname -s)" = Linux ] || die "Riparr installs on the appliance. This is $(uname -s)."

# ── where the boot partition is mounted differs by OS version ──
# config.txt only exists on Raspberry Pi. Armbian on Allwinner keeps /boot inside the
# root filesystem with armbianEnv.txt instead, and riparr.conf is written straight there.
BOOT=""
for d in /boot/firmware /boot; do
  [ -d "$d" ] || continue
  if [ -f "$d/riparr.conf" ] || [ -f "$d/config.txt" ] || [ -f "$d/armbianEnv.txt" ]; then
    BOOT="$d"; break
  fi
done

PORT=9797
if [ -n "$BOOT" ] && [ -f "$BOOT/riparr.conf" ]; then
  # shellcheck disable=SC1090
  . "$BOOT/riparr.conf"
  PORT="${RIPARR_PORT:-9797}"
fi

say "Installing Riparr"
info "port $PORT · $INSTALL_DIR · $(cat /proc/device-tree/model 2>/dev/null | tr -d '\0' || echo 'unknown board')"

# ── 1. packages ──
say "1/6  Packages"
export DEBIAN_FRONTEND=noninteractive
MISSING=()
# avahi-daemon is what makes http://<hostname>.local resolve. Raspberry Pi OS Lite
# usually has it, but "usually" is not good enough for the one address we print.
for pkg in python3-venv python3-pip git ca-certificates avahi-daemon; do
  dpkg -s "$pkg" >/dev/null 2>&1 || MISSING+=("$pkg")
done
if [ ${#MISSING[@]} -gt 0 ]; then
  info "installing: ${MISSING[*]}"
  apt-get update -qq
  apt-get install -y -qq "${MISSING[@]}" >/dev/null
fi
systemctl enable --now avahi-daemon >/dev/null 2>&1 || true
ok "dependencies present"

# ── 2. account ──
say "2/6  Account"
if ! id -u "$RIPARR_USER" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir /var/lib/"$RIPARR_USER" \
          --shell /usr/sbin/nologin "$RIPARR_USER"
fi
# The optical drive is root:cdrom, and vcgencmd (temperature, throttling) needs video.
for g in cdrom video; do
  getent group "$g" >/dev/null && usermod -aG "$g" "$RIPARR_USER"
done
install -d -o "$RIPARR_USER" -g "$RIPARR_USER" "$DATA_DIR"
# The dedicated staging partition (D4) does not exist on stock Raspberry Pi OS. Create
# the directory anyway: the service declares it writable, and the status page reports
# free space from it. Falls back to a plain directory on the root filesystem.
install -d -o "$RIPARR_USER" -g "$RIPARR_USER" -m 0775 /srv/staging
ok "user '$RIPARR_USER' ready; staging at /srv/staging"

# ── 3. source ──
say "3/6  Riparr"
UPGRADE=no
[ -d "$INSTALL_DIR" ] && UPGRADE=yes

# A checkout sitting next to this script wins over the network. The repository may not be
# published yet, and even when it is, someone who copied a working tree to the device
# meant to install *that*.
SRC=""
if [ -f "$(dirname "$0")/../server/riparr/main.py" ]; then
  SRC="$(cd "$(dirname "$0")/.." && pwd)"
fi

# Re-installing throws the tree away, so park the virtualenv and put it back afterwards.
# Rebuilding it costs several minutes of wheel installs on a Zero 2 W.
KEEP_VENV=""
if [ "$SRC" != "$INSTALL_DIR" ] && [ -d "$INSTALL_DIR/.venv" ]; then
  KEEP_VENV=$(mktemp -d)
  mv "$INSTALL_DIR/.venv" "$KEEP_VENV/.venv"
fi

if [ -n "$SRC" ] && [ "$SRC" = "$INSTALL_DIR" ]; then
  info "installing in place from $SRC"
elif [ -n "$SRC" ]; then
  info "installing from $SRC"
  rm -rf "$INSTALL_DIR"
  mkdir -p "$INSTALL_DIR"
  # --exclude .venv: a working tree copied off a Mac carries a virtualenv full of
  # x86/arm64-Darwin binaries that would sit in /opt doing nothing but confusing people.
  tar -cf - -C "$SRC" --exclude=.venv . | tar -xf - -C "$INSTALL_DIR"
elif [ -d "$INSTALL_DIR/.git" ]; then
  info "updating existing install"
  if git -C "$INSTALL_DIR" fetch --quiet origin "$BRANCH" 2>/dev/null; then
    git -C "$INSTALL_DIR" reset --hard --quiet "origin/$BRANCH"
  else
    # Not fatal: an unreachable origin should reinstall what is already here, not abort
    # halfway and leave the box without a service.
    info "could not reach $REPO_URL — keeping the copy already installed"
  fi
else
  info "cloning $REPO_URL"
  rm -rf "$INSTALL_DIR"
  git clone --quiet --depth 1 --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR" \
    || die "Could not download Riparr from $REPO_URL. Check the network and try again."
fi

[ -n "$KEEP_VENV" ] && { rm -rf "$INSTALL_DIR/.venv"; mv "$KEEP_VENV/.venv" "$INSTALL_DIR/.venv"; rmdir "$KEEP_VENV"; }
VERSION=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$INSTALL_DIR/server/riparr/__init__.py" 2>/dev/null || echo "?")
ok "Riparr $VERSION in $INSTALL_DIR"

# ── 4. python environment ──
say "4/6  Python environment"
[ -d "$INSTALL_DIR/.venv" ] || python3 -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/pip" install --quiet --upgrade pip >/dev/null 2>&1 || true
info "installing dependencies (a few minutes on a Zero 2 W)"
"$INSTALL_DIR/.venv/bin/pip" install --quiet -r "$INSTALL_DIR/server/requirements.txt" \
  || die "Dependencies failed to install. Re-run to try again."
chown -R "$RIPARR_USER":"$RIPARR_USER" "$INSTALL_DIR"
ok "environment ready"

# ── 5. service ──
say "5/6  Service"
install -m 0644 "$INSTALL_DIR/packaging/riparr.service" "/etc/systemd/system/$SERVICE"
systemctl daemon-reload
systemctl enable --quiet "$SERVICE"
systemctl restart "$SERVICE"
ok "riparr.service enabled and started"

# ── 6. confirm it is actually answering ──
say "6/6  Checking it works"
HOST=$(hostname)
UP=no
for _ in $(seq 1 30); do
  if curl -fsS --max-time 2 "http://127.0.0.1:$PORT/api/setup/state" >/dev/null 2>&1; then
    UP=yes; break
  fi
  sleep 1
done

if [ "$UP" != yes ]; then
  printf '\n\033[31m✗ Riparr installed but is not answering on port %s.\033[0m\n\n' "$PORT"
  echo "  What it says for itself:"
  journalctl -u "$SERVICE" -n 25 --no-pager | sed 's/^/    /'
  echo
  echo "  Once fixed:  sudo systemctl restart $SERVICE"
  exit 1
fi

printf '\n\033[32m✓ Riparr is running.\033[0m\n\n'
if [ "$UPGRADE" = yes ]; then
  echo "  Upgraded to $VERSION. Your settings and history were kept."
else
  echo "  Open it and finish setup:"
fi
printf '\n      \033[1mhttp://%s.local:%s\033[0m\n\n' "$HOST" "$PORT"
echo "  If that name doesn't resolve, use the address instead:"
printf '      http://%s:%s\n\n' "$(hostname -I | awk '{print $1}')" "$PORT"
