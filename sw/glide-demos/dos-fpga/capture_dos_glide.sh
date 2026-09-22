#!/usr/bin/env bash
# Run a DOS Glide program in 32-bit DOSBox-X and capture its Voodoo command
# trace via the repo's sim libglide2x (glide passthrough). See README.md.
#
#   DOSBOXX32_BIN=/path/to/32bit/dosbox-x \
#     ./capture_dos_glide.sh <mount-dir> <EXE[ args]> <out-trace.bin> [seconds]
#
# The mount-dir is mounted as C:, then the EXE is run. A DOS program that uses
# the 3dfx SDK/OVL Glide path forwards to the sim backend; the trace is written
# to <out-trace.bin>. Render it with ext/SpinalVoodoo/emu/test/obj_dir/trace_test.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXT="$(cd "$HERE/../../../ext/SpinalVoodoo" && pwd)"
LIBDIR="$EXT/emu/glide/glide2x/sst1/lib/sst1"

MOUNT="${1:?usage: capture_dos_glide.sh <mount-dir> <EXE args> <out.bin> [secs]}"
EXE="${2:?missing EXE}"
OUT="${3:?missing out trace path}"
SECS="${4:-45}"

DB="${DOSBOXX32_BIN:?set DOSBOXX32_BIN to a 32-bit dosbox-x}"
if ! file -Lb "$DB" | grep -q "ELF 32-bit"; then
  echo "error: $DB is not a 32-bit DOSBox-X (needed to dlopen 32-bit libglide2x)" >&2
  exit 1
fi
if [ ! -f "$LIBDIR/libglide2x.so.2" ]; then
  echo "error: build the trace libglide2x first: make -C '$EXT' native/trace/build" >&2
  exit 1
fi

CONF="$(mktemp --suffix=.conf)"
cat > "$CONF" <<'EOF'
[dosbox]
quit warning=false
[cpu]
core=normal
cputype=pentium
[mixer]
nosound=true
[voodoo]
voodoo_card=false
glide=true
lfb=full_noaux
splash=false
EOF

mkdir -p "$(dirname "$OUT")"
rm -f "$OUT"
SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy \
  LD_LIBRARY_PATH="$LIBDIR" SIM_TRACE_FILE="$OUT" \
  timeout "$SECS" "$DB" -conf "$CONF" \
    -c "mount c $MOUNT" -c "c:" -c "$EXE" -c "exit" >/dev/null 2>&1
rm -f "$CONF"

if [ -s "$OUT" ]; then
  echo "captured $(stat -c%s "$OUT") bytes -> $OUT"
else
  echo "no trace captured (program may not use the SDK/OVL Glide passthrough path)" >&2
  exit 1
fi
