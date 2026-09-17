# Bring-up: booting an Allwinner T113-i without Linux

Workstream C. Status: 2026-09-17, research complete, **nothing tested on
silicon**. No T113 board has been in front of this work.

The question this document answers: what actually brings a T113-i up from
reset, where the DRAM parameters for an *external* DDR3 part come from, and how
you hand control to a bare-metal or RTOS payload instead of a kernel.

## The part, and why the distinction matters

| | T113-S3 | T113-S4 | **T113-i** |
|---|---|---|---|
| Package | TQFP-128 | TQFP-128 | **LFBGA** |
| DRAM | 128 MB DDR3 **co-packaged** | 256 MB DDR3 co-packaged | **external, up to 2 GB** |
| Who targets it | every hobbyist board, every bootloader port | a few | industrial SoMs (Forlinx FET113i-S), 100ask T113i devkit, Tronlong MiniEVM |

Everything in the sunxi hobbyist ecosystem — awboot, xboot, xfel's `ddr`
command, the FreeRTOS port, every blog post — is written against the **S3**.
Where a piece of evidence below applies only to the S3, it says so.

The good news, established in section 2: the silicon is the same die family
(`sun8iw20`, mainline U-Boot symbol `MACH_SUN8I_R528`), the DRAM driver is the
same code, and the difference between co-packaged and external DDR3 comes down
to **four numbers in a defconfig**.

Convention used throughout: **[V]** = read from source or vendor
documentation, cited. **[I]** = inference, and the reasoning is given.

---

## 1. Boot ROM flow, image format, FEL

### 1.1 What the BROM does at reset [V]

The A7 comes out of reset executing the mask-ROM BROM. It walks its boot media
list looking for an image with the `eGON.BT0` signature, copies the first stage
into SRAM, verifies a checksum, and branches to it. If nothing valid is found
anywhere, it drops into **FEL** — a USB device mode on the OTG port that speaks
a proprietary read-memory / write-memory / execute protocol.

Media order, in the order the BROM tries them:

| Order | Device | Where it looks |
|---:|---|---|
| 1 | MMC0 (microSD) | sector 16 (8 KB), then sector 256 (128 KB) |
| 2 | SPI0 NOR / SPI NAND on PortC | offset 0 |
| 3 | MMC2 (eMMC) | sector 16, sector 256, or a boot partition |
| — | none valid | **FEL over USB-OTG** |

The two SD offsets are verified from U-Boot
`doc/board/allwinner/sunxi.rst`: *"All Allwinner SoCs will try to find a boot
image at sector 16 (8KB) of an SD card"*, and newer SoCs *"also look at sector
256 (128KB) for the signature"*. **[V]**

The relative priority of SPI versus MMC is stated less precisely: sunxi.rst
says *"Typically the SPI flash has the lowest boot priority, so SD card and
eMMC devices will be considered first."* **[V for SD-before-SPI, I for the
exact eMMC/SPI ordering on this specific part.]** Confirm against the T113-i
datasheet's boot-mode table before designing a board that relies on it.

U-Boot's SPL enumerates `SUNXI_BOOTED_FROM_MMC0`, `..._MMC0_HIGH` (the 128 KB
location), `..._SPI`, `..._NAND`, `..._MMC2`, `..._MMC2_HIGH`
(`arch/arm/mach-sunxi/board.c`), which is the authoritative list of what the
BROM can hand over from. **[V]**

A detail worth knowing: **the BROM writes the media it booted from back into
the image header in SRAM**, at the `boot_media` field. The SPL reads it to
decide where to look for the next stage:

```c
/* arch/arm/mach-sunxi/board.c */
if (sunxi_egon_valid(egon_head))
    return readb(&egon_head->boot_media);
...
/* Not a valid image, so we must have been booted via FEL. */
return SUNXI_INVALID_BOOT_SOURCE;
```
**[V]**

### 1.2 The eGON.BT0 header [V]

From U-Boot `include/sunxi_image.h` — this is the structure the BROM parses:

| Offset | Size | Field | Notes |
|---:|---:|---|---|
| 0x00 | 4 | `b_instruction` | An ARM `b` (`0xeaxxxxxx`) or RISC-V `j` past the header |
| 0x04 | 8 | `magic` | `"eGON.BT0"` — not NUL-terminated |
| 0x0c | 4 | `check_sum` | Simple 32-bit word sum, seeded with `0x5f0a6c39` |
| 0x10 | 4 | `length` | Image length, **must be a multiple of 512** |
| 0x14 | 4 | `pub_head_size` / `spl_signature` | U-Boot stamps `"SPL"` + version here |
| 0x18 | 4 | `fel_script_address` | set by `sunxi-fel` |
| 0x1c | 4 | `fel_uEnv_length` | set by `sunxi-fel` |
| 0x20 | 4 | `dt_name_offset` | |
| 0x24 | 4 | `dram_size` | MiB, written by the SPL after DRAM init |
| 0x28 | 4 | `boot_media` | **written here by the boot ROM** |
| 0x2c | 52 | `string_pool[13]` | |
| **0x38** | **96** | **`dram_para_t`** | **24 u32 — see section 2** |

That last row is the single most useful fact in this document and it is not in
the header struct: Allwinner's own boot0 puts its DRAM parameter block
immediately after the header, and U-Boot's D1 driver header says so
explicitly — *"This is copied from Allwinner's boot0 data structure, which can
be found at offset 0x38 in any boot0 binary"*
(`drivers/ram/sunxi/dram_sun20i_d1.h`). **[V]** Independently confirmed by
xfel, which uploads its DDR payload to SRAM `0x28000` and then writes its
parameter struct to `0x28038` before executing
(`chips/r528_t113.c`). **[V]** And confirmed empirically: `scripts/ddrpara.py`
decodes xfel's payload at 0x38 and gets `dram_clk = 0x318 = 792`,
`dram_type = 3`. **[V]**

