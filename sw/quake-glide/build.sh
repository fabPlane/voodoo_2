#!/usr/bin/env bash
#
# GLQuake -> Voodoo FPGA simulator, via the period-correct Linux Glide route.
#
#   id GLQuake (fxMesa video backend)
#     -> Mesa 3.4.2 "fx" driver (OpenGL 1.1 over Glide2)
#       -> our libglide2x (SpinalVoodoo sim backend, trace-capture mode)
#         -> trace_test (86Box software Voodoo reference model)
#           -> a real, textured Quake frame.
#
# The ugly 2001-era Mesa/GLQuake build happens entirely inside the `glide32`
# i386 Docker container, so the host toolchain stays clean. Only the trace
# render (trace_test) runs on the host.
#
# Usage:
#   ./build.sh            # full pipeline: patch, build libs, Mesa, GLQuake, run, render
#   ./build.sh build      # patch + build libs, Mesa and GLQuake; do not run
#   ./build.sh run        # capture a trace (QUAKE_CMDS, default "map start")
#   ./build.sh render     # just re-render the last captured trace
#   ./build.sh pcie       # native -m32 build for the live board (work/native, run-pcie.sh)
#   SECS=2 ./build.sh     # seconds to free-run (default 2)
#   QUAKE_CMDS='playdemo demo1' SECS=10 ./build.sh run
#
# Prereqs: Docker with the glide32 image (see docker/glide32.Dockerfile), the
# ext/SpinalVoodoo submodule, and the multilib host toolchain from install.sh.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
EXT="$REPO/ext/SpinalVoodoo"
EBOX="$EXT/emu/86Box"
WORK="$HERE/work"
GLIDE_LIB="$EXT/emu/glide/glide2x/sst1/lib/sst1"
TT="$EXT/emu/test/obj_dir/trace_test"
SECS="${SECS:-2}"
DOCKER="${DOCKER:-sudo docker}"
IMAGE="${IMAGE:-glide32:latest}"

mkdir -p "$WORK" "$HERE/shots"

apply_patch() { # <repo-dir> <patch> <sentinel-file> <sentinel-string>
  local dir="$1" patch="$2" file="$3" marker="$4"
  if grep -q "$marker" "$dir/$file" 2>/dev/null; then
    echo "[quake-glide] patch already applied: $patch"
  else
    echo "[quake-glide] applying $patch"
    git -C "$dir" apply "$patch"
  fi
}

stage_patches() {
  # 1. libglide2x memory size, trace_test dump, ref-model screen size, and
  #    the even/odd tLOD mask. The sentinel is the ditex mask fix so an older
  #    revision of this patch (dump-frames / videoDimensions only) is replaced.
  local tt="emu/test/trace_test.cpp"
  local ditex="emu/glide/glide2x/sst1/glide/src/ditex.c"
  if grep -q "A missing mask contributes no bits" "$EXT/$ditex" 2>/dev/null; then
    echo "[quake-glide] patch already applied: spinalvoodoo-glquake.patch"
  else
    if grep -q "dump_frames_dir" "$EXT/$tt" 2>/dev/null || \
       grep -q "simulator backend cannot probe" \
         "$EXT/emu/glide/glide2x/sst1/init/init.c" 2>/dev/null; then
      echo "[quake-glide] refreshing spinalvoodoo-glquake.patch"
      git -C "$EXT" checkout -- \
        emu/glide/glide2x/sst1/init/init.c \
        "$ditex" "$tt" emu/test/ref_model.c
    fi
    echo "[quake-glide] applying spinalvoodoo-glquake.patch"
    git -C "$EXT" apply "$HERE/patches/spinalvoodoo-glquake.patch"
  fi
  # 2. 86Box reference rasterizer robustness (mask colour fb base like the aux
  #    path + clamp the span) so unclipped off-screen GL triangles can't crash.
  apply_patch "$EBOX" "$HERE/patches/86box-render-robustness.patch" \
    "src/video/vid_voodoo_render.c" "Safety net for reference-model robustness"
  # 3. Texture RAM: separate TMU aperture, drop page-flush holes, and sample
  #    a texture with the layout it was downloaded in.
  apply_patch "$EBOX" "$HERE/patches/86box-texture-layout.patch" \
    "src/video/vid_voodoo_texture.c" "Keep TMU1 downloads in their own RAM"
  # 3b. SST-1 has no tLOD mirror bits. Glide leaves bits 28-29 set, and the
  #     reference model was treating them as Voodoo2 TMIRROR.
  apply_patch "$EBOX" "$HERE/patches/86box-voodoo1-mirror.patch" \
    "src/video/vid_voodoo_render.c" "Voodoo1 ignores stray tLOD mirror bits"
  # 4. CoreSim HDMI tie-off so the sim top elaborates (shared with homebrew).
  if [ -f "$REPO/sw/glide-homebrew/render/coresim-hdmi.patch" ] && \
     ! grep -q 'io.hdmi.clock' "$EXT/src/voodoo/CoreSim.scala"; then
    git -C "$EXT" apply "$REPO/sw/glide-homebrew/render/coresim-hdmi.patch"
  fi
}

