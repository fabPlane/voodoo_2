# Gowin bitstream build and hardware-check runbook

Last updated: 2026-09-22

This runbook builds a Tang Console 138K image on `hyper-ms-lin`, programs it
into volatile FPGA SRAM, and records enough evidence to decide whether a PR is
safe to merge.  It is deliberately separate from CoreSim validation: a routed
bitstream is not proof that the renderer or DDR works.

See also `hardware/tang-console-138k/README.md` for board controls and the
debug HUD.

## Acceptance summary

A hardware check passes only when all of the following are true:

1. Gowin synthesis, placement, routing, timing analysis, and bitstream
   generation complete without errors or unrouted nets.
2. SRAM programming completes successfully on the expected JTAG device.
3. The display has a stable 640×480 HDMI signal.
4. The HUD reports `CAL=1`, `W=0`, and `UF=0`.
5. Every new or affected menu demo launches and shows the expected image.
6. No new wedge, persistent black screen, scanout underflow, tearing, or
   corrupt framebuffer appears.

`HDMI visible` with `CAL=0` is a failure, not a partial pass suitable for
merging.

## 1. Use an isolated Linux worktree

The saved checkout at `/home/hyper/projects/me/vodoo_2` has unrelated local
changes.  Never reset or clean it.  Create a dedicated worktree from the exact
PR head or, for a stacked PR, from the prospective merge of its current base
and head.

Example for PR #4:

```sh
ssh hyper-ms-lin
cd /home/hyper/projects/me/vodoo_2
git fetch origin codex/voodoo-console-sdram
git fetch origin cursor/console-menu-homebrew-demos-1d1c
git worktree add --detach /home/hyper/.codex/worktrees/pr4-hw-gate \
  origin/cursor/console-menu-homebrew-demos-1d1c
cd /home/hyper/.codex/worktrees/pr4-hw-gate
git submodule update --init ext/SpinalVoodoo
git status --short --branch
git rev-parse HEAD
git submodule status ext/SpinalVoodoo
```

If the PR branch is behind a changed stacked base, create the worktree at the
base head and merge the PR head with `--no-commit`.  Build that prospective
merge, not an obsolete standalone head.  Record both commit IDs.

## 2. Match the Gowin IP to the board wrapper

The IP cache under `fpga/hw/gowin/ip/` is gitignored and must be populated in
each clean worktree.  Treat it as a versioned build input even though Git does
not track it.

Before copying an IP cache, compare the wrapper interface:

| Interface | Clean PR #4 16-bit wrapper | Experimental dirty 32-bit wrapper |
|---|---:|---:|
| DDR address | 14 bits | 15 bits |
| DDR DQ | 16 bits | 32 bits |
| DDR DQS | 2 pairs | 4 pairs |
| Native write data | 128 bits | 256 bits |
| Native write mask | 16 bits | 32 bits |

Known DDR IP hashes:

```text
16-bit encrypted IP: 687cb7ea5f113aef2df19042c2317ebd4dfdd3794e6a2e3f69fe5709d8a8e13a
32-bit revision-C IP: d7cae5a16467ca35ee3f95411537344ae3702e1dba86a06571530e3dd0180c57
```

Check the selected file:

```sh
sha256sum fpga/hw/gowin/ip/DDR3_Memory_Interface/ddr3_memory_interface.v
```

Using the 32-bit IP with the 16-bit wrapper fails before placement with
`PA2014` on dangling `dqs_iobuf_gen[2]` and `[3]`.  Conversely, a build that
routes with the 16-bit IP can still fail physical DDR calibration.  Keep the
IP, wrapper, CST, native-bus widths, and clocking change coherent.

If reusing a cache from another machine, copy only into the isolated worktree
and preserve its source/hash in the test report.  Do not overwrite the dirty
saved checkout.

## 3. Select the correct build target

For the DDR3-backed `TangConsole138k` menu used by PR #4:

```sh
cd /home/hyper/.codex/worktrees/pr4-hw-gate/fpga
GOWIN_PNR_FORCE_LOCAL=1 make gowin-console
```

Do not substitute `gowin-console-sdram`; that is a separate experimental
design under `fpga/hw/gowin/console-sdram/`.

The normal target performs these steps:

1. Generate `fpga/rtl/gowin/TangConsole138k.v` from Scala/SpinalHDL.
2. Apply the distributed-FIFO RAM annotation pass.
3. Run `gw_sh build_console.tcl` locally on Linux.
4. Synthesize, place, route, analyze timing, and generate bitstreams.

Do not run competing Gowin jobs.  Check for an existing build first:

```sh
pgrep -af '[/]gw_sh build_console.tcl'
```

The design is dense and placement may remain at one progress marker for many
minutes while consuming one CPU at approximately 100%.  Silence alone is not
a hang.  Check `ps` before terminating it.

