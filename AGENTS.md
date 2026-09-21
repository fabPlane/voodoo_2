# Agent notes: Mac vs Linux FPGA builds

This repo is edited on a Mac. **Gowin PnR always runs on hyper-ms-lin**
unless that box is down or already in `gw_sh`. Then, and only then, use
the Mac. `make gowin` / `make gowin-console` from `fpga/` do this via
`hw/gowin/run_pnr.sh` (RTL still generates on whichever machine you invoked
make). Do not start a second `gw_sh` on a box that already has one.

## Machines

| | Mac (this checkout) | Linux (`hyper-ms-lin`) |
|---|---|---|
| CPU | Apple M2 Max, 12 cores | AMD Ryzen 9 9955HX, 16c/32t |
| RAM | 96 GB | 91 GB |
| OS | macOS | Ubuntu 24.04 (`hyper-MS-lin`) |
| Tree | `/Users/hyper/projects/me/vodoo_2` | `/home/hyper/projects/me/vodoo_2` |
| Gowin | `/Applications/GowinIDE.app/.../Gowin_EDA` 1.9.11.03 Education | `/home/hyper/opt/gowin` 1.9.11.03 Education |
| `gw_sh` | not on default PATH; Makefile sets it | `/usr/local/bin/gw_sh` |

Home disk on Linux is 1.7 TB. Root (`/`) is a small ~51 GB NVMe slice and
is often tight. Keep trees, Gowin, and logs under `/home/hyper`.

## When to use which

| Job | Where | Why |
|---|---|---|
| `cd fpga && make compile` / `rtl-console` / `test` | Mac is fine | Mostly serial JVM. M2 Max beat the 9955HX on these. |
| CoreSim Verilator C++ rebuild | Mac unless it is busy | Same story: not a 32-thread win. Needs Verilator **5.052** (`--decorations none`). |
| `make gowin` / `make gowin-console` | **Linux PnR, always** | `hw/gowin/run_pnr.sh` SSHes to hyper-ms-lin, runs `gw_sh`, copies `impl/` back. ~2× vs Mac (30m 52s vs ~57 min, 2026-09-18). |
| Linux already in `gw_sh build*.tcl` | **Mac fallback** | Script prints that and runs local `gw_sh`. If the Mac is also in PnR, it exits instead of stacking a third. |
| Linux SSH timeout (box off) | **Mac fallback** | Same script. Tell the user the box was down. |
| Force Mac anyway | `GOWIN_PNR_FORCE_LOCAL=1 make gowin-console` | Debug only. |
| KiCad, docs, small edits | Mac | No reason to remote. |

Measured Gowin console bitstream (same `build_console.tcl`, Education 1.9.11.03):

| Stage | Mac | Linux |
|---|---|---|
| Synthesis | 4m 48s | 4m 14s |
| Placement | 44m 22s | 17m 18s |
| Routing | 7m 35s | 8m 33s |
| Full `gw_sh` wall | ~57 min | **30m 52s** |

## Is Linux (or the Mac) already in PnR?

```
# Linux
ssh hyper-ms-lin 'pgrep -af "[/]gw_sh build" || echo idle'

# Mac leftovers / a fallback run
pgrep -lf 'gw_sh build' || true
```

A live console PnR writes:

`fpga/hw/gowin/console/impl/pnr/spinalvoodoo_tang_console_138k.log`

On Linux that path is under `/home/hyper/projects/me/vodoo_2/`. Tail it.
Placement phase 2 and routing phase 1 sit at ~100% CPU for a long time.
That is normal. Do not kill it unless the user says so. Do not start
another `gw_sh build*.tcl` on the same box.

This Mac sometimes has leftover `gw_sh generate_ip.tcl` / `gw_sh -h`
processes from earlier IDE runs. Those are not PnR. `run_pnr.sh` ignores
them.

## Reach the Linux box

SSH config on the Mac already has both paths. Prefer LAN on the home net.

```
ssh -o ConnectTimeout=8 -o BatchMode=yes hyper-ms-lin 'echo ok; hostname; uptime'
```

| Host alias | Address | Use |
|---|---|---|
| `hyper-ms-lin` | `10.197.1.179` | default (LAN, faster) |
| `hyper-ms-lin-ts` | `hyper-ms-lin.tail4f6a7.ts.net` | off-LAN / Tailscale |

User `hyper`, key `~/.ssh/id_rsa`. Non-interactive SSH does **not** load
`~/.bashrc`, so `GOWINHOME` may be empty. That is fine: the wrappers under
`/usr/local/bin` set it. Use `bash -lc '…'` only if you need interactive
PATH extras.

If SSH times out on both aliases, the box is probably powered off. The desk
KVM is `glkvm` (`10.197.1.132`). Do not assume Wake-on-LAN works. Tell the
user the machine is down.