build_host() {
  echo "[quake-glide] building trace-capture libglide2x + trace_test (host)..."
  make -C "$EXT" native/trace/build >/dev/null
  # 86Box reference submodule (SSH url -> HTTPS) for trace_test.
  if [ ! -f "$EBOX/src/video/vid_voodoo_render.c" ]; then
    git -C "$EXT" config submodule.emu/86Box.url https://github.com/fayalalebrun/86Box.git
    git -C "$EXT" submodule update --init --depth 1 emu/86Box
  fi
  local VF='--cc --output-split 20000 --output-split-cfuncs 5000 --public-flat-rw --x-assign fast --x-initial fast --noassert -Wno-WIDTHTRUNC -Wno-WIDTHEXPAND -Wno-CMPCONST -Wno-UNSIGNED'
  make -C "$EXT/emu/test" VERILATOR_FLAGS="$VF" >/dev/null
}

stage_glide_sdk() {
  # Assemble the Glide2 SDK (headers + our trace libglide2x) for Mesa/GLQuake.
  local G="$WORK/glide-sdk" g2x="$EXT/emu/glide/glide2x/sst1" sw="$EXT/emu/glide/swlibs"
  rm -rf "$G"; mkdir -p "$G/include" "$G/lib"
  cp "$g2x"/glide/src/*.h "$g2x"/incsrc/*.h "$g2x"/init/sst1vid.h "$sw"/fxmisc/3dfx.h "$G/include/"
  cp "$GLIDE_LIB/libglide2x.so.2" "$G/lib/"; ln -sf libglide2x.so.2 "$G/lib/libglide2x.so"
}

fetch_sources() {
  if [ ! -d "$WORK/Mesa-3.4.2" ]; then
    echo "[quake-glide] fetching Mesa 3.4.2..."
    curl -fsSL https://archive.mesa3d.org/older-versions/3.x/MesaLib-3.4.2.tar.gz \
      -o "$WORK/MesaLib-3.4.2.tar.gz"
    tar -C "$WORK" -xf "$WORK/MesaLib-3.4.2.tar.gz"
  fi
  if [ ! -d "$WORK/Quake" ]; then
    echo "[quake-glide] fetching id Quake GPL source..."
    git -C "$WORK" clone --depth 1 https://github.com/id-Software/Quake.git
  fi
}

DRUN() { $DOCKER run --rm -v "$WORK:/work" -v "$HERE:/qg" -w /work "$IMAGE" bash -c "$1"; }

build_mesa() {
  echo "[quake-glide] building Mesa 3.4.2 fx/Glide2 driver (container)..."
  DRUN '
    set -e
    mkdir -p /usr/local/glide
    cp -rf /work/glide-sdk/include /usr/local/glide/include
    cp -rf /work/glide-sdk/lib     /usr/local/glide/lib
    ln -sf /usr/local/glide/include /usr/include/glide 2>/dev/null || true
    cd /work/Mesa-3.4.2
    if [ ! -f .patched ]; then
      # Modernise the 2001 build for gcc 12: __FUNCTION__ was a string literal
      # pre-gcc3.4; alias it to __FILE__ so the fprintf concatenations compile.
      sed -i \
        -e "s/-ansi -pedantic/-std=gnu89 -fcommon -D__FUNCTION__=__FILE__ -Wno-implicit-function-declaration -Wno-implicit-int/g" \
        -e "s#-I/usr/X11R6/include#-I/usr/include#g" \
        -e "s#-L/usr/X11R6/lib#-L/usr/lib/i386-linux-gnu#g" \
        Make-config
      touch .patched
    fi
    # libGL + libGLU (the widgets-sgi Motif target failing afterwards is fine).
    make linux-glide >/tmp/mesa.log 2>&1 || true
    ls -la lib/libGL.so.* lib/libGLU.so.*
  '
}

build_glquake() {
  echo "[quake-glide] building id GLQuake (fxMesa backend) (container)..."
  DRUN '
    set -e
    MESA=/work/Mesa-3.4.2; QSRC=/work/Quake/WinQuake
    mkdir -p /usr/local/glide && cp -rf /work/glide-sdk/include /usr/local/glide/include && cp -rf /work/glide-sdk/lib /usr/local/glide/lib
    cd "$QSRC"
    # Force the portable C build (no x86 .s asm) since the container is i386.
    sed -i "s/^#if defined __i386__.*/#if 0  \/* forced C-only build *\//" quakedef.h
    cp /qg/_fpcw_stub.c .
    SRCS=""
    for o in $(awk "/^GLQUAKE_OBJS=/,/^\$/" Makefile.linuxi386 | grep -oE "[a-zA-Z0-9_]+\.o" | sed "s/\.o\$/.c/" | sort -u); do
      [ -f "$o" ] && SRCS="$SRCS $o"
    done
    SRCS="$SRCS gl_vidlinux.c _fpcw_stub.c"
    CFLAGS="-m32 -std=gnu89 -fcommon -fno-strict-aliasing -w -O2 -DGLQUAKE -I/qg/stubs -I$QSRC -I$MESA/include -I/usr/local/glide/include"
    LIBS="-L$MESA/lib -lGL -lGLU -L/usr/local/glide/lib -lglide2x -lX11 -lXext -lm -ldl -lpthread"
    gcc $CFLAGS $SRCS -o /work/glquake $LIBS
    echo "built /work/glquake"; ls -la /work/glquake
  '
}

