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
