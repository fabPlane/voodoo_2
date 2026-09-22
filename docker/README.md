# Containerized toolchains (no host toolchain changes)

Awkward/old toolchains for the Voodoo Glide work run in Docker so the host
stays clean. Docker in the Cloud Agent VM needs the **vfs** storage driver
(overlay2 fails in the nested container):

```sh
echo '{"storage-driver":"vfs"}' | sudo tee /etc/docker/daemon.json
sudo dockerd &            # or: sudo service docker start
```

## `glide32.Dockerfile` — 32-bit (i386) Linux Glide build environment

A native **i386 Debian** image (so everything is 32-bit without host multilib)
for the old Glide stack: our sim `libglide2x`, the 3dfx Glide SDK test programs,
and the OpenGL→Glide path for old Linux Glide games (GLQuake via Mesa‑Glide2).

```sh
sudo docker build -t glide32 -f docker/glide32.Dockerfile docker/

# Build our trace libglide2x + a Glide program and capture a Voodoo trace,
# entirely in-container (host untouched); render it on the host FPGA sim:
sudo docker run --rm -v "$PWD":/workspace -v /tmp/out:/out \
  -w /workspace/ext/SpinalVoodoo glide32 bash -c '
    export CC32=gcc CXX32=g++          # native 32-bit in the i386 image
    make native/trace/build
    G2X=emu/glide/glide2x
    g++ -m32 -O2 -DSST1 -DSIM_BACKEND \
      -I$G2X/sst1/glide/src -I$G2X/sst1/incsrc -I$G2X/sst1/init \
      -I$G2X/../swlibs/fxmisc -I$G2X/../swlibs/newpci/pcilib \
      /workspace/sw/glide-homebrew/apps/hb02_gouraud.c -o /out/app \
      -L$G2X/sst1/lib/sst1 -lglide2x -lm -lstdc++
    LD_LIBRARY_PATH=$G2X/sst1/lib/sst1 SIM_TRACE_FILE=/out/app.bin /out/app 1'

ext/SpinalVoodoo/emu/test/obj_dir/trace_test /tmp/out/app.bin --ref-only -o out
```

Status: **verified** — libglide2x + Glide programs build and capture traces in
this container, rendered on the host FPGA sim. GLQuake specifically also needs
an OpenGL→Glide2 bridge (**Mesa 3.x `linux-glide`**, 2001-era); building that in
this image is the remaining step for full Linux-Glide *games*.

## `rvquake.Dockerfile` — RISC-V bare-metal toolchain

xPack `riscv-none-elf-gcc` (rv32imafdc/ilp32d) + cmake/make, to cross-build the
RISC-V Quake port (`sw/quake-riscv/`, on the console branches) without a host
RISC-V toolchain:

```sh
sudo docker build -t rvquake -f docker/rvquake.Dockerfile docker/
sudo docker run --rm -v "$PWD":/repo -w /repo/sw/quake-riscv rvquake \
  make engine-rv32 CROSS_COMPILE=riscv-none-elf-
# -> build-rv32/quake-sw : ELF 32-bit RISC-V, double-float, entry 0x80000000
```

Status: **verified** — produces the RV32 Quake ELF for the soft core (Andes A25
on DDR; the 32 KiB `BarRv32` core is too small for the full game + pak).