Build one with U-Boot's mkimage: `mkimage -T sunxi_egon -A arm -d in.bin
out.bin`. Padding is 8192 bytes by default (`PAD_SIZE` in `tools/sunxi_egon.c`)
so that one image works for NAND as well as SD; the minimum is 512. **[V]**
awboot ships its own 200-line `tools/mksunxi.c` doing the same job, invoked as
`mksunxi <file> <pad>`. **[V]**

### 1.3 First-stage size limit [V, with a caveat]

The first stage runs entirely in SRAM, before DRAM exists. On T113:

- Load address **0x00020000** when booted by the BROM from SD/SPI/eMMC
  (U-Boot `CONFIG_SUNXI_SRAM_ADDRESS`, `default 0x20000 if ... SUNXI_GEN_NCAT2`,
  which `MACH_SUN8I_R528` selects). **[V]**
- Load address **0x00028000** in FEL mode, because the BROM's own FEL code is
  using the low part of SRAM A1. **[V]** — awboot's Makefile literally builds
  two link scripts, `-D__RAM_BASE=0x00020000` for boot and
  `-D__RAM_BASE=0x00028000` for FEL.

Available size — three independent sources, and they agree once you see what
each is measuring:

| Source | Says |
|---|---|
| sunxi-tools `soc_info.c`, entry `0x1859 /* D1/D1s/R528/T113-S3 */` | `.spl_addr = 0x20000`, **`.sram_size = 160 * 1024`**, `.sid_base = 0x03006000`, `.sid_offset = 0x200` |
| awboot `arch/arm32/mach-t113s3/link.ld` comment | "A1 + DSP0 IRAM + DSP0 DRAM0. **160K in boot mode, 128K in FEL mode**" |
| SyterKit `archive/configs/100ask-t113i_defconfig` | `SPL_BIN_TEXT_BASE=0x20000`, `SPL_BIN_MAX_SIZE=0x20000` (128 KB); `SPL_FEL_TEXT_BASE=0x28000`, `SPL_FEL_MAX_SIZE=0x19000` (100 KB) |

All three **[V]**. The physical window is **160 KB at 0x20000** — SRAM A1 plus
the DSP0 IRAM and DRAM0 aliases, which are contiguous. FEL costs you the first
32 KB. SyterKit's figures are its own conservative budget, not a hardware
limit.

**[I]** Design to **128 KB boot / 100 KB FEL** and you are safe under every
reading. For scale: U-Boot's sunxi SPL with the D1 DRAM driver is well inside
this, and so is awboot's complete SD-boot binary including FatFs.

Note also `sid_base 0x03006000 + sid_offset 0x200` = **0x03006200**, which is
exactly U-Boot's `SUNXI_SID_BASE` in the DRAM driver — the same SID block whose
`+0x28` efuse selects the AC remapping table in section 2.5. **[V]**

### 1.4 FEL: how it works and what drives it [V]

FEL is entered when no boot medium holds a valid image, or via a board's FEL
button, or by putting the `fel-sdboot.sunxi` magic binary on an SD card (ships
in sunxi-tools' `bin/`). USB VID:PID is **1f3a:efe8**. There is normally no
on-board indication other than the device appearing on the host. **[V]**

Two tools, and for this SoC the answer is not the obvious one:

| Tool | Licence | T113 support | Verdict |
|---|---|---|---|
| **sunxi-tools** (`sunxi-fel`) | GPL-2.0 | Knows the part: `soc_info.c` has `.soc_id = 0x1859, /* Allwinner D1/D1s/R528/T113-S3 */, .name = "R528"` with the SRAM map and SID base filled in. **[V]** Does generic `read`/`write`/`exec`/`spiflash-write`/`uboot`. **No DRAM init for this family** — it has no per-SoC DDR payloads at all. | Fine for `sunxi-fel uboot u-boot-sunxi-with-spl.bin`, which works because the SPL does its own DRAM init |
| **xfel** (xboot/xfel) | **MIT** | Dedicated `chips/r528_t113.c`, 4600 lines. `xfel ddr {r528-s3,t113-s3,t113-s4}` brings DRAM up *from the host*, plus `spinor`/`spinand` programming, `sid`, `jtag`, `efuse`. | **The one to use.** DRAM-up-then-load is the fast dev loop |

xfel's DDR init is the interesting capability: it uploads a 24064-byte
eGON.BT0 DDR-init image to SRAM 0x28000, overwrites the 96-byte `dram_para_t`
at 0x28038, and executes it. Afterwards you can `xfel write 0x40000000
payload.bin; xfel exec 0x40000000` and you are running from DRAM with no
bootloader on the board at all. **[V — read from `chips/r528_t113.c`.]**

**xfel has no `t113-i` preset.** Its three presets are all co-packaged-DRAM
parts. `scripts/fel-ddr-t113i.sh` in this directory reproduces the three steps
by hand with external-DDR3 parameters; see section 2.6.

---

## 2. DRAM init for an external-DDR3 T113-i

This is the question the plan calls the single biggest unknown. It is
substantially **resolved**, and the answer is better than expected.

### 2.1 Where the code lives [V]

One driver, shared across the whole die family:

```
u-boot/drivers/ram/sunxi/dram_sun20i_d1.c      1451 lines, GPL-2.0+
u-boot/drivers/ram/sunxi/dram_sun20i_d1.h        84 lines
u-boot/drivers/ram/sunxi/Kconfig                 60 lines
```

Its own header: *"Allwinner D1/D1s/R528/T113-sx DRAM initialisation … As usual
there is no documentation for the memory controller or PHY IP used here. The
baseline of this code was lifted from awboot[1], which seems to be based on
some form of de-compilation of some original Allwinner code bits."*

Kconfig help for `DRAM_SUN20I_D1`: *"This enables support for the DRAM
controller driver covering the Allwinner D1/R528/T113s SoCs."* **[V]**

So the lineage is: Allwinner boot0 blob → decompiled into awboot → cleaned up
into mainline U-Boot (v2024.01) → re-adopted by awboot, which now carries the
U-Boot version with a `GPL-2.0+` SPDX tag and the U-Boot file header. It is
**GPL source, not a blob.** That is the answer to "is this an Allwinner blob or
the Tina SDK?" — **it is neither, any more.**

SyterKit carries its own copy at `drivers/dram/dram-sun8iw20.c`; xfel carries
a precompiled eGON image of it. Same code, four packagings. **[V]**

### 2.2 What is auto-detected, and what is not [V]

This is why density is a non-problem. `init_DRAM()` calls
`auto_scan_dram_config()`, which calls:

- `auto_scan_dram_rank_width()` — runs a DQS training pass and reads the error
  bits to determine **1 or 2 ranks** and **half or full DQ width**
  (`dram_para2` bits 12 and 0).
- `auto_scan_dram_size()` — walks the address lines per rank to find **row
  count, bank count (4 or 8), and page size**, writing the result into
  `dram_para1`.
- `DRAMC_get_dram_size()` — computes the total in MB from the controller
  registers afterwards.

```c
/* dram_sun20i_d1.c */
debug("rank %d row = %d\n", rank, i);
debug("rank %d bank = %d\n", rank, (j + 1) << 2); /* 4 or 8 */
debug("rank %d page size = %d KB\n", rank, pgsize);
```

**Consequence: you do not configure the DDR3 density, organisation, rank count
or bus width anywhere.** A 2 Gbit x16 part and a 4 Gbit x16 part use the same
defconfig. **[V]**

### 2.3 What *is* configurable, and what is hardcoded [V]

Mainline exposes seven knobs, and only seven:

| Kconfig | `dram_para_t` field | What it is |
|---|---|---|
| `DRAM_CLK` | `dram_clk` | MHz. 792 on every published T113 board |
| `SUNXI_DRAM_TYPE_DDR3` | `dram_type` | 2/3/6/7 = DDR2/DDR3/LPDDR2/LPDDR3 |
| `DRAM_ZQ` | `dram_zq` | ZQ calibration, `0x7b7bfb` everywhere |
| `DRAM_SUNXI_ODT_EN` | `dram_odt_en` | on-die termination enable |
| `DRAM_SUNXI_TPR11` | `dram_tpr11` | **per-byte-lane read/write delay trim** |
| `DRAM_SUNXI_TPR12` | `dram_tpr12` | **per-byte-lane read/write delay trim** |
| `DRAM_SUNXI_TPR13` | `dram_tpr13` | feature/behaviour bitfield |

Everything else in the 24-word structure is a **compile-time constant in the
driver** (`static const dram_para_t para = { ... }` at the bottom of the file):

```c
.dram_mr0  = 0x1c70,    .dram_mr1  = 0x42,    .dram_mr2 = 0x18,
.dram_tpr0 = 0x004a2195, .dram_tpr1 = 0x02423190, .dram_tpr2 = 0x0008b061,
.dram_tpr5 = 0x48484848, .dram_tpr6 = 0x00000048,
```

Three notes on that, each verified by reading the code:

1. **The DDR3 JEDEC timings are computed, not taken from `tpr0..tpr2`.**
   `mctl_set_timing_params()` derives `tfaw`, `trrd`, `trcd`, `trc`, `txp`,
   `twtr`, `twr`, `trp`, `tras`, `trfc`, `trefi`, `tcl`, `tcwl`, MR0 and MR2
   from nanosecond constants and `CONFIG_DRAM_CLK`, via
   `ns_to_t(ns) = DIV_ROUND_UP((CONFIG_DRAM_CLK/2) * ns, 1000)`. The `tpr0`
   comments (`//DRAMTMG0`) are vestigial. **[V]**
