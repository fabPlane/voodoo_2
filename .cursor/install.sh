#!/usr/bin/env bash
# Cloud Agent bootstrap for the SpinalVoodoo FPGA ports.
#
# Installs the RTL toolchain (Verilator 5.x + scala-cli, which itself fetches
# JDK 17 per fpga/project.scala), pulls in the upstream Voodoo core submodule
# that fpga/ builds against, and warms the dependency cache. The FPGA bitstream
# backends (Gowin EDA, Vivado) are proprietary and are intentionally not
# installed here; simulation, RTL generation and lint work without them.
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

# 3. Upstream Voodoo core: fpga/project.scala references ../ext/SpinalVoodoo/src.
echo "[install] Initializing ext/SpinalVoodoo submodule..."
git submodule update --init ext/SpinalVoodoo

# 4. Warm the scala-cli / coursier dependency cache (SpinalHDL, scalatest, JDK 17)
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

echo "[install] Done. Try: cd fpga && make compile / make rtl / make lint / make test"
