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
BOOT=""
for d in /boot/firmware /boot; do
  [ -d "$d" ] && [ -f "$d/config.txt" ] && { BOOT="$d"; break; }
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
for pkg in python3-venv python3-pip git ca-certificates; do
  dpkg -s "$pkg" >/dev/null 2>&1 || MISSING+=("$pkg")
done
if [ ${#MISSING[@]} -gt 0 ]; then
  info "installing: ${MISSING[*]}"
  apt-get update -qq
  apt-get install -y -qq "${MISSING[@]}" >/dev/null
fi
ok "dependencies present"

# ── 2. account ──
say "2/6  Account"
if ! id -u "$RIPARR_USER" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir /var/lib/"$RIPARR_USER" \
          --shell /usr/sbin/nologin "$RIPARR_USER"
fi
# The optical drive is root:cdrom; without this the service cannot read a disc.
getent group cdrom >/dev/null && usermod -aG cdrom "$RIPARR_USER"
install -d -o "$RIPARR_USER" -g "$RIPARR_USER" "$DATA_DIR"
ok "user '$RIPARR_USER' in group cdrom"

# ── 3. source ──
say "3/6  Riparr"
UPGRADE=no
if [ -d "$INSTALL_DIR/.git" ]; then
  UPGRADE=yes
  info "updating existing install"
  git -C "$INSTALL_DIR" fetch --quiet origin "$BRANCH"
  git -C "$INSTALL_DIR" reset --hard --quiet "origin/$BRANCH"
elif [ -f "$(dirname "$0")/../server/riparr/main.py" ]; then
  # Running from a checkout already on the device: install from it, do not re-download.
  SRC="$(cd "$(dirname "$0")/.." && pwd)"
  info "installing from $SRC"
  [ "$SRC" = "$INSTALL_DIR" ] || { rm -rf "$INSTALL_DIR"; cp -a "$SRC" "$INSTALL_DIR"; }
else
  info "cloning $REPO_URL"
  rm -rf "$INSTALL_DIR"
  git clone --quiet --depth 1 --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR" \
    || die "Could not download Riparr from $REPO_URL. Check the network and try again."
fi
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
