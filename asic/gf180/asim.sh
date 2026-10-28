#!/bin/bash
# asim.sh TAG [CORESIMGEN_ARGS...]: ASIC study run. Snapshot the asic-gf180
# worktree's SpinalVoodoo, build CoreSim (single-TMU dock config + args),
# replay the GLQuake frame-1923 trace; results in ~/.cache/asic-est/simres/TAG.*
# The snapshot is deleted afterwards (disk).
E=/home/hyper/projects/vodoo_2-asic/ext/SpinalVoodoo
R=$HOME/.cache/asic-est/simres
tag=$1; shift
W=$HOME/.cache/simwork/asic-$tag
rm -rf $W; mkdir -p $W/emu
rsync -a --delete $E/src $E/project.scala $W/
for d in sim 86Box trace test; do [ -d $E/emu/$d ] && rsync -a --delete --exclude obj_dir --exclude rtl $E/emu/$d $W/emu/; done
mkdir -p $W/emu/sim/rtl
base="--gowin --sim-mem-mode pipelined --sim-mem-read-latency 40 --sim-mem-write-latency 20 --no-triangle-snapshot --pixel-config-slots --tex-line-words 16 --tex-cache-paired --skip-redundant-fbz-sync --pci-fifo-depth 2048 --tex-max-outstanding 47 --tex-fill-request-window 48 --fb-write-slots 4 --tex-hint-queue 8 --fill-offload"
echo "args: $base $*" > $R/$tag.log
EVF=""; ECX=""; case " $* " in *" --split-dies "[1-9]*) ECX="-DSIM_SPLIT";; esac; [ -n "$ECX" ] || case " $* " in *" --asic-chips "[2-9]*) N=$(echo " $* " | sed -E "s/.* --asic-chips ([0-9]+).*/\1/"); EVF="--flatten"; ECX="-DSIM_ASIC_CHIPS=$N";; esac
cd $W && make -C emu/test SIM_INTERFACE=core EXTRA_VERILATOR_FLAGS="$EVF" EXTRA_CXXFLAGS="$ECX" CORE_SIM_GEN_ARGS="$base $*" > $R/$tag.build.log 2>&1 || { echo "BUILD FAILED" >> $R/$tag.log; rm -rf $W; exit 1; }
mkdir -p $R/out_$tag
SIM_PERF=1 emu/test/obj_dir/trace_test $HOME/.cache/voodoo-traces/quake/ --output-dir $R/out_$tag > $R/$tag.full.log 2>&1
grep -aE "pixels mismatched|\[perf\] sim" $R/$tag.full.log >> $R/$tag.log
cd /; rm -rf $W
echo DONE >> $R/$tag.log
