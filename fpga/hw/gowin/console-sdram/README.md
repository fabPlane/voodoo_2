# Tang Console 138K SDRAM experiment

This target replaces the Gowin DDR3 PHY in the Console Voodoo build with the
board's 32 MiB, x16 SDR-SDRAM interface used by NESTang.  The normal
`../console` DDR3 target remains unchanged.

The controller presents the same 128-bit native interface expected by
`TangConsole138k`.  Each native command becomes one aligned SDRAM BL8 burst,
so the existing AXI adapter and bundled-data clock crossing are reused.
Only the low 24 physical-word address bits are implemented; the Voodoo
framebuffer and texture windows occupy 12 MiB and fit within that 32 MiB.
SDRAM CKE is explicitly constrained to package pin `M13`, matching the shipped
NESTang placement report.  It must not be left to automatic placement: Gowin
revision C previously placed the unconstrained port on an unrelated special
pin, leaving the physical SDRAM asleep.
The bring-up image uses the proven Gowin `CLKDIV` primitive to derive one
25 MHz clock for both the Voodoo core and SDRAM controller.  The external
SDRAM clock is inverted, giving its inputs half a cycle of setup time.  This
deliberately conservative arrangement also avoids the hand-written PLL that
the physical FPGA rejected during configuration, despite a clean Gowin PnR.

Build from `fpga/`:

```sh
make gowin-console-sdram
```

The build runs Gowin PnR on `hyper-ms-lin` through `../run_pnr.sh` and writes:

```text
impl/pnr/spinalvoodoo_tang_console_138k_sdram.bin
impl/pnr/spinalvoodoo_tang_console_138k_sdram.fs
```

The target intentionally uses device revision C.  Revision B ignores the
distributed-RAM annotations in the generated Voodoo RTL, which expands the
design to roughly 90k FFs and leaves thousands of nets unrouted.  Revision C
produces the same compact FIFO fabric as the existing Console target.

For a volatile hardware test, leave both Console buttons released and run from
the repository root:

```sh
hardware/tang-console-138k/program_debug_sram.sh \
  fpga/hw/gowin/console-sdram/impl/pnr/spinalvoodoo_tang_console_138k_sdram.fs
```

Do not press MODE during the load.  Holding it lets the payload reach 100% but
blocks entry into user mode (status `0x00004000`, USER_CODE zero).  With MODE
released this image loads as USER_CODE `0x000010B0`, status `0x00022230`.
This writes FPGA SRAM only; it does not alter flash or the SD card.

Current build status: synthesis, placement, routing, static timing, and
volatile SRAM configuration pass. HDMI sync and the menu were observed on
hardware. A rendered demo is the remaining end-to-end SDRAM check.