run_glquake() {
  echo "[quake-glide] running headless GLQuake ($SECS s free-run) -> Glide trace..."
  # Quake shareware data: reuse the pak fetched for the RISC-V port if present.
  local PAK=""
  for c in "$REPO/sw/quake-riscv/id1/pak0.pak" "$WORK/id1/pak0.pak"; do
    [ -f "$c" ] && PAK="$c" && break
  done
  if [ -z "$PAK" ]; then
    echo "[quake-glide] NOTE: no id1/pak0.pak found. Put the shareware pak at"
    echo "             sw/quake-glide/work/id1/pak0.pak and re-run."
    return 1
  fi
  mkdir -p "$WORK/q/id1"; cp -f "$PAK" "$WORK/q/id1/"
  DRUN "
    export LD_LIBRARY_PATH=/work/Mesa-3.4.2/lib:/work/glide-sdk/lib
    export SIM_TRACE_FILE=/work/glquake.trace FX_GLIDE_NO_SPLASH=1
    rm -f \$SIM_TRACE_FILE
    # fxMesa renders straight to Glide (no X). -nosound: no /dev/dsp in the
    # container. -no8bit: full RGBA textures (no paletted-texture extension).
    timeout --signal=TERM $SECS /work/glquake -basedir /work/q -nosound -nocdaudio -no8bit \
        -width 320 -height 240 +'${QUAKE_CMDS:-map start}' >/tmp/gq.log 2>&1 || true
    echo '--- quake log tail ---'
    tail -20 /tmp/gq.log || true
    ls -la /work/glquake.trace
  "
}

render() {
  echo "[quake-glide] rendering trace through the Voodoo reference model..."
  "$TT" "$WORK/glquake.trace" --ref-only --output-dir "$HERE/shots" \
    >"$HERE/shots/render.log" 2>&1
  echo "[quake-glide] wrote shots/glquake.trace_ref.png (see shots/render.log)"
}

