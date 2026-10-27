#!/bin/bash
# seed_search.sh: hunt for a timing-clean 54.5 MHz placement. Cycles through
# (rtl, place_option, max_fanout) combos from a q33-based template dir,
# three builds at a time; kills builds running > 90 min; logs results to
# ~/.cache/gowin-builds/seed_search.log; stops at the first all-clean build
# (its .fs copied to ~/.cache/gowin-builds/fs/<tag>_54M.fs).
HW=/home/hyper/projects/vodoo_2/fpga/hw; OUT=$HOME/.cache/gowin-builds; LOG=$OUT/seed_search.log
TEMPLATE=${TEMPLATE:-$HW/gowin-f54q33}; PREFIX=${PREFIX:-ss}
COMBOS=("rtl_b193 4 86" "rtl_b191 4 96" "rtl_b193 3 90" "rtl_b192 4 84" "rtl_b191 3 86" "rtl_b193 4 98" "rtl_b192 3 96" "rtl_b191 4 80" "rtl_b193 3 82" "rtl_b192 4 100" "rtl_b191 3 92" "rtl_b193 4 76")
clean() { awk '/^2.4 Total Negative Slack Summary/,/^3\./' $OUT/reports/$1/*.tr 2>/dev/null | grep -E "setup|hold" | awk '$3!="0.000"' | wc -l; }
fmax() { sed -n '/Max Frequency Summary/,/Total Negative/p' $OUT/reports/$1/*.tr 2>/dev/null | grep -E "clk100|clk_core|ddr_clk_out" | awk '{printf "%s=%s ",$2,$4}'; }
check_slots() {
  for s in 0 1 2; do dd=$HW/gowin-ss$s; [ -f $dd/.running ] || continue; t=$(cat $dd/.running)
    if grep -q "^DONE" $OUT/$t.status 2>/dev/null; then
      n=$(clean $t); echo "$(date '+%F %T') $t $(cat $dd/.combo) $(fmax $t) failing_groups=$n" >> $LOG; rm -f $dd/.running
      if [ "$n" = 0 ] && [ -s $OUT/reports/$t/spinalvoodoo_tang_mega_138k_pro.fs ]; then cp $OUT/reports/$t/spinalvoodoo_tang_mega_138k_pro.fs $OUT/fs/${t}.fs; echo "$(date '+%F %T') CLEAN $t" >> $LOG; exit 0; fi
    elif [ $(( $(date +%s) - $(stat -c %Y $dd/.running) )) -gt 5400 ]; then
      for p in $(pgrep gw_sh); do [ "$(readlink /proc/$p/cwd)" = "$dd" ] && kill $p; done
      echo "$(date '+%F %T') $t $(cat $dd/.combo) KILLED (90 min)" >> $LOG; sleep 5; rm -f $dd/.running $dd/.build.lock
    fi; done
}
i=0
for c in "${COMBOS[@]}"; do
  set -- $c; tag=$PREFIX$i; d=$HW/gowin-ss$((i % 3)); i=$((i+1))
  while [ -f $d/.running ]; do check_slots; sleep 60; done
  mkdir -p $d && rsync -a --delete --exclude impl --exclude .build.lock --exclude .running --exclude .combo $TEMPLATE/ $d/
  sed -i "s/set_option -place_option [0-9]*/set_option -place_option $2/; s/set_option -max_fanout [0-9]*/set_option -max_fanout $3/; s#rtl_b[0-9]*/TangMega138kPro.v#$1/TangMega138kPro.v#" $d/build.tcl
  free_kb=$(df -Pk $HW | awk 'NR==2 {print $4}'); [ "$free_kb" -lt 6000000 ] && { echo "$(date '+%F %T') stop: disk" >> $LOG; exit 1; }
  echo $tag > $d/.running; echo "$c" > $d/.combo
  setsid nohup $HW/gowin/run_detached.sh $(basename $d) $tag >/dev/null 2>&1 &
  sleep 20
done
while ls $HW/gowin-ss*/.running >/dev/null 2>&1; do check_slots; sleep 60; done
echo "$(date '+%F %T') combos exhausted" >> $LOG
