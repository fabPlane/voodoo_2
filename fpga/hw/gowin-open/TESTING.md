# Testing and debugging the open Apicula build

This runbook covers the Yosys + nextpnr-himbaechel + Apicula flow for the
Tang Retro Console 138K (`GW5AST-LV138PG484AC1/I0`, device database
`GW5AST-138C`). It is intentionally separate from the Gowin EDA Voodoo build.

The most important rule is to prove one layer at a time. A bitstream reaching
FPGA user mode does not prove that its clock, logic, RAM, HDMI, or DDR path
works.

## Safety and naming rules

- Load a new image into volatile SRAM first. A reset, power cycle, or TangCore
  reload can replace it.
- Keep the `-open` suffix on every open-flow artifact.
- Never overwrite `cores/console138k/voodootang.bin`; that name belongs to the
  Gowin EDA build.
- The reserved name for a complete open-flow Voodoo core is
  `cores/console138k/voodootang-open.bin`.
- Do not rename a textual `.fs` file to `.bin`. Run `make bin`; TangCore needs
  the packed binary form.
- Only one process may own the JTAG adapter. Stop other Programmer, OpenOCD,
  openFPGALoader, or automated test processes before drawing conclusions from
  a failed scan.
- Record the Git revisions, tool paths, artifact hash, and programmer output
  for every hardware test. Files under `build/` are ignored and can easily be
  stale or belong to another branch.

## What has actually been proven

The following later open-flow milestones were validated on physical hardware:

| Layer | Hardware result |
|---|---|
| Configuration framing and GPIO | FPGA entered user mode with status `0x00022230`; RESET changed the test output |
| Fabric JTAG mailbox | Initial word `0x4a544147`; repeated writes and readbacks passed |
| 50 MHz clock and ordinary logic | JTAG counter advanced by about 5,000,000 counts per 100 ms |
| Inferred block RAM | 1,024-word write/read/compare returned `0xb5a5b3ff` |
| PLL/HCLK/serializer packing | A complete HDMI color-bar image was packed and entered user mode |
| Visible open HDMI | Not yet a reliable pass; configuration status alone is insufficient |
| Open DDR3 PHY/controller | Not implemented or validated |

Some diagnostic sources and host scripts were added after the branch that may
currently be checked out. Before following a JTAG-probe command, confirm that
the named target and script exist. Useful milestone commits include:

```text
cf6ab5f  Fix GW5AST JTAG fabric node mapping
3fee517  Add GW5AST clock divider blocks
cd6d492  Route GW5AST PLL clocks through HCLK
```

Do not move branches or restore those files over unrelated work merely to run
a test. Use a clean worktree when reproducing an older milestone.

## Board controls and common false positives

The constraints in this directory map the controls as follows:

| Signal | Pin | Physical control | Important behavior |
|---|---|---|---|
| `key0_n` | `AA13` | MODE | Pressing it can make TangCore reload/reconfigure the FPGA; it is not a dependable ordinary button during SRAM tests |
| `key1_n` | `AB13` | RESET | Usable as the normal active-low test input |
| `led_n[0]` | `G11` | status LED channel | Active low |
| `led_n[1]` | `U12` | status LED channel | Active low |

The RGB status LED is also influenced by board/TangCore behavior. A transient
red, orange, purple, or dark state is not enough to prove FPGA logic. Likewise,
a monitor can retain an old frame, show its own blue screen, or take several
seconds to report loss of signal. Prefer numeric JTAG checks over visual LED
judgment.

For the current `hdmi_colorbars.v`, the actual RTL behavior is:

- MODE (`key0_n`) resets the video counters while low, although the board may
  reconfigure before this is useful.
- RESET (`key1_n`) blanks active video while low.
- LED 0 indicates PLL lock.
- LED 1 toggles from the frame counter.

## 1. Capture the exact source and toolchain

From the repository checkout:

```sh
git status --short --branch
git rev-parse HEAD
git submodule status
```

The Apicula fork used for GW5AST work is normally checked out separately as
`fabPlane/apicula`. Record its state as well:

```sh
git -C /Users/hyper/projects/fabPlane/apicula status --short --branch
git -C /Users/hyper/projects/fabPlane/apicula rev-parse HEAD
```

On the machine that performs the build, record which programs are really used:

