#!/usr/bin/env bash
#
# Quake II (id's GPL 3.21 source) -> ref_gl (fxMesa) -> our Mesa 3.4.2 fx
# driver -> libglide2x with the PCIe BAR backend -> the Tang dock.
#
# Reuses the Mesa and Glide libraries built by ../quake-glide/build.sh pcie
# (run that first). Builds natively with gcc -m32, C only (no x86 asm).
#
#   ./build.sh          # fetch source + demo data, patch, build into work/
#   then: fpga/hw/gowin/board-lock -- sudo sw/quake2-glide/run-pcie.sh
#
# The id demo data (q2-314-demo-x86.exe) is copyrighted and is downloaded
# into work/, never committed.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
WORK="$HERE/work"
QG="$REPO/sw/quake-glide/work/native"
MESA="$QG/Mesa-3.4.2" GLIDE="$QG/glide-pcie"
CC32="${CC32:-gcc -m32}"
Q2_REV=372afde46e7defc9dd2d719a1732b8ace1fa096e
DEMO_URL="${DEMO_URL:-https://deponie.yamagi.org/quake2/idstuff/q2-314-demo-x86.exe}"
DEMO_SHA=7ace5a43983f10d6bdc9d9b6e17a1032ba6223118d389bd170df89b945a04a1e

[ -f "$MESA/lib/libGL.so" ] && [ -f "$GLIDE/lib/libglide2x.so" ] || {
  echo "build ../quake-glide first: ./build.sh pcie" >&2; exit 1; }
mkdir -p "$WORK"

# Source
S="$WORK/Quake-2"
if [ ! -f "$S/.patched" ]; then
  rm -rf "$S" "$WORK/q2src"
  git clone -q https://github.com/id-Software/Quake-2.git "$WORK/q2src"
  git -C "$WORK/q2src" checkout -q "$Q2_REV"
  cp -r "$WORK/q2src" "$S"; rm -rf "$S/.git"
  patch -d "$S" -p1 <"$HERE/patches/quake2-fxmesa-pcie.patch"
  touch "$S/.patched"
fi

# Demo data
D="$WORK/demo"
if [ ! -f "$D/baseq2/pak0.pak" ]; then
  mkdir -p "$D/dl"
  [ -f "$D/dl/q2-314-demo-x86.exe" ] || curl -fL -o "$D/dl/q2-314-demo-x86.exe" "$DEMO_URL"
  echo "$DEMO_SHA  $D/dl/q2-314-demo-x86.exe" | sha256sum -c -
  (cd "$D/dl" && unzip -qo q2-314-demo-x86.exe 'Install/Data/baseq2/pak0.pak')
  mkdir -p "$D/baseq2"; mv "$D/dl/Install/Data/baseq2/pak0.pak" "$D/baseq2/"
fi

HOSTOPT="${HOSTOPT--march=native -mfpmath=sse}"
CFLAGS="-std=gnu89 -fcommon -fno-strict-aliasing -w -O2 -ffast-math $HOSTOPT -DC_ONLY -Dstricmp=strcasecmp"
B="$WORK/bin"; mkdir -p "$B"
cd "$S"

echo "[quake2] client"
$CC32 $CFLAGS -o "$B/quake2" \
  client/{cl_newfx,cl_cin,cl_ents,cl_fx,cl_input,cl_inv,cl_main,cl_parse,cl_pred,cl_tent,cl_scrn,cl_view,console,keys,menu,snd_dma,snd_mem,snd_mix,qmenu}.c \
  game/m_flash.c qcommon/{cmd,cmodel,common,crc,cvar,files,md4,net_chan,pmove}.c \
  server/{sv_ccmds,sv_ents,sv_game,sv_init,sv_main,sv_send,sv_user,sv_world}.c \
  linux/{cd_linux,q_shlinux,vid_menu,vid_so,snd_linux,sys_linux,glob,net_udp}.c game/q_shared.c \
  -ldl -lm

echo "[quake2] ref_gl (fxMesa)"
$CC32 $CFLAGS -fPIC -shared -o "$B/ref_gl.so" -I"$MESA/include" -I"$GLIDE/include" \
  ref_gl/{gl_draw,gl_image,gl_light,gl_mesh,gl_model,gl_rmain,gl_rmisc,gl_rsurf,gl_warp}.c \
  linux/{qgl_linux,gl_fxmesa,rw_in_null,q_shlinux,glob}.c game/q_shared.c \
  -L"$MESA/lib" -lGL -L"$GLIDE/lib" -lglide2x -ldl -lm

echo "[quake2] game"
mkdir -p "$D/baseq2"
$CC32 $CFLAGS -fPIC -shared -o "$D/baseq2/gamei386.so" game/*.c -lm

echo "[quake2] built $B/quake2, ref_gl.so, $D/baseq2/gamei386.so"
