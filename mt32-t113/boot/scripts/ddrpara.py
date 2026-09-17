#!/usr/bin/env python3
"""
ddrpara.py - read, write and compare Allwinner sun8iw20 (D1/D1s/R528/T113) DRAM
parameter blocks.

Why this exists
---------------
On this SoC family the DRAM controller is configured from a 24-word structure
that Allwinner calls `dram_para_t`.  It lives at **offset 0x38 of every boot0
image** (vendor boot0, awboot, U-Boot SPL, and the DDR-init payload that xfel
uploads).  That single fact is the key to the whole external-DDR3 question:

  * you can *read* a known-good parameter set out of any vendor boot0 blob, and
  * you can *write* your own set into a FEL DDR payload without rebuilding
    anything, because the structure sits at a fixed offset in a fixed-size
    image.

xfel ships DDR payloads for `r528-s3`, `t113-s3` and `t113-s4` only.  There is
no `t113-i` preset, because the T113-i takes *external* DDR3 and its parameters
are a board property.  This tool lets us make one.

See ../BRINGUP.md section 2 for where each preset's numbers come from.

Usage
-----
  # what parameters does this blob use?
  ddrpara.py decode vendor-boot0.bin
  ddrpara.py decode --preset t113i-100ask

  # pull the DDR-init payload out of an xfel checkout
  ddrpara.py extract vendor/xfel/chips/r528_t113.c -o payloads/t113-ddr.bin

  # stamp our parameters into it
  ddrpara.py patch payloads/t113-ddr.bin --preset t113i-100ask \
             -o payloads/t113i-ddr.bin

  # what would this look like in a U-Boot defconfig?
  ddrpara.py kconfig --preset t113i-100ask

  # what changed between two boards?
  ddrpara.py diff --preset t113-s3 --preset t113i-100ask
"""

import argparse
import os
import re
import struct
import sys

# Offset of dram_para_t inside an eGON.BT0 image.  Verified against U-Boot
# (drivers/ram/sunxi/dram_sun20i_d1.h, "offset 0x38 in any boot0 binary") and
# against xfel, which does fel_write(ctx, 0x28038, &ddr3, sizeof(ddr3)) after
# uploading its payload to 0x28000.
PARA_OFFSET = 0x38

# Field order is dram_para_t as defined identically in:
#   u-boot      drivers/ram/sunxi/dram_sun20i_d1.h
#   awboot      arch/arm32/mach-t113s3/dram.h
#   SyterKit    include/drivers/dram/dram.h  (the non-V2 variant)
#   xfel        chips/r528_t113.c            (struct ddr3_param_t)
FIELDS = [
    "dram_clk",     # MHz
    "dram_type",    # 2=DDR2 3=DDR3 6=LPDDR2 7=LPDDR3
    "dram_zq",      # ZQ calibration value
    "dram_odt_en",  # on-die termination enable
    "dram_para1",   # row/bank/page geometry seed (auto-scan overwrites)
    "dram_para2",   # rank/DQ-width seed (auto-scan overwrites)
    "dram_mr0",     # recomputed from dram_clk for DDR3; seed only
    "dram_mr1",     # USED as-is for DDR3: drive strength + Rtt_Nom
    "dram_mr2",     # recomputed from dram_clk for DDR3; seed only
    "dram_mr3",
    "dram_tpr0",    # DRAMTMG0 - recomputed for DDR3
    "dram_tpr1",    # DRAMTMG1 - recomputed for DDR3
    "dram_tpr2",    # DRAMTMG2 - recomputed for DDR3
    "dram_tpr3",    # unused
    "dram_tpr4",
    "dram_tpr5",    # per-lane write leveling / DX drive
    "dram_tpr6",
    "dram_tpr7",    # unused
    "dram_tpr8",
    "dram_tpr9",
    "dram_tpr10",   # AC/DQ delay trim (coarse)
    "dram_tpr11",   # PER-BIT-LANE READ/WRITE DELAY TRIM - board specific
    "dram_tpr12",   # PER-BIT-LANE READ/WRITE DELAY TRIM - board specific
    "dram_tpr13",   # feature bitfield - see TPR13_BITS below
]
NWORDS = len(FIELDS)  # 24
PARA_SIZE = NWORDS * 4  # 96 bytes

