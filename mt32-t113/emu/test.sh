#!/bin/sh
# test.sh - the same assertions as port/host/test.sh, made against a Cortex-A7
# booting from reset under qemu-system-arm.
#
# docs/PLAN.md section 0.5 rule 2: both implementations of port/include pass
# the same conformance tests, so that when the T113 implementation disagrees,
# the difference is a driver bug and the test names it. The case numbers and
# the labels below are deliberately the same as the host suite's, so the two
# outputs can be diffed directly.
#
# The three MIDI streams are not re-derived here: tools/gen_vectors.py compiles
# port/host's own vectors into the image (the built-in demo from
# port/host/main.c, the 16 kB bank dump and the three-ways-wrong stream from
# port/host/test.sh cases 2 and 3).
#
# SPDX-License-Identifier: 0BSD

set -e
cd "$(dirname "$0")"

QEMU=${QEMU:-qemu-system-arm}
ELF=${ELF:-build/mt32emu-bare.elf}
OUT=build/t
TIMEOUT=${TIMEOUT:-120}

[ -f "$ELF" ] || { echo "build first: make"; exit 2; }
command -v "$QEMU" >/dev/null || { echo "no $QEMU"; exit 2; }
mkdir -p "$OUT"
fail=0

# QEMU flags. -M virt -cpu cortex-a7 because QEMU has no T113/R528/D1 machine
# and never will (boot/BRINGUP.md 6.4); see FINDINGS.md for what that does and
# does not prove.
QFLAGS="-M virt -cpu cortex-a7 -m 256 -nographic -nodefaults -serial mon:stdio -no-reboot"

# boot <logfile> <args...>  -- arguments reach the image through semihosting
# SYS_GET_CMDLINE, so one image serves every case.
boot() {
    log="$1"; shift
    args="arg=mt32-bare"
    for a in "$@"; do args="$args,arg=$a"; done
    timeout "$TIMEOUT" $QEMU $QFLAGS \
        -semihosting-config "enable=on,target=native,$args" \
        -kernel "$ELF" 2>&1 | tr -d '\r' > "$log" || true
    grep -q -- "--- end ---" "$log" || {
        echo "FAIL: image did not reach the end of a run (see $log)"
        tail -5 "$log"
        fail=1
    }
}

expect() {   # expect <label> <pattern> <value> <output-file>
    got=$(grep -E "$2" "$4" | head -1 | awk '{print $NF}')
    if [ "$got" != "$3" ]; then
        echo "FAIL $1: expected $3, got '$got'"
        fail=1
    else
        echo "ok   $1 ($3)"
    fi
}

grep_ok() {  # grep_ok <label> <pattern> <file>
    if grep -q "$2" "$3"; then echo "ok   $1"
    else echo "FAIL $1"; fail=1; fi
}

# --- 0. it boots at all, from reset, with the MMU and caches off at entry ---
boot "$OUT/d.txt" --midi demo --seconds 1 --wav "$OUT/demo.wav"
grep_ok "boots into SVC with MMU off, caches off, interrupts masked" \
        "mode SVC  I=1 F=1" "$OUT/d.txt"
grep_ok "generic timer is present and running" "timebase  *[0-9]* Hz" "$OUT/d.txt"

# --- 1. built-in demo: running status, one sysex, one real-time byte --------
expect "demo short msgs"   "^short messages"   8  "$OUT/d.txt"
expect "demo sysex"        "^sysex messages"   1  "$OUT/d.txt"
expect "demo underruns"    "^underruns"        0  "$OUT/d.txt"
test -s "$OUT/demo.wav" && echo "ok   demo wav written" || \
  { echo "FAIL demo wav"; fail=1; }

# --- 2. a 16 kB timbre bank dump interleaved with notes ---------------------
boot "$OUT/b.txt" --midi bank --seconds 30 --block 64 --ring 2
expect "bank sysex count"  "^sysex messages"  64  "$OUT/b.txt"
expect "bank short msgs"   "^short messages"  12  "$OUT/b.txt"
expect "bank underruns"    "^underruns"        0  "$OUT/b.txt"
grep_ok "bank parsed cleanly" \
        "orphan data 0, sysex truncated 0, sysex aborted 0" "$OUT/b.txt"

# --- 3. a stream that is wrong in three ways --------------------------------
boot "$OUT/x.txt" --midi bad --seconds 3
expect "bad sysex emitted" "^sysex messages"   0  "$OUT/x.txt"
expect "bad short msgs"    "^short messages"   2  "$OUT/x.txt"
expect "bad underruns"     "^underruns"        0  "$OUT/x.txt"
grep_ok "3 orphan data bytes counted" "orphan data 3"     "$OUT/x.txt"
grep_ok "oversize sysex refused"      "sysex truncated 1" "$OUT/x.txt"
grep_ok "unterminated sysex aborted"  "sysex aborted 1"   "$OUT/x.txt"