2. **`CONFIG_DRAM_SUNXI_TPR0` is a dead Kconfig symbol.** It exists in
   `drivers/ram/sunxi/Kconfig` and is set in `mangopi_mq_r_defconfig`, but
   `grep -n TPR0 dram_sun20i_d1.c` finds only comments. Harmless, but do not
   expect setting it to do anything. **[V]**
3. **`dram_mr1 = 0x42` is used as-is for DDR3** (`mr1 = para->dram_mr1;` in the
   DDR3 branch). MR1 bit 1 = output drive RZQ/7 (34 Ω), bit 6 = Rtt_Nom RZQ/2
   (120 Ω). **[V for the code path; I for the decode — that is the standard
   JEDEC MR1 encoding, not something the source states.]** If our DDR3 part or
   topology wants different termination, **this is a driver patch, not a
   defconfig line**. It is the one genuinely missing knob.

The generic `DRAM_TIMINGS_DDR3_1066F_1333H` / `DRAM_TIMINGS_DDR3_800E_...`
speed-bin choice in `arch/arm/mach-sunxi/Kconfig` is gated
`if MACH_SUN4I || MACH_SUN5I || MACH_SUN7I` and **does not apply to this
family**. **[V]**

### 2.4 The actual external-DDR3 numbers [V]

This is the find that closes the question. SyterKit carries a board file for
the **100ask T113i-Industrial DevKit**, which its own README describes as
*"Main control: Allwinner T113i … DRAM: DDR3 512MB"* — i.e. external DDR3 —
alongside a 100ask T113-S3 board with the co-packaged 128 MB.

`vendor/syterkit/archive/boards/100ask-t113i/board.dts` carries the full
24-word `allwinner,dram-parameters` array, and the field order is pinned by
`include/drivers/dram/dram.h`. Decoding it and diffing against the S3 board in
the same repo:

```
$ scripts/ddrpara.py diff --preset t113-s3 --preset t113i-100ask
field        t113-s3      t113i-100ask
dram_odt_en  0x00000000   0x00000001
dram_tpr11   0x00340000   0x00770000
dram_tpr12   0x00000046   0x00000002
dram_tpr13   0x34000100   0x34050100

4 field(s) differ
```

**Four fields. All four are mainline Kconfig symbols.** Everything else —
clock, type, ZQ, MRs, all the TMG words — is byte-identical between the
co-packaged S3 and the external-DDR3 T113-i. **[V]**

`scripts/ddrpara.py kconfig --preset mt32-t113` emits exactly the defconfig
block, and `configs/t113i_mt32_defconfig` is that block in context.

### 2.5 The catch: AC remapping, and why it is a *layout* question [V + I]

`dram_tpr13` differs by `0x00050000` — bits 16 and 18. Bit 18 is the
interesting one:

```c
/* dram_sun20i_d1.c, mctl_phy_ac_remapping() */
fuse = (readl(SUNXI_SID_BASE + 0x28) & 0xf00) >> 8;
...
if (config->dram_tpr13 & 0xc0000) {
        cfg = ac_remapping_tables[7];
} else {
        switch (fuse) {
        case 8:  cfg = ac_remapping_tables[2]; break;
        ...
        case 13:
        case 14: cfg = ac_remapping_tables[0]; break;   /* table 0 is all zeros */
        }
}
```
**[V]**

AC remapping is a **swizzle of the DDR3 address and command lines** between the
controller and the PHY pins, programmed into register `0x3102500` and friends.
On a co-packaged part, the bond wiring inside the package is fixed, so an
efuse tells the driver which permutation matches it. On a part with *external*
DDR3 the efuse is meaningless — whatever routing the board designer chose is
what has to be described.

The 100ask external-DDR3 board sets bit 18, forcing **table 7**:
`{3,2,4,7,9,1,17,12,18,14,13,8,15,6,10,5,19,22,16,21,20,11}`. Table 0 is all
zeros (identity / no remap) and is what fuse values 13 and 14 select. **[V]**

**The inference, and it is important for workstream B:** on our own board the
DDR3 A/BA/RAS/CAS/WE routing must match whichever remapping table we program.
This is not something you tune afterwards — it is a schematic and layout
decision that has to be made *before* the board is fabbed. **[I]** The safe
options are (a) copy the 100ask T113-i routing and set bit 18, or (b) route
straight through and try to select table 0. Option (a) is the one with a known
working reference; option (b) requires knowing the T113-i's efuse value, which
we cannot read without a part in hand.

Get the reference routing from the 100ask T113i devkit schematic or a Forlinx
FET113i-S SoM reference design before laying out DDR3. This is the concrete
hand-off from workstream C to workstream B.

### 2.6 A second, contradictory data point — and why it is reassuring [V]

`nickfox-taterli/t113-uboot` is a U-Boot 2025.10 fork titled *"Mainline UBoot
for T113i MiniEVM (Tronlong)"* — another **external-DDR3 T113-i** board. Its
`configs/t113i_minievm_defconfig`:

```
CONFIG_DRAM_SUNXI_ODT_EN=0
CONFIG_DRAM_SUNXI_TPR11=0x340000
CONFIG_DRAM_SUNXI_TPR12=0x46
CONFIG_DRAM_SUNXI_TPR13=0x34000100
CONFIG_DRAM_CLK=792
CONFIG_DRAM_ZQ=8092667
CONFIG_MACH_SUN8I_R528=y
```

Those are the **T113-S3's values, unchanged, on external DDR3**. And the whole
delta of that fork against mainline U-Boot is a board DTS plus a UART0 PG
pinmux commit — the DRAM driver itself is byte-identical to mainline except for
a T113-S4 chip-ID check that postdates its base. **[V — diffed locally, 26
lines, all of it the S4 chipid hunk.]**

So: two published external-DDR3 T113-i boards, two different parameter sets,
both reported working. **[V for the files; the "working" claim is the repos'
own, unverified by us.]**

