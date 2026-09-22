# Containerized toolchains (keep the host clean)

The Voodoo/Glide work needs some awkward or ancient toolchains (native 32-bit
i386, a 2001-era Mesa, a bare-metal RISC-V cross compiler). Rather than mutate
the host, they live in Docker images built from the Dockerfiles here. Only the
final trace *render* (`trace_test`) runs on the host.

| Image | Dockerfile | Purpose |
|---|---|---|
| `glide32` | `glide32.Dockerfile` | native i386 Linux Glide: build our `libglide2x`, the Glide SDK tests, and the OpenGL→Glide2 stack (Mesa 3.4.2 + GLQuake) |
| `rvquake` | `rvquake.Dockerfile` | RISC-V bare-metal cross toolchain (xPack `riscv-none-elf-gcc`) for the RISC-V Quake port |

## Scripts in this directory

| Script | What it does |
|---|---|
| `setup-docker.sh` | Configure + start the Docker daemon for the nested VM (forces the **vfs** storage driver). Idempotent; run it first. |
| `build-images.sh` | Build the images (`all` \| `glide32` \| `rvquake`). Calls `setup-docker.sh` first. |
| `glide-trace.sh` | Smoke test: compile a Glide program in `glide32`, capture its Voodoo trace, and render it on the host reference model. |

## 0. One-time daemon setup (nested VM)

Docker in the Cloud Agent VM must use the **vfs** storage driver — the default
`overlay2` fails with `invalid argument` inside the nested container.

```sh
./docker/setup-docker.sh
# -> [setup-docker] Docker <ver> ready (storage-driver=vfs)
```

<details><summary>What it does by hand</summary>

```sh
echo '{"storage-driver":"vfs"}' | sudo tee /etc/docker/daemon.json
sudo service docker restart   # or: sudo dockerd &
```
</details>

## 1. Build the images

```sh
./docker/build-images.sh            # both
./docker/build-images.sh glide32    # just the i386 Glide image
./docker/build-images.sh rvquake    # just the RISC-V image
```

## 2. `glide32` — 32-bit (i386) Linux Glide

A native **i386 Debian** image, so everything is 32-bit without host multilib.
Used to build our sim `libglide2x`, the 3dfx Glide SDK test programs, and the
OpenGL→Glide2 path for real Linux Glide games (GLQuake via Mesa-Glide2).

### Quick smoke test (build a Glide app + trace + render)

```sh
./docker/glide-trace.sh sw/glide-homebrew/apps/hb02_gouraud.c 1
# -> docker/out/hb02_gouraud.bin  (Voodoo command trace, captured in-container)
# -> docker/out/hb02_gouraud_ref.png  (rendered on the host reference model)
```

`glide-trace.sh` runs, in the container: `make native/trace/build` (our
trace-capture `libglide2x`), compiles the app 32-bit against it, and runs it with
`SIM_TRACE_FILE` set to record every Glide call. Then, on the host, it replays
the trace through `ext/SpinalVoodoo/emu/test/obj_dir/trace_test` (the 86Box
software Voodoo reference model). If `trace_test` isn't built yet, the script
prints how to build it (`bash sw/glide-homebrew/render/render.sh`).

### Doing it by hand

```sh
sudo docker run --rm -v "$PWD":/workspace -w /workspace/ext/SpinalVoodoo glide32 bash -c '
  make native/trace/build
  G2X=emu/glide/glide2x/sst1; SW=emu/glide/swlibs
  g++ -m32 -O2 -DSST1 -DSIM_BACKEND \
    -I$G2X/glide/src -I$G2X/incsrc -I$G2X/init -I$SW/fxmisc -I$SW/newpci/pcilib \
    /workspace/sw/glide-homebrew/apps/hb02_gouraud.c -o /workspace/docker/out/app \
    -L$G2X/lib/sst1 -lglide2x -lm -lstdc++
  LD_LIBRARY_PATH=$G2X/lib/sst1 SIM_TRACE_FILE=/workspace/docker/out/app.bin \
    /workspace/docker/out/app 1'
ext/SpinalVoodoo/emu/test/obj_dir/trace_test docker/out/app.bin --ref-only --output-dir docker/out
```

### Full Linux-Glide game: GLQuake

The complete OpenGL→Glide2→Voodoo path for a real game (GLQuake via Mesa 3.4.2)
is scripted in [`sw/quake-glide/`](../sw/quake-glide/README.md). It uses this
same `glide32` image:

```sh
# fetches + builds Mesa 3.4.2 + id GLQuake in the container, runs headless,
# captures a Glide trace, and renders a real Quake frame on the host:
sw/quake-glide/build.sh
```

Other consumers of this image: the homebrew demos
(`make -C sw/glide-homebrew`) and the open-source torus
(`make -C sw/glide-demos/nostalgic`) build the same way against `libglide2x`.

Status: **verified** — `libglide2x`, Glide SDK/homebrew programs, and GLQuake
(Mesa 3.4.2 `fx`/Glide2) all build and trace in this container and render on the
host FPGA sim.

## 3. `rvquake` — RISC-V bare-metal toolchain

xPack `riscv-none-elf-gcc` (full multilib incl. `rv32imafdc/ilp32d`) +
cmake/make, to cross-build the RISC-V Quake port without a host RISC-V toolchain.

```sh
sudo docker run --rm -v "$PWD":/repo -w /repo/sw/quake-riscv rvquake \
  make engine-rv32 CROSS_COMPILE=riscv-none-elf-
# -> build-rv32/quake-sw : ELF 32-bit RISC-V, double-float, entry 0x80000000
```

Status: **verified** — produces the RV32 Quake ELF for the soft core (Andes A25
on DDR; the 32 KiB `BarRv32` core is too small for the full game + pak).

## Notes

- All images mount the repo read-write (`-v "$PWD":/workspace` or `/repo`);
  build outputs land under the repo (e.g. `docker/out/`, gitignored).
- Rebuilds are cached; `build-images.sh` is safe to re-run.
- `trace_test` is a host binary built from `ext/SpinalVoodoo/emu/test`
  (`sw/glide-homebrew/render/render.sh` builds it and the 86Box submodule).