```sh
command -v yosys
command -v nextpnr-himbaechel
command -v gowin_pack
yosys -V
nextpnr-himbaechel --version
```

The nextpnr chip database and `gowin_pack` must come from compatible Apicula
sources. A new packer with an older chip database, or the reverse, can produce
a valid-looking file with missing BELs, routes, or configuration fuses.

## 2. Build without accidentally using stale output

The normal Mac-to-Linux wrapper copies this directory to the Linux build host,
runs the selected target there, and copies `build/` back:

```sh
cd fpga/hw/gowin-open
./build-linux.sh blinky
./build-linux.sh bram_test
./build-linux.sh uart_hello
./build-linux.sh hdmi_colorbars
```

The first three targets exist in the base checkout. HDMI support depends on the
Apicula/nextpnr revision. On a machine with a matching recent OSS CAD Suite:

```sh
make -j3
```

Use `make -B <target>` when a target must be rebuilt without deleting the many
historical diagnostic files under `build/`:

```sh
make -B blinky
```

Expected stages for one target are:

1. Yosys writes `build/<name>-synth.json`.
2. nextpnr writes `build/<name>-pnr.json`.
3. Apicula writes `build/<name>-open.fs`.
4. `make bin` writes `build/<name>-open.bin` for TangCore.

Do not accept a build merely because the final file exists. Check that its
modification time is from the current run and preserve its hash:

```sh
ls -lhT build/blinky-*
shasum -a 256 build/blinky-open.fs
```

For each build, save the Yosys and nextpnr output. Reject it if nextpnr reports
an unrouted net, impossible BEL placement, or a resource error. The Makefile
currently permits timing failure for bring-up, so a completed build is not a
timing pass.

### Build-stage diagnosis

| Failure point | Investigate first |
|---|---|
| Yosys does not recognize a primitive | `synth_gowin -family gw5a`, primitive spelling, and port/parameter names |
| nextpnr says no BEL remains | Chip database BEL creation, package/device selection, or an impossible placement constraint |
| nextpnr cannot route a clock | PLL/BUFG/HCLK/CLKDIV nodes and the matching chip database revision |
| `gowin_pack` rejects a BEL or pip | Packer support and attribute encoding for that exact GW5AST resource |
| `.fs` is produced but FPGA does not enter user mode | Framing, template baseline, checksum/CRC, READY/DONE configuration, or wrong device |
| FPGA enters user mode but the test fails | Stop changing framing; debug clocks, routes, primitives, and application RTL |

## 3. Convert an SRAM-tested image for the SD card

Only do this after the matching `.fs` image has passed an SRAM test:

```sh
cd fpga/hw/gowin-open
make bin
```

Copy the resulting file under a distinct name, for example:

```sh
cp build/blinky-open.bin "$CARD/cores/console138k/blinky-open.bin"
cp build/bram_test-open.bin "$CARD/cores/console138k/bram_test-open.bin"
cp build/uart_hello-open.bin "$CARD/cores/console138k/uart_hello-open.bin"
```

Replace `$CARD` with the mounted FAT32 card path. Eject the card cleanly. Keep
the stock cores and `voodootang.bin` untouched so there is always a known-good
control image.

## 4. Check the JTAG adapter before programming

First ensure no competing process owns the cable:

```sh
pgrep -af 'programmer_cli|openocd|openFPGALoader' || true
```

On macOS the repository wrapper uses the Gowin Programmer transport and cable
index 4. Run it from the repository root:

```sh
cd /Users/hyper/projects/me/vodoo_2
hardware/tang-console-138k/program_debug_sram.sh \
  fpga/hw/gowin-open/build/blinky-open.fs
```

Follow its prompt: hold MODE while starting the load and keep holding until the
board reaches the alternating-loader indication. Release it when programming
begins. The load is volatile; it does not overwrite the SD card or FPGA flash.

Cable indices are host-specific. If using Programmer directly, scan first and
confirm the expected device rather than copying an index from another machine:

```text
Family: GW5AST
Name: GW5AST-138B GW5AST-138C
ID: 0x0001081B
```

The programmer has sometimes printed a trailing generic `Error` even after an
open image configured successfully. Treat the separate status code as the
authority. The known-good configured/user-mode value is:

```text
0x00022230
```

This proves only that configuration completed. It does not prove the design is
executing correctly.

