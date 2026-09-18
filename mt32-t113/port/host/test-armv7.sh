#!/bin/sh
# test-armv7.sh - run the platform layer's own test suite as armv7 code.
#
# Cross-builds for Cortex-A7 (hard float, NEON) and runs the same test.sh
# through qemu-user. This catches alignment, type-size and endianness bugs in
# the parser, ring and render loop long before any silicon exists.
#
# What it does NOT tell you: anything about speed. QEMU's TCG does not model
# the A7 pipeline or its caches, so timings from it are meaningless and the
# real-time-factor gate in ../../bench cannot be closed this way.
#
# Needs: gcc-arm-linux-gnueabihf, qemu-user.
set -e
cd "$(dirname "$0")"

CROSS=${CROSS:-arm-linux-gnueabihf-}
QEMU=${QEMU:-qemu-arm}
SYSROOT=${SYSROOT:-/usr/arm-linux-gnueabihf/}

command -v "${CROSS}gcc" >/dev/null || { echo "no ${CROSS}gcc; apt install gcc-arm-linux-gnueabihf"; exit 2; }
command -v "$QEMU" >/dev/null      || { echo "no $QEMU; apt install qemu-user"; exit 2; }

make clean >/dev/null
make CC="${CROSS}gcc" CFLAGS="-O2 -g -Wall -Wextra -Wshadow -std=c99 -mcpu=cortex-a7 -mfpu=neon-vfpv4 -mfloat-abi=hard" >/dev/null

# test.sh runs build/mtp_host directly, so stand a wrapper in its place.
mv build/mtp_host build/mtp_host.arm
cat > build/mtp_host <<WRAP
#!/bin/sh
exec $QEMU -L $SYSROOT "\$(dirname "\$0")/mtp_host.arm" "\$@"
WRAP
chmod +x build/mtp_host

./test.sh
echo
echo "the above ran as armv7-a code under $QEMU, not as x86"
