#!/bin/bash
# Build debugfs for Windows. Run inside Cygwin's bash, from the release workflow.
#
#   build-debugfs.sh <out-dir> [<tools-dir>]
#
# Windows cannot mount ext4, and an Armbian card is one ext4 partition with nothing else
# to write to. So the Windows Preparer carries debugfs and configures the image before it
# is written -- see hostos/windows.py, PROVISION_IN_IMAGE. Cygwin's own e2fsprogs package
# stopped at 1.44.5 in 2018, which predates ext4 features a current image can carry, so
# this builds the current release from kernel.org instead, checked against its published
# hash.
#
# <out-dir> gets debugfs.exe, every DLL it loads from Cygwin, and the licences that come
# with them. <tools-dir>, if given, also gets mke2fs.exe -- for the CI test that builds a
# small ext4 image to configure, not for the app.
set -euo pipefail

VER=1.47.4
SHA256=fd5bf388cbdbe006a3d3b318d983b2948382440acc85a87f1e7d108653e8db0b
URL=https://mirrors.edge.kernel.org/pub/linux/kernel/people/tytso/e2fsprogs/v$VER/e2fsprogs-$VER.tar.xz

# Cygwin's tools only. The runner's Windows PATH carries Strawberry Perl's pkg-config,
# among others, and configure will find and run the wrong one.
export PATH=/usr/bin:/bin
unset PKG_CONFIG_PATH

OUT=$(cygpath -u "$1")
TOOLS=${2:+$(cygpath -u "$2")}
WORK=$(mktemp -d)

cd "$WORK"
curl -fsSLO "$URL"
echo "$SHA256  e2fsprogs-$VER.tar.xz" | sha256sum -c -
tar xf "e2fsprogs-$VER.tar.xz"
cd "e2fsprogs-$VER"

# getsize.c takes its native-Windows branch under Cygwin too, and that branch calls the
# MSVC runtime's _get_osfhandle, which Cygwin does not have. Cygwin's own package carries
# this same one-line patch (1.42.6-cygwin-getsize.patch); its POSIX branch is the right
# one here, and it only matters for block devices anyway -- the Preparer hands debugfs a
# file.
sed -i 's/^#if defined(__CYGWIN__) || defined (WIN32)$/#if defined (WIN32)/' lib/ext2fs/getsize.c
grep -q '^#if defined (WIN32)$' lib/ext2fs/getsize.c || { echo "getsize.c patch did not apply"; exit 1; }

# Static e2fsprogs libraries (the default), so the only DLLs are Cygwin's own. Its own
# libuuid and libblkid, so nothing else has to be installed to build or to run.
./configure --disable-nls --disable-fuse2fs --disable-uuidd --disable-defrag \
            --disable-e2initrd-helper --enable-libuuid --enable-libblkid >/dev/null
make -j"$(nproc)" libs >/dev/null
make -C debugfs debugfs >/dev/null

mkdir -p "$OUT"
cp debugfs/debugfs.exe "$OUT/"
# Whatever it actually loads from Cygwin, rather than a list that drifts.
ldd debugfs/debugfs.exe | awk '$3 ~ /^\/usr\/bin\// {print $3}' | sort -u | while read -r dll; do
  cp "$dll" "$OUT/"
done
cp NOTICE "$OUT/e2fsprogs-NOTICE.txt"
cat > "$OUT/README.txt" <<EOF
debugfs $VER from e2fsprogs (GPL-2.0), built under Cygwin by the Riparr release workflow.
Source: $URL
cygwin1.dll is Cygwin's runtime (LGPL-3.0); source: https://cygwin.com/git/newlib-cygwin.git
The Preparer uses debugfs to write your settings into the card image before writing it.
EOF

if [ -n "$TOOLS" ]; then
  make -C misc mke2fs >/dev/null
  mkdir -p "$TOOLS"
  cp misc/mke2fs.exe "$TOOLS/"
  # Cygwin is not on PATH in the workflow, so the DLLs go next to it as they do in the app.
  ldd misc/mke2fs.exe | awk '$3 ~ /^\/usr\/bin\// {print $3}' | sort -u | while read -r dll; do
    cp "$dll" "$TOOLS/"
  done
fi

echo "built:"
ls -l "$OUT"
"$OUT/debugfs.exe" -V