# Bits of dram_tpr13 that the mainline U-Boot driver actually tests.
# Line numbers are against u-boot drivers/ram/sunxi/dram_sun20i_d1.c at
# v2026.10-rc (commit 211de43d).
TPR13_BITS = {
    0: "skip auto_scan_dram_config() (geometry is taken from para1/para2 as given)",
    1: "set by the driver when bit15 is clear",
    2: "DDR3 trd2wr selector (with bit 3)",
    3: "DDR3 trd2wr selector (with bit 2)",
    6: "use dram_tpr9 as the PLL_DDR frequency instead of dram_clk",
    13: "set by the driver when bit15 is clear",
    14: "set by the driver when bit15 is clear",
    15: "do not auto-set bits 0,1,13,14",
    16: "internal ZQ only (skip external ZQ resistor calibration path)",
    18: "use AC remapping table 7 instead of the efuse-selected table",
    19: "use AC remapping table 7 instead of the efuse-selected table",
    20: "write training",
    21: "read training",
    22: "read gate training",
    28: "run dramc_simple_wr_test() after init",
    30: "extra master config from dram_tpr8",
}

# ---------------------------------------------------------------------------
# Presets.  Every one of these is copied verbatim from a primary source; the
# provenance comment is the whole point, do not edit a number without moving
# its citation with it.
# ---------------------------------------------------------------------------

PRESETS = {
    # Allwinner T113-S3: 128 MB DDR3 CO-PACKAGED inside the SoC.
    # Source: xfel chips/r528_t113.c, `xfel ddr t113-s3` branch.  Identical to
    # awboot arch/arm32/mach-t113s3/dram.c lines 35-41 and to U-Boot
    # configs/mangopi_mq_r_defconfig.  THIS IS NOT OUR PART.
    "t113-s3": dict(
        dram_clk=792, dram_type=3, dram_zq=0x007B7BFB, dram_odt_en=0x00,
        dram_para1=0x000010D2, dram_para2=0x00000000,
        dram_mr0=0x00001C70, dram_mr1=0x00000042,
        dram_mr2=0x00000018, dram_mr3=0x00000000,
        dram_tpr0=0x004A2195, dram_tpr1=0x02423190, dram_tpr2=0x0008B061,
        dram_tpr3=0xB4787896, dram_tpr4=0x00000000, dram_tpr5=0x48484848,
        dram_tpr6=0x00000048, dram_tpr7=0x1620121E, dram_tpr8=0x00000000,
        dram_tpr9=0x00000000, dram_tpr10=0x00000000,
        dram_tpr11=0x00340000, dram_tpr12=0x00000046, dram_tpr13=0x34000100,
    ),

    # (t113-s4 is defined below: identical to t113-s3 but clocked at 936 MHz.
    #  Source: xfel chips/r528_t113.c, `xfel ddr t113-s4` branch.)

    # === THE ONES THAT MATTER ===

    # Allwinner T113-i, EXTERNAL 512 MB DDR3 (one x16 part), on the
    # 100ask "T113i-Industrial DevKit".
    # Source: SyterKit archive/boards/100ask-t113i/board.dts, the
    # `allwinner,dram-parameters` property, decoded through the field order in
    # SyterKit include/drivers/dram/dram.h.
    # Deltas vs t113-s3: odt_en 0->1, tpr11, tpr12, tpr13.
    "t113i-100ask": dict(
        dram_clk=792, dram_type=3, dram_zq=0x007B7BFB, dram_odt_en=0x01,
        dram_para1=0x000010D2, dram_para2=0x00000000,
        dram_mr0=0x00001C70, dram_mr1=0x00000042,
        dram_mr2=0x00000018, dram_mr3=0x00000000,
        dram_tpr0=0x004A2195, dram_tpr1=0x02423190, dram_tpr2=0x0008B061,
        dram_tpr3=0xB4787896, dram_tpr4=0x00000000, dram_tpr5=0x48484848,
        dram_tpr6=0x00000048, dram_tpr7=0x1620121E, dram_tpr8=0x00000000,
        dram_tpr9=0x00000000, dram_tpr10=0x00000000,
        dram_tpr11=0x00770000, dram_tpr12=0x00000002, dram_tpr13=0x34050100,
    ),

    # Allwinner T113-i, EXTERNAL DDR3, on the Tronlong "T113i MiniEVM".
    # Source: nickfox-taterli/t113-uboot configs/t113i_minievm_defconfig
    # (U-Boot 2025.10 base).  Note this board runs the *T113-S3* numbers
    # unchanged on external DDR3 and is reported working.  Two different
    # T113-i boards therefore use two different parameter sets - which is the
    # evidence that these are board-layout trim, not part constants.
    "t113i-tronlong": dict(
        dram_clk=792, dram_type=3, dram_zq=0x007B7BFB, dram_odt_en=0x00,
        dram_para1=0x000010D2, dram_para2=0x00000000,
        dram_mr0=0x00001C70, dram_mr1=0x00000042,
        dram_mr2=0x00000018, dram_mr3=0x00000000,
        dram_tpr0=0x004A2195, dram_tpr1=0x02423190, dram_tpr2=0x0008B061,
        dram_tpr3=0xB4787896, dram_tpr4=0x00000000, dram_tpr5=0x48484848,
        dram_tpr6=0x00000048, dram_tpr7=0x1620121E, dram_tpr8=0x00000000,
        dram_tpr9=0x00000000, dram_tpr10=0x00000000,
        dram_tpr11=0x00340000, dram_tpr12=0x00000046, dram_tpr13=0x34000100,
    ),
}

