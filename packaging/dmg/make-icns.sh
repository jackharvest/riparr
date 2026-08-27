#!/bin/bash
# Build the macOS icon from the product mark.
#
# PyInstaller shipped its own default icon because --icon was never passed, so the app
# in the Dock and in the disk image was a generic one while the real mark sat in the
# repository being used by everything else.
set -e
SRC="${1:-server/static/img/riparr-512.png}"
OUT="${2:-riparr.icns}"
SET=$(mktemp -d)/riparr.iconset
mkdir -p "$SET"

# The sizes macOS actually asks for. Anything missing here is silently upscaled from a
# neighbour and looks it, most visibly at 32 and in Get Info.
for sz in 16 32 128 256 512; do
  sips -z $sz $sz "$SRC" --out "$SET/icon_${sz}x${sz}.png" >/dev/null
  d=$((sz * 2))
  sips -z $d $d "$SRC" --out "$SET/icon_${sz}x${sz}@2x.png" >/dev/null
done

iconutil -c icns "$SET" -o "$OUT"
rm -rf "$(dirname "$SET")"
echo "wrote $OUT"