## 4. Inspect build outputs

Expected directory:

```text
fpga/hw/gowin/console/impl/pnr/
```

Important files:

```text
spinalvoodoo_tang_console_138k.fs       SRAM programmer input
spinalvoodoo_tang_console_138k.bin      TangCore/SD-card binary
spinalvoodoo_tang_console_138k.rpt.txt  resource and routing report
spinalvoodoo_tang_console_138k.tr.html  timing report
spinalvoodoo_tang_console_138k.log      build log, when emitted
```

Record sizes and hashes:

```sh
ls -lh fpga/hw/gowin/console/impl/pnr/spinalvoodoo_tang_console_138k.{fs,bin}
sha256sum fpga/hw/gowin/console/impl/pnr/spinalvoodoo_tang_console_138k.bin
```

Inspect the text report for utilization and any unrouted-net indication.  A
2026-09-22 PR #4 build used 48% logic, 71% CLS, 56% BSRAM, and 20% DSP.  Its
`.bin` SHA-256 was:

```text
ae94ba361d022b494d981ea96694b6e4d923da23248b87358ad90aed53da163b
```

Do not program an image if PnR failed, the expected artifact predates the
build, or the report contains unrouted nets.

## 5. Confirm the JTAG cable and device

The programmer is installed at:

```text
/home/hyper/opt/gowin/Programmer/bin/programmer_cli
```

List cables and scan cable index 1:

```sh
/home/hyper/opt/gowin/Programmer/bin/programmer_cli --scan-cables F
/home/hyper/opt/gowin/Programmer/bin/programmer_cli \
  --device GW5AST-138C \
  --cable-index 1 \
  --frequency 2.5MHz \
  --scan
```

Expected device ID on the current setup:

```text
Family: GW5AST
Name: GW5AST-138B GW5AST-138C
ID: 0x0001081B
```

Stop if the scan finds no device or a different target.

## 6. Program volatile SRAM

Use the `.fs` file and operation index 2:

```sh
/home/hyper/opt/gowin/Programmer/bin/programmer_cli \
  --device GW5AST-138C \
  --operation_index 2 \
  --cable-index 1 \
  --frequency 2.5MHz \
  --fsFile /home/hyper/.codex/worktrees/pr4-hw-gate/fpga/hw/gowin/console/impl/pnr/spinalvoodoo_tang_console_138k.fs
```

This changes FPGA SRAM only; a power cycle or another load replaces it.  A
successful 2026-09-22 PR #4 load ended with:

```text
Programming... 100%
User Code is: 0x0000C5C9
Status Code is: 0x00022230
Finished.
```

Record the complete programmer output.  Do not report success based only on
the command's progress reaching a nonzero percentage.

## 7. Verify the live display

Inspect the monitor or a camera aimed at it.  The current Linux host exposes
`/dev/video0`; it may already be owned by
`hardware/tang-console-138k/webcam_stream.py`.  Reuse an existing stream
rather than killing it.  If the device is free, a one-frame capture is:

```sh
ffmpeg -hide_banner -loglevel error \
  -f v4l2 -input_format mjpeg -video_size 1920x1080 \
  -i /dev/video0 -frames:v 1 -y /tmp/gowin-hardware.jpg
```

Check these HUD fields before launching a demo:

| Field | Required | Meaning |
|---|---:|---|
| `CAL` | `1` | DDR initialization/calibration completed |
| `W` | `0` | Core/host bridge is not wedged |
| `UF` | `0` | HDMI scanout has not underflowed |
| `PLAY` | changes when launched | Menu handed control to the ROM player |
| `PC` | advances | Packed trace player is issuing commands |

Then run every demo affected by the PR.  For PR #4 that means at least:

1. Homebrew pinwheel — colored triangle fan.
2. Homebrew depth — overlapping green/blue depth-tested triangles.
3. Homebrew alpha — overlapping translucent triangles.
4. Existing `test04` as a baseline control.

Capture a still for each demo and record whether the HUD reports underflow or
wedge.  A menu/debug screen proves HDMI electrical output only; it does not
prove DDR, rendering, swapping, or scanout from the framebuffer.

## 8. Report template

Use this compact report for every candidate:

```text
Parent commit:
Submodule commit:
Worktree:
Gowin version:
DDR IP SHA-256 and width:
Build command:
Build duration:
Synthesis/place/route/timing:
Unrouted nets:
Resource use (logic/CLS/BSRAM/DSP):
BIN path, size, SHA-256:
JTAG cable/device ID:
Programmer result/user code/status:
HDMI stable:
HUD CAL/W/UF:
Demo results and image paths:
Decision: pass / fail / blocked
```

Never merge a synthesis-changing PR merely because it generated a bitstream.
The final decision requires both the simulator gates and the physical
`CAL=1`, demo-visible hardware gate.