## Sync Mac → Linux

**Amaranth rewrite (the `amaranth/rewrite` branch, its own checkout at
`/Users/hyper/projects/me/vodoo_2-amaranth`, origin the same GitHub repo) must never rsync into
`/home/hyper/projects/me/vodoo_2`.** That directory is the Gowin PnR tree
of other sessions; an `rsync --delete` from the rewrite tree overwrote it
mid-PnR on 2026-09-21. The rewrite syncs to its own directory:

```
rsync -aH --delete <the excludes below> \
  /Users/hyper/projects/me/vodoo_2-amaranth/ \
  hyper-ms-lin:/home/hyper/projects/me/vodoo_2-amaranth/
```

Nothing in Phases 0–7 needs Linux except the `-m32` Glide link
(`amaranth/Makefile` `sim-build`/`sim-run`) and the trace-check gate; Gowin
PnR for the Amaranth console (Phase 8) also goes under `vodoo_2-amaranth`.


Do not rsync Scala/Gowin build products. A Mac `.scala-build` carries
Coursier paths that break scala-cli on Linux.

```
rsync -aH --delete \
  --exclude '.DS_Store' \
  --exclude 'fpga/.scala-build' \
  --exclude 'fpga/.bsp' \
  --exclude 'fpga/rtl' \
  --exclude 'fpga/simWorkspace' \
  --exclude 'fpga/hw/gowin/impl' \
  --exclude 'fpga/hw/gowin/console/impl' \
  --exclude 'fpga/test/voodoo/gowin/.scala-build' \
  --exclude 'fpga/test/voodoo/gowin/.bsp' \
  --exclude 'ext/SpinalVoodoo/.scala-build' \
  --exclude 'ext/SpinalVoodoo/.bsp' \
  --exclude 'sw/quake-riscv/build' \
  --exclude 'sw/quake-riscv/build-rv32' \
  --exclude 'sw/quake-riscv/.toolchains' \
  --exclude 'sw/quake-riscv/.cache' \
  --exclude 'hardware/tang-console-138k/sd-card-backup' \
  --exclude 'outputs' \
  --exclude '__pycache__' \
  --exclude 'amaranth/.venv' \
  --exclude 'amaranth/rtl' \
  --exclude 'amaranth/emu/sim/obj_dir' \
  /Users/hyper/projects/me/vodoo_2/ \
  hyper-ms-lin:/home/hyper/projects/me/vodoo_2/
```

The three `amaranth/` excludes are the same story as `.scala-build`: `.venv`
holds Mac wheels and a Mac interpreter, and `rtl/` plus `emu/sim/obj_dir/` are
generated (`make rtl-core-sim` / `make sim-model` rebuild them in ~35 s each).

`--delete` is OK for those excludes (they are not deleted on the destination
unless they also exist as source files). After a sync that accidentally
copied `.scala-build`, delete the remote copies:

```
ssh hyper-ms-lin 'rm -rf ~/projects/me/vodoo_2/fpga/.scala-build ~/projects/me/vodoo_2/fpga/.bsp ~/projects/me/vodoo_2/ext/SpinalVoodoo/.scala-build ~/projects/me/vodoo_2/ext/SpinalVoodoo/.bsp'
```

IP Verilog under `fpga/hw/gowin/ip/` is gitignored but **must** exist on
Linux for `gw_sh`. The rsync above copies it. Do not exclude `fpga/hw/gowin/ip`.

## Toolchain on Linux (already installed)

| Tool | Location | Notes |
|---|---|---|
| JDK 17 | scala-cli pulls Temurin 17 from `fpga/project.scala` | Default `java` on PATH is Ubuntu 25. Do not use that for Spinal. `/usr/lib/jvm/java-17-openjdk-amd64` is also there if you need `JAVA_HOME`. |
| scala-cli | `/usr/local/bin/scala-cli` | 1.17.x |
| Verilator | `/usr/local/bin/verilator` | **5.052**. Apt’s 5.020 rejects `--decorations none`. |
| Gowin EDA | `/home/hyper/opt/gowin` | Education 1.9.11.03, no license server. |
| `gw_sh` / `GowinModGen` | `/usr/local/bin/…` → `$GOWINHOME/IDE/bin/…` | Wrappers set `GOWINHOME`, `QT_QPA_PLATFORM=offscreen`, and **unset `LD_LIBRARY_PATH`**. |
| 32-bit dev libs | `zlib1g-dev:i386`, `libx11-dev:i386`, `libxext-dev:i386` | Added 2026-09-20 along with the dpkg `i386` foreign architecture. Glide2x is 32-bit, so `emu/sim` and the whole Glide stack build `-m32` (`CC32`/`CXX32`); without these three, `libglide2x.so` fails to link with `cannot find -lz / -lX11 / -lXext` and **no** Glide test binary can be built — upstream's own `make native/sim/build` included. Upstream gets them from its nix devenv (`pkgs.pkgsi686Linux.*`); nix is not installed here, so they are apt packages instead. `gcc-multilib`/`g++-multilib` were already present. |

