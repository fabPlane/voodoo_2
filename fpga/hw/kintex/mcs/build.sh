#!/bin/bash
# Build mcs_fw.elf for the MicroBlaze V MCS (RV32IC, no M/A/F) with clang +
# ld.lld (lld from ~/opt/micromamba env rvtools). build.tcl associates the
# ELF with the MCS so the bitstream carries it in the LMB block RAM.
set -e
cd "$(dirname "$0")"
LLD=${LLD:-$HOME/opt/micromamba/envs/rvtools/bin/ld.lld}
CF="--target=riscv32-unknown-elf -march=rv32ic -mabi=ilp32 -Os -ffreestanding -nostdlib -fno-pic -mno-relax -Wall"
clang $CF -c start.S -o start.o
clang $CF -c main.c -o main.o
$LLD -T link.ld --no-relax -o mcs_fw.elf start.o main.o
rm -f start.o main.o
echo "mcs_fw.elf: $(stat -c %s mcs_fw.elf) bytes"
