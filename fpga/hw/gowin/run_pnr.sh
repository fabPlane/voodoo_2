#!/usr/bin/env bash
# Run Gowin synth+PnR. From a Mac this prefers hyper-ms-lin (~2x vs M2 Max)
# and falls back to local gw_sh only if Linux is down or already in gw_sh.
set -euo pipefail

usage() {
  echo "usage: $0 console|console-sdram|dock" >&2
  echo "  GOWIN_PNR_FORCE_LOCAL=1   skip the Linux box" >&2
  echo "  GOWIN_PNR_HOST=...        LAN SSH alias (default hyper-ms-lin)" >&2
  exit 2
}

TARGET="${1:-}"
[[ "$TARGET" == "console" || "$TARGET" == "console-sdram" || "$TARGET" == "dock" ]] || usage

HERE="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
FPGA_ROOT="$(CDPATH= cd -- "$HERE/../.." && pwd)"
REMOTE_ROOT="${GOWIN_PNR_REMOTE_ROOT:-/home/hyper/projects/me/vodoo_2}"
LAN_HOST="${GOWIN_PNR_HOST:-hyper-ms-lin}"
TS_HOST="${GOWIN_PNR_FALLBACK_HOST:-hyper-ms-lin-ts}"
SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=8 -o ServerAliveInterval=30 -o ServerAliveCountMax=12)

ssh_ok() {
  ssh "${SSH_OPTS[@]}" "$1" true >/dev/null 2>&1
}

# Real synth+PnR only (`gw_sh build*.tcl`). Ignore hung `gw_sh -h` /
# `generate_ip.tcl` leftovers. The `[/]` keeps this ssh command from matching.
remote_gw_sh_busy() {
  ssh "${SSH_OPTS[@]}" "$1" 'pgrep -f "[/]gw_sh build(_console(_sdram)?)?\\.tcl" >/dev/null'
}

local_gw_sh_busy() {
  pgrep -f '[/]gw_sh build(_console(_sdram)?)?\.tcl' >/dev/null 2>&1
}

pick_remote_host() {
  if ssh_ok "$LAN_HOST"; then
    printf '%s\n' "$LAN_HOST"
  elif ssh_ok "$TS_HOST"; then
    printf '%s\n' "$TS_HOST"
  fi
}

ip_ready() {
  if [[ "$TARGET" == console-sdram ]]; then
    [[ -f "$FPGA_ROOT/hw/gowin/ip/Gowin_PLL_Hdmi/Gowin_PLL_Hdmi.v" ]]
    return
  fi
  [[ -f "$FPGA_ROOT/hw/gowin/ip/DDR3_Memory_Interface/ddr3_memory_interface.v" ]] &&
    [[ -f "$FPGA_ROOT/hw/gowin/ip/Gowin_PLL_Sys/Gowin_PLL_Sys.v" ]]
}

ensure_ip() {
  if ip_ready; then
    return 0
  fi
  if [[ "$(uname)" == Darwin && -d /tmp/ddr3_framebuffer_gowin/src ]]; then
    echo "gowin pnr: generating IP via generate_ip.sh" >&2
    (cd "$HERE/console" && ./generate_ip.sh)
    return 0
  fi
  echo "gowin pnr: missing fpga/hw/gowin/ip (DDR3 + PLL). See hw/gowin/ip.md" >&2
  exit 1
}

run_gw_sh_here() {
  local dir="$1" tcl="$2"
  export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}"
  if [[ "$(uname)" == Darwin ]]; then
    local gowin_home="${GOWINHOME:-/Applications/GowinIDE.app/Contents/Resources/Gowin_EDA}"
    export GOWINHOME="$gowin_home"
    export PATH="$gowin_home/IDE/bin:$PATH"
    export DYLD_LIBRARY_PATH="$gowin_home/IDE/lib"
    export DYLD_FRAMEWORK_PATH="$gowin_home/IDE/lib"
  else
    export PATH="/usr/local/bin:${GOWINHOME:-$HOME/opt/gowin}/IDE/bin:$PATH"
    unset LD_LIBRARY_PATH
  fi
  echo "gowin pnr: local $(hostname)  $(command -v gw_sh)  $dir/$tcl" >&2
  (cd "$dir" && gw_sh "$tcl")
}

run_local() {
  ensure_ip
  if [[ "$TARGET" == console ]]; then
    run_gw_sh_here "$HERE/console" build_console.tcl
  elif [[ "$TARGET" == console-sdram ]]; then
    run_gw_sh_here "$HERE/console-sdram" build_console_sdram.tcl
  else
    run_gw_sh_here "$HERE" build.tcl
  fi
}

