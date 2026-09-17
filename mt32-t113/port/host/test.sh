#!/bin/sh
# test.sh - exercises the port's structure end to end, with no synthesiser and
# no silicon. Run from port/host after `make`.
#
# Each case asserts on the counters the harness prints, because those counters
# are the contract: a MIDI parser that silently eats a running-status note or a
# ring that silently underruns is exactly the failure this layer exists to make
# visible.

set -e
BIN=build/mtp_host
cd "$(dirname "$0")"
[ -x "$BIN" ] || { echo "build first: make"; exit 2; }
mkdir -p build/t
cd build/t
BIN=../mtp_host
fail=0

expect() {  # expect <label> <pattern> <value> <output-file>
    got=$(grep -E "$2" "$4" | head -1 | awk '{print $NF}')
    if [ "$got" != "$3" ]; then
        echo "FAIL $1: expected $3, got '$got'"
        fail=1
    else
        echo "ok   $1 ($3)"
    fi
}

# --- 1. built-in demo: running status, one sysex, one real-time byte --------
$BIN --seconds 1 --wav demo.wav > d.txt 2>&1
expect "demo short msgs"   "^short messages"   8  d.txt
expect "demo sysex"        "^sysex messages"   1  d.txt
expect "demo underruns"    "^underruns"        0  d.txt
test -s demo.wav && echo "ok   demo wav written"

# --- 2. a 16 kB timbre bank dump interleaved with notes ---------------------
python3 - <<'PY'
def sysex(addr, data):
    body = list(addr) + list(data)
    return bytes([0xF0,0x41,0x10,0x16,0x12]) + bytes(body) + \
           bytes([(-sum(body)) & 0x7f, 0xF7])
out = bytearray([0x90,60,100, 64,100, 67,100])
for i in range(64):
    out += sysex((0x08,(i*2)&0x7f,0x00), bytes([(j*7+i)&0x7f for j in range(246)]))
    if i % 8 == 0:
        out += bytes([0xFE, 72+(i%5), 90])   # real time, then running status
out += bytes([0xB0,123,0])
open('bank.syx','wb').write(bytes(out))
PY
$BIN --midi bank.syx --seconds 30 --block 64 --ring 2 --wav bank.wav > b.txt 2>&1
expect "bank sysex count"  "^sysex messages"  64  b.txt
expect "bank short msgs"   "^short messages"  12  b.txt
expect "bank underruns"    "^underruns"        0  b.txt
grep -q "orphan data 0, sysex truncated 0, sysex aborted 0" b.txt \
  && echo "ok   bank parsed cleanly" || { echo "FAIL bank parse"; fail=1; }

# --- 3. a stream that is wrong in three ways --------------------------------
python3 - <<'PY'
bad = bytearray([40,50,60])                                   # orphan data
bad += bytes([0xF0,0x41,0x10,0x16,0x12]) + bytes(100)         # unterminated
bad += bytes([0x90,60,100])                                   # aborts it
bad += bytes([0xF0]) + bytes([0x7f]*40000) + bytes([0xF7])    # oversize
bad += bytes([0x80,60,0])
open('bad.syx','wb').write(bytes(bad))
PY
$BIN --midi bad.syx --seconds 3 --wav bad.wav > x.txt 2>&1
expect "bad sysex emitted" "^sysex messages"   0  x.txt
expect "bad short msgs"    "^short messages"   2  x.txt
expect "bad underruns"     "^underruns"        0  x.txt
grep -q "orphan data 3" x.txt && echo "ok   3 orphan data bytes counted" || \
  { echo "FAIL orphan data count"; fail=1; }
grep -q "sysex truncated 1" x.txt && echo "ok   oversize sysex refused" || \
  { echo "FAIL oversize sysex"; fail=1; }
grep -q "sysex aborted 1" x.txt && echo "ok   unterminated sysex aborted" || \
  { echo "FAIL sysex abort"; fail=1; }

# --- 4. real-time pacing: the ring must never empty -------------------------
$BIN --seconds 1 --realtime --wav rt.wav > r.txt 2>&1
expect "realtime underruns" "^underruns"       0  r.txt

echo
[ $fail -eq 0 ] && echo "all tests passed" || echo "FAILURES"
exit $fail
