# Spinal reference state

Every lockstep diff in this tree is run against the SpinalHDL sources in
`ext/SpinalVoodoo` **as checked out in this repo's working tree**, which is
the pinned submodule commit (`28d1435`) plus the local, uncommitted edits in
`upstream-local-edits.patch` (HDMI scanout integrated into `Core`,
`enableHdmiScanout` in `Config`, framebuffer-subsystem and plane-reader
changes, the scanout test, and two sim-harness tweaks). `fpga/` builds
against the same state.

A fresh clone or a new git worktree gets the pristine submodule and
`GenOne` will not even compile (`enableHdmiScanout` missing). Apply the
patch first:

```
cd ext/SpinalVoodoo && git apply ../../amaranth/spinal-ref/upstream-local-edits.patch
```

Regenerate the patch whenever the submodule working tree changes:

```
cd ext/SpinalVoodoo && git diff > ../../amaranth/spinal-ref/upstream-local-edits.patch \
  && git diff --no-index /dev/null test/FramebufferPlaneReaderScanoutTest.scala \
     >> ../../amaranth/spinal-ref/upstream-local-edits.patch
```

Long term these edits belong on a fork branch of SpinalVoodoo with the
submodule pinned to it; until then this patch is the reference.
