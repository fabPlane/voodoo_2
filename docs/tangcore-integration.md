# TangCore integration (Sipeed Tang Retro Console 138K)

The target hardware is the **Sipeed Tang Retro Console 138K**
(`GW5AST-LV138PG484AC1/I0`). Its multi-core loader is
[TangCore](https://github.com/nand2mario/tangcore), vendored in this repo as the
submodule **`ext/tangcore`** (pinned commit). This document describes how the
SpinalVoodoo console core plugs into TangCore, and how the homebrew Glide demos
in `sw/glide-homebrew/` reach the screen.

## What TangCore provides

TangCore is an FPGA "gaming distribution": a `monitor` menu core plus a set of
system cores (NES/SNES/GBA/Genesis/SMS/PC-XT), a BL616 companion firmware, and a
USB/SD-drive layout. On the Tang Console the BL616 handles USB (HDMI menu input,
USB gamepads, storage) and loads FPGA core bitstreams on demand.

Relevant pieces for us (paths inside `ext/tangcore`):

- `monitor/` — the on-screen menu bitstream (`cores/<board>/monitor.bin`).
- `firmware-bl616/` — BL616 firmware (USB HID + core loading over UART).
- `build.bat` / `:do_package` — assembles the `build/cores/<board>/` layout,
  e.g. `build/cores/console138k/{monitor,nestang,snestang,...}.bin`.
- `docs/user-guide/installation.md` — the USB-drive `cores/` layout the menu
  scans at boot.

## Where the Voodoo core fits

The SpinalVoodoo console fabric (`fpga/src/voodoo/gowin/TangConsole138k.scala`)
is built into a Gowin bitstream and dropped in alongside the TangCore cores:

```
<usb-drive>/
└── cores/
    └── console138k/
        ├── monitor.bin        # TangCore menu (from ext/tangcore)
        ├── nestang.bin        # TangCore cores (from ext/tangcore) ...
        └── voodootang.bin     # ← SpinalVoodoo console core (this repo)
```

The TangCore `monitor` menu lists `voodootang` as a selectable core and loads it
like any other. This matches the reservation already documented in
`fpga/README.md` (`cores/console138k/voodootang.bin`).

### Controller input

The Voodoo fabric consumes USB gamepad state exactly the way TangCore delivers
it: the BL616 sends HID reports over its UART as command `0x09`, SNES button
layout, into `io.hid1`/`io.hid2` (see `TangConsole138k` and `SnesPad.scala`).
No extra glue is required beyond running the stock TangCore BL616 firmware.

## Building & deploying (requires Gowin EDA)

Bitstream synthesis needs the proprietary Gowin EDA toolchain, which is **not**
part of the Cloud Agent environment, so the steps below run on a workstation
that has `gw_sh`:

1. Build the Voodoo console core:
   `make -C fpga rtl-console && make -C fpga gowin-console`
   (produces the console `.bin`; copy it to `cores/console138k/voodootang.bin`).
2. Build/collect the TangCore cores + firmware from `ext/tangcore`
   (`build.bat all` / `build.bat package` on Windows with Gowin EDA), or use a
   published TangCore release for the console138k board.
3. Flash the BL616 firmware once with Bouffalo Dev Cube (see TangCore's
   `docs/user-guide/installation.md`).
4. Copy the assembled `cores/console138k/` directory (including
   `voodootang.bin`) to the USB drive and boot; pick **voodootang** in the menu.

## Homebrew Glide demos on the console

The demos in `sw/glide-homebrew/` are packed into `addr:data` player ROMs
(`sw/glide-homebrew/roms/*.bin`) in the exact format `voodoo.gowin.DemoTraceRom`
loads. To ship them as selectable items, add `DemoCatalog` entries pointing at
the packed files and rebuild the console core (step 1 above). See
`sw/glide-homebrew/README.md` for the capture/pack pipeline.

## Submodule notes

`ext/tangcore` itself references the individual cores as **nested** submodules
with relative URLs (`../nestang.git`, `../firmware-bl616.git`, ...). Those are
large and only needed to *build* TangCore bitstreams (which requires Gowin EDA),
so the environment initializes only the TangCore superproject. To fetch them
manually:

```
git -C ext/tangcore submodule update --init
```
