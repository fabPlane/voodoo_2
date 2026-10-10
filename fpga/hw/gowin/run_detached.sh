#!/bin/bash
# Run gw_sh build.tcl in fpga/hw/<dir> detached from the shell (survives
# agent/tool timeouts), retrying on Sipeed float-licence drops.
#   setsid nohup hw/gowin/run_detached.sh <hw dir name> <tag> &
# Log: $HOME/.cache/gowin-builds/<tag>.log, status (DONE line): <tag>.status
set -u
HW=$(cd "$(dirname "$0")/.." && pwd)
OUT="$HOME/.cache/gowin-builds"; mkdir -p "$OUT"
. "$HW/gowin/env.sh"
cd "$HW/$1" || exit 1
# One build per directory: another agent may build elsewhere in parallel.
exec 8>>.build.lock
if ! flock -n 8; then echo "build dir $1 busy (another gw_sh build)" >> "$OUT/$2.status"; echo "DONE busy $(date)" >> "$OUT/$2.status"; exit 1; fi
rm -rf impl
for i in $(seq 1 20); do
  gw_sh build.tcl > "$OUT/$2.log" 2>&1
  if grep -qi "Server not responding\|License verification failed" "$OUT/$2.log"; then
    echo "license retry $i" >> "$OUT/$2.status"; sleep 30; continue
  fi
  break
done
echo "DONE $(date)" >> "$OUT/$2.status"
