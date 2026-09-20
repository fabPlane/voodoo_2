# Spinal Verilog naming, the Glide harness taps, and the out-of-tree Makefile

Status: **written 2026-09-20** as part of Phase 0.4 of
[`docs/amaranth-rewrite-plan.md`](../amaranth-rewrite-plan.md) (Spinal side).

Everything here is *observed*, not recalled: the naming rules come from Verilog that
`voodoo.diff.GenOne` actually generated on 2026-09-20, the harness inventory from a parse of
`ext/SpinalVoodoo/emu/sim/harness/sim_harness.cpp`, and the build wiring from the two upstream
Makefiles. Reproduce any of it with:

```bash
cd fpga
scala-cli run . --main-class voodoo.diff.GenOne -- --list          # registry
scala-cli run . --main-class voodoo.diff.GenOne -- CoreSim rtl/diff
python3 src/voodoo/diff/ports.py rtl/diff/CoreSim_spinal.v -o src/voodoo/diff/portmaps
verilator --lint-only -Wno-fatal --top-module CoreSim_spinal rtl/diff/CoreSim_spinal.v
```

`fpga/rtl/` is gitignored; the committed artefacts are the JSON port maps under
`fpga/src/voodoo/diff/portmaps/`.

---

## 0. Which SpinalHDL, and which reset

Two different SpinalHDL versions are in play, and they produce slightly different Verilog:

| Build | SpinalHDL | Reset | Where |
|---|---|---|---|
| Upstream sim (`make -C ext/SpinalVoodoo native/sim/…`) | **1.13.0** (`ext/SpinalVoodoo/project.scala`) | **ASYNC, active high** — `always @(posedge clk or posedge reset)` | `ext/SpinalVoodoo/emu/sim/rtl/CoreSim.v` |
| This repo's ports and `GenOne` | **1.15.0** (`fpga/project.scala`) | **SYNC, active high** by default — `always @(posedge clk)` + `if(reset)` | `fpga/rtl/**` |

`GenOne` defaults to SYNC/HIGH because that is what the Console target uses
(`GowinGenSupport.verilog` in `fpga/src/voodoo/gowin/CoreGowinGen.scala`) and what Amaranth's
`sync` domain emits natively. Pass `--async-reset` to reproduce upstream's
`GenSupport.simVerilog` behaviour (Spinal's own default `ClockDomainConfig()` is ASYNC/HIGH; only
the DE10 config states it explicitly).

Clock and reset port names are always `clk` and `reset` (Spinal's defaults; this project never
renames them). No `clkEnable`, no `softReset`, no active-low variants anywhere in the core.

`GenOne` also sets `inlineRom = true`, so ROM contents (dither tables, TMU lookup tables) land in
`initial begin … end` blocks instead of `$readmemb` sidecars. Upstream's sim build does the
opposite and then rewrites the `$readmemb` paths to absolute ones with `sed` (see §3). Amaranth
emits inline initialisation, so `inlineRom = true` is the closer match for a diff.

---

## 1. Naming conventions in generated Verilog

### 1.1 Ports

* A Component's ports are the leaves of whatever `Data` it declares. Nothing is packed: every
  scalar leaf becomes one Verilog port. `CoreSim` has 19 ports; `Rasterizer` has 259;
  `AfixOps` has 282.
* **`io_` prefix is not universal.** It exists only because the upstream style is
  `val io = new Bundle { … }` — the Bundle is named `io`, and Spinal prefixes with the Scala
  `val` name. Components that declare ports at component level get no prefix:
  * `PciFifo`, `ColorCombine`, `CoreSim`, `Tmu` → `io_…`
  * `Rasterizer` → `i_valid`, `o_payload_…`, `enableClipping`, `clipLeft`, `running`
  * `AfixOps` (this repo) → `a_vertex`, `add_vertex`, …
* Separator is always `_`; Spinal does not emit escaped identifiers for this design
  (`grep -c '\\' CoreSim_spinal.v` → 0), so no `\name ` forms to handle.
