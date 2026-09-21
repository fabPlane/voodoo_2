#!/usr/bin/env bash
# Run build_hdmi_demo.tcl on hyper-ms-lin and copy the results back.
#
# Called by `make hdmi-demo` from amaranth/ after the Verilog has been
# generated into hw/hdmi_demo/build/, linted and simulated. Steps:
#
#   1. rsync this worktree to hyper-ms-lin:/home/hyper/projects/me/vodoo_2-amaranth/
#      with the AGENTS.md excludes (never to the shared vodoo_2/ tree);
#   2. refuse to run if any gw_sh is already running on the box (one PnR per
#      box; no silent fallback to the Mac);
#   3. `cd hw/hdmi_demo/build && gw_sh ../build_hdmi_demo.tcl` there;
#   4. copy impl/pnr/*.fs, *.bin and the synth/PnR logs and reports back
#      into hw/hdmi_demo/build/.
#
# Environment: HDMI_DEMO_HOST (default hyper-ms-lin), HDMI_DEMO_REMOTE_TREE.

set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"          # amaranth/hw/hdmi_demo
worktree="$(cd "$here/../../.." && pwd)"                       # repo root (the worktree)
rel="amaranth/hw/hdmi_demo"

host="${HDMI_DEMO_HOST:-hyper-ms-lin}"
remote_tree="${HDMI_DEMO_REMOTE_TREE:-/home/hyper/projects/me/vodoo_2-amaranth}"

case "$worktree" in
    */.claude/worktrees/*) ;;
    *)
        # AGENTS.md: only the rewrite worktree may sync to vodoo_2-amaranth.
        # From any other checkout, refuse rather than clobber someone's tree.
        echo "run_pnr.sh: $worktree is not the amaranth-rewrite worktree; refusing to rsync" >&2
        exit 2 ;;
esac

[ -f "$here/build/HdmiDemoTop.v" ] || { echo "run_pnr.sh: build/HdmiDemoTop.v missing (make hdmi-demo generates it)" >&2; exit 2; }
[ -f "$worktree/fpga/hw/gowin/ip/Gowin_PLL_Hdmi/Gowin_PLL_Hdmi.v" ] || {
    echo "run_pnr.sh: fpga/hw/gowin/ip/Gowin_PLL_Hdmi is missing (gitignored; copy it from the main checkout)" >&2; exit 2; }

ssh_opts=(-o ConnectTimeout=8 -o BatchMode=yes -o ServerAliveInterval=30)

echo "== reachability: $host"
ssh "${ssh_opts[@]}" "$host" 'echo ok; hostname' || { echo "run_pnr.sh: $host unreachable; not falling back to the Mac" >&2; exit 3; }

echo "== gw_sh check on $host"
if busy="$(ssh "${ssh_opts[@]}" "$host" 'pgrep -af "[/]gw_sh"')"; then
    echo "run_pnr.sh: a gw_sh is already running on $host -- not stacking a second one:" >&2
    echo "$busy" >&2
    exit 4
fi

echo "== rsync $worktree/ -> $host:$remote_tree/"
rsync -aH --delete \
    --exclude '.DS_Store' \
    --exclude 'fpga/.scala-build' \
    --exclude 'fpga/.bsp' \
    --exclude 'fpga/rtl' \
    --exclude 'fpga/simWorkspace' \
    --exclude 'fpga/hw/gowin/impl' \
    --exclude 'fpga/hw/gowin/console/impl' \
    --exclude 'fpga/test/voodoo/gowin/.scala-build' \
    --exclude 'fpga/test/voodoo/gowin/.bsp' \
    --exclude 'ext/SpinalVoodoo/.scala-build' \
    --exclude 'ext/SpinalVoodoo/.bsp' \
    --exclude 'sw/quake-riscv/build' \
    --exclude 'sw/quake-riscv/build-rv32' \
    --exclude 'sw/quake-riscv/.toolchains' \
    --exclude 'sw/quake-riscv/.cache' \
    --exclude 'hardware/tang-console-138k/sd-card-backup' \
    --exclude 'outputs' \
    --exclude '__pycache__' \
    --exclude 'amaranth/.venv' \
    --exclude 'amaranth/rtl' \
    --exclude 'amaranth/emu/sim/obj_dir' \
    --exclude "$rel/build/impl" \
    "$worktree/" "$host:$remote_tree/"

echo "== gw_sh on $host"
ssh "${ssh_opts[@]}" "$host" "set -e; cd '$remote_tree/$rel/build' && rm -rf impl && /usr/local/bin/gw_sh ../build_hdmi_demo.tcl" \
    2>&1 | tee "$here/build/gw_sh.log"

echo "== copy back"
mkdir -p "$here/build/impl/pnr" "$here/build/impl/gwsynthesis"
rsync -a --include='*/' --include='*.fs' --include='*.bin' --include='*.log' --include='*.rpt.txt' --include='*.rpt.html' \
    --include='*.html' --include='*.log.txt' --exclude='*' \
    "$host:$remote_tree/$rel/build/impl/" "$here/build/impl/"

if grep -q "Bitstream generation completed" "$here/build/gw_sh.log"; then
    echo "== OK: $here/build/impl/pnr/amaranth_hdmi_demo.fs"
else
    echo "run_pnr.sh: no 'Bitstream generation completed' in gw_sh.log" >&2
    exit 5
fi