**What that means.** These four fields are **board trim, not part constants**.
There is no secret T113-i parameter set to obtain from Allwinner. Either
published set is a legitimate starting point; the ones that will need sweeping
on our PCB are `tpr11` and `tpr12` (byte-lane delay compensation, i.e. trace
length), and `tpr13` bit 18 has to agree with our routing.

### 2.7 So: blob, SDK, or source? [V]

**Source, GPL-2.0+, in mainline U-Boot since v2024.01.** Verified by tag:

```
v2023.10: [configs/mangopi_mq_r_defconfig no] [drivers/ram/sunxi/dram_sun20i_d1.c no]
v2024.01: [configs/mangopi_mq_r_defconfig YES] [drivers/ram/sunxi/dram_sun20i_d1.c YES]
```

You do not need a vendor boot0, the Tina SDK, or a blob. The plan's stated
risk — *"the DRAM parameter set is the specific unknown"* — can be downgraded.

**But keep one escape hatch.** If a board we buy does not come up, the vendor
boot0 on its SPI NAND or SD card contains a known-good parameter set at offset
0x38, and `scripts/ddrpara.py decode <boot0.bin>` will print it in named form.
That is the fallback, and it is cheap.

### 2.8 The tooling in this directory

- **`scripts/ddrpara.py`** — decode / diff / patch `dram_para_t` blocks.
  Presets for `t113-s3`, `t113-s4`, `t113i-100ask`, `t113i-tronlong` and
  `mt32-t113`, each with its provenance in a comment. Subcommands:
  `decode`, `kconfig`, `diff`, `extract` (pull the DDR payload out of xfel's C
  source), `patch` (stamp a preset into a payload or boot0 image).
- **`scripts/fel-ddr-t113i.sh`** — the thing xfel does not have: bring up
  *external* DDR3 over FEL, then optionally load and run a payload. Extracts
  xfel's payload, patches our parameters in at 0x38, `xfel write` + `xfel exec`.
- **`configs/t113i_mt32_defconfig`** — a U-Boot defconfig for our board with
  every non-obvious line's provenance recorded.

Both scripts are exercised end to end against real xfel source; neither has
touched hardware.

---

## 3. Bootloader options

| | Mainline U-Boot | awboot | xboot | SyterKit | Vendor (Tina/boot0) |
|---|---|---|---|---|---|
| Repo | u-boot/u-boot | szemzoa/awboot | xboot/xboot | YuzukiHD/SyterKit | Allwinner |
| Licence | GPL-2.0+ | GPL-2.0+ (DRAM code) | GPL | GPL-2.0 | proprietary |
| Last commit seen | 2026-09-17 (master) | 2025-11-21 | active | 2026-09-12 | — |
| DRAM init | **yes**, `dram_sun20i_d1.c` | **yes**, same code | yes | yes, `dram-sun8iw20.c` | yes |
| T113-S3 | `mangopi_mq_r_defconfig` | `mach-t113s3` | `PLATFORM=arm32-t113s3` | archived board | yes |
| T113-S4 | chip-ID special case in the DRAM driver | `mach-t113s4` | — | — | yes |
| **T113-i (external DDR3)** | **no in-tree board**, but the driver covers it; taterli's fork adds one | no mach dir; change 7 `#define`s in `dram.c` | no | **`archive/boards/100ask-t113i`, with real parameters** | yes |
| Size | SPL ~32 KB + U-Boot proper ~600 KB | single ~100 KB binary | ~300 KB+ | small, configurable | — |
| Boot time | ~1 s to prompt | sub-second, its stated purpose | fast | fast | — |
| Payload handoff | `go` / `bootelf` / `booti` / `bootm`, env scripting, FAT/ext4/TFTP | loads a fixed kernel+dtb off FAT or SPI | its own app model | `syter_boot` app, or write your own | — |

The **Size** and **Boot time** rows are **[I]** — order-of-magnitude estimates
from the shape of each project, not measurements. Nobody has timed any of these
on a T113. Everything else in the table is **[V]** from the checkouts under
`vendor/`.

Detail per option:

**Mainline U-Boot.** T113 support landed in **v2024.01** (verified by tag in
3.–2.7 above): the DRAM driver, `sun8i-t113s.dtsi`, and
`mangopi_mq_r_defconfig`. `MACH_SUN8I_R528` selects `CPU_V7A`,
`SPL_ARMV7_SET_CORTEX_SMPEN`, `SUNXI_GEN_NCAT2`, `SUNXI_NEW_PINCTRL`,
`MMC_SUNXI_HAS_NEW_MODE`, `SUPPORT_SPL`, `DRAM_SUN20I_D1`, and implies
`OF_UPSTREAM`. **[V]** What you get: DRAM, clocks, MMC, SPI flash, UART
console, USB, a devicetree, FAT/ext4, TFTP, an environment, and a scriptable
boot. What you don't get in-tree: a T113-i board, which is a DTS plus the four
DRAM lines. **The most capable option and the one with a future.**

**awboot.** Purpose-built "small linux bootloader for Allwinner T113-s3,
T113-s4, V851s", alive as of Nov 2025, with a recent PSCI merge. Builds four
variants (fel / spi / sdmmc / emmc) with valid eGON headers. Reads kernel+DTB
off FAT32 on SD or off SPI NOR/NAND. It now carries the *U-Boot* DRAM code with
the U-Boot header and an SPDX tag, so it and U-Boot are the same driver. For a
T113-i you would add a `mach-t113i` or edit seven `#define`s at the top of
`arch/arm32/mach-t113s3/dram.c` (lines 35-41). **[V]** It also has the cleanest
existing **PSCI / second-core** implementation for this SoC — see section 4.3.
**Attractive if boot time matters; weak if you want a shell.**

**xboot.** Has `src/arch/arm32/mach-t113s3/` with a driver set including G2D,
and a documented build (`make PLATFORM=arm32-t113s3`) and flash flow via xfel.
**[V]** It is really an application framework with a Lua runtime, not a
bootloader you hand off from; it would be a *replacement* for our payload
rather than a stage under it. **Not the shape we want.**

**SyterKit.** A bare-metal firmware framework and bootloader toolkit, GPL-2.0,
very active (Sept 2026), Kconfig + Kbuild + device trees compiled to C. Its
current board list is A523/T527/H618/T536-class parts; **the T113 boards are in
`archive/`** — `100ask-t113s3` and `100ask-t113i`. The archived T113-i board is
nonetheless the single most valuable artefact this research turned up, because
it carries the external-DDR3 parameters. **[V]** As a *bootloader* it is a
risk: our target is archived, so we would be maintaining it. **Use it as a
parameter source and a driver reference, not as the bootloader.**

**Vendor route (Tina Linux / boot0).** Not needed, per 2.7. Keep the vendor
boot0 of whatever dev board we buy as a DRAM-parameter fallback.

### Recommendation

**Mainline U-Boot**, with a board DTS and the four DRAM lines added. It is the
only option that is simultaneously (a) covering our exact part family in
upstream-maintained GPL source, (b) able to load an arbitrary payload off FAT
or TFTP and start it, and (c) still going to exist in three years. Boot time is
a second, which for a synth module that is switched on and left on is not worth
trading away the tooling.

