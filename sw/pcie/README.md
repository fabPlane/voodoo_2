# PCIe host tools (Tang Mega 138K Pro Dock)

Userspace access to the Gowin PCIe BAR0 on the Sipeed Tang Mega 138K Pro Dock.
The FPGA side is `TangMega138kPro` + `PcieTlpBarTarget`: BAR0 is 1 MiB, byte
`0x250` selects which megabyte of the 16 MiB Voodoo map is visible, and
`0x254` is the never-hang bridge status word.

Bring-up IDs are `10ee:0007`. Cold-plug the dock; Gowin PCIe IP does not
hot-plug.

```
make
make test
sudo ./pci-bar list
sudo ./pci-bar status
sudo ./pci-bar peek 0x000
sudo ./pci-trace-replay ../../ext/SpinalVoodoo/traces/logo
```

`pci-bar` peeks and pokes the windowed Voodoo map. `pci-trace-replay` is the
PCIe port of `ext/SpinalVoodoo/tools/de10-trace-replay.cpp`: register / LFB /
texture writes go through BAR0 instead of `/dev/mem`.

## Live video over PCIe DMA (B27+)

The bitstream taps the HDMI scanout, packs it to RGB565 and writes each
frame into host memory with posted MWr TLPs (`FrameDmaWriter`, control
registers in BAR0 window 31). The host needs a DMA buffer below 4 GiB:

```bash
make -C voodoo_dma && sudo insmod voodoo_dma/voodoo_dma.ko   # /dev/voodoo_dma
make voodoo-stream
# start streaming before the game (it programs window 31 once, then only
# reads host memory), then watch http://<host>:8090/ and record an MP4:
sudo ./voodoo-stream | ./voodoo-view.py --record live.mp4
sudo ./voodoo-stream --counters   # frames / overflows / resyncs / TLPs
sudo ./voodoo-stream --stop       # when nothing else is using the BAR
```