# --- 4. real-time pacing: the ring must never empty -------------------------
# Here the pacing is not a simulation. The MIDI bytes become readable on the
# generic timer's schedule and the audio deadline is a timer interrupt, so
# "underruns 0" means the render loop met 375 real deadlines in a row.
boot "$OUT/r.txt" --midi demo --seconds 1 --realtime
expect "realtime underruns" "^underruns"       0  "$OUT/r.txt"
grep_ok "no spurious interrupts"  "irqs taken .*(spurious 0)" "$OUT/r.txt"

# --- 5. bare-metal-only: the things the host harness cannot assert ----------
grep_ok "audio ring is in the non-cacheable window" \
        "Normal Non-cacheable" "$OUT/d.txt"
grep_ok "sink actually read every block it played" \
        "sink checksum       0x" "$OUT/d.txt"
if [ "$(grep -E '^sink blocks played' "$OUT/d.txt" | awk '{print $NF}')" -gt 0 ]; then
    echo "ok   deadline-driven sink consumed blocks"
else
    echo "FAIL deadline-driven sink consumed blocks"; fail=1
fi
grep_ok "heap high water is reported (PORTING.md 7)" "heap high water" "$OUT/d.txt"

# --- 5b. the virtio-sound sink: a real device model ------------------------
# Optional, because it needs a QEMU with virtio-sound-device. The point of the
# case is not the audio -- it is that the same render produces the same PCM
# through a descriptor ring with device-driven completion as it does through
# the timer sink, which is the closest rehearsal available for the T113's
# I2S-plus-DMAC path.
if $QEMU -M virt -device help 2>/dev/null | grep -q virtio-sound-device; then
    VFLAGS="-net none -global virtio-mmio.force-legacy=false"
    VFLAGS="$VFLAGS -audiodev wav,id=snd0,path=$OUT/virtio.wav"
    VFLAGS="$VFLAGS -device virtio-sound-device,audiodev=snd0"
    timeout "$TIMEOUT" $QEMU $QFLAGS $VFLAGS \
        -semihosting-config "enable=on,target=native,arg=x,arg=--midi,arg=demo,arg=--seconds,arg=1,arg=--sink,arg=virtio,arg=--ring,arg=6,arg=--wav,arg=$OUT/virtio-tap.wav" \
        -kernel "$ELF" 2>&1 | tr -d '\r' > "$OUT/v.txt" || true
    grep_ok "virtio-sound stream starts" "sink                virtio-sound" "$OUT/v.txt"
    grep_ok "virtio periods complete"    "virtio periods done [1-9]"        "$OUT/v.txt"
    # The strong check: the PCM the render loop produced must be identical
    # whichever sink consumed it. Run lengths can differ by a block, so compare
    # the common prefix. (The cumulative sink checksum cannot be compared
    # directly for the same reason -- it covers a different number of blocks.)
    a="$OUT/demo.wav"; b="$OUT/virtio-tap.wav"
    if [ -s "$a" ] && [ -s "$b" ]; then
        na=$(wc -c < "$a"); nb=$(wc -c < "$b")
        n=$na; [ "$nb" -lt "$n" ] && n=$nb
        n=$((n - 44))
        if cmp -s -i 44:44 -n "$n" "$a" "$b"; then
            echo "ok   virtio and timer sinks carried identical PCM ($n bytes)"
        else
            echo "FAIL virtio and timer sinks carried different PCM"; fail=1
        fi
    else
        echo "FAIL missing wav for the sink cross-check"; fail=1
    fi
    if [ -s "$OUT/virtio.wav" ]; then echo "ok   QEMU wrote the device's audio to a host wav"
    else echo "FAIL virtio wav empty"; fail=1; fi
else
    echo "skip virtio-sound sink (this QEMU has no virtio-sound-device)"
fi

# --- 6. the ROMs-absent path, which is the normal one -----------------------
# port/DESIGN.md 4.3: name the path, keep the console up, never hang.
if grep -q "mt32emu" "$OUT/d.txt.engines" 2>/dev/null || \
   $QEMU $QFLAGS -semihosting-config \
       "enable=on,target=native,arg=x,arg=--engine,arg=mt32emu,arg=--control-rom,arg=roms/MT32_CONTROL.ROM,arg=--pcm-rom,arg=roms/MT32_PCM.ROM" \
       -kernel "$ELF" 2>&1 | tr -d '\r' > "$OUT/rom.txt"; then :; fi
if grep -q "this image has: fake" "$OUT/rom.txt" 2>/dev/null; then
    echo "skip mt32emu ROM failure path (image built without the engine)"
elif grep -q "no such file" "$OUT/rom.txt" 2>/dev/null && \
     grep -q -- "--- end ---" "$OUT/rom.txt" 2>/dev/null; then
    echo "ok   missing ROMs are named and the image still exits cleanly"
else
    echo "FAIL missing-ROM path"; fail=1
fi

echo
[ $fail -eq 0 ] && echo "all tests passed" || echo "FAILURES"
exit $fail