sync_to_remote() {
  local host="$1"
  local dest="$host:$REMOTE_ROOT/fpga"
  ssh "${SSH_OPTS[@]}" "$host" "mkdir -p '$REMOTE_ROOT/fpga/rtl/gowin' '$REMOTE_ROOT/fpga/hw/gowin'"
  rsync -a --delete \
    --exclude impl \
    --exclude '.DS_Store' \
    "$FPGA_ROOT/hw/gowin/ip/" "$dest/hw/gowin/ip/"
  if [[ "$TARGET" == console || "$TARGET" == console-sdram ]]; then
    rsync -a "$FPGA_ROOT/rtl/gowin/TangConsole138k.v" "$dest/rtl/gowin/"
  fi
  if [[ "$TARGET" == console ]]; then
    rsync -a --delete --exclude impl --exclude '.DS_Store' \
      "$FPGA_ROOT/hw/gowin/console/" "$dest/hw/gowin/console/"
  elif [[ "$TARGET" == console-sdram ]]; then
    rsync -a --delete --exclude impl --exclude '.DS_Store' \
      "$FPGA_ROOT/hw/gowin/console-sdram/" "$dest/hw/gowin/console-sdram/"
  else
    rsync -a "$FPGA_ROOT/rtl/gowin/TangMega138kPro.v" "$dest/rtl/gowin/"
    rsync -a "$FPGA_ROOT/hw/gowin/build.tcl" \
      "$FPGA_ROOT/hw/gowin/tang_mega_138k_pro.cst" \
      "$FPGA_ROOT/hw/gowin/tang_mega_138k_pro.sdc" \
      "$dest/hw/gowin/"
    rsync -a --delete --exclude '.DS_Store' \
      "$FPGA_ROOT/hw/gowin/src/" "$dest/hw/gowin/src/"
  fi
}

sync_from_remote() {
  local host="$1"
  local src="$host:$REMOTE_ROOT/fpga"
  if [[ "$TARGET" == console ]]; then
    mkdir -p "$FPGA_ROOT/hw/gowin/console/impl"
    rsync -a "$src/hw/gowin/console/impl/" "$FPGA_ROOT/hw/gowin/console/impl/"
  elif [[ "$TARGET" == console-sdram ]]; then
    mkdir -p "$FPGA_ROOT/hw/gowin/console-sdram/impl"
    rsync -a "$src/hw/gowin/console-sdram/impl/" "$FPGA_ROOT/hw/gowin/console-sdram/impl/"
  else
    mkdir -p "$FPGA_ROOT/hw/gowin/impl"
    rsync -a "$src/hw/gowin/impl/" "$FPGA_ROOT/hw/gowin/impl/"
  fi
}

run_remote() {
  local host="$1"
  local remote_dir remote_tcl
  if [[ "$TARGET" == console ]]; then
    remote_dir="$REMOTE_ROOT/fpga/hw/gowin/console"
    remote_tcl=build_console.tcl
  elif [[ "$TARGET" == console-sdram ]]; then
    remote_dir="$REMOTE_ROOT/fpga/hw/gowin/console-sdram"
    remote_tcl=build_console_sdram.tcl
  else
    remote_dir="$REMOTE_ROOT/fpga/hw/gowin"
    remote_tcl=build.tcl
  fi
  echo "gowin pnr: $TARGET on $host ($remote_dir/$remote_tcl)" >&2
  ensure_ip
  sync_to_remote "$host"
  ssh "${SSH_OPTS[@]}" "$host" "export PATH=/usr/local/bin:\$PATH QT_QPA_PLATFORM=offscreen; unset LD_LIBRARY_PATH; cd '$remote_dir' && gw_sh '$remote_tcl'"
  sync_from_remote "$host"
}

if [[ "${GOWIN_PNR_FORCE_LOCAL:-0}" == 1 ]]; then
  echo "gowin pnr: GOWIN_PNR_FORCE_LOCAL=1" >&2
  run_local
  exit 0
fi

# Already on the Linux box: this *is* the preferred machine.
if [[ "$(uname)" == Linux ]]; then
  run_local
  exit 0
fi

HOST="$(pick_remote_host || true)"
if [[ -n "${HOST}" ]] && remote_gw_sh_busy "$HOST"; then
  echo "gowin pnr: $HOST already running gw_sh; falling back to Mac" >&2
  HOST=""
fi

if [[ -n "${HOST}" ]]; then
  run_remote "$HOST"
  exit 0
fi

if local_gw_sh_busy; then
  echo "gowin pnr: Linux unavailable/busy and Mac already has gw_sh. Not starting a second PnR." >&2
  exit 1
fi

echo "gowin pnr: Linux down or busy; running on Mac" >&2
run_local
