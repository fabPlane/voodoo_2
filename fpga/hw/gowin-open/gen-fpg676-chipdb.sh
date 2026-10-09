#!/bin/sh
# Regenerate chipdb-GW5AST-138C-fpg676.bin: the GW5AST-138C nextpnr database
# with the Tang Mega 138K Pro Dock package (FCPBGA676A) added.
#
# Why this recipe: apicula's stock database is built from Gowin data that
# lists GW5AST-138C only in PBGA484A. Gowin 1.9.12.04 adds FCPBGA676A but its
# .dat layout shifted and apicula's parser rejects it. So we use the Education
# 1.9.11.03 data (which the fabPlane apicula parser handles) with the two
# FCPBGA676A rows from 1.9.12.04 appended to device_package.csv and
# device_info.csv; the FCPBGA676A.json pin file is identical in both versions.
#
# Inputs (all on hyper-ai):
#   GOWINHOME overlay  /home/hyper/gowin/edu-1.9.11.03-fpg676-overlay
#   apicula            fabPlane/apicula at cd6d492 (master). Later commits
#                      (wip/gw5ast-bitstream-jtag-pips) drop pips and break
#                      routing of a plain counter; do not use them here.
#   nextpnr arch gen   fabPlane/nextpnr-dbgen with the PINCFG VCC/VSS
#                      constant-wire patch (uncommitted there).
#   bbasm              built from nextpnr-dbgen/bba/main.cc
#                      (g++ -O2 -std=c++17 main.cc -lboost_program_options)
set -eu
OVERLAY=${OVERLAY:-/home/hyper/gowin/edu-1.9.11.03-fpg676-overlay}
APICULA=${APICULA:-/home/hyper/projects/fabPlane/apicula-head}
NEXTPNR=${NEXTPNR:-/home/hyper/projects/fabPlane/nextpnr-dbgen}
PY=${PY:-/home/hyper/projects/fabPlane/apicula/.venv/bin/python}
BBASM=${BBASM:-/tmp/bbasm}
OUT=$(cd "$(dirname "$0")" && pwd)/chipdb-GW5AST-138C-fpg676.bin

rm -f "$APICULA/apycula/GW5AST-138C.msgpack.xz"
(cd "$APICULA" && GOWINHOME="$OVERLAY" PYTHONPATH="$APICULA" "$PY" -m apycula.chipdb_builder GW5AST-138C)
(cd "$NEXTPNR/himbaechel/uarch/gowin" && \
  PYTHONPATH="$APICULA:$NEXTPNR/himbaechel" "$PY" gowin_arch_gen.py -d GW5AST-138C -o /tmp/chipdb-fpg676.bba)
"$BBASM" --le /tmp/chipdb-fpg676.bba "$OUT"
echo "wrote $OUT"
echo "pack with: PYTHONPATH=$APICULA gowin_pack ..."