## 5. Run the tests in increasing order of complexity

Do not start with HDMI or DDR. Run the smallest test that exercises the newly
changed resource.

### A. Configuration and static GPIO

Use the hardware-proven GPIO milestone or another static input-to-output probe.
Confirm `0x00022230`, then operate RESET. This separates bitstream framing and
pin configuration from clocks and sequential logic.

Pass condition: the separately read configuration status is correct and the
RESET-dependent output changes repeatably.

### B. Fabric JTAG mailbox

When the later diagnostic files are present, build and load
`jtag_mailbox-open.fs`, then run these commands from the repository root:

```sh
python3 hardware/tang-console-138k/jtag_mailbox.py --read
python3 hardware/tang-console-138k/jtag_mailbox.py --write 0xa5c35a7e
python3 hardware/tang-console-138k/jtag_mailbox.py \
  --sequence 0,3 --count 4 --interval 0.15
```

Pass condition: the initial value is `0x4a544147`, and every transfer reads
back the value installed by the preceding scan. The ER1 protocol uses JTAG
instruction `0x42` and a 34-bit scan for a 32-bit payload.

### C. 50 MHz clock and sequential logic

Load the JTAG clock probe and sample it:

```sh
python3 hardware/tang-console-138k/jtag_clock_probe.py \
  --count 8 --interval 0.1
```

Pass condition: the decoded counter changes every time and advances by roughly
5,000,000 counts per 100 ms. This proves the input clock, global clock path,
carry/DFF logic, and JTAG clock-domain readback without trusting the LEDs.

The simple `blinky` RTL is still useful as a visual smoke test, but it is weaker
evidence than the counter. Its intended behavior is LED 0 blinking and LED 1
following MODE; MODE's board-level reconfiguration behavior makes the second
part unsuitable as a strict acceptance test.

### D. Inferred block RAM

Prefer the JTAG-readable BSRAM test over `bram_test`'s LEDs:

```sh
python3 hardware/tang-console-138k/jtag_bram_probe.py
```

Pass condition:

```text
0xb5a5b3ff
```

That means state `DONE`, completion set, failure clear, and final address
`0x3ff` after writing and checking 1,024 32-bit words. The base `bram_test`
only reports a blinking pass LED or a lit failure LED, which is too easy to
confuse with board status colors.

### E. UART

`uart_hello` transmits `OPEN GOWIN 138K OK` at 115200 baud once per second on
the FPGA-to-BL616 UART pins. This is an internal FPGA/BL616 connection, not a
guarantee that text will appear automatically as a USB serial port on the host.
No visible host output therefore does not by itself mean the FPGA UART failed.

Validate this target with a logic analyzer, a BL616 firmware path known to
forward that UART, or a JTAG counter that records transmitted characters.
Pressing MODE to request an immediate message is not a dependable test because
MODE can reconfigure the FPGA.

### F. PLL and HDMI

The color-bar design should produce a 640x480 signal with eight vertical bars.
Validate it in this order:

1. Configuration status is `0x00022230`.
2. JTAG exposes PLL lock, pixel counter, frame counter, and ideally serializer
   activity.
3. Measure approximately 25 MHz pixel and 125 MHz serializer clocks.
4. Confirm the monitor reports a stable 640x480 mode.
5. Confirm the visible bars match the RTL, then hold RESET and verify active
   video blanks and returns.

A black screen, a monitor-generated blue screen, or one color change at load
time is not a pass. If PLL lock is low, debug PLL attributes and its placement.
If lock is high but the frame counter is static, debug HCLK/CLKDIV routing. If
both counters run but there is no signal, inspect OSER10, ELVDS output
configuration, differential pin order, and TMDS bit ordering.

### G. DDR3

DDR3 is a separate project, not an extension of the BSRAM pass. The open flow
still needs a GW5A-specific DDR PHY even if LiteDRAM supplies controller logic.
Bring it up in isolation before attaching Voodoo or HDMI scanout:

1. Prove the memory clock and reset sequencing through JTAG.
2. Expose initialization/training state and failure step numerically.
3. Run single-address writes and reads.
4. Run address, inverse-address, walking-one, and walking-zero patterns.
5. Sweep all byte lanes and banks.
6. Run a sustained test with refresh enabled and count errors.
7. Only then attach framebuffer traffic.

