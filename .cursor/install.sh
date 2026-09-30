#!/usr/bin/env bash
# Cloud Agent bootstrap for the SpinalVoodoo FPGA ports.
#
# Installs:
#   - Temurin JDK 17 (the JVM pinned by fpga/project.scala)
#   - scala-cli, with Scala 2.12.18 resolved on that JDK
#   - Verilator 5.052 (known-good for the whole-console simulator)
#   - libpng (the console HDMI grabber links it via pkg-config)
#   - the 32-bit toolchain for the Glide2x sim backend
#   - Gowin EDA Education 1.9.11.03 and the matching Programmer
#     (no license; education builds only)
#   - vendored submodules, then a warm Scala dependency cache
#
# The whole-console simulator lives with the Tang Console sources
# (amaranth/emu/console). After RTL generation:
#   make -C fpga rtl-console
#   make -C amaranth/emu/console run-test04
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

CURL_UA='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'
TEMIN_VERSION='17.0.20.1+1'
TEMIN_DIR="jdk-${TEMIN_VERSION}"
TEMIN_URL='https://github.com/adoptium/temurin17-binaries/releases/download/jdk-17.0.20.1%2B1/OpenJDK17U-jdk_x64_linux_hotspot_17.0.20.1_1.tar.gz'
TEMIN_SHA256='3808d1d15e3ec6bd5b84057fb5d84c33d8a1536a258146bcea2e603fc726e08e'
GOWIN_VERSION='1.9.11.03'
GOWIN_ROOT='/opt/gowin'
GOWIN_IDE_URL='https://cdn.gowinsemi.com.cn/Gowin_V1.9.11.03_Education_Linux.tar.gz'
GOWIN_IDE_MD5='d65912e3da9cdbebed92f0ccc5feb498'
GOWIN_PROG_URL='https://cdn.gowinsemi.com.cn/programmer1.9.11.03Education%282536%29.Linux.x86.tar.gz'
GOWIN_PROG_MD5='60c72afc050fc1d1b86b4856a5200a07'
VERILATOR_VERSION='5.052'

version_ge() {
  # True when $1 >= $2 (version sort). `head` closes the pipe early; ignore
  # the resulting SIGPIPE so `set -o pipefail` does not fail the check.
  local lo
  lo="$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -1 || true)"
  [[ "$lo" == "$2" ]]
}

apt_install() {
  sudo apt-get update -qq
  sudo apt-get install -y --no-install-recommends "$@"
}

# 1. Temurin JDK 17 as the default java/javac.
if ! java -version 2>&1 | grep -q 'Temurin-17'; then
  echo "[install] Installing Temurin JDK ${TEMIN_VERSION}..."
  tmp="$(mktemp -d)"
  curl -fL --retry 3 --retry-delay 2 -o "$tmp/temurin.tar.gz" "$TEMIN_URL"
  echo "${TEMIN_SHA256}  $tmp/temurin.tar.gz" | sha256sum -c -
  sudo mkdir -p /usr/lib/jvm
  sudo tar -xzf "$tmp/temurin.tar.gz" -C /usr/lib/jvm
  sudo ln -sfn "/usr/lib/jvm/${TEMIN_DIR}" /usr/lib/jvm/temurin-17
  sudo update-alternatives --install /usr/bin/java java /usr/lib/jvm/temurin-17/bin/java 1700
  sudo update-alternatives --install /usr/bin/javac javac /usr/lib/jvm/temurin-17/bin/javac 1700
  sudo update-alternatives --set java /usr/lib/jvm/temurin-17/bin/java
  sudo update-alternatives --set javac /usr/lib/jvm/temurin-17/bin/javac
  sudo tee /etc/profile.d/temurin-17.sh >/dev/null <<'EOF'
export JAVA_HOME=/usr/lib/jvm/temurin-17
export PATH="$JAVA_HOME/bin:$PATH"
EOF
  rm -rf "$tmp"
fi
echo "[install] $(java -version 2>&1 | head -1)"

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

echo "[install] Checking Scala 2.12.18 on Temurin 17..."
scala_probe="$(
  printf 'object V { def main(a: Array[String]): Unit = println(System.getProperty("java.vendor") + " " + System.getProperty("java.version") + " scala " + util.Properties.versionNumberString) }\n' \
    | scala-cli run --scala 2.12.18 --jvm temurin:17 -
)"
echo "[install] $scala_probe"
printf '%s\n' "$scala_probe" | grep -q 'Adoptium'
printf '%s\n' "$scala_probe" | grep -q '17\.'
printf '%s\n' "$scala_probe" | grep -q 'scala 2.12.18'

