# SpinalVoodoo FPGA ports

Board-specific packages for SpinalVoodoo. The Voodoo core itself is the upstream
[SpinalVoodoo](https://github.com/fayalalebrun/SpinalVoodoo) source, pulled in from
`ext/SpinalVoodoo`; nothing upstream is modified.

| Target | Package | Host | Memory | Video |
|---|---|---|---|---|
| Artix-7 card | `voodoo.artix` | AXI4-Lite (PCIe BAR0 / PCI target) | two AXI4 masters into MIG | parallel RGB → ADV7123 |
| Sipeed Tang Mega 138K Pro Dock | `voodoo.gowin` | Gowin PCIe TLP BAR0 → AXI4-Lite | AXI4 → Gowin DDR3 native UI | parallel RGB → DVI_TX_Top |

`CoreArtix` / `CoreGowin` are the ports of upstream `CoreDe10`. The Gowin board
top (`TangMega138kPro`) adds the TLP BAR target, a 1 MiB BAR window at 0x250
(Gowin BARs max out at 1 MiB), and a 256-bit native DDR3 bridge. Vendor IP is
instantiated in `hw/gowin/src/board_top.v`, not in SpinalHDL.

## Tools

- JDK 17 (fetched by scala-cli from `//> using jvm`), scala-cli, Verilator 5.x.
- SpinalHDL 1.15.0.
- Gowin EDA (`gw_sh`) plus the IP cores in `hw/gowin/ip.md` for a bitstream.

```
make compile      # type-check both ports
make rtl          # Artix + Gowin Verilog
make rtl-gowin    # rtl/gowin/TangMega138kPro.v
make test         # SpinalSim, including the TLP BAR target
make gowin        # Gowin EDA bitstream (after generating IP)
```

Program the dock with the Gowin programmer or `openFPGALoader -c ft2232`.

## Gowin / Tang Mega 138K Pro

Device: `GW5AST-LV138FPG676AES` (`GW5AST-138B`). Cold-plug the PCIe finger;
the Sipeed demo does not hot-plug.

Colour, depth and textures all live in the module DDR3 (the dock has no
HyperRAM): 4 MiB framebuffer at 0, 8 MiB textures at 4 MiB.

Host map: BAR0 is 1 MiB. Write the megabyte index to byte 0x250, then access
the 1 MiB window. 0x254 is the same never-hang status word as DE10/Artix.

HDMI uses the dock DVI TX pins. LiteX found DVI TX dead on some boards and
drove DVI RX instead; if the monitor stays dark, swap the TMDS locations in
`hw/gowin/tang_mega_138k_pro.cst`.

LEDs (active low): heartbeat, PCIe link, DDR calib, host-bridge wedged.

## Notes for the Artix path

- `io.memFb` and `io.memTex` are AXI4 masters without ID signals (one source after the burst
  fixer). Tie the ID inputs to zero in the Vivado block design and route both through an AXI
  interconnect into MIG.
- Every memory burst is aligned and extended to 256 bytes (`CoreArtix.axiBurstLog2`). Correct,
  simple, and wasteful for single-pixel writes; a boundary-splitting bridge is the follow-up.
- `ArtixGenSupport` emits synchronous active-high resets for the whole design, which is what
  MIG and the PCIe block provide.
- Untouched from upstream: `Config`, all of `Core`, the Verilator harness and Glide tests.
