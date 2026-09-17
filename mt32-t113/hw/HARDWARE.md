# hw/ — T113-i minimum test board

Workstream B. Status: **research complete, nothing committed to copper.**
Date of all prices and stock figures: **2026-09-17**.

This document answers six questions: what the part is, what reference designs
exist to copy, what the minimum BOM is, what the board realities are, what it
costs, and whether we should be doing this at all versus buying a module.

## 0. Read this first: what could not be verified, and why

The egress proxy in this environment blocks almost every vendor and wiki domain.
Confirmed blocked (HTTP 403 at the gateway, or explicit `EGRESS_BLOCKED`):

`lcsc.com` · `jlcpcb.com` · `forlinx.net` · `linux-sunxi.org` · `bbs.aw-ol.com` ·
`dl.100ask.net` · `docs.100ask.net` · `mangopi.org` · `whycan.com` ·
`file.whycan.com` · `lists.denx.de` · `docs.u-boot.org` · `mouser.com` ·
`digikey.com` · `myir.cn` · `cnx-software.com` · `medium.com` ·
`blog.findchips.com` · `soc-guides.com` · `mt-system.ru`

Reachable: `github.com`, `raw.githubusercontent.com`, and the search index.

**Consequence: I could not open the T113-i datasheet, the T113 hardware design
guide, or any LCSC/JLCPCB page myself.** Everything below that comes from those
documents comes from a search engine's extraction of them, not from my own read.
It is tagged accordingly:

- **[DS]** — traceable to an Allwinner datasheet / user manual / design guide
- **[V]** — vendor page, reseller listing, distributor blog, or vendor SoM manual
- **[C]** — community: forum post, mailing list, wiki, GitHub repo
- **[I]** — my inference or engineering judgement, not a citation
- **[GUESS]** — a number I made up because I could not find one

Three whole sections of this document (power sequencing, DDR3 length-matching
tolerances, T113-i ball pitch) are **blocked on getting four PDFs**. They are
listed in § 7 as the first action items. Under the project rule "copy a
known-good sequence, do not derive it", no power tree gets drawn until those
PDFs are in `hw/ref/`.

---

## 1. The part

### 1.1 Identity

| | | Source |
|---|---|---|
| Part | Allwinner **T113-i** | |
| Cores | 2× Cortex-A7 @ 1.2 GHz, 1× XuanTie C906 RISC-V, 1× HiFi4 DSP | [V] |
| Package | **337-ball LFBGA, 13 × 13 mm** | [DS] via search extraction of the T113-i datasheet |
| Ball pitch | **not verified** — see § 1.4 | |
| DRAM | External DDR2 / DDR3 / DDR3L, **single-channel 16-bit** controller, 128/256/512 MB standard, "up to 2 GB" | width claim is [V] (Chinese SoM vendor pages); "up to 2 GB" is [V]/[DS] |
| Temperature | Industrial, −40 °C to +85 °C | [V] |
| Audio | Integrated multi-ADC/DAC audio codec **plus** I2S / PCM / DMIC / OWA (S/PDIF) digital interfaces | [DS]-derived brief |
| Boot | BROM: SD card first, then SPI NAND/NOR, else **FEL over USB-OTG** | [C] linux-sunxi Boot Process / FEL, awboot |

The name that matters for search hygiene: the die is shared with the
**Allwinner D1 / D1s / R528 / T113-S3 / T113-S4** family. U-Boot's DRAM
initialisation for all of them is one driver, `DRAM_SUN20I_D1`, selected by
`MACH_SUN8I_R528` ([C], u-boot `arch/arm/mach-sunxi/Kconfig`, read directly).
That is good news for `boot/`: the silicon bring-up code is shared. What is
*not* shared is the DRAM parameter set, because every public one targets memory
inside the package.

### 1.2 The -i versus -S3 distinction, stated once, precisely

| | T113-S3 / T113-S4 | **T113-i** |
|---|---|---|
| Package | QFN / TQFP-style, ~128 pins | 337-ball LFBGA 13×13 mm |
| DRAM | **128 MB (S3) / 256 MB (S4) DDR3 co-packaged, fixed** | **external DDR3 bus, you place the chip** |
| Temperature | commercial/industrial depending on SKU | industrial −40…+85 |
| Community boards | MangoPi MQ-R, 100ask DongshanPI T113-Pro, dozens of EasyEDA projects | almost none; SoM vendors only |
| LCSC price | $18.39 (per parent doc, Sept 2026) | **$4.84** |
| Mainline Linux DT | `sun8i-t113s-mangopi-mq-r-t113.dtb`, since v6.5-rc1 [C] | no upstream board DT found |

Every hobbyist artefact you will find — awboot, the MQ-R device tree, the
EasyEDA footprints, the "T113-S3-BD" OSHW project, the buildroot_100ask trees —
is for the **-S3**. `awboot`'s own README lists its targets as "T113-S3, T113-S4
and V851s" [C]. None of them exercises an external DDR3 bus.

### 1.3 Orderable part numbers and availability

I could not open a distributor page. These are search-extracted listings:

| Distributor | Part | Code | Price (qty 1) | Stock |
|---|---|---|---:|---|
| LCSC | T113-i | **C7545092** | **$4.8421** (one listing showed $4.6507) | 308 [V] |
| JLCPCB parts library | T113-i | **C7545092** | — | page exists [V] |
| LCSC | T113-S3 (contrast) | C5197687 | ~$18.39 per parent doc | — |

The package string LCSC shows is `BGA-337_13X13` [V].