# --- Live board (PCIe) variant --------------------------------------------
# Same Mesa 3.4.2 + GLQuake, built natively with the host multilib toolchain
# (gcc -m32, i386 libX11/libXext/zlib dev packages; no Docker), against a
# libglide2x whose sim_* backend is sw/glide-homebrew/pcie/sim_pcie_harness.cpp
# (BAR0 MMIO on the Tang dock) instead of trace capture. The Glide sources are
# copied to work/native/glide-src and the Glide hunks of
# spinalvoodoo-glquake.patch (2 MB memory report, even/odd tLOD mask) are
# applied to the copy, so ext/SpinalVoodoo and its trace build are untouched.
# Everything lands in work/native/; run it with run-pcie.sh.
build_pcie() {
  local N="$WORK/native" PH="$REPO/sw/glide-homebrew/pcie/sim_pcie_harness.cpp"
  local CXX32="${CXX32:-g++ -m32}" CC32="${CC32:-gcc -m32}"
  mkdir -p "$N"
  fetch_sources

  echo "[quake-glide] pcie: building libglide2x with the PCIe BAR backend..."
  local GS="$N/glide-src/emu/glide"
  rm -rf "$N/glide-src"; mkdir -p "$GS"
  cp -r "$EXT/emu/glide/glide2x" "$EXT/emu/glide/swlibs" "$GS/"
  find "$GS" \( -name '*.o' -o -name '*.a' -o -name '*.so*' \) -delete
  (cd "$N/glide-src" && git init -q . && \
   git apply --include='emu/glide/*' "$HERE/patches/spinalvoodoo-glquake.patch" && rm -rf .git)
  $CXX32 -O2 -Wall -I"$EXT/emu/sim/harness" -c "$PH" -o "$N/sim_pcie_harness.o"
  make -C "$GS/glide2x/sst1/glide/src" -f Makefile.sim -j"$(nproc)" \
    TRACE_CAPTURE=1 SIM_DIR="$EXT/emu/sim" CC="$CC32" CXX="$CXX32" \
    BACKEND_OBJ="$N/sim_pcie_harness.o" BACKEND_DEPS="$N/sim_pcie_harness.o" \
    >"$N/glide.log" 2>&1 || { tail -20 "$N/glide.log"; return 1; }

  local G="$N/glide-pcie" g2x="$GS/glide2x/sst1" sw="$GS/swlibs"
  rm -rf "$G"; mkdir -p "$G/include" "$G/lib"
  cp "$g2x"/glide/src/*.h "$g2x"/incsrc/*.h "$g2x"/init/sst1vid.h "$sw"/fxmisc/3dfx.h "$G/include/"
  cp -a "$g2x"/lib/sst1/libglide2x.so* "$G/lib/"

  echo "[quake-glide] pcie: building Mesa 3.4.2 fx driver (native -m32)..."
  if [ ! -f "$N/Mesa-3.4.2/.patched" ]; then
    rm -rf "$N/Mesa-3.4.2"
    tar -C "$N" -xf "$WORK/MesaLib-3.4.2.tar.gz"
    sed -i \
      -e "s/-ansi -pedantic/-m32 -std=gnu89 -fcommon -D__FUNCTION__=__FILE__ -Wno-implicit-function-declaration -Wno-implicit-int -Wno-incompatible-pointer-types -Wno-int-conversion/g" \
      -e "s#-I/usr/X11R6/include#-I/usr/include#g" \
      -e "s#-L/usr/X11R6/lib#-L/usr/lib/i386-linux-gnu#g" \
      -e "s#-I/usr/include/glide -I/usr/local/glide/include#-I$G/include#" \
      -e "s#-L/usr/local/glide/lib#-L$G/lib#g" \
      "$N/Mesa-3.4.2/Make-config"
    sed -i 's/^gcc -shared/gcc -m32 -shared/' "$N/Mesa-3.4.2/bin/mklib.linux"
    touch "$N/Mesa-3.4.2/.patched"
  fi
  # libGL + libGLU (the widgets-sgi Motif target failing afterwards is fine).
  make -C "$N/Mesa-3.4.2" -j"$(nproc)" linux-glide >"$N/mesa.log" 2>&1 || true
  [ -f "$N/Mesa-3.4.2/lib/libGL.so" ] || { tail -20 "$N/mesa.log"; return 1; }

  echo "[quake-glide] pcie: building GLQuake (native -m32)..."
  rm -rf "$N/Quake"; cp -r "$WORK/Quake" "$N/Quake"
  local QSRC="$N/Quake/WinQuake" SRCS="" o
  sed -i "s/^#if defined __i386__.*/#if 0  \/* forced C-only build *\//" "$QSRC/quakedef.h"
  cp "$HERE/_fpcw_stub.c" "$QSRC/"
  for o in $(awk "/^GLQUAKE_OBJS=/,/^\$/" "$QSRC/Makefile.linuxi386" | grep -oE "[a-zA-Z0-9_]+\.o" | sed "s/\.o\$/.c/" | sort -u); do
    [ -f "$QSRC/$o" ] && SRCS="$SRCS $o"
  done
  (cd "$QSRC" && $CC32 -std=gnu89 -fcommon -fno-strict-aliasing -w -O2 -DGLQUAKE \
     -I"$HERE/stubs" -I. -I"$N/Mesa-3.4.2/include" -I"$G/include" \
     $SRCS gl_vidlinux.c _fpcw_stub.c -o "$N/glquake-pcie" \
     -L"$N/Mesa-3.4.2/lib" -lGL -lGLU -L"$G/lib" -lglide2x -lX11 -lXext -lm -ldl -lpthread)

  # Game data: link the shareware pak (never copied into git).
  local PAK=""
  for c in "$REPO/sw/quake-riscv/id1/pak0.pak" "$WORK/id1/pak0.pak"; do
    [ -f "$c" ] && PAK="$c" && break
  done
  mkdir -p "$N/q/id1"
  if [ -n "$PAK" ]; then ln -sf "$PAK" "$N/q/id1/pak0.pak"
  else echo "[quake-glide] NOTE: no pak0.pak found; put it at $N/q/id1/pak0.pak"; fi
  echo "[quake-glide] pcie: built $N/glquake-pcie; run with run-pcie.sh (see README)"
}

case "${1:-all}" in
  pcie) build_pcie ;;
  build)
    stage_patches
    build_host
    stage_glide_sdk
    fetch_sources
    build_mesa
    build_glquake
    ;;
  run) run_glquake ;;
  render) render ;;
  *)
    stage_patches
    build_host
    stage_glide_sdk
    fetch_sources
    build_mesa
    build_glquake
    run_glquake
    render
    ;;
esac
echo "[quake-glide] done."