* Direction keywords: `input wire`, `output wire`, `output reg` (an output driven from an
  `always` block). `reg`-ness is a Verilog artefact, not part of the interface — the port map
  records it as `verilog_type` so a diff harness does not mistake it for a semantic difference.
* Vectors are always `[msb:0]` unsigned, even for `SInt`/`AFix`. Sign is *not* in the Verilog;
  the Amaranth side must know each port's signedness from the Scala/port map, not from the RTL.
  Single-bit signals have no range, except `Bits(1 bits)`/`UInt(1 bits)` which come out as
  `[0:0]` (e.g. `io_cpuBus_cmd_payload_fragment_opcode`).

### 1.2 Stream, Flow and Fragment

`spinal.lib.Stream[T]` flattens to three names under a common base:

```
<base>_valid      // master -> slave
<base>_ready      // slave  -> master
<base>_payload…   // one port per payload leaf; absent for Stream(NoData)
```

`Flow[T]` is the same minus `_ready`. `Stream[Fragment[T]]` (which is what `Bmb` cmd/rsp are)
inserts `_last` and `_fragment`:

```
io_cpuSide_cmd_valid
io_cpuSide_cmd_ready
io_cpuSide_cmd_payload_last
io_cpuSide_cmd_payload_fragment_source      [3:0]
io_cpuSide_cmd_payload_fragment_opcode      [0:0]
io_cpuSide_cmd_payload_fragment_address     [11:0]
io_cpuSide_cmd_payload_fragment_length      [1:0]
io_cpuSide_cmd_payload_fragment_data        [31:0]
io_cpuSide_cmd_payload_fragment_mask        [3:0]
```

