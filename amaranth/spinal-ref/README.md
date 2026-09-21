# Spinal reference state

Every lockstep diff in this tree is run against the SpinalHDL sources in
`ext/SpinalVoodoo` as pinned by this branch's submodule pointer. The
submodule tracks the fabPlane fork of upstream:

- repo: https://github.com/fabPlane/SpinalVoodoo (fork of fayalalebrun/SpinalVoodoo)
- branch: `fabplane/console-scanout`
- pinned: `2749df5` = upstream `28d1435` + one commit with the Console
  reference edits (HDMI scanout integrated into `Core`, `enableHdmiScanout`
  in `Config`, framebuffer-subsystem and plane-reader changes, the scanout
  test, two sim-harness tweaks). `fpga/` builds against the same state.

Fresh checkouts and the Linux box get it with the usual
`git submodule update --init ext/SpinalVoodoo`. The main `vodoo_2` checkout
on the Mac still has these edits as uncommitted files in its own submodule
working tree; once that branch merges this one, `git submodule sync` +
`git submodule update` there replaces them with the commit.

Upstream tracks its traces with Git LFS and this checkout never fetched the
LFS blobs; pushing from it needs `--no-verify`
(or `-c lfs.allowincompletepush=true`). Nothing in the rewrite reads those
blobs.

Further edits to the Spinal side go on that branch in the fork, and the
submodule pointer here moves with them; the diff gates are only meaningful
against a committed reference.