**The T113-i has a JLCPCB part-detail page.** JLCPCB's `partdetail` catalogue is
its assembly library, so this is meaningful evidence that the SoC is
orderable *as an assembled part*, not merely as loose stock — but I could not
open the page to read whether it is Basic, Preferred or Extended, nor its
assembly-side stock. Treat as **[V], to be confirmed**. Same for
`W25Q128JVSIQ` (C97521) and `TF-01A` (C91145), which also have JLCPCB pages.

### 1.4 Ball pitch — the one package fact I could not pin down

337 balls in a 13 × 13 mm body is consistent with **either**:

- 0.5 mm pitch on a partially-depopulated 21×21 or 25×25 grid, or
- 0.65 mm pitch on a 19×19 grid (spans 11.7 mm — fits comfortably)

**[I]** It does not change any decision in this document: JLCPCB states support
for "ball grid array packages up to 17 × 17 mm with 0.5 mm pitch" and explicitly
excludes only "large BGAs with pitch < 0.5 mm" [V, JLCPCB assembly capabilities],
so the T113-i is inside their envelope on either reading. It *does* change the
PCB escape strategy (§ 5.2), so resolve it from the datasheet package drawing
before layout.

### 1.5 Documents to obtain

None of these are reachable from here. Links recorded so a human on an open
network can fetch them into `hw/ref/`:

- T113-i datasheet v1.4 — `https://dl.100ask.net/Hardware/MPU/T113i-Industrial/T113-i_Datasheet_V1.4.pdf` (also mirrored on bbs.aw-ol.com)
- T113-i user manual v1.4 — indexed on the Allwinner forum (`bbs.aw-ol.com/topic/3108`)
- **T113 硬件设计指南 V1.0** (hardware design guide) — `http://file.whycan.com/files/202304/T113-I/T113_硬件设计指南V1_0.pdf`
- **T113-i EVB V1.0 schematic, 2022-05-07** — `http://file.whycan.com/files/202304/T113-I/T113-i_EVB_V1_0_20220507.pdf`
- T113-I 硬件调试指南 V1.0 (hardware debug guide) — same directory
- T113-I EVB 硬件操作指南 V1.0 — same directory

The last four are the important ones. The middle two are the project's
"copy, don't derive" source for power and DDR.

---

## 2. Reference designs

| Design | SoC | External DDR3? | What you get | Source |
|---|---|---|---|---|
| **Allwinner T113-i EVB v1.0** | **-i** | **yes** | Schematic PDF published (whycan mirror), block diagram + power distribution | [V] |
| **Forlinx FET113i-S SoM** + OK113i-S SBC | **-i** | **yes** (256/512 MB) | Carrier-board schematic and PCB in Altium format, carrier schematic PDF, pin-mux table, design guidance — **but the SoM internals are not published** | [V] |
| **MYIR MYC-YT113i SoM** + MYD-YT113i board | **-i** | **yes** (512 MB / 1 GB) | 37 × 39 mm, **6-layer**, immersion gold, discrete power circuits | [V] |
| Tronlong T113-i industrial SoM | **-i** | yes | Spec sheet + hardware manual (Chinese) | [V] |
| **MangoPi MQ-R** | -S3 | **no** | Mainline DT since 6.5-rc1; SY8008 buck for 3.3 V, XC6206 LDOs; U-Boot board support | [C]/[V] |
| **100ask DongshanPI T113-Pro** and T113s3 Industrial DevKit | -S3 | **no** | Full Tina SDK + Buildroot trees on GitHub | [C] |
| 100ask "T113i-Industrial" doc set | **-i** | yes | Docs site exists (`docs.100ask.net/dshanpi/.../T113i-Industrial/`); hosts the -i datasheet | [V] |
| `oshwlab.com/chcbaram/t113-s3-bd`, "SBC-T113" hackaday project | -S3 / -S4 | no | Open EasyEDA/KiCad, useful only for the non-DDR half | [C] |
| Anonymous whycan build "自制T113-I 核心板一次成功开机" | **-i** | yes | A person hand-built a T113-i core board with DDR3 + eMMC and booted it first try. **Explicitly declined to open-source it.** | [C] |

**The shape of the evidence:** every design that actually runs an external DDR3
bus on this part is a commercial SoM whose internals are closed, plus one
Allwinner EVB whose schematic is published on a Chinese file mirror. The one
hobbyist who did it kept it to himself. That is the ground truth gap `docs/PLAN.md`
§ 4 already names, confirmed.

### 2.1 Power: rails, and the sequencing rule

What I could establish, all of it **[V]** (vendor/community paraphrase of the
Allwinner design guide, **not** my own read of it):

| Rail | Voltage | Current | Note |
|---|---|---:|---|
| VCC-CORE / VDD-CPU | 0.9 V or 1.0 V nominal; higher for 1.2 GHz | up to ~2 A | "requirements are strict, current is large, ripple must be small" |
| VCC-DRAM | **1.5 V default** | >1 A | DDR3 at 1.5 V is the reference configuration |
| VCC-IO / 3.3 V | 3.3 V | >2 A on a full board | flash, USB PHY, Wi-Fi all hang off it |
| 1.8 V (PLL / analog) | 1.8 V | small | T113-S3 is documented as needing 0.9 / 1.8 / 3.3 V [V] |

Two statements about sequencing, both worth more than the voltages:

