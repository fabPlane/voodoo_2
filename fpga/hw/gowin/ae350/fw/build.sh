#!/bin/bash
# Build the AE350 boot ROM image (ae350_rom.hex, 2048 x 32-bit words for
# $readmemh) with clang + ld.lld (lld from ~/opt/micromamba env rvtools).
set -e
cd "$(dirname "$0")"
LLD=${LLD:-$HOME/opt/micromamba/envs/rvtools/bin/ld.lld}
CF="--target=riscv32-unknown-elf -march=rv32imac -mabi=ilp32 -Os -ffreestanding -nostdlib -fno-pic -mno-relax -Wall"
clang $CF -c start.S -o start.o
clang $CF -c main.c -o main.o
$LLD -T link.ld --no-relax -o ae350.elf start.o main.o
llvm-objcopy-18 -O binary --only-section=.text ae350.elf ae350.bin 2>/dev/null || /usr/lib/llvm-18/bin/llvm-objcopy -O binary --only-section=.text ae350.elf ae350.bin
python3 - <<'PY'
import struct
b = open('ae350.bin', 'rb').read()
assert len(b) <= 8192, len(b)
b += b'\0' * ((-len(b)) % 4)
w = [struct.unpack('<I', b[i:i+4])[0] for i in range(0, len(b), 4)]
w += [0] * (2048 - len(w))
open('../ae350_rom.hex', 'w').write('\n'.join('%08x' % x for x in w) + '\n')
print('ae350_rom.hex: %d bytes of code' % len(b))
PY