Gowin’s bundled `libfreetype.so.6` was renamed to `libfreetype.so.6.gowin`
so it cannot shadow the system library (fontconfig `FT_Done_MM_Var` crash).
Do not put `$GOWINHOME/IDE/lib` on `LD_LIBRARY_PATH`. Do not restore that
`.so`. The Mac Makefile still exports `DYLD_*`; that is Darwin-only and
harmless on Linux.

Smoke test:

```
ssh hyper-ms-lin 'gw_sh <<EOF
puts gowin-ok
exit
EOF'
```

## Console bitstream

From the Mac (normal path):

```
cd fpga
make gowin-console
```

That generates RTL here, annotates FIFOs, rsyncs Verilog + `hw/gowin/console`
+ `hw/gowin/ip` to Linux, runs `gw_sh build_console.tcl` there, and copies
`impl/` back. Expect ~30 minutes of SSH staying open (ServerAlive is set).
Do not background it unless the user wants that; make should hold the
session.

`generate_ip.sh` is **not** part of this path. It copies DDR3/PLL files from
`/tmp/ddr3_framebuffer_gowin/`, which only exists on the Mac that originally
generated that IP. The copy lives in `fpga/hw/gowin/ip/`. `run_pnr.sh`
skips the script when those files are present.

`force_distributed_fifo_ram.py` is **not** optional. Without it, synth
succeeds and PnR dies with `ERROR (PA2017)` (BSRAM 369 > 340). The Makefile
runs it before `run_pnr.sh`.

If you are already on Linux (`ssh hyper-ms-lin` then make), `run_pnr.sh`
runs `gw_sh` locally. Same FIFO script, still skip `generate_ip.sh`.

Success is `Bitstream generation completed` and
`fpga/hw/gowin/console/impl/pnr/spinalvoodoo_tang_console_138k.bin` (also
copied back to the Mac). TangCore filename on the card is
`cores/console138k/voodootang.bin`. See `hardware/tang-console-138k/README.md`.

## Scala / Verilator on Linux (only if the Mac cannot)

```
ssh hyper-ms-lin 'bash -s' << 'EOF'
set -euo pipefail
export PATH="/usr/local/bin:/usr/lib/jvm/java-17-openjdk-amd64/bin:$PATH"
cd /home/hyper/projects/me/vodoo_2/fpga
make compile
make test
make rtl-console
EOF
```

CoreSim:

```
ssh hyper-ms-lin 'export PATH=/usr/local/bin:$PATH; make -C ~/projects/me/vodoo_2/ext/SpinalVoodoo/emu/test'
```

Expect these to be similar to the Mac, not faster. Offload them when the Mac
is pegged, not because Linux has more threads.

## Gowin wrapper rules (already burned once)

- `/usr/local/bin/gw_sh` must `exec` `$GOWINHOME/IDE/bin/gw_sh` by **absolute
  path**. Never install a wrapper *over* `IDE/bin/gw_sh` (that recurses until
  SIGTERM / exit 143).
- Linux binary is `GowinModGen`. Some scripts call `GowinModgen`. Both names
  exist as wrappers plus an `IDE/bin` symlink.
- Headless Qt needs `QT_QPA_PLATFORM=offscreen` (wrappers set this).

## If something looks hung

| Symptom | Likely cause |
|---|---|
| `gw_sh` immediately `PA2017` BSRAM | Forgot `force_distributed_fifo_ram.py` after regenerating RTL |
| `generate_ip.sh` missing `/tmp/ddr3_framebuffer_gowin` | Expected on Linux; use existing `fpga/hw/gowin/ip/` |
| `FT_Done_MM_Var` / fontconfig abort | `LD_LIBRARY_PATH` points at Gowin `IDE/lib` |
| `gw_sh` exit 143, CPU idle, wrapper looping | Recursive wrapper on `IDE/bin/gw_sh`; restore the ELF from the 1.9.11.03 tarball |
| Apt Verilator `unknown option --decorations` | PATH is `/usr/bin/verilator` 5.020; use `/usr/local/bin/verilator` 5.052 |
| scala-cli talks about `/Users/hyper/Library/Caches/Coursier` | Leftover Mac `.scala-build`; delete it on Linux |
| SSH timeout | Box is off; do not sit in a retry loop |

PnR at 100% CPU for 15–45 minutes with log lines stuck between placement
phases is normal, not hung.