Do not copy the Tang Mega 138K Pro generated DDR block or CST wholesale. That
example uses a different FPGA package and a 32-bit bus. Its memory timing
profiles and test algorithms are useful references, but the Console needs its
own PG484 pinout and PHY implementation.

## 6. Debug Apicula with a minimal vendor oracle

When a primitive or route is wrong, reduce the test to one input, one instance
of the resource, and one observable output or JTAG register. Generate a matching
Gowin EDA image only as an oracle, then compare decoded tile data. Do not copy
encrypted HDL or secret material; the useful evidence is the configuration
delta produced by a controlled public RTL design.

For each experiment:

1. Keep top-level RTL and constraints identical between vendor and open flows.
2. Change one parameter, route, or primitive at a time.
3. Unpack both bitstreams with the same Apicula revision.
4. Diff tile coordinates and local fuse positions.
5. Reverse-bisect the differing fuse groups.
6. Encode the result in the chip database or packer, not as a post-pack patch.
7. Add a focused unit test in the Apicula fork.
8. Rebuild the minimal image and rerun the numeric hardware test.

The successful GW5AST work required template-backed bitstreams because blank
tiles contain non-zero family/device defaults. A configured tile may need both
setting and clearing relative to that baseline. Starting every unknown tile
from zero can produce a syntactically valid image that configures but behaves
incorrectly.

Run the relevant Apicula tests in its configured Python environment before
using a changed packer. For example:

```sh
cd /home/hyper/projects/fabPlane/apicula
.venv/bin/python -m unittest discover -s tests -p 'test_gw5ast*.py'
```

If that environment does not exist, create/install it according to the fork's
own development instructions; do not silently fall back to a different
system-wide `apycula` package.

## 7. Symptom guide

| Symptom | Likely interpretation | Next check |
|---|---|---|
| Programmer cannot open cable | Another process owns FT2232, wrong channel/index, or disconnected cable | Stop competing processes and scan the chain |
| Programming reaches 100% then prints generic error | May be Programmer false negative | Read configuration status separately; require `0x00022230` |
| Status is not `0x00022230` | Configuration/framing failure | Device selection, template, CRC/checksum, READY/DONE fuses |
| Status is correct but nothing changes | User logic, clock, or routing failure | JTAG mailbox, then clock counter |
| LED changes only while a button is held | Static GPIO may work; it does not prove a running clock | Run the JTAG clock probe |
| MODE causes a brief color change then old behavior returns | TangCore probably reloaded another image | Reload SRAM and avoid MODE during the functional test |
| `bram_test` does not visibly blink | LED evidence is ambiguous | Run the JTAG BSRAM probe and check for `0xb5a5b3ff` |
| `uart_hello` is silent on the computer | BL616 may not forward the internal UART | Probe the pin or instrument TX through JTAG |
| HDMI monitor shows blue | Often the monitor's no-signal screen or an older image | Prove active FPGA image, PLL lock, and counters |
| HDMI is black with lock and running counters | Serializer/LVDS/pin/TMDS problem | Inspect OSER10, ELVDS, pair order, and encoded symbols |
| Result changes between identical loads | JTAG contention, stale artifact, uninitialized state, or marginal clocking | Hash artifact, stop other processes, power-cycle, repeat |

## 8. Test record template

Keep one record per artifact:

```text
Date/time:
vodoo_2 branch and commit:
vodoo_2 dirty files relevant to test:
Apicula branch and commit:
nextpnr path/version/chipdb source:
Yosys path/version:
Packer command/path:
Build command and exit status:
PnR warnings/unrouted nets/timing:
FS path, size, mtime, SHA-256:
BIN path, size, mtime, SHA-256:
JTAG host, adapter, cable index, device ID:
Programmer output:
Configuration status:
Mailbox result:
Clock-counter result:
BSRAM result:
PLL lock and measured clocks:
HDMI mode and visible result:
DDR training/test result:
Decision: pass / fail / blocked
```

## Related files

- `README.md` — open-flow status and porting order
- `Makefile` — current targets and tool overrides
- `tang_console_138k_open.cst` — board pin mapping
- `hardware/tang-console-138k/program_debug_sram.sh` — volatile Mac JTAG load
- `hardware/tang-console-138k/README.md` — board and SD-card conventions