Keep **xfel** beside it as the development loop (section 6) and as the
recovery path.

---

## 4. Loading a non-Linux payload

### 4.1 The four commands, and the CPU state each leaves [V]

This matters more than it looks, because an RTOS's reset code usually assumes
caches and MMU are off.

| Command | What it does | MMU | D-cache | Interrupts |
|---|---|---|---|---|
| `go <addr>` | calls `entry(argc, argv)` directly | **on** | **on** | as U-Boot left them |
| `bootelf [-p] <addr>` | loads ELF segments, then calls the entry point | **on** | **on** | as U-Boot left them |
| `booti` / `bootm` / `bootz` | calls `cleanup_before_linux()` first | **off** | **off, flushed** | **off** |

Verified in source:

```c
/* cmd/boot.c - do_go */
rc = do_go_exec((void *)addr, argc - 1, argv + 1);   /* no cleanup at all */

/* lib/elf.c - bootelf() */
return bootelf_exec((void *)entry_addr, argc, argv); /* weak, plain call */

/* arch/arm/lib/bootm.c */
cleanup_before_linux();            /* three call sites, all in the boot* paths */

/* arch/arm/cpu/armv7/cpu.c */
int cleanup_before_linux_select(int flags) {
        disable_interrupts();
        if (flags & CBL_DISABLE_CACHES) {
                dcache_disable();      /* flushes d-cache AND disables the MMU */
                v7_outer_cache_disable();
        }
        ...
}
```
**[V]**

**[I] Consequence for us:** if the payload is an RTOS or bare-metal image whose
`start.S` sets up its own page tables and enables caches, `go` and `bootelf`
will hand it a live MMU with U-Boot's identity mapping and dirty cache lines,
and it will fault or corrupt memory. Two clean ways out:

1. **Wrap the payload as a Linux-format image and use `bootm`/`booti`.**
   `mkimage -A arm -O linux -T kernel -C none -a 0x40200000 -e 0x40200000`.
   You then inherit `cleanup_before_linux()` for free, and the payload starts
   with MMU off, caches off, interrupts off — exactly the state a bare-metal
   `start.S` wants. This is what the Circle framework relies on under mt32-pi,
   and it is the path of least surprise.
2. **Use `go` and make the payload's first instructions disable the MMU and
   caches itself**, which is what SyterKit's `clean_syterkit_data()` does before
   chaining:
   ```c
   arm32_mmu_disable(); arm32_dcache_disable();
   arm32_icache_disable(); arm32_interrupt_disable();
   ```
   **[V — `vendor/syterkit/archive/boards/100ask-t113i/board.c`]**

Prefer (1). It costs one `mkimage` invocation and removes a whole class of
bring-up bug.

### 4.2 Addresses [V + I]

- DRAM base **0x40000000**. **[V]** — `SDRAM_BASE` in awboot, `memory@40000000`
  in every T113 DTS, `CFG_SYS_SDRAM_BASE` in U-Boot.
- U-Boot proper relocates itself to the **top** of DRAM, so load the payload low
  and leave the top ~16 MB alone. **[I]** On a 512 MB part, loading at
  0x40200000 (2 MB in) or 0x41000000 (16 MB in) is conventional and safe.
- FEL-mode SRAM staging is at **0x28000**, with ~100 KB usable. **[V]**

### 4.3 Which core, and starting the second one [V]