# 3. Verilator 5.052 — apt on this image is 5.020, which is older than the
#    console-simulator runbook. Build the release tag into /usr/local.
if ! command -v verilator >/dev/null 2>&1 || ! version_ge "$(verilator --version | awk '{print $2}')" "$VERILATOR_VERSION"; then
  echo "[install] Building Verilator ${VERILATOR_VERSION}..."
  apt_install git help2man perl python3 make autoconf g++ flex bison ccache \
    libfl2 libfl-dev zlib1g-dev
  tmp="$(mktemp -d)"
  git clone --depth 1 --branch "v${VERILATOR_VERSION}" https://github.com/verilator/verilator.git "$tmp/verilator"
  (
    cd "$tmp/verilator"
    autoconf
    ./configure --prefix /usr/local
    make -j"$(nproc)"
    sudo make install
  )
  rm -rf "$tmp"
fi
if dpkg -s verilator >/dev/null 2>&1; then
  echo "[install] Removing the older apt Verilator so /usr/local/bin/verilator is the only one..."
  sudo apt-get remove -y verilator
fi
hash -r
echo "[install] $(verilator --version)"
version_ge "$(verilator --version | awk '{print $2}')" "$VERILATOR_VERSION"

# 4. libpng for the whole-console HDMI grabber.
if ! pkg-config --exists libpng; then
  echo "[install] Installing libpng development files..."
  apt_install pkg-config libpng-dev
fi
echo "[install] libpng $(pkg-config --modversion libpng)"

# 5. 32-bit toolchain for the Glide2x sim backend and the homebrew Glide demos
#    (sw/glide-homebrew). libglide2x is built 32-bit and links zlib + X11.
if ! echo 'int main(void){return 0;}' | gcc -m32 -x c - -o /dev/null >/dev/null 2>&1; then
  echo "[install] Installing 32-bit Glide toolchain (multilib + i386 libs)..."
  sudo dpkg --add-architecture i386
  apt_install gcc-multilib g++-multilib \
    zlib1g-dev:i386 libx11-dev:i386 libxext-dev:i386
fi
echo "[install] 32-bit gcc: $(gcc -m32 --version | head -1)"

# 6. Gowin EDA Education + Programmer. Education builds do not need a license.
write_gowin_wrappers() {
  sudo tee /usr/local/bin/gw_sh >/dev/null <<'EOF'
#!/bin/bash
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/tmp/runtime-${USER:-ubuntu}}"
mkdir -p "$XDG_RUNTIME_DIR"
chmod 700 "$XDG_RUNTIME_DIR" 2>/dev/null || true
export LD_LIBRARY_PATH="/opt/gowin/IDE/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export LD_PRELOAD="/usr/lib/x86_64-linux-gnu/libfreetype.so.6:/lib/x86_64-linux-gnu/libz.so.1${LD_PRELOAD:+:$LD_PRELOAD}"
export QT_PLUGIN_PATH="/opt/gowin/IDE/plugins/qt"
export QT_QPA_PLATFORM_PLUGIN_PATH="/opt/gowin/IDE/plugins/qt/platforms"
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-minimal}"
export QT_XCB_GL_INTEGRATION="${QT_XCB_GL_INTEGRATION:-none}"
exec /opt/gowin/IDE/bin/gw_sh "$@"
EOF
  sudo tee /usr/local/bin/programmer_cli >/dev/null <<'EOF'
#!/bin/bash
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/tmp/runtime-${USER:-ubuntu}}"
mkdir -p "$XDG_RUNTIME_DIR"
chmod 700 "$XDG_RUNTIME_DIR" 2>/dev/null || true
export LD_LIBRARY_PATH="/opt/gowin/Programmer/bin${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export LD_PRELOAD="/usr/lib/x86_64-linux-gnu/libfreetype.so.6:/lib/x86_64-linux-gnu/libz.so.1${LD_PRELOAD:+:$LD_PRELOAD}"
export QT_PLUGIN_PATH="/opt/gowin/Programmer/bin/PyQt5/qt-plugins"
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-minimal}"
exec /opt/gowin/Programmer/bin/programmer_cli "$@"
EOF
  sudo chmod 755 /usr/local/bin/gw_sh /usr/local/bin/programmer_cli
}

