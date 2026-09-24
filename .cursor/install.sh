#!/usr/bin/env bash
# Cloud Agent bootstrap for the SpinalVoodoo FPGA ports.
#
# Installs the RTL toolchain (Verilator 5.x + scala-cli, which itself fetches
# JDK 17 per fpga/project.scala), the 32-bit toolchain for the Glide2x sim
# backend + homebrew demos, pulls in the vendored submodules (upstream Voodoo
# core and TangCore), and warms the dependency cache. The FPGA bitstream
# backends (Gowin EDA, Vivado) are proprietary and are intentionally not
# installed here; simulation, RTL generation, lint and Glide trace capture all
# work without them.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# 1. Verilator 5.x — required by `make lint` and the SpinalSim test suite.
if ! command -v verilator >/dev/null 2>&1; then
  echo "[install] Installing Verilator via apt..."
  sudo apt-get update -qq
  sudo apt-get install -y --no-install-recommends verilator
fi
echo "[install] $(verilator --version)"

# 2. scala-cli — builds the SpinalHDL board ports and runs the tests.
if ! command -v scala-cli >/dev/null 2>&1; then
  echo "[install] Installing scala-cli..."
  tmp="$(mktemp -d)"
  curl -fsSL \
    https://github.com/VirtusLab/scala-cli/releases/latest/download/scala-cli-x86_64-pc-linux.gz \
    -o "$tmp/scala-cli.gz"
  gunzip -f "$tmp/scala-cli.gz"
  chmod +x "$tmp/scala-cli"
  sudo mv "$tmp/scala-cli" /usr/local/bin/scala-cli
  rm -rf "$tmp"
fi
echo "[install] scala-cli $(scala-cli version --cli-version 2>/dev/null || scala-cli version 2>&1 | head -1)"

# 3. 32-bit toolchain for the Glide2x sim backend and the homebrew Glide demos
#    (sw/glide-homebrew). libglide2x is built 32-bit and links zlib + X11.
if ! echo 'int main(void){return 0;}' | gcc -m32 -x c - -o /dev/null >/dev/null 2>&1; then
  echo "[install] Installing 32-bit Glide toolchain (multilib + i386 libs)..."
  sudo dpkg --add-architecture i386
  sudo apt-get update -qq
  sudo apt-get install -y --no-install-recommends \
    gcc-multilib g++-multilib \
    zlib1g-dev:i386 libx11-dev:i386 libxext-dev:i386
fi
echo "[install] 32-bit gcc: $(gcc -m32 --version | head -1)"

# 4. Vendored submodules:
#    - ext/SpinalVoodoo: fpga/project.scala references ../ext/SpinalVoodoo/src.
#    - ext/tangcore: the Sipeed Tang Console loader/menu the Voodoo core targets.
#      Only the superproject is initialized; its per-core nested submodules are
#      large and only needed to build TangCore bitstreams (Gowin EDA required).
#    - ext/nostalgic-glide-demo: efliks' open-source (GPLv3) 3Dfx Glide demo,
#      built against our libglide2x for sw/glide-demos/nostalgic.
echo "[install] Initializing ext/SpinalVoodoo, ext/tangcore, ext/nostalgic-glide-demo submodules..."
git submodule update --init ext/SpinalVoodoo ext/tangcore ext/nostalgic-glide-demo

# 5. Warm the scala-cli / coursier dependency cache (SpinalHDL, scalatest, JDK 17)
#    so the first `make compile` / `make test` is fast. Best-effort: never fails
#    the install. Two steps because the project's test scope is only resolved
#    once main compiles, so the test-framework jars are fetched directly here.
echo "[install] Warming the build dependency cache (best-effort)..."
( cd fpga && scala-cli compile . >/dev/null 2>&1 ) || true
printf 'object _Warm\n' | scala-cli compile --scala 2.12.18 \
  --dep com.github.spinalhdl::spinalhdl-core:1.15.0 \
  --dep com.github.spinalhdl::spinalhdl-lib:1.15.0 \
  --dep org.scodec::scodec-core:1.11.10 \
  --dep org.scodec::scodec-bits:1.1.38 \
  --dep org.scalatest::scalatest:3.2.18 \
  --dep com.github.spinalhdl::spinalhdl-tester:1.15.0 - >/dev/null 2>&1 || true
echo "[install] Dependency warm-up finished (build results ignored)."

# 6. Build the homebrew Glide demos + console ROMs (best-effort validation of
#    the 32-bit Glide sim pipeline). ROMs are committed, so failure is non-fatal.
echo "[install] Building homebrew Glide demos (best-effort)..."
( make -C sw/glide-homebrew >/dev/null 2>&1 && \
    echo "[install] Homebrew Glide ROMs built in sw/glide-homebrew/roms/." ) || \
  echo "[install] Homebrew Glide build skipped/failed (committed ROMs remain)."

echo "[install] Done."
echo "[install]   RTL:   cd fpga && make compile / rtl / lint / test"
echo "[install]   Glide: make -C sw/glide-homebrew (+ make -C sw/glide-homebrew summary)"