# t113-s4 = t113-s3 at 936 MHz (xfel chips/r528_t113.c).
PRESETS["t113-s4"] = dict(PRESETS["t113-s3"], dram_clk=936)

# Our own starting point for the mt32-t113 board.  Deliberately identical to
# t113i-100ask: it is the only published external-DDR3 T113-i set whose board
# we can also read the schematic story of, and 512 MB x16 DDR3 is the same
# shape as what we plan to fit.  Expect to re-tune tpr11/tpr12 on our PCB.
PRESETS["mt32-t113"] = dict(PRESETS["t113i-100ask"])


def preset(name):
    if name not in PRESETS:
        sys.exit("unknown preset %r; have: %s" % (name, ", ".join(sorted(PRESETS))))
    return [PRESETS[name][f] for f in FIELDS]


def words_from_blob(data, offset=PARA_OFFSET):
    if len(data) < offset + PARA_SIZE:
        sys.exit("blob is only %d bytes, need at least %d" % (len(data), offset + PARA_SIZE))
    return list(struct.unpack_from("<%dI" % NWORDS, data, offset))


def blob_is_egon(data):
    return len(data) >= 12 and data[4:12] == b"eGON.BT0"


def describe_tpr13(v):
    out = []
    for bit in sorted(TPR13_BITS):
        if v & (1 << bit):
            out.append("  bit %-2d  %s" % (bit, TPR13_BITS[bit]))
    return out


def cmd_decode(args):
    if args.preset:
        words = preset(args.preset)
        src = "preset %s" % args.preset
    else:
        data = open(args.blob, "rb").read()
        words = words_from_blob(data, args.offset)
        src = "%s @ 0x%02x" % (args.blob, args.offset)
        if blob_is_egon(data):
            length = struct.unpack_from("<I", data, 16)[0]
            print("eGON.BT0 image, header-declared length %d bytes (file is %d)"
                  % (length, len(data)))
        else:
            print("warning: no eGON.BT0 magic at offset 4 - is this a boot0 image?")
    print("dram_para_t from %s\n" % src)
    for name, v in zip(FIELDS, words):
        if name == "dram_clk":
            print("  %-12s 0x%08x  (%d MHz)" % (name, v, v))
        elif name == "dram_type":
            t = {2: "DDR2", 3: "DDR3", 6: "LPDDR2", 7: "LPDDR3"}.get(v, "?")
            print("  %-12s 0x%08x  (%s)" % (name, v, t))
        else:
            print("  %-12s 0x%08x" % (name, v))
    print("\ndram_tpr13 decoded:")
    for line in describe_tpr13(words[FIELDS.index("dram_tpr13")]) or ["  (no known bits set)"]:
        print(line)
    return 0


def cmd_kconfig(args):
    words = preset(args.preset) if args.preset else words_from_blob(
        open(args.blob, "rb").read(), args.offset)
    d = dict(zip(FIELDS, words))
    print("# mainline U-Boot knobs for this parameter set.")
    print("# Everything not listed here is HARDCODED in")
    print("# drivers/ram/sunxi/dram_sun20i_d1.c and cannot be set from a defconfig.")
    print("CONFIG_MACH_SUN8I_R528=y")
    print("CONFIG_DRAM_CLK=%d" % d["dram_clk"])
    print("CONFIG_SUNXI_DRAM_TYPE_DDR3=y" if d["dram_type"] == 3
          else "# dram_type=%d - pick the matching SUNXI_DRAM_TYPE_* symbol" % d["dram_type"])
    print("CONFIG_DRAM_ZQ=%d" % d["dram_zq"])
    print("CONFIG_DRAM_SUNXI_ODT_EN=0x%x" % d["dram_odt_en"])
    print("CONFIG_DRAM_SUNXI_TPR11=0x%x" % d["dram_tpr11"])
    print("CONFIG_DRAM_SUNXI_TPR12=0x%x" % d["dram_tpr12"])
    print("CONFIG_DRAM_SUNXI_TPR13=0x%x" % d["dram_tpr13"])
    print("# CONFIG_DRAM_SUNXI_TPR0 exists as a Kconfig symbol but the driver")
    print("# ignores it (dram_tpr0 is a hardcoded 0x%08x)." % d["dram_tpr0"])
    if d["dram_mr1"] != 0x42:
        print("# WARNING: this set wants dram_mr1=0x%x but mainline hardcodes 0x42."
              % d["dram_mr1"])
        print("#          MR1 is DDR3 output drive + Rtt_Nom - patch the driver.")
    return 0


