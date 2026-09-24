# DOS Glide → FPGA simulator pipeline

Run real DOS Glide2x programs and render their output through the SpinalVoodoo
FPGA simulator. A DOS Glide binary runs inside a **32-bit DOSBox-X** whose
built-in Glide passthrough (`[voodoo] glide=true`) forwards the guest's Glide
commands to this repo's sim `libglide2x` (trace-capture build). The captured
Voodoo command stream is then replayed through the Voodoo core:

```
DOS .exe ──(DOSBox-X 32-bit, glide=true)──▶ sim libglide2x (trace) ──▶ trace .bin
trace .bin ──(ext/SpinalVoodoo trace_test)──▶ PNG   (86Box ref model or Voodoo RTL)
```

`trace_test --ref-only` renders via the 86Box software reference (the golden
model the RTL is verified against); `trace_test --sim-replay-only` renders
through the actual Verilator Voodoo RTL (much slower).

## What works, and what doesn't

The passthrough forwards Glide from programs that use the **3dfx SDK / OVL
(port-0x600) Glide path** — e.g. the 3dfx Glide SDK sample programs. Verified
end-to-end here (rendered through the FPGA sim):

| DOS binary | Scene |
|---|---|
| `df00sdk` / `df01sdk` / `df02sdk` | SDK flow tests (clear, moving triangle, textured quad) |
| `test04` | gouraud-shaded triangle |
| `test16` | red/blue tiled triangle pattern (3dfx splash test) |

Commercial game demos that **statically link an older real-PCI Glide** (e.g. the
freely-distributed *Tomb Raider* 3dfx demo, `tomb3dem`) do **not** forward to the
sim: their Glide probes the PCI/MMIO Voodoo directly instead of the DOSBox-X
passthrough port, so they silently fall back to the software renderer. The full
retail game + official 3dfx patch ships a `glide2x.ovl` that uses the passthrough
path and would forward, but those assets are copyrighted and not included here.
The Tomb Raider 3dfx demo does run (in software) under the 32-bit DOSBox-X built
by this flow.

## Toolchain setup (one-time)

Requires a **32-bit** DOSBox-X (to `dlopen` the 32-bit sim `libglide2x`), Open
Watcom + the 3dfx Glide SDK (to build DOS Glide binaries), and this repo's sim
`libglide2x` in trace mode.

```sh
# 32-bit DOSBox-X: build from source (see build_dosboxx32.sh) or use
#   nix build nixpkgs#pkgsi686Linux.dosbox-x
# Open Watcom (DOS cross-compiler):
curl -fsSLo /tmp/ow.tar.xz \
  https://github.com/open-watcom/open-watcom-v2/releases/download/Current-build/ow-snapshot.tar.xz
mkdir -p /tmp/watcom && tar xf /tmp/ow.tar.xz -C /tmp/watcom
export WATCOM=/tmp/watcom PATH=/tmp/watcom/binl64:$PATH

# 3dfx Glide SDK 2.43 (glide2x.lib, DOS4GW, headers):
python3 ext/SpinalVoodoo/scripts/fetch-3dfx-sdk243.py /tmp/3dfx-sdk/sdk243

# sim libglide2x (trace-capture, 32-bit):
make -C ext/SpinalVoodoo native/trace/build
```

## Capture + render

```sh
# 1. Build DOS Glide SDK binaries (writes *.exe into the tests dir):
make -C ext/SpinalVoodoo/emu/glide/glide2x/sst1/glide/tests -f Makefile.sdkwat df00sdk.exe

# 2. Run in DOSBox-X + capture the Glide trace:
DOSBOXX32_BIN=/path/to/32bit/dosbox-x ./capture_dos_glide.sh \
    /path/to/tests DF00SDK.EXE /tmp/df00.bin

# 3. Render the trace through the FPGA sim:
ext/SpinalVoodoo/emu/test/obj_dir/trace_test /tmp/df00.bin --ref-only --output-dir out
#   ... or --sim-replay-only for the actual Voodoo RTL (slow).
```