Reset releases **CPU0 only**; CPU1 is held in reset with its power domain off.
The A7 pair is a Cortex-A7 MPCore, GIC-400 at **0x03020000**
(`CONFIG_ARM_GIC_BASE_ADDRESS` in awboot; `compatible = "arm,gic-400"`,
`allwinner,irq-count = <223>` in SyterKit's T113 DTS). Architected timer runs at
**24 MHz**. **[V]**

To start CPU1, awboot has a complete PSCI implementation for this SoC
(`arch/arm32/mach-t113s3/psci.c`, `psci_board.c`, `psci-common.c`,
`CONFIG_ARMV7_PSCI_NR_CPUS 2`). The `CPU_ON` sequence is:

```c
int __secure psci_cpu_on(u32 unused, u32 mpidr, u32 pc, u32 context_id)
{
        u32 cpu = (mpidr & 0x3);
        psci_save(cpu, pc, context_id);
        sunxi_cpu_set_entry(cpu, &psci_cpu_entry);  /* R_CPUCFG + 0x1c8 */
        sunxi_cpu_set_reset(cpu, true);
        sunxi_cpu_invalidate_cache(cpu);
        sunxi_cpu_set_locking(cpu, true);           /* disable ext debug */
        sunxi_cpu_set_power(cpu, true);
        sunxi_cpu_set_reset(cpu, false);
        sunxi_cpu_set_locking(cpu, false);
        return ARM_PSCI_RET_SUCCESS;
}
```
plus `enable_smp_via_cp15()` setting ACTLR.SMP (bit 6) on the boot core.
Soft-entry register is `R_CPUCFG_BASE (0x07000400) + 0x1c8`; CPUCFG is at
`0x09010000`. **[V]**

**[I] For us:** that is ~200 lines to lift into the payload if we ever want
CPU1, and it is GPL-2.0+ code we can read. The plan's render loop is
single-threaded on one A7 for now; the honest read is that a second core is a
*later* optimisation, not day-one work. Note also that U-Boot's sunxi SPL sets
SMPEN itself (`SPL_ARMV7_SET_CORTEX_SMPEN` is selected by `MACH_SUN8I_R528`),
so if U-Boot is the stage below us, half the job is already done. **[V for the
Kconfig select.]**

### 4.4 Exception level [V + I]

This is ARMv7-A, so there are no EL0-3 — the relevant question is **secure vs
non-secure, and PL1 vs Hyp**. `MACH_SUN8I_R528` selects `CPU_V7_HAS_NONSEC`,
`CPU_V7_HAS_VIRT` and `ARCH_SUPPORT_PSCI`. **[V]** awboot's T113 config sets
`CONFIG_ARMV7_VIRT 1` and `CONFIG_ARMV7_SECURE_BASE 0x00044000`. **[V]**

**[I]** By default, unless U-Boot is configured to drop to non-secure/Hyp for a
kernel, `go`/`bootelf` hands over in **secure PL1 (SVC)**. That is the simplest
state for a bare-metal payload — full access to the GIC distributor, CP15, and
the secure-only registers the PSCI code touches. If we later use `bootm` with
`CONFIG_ARMV7_NONSEC`, we would land in non-secure Hyp or SVC instead, which
would break naive GIC setup. **Pin this down on hardware with a `mrs`/`mrc`
dump in the first payload; it is a 10-line test and it removes a guess.**

---

## 5. RTOS options: what actually exists for this silicon

The plan asks for driver paths, not ecosystems. Here they are.

### 5.1 RT-Thread + Allwinner's `sunxi-hal` — the clear winner on coverage [V]

RT-Thread's tree carries `bsp/allwinner/libraries/sunxi-hal`, which is
Allwinner's own Melis/Tina HAL vendored in. It has **per-SoC files for
`sun8iw20`, which *is* the T113/D1 die**:

| Function | Path under `bsp/allwinner/libraries/sunxi-hal/hal/source/` |
|---|---|
| **I²S / TDM ("daudio")** | `sound/platform/sunxi-daudio.c` + `sound/platform/platforms/daudio-sun8iw20.h` |
| I²S register base | `daudio-sun8iw20.h`: `#define SUNXI_DAUDIO_BASE (0x02032000)`, `DAUDIO_NUM_MAX 3` |
| **PCM/DMA ring** | `sound/platform/sunxi-pcm.c`, `sound/core/snd_dma.c`, `snd_pcm.c` |
| On-chip audio codec | `sound/codecs/sun8iw20-codec.c` |
| ALSA-shaped API | `sound/component/aw-alsa-lib/` (pcm.c, pcm_rate.c, pcm_softvol.c, control.c, a NEON resampler) |
| S/PDIF, DMIC | `sound/platform/sunxi-spdif.c`, `sunxi-dmic.c`, `platforms/spdif-sun8iw20.h`, `dmic-sun8iw20.h` |
| Clocks | `ccmu/sunxi-ng/ccu-sun8iw20.c`, `ccu-sun8iw20-r.c`, `ccu-sun8iw20-rtc.c` |
| GPIO/pinctrl | `gpio/sun8iw20/gpio-sun8iw20.c` |
| Interrupt controller | `intc/platform/intc-sun8iw20.h` + `intc.c`, `hal_intc.c` |
| DMA | `dma/` + `platform-dma.h` (has sun8iw20) |
| SD/MMC | `sdmmc/` — `hal_sdhost.c`, `sd.c`, `mmc.c`, `core.c`, `sdio.c`, with **RT-Thread OSAL bindings already written** (`sdmmc/osal/os/RT-Thread/*`) |
| UART, TWI, SPI, PWM, RTC, watchdog, thermal, efuse, USB | all present with `sun8iw20` platform headers |

**[V — all read from the checkout at `vendor/rt-thread`.]**

The `sunxi-daudio.c` driver has real DMA plumbing
(`playback_dma_param.src_maxburst`, `dma_addr`, `hal_dma.h`), which is exactly
the I²S-DMA-ring piece workstream D needs.

FAT comes from RT-Thread proper (DFS + elm-chan FatFs), not from the HAL. **[V
by absence — there is no filesystem in `sunxi-hal`.]**

**Two real caveats, and they are not small.**

1. **There is no ARM Cortex-A7 T113 BSP.** The in-tree boards are
   `bsp/allwinner/d1` and `bsp/allwinner/d1s`, both **RISC-V** RT-Smart. The HAL
   is SoC-specific but largely architecture-neutral C; what is *not* neutral is
   the interrupt controller (GIC-400 on the A7 side, PLIC on the C906 side), the
   startup code, the MMU setup and the context switch. **[V for the BSP list; I
   for the size of the port — call it "an A7 BSP shell around an existing HAL",
   which is real work but bounded, and RT-Thread already has ARMv7-A support and
   GIC drivers from other BSPs.]**
2. **Licence.** Every `sunxi-hal` file carries
   `Copyright (c) 2019-2025 Allwinner Technology Co., Ltd. ALL rights reserved.`
   followed by a warranty disclaimer and **no licence grant**, sitting inside an
   Apache-2.0 repository. **[V — quoted from `sunxi-daudio.c`.]** For a project
   that already has to be careful about LGPL-2.1 boundaries around `mt32emu`,
   this needs a decision before any of it is copied, not after.

**Melis** is Allwinner's own RTOS built on RT-Thread with this same HAL;
`DongshanPI/D1s-Melis` is the public D1s port. It is a *source* for drivers and
a documentation crib (mostly Chinese), not a platform to adopt.

### 5.2 FreeRTOS: `robots/allwinner_t113` [V]

Cloned and read. What it has, on real T113 silicon:

- FreeRTOS, **single core, no SMP** (author's own words).
- GICv2 interrupt controller — `common/arm/irq.c`.
- ARM architected timer — `common/arm/arm_timer.c`.
- MMU setup — `common/arm/mmu.c`.
- CCU clocks — `common/aw/ccu.c`. CPU at 1008 MHz.
- DMA controller — `common/aw/dmac.c`.
- GPIO — `common/aw/gpio.c`.
- **SD/MMC** — `common/aw/smhc.c`, plus FatFs (`diskio.c`, `ffconf.h`, `sdfs.c`).
- Display engine + LCD — `common/aw/de.c`, `tcon_lcd.c`, 800×480 working.
- USB OHCI via TinyUSB, described by the author as "a bit hackish".
- UART — `common/aw/uart.c`, **blocking, no DMA or interrupts**.
- TWI — "not working/finished".
- It runs Doom.

**What it does not have: any I²S or audio driver at all.** Grepping the tree for
`i2s|audio|codec|dac|ahub` hits only Doom's sound stubs, liblzg and CMSIS
headers. **[V]**

Two more things that matter:

- *"DDR3 is initialized by the proprietary code in xfel. There is no bootloader
  yet. Get uboot or something."* **[V — its README.]** So it gives us nothing on
  the external-DDR3 question, and it targets `AW_T113S2.ld` — a co-packaged-DRAM
  part.
- Last commit **May 2024**. Licence is generous: the author's own files are
  public domain; `start.S` and parts of `irq.c` are from xboot; TinyUSB MIT,
  FreeRTOS MIT, TLSF BSD, CMSIS Apache-2.0. **[V]**

**Verdict: an excellent reference for GIC, timer, MMU and SMHC on this exact
SoC, under a licence we can actually use. Not a BSP. You would write the I²S
driver.**

### 5.3 Zephyr [V]

`soc/allwinner/` in Zephyr main contains exactly two entries: **`sun8i_h3`** and
**`sun50i_h618`**. No `sun8iw20`, no `sun20i`, no T113, no D1, no R528. **[V]**

Zephyr does support ARMv7-A / Cortex-A (the `cortex_a_r` architecture), and the
H3 is itself a Cortex-A7 Allwinner part, so the precedent exists. But there is
no T113 SoC definition, no board, and no driver. **[V for the tree contents; I
for the effort — this would be a from-scratch SoC port including clocks,
pinctrl, GIC wiring and I²S, with the added cost of doing it Zephyr's way.]**

**Verdict: do not plan on it**, as the parent document already concluded.

### 5.4 Summary

| | I²S + DMA | SD/MMC | FAT | GIC | Timer | UART | Ported to T113 A7? | Licence |
|---|---|---|---|---|---|---|---|---|
| RT-Thread + `sunxi-hal` | **yes, `sunxi-daudio.c` for sun8iw20** | yes, with RT-Thread OSAL | via RT-Thread DFS | yes (HAL + other BSPs) | yes | yes | **no ARM BSP — RISC-V only** | Apache-2.0 repo, **Allwinner "all rights reserved" files** |
| FreeRTOS `robots/allwinner_t113` | **no** | yes | yes (FatFs) | yes | yes | yes, blocking | **yes, running** | public domain + MIT/BSD |
| Zephyr | no | no | yes (generic) | yes (generic) | yes | yes | **no SoC at all** | Apache-2.0 |
| Bare metal on awboot/U-Boot | write it | write it | write it (FatFs) | write it | write it | write it | n/a | ours |

**The honest read.** Neither RTOS is a drop-in. RT-Thread has *by far* the most
real driver code for this exact SoC — and critically, the only I²S-with-DMA
driver that exists for it — but it is behind an ARM BSP that does not exist and
a licence that has not been cleared. FreeRTOS has a *working* T113 A7 port with
the awkward parts (GIC, MMU, timer, SD) already done, under a clean licence, and
is missing precisely the one driver we most need.

**[I] The pragmatic shape:** take the FreeRTOS port's GIC/timer/MMU/SMHC
scaffolding (or write the equivalent bare-metal), and write one I²S+DMA driver
against the T113 user manual, using RT-Thread's `sunxi-daudio.c` as
documentation for a register set Allwinner does not publish well — reading it
for understanding rather than copying it, which sidesteps the licence question.
One I²S driver is days, not weeks; the parent document's "weekend-scale driver
list" is optimistic but the right order of magnitude, and this is the
concrete reason it holds: everything *except* I²S already exists somewhere we
can read.

---

## 6. Toolchain and development loop

### 6.1 Compiler [V]

Cortex-A7, NEON-VFPv4, hard float. awboot's flags:

```
-mcpu=cortex-a7 -mthumb-interwork -mthumb -mno-unaligned-access \
-mfpu=neon-vfpv4 -mfloat-abi=hard
```
**[V — awboot Makefile.]**

- Bootloader / bare metal: `arm-none-eabi-` (awboot's default).
- U-Boot and anything wanting a libc: `arm-linux-gnueabihf-`.
- **Match `bench/`.** Workstream A is already cross-building `mt32emu` for
  `armv7-a` NEON hard-float; the payload must use the same ABI and the same
  `-mfpu`, or the C++ objects will not link cleanly against the platform layer.
  Drop `-mthumb` for the synth — Munt is FP-heavy and ARM mode is the safer
  default until measured.

### 6.2 Serial console [V + I]

The T113 has multiple UARTs and **every board puts the console somewhere
different**, which is a genuine trap:

| Board | Console | Pins | Source |
|---|---|---|---|
| MangoPi MQ-R (T113-S3) | UART3 (`CONS_INDEX=4`) | — | `configs/mangopi_mq_r_defconfig` **[V]** |
| 100ask T113-i devkit | UART0 @ 0x02500000 | PB8/PB9, mux 6 | SyterKit `100ask-t113i/board.dts` **[V]** |
| 100ask T113-S3 | UART3 @ 0x02500c00 | PB6/PB7, mux 7 | SyterKit `100ask-t113s3/board.dts` **[V]** |
| Tronlong T113i MiniEVM | UART0 | PG17/PG18 | taterli fork, `uart0_pg_pins` + a `CONFIG_UART0_PORT_PG` symbol that **only exists in that fork** **[V]** |

3.3 V TTL, 115200 8N1. **[I]** Get a CH340 or FT232 adapter and check the
board's schematic before assuming; the Tronlong case shows that even mainline
may need a small pinmux patch.

Note that the MIDI-in UART for the synth is a *separate* problem — 31 250 baud,
which is `24 MHz / 768`; the sunxi UART divisor arithmetic works out exactly, so
no fractional-baud gymnastics needed. **[I — arithmetic, not read from source.]**

### 6.3 Iteration loop, fastest first [V]

**1. xfel — no SD swapping, seconds per cycle. The one to use.**
```sh
# board in FEL mode (no SD inserted, or FEL button held)
scripts/fel-ddr-t113i.sh build/payload.bin
```
That brings up external DDR3 with our parameters, loads the payload to
0x40000000 and runs it. Nothing is written to any flash. This is how the
FreeRTOS T113 port is developed (its README says so outright), and it is how
xboot documents its own T113 flow. **[V]**

**2. U-Boot + TFTP.** Once U-Boot is on an SD card, leave it there and iterate
over Ethernet or serial:
```
=> setenv serverip 192.168.1.10; tftpboot 0x41000000 payload.uimg; bootm 0x41000000
```
Slower to set up, but a useful second loop once there's a board with a PHY.

**3. SD swap.** The fallback. `dd if=u-boot-sunxi-with-spl.bin of=/dev/sdX bs=1k
seek=8`, then copy the payload onto a FAT32 partition. Slow, and SD card
sockets wear out.

**4. JTAG.** The T113 has JTAG and both SyterKit
(`scripts/openocd/allwinner_t113.cfg`) and the FreeRTOS port (`openocd.cfg` in
each firmware dir) ship OpenOCD configs. **[V]** `xfel jtag` also exists.
Worth wiring the pads on our board even if we never use them.

### 6.4 QEMU: no [V]

QEMU's ARM tree has exactly three Allwinner SoC models —
`allwinner-a10.c` (Cubieboard), `allwinner-h3.c` (Orange Pi PC),
`allwinner-r40.c` (Banana Pi M2U) — per `hw/arm/meson.build` and
`hw/arm/Kconfig` on master. **There is no R528, T113, D1 or sun8iw20 machine.**
**[V]**

**[I] What you *can* do under QEMU, and it is not nothing:**
`qemu-system-arm -M virt -cpu cortex-a7` will run ARMv7-A NEON code correctly,
which is enough to exercise (a) `mt32emu` itself and workstream A's RTF harness
as a rough cross-check, (b) the render loop and any pure-computation platform
code, and (c) a FreeRTOS or bare-metal payload's scheduler and C++ runtime
bring-up. It tells you **nothing** about DRAM init, I²S, DMA, the GIC-400's
Allwinner wiring, SMHC, or timing. Do not let a green QEMU run create
confidence about the parts that will actually be hard.

---

## 7. Recommended path

Let **mainline U-Boot v2024.01 or later** do silicon bring-up: it has the
D1/R528/T113 DRAM driver in GPL source, and the external-DDR3 delta is four
Kconfig lines (`ODT_EN`, `TPR11`, `TPR12`, `TPR13`) that two published T113-i
boards already give us working values for, because the driver auto-detects
rank, DQ width and density and computes the DDR3 JEDEC timings from
`DRAM_CLK`. Build `u-boot-sunxi-with-spl.bin` with a board DTS copied from the
Tronlong T113-i MiniEVM, dd it to an SD card at 8 KB, and have U-Boot load our
payload from FAT as a `mkimage`-wrapped `bootm` image so that
`cleanup_before_linux()` hands it a clean CPU — MMU off, caches off, interrupts
off, secure PL1, CPU1 still parked. Above that, write bare metal rather than
adopting an RTOS on day one: read the FreeRTOS `robots/allwinner_t113` port for
GIC-400, architected-timer, MMU and SMHC on this exact silicon (public domain,
already running), read RT-Thread's `sunxi-daudio.c` for the I²S register set
(the only I²S driver that exists for sun8iw20, but under an Allwinner "all
rights reserved" header, so read it, do not paste it), and write the one driver
nobody has: an I²S DMA ring. Develop the whole thing over **xfel**, never
touching an SD card — `scripts/fel-ddr-t113i.sh` brings up external DDR3 with
our parameters and runs a payload from DRAM in seconds. Before the PCB is laid
out, settle the AC-remapping question (section 2.5): the DDR3 address/command
routing on our board must match the remapping table we program in `TPR13`, and
that is a schematic decision, not a software tune.

### First three commands on a T113 dev board

```sh
# 1. Is it alive, and is it the part we think it is?
#    Board in FEL mode (no SD card inserted), USB-OTG to the host.
xfel version && xfel sid

# 2. Bring up DRAM and prove it is really there.
#    On a T113-S3 dev board this is enough:
xfel ddr t113-s3 && xfel hexdump 0x40000000 0x40
#    On a T113-i board with external DDR3, use ours instead:
#    ./scripts/fel-ddr-t113i.sh

# 3. Build mainline U-Boot for the closest in-tree board and run it from FEL —
#    no SD card, nothing written to the board.
git clone --depth 1 https://github.com/u-boot/u-boot
make -C u-boot CROSS_COMPILE=arm-linux-gnueabihf- mangopi_mq_r_defconfig
make -C u-boot CROSS_COMPILE=arm-linux-gnueabihf- -j"$(nproc)"
sunxi-fel uboot u-boot/u-boot-sunxi-with-spl.bin      # watch the UART
```

If step 3 prints a U-Boot banner, every hard part of section 1 and section 2 is
proven on that board, and the remaining work is our own code.

---

## What is still unknown

1. **The AC remapping table for our own DDR3 routing** (section 2.5). Two
   published T113-i boards disagree on `TPR13` bit 18, which means they route
   the DDR3 address/command lines differently. We cannot resolve this from
   software; it needs the 100ask T113i or Forlinx FET113i-S schematic, or a
   part in hand to read the efuse at SID+0x28[11:8]. **This gates the PCB, not
   the software, and it is the single item that should go to workstream B
   today.**
2. **`dram_mr1` is not configurable** (section 2.3). If our DDR3 part or
   topology wants termination other than Rtt_Nom = RZQ/2 with a 34 Ω output
   driver, that is a one-line driver patch, but nobody has needed it yet so
   nobody has tested it.
3. **Exception level and security state on handoff** (section 4.4). Inferred as
   secure PL1; a 10-line payload on real hardware settles it.
4. **The RT-Thread `sunxi-hal` licence** (section 5.1). Allwinner copyright with
   no grant, inside an Apache-2.0 repo. Needs a decision, not a guess, before
   any of it is copied.
5. **Whether the taterli T113-i U-Boot fork actually boots.** Its defconfig has
   `# CONFIG_SPL is not set`, which means no SPL, which means the DRAM init in
   that build never runs from the BROM path. **[I]** The plausible explanation
   is that the author runs `xfel ddr` first and then loads U-Boot proper into
   already-live DRAM — which would be consistent with everything else in the
   repo, but it is a guess, and it means that fork is evidence about *DRAM
   parameters* rather than evidence about a complete SD-boot flow.

## Sites blocked by the egress proxy

Named per the brief:

- **linux-sunxi.org** — blocked (`EGRESS_BLOCKED`) by both WebFetch and curl.
  This is the community wiki and the canonical reference for the eGON header,
  FEL, the T113-S3 page and the boot-media order. Everything that would have
  come from there was instead read from U-Boot, awboot, SyterKit and xfel
  source, which is a better source anyway.
- **lists.denx.de** — blocked. The U-Boot patch series that added the
  D1/R528/T113 DRAM code could not be read; the code itself was read instead.
- **www.mail-archive.com** — blocked. Same patch series.

## Reference tree

Everything under `vendor/` is a read-only clone, gitignored, never committed.
To recreate:

```sh
cd vendor
git clone --depth 1 https://github.com/szemzoa/awboot.git awboot
git clone --depth 1 https://github.com/YuzukiHD/SyterKit.git syterkit
git clone --depth 1 https://github.com/robots/allwinner_t113.git freertos-t113
git clone --depth 1 https://github.com/xboot/xfel.git xfel
git clone --depth 1 --filter=blob:none https://github.com/u-boot/u-boot.git
git clone --depth 1 --filter=blob:none https://github.com/nickfox-taterli/t113-uboot.git
git clone --depth 1 --filter=blob:none https://github.com/RT-Thread/rt-thread.git
git clone --depth 1 https://github.com/linux-sunxi/sunxi-tools.git
```

The two U-Boot-shaped clones are big; `--filter=blob:none` plus
`git sparse-checkout set drivers/ram arch/arm/mach-sunxi configs arch/arm/dts
doc/board/allwinner` keeps them under 50 MB.

The files that carry the load, in rough order of importance:

| File | Why |
|---|---|
| `u-boot/drivers/ram/sunxi/dram_sun20i_d1.c` | the DRAM driver, all of section 2 |
| `u-boot/drivers/ram/sunxi/Kconfig` | the seven knobs |
| `syterkit/archive/boards/100ask-t113i/board.dts` | **external-DDR3 T113-i parameters** |
| `syterkit/archive/boards/100ask-t113s3/board.dts` | the co-packaged set, for the diff |
| `t113-uboot/configs/t113i_minievm_defconfig` | the second T113-i data point |
| `xfel/chips/r528_t113.c` | FEL DDR payload + protocol |
| `u-boot/include/sunxi_image.h` | eGON header |
| `u-boot/doc/board/allwinner/sunxi.rst` | boot media, offsets, FEL |
| `awboot/arch/arm32/mach-t113s3/psci*.c` | second-core start |
| `freertos-t113/common/arm/*`, `common/aw/*` | GIC, timer, MMU, SMHC on this SoC |
| `sunxi-tools/soc_info.c` | the SRAM map and SID base, straight from the FEL tool |
| `rt-thread/bsp/allwinner/libraries/sunxi-hal/hal/source/sound/` | the only I²S driver for sun8iw20 |

## Links

- U-Boot: https://github.com/u-boot/u-boot — `drivers/ram/sunxi/dram_sun20i_d1.c`, `doc/board/allwinner/sunxi.rst`
- awboot: https://github.com/szemzoa/awboot
- SyterKit: https://github.com/YuzukiHD/SyterKit
- xboot: https://github.com/xboot/xboot — `docs/guide-allwinner-t113s3.md`
- xfel: https://github.com/xboot/xfel — MIT
- sunxi-tools: https://github.com/linux-sunxi/sunxi-tools
- T113-i U-Boot (Tronlong MiniEVM): https://github.com/nickfox-taterli/t113-uboot
- FreeRTOS on T113: https://github.com/robots/allwinner_t113
- RT-Thread: https://github.com/RT-Thread/rt-thread — `bsp/allwinner/`
- D1s-Melis: https://github.com/DongshanPI/D1s-Melis
- Forlinx OK113i-S / FET113i-S: https://www.forlinx.net/single-board-computer/t113i-s-sbc-144.html
- Zephyr Allwinner SoCs: https://github.com/zephyrproject-rtos/zephyr/tree/main/soc/allwinner
- QEMU ARM machines: https://www.qemu.org/docs/master/system/target-arm.html
- linux-sunxi wiki (blocked from here): https://linux-sunxi.org/T113-s3