def cmd_diff(args):
    if len(args.preset) != 2:
        sys.exit("diff needs exactly two --preset arguments")
    a, b = (preset(p) for p in args.preset)
    print("%-12s %-12s %-12s" % ("field", args.preset[0], args.preset[1]))
    n = 0
    for name, x, y in zip(FIELDS, a, b):
        if x != y:
            print("%-12s 0x%08x   0x%08x" % (name, x, y))
            n += 1
    print("\n%d field(s) differ" % n)
    return 0


def cmd_extract(args):
    """Pull xfel's DDR-init payload out of its C source as a flat binary."""
    src = open(args.source, "r", errors="replace").read()
    m = re.search(r"static const uint8_t %s\[\]\s*=\s*\{(.*?)\n\t\};"
                  % re.escape(args.array), src, re.S)
    if not m:
        m = re.search(r"static const uint8_t %s\[\]\s*=\s*\{(.*?)\};"
                      % re.escape(args.array), src, re.S)
    if not m:
        sys.exit("array %s not found in %s" % (args.array, args.source))
    data = bytes(int(h, 16) for h in re.findall(r"0x([0-9a-fA-F]{2})", m.group(1)))
    if not blob_is_egon(data):
        sys.exit("extracted %d bytes but they are not an eGON.BT0 image" % len(data))
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    open(args.out, "wb").write(data)
    print("wrote %s (%d bytes, eGON.BT0)" % (args.out, len(data)))
    print("its built-in parameters:")
    for name, v in zip(FIELDS, words_from_blob(data)):
        print("  %-12s 0x%08x" % (name, v))
    return 0


def cmd_patch(args):
    data = bytearray(open(args.blob, "rb").read())
    words = preset(args.preset)
    before = words_from_blob(bytes(data), args.offset)
    struct.pack_into("<%dI" % NWORDS, data, args.offset, *words)
    open(args.out, "wb").write(bytes(data))
    print("wrote %s (%d bytes), dram_para_t at 0x%02x replaced with preset %s"
          % (args.out, len(data), args.offset, args.preset))
    for name, old, new in zip(FIELDS, before, words):
        if old != new:
            print("  %-12s 0x%08x -> 0x%08x" % (name, old, new))
    if blob_is_egon(bytes(data)):
        print("note: the eGON checksum is NOT recomputed. The BROM checks it when "
              "booting from SD/SPI; FEL (xfel write + xfel exec) does not.")
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("decode", help="print a parameter set from a blob or preset")
    d.add_argument("blob", nargs="?")
    d.add_argument("--preset")
    d.add_argument("--offset", type=lambda s: int(s, 0), default=PARA_OFFSET)
    d.set_defaults(fn=cmd_decode)

    k = sub.add_parser("kconfig", help="emit the U-Boot defconfig lines")
    k.add_argument("blob", nargs="?")
    k.add_argument("--preset")
    k.add_argument("--offset", type=lambda s: int(s, 0), default=PARA_OFFSET)
    k.set_defaults(fn=cmd_kconfig)

    f = sub.add_parser("diff", help="compare two presets")
    f.add_argument("--preset", action="append", default=[])
    f.set_defaults(fn=cmd_diff)

    e = sub.add_parser("extract", help="pull a DDR payload out of xfel's C source")
    e.add_argument("source", help="path to xfel chips/r528_t113.c")
    e.add_argument("--array", default="t113_ddr_payload")
    e.add_argument("-o", "--out", required=True)
    e.set_defaults(fn=cmd_extract)

    a = sub.add_parser("patch", help="stamp a preset into a boot0/DDR-payload blob")
    a.add_argument("blob")
    a.add_argument("--preset", required=True)
    a.add_argument("--offset", type=lambda s: int(s, 0), default=PARA_OFFSET)
    a.add_argument("-o", "--out", required=True)
    a.set_defaults(fn=cmd_patch)

    args = p.parse_args()
    if args.cmd in ("decode", "kconfig") and not args.blob and not args.preset:
        sys.exit("give a blob path or --preset")
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