1. **Allwinner's design guide recommends discrete DC-DCs for VCC-DRAM and says
   the power-up sequence is to be implemented with an RC delay on the DC-DC
   `EN` pin.** ("强烈建议 VCC-DRAM 使用外部 DC-DC 供电…上电时序可以通过在 DCDC EN
   pin 增加 RC 延时电路的方式实现") [V]. So the reference approach is *not* a
   PMIC — it is three or four bucks with staggered enables.
2. **Forlinx's SoM manual warns, in as many words, that incorrect power-up
   timing causes excessive inrush, boot failure, or irreversible damage to the
   processor**, and tells carrier designers to gate the carrier's power on the
   SoM's VDD_3V3 output [V]. Their SoM releases `RESETn` **92.5 ms after** the
   5 V input appears [V].

**The exact rail ordering is the single most important thing this document does
not contain.** I will not guess it. It is in the T113 hardware design guide and
in the EVB schematic, both listed in § 1.5. This is the blocking item.

PMIC options if we ever want one instead of discrete bucks: the **AXP313A**
(3 DCDC + 3 LDO, QFN-20 3×3, LCSC C5365290, $0.504, **out of stock at LCSC** [V])
is used with T113 in at least one documented bring-up [C]. The AXP2101 is the
bigger sibling. **[I]** For a five-board test run, discrete SY8089-class bucks
with RC-staggered enables are the better choice anyway: they match what the
Allwinner guide describes, they are all in stock, and there is no I2C-configured
PMIC state to get wrong before DRAM comes up.

---

## 3. Minimum BOM

Design intent: **SD-boot only, no eMMC, no Ethernet, no display, no Wi-Fi, no
USB host.** One DDR3 chip. Three switching rails plus one LDO. Audio out, MIDI
in, console, FEL. That is the whole board.

### 3.1 The list

| # | Function | Part | LCSC | Qty | Unit | Ext | Conf. |
|---|---|---|---|---:|---:|---:|---|
| 1 | SoC | Allwinner T113-i, LFBGA-337 | **C7545092** | 1 | $4.84 | $4.84 | [V] |
| 2 | DRAM | DDR3L 4 Gbit x16, FBGA-96 — see § 3.2 | C20463642 / C253882 | 1 | **$6.53–$13.77** | ~$10.00 | [V] |
| 3 | Audio DAC | PCM5102APWR, TSSOP-20 | **C107671** | 1 | $0.84 | $0.84 | [V] |
| 4 | MIDI opto | H11L1S(TA), **SMD** Schmitt-output optocoupler | **C78589** | 1 | $0.156 | $0.16 | [V] |
| 5 | Buck ×3 | SY8089AAAC, 2 A synchronous buck, SOT-23-5 | **C78988** | 3 | $0.078 | $0.23 | [V] |
| 6 | LDO | 1.8 V LDO (XC6206 / ME6211 class) | — | 1 | $0.05 | $0.05 | [GUESS] |
| 7 | Inductors | 2.2 µH / 1 µH shielded, 2 A, 0806/1210 | — | 3 | $0.06 | $0.18 | [GUESS] |
| 8 | microSD | TF-01A push-pull socket | **C91145** | 1 | $0.105 | $0.11 | [V] |
| 9 | Crystal | 24 MHz, SMD 3225, X322524MSB4SI | **C15643** | 1 | $0.049 | $0.05 | [V] |
| 10 | Crystal | 32.768 kHz RTC (SC-20S or DT-38) | C97603 / C93228 | 1 | $0.07–0.13 | $0.10 | [V] |
| 11 | Boot flash | W25Q128JVSIQ SPI NOR, SOIC-8 — **DNP**, see § 3.4 | **C97521** | 0 | $1.69 | $0.00 | [V] |
| 12 | USB (FEL) | USB-C receptacle, 16-pin, + 2× 5.1 kΩ CC | — | 1 | $0.15 | $0.15 | [GUESS] |
| 13 | MIDI in | DIN-5 180° PCB jack, through-hole | — | 1 | $0.40 | $0.40 | [GUESS] |
| 14 | Console | 4-pin 2.54 header (external USB-TTL dongle) | — | 1 | $0.05 | $0.05 | [GUESS] |
| 15 | Passives | ~45 decoupling caps, ~12 bulk, ~50 R, dividers, ferrites | — | ~120 | — | $1.60 | [GUESS] |
| 16 | Misc | ESD diodes, 3.5 mm jack or 2-pin out, test points, reset button, strap jumpers | — | — | — | $0.60 | [GUESS] |
| | | | | | **Total** | **≈ $18.9** | |
| | | | | | *of which SoC + DRAM* | **≈ $14.8** | |
| | | | | | *of which everything else* | **≈ $4.1** | |

Every `[GUESS]` line is a price I invented because I could not reach LCSC. They
total about $3.4 of the $18.9, so the bottom line is not sensitive to them.
The line that *is* sensitive is #2.

### 3.2 DRAM: which chip, and the uncomfortable part

**Decision: one x16 4 Gbit DDR3L (512 MB), not a 2 Gbit part.** The reason is
not capacity, it is price — the 2 Gbit part costs *more*:

| Part | Density | LCSC | Price (qty 1) | Note |
|---|---|---|---:|---|
| MT41K256M16HA-125 IT:E | 4 Gb (512 MB) | **C20463642** | **$6.53** | 96-FBGA 9×14, 800 MHz, industrial. **DigiKey marks it obsolete** [V] |
| MT41K256M16TW-107 XIT:P | 4 Gb | C1349259 | $3.03 | suspiciously cheap — verify, may be stale or clearance |
| MT41K256M16TW-107:P | 4 Gb | C253882 | $13.77 | 933 MHz grade |
| MT41K256M16TW-107 IT:P | 4 Gb | C367428 | — | industrial |
| NT5CC256M16ER-EK (Nanya) | 4 Gb | C428584 | $30.12 | 321 in stock |
| MT41K128M16JT-125 XIT:K | **2 Gb (256 MB)** | C598780 | $10.46 | **more than the 4 Gbit part** |
| MT41K128M16JT-125 IT:K | 2 Gb | C115792 | $11.74 | |
| MT41K128M16JT-125:K | 2 Gb | C36513 | $12.19 | |

So the parent document's § 2.4 argument ("256 MB is probably enough, and a
2 Gbit chip is cheaper") **does not survive contact with 2026 prices.** In this
market the 2 Gbit parts are the legacy tail and are priced like it. Take the
512 MB. It is cheaper, it doubles the SoundFont headroom, and the layout is
identical.

Two hard constraints that follow from the T113-i's **16-bit single-channel**
controller [V]:

- **One chip, x16, period.** No 2-chip x8 arrangement, no fly-by topology, no
  dual-rank unless you deliberately stack ranks on the same 16 bits. This is
  the single biggest simplifier in the whole design — it turns "DDR3 layout"
  into "one short point-to-point bus", which is the easy case.
- 512 MB from one x16 4 Gbit device is the ceiling for a single-rank design.
  The "up to 2 GB" in the marketing implies dual-rank plus 8 Gbit; ignore it.

**Voltage: run the DDR3L part at 1.5 V, not 1.35 V.** Allwinner's documented
VCC-DRAM default is 1.5 V [V]; DDR3L devices are dual-rated 1.35/1.5 V. Running
the low-voltage part at the reference voltage keeps us on Allwinner's known-good
configuration and costs nothing. Revisit only if power becomes an issue, which
it will not on a bench board.

**The market context, which is the real risk on this line:** DDR3L lead times
past **39 weeks** as of May 2026; supply partners telling customers to plan for
**10–20 % price increases per month through the end of 2026**; DDR3/DDR3L
"quietly reaching end of life", with industrial designs facing last-time-buy
decisions; Micron/Samsung/SK Hynix reallocating capacity to HBM and DDR5, with
Nanya and Winbond carrying what is left [V, several 2026 trade sources]. The
$6.53 part is already flagged obsolete at DigiKey. **Whatever we pick, buy the
lifetime quantity in the first order.** Twenty-five chips is $165–350; that is
cheap insurance against a respin forced by a part vanishing.

### 3.3 MIDI input: H11L1, and yes I agree

Agreed, and for a reason worth writing down rather than repeating the folklore.

- The MIDI 1.0 spec's recommendation of the **6N138** dates to 1983. It is a
  photo-Darlington. Its turn-*off* is slow and load-dependent, so you trade
  pull-up resistance against rise time and end up with asymmetric bit widths.
  At 31.25 kBd (32 µs/bit) you get away with it, but you are tuning a resistor
  to fix a part choice.
- The **H11L1** is an IRED driving a **Schmitt-trigger** logic output — 1 MHz
  bandwidth, no CTR tuning, clean symmetric edges straight into a UART RX pin,
  5 kV isolation, and it is a $0.16 part. Multiple practitioners report it works
  directly in MIDI [V/C].
- The **6N137** is also excellent but wants 4.5–5.5 V on the output side, which
  would mean a level shifter into a 3.3 V SoC. The H11L1 is specified from
  **3.0 V**, so 3.3 V operation is in spec — near the bottom of the range, but
  in it.

**Use `H11L1S(TA)`, LCSC C78589 — the SMD version**, $0.156, 17 045 in stock [V].
The through-hole `H11L1` (C78588, $0.161) would be a hand-solder step. On
onsemi, the SMD equivalent is `H11L1SM` (C899473).

Circuit: DIN pin 4 → 220 Ω → anode; DIN pin 5 → cathode; 1N4148 reverse across
the LED; output pin 6 with 4.7 kΩ pull-up to 3.3 V, straight to a T113 UART RX.
**[I]** Put a 0 Ω/jumper and a test point between the opto and the SoC so the
MIDI input can be driven from a TTL source during bring-up without a DIN cable —
that is how the FPGA-fed variant in the parent plan will feed it anyway.

**Baud rate sanity check [I]:** the T113 UARTs are clocked from 24 MHz.
24 000 000 / 16 / 31 250 = **48 exactly**. 31 250 baud is an exact divisor, not
an approximation. No PLL gymnastics, no fractional divider.

### 3.4 Boot flash: not needed, but lay the footprint

The BROM checks **SD card first, then SPI flash, then falls through to FEL** on
USB-OTG [C]. If we boot from SD, an SPI NOR is genuinely redundant.

Keep the SOIC-8 footprint and **do not populate it**. Reasons: it costs one
footprint and zero dollars; SPI-NOR boot is the answer if SD timing turns out to
be the thing keeping cold-boot latency above target; and awboot already supports
`fel/spi/sdmmc/emmc` targets [C], so the software side is free. `W25Q128JVSIQ`,
C97521, $1.69, 28 285 in stock [V], if we ever stuff it.

Corollary: **you cannot brick this board.** With no SD card and no SPI flash,
the BROM enters FEL and `xfel` can push code over USB-C. No JTAG needed, no
bootstrap straps needed.

### 3.5 Things deliberately not on the board

- **No USB host.** Parent plan § 2.6, deliberate. USB-C is device-mode FEL and
  5 V input only.
- **No CH340N on board.** A 4-pin header and a $1 USB-TTL dongle does the console
  job, saves a connector, saves an extended-part fee, and avoids arbitrating one
  USB connector between FEL and console. (CH340N is C506813, $0.269 [V], if we
  change our minds.)
- **No eMMC, Ethernet, display, Wi-Fi.** None of them is on the critical path to
  "does Munt render in real time and does audio come out".
- **No on-chip codec used for line out.** The T113-i *has* an integrated
  ADC/DAC codec [DS-derived brief], which could in principle delete the PCM5102A.
  **[I]** Keep the PCM5102A: it is $0.84, it needs no MCLK (internal PLL, three
  wires: BCK/LRCK/DIN), its output stage is designed for 2 Vrms line level, and
  the I2S path is the one we actually want to prove because that is what a
  production module would use. Route the on-chip codec to a test point if the
  pins are free, and treat it as a fallback.

---

## 4. Costed estimate

### 4.1 Per-board parts

| | qty 5 | qty 25 |
|---|---:|---:|
| **Compute: T113-i** | $4.84 | $4.65–4.84 |
| **DRAM: 1× 4 Gbit DDR3L x16** | **$6.53 – $13.77** (take **$10**) | **$6.50 – $13.80** (take **$10**) |
| Everything else (§ 3.1 rows 3–16) | $4.10 | $3.60 [GUESS, modest volume break] |
| **Parts subtotal / board** | **≈ $18.9** | **≈ $18.3** |

The compute/DRAM split, called out as asked: **at qty 5, $4.84 of SoC against
roughly $10 of DRAM.** The memory costs about twice the processor, and it is the
only line whose price could double again before we order. Everything else on the
board together costs less than the DRAM chip.

### 4.2 Board and assembly

All JLCPCB figures are **[GUESS]** — their site is blocked from here. The
process facts they rest on are [V].

| | qty 5 | qty 25 |
|---|---:|---:|
| PCB, 4-layer impedance-controlled, ~80 × 55 mm, ENIG | $30 | $55 |
| SMT setup fee | $8 | $8 |
| Stencil | included | included |
| Extended-part feeder fees (~12 unique @ $1.50–$3.00) | $18–36 | $18–36 |
| **X-ray inspection (mandatory for BGA)** — JLCPCB states it is applied automatically and priced per order [V]; the amount is unpublished | **$25–50** | **$40–80** |
| Placement labour | $15 | $60 |
| **Build subtotal** | **$96 – $139** | **$181 – $239** |

### 4.3 Bottom line

| | qty 5 | qty 25 |
|---|---:|---:|
| Parts | $95 | $458 |
| Board + assembly | $96 – $139 | $181 – $239 |
| **Total order** | **$191 – $234** | **$639 – $697** |
| **Per board** | **$38 – $47** | **$26 – $28** |

Call it **$45/board at qty 5 and $28/board at qty 25**, and add a mental 30 %
because the first spin of a BGA board is never the last one. Two spins at qty 5
before a qty-25 run is the realistic plan, so budget **~$500 to get to a working
design** in hardware cost alone, not counting the weeks.

---

## 5. Board realities

### 5.1 Layer count

**4 layers is defensible. 6 is what I would order.**

The case for 4: one x16 DDR3 device, point-to-point, no fly-by, ~50 nets
(16 DQ + 2 DQS pairs + 2 DM + ~15 addr + 3 BA + CKE/CS/ODT/RAS/CAS/WE/RESET +
1 CK pair). A Sig / GND / PWR / Sig stack routes DQ and DQS on top over the
solid ground on L2, and address/command on the bottom over a VCC-DRAM pour on
L3 — which is exactly the reference-plane assignment DDR3 practice calls for
(DQ/DQS/DM/CK reference VSS; address/command/control reference VDD) [C]. The bus
is short. This is the easy corner of DDR3 design.

The case for 6, which I find stronger:
- **MYIR's own T113-i SoM is a 6-layer board** [V]. That is the closest thing we
  have to a known-good layout of this exact interface, and its designers chose 6.
- 6 layers gives a dedicated ground plane on both sides of the DDR signal layer,
  real return-path stitching around the BGA escape, and somewhere to put the
  power rails that is not the DDR reference plane.
- At qty 5–25 the delta is on the order of $20–40 on the whole order. It is
  the cheapest risk reduction available anywhere in this project.

**Recommendation: 6-layer, JLC06161H-2313-class stack.** Fall back to 4 only if
the quote surprises us.

### 5.2 Stack-up, and the trap in JLCPCB's default

Read directly from a GitHub mirror of JLCPCB's stack-up definitions
(`ayberkozgur/jlcpcb-design-rules-stackups`) [C]:

| Stack-up | Top → L2 dielectric | Er | 50 Ω microstrip width [I] |
|---|---|---|---|
| **JLC7628, 4-layer 1.6 mm — JLCPCB's *default*** | **0.200 mm** prepreg 7628 | 4.6 | **≈ 0.33 mm (13 mil)** |
| **JLC2313, 4-layer 1.6 mm** | **0.100 mm** prepreg 2313 | 4.05 | **≈ 0.18 mm (7 mil)** |
| **JLC2313, 6-layer 1.6 mm** | 0.100 mm 2313 / core 0.565 / 2116 0.127 | 4.05 / 4.5 / 4.25 | ≈ 0.18 mm outer |

**This is the specific way a 4-layer DDR3 board at JLCPCB goes wrong.** On the
default 7628 stack the prepreg is 0.2 mm, so a 50 Ω trace has to be a third of a
millimetre wide — you cannot escape a 0.5 mm-pitch BGA with those, and if you
neck them down you have lost impedance control across the whole escape region.
**Specify the 2313 stack-up explicitly** (JLCPCB's naming for these is
`JLC04161H-2313A` / `JLC06161H-2313`-style [V]) and the 0.1 mm prepreg gives
~0.18 mm traces, which is routable with a short neck-down under the BGA only.

JLCPCB design rules, from the same mirror [C]:
- 4-layer, 1 oz: 4 mil trace and clearance on all layers; min via Ø 0.45 mm
- 6-layer, 1 oz: **3.5 mil** trace and clearance on all layers; min via Ø 0.45 mm
- Impedance control is offered at no extra charge on multilayer [V]

**[I]** 3.5 mil (0.089 mm) trace/space on 6 layers is enough to escape a 0.5 mm
pitch BGA with one trace between balls, which is the thing that actually decides
whether this board is buildable. That, more than signal integrity, is the
argument for 6 layers if the pitch turns out to be 0.5 mm.

### 5.3 Length matching and impedance

**These numbers are [C]/[I] — from general DDR3 practice and Chinese summaries
of the Allwinner guide, not from my own read of the T113 design guide.** They are
the right order of magnitude and the right *shape*; get the real tolerances out
of `T113_硬件设计指南V1_0.pdf` before committing.

| Constraint | Value |
|---|---|
| Single-ended impedance | 50 Ω (one Allwinner-referenced summary says 40 Ω — **resolve this from the guide**) |
| Differential (DQS±, CK±) | 100 Ω (or 80 Ω if the 40 Ω figure is right) |
| Within a byte lane (DQ/DQM to its DQS) | ±20–30 mil (0.5–0.75 mm) |
| DQS± intra-pair skew | ±5 mil |
| Byte lane to byte lane | not matched to each other (each lane has its own strobe); keep within ~120 mil for sanity |
| Address / command / control to CK | ±100 mil (2.5 mm) |
| Spacing within a group | 3H (H = height above the reference plane) |
| Spacing between groups | ≥ 5H |
| DQS to DQ spacing | 5H |
| DQS / CK differential coupling | tight, pair spacing < 2× trace width |
| Reference plane, DQ/DQS/DM/CK | VSS |
| Reference plane, address/command/control | VDD |

With 0.1 mm dielectric, "3H" is 0.3 mm and "5H" is 0.5 mm — comfortable. The
tight-coupling requirement on DQS and CK pairs is the one that fights the escape
region; plan to break out of the BGA first and pair up afterwards.

**Termination [I]:** with a single point-to-point x16 device there is no fly-by
topology and no series-stub problem. DDR3 on-die termination handles DQ/DQS.
Address/command on a one-load bus is normally left unterminated — **no VTT rail,
no termination resistor pack.** That deletes an entire regulator and ~20
resistors from the board and is the main reason a single-chip DDR3 design is
tractable. Confirm against the Allwinner DRAM template before trusting it;
their guide reportedly ships template designs that have been stability-tested
and says to follow them for DRAM connections and filter-cap selection [V].

VREF: DDR3 needs VREFCA (and VREFDQ) at VDDQ/2. 1 % divider from VCC-DRAM with a
0.1 µF cap at the pin, per JEDEC. Keep the divider next to the DRAM.

Decoupling **[I]**: one 100 nF 0402 per power ball cluster on the SoC (budget
~25), 4× 100 nF + 2× 10 µF on the DRAM, 22 µF bulk on VCC-DRAM, 22 µF + 100 µF
on VDD-CPU close to the inductor, ferrite-isolated 3.3 V for the PCM5102A analog
supply with its own 10 µF + 100 nF.

### 5.4 Will JLCPCB assemble it?

**Almost certainly yes, and the evidence is decent, but I could not read their
page myself.**

- JLCPCB's assembly capability page states support for **BGA, LGA, QFN, QFP and
  other high-density IC packages**, specifically **"ball grid array packages up
  to 17 × 17 mm with 0.5 mm pitch"**, and excludes only **"large BGAs with pitch
  < 0.5 mm"** [V]. A 13 × 13 mm 337-ball part is inside that.
- The DDR3 chip is a 96-ball FBGA, 9 × 14 mm, 0.8 mm pitch — trivially inside it.
- **X-ray inspection is mandatory and automatic** for BGA/QFN/LGA: "if such
  components are detected in your BOM, X-ray inspection is automatically applied
  by the system during production. The associated cost is calculated based on
  your order configuration" [V]. **The amount is not published.** My $25–80 range
  is a [GUESS] and is the largest single uncertainty in § 4.2.
- They caution that "boards with dense component placements and/or lower pitch
  components (i.e. 0.4 mm BGAs) require more accuracy with machine setup, and in
  some cases will be required to run slower" [V] — a schedule note, not a refusal.
- Extended-part fee: **$3 per unique extended component**, with one source
  reporting a 2025-12-19 change to **$1.50** [V]. Either way, ~12 extended lines
  is $18–36 per order, flat, so it hurts at qty 5 and disappears at qty 25.

**The residual risk is not the package, it is the part.** JLCPCB must have the
T113-i and the specific DDR3 SKU *in the assembly library with stock*, not merely
on LCSC. The T113-i has a `jlcpcb.com/partdetail` page [V], which is good
evidence. Confirm before committing, and confirm the DDR3 part separately — in
this market it is the one likely to come back "not available for assembly", in
which case the fallback is **consigned parts** (buy the DRAM yourself, ship it to
them) at extra fee and extra delay.

---

## 6. SoM on a carrier versus our own BGA

### 6.1 The two options, priced

| | **Our own board** | **SoM + carrier** |
|---|---|---|
| Compute + DRAM | $14.8 (T113-i + 512 MB) | **$15–32** per module: Forlinx FET113i-S **$15–23** (256 MB DDR3 + 256 MB NAND, Alibaba, MOQ 1) [V]; MYIR MYC-YT113i **$19.80–31.80** (512 MB/1 GB + eMMC), "from $12.80 in volume" [V] |
| Rest of BOM | $4.1 | ~$4.1 (same DAC, opto, SD, jacks) |
| PCB | 6-layer impedance-controlled | **2- or 4-layer, no impedance control** |
| Assembly | BGA ×2, X-ray, ~$96–139 at qty 5 | no BGA, no X-ray, ~$40–60 at qty 5 [GUESS] |
| **Per board, qty 5** | **$38–47** | **$40–60** [GUESS] |
| **Per board, qty 25** | **$26–28** | **$32–48** [GUESS] |
| Design time to first spin | 3–5 weeks [GUESS] | **3–5 days** [GUESS] |
| Respin probability | high | low |
| DDR3 procurement risk | **ours** | **the vendor's** |
| Power sequencing risk | **ours** | **the vendor's** |
| DRAM parameter risk | **ours** | vendor ships a working U-Boot |
| Form factor | whatever we want | 37 × 39 mm module + connector footprint |
| Availability from here | LCSC/JLCPCB, one order | Alibaba / MYIR / Forlinx, MOQ + lead time + shipping, not through JLCPCB |

The costs are **within noise of each other at both quantities.** Our own board
is nominally cheaper per unit, and that advantage is entirely eaten by one
respin.

### 6.2 What I would do first

**Start with a SoM on a carrier.** Four reasons, in order of weight:

1. **It deletes the riskiest item outright.** External DDR3 on a T113-i is the
   one thing nobody in public has documented — the DRAM parameter sets in
   awboot and mainline U-Boot are all for the *in-package* S3/S4. Buying a SoM
   means buying a validated DDR3 layout *and* a vendor U-Boot with working DRAM
   parameters, which is the other half of the problem and the half that
   `boot/` currently has no answer for.
2. **It deletes the power-sequencing question**, which is the item this document
   is honestly blocked on, and which Forlinx's own manual says can
   *irreversibly damage the processor* if you get it wrong [V].
3. **In the 2026 DRAM market, the module vendor has already bought the memory.**
   Our own board exposes us to a 39-week lead time and 10–20 % monthly price
   moves on the single most expensive line in the BOM.
4. **It is not more expensive.** At the quantities this project will ever build,
   the module premium is roughly one respin's cost, and we would be paying it
   whether or not we respin.

And the sequencing that makes it painless: `docs/PLAN.md` § 3 already says buy a
cheap dev board first and prove the software on someone else's silicon. The SoM
route is the same idea carried one step further — prove Munt, I2S, MIDI and SD
on a Forlinx OK113i-S or MYIR MYD-YT113i ($62–75 [V]), then move that *same*
module onto our own 4-layer carrier with our DAC and our MIDI input. The carrier
is a weekend. Nothing about the software changes.

**Do the bare BGA second, if at all.** It is the right answer only if this
becomes a product at hundreds of units, where $10–20/board of module premium
starts to matter and where a respin amortises. By then we will have a working
carrier schematic, a proven audio path, a known-good U-Boot with real DRAM
parameters, and — crucially — we will have had the Allwinner EVB schematic and
design guide in hand long enough to copy them properly.

The one thing that would change my mind: if the SoM's 37 × 39 mm footprint plus
connectors cannot be made to fit the WaveBlaster form factor (~63 × 38 mm) that
§ 5 of the parent plan cares about. Check that against the actual module
drawing before committing — but note it does not affect the *test* board, which
has no form-factor constraint at all.

---

## 7. Action items, in order

1. **Fetch the four Allwinner PDFs** listed in § 1.5 into `hw/ref/` from an
   unblocked network: datasheet v1.4, user manual v1.4, **T113 硬件设计指南
   V1.0**, **T113-i EVB V1.0 schematic**. Nothing else in this list can start
   properly without them.
2. From those: the **exact rail list, the power-up order, the RC-delay values on
   the buck enables, and the reset release timing**. Write them into this file
   as [DS] facts and delete the [V] paraphrases in § 2.1.
3. From those: the **ball pitch** (§ 1.4) and the **DDR3 length-matching and
   impedance rules** (§ 5.3), replacing the generic DDR3 numbers.
4. Re-quote on an unblocked network: T113-i at LCSC **and** JLCPCB assembly
   availability; the chosen DDR3 SKU likewise; the JLCPCB X-ray fee on a real
   quote with a BGA in the BOM. § 4.2 is the weakest part of this document.
5. **Price and order one SoM dev board** (Forlinx OK113i-S or MYIR MYD-YT113i)
   and one cheap T113-S3 board (MangoPi MQ-R / 100ask DongshanPI) — the latter
   is the RTF measurement proxy `bench/` needs, the former is the real target.
6. Only after `bench/` reports RTF under 0.6: draw the carrier schematic.

## 8. Sources

Datasheets and vendor documentation (all currently unreachable from here;
links recorded for a human):
- T113-i datasheet v1.4 — https://dl.100ask.net/Hardware/MPU/T113i-Industrial/T113-i_Datasheet_V1.4.pdf
- T113-i datasheet v1.4 mirror — https://bbs.aw-ol.com/assets/uploads/files/1678720117073-eb74fed1-28d3-4451-b1e6-c9be3af193af-t113-i_datasheet_v1.4.pdf
- T113-i datasheet + user manual index — https://bbs.aw-ol.com/topic/3108/
- T113 硬件设计指南 V1.0 — http://file.whycan.com/files/202304/T113-I/T113_硬件设计指南V1_0.pdf
- T113-i EVB V1.0 schematic — http://file.whycan.com/files/202304/T113-I/T113-i_EVB_V1_0_20220507.pdf
- T113-S3 datasheet v1.6 (contrast part) — https://mangopi.org/_media/t113-s3_datasheet_v1.6.pdf
- T113-S3 user manual v1.3 — https://mangopi.org/_media/t113-s3_user_manual_v1.3_.pdf
- AXP313A datasheet — https://mangopi.org/_media/axp313a_datasheet_v0.1-20201105.pdf
- SY8089 datasheet — https://datasheet.lcsc.com/lcsc/Silergy-Corp-SY8089AAAC_C78988.pdf

Reference designs and modules:
- Forlinx FET113i-S SoM — https://www.forlinx.net//product/t113i-s-system-on-module-143.html
- Forlinx OK113i-S SBC — https://www.forlinx.net/single-board-computer/t113i-s-sbc-144.html
- Forlinx FET113i-S / OK113i-S brief — https://forlinx.net/download/FET113i-S-SoM-OK113i-S-SBC-Brief.pdf
- MYIR MYC-YT113i SoM — https://en.myir.cn/T113/76.html · https://www.myir.cn/shows/118/66.html
- MYIR MYD-YT113i board — https://www.myirtech.com/list.asp?id=742
- MYIR MYC-YT113i datasheet (Mouser mirror) — https://www.mouser.com/datasheet/2/951/MYC_YT113i-3359804.pdf
- MangoPi MQ-R — https://linux-sunxi.org/MangoPi_MQ-R
- 100ask T113i-Industrial docs — https://docs.100ask.net/dshanpi/en/docs/T113i-Industrial/BoardIntroduction/
- 100ask/DongshanPI T113-Pro Buildroot — https://github.com/DongshanPI/buildroot_100ask_t113-pro
- awboot — https://github.com/szemzoa/awboot
- whycan "自制T113-I 核心板一次成功开机" — https://whycan.com/t_10651.html
- Tronlong T113-i SoM spec — https://blog.csdn.net/Tronlong/article/details/146339705

Distributor listings (search-extracted, none opened directly):
- T113-i — https://www.lcsc.com/product-detail/C7545092.html · https://jlcpcb.com/partdetail/Allwinner-T113i/C7545092
- MT41K256M16HA-125 IT:E — https://www.lcsc.com/product-detail/C20463642.html
- MT41K256M16TW-107:P — https://www.lcsc.com/product-detail/C253882.html
- MT41K128M16JT-125 IT:K — https://www.lcsc.com/product-detail/C115792.html
- NT5CC256M16ER-EK — https://www.lcsc.com/product-detail/C428584.html
- PCM5102APWR — https://www.lcsc.com/product-detail/C107671.html
- H11L1S(TA) — https://lcsc.com/product-detail/Optocouplers-Logic-Output_Everlight-Elec_C78589.html
- SY8089AAAC — https://www.lcsc.com/product-detail/C78988.html
- AXP313A — https://www.lcsc.com/product-detail/Power-Management-Specialized_X-Powers-Tech-AXP313A_C5365290.html
- W25Q128JVSIQ — https://www.lcsc.com/product-detail/NOR-FLASH_Winbond-Elec-W25Q128JVSIQ_C97521.html
- CH340N — https://www.lcsc.com/product-detail/C506813.html
- TF-01A — https://www.lcsc.com/product-detail/C91145.html
- X322524MSB4SI 24 MHz — https://www.lcsc.com/product-detail/Crystals_YXC-X322524MSB4SI_C15643.html
- SC-20S 32.768 kHz — https://www.lcsc.com/product-detail/Crystals_Seiko-SC-20S-32-768kHz-20PPM-9pF_C97603.html

Process and manufacturing:
- JLCPCB assembly capabilities — https://jlcpcb.com/capabilities/pcb-assembly-capabilities
- JLCPCB assembly price — https://jlcpcb.com/help/article/pcb-assembly-price
- JLCPCB assembly FAQs — https://jlcpcb.com/help/article/pcb-assembly-faqs
- JLCPCB impedance / stack-ups — https://jlcpcb.com/impedance
- JLCPCB stack-ups mirrored on GitHub (**read directly**) — https://github.com/ayberkozgur/jlcpcb-design-rules-stackups
- JLCPCB autogenerated stack-ups — https://github.com/gsuberland/jlcpcb_autogenerated_stackups
- JLCPCB assembly guide (third-party) — https://www.schemalyzer.com/en/blog/manufacturing/jlcpcb/jlcpcb-assembly-guide
- JLCPCB cost optimising (third-party) — https://highway.hackclub.com/guides/JLC-cost-optimizing

Software / bring-up context:
- u-boot `arch/arm/mach-sunxi/Kconfig` (**read directly**) — https://github.com/u-boot/u-boot/blob/master/arch/arm/mach-sunxi/Kconfig
- sunxi DRAM init for R528/T113-s3/D1 — https://lists.denx.de/pipermail/u-boot/2023-October/534801.html
- U-Boot sunxi board docs — https://docs.u-boot.org/en/v2026.04/board/allwinner/sunxi.html
- linux-sunxi Boot Process / FEL — https://linux-sunxi.org/Boot_Process · https://linux-sunxi.org/FEL/USBBoot
- linux-sunxi T113-s3 — https://linux-sunxi.org/T113-s3

2026 DRAM market:
- Memory Shortage Watch: DDR3L lead times past 39 weeks (May 2026) — https://blog.findchips.com/memory-shortage-watch-ddr3l-ddr4-may-2026/
- Sourcing legacy DDR2/DDR3 in 2026 — https://suntsu.com/blog/sourcing-legacy-ddr2-ddr3-memory-in-2026-supply-technical-guide/
- DDR4 prices over 50 % in Q3 2026, DDR3 impacted — https://wccftech.com/memory-shortages-drive-ddr4-prices-over-50-in-q3-2026-ddr3-also-impacted-by-higher-costs/
- DRAM and NAND prices jump as suppliers tighten — https://www.astutegroup.com/news/memory-shortages/dram-and-nand-prices-jump-as-samsung-sk-hynix-and-micron-tighten-supply/

MIDI optocoupler:
- Hackaday, optocouplers for MIDI — https://hackaday.com/2018/05/09/optocouplers-defending-your-microcontroller-midi-and-a-hot-tip-for-speed/
- 6N137 vs 6N138 vs 6N139 for MIDI — https://www.kieranreck.co.uk/MIDI-6N137-vs-6N138-vs-6N139/
- Designing MIDI in/out schematics — https://n-audio.net/designing-midi-in-and-midi-out-schematics/