So a `Bmb` master/slave is exactly two such groups (`_cmd`, `_rsp`), and the rsp side carries
only the fields the parameters enable (`PciFifo`'s rsp has no `address`/`length`/`mask`).

`ports.py` reconstructs this: each port gets `role` (`valid`/`ready`/`payload`), `stream` (the
base), and `field_path` (the payload path below `payload`). The `streams` section of the JSON
lists every handshake group and classifies it `stream` (valid+ready), `flow` (valid only) or
`bundle`.

### 1.3 Bundles and Vecs

* A nested `Bundle` adds one `_<fieldName>` level per nesting step, in declaration order:
  `i_payload_grads_redGrad_start`, `i_payload_config_fbzColorPath_rgbSel`.
* A `Vec` adds a numeric segment for the index, **in place**, not only at the end:
  `i_payload_coeffs_0_a`, `i_payload_coeffs_2_c`, `i_payload_xrange_1`,
  `io_commandReady_0 … io_commandReady_4`.
* **`_<N>` is ambiguous.** Spinal also appends `_1`, `_2`, … to disambiguate colliding
  *identifiers* — module names (`SimBmbMemory` / `SimBmbMemory_1`), instance names
  (`core_1`, `pciFifo_1`, `colorCombine_1`, `fog_1`, `rasterizer_1`), and internal signals.
  A trailing numeric segment on a port is therefore either a Vec index or a de-duplication
  suffix. `ports.py` resolves it by looking for siblings: a contiguous `_0 … _k` family under
  the same prefix is reported as `trailing_index_kind: "vec_index"`, anything else as
  `"ambiguous"`, and the Amaranth wrapper should then take the name literally.

### 1.4 AFix

`AFix` is a `MultiData` whose only hardware member is `raw`, so **AFix never appears in the
Verilog**: an `AFix` leaf becomes a single unsigned vector of `raw`'s width, with the binary
point implied. Measured with SpinalHDL 1.15 (`Config.voodoo1()`):

| `QFormat` | port width | `exp` | `intWidth` | `fracWidth` | equivalent `AFix.SQ/UQ` |
|---|---|---|---|---|---|
| `SQ(16, 4)` vertex | 16 | -4 | 12 | 4 | `SQ(11 bits, 4 bits)` |
| `SQ(24, 12)` vColor | 24 | -12 | 12 | 12 | `SQ(11 bits, 12 bits)` |
| `SQ(32, 12)` vDepth | 32 | -12 | 20 | 12 | — |
| `SQ(34, 8)` coefficient | 34 | -8 | 26 | 8 | — |
| `SQ(32, 18)` texCoords | 32 | -18 | 14 | 18 | — |
| `SQ(32, 30)` w | 32 | -30 | 2 | 30 | — |
| `SQ(46, 30)` texCoordsHi | 46 | -30 | 16 | 30 | — |
| `SQ(48, 18)` texCoordsAccum | 48 | -18 | 30 | 18 | — |
| `SQ(48, 30)` wAccum | 48 | -30 | 18 | 30 | — |
| `QFormat(19, 8, true)` colorMul | 19 | -8 | 11 | 8 | `AFix.SQ(10 bits, 8 bits)` |
| `UQ(8, 0)` | 8 | 0 | 8 | 0 | `AFix.UQ(8 bits, 0 bits)` |
| `UQ(9, 8)` | 9 | -8 | 1 | 8 | `AFix.UQ(1 bits, 8 bits)` |

Two traps for the Amaranth fixed-point library:

1. **`QFormat.width` is the total width for signed formats** (sign bit included in
   `nonFraction`), so `SQ(16, 4)` is 12 integer bits *including sign* plus 4 fractional bits →
   16 raw bits. `AFix.SQ(i bits, f bits)`, by contrast, takes the magnitude integer width and
   produces `1 + i + f` bits. `SQ(19, 8) == AFix.SQ(10 bits, 8 bits)`.
2. **`UQ(0, 8)` elaborates to a ZERO-bit AFix.** `AFix(UQ(0, 8))` has `intWidth = -8`,
   `maxRaw = 0`, `raw.getWidth = 0`. The core only ever names it as a `fixTo` *target*, in
   `ColorCombine.clampAFixToFactor`, which is consequently dead code; the live blend-factor
   format is `AFix.UQ(1 bits, 8 bits)`. `AfixOps` skips zero-width results rather than emitting
   an illegal 0-bit port, and `fpga/src/voodoo/diff/AfixOps.scala` documents this inline.

`AfixOps_spinal.v` is the machine-readable version of all of this: 282 ports covering `+ - * -x`,
`fixTo` in seven rounding modes, `floor/ceil/roundHalfDown/roundHalfUp/round/floorToZero/
ceilToInf` at exponent 0, the three `sat` shapes, the `.truncated` assignment path, and the
cross-format `fixTo` pairs copied out of Rasterizer / SpanWalker / TriangleSetup / ColorCombine.
Output widths are *not* asserted anywhere — they are the measurement. Read them from
`fpga/src/voodoo/diff/portmaps/AfixOps.json`.

### 1.5 Internals: registers, memories, submodules

Relevant when the harness (or a waveform diff) reaches inside a module rather than at its ports.

* Signal names follow the Scala `val` path inside the Component, `_`-joined, with `Area` and
  `Composite` names included: `start_streamWhile_counter_value`,
  `controlPlane_swapBuffer_swapCountReg`, `queuedReqsRaw_fifo_io_occupancy`.
* Spinal inserts synthetic names for anonymous nodes: `_zz_<something>`, `when_<File>_l<line>`
  (e.g. `when_AFix_l770_23`, `when_StreamWhile_l65`). These are line-number dependent and will
  **not** survive a SpinalHDL version bump — never build a harness on them.
* `Mem` becomes a plain Verilog array: `reg [15:0] ram [0:4095];`,
  `reg [16:0] lookupTables_recipTable [0:511];`, `reg [407:0] logic_ram [0:63];`
  (`logic_ram` is `StreamFifo`'s storage). Byte-enabled memories are split per byte lane into
  `ram_symbol0 … ram_symbol3` — which is exactly how the Glide harness reads the framebuffer
  (§2).
* Submodule instances take the Scala `val` name, de-duplicated with `_N`:
  `Core core_1`, `SimBmbMemory fbWriteRam`, `SimBmbMemory_1 texRam`, `BmbArbiter_4 fbArbiter`,
  `PciFifo pciFifo_1`, `PixelPipeline pixelPipeline_1`. Note that the *module* name gets a
  suffix too when the same class is elaborated with different parameters
  (`SimBmbMemory` vs `SimBmbMemory_1`).
* Verilator flattens this hierarchy into C++ member names as
  `<Top>__DOT__<inst>__DOT__<signal>`, with `__DOT__` per level; signals promoted to the parent
  scope by `--public-flat-rw` appear with a single `__DOT__` and the rest `_`-joined
  (`CoreSim__DOT__core_1__DOT__pciFifo_1_io_cpuSide_cmd_ready`). Both spellings appear in the
  harness.

### 1.6 Top-module renaming

`GenOne` calls `setDefinitionName("<Name>_spinal")` on the top only. Submodule names are left
untouched, so `CoreSim_spinal.v` contains `Core`, `PciFifo`, `Tmu`, … unchanged and
`verilator --top-module CoreSim_spinal` sees the real hierarchy. When the Amaranth module is
generated as `<Name>_am`, the two can be instantiated side by side in one Verilator build
without a name clash — as long as the Amaranth side also namespaces its submodules.

### 1.7 Undriven outputs: only the top is checked

`PhaseCheck_noLatchNoOverride` rejects an undriven combinational output **on the top component**,
and also on an inner component whose output is actually read. `CoreDebug`
(`ext/SpinalVoodoo/src/voodoo/core/CoreTypes.scala`) declares ten debug words —
`pipelineBusySources`, `fbAccess`, `fbColorCache{,Req,Expected,Occupancy}`,
`fbAuxCache{,Req,Expected,Occupancy}` — that **nothing in the design ever assigns**.
`Core`/`CoreSim` elaborate anyway because RegisterBank does not really consume them and Spinal
prunes the whole path; a bare `PixelPipeline` top does not. `DiffHierWrap` (in
`DiffWrappers.scala`) handles this generically: it re-exports a component's io one level up and
skips any output with `hasDataAssignment == false`, printing the list. For `PixelPipeline` that
is the ten words above — the Amaranth port should simply not implement them (or drive them to
zero and say so), and the diff harness must not expect them.

Sweep result on 2026-09-20: all **44 buildable registry entries** generate, and all 44
`verilator --lint-only -Wno-fatal` runs report **0 errors** (warnings are the usual
`WIDTHTRUNC` / `CMPCONST` / `UNSIGNED` classes the repo's own `fpga/Makefile` suppresses).

---

## 2. What `sim_harness.cpp` reaches into

`ext/SpinalVoodoo/emu/sim/harness/sim_harness.cpp` (1075 lines) is the C API the Glide2x sim
backend links against (`sim_init`, `sim_write`, `sim_read`, `sim_idle_wait`, `sim_read_fb`, …,
declared in `sim_harness.h`). Most of it drives the `CoreSim` **ports** (`top->io_cpuBus_…`),
which the Amaranth `CoreSim` reproduces for free by keeping the port list identical. The
interesting part is the hierarchical `top->rootp->…` accesses.

**Count: 153 distinct hierarchical names appear in the file — but only 14 are live.** The other
139 sit inside two `#if 0` blocks (lines 191–213 and 218–475) with the comment *"Internal signal
logging below is best-effort debug only and is currently disabled for the native CoreSim harness
until signal names are refreshed"*. That is very good news for the port: the Amaranth `CoreSim`
must replace 14 names to link and run, not 150.

### 2.1 Live taps (must be replaced by a `dbg` port bundle)

| Hierarchical name | Used by | Purpose |
|---|---|---|
| `CoreSim__DOT__fbWriteRam__DOT__ram_symbol0..3` | `sim_read_fb`, `sim_write_fb_bulk`, `dump_fb_word` (→ `dump_fb_debug`, `dump_timeout_debug`) | Backdoor access to the 4 MiB framebuffer BRAM, one byte lane per symbol, indexed by word |
| `CoreSim__DOT__texRam__DOT__ram_symbol0..3` | `sim_write_tex_bulk`, `sim_read_tex` | Same for the 8 MiB texture BRAM |
| `CoreSim__DOT__core_1__DOT__controlPlane_swapBuffer__DOT__swapCountReg` | `sim_set_swap_count` (write!), `sim_get_swap_count` | 2-bit pending-swap counter; the only *written* internal signal |
| `…core_1__DOT__pciFifo_1_io_cpuSide_cmd_ready` | `dump_timeout_debug` | Bus-stall post-mortem |
| `…core_1__DOT__frontdoor_io_cpuBus_cmd_ready` | `dump_timeout_debug` | idem |
| `…core_1__DOT__pciFifo_1_io_pciFifoFree` | `dump_timeout_debug` | idem |
| `…core_1__DOT__pciFifo_1_io_syncDrained` | `dump_timeout_debug` | idem |
| `…core_1__DOT__pixelPipeline_1_io_debug_pipelineBusy` | `dump_timeout_debug` | idem |

Design consequence for the Amaranth `CoreSim` (plan §4.3, task 7.2):

* **Memories**: the two sim BRAMs cannot become ports. Either (a) keep byte-lane arrays with
  predictable Amaranth names and let the copied harness use the new spelling, or (b) give
  `CoreSim` a small backdoor port (`dbg_mem_sel/addr/wdata/we/rdata`) and rewrite
  `sim_read_fb` / `sim_write_*_bulk` to pump it. (b) is slower per word but immune to naming
  churn; (a) keeps bulk uploads at memcpy speed, which matters for texture uploads in the trace
  replays. Recommendation: (a), with the array names pinned by an Amaranth `Memory` instance
  name, and a test that asserts the C++ symbol exists.
* **`swapCountReg` is written**, so it needs a real write path: a `dbg_swap_count_set` +
  `dbg_swap_count_we` input pair on `CoreSim` (sim-only, like `flushFbCaches`) plus a
  `dbg_swap_count` output.
* **The five debug scalars** become five bits/fields of a `dbg` output bundle:
  `dbg_pci_cmd_ready`, `dbg_frontdoor_cmd_ready`, `dbg_pci_fifo_free[6:0]`,
  `dbg_sync_drained`, `dbg_pipeline_busy`.

### 2.2 Statistics: all currently stubbed

`sim_harness.h` declares ~120 `sim_get_*` counters. In the current harness:

* Live: `sim_get_cycle` (from the harness's own `sim_time`), `sim_get_total_read_ticks`,
  `sim_get_total_read_count`, `sim_get_total_write_ticks`, `sim_get_total_write_count` — all
  counted in the C++ harness around bus transactions, not read from RTL.
* **56 functions are literally `{ return 0; }`** — every `sim_get_fb_*`, `sim_get_tex_*`,
  `sim_get_pixels_*` counter, plus `sim_dump_first_fb_writes`, which is a no-op.

So no RTL statistics counter is consumed today. The Amaranth port should keep the same
signatures returning 0, and treat re-enabling them as a separate task — the RegisterBank's
statistics registers (read over the bus by `sim_read`) remain the real, tested path.

### 2.3 Disabled taps, grouped (the design input for a future `dbg` bundle)

If the pixel-trace debugging is ever revived, these are the taps it wants. Groups are the
harness's own section comments; names are shown with the `CoreSim__DOT__core_1__DOT__` prefix
stripped.

| Group (harness comment) | Signals | Feature |
|---|---|---|
| fbWrite logging (lines 191–213) | `writeColor_o_fbWrite_valid/_payload_address/_payload_data/_payload_mask` | `fbwrite_log` text-area write log |
| TMU pipeline logging | `rasterizer_1_o_valid`, `…_o_payload_coords_0/1`, `…_o_payload_grads_sGrad/tGrad/wGrad` (6) | `tmu_log` cutoff analysis |
| TMU input | `rasterFork_0_valid/_payload_coords_0/1`, `tmu_1_io_input_ready`, `tmu_1_io_input_payload_s/t/w`, `tmu_1__DOT__texS/texT/sPoint/tPoint/lodLevel/pointAddr` (13) | watched-pixel trace (`SIM_WATCH_X/Y`) |
| TMU join | `tmuJoined_valid/_payload_2_coords_0/1`, `tmu_1_io_output_payload_texture_r/g/b/textureAlpha` (7) | idem |
| ColorCombine output | `colorCombine_1_io_output_valid/ready/_payload_color_r/g/b/_payload_alpha/_payload_coords_0/1`, `colorCombine_1__DOT__io_input_payload_config_rgbSel/mselect/textureEnable` (11) | idem |
| Fog output | `fog_1_io_output_valid/ready/_payload_color_r/g/b/_payload_alpha/_payload_coords_0/1` (8) | idem |
| FramebufferAccess input | `fbAccess__DOT__io_input_valid`, `fbAccess_io_input_ready`, `…_payload_color_r/g/b`, `_payload_alpha`, `_payload_coords_0/1`, `fbAccess_io_input_payload_depth`, `…_payload_wDepth`, `…_payload_fbzMode_*` (5), `…_payload_alphaMode_alphaBlendEnable` (16) | idem |
| FramebufferAccess request fire | `request_colorAddress/auxAddress/_passthrough_coords_0/1/_passthrough_newDepth`, `io_fbReadColorReq_valid/ready`, `io_fbReadAuxReq_valid/ready`, `readJoined_valid`, `fetchedJoined_valid`, `queuedReqsRaw_fifo_io_occupancy` (12) | idem |
| FramebufferAccess fetched/joined | `fetched_valid/ready/_payload_colorData/_payload_auxData/_payload_passthrough_*` (6), `oldDepth`, `depthPassed`, `depthKill` (15) | idem |
| FramebufferAccess after depth test | `afterDepthTest_valid/ready/_payload_passthrough_color_r/g/b/_passthrough_alpha/_passthrough_coords_0/1`, `depthPassed`, `depthKill` (10) | idem |
| FramebufferAccess output | `fbAccess_io_output_valid/_payload_color_r/g/b/_payload_alpha/_payload_coords_0/1`, `blended_r/g/b`, `io_input_payload_alphaMode_rgbSrcFact/rgbDstFact/alphaBlendEnable`, `io_input_payload_fbzMode_depthFunction` (14) | idem |
| Pre-dither arbiter output | `preDitherMerged_valid/_payload_r/g/b/_payload_coords_0/1/_payload_fbBaseAddr/_payload_auxBaseAddr/_payload_fbPixelStride/_payload_rgbWrite/_payload_auxWrite` (11) | idem |
| Pre-dither (RGB888 into dither) | `ditherJoined_valid/_payload_2_r/g/b/_payload_2_coords_0/1/_payload_2_fbBaseAddr/_payload_2_fbPixelStride` (8) | idem |
| Write stage | `writeColor__DOT__i_fromPipeline_valid/_payload_coords_0/1/_payload_data/_payload_fbBaseAddr`, `frontBufferBase`, `backBufferBase`, `swapBuffer_1__DOT__swapCountReg` (8) | idem |

Shape of the natural `dbg` bundle these imply: one sub-bundle per pipeline stage
(`raster`, `tmu_in`, `tmu_out`, `cc`, `fog`, `fb_in`, `fb_req`, `fb_fetch`, `fb_depth`,
`fb_out`, `predither`, `dither`, `write`), each carrying `valid`, `ready`, the stage's
coordinates, and the stage's payload — i.e. a tap of the *stream between stages*, not of
arbitrary internals. That is portable; `__DOT__` paths are not.

Note also that two of the disabled names (`swapBuffer_1__DOT__swapCountReg` and
`controlPlane_swapBuffer__DOT__swapCountReg`) are two different spellings of the same register
from different Spinal versions — more evidence that hierarchical names are not a contract.

---

## 3. Driving the upstream build from an out-of-tree Amaranth `CoreSim`

Nothing in `ext/SpinalVoodoo/**` may be edited. The good news: every path the build needs is a
plain `make` variable, and command-line variables beat in-makefile `=` assignments, so an
`amaranth/Makefile` can point the upstream build at its own RTL and harness copy.

### 3.1 How the upstream build finds things today

`ext/SpinalVoodoo/Makefile` (top level):

```make
SIM_INTERFACE ?= core
SIM_MODEL = $(if $(filter de10,$(SIM_INTERFACE)),CoreDe10,CoreSim)
SIM_DIR    = emu/sim
SIM_STAMP  = $(SIM_DIR)/obj_dir/V$(SIM_MODEL)__ALL.a
SIM_RTL    = $(SIM_DIR)/rtl/$(SIM_MODEL).v
SCALA_SRCS := $(shell find src -name '*.scala') project.scala
$(SIM_STAMP): $(SCALA_SRCS) $(TRACE_MODE_FILE)
	$(MAKE) -C $(SIM_DIR) all SIM_INTERFACE=$(SIM_INTERFACE)
```

`ext/SpinalVoodoo/emu/sim/Makefile`:

```make
PROJ_ROOT   = ../..
RTL_DIR     = rtl
OBJ_DIR     = obj_dir
HARNESS_DIR = harness
SIM_TOP      = CoreSim                     # or CoreDe10
SIM_GEN_CLASS= voodoo.CoreSimGen
RTL_FILE     = $(RTL_DIR)/$(SIM_TOP).v
HARNESS_SRC  = $(HARNESS_DIR)/sim_harness.cpp
VERILATOR_FLAGS = --cc --output-split 20000 --output-split-cfuncs 5000 --decorations none \
                  --public-flat-rw --x-assign fast --x-initial fast --noassert \
                  -Wno-WIDTHTRUNC -Wno-WIDTHEXPAND -Wno-CMPCONST -Wno-UNSIGNED
$(RTL_FILE): $(SCALA_SRCS) …                # runs java -cp <scala-cli classpath> voodoo.CoreSimGen
	…
	sed -i 's|$$readmemb("\([^/"]*\.bin\)"|$$readmemb("$(abspath $(RTL_DIR))/\1"|g' $@
```

`ext/SpinalVoodoo/emu/glide/glide2x/sst1/glide/src/Makefile.sim` (the Glide backend `.so`):

```make
SIM_DIR   = ../../../../../../emu/sim
SIM_MODEL = CoreSim
CFLAGS   += -I$(SIM_DIR)/harness
BACKEND_OBJ = $(SIM_DIR)/obj_dir/sim_harness.o $(SIM_DIR)/obj_dir/voodoo_trace_writer.o \
              $(SIM_DIR)/obj_dir/verilated.o   $(SIM_DIR)/obj_dir/verilated_threads.o
BACKEND_LIB = -Wl,--whole-archive $(SIM_DIR)/obj_dir/V$(SIM_MODEL)__ALL.a -Wl,--no-whole-archive
```

The test executables (`tests/Makefile.sim`) link only against `libglide2x.so`, so they need no
override at all.

### 3.2 Variables an `amaranth/Makefile` must override

Note `--public-flat-rw` above: that flag is what makes every internal signal reachable from C++.
Keep it for the Amaranth build too — both for the 14 live taps and for FST debugging.

| Make invocation | Override | Value for the Amaranth build |
|---|---|---|
| `make -C ext/SpinalVoodoo native/sim/check-all` | `SIM_DIR` | `$(abspath amaranth/emu/sim)` |
| | `SIM_RTL` | `$(abspath amaranth/rtl/CoreSim.v)` (only used for the `output/<test>/CoreSim.v` symlink) |
| | `SIM_STAMP` | `$(abspath amaranth/emu/sim/obj_dir/VCoreSim__ALL.a)` |
| | `SCALA_SRCS` | ` ` (empty) — otherwise the stamp rule re-runs the Spinal generator |
| `make -C ext/SpinalVoodoo/emu/sim all` | `RTL_DIR` | directory holding the Amaranth `CoreSim.v` |
| | `HARNESS_DIR` | `amaranth/emu/sim/harness` (the adapted copy) |
| | `OBJ_DIR` | a build dir under `amaranth/` |
| | `RTL_FILE` | the Amaranth `CoreSim.v`, **and** it must be newer than `$(SCALA_SRCS)` or the rule regenerates it with `java … voodoo.CoreSimGen` |
| `make -C …/glide/src -f Makefile.sim` | `SIM_DIR` | `$(abspath amaranth/emu/sim)` |
| | `SIM_MODEL` | `CoreSim` (unchanged — keep the Amaranth top named `CoreSim`) |

**Recommendation.** Overriding `emu/sim/Makefile` is fragile precisely because of its
`$(RTL_FILE)` rule (it would happily regenerate the Spinal RTL over the Amaranth one). Copy that
Makefile into `amaranth/emu/sim/Makefile` — the plan already calls for copying the harness
directory — delete the `$(RTL_FILE)` generation rule, and keep the identical `VERILATOR_FLAGS`,
`CXX32`/`-m32` settings, `OBJ_DIR` layout and object-file names
(`sim_harness.o`, `verilated.o`, `voodoo_trace_writer.o`, `VCoreSim__ALL.a`). Then only the
*top* Makefile and the Glide `Makefile.sim` need command-line overrides, both of which are
harmless `-C` invocations:

```make
# amaranth/Makefile (sketch)
UP      := $(abspath ../ext/SpinalVoodoo)
AMSIM   := $(abspath emu/sim)

glide-lib: rtl-core-sim sim-model
	$(MAKE) -C $(UP)/emu/glide/glide2x/sst1/glide/src -f Makefile.sim \
	        SIM_DIR=$(AMSIM) SIM_MODEL=CoreSim

check-all: glide-lib
	$(MAKE) -C $(UP) native/sim/check-all \
	        SIM_DIR=$(AMSIM) SIM_RTL=$(abspath rtl/CoreSim.v) \
	        SIM_STAMP=$(AMSIM)/obj_dir/VCoreSim__ALL.a SCALA_SRCS=
```

Three further facts that bite:

1. **32-bit build.** The harness and the Verilator model are compiled `-m32` (`CC32`/`CXX32`)
   because Glide2x is 32-bit. Same for the Amaranth copy; nothing changes, but it means this
   path only runs on Linux (`hyper-ms-lin`), not on the Mac.
2. **`$readmemb` rewriting.** Only needed while `inlineRom = false`. If the Amaranth generator
   inlines memory initialisation (it does by default), drop the `sed` step entirely.
3. **`--decorations none`** requires Verilator ≥ 5.03x; both machines have 5.052 (AGENTS.md).

---

## 4. Files

| Path | What |
|---|---|
| `fpga/src/voodoo/diff/GenOne.scala` | registry + `scala-cli` main; `GenOne <Module> [outDir]` |
| `fpga/src/voodoo/diff/DiffWrappers.scala` | `StreamWhileDiffWrap`, `FpxxDiffWrap`, `DiffHierWrap` |
| `fpga/src/voodoo/diff/AfixOps.scala` | AFix op-by-op reference module |
| `fpga/src/voodoo/diff/ports.py` | `*_spinal.v` → JSON port map |
| `fpga/src/voodoo/diff/portmaps/*.json` | committed port maps (the RTL itself is gitignored) |
| `fpga/rtl/diff/*_spinal.v` | generated, gitignored |
