#!/bin/bash
# Run gw_sh build.tcl in fpga/hw/<dir> detached from the shell (survives
# agent/tool timeouts), retrying while no licence seat is free.
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
# Licences: $GOWINHOME/IDE/bin (gwlicense.ini -> our 1-seat floating server
# on this PC) first; when that seat is busy or down, IDE/bin_sipeed (Sipeed's
# shared server, seats can be busy for an hour).
licence_failed() { grep -qi "Server not responding\|License verification failed\|No more license available\|License information lost" "$1"; }
for i in $(seq 1 200); do
  gw_sh build.tcl > "$OUT/$2.log" 2>&1
  licence_failed "$OUT/$2.log" || { echo "licence: ours" >> "$OUT/$2.status"; break; }
  if [ -x "$GOWINHOME/IDE/bin_sipeed/gw_sh" ]; then
    "$GOWINHOME/IDE/bin_sipeed/gw_sh" build.tcl > "$OUT/$2.log" 2>&1
    licence_failed "$OUT/$2.log" || { echo "licence: sipeed" >> "$OUT/$2.status"; break; }
  fi
  echo "license retry $i" >> "$OUT/$2.status"; sleep 30
done
# Keep the reports and bitstream: the next build in this dir wipes impl/.
mkdir -p "$OUT/reports/$2"
cp impl/pnr/*.tr* impl/pnr/*.rpt* impl/pnr/*.html impl/gwsynthesis/*_syn_rsc.xml "$OUT/reports/$2/" 2>/dev/null
cp impl/pnr/*.fs "$OUT/reports/$2/" 2>/dev/null
echo "DONE $(date)" >> "$OUT/$2.status"