if [[ ! -x "${GOWIN_ROOT}/IDE/bin/gw_sh" || ! -x "${GOWIN_ROOT}/Programmer/bin/programmer_cli" || "$(cat "${GOWIN_ROOT}/EDUCATION_VERSION" 2>/dev/null || true)" != "$GOWIN_VERSION" ]]; then
  echo "[install] Installing Gowin EDA Education ${GOWIN_VERSION}..."
  apt_install ca-certificates curl \
    libfreetype6 libfontconfig1 libftdi1-2 libhidapi-hidraw0 libudev1 \
    libnss3 libxcomposite1 libxdamage1 libxrandr2 libxtst6 \
    libasound2t64 libqt5widgets5t64 libqt5gui5t64 libqt5core5t64 \
    libqt5x11extras5 libxcb1 libxcb-glx0 libxcb-keysyms1 libxcb-image0 \
    libxcb-shm0 libxcb-icccm4 libxcb-sync1 libxcb-xfixes0 \
    libxcb-render-util0 libx11-xcb1 libgl1
  tmp="$(mktemp -d)"
  curl -fL --retry 3 --retry-delay 2 -A "$CURL_UA" -o "$tmp/ide.tar.gz" "$GOWIN_IDE_URL"
  echo "${GOWIN_IDE_MD5}  $tmp/ide.tar.gz" | md5sum -c -
  curl -fL --retry 3 --retry-delay 2 -A "$CURL_UA" -o "$tmp/prog.tar.gz" "$GOWIN_PROG_URL"
  echo "${GOWIN_PROG_MD5}  $tmp/prog.tar.gz" | md5sum -c -
  sudo rm -rf "$GOWIN_ROOT"
  sudo mkdir -p "$GOWIN_ROOT"
  sudo tar -xzf "$tmp/ide.tar.gz" -C "$GOWIN_ROOT"
  sudo tar -xzf "$tmp/prog.tar.gz" -C "$GOWIN_ROOT"
  echo "$GOWIN_VERSION" | sudo tee "${GOWIN_ROOT}/EDUCATION_VERSION" >/dev/null
  sudo chmod -R a+rX "$GOWIN_ROOT"
  rm -rf "$tmp"
fi
write_gowin_wrappers
echo "[install] $(programmer_cli --help 2>/dev/null | awk '/Version/{print; exit}')"
printf 'puts gowin-ok\nexit\n' | gw_sh | grep -q 'gowin-ok'

# 7. Vendored submodules:
#    - ext/SpinalVoodoo: fpga/project.scala references ../ext/SpinalVoodoo/src.
#    - ext/tangcore: the Sipeed Tang Console loader/menu the Voodoo core targets.
#      Only the superproject is initialized; its per-core nested submodules are
#      large and only needed to build TangCore bitstreams (Gowin EDA required).
#    - ext/nostalgic-glide-demo: efliks' open-source (GPLv3) 3Dfx Glide demo,
#      built against our libglide2x for sw/glide-demos/nostalgic.
echo "[install] Initializing ext/SpinalVoodoo, ext/tangcore, ext/nostalgic-glide-demo submodules..."
git submodule update --init ext/SpinalVoodoo ext/tangcore ext/nostalgic-glide-demo

# 8. Warm the scala-cli / coursier dependency cache (SpinalHDL, scalatest, JDK 17)
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

# 9. Build the homebrew Glide demos + console ROMs (best-effort validation of
#    the 32-bit Glide sim pipeline). ROMs are committed, so failure is non-fatal.
echo "[install] Building homebrew Glide demos (best-effort)..."
( make -C sw/glide-homebrew >/dev/null 2>&1 && \
    echo "[install] Homebrew Glide ROMs built in sw/glide-homebrew/roms/." ) || \
  echo "[install] Homebrew Glide build skipped/failed (committed ROMs remain)."

echo "[install] Done."
echo "[install]   Java:  $(java -version 2>&1 | head -1)"
echo "[install]   Scala: 2.12.18 via scala-cli (fpga/project.scala)"
echo "[install]   Sim:   verilator $(verilator --version | awk '{print $2}'), libpng $(pkg-config --modversion libpng)"
echo "[install]   Gowin: Education ${GOWIN_VERSION} (gw_sh, programmer_cli)"
echo "[install]   RTL:   cd fpga && make compile / rtl / lint / test"
echo "[install]   Glide console sim: make -C amaranth/emu/console run-test04"
