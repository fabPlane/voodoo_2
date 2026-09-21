# Spinal reference state

Every lockstep diff in this tree is run against the SpinalHDL sources in
`ext/SpinalVoodoo` as pinned by this branch's submodule pointer: upstream
`28d1435` plus one commit, `2749df5` on branch `fabplane/console-scanout`
(HDMI scanout integrated into `Core`, `enableHdmiScanout` in `Config`,
framebuffer-subsystem and plane-reader changes, the scanout test, two
sim-harness tweaks). `fpga/` builds against the same state.

That branch is not on GitHub yet. It lives in a local bare repo:

```
/Users/hyper/projects/me/SpinalVoodoo-fabplane.git
```

A checkout whose submodule cannot resolve `2749df5` (a fresh clone, the
main `vodoo_2` checkout, the Linux box) fetches it from there:

```
cd ext/SpinalVoodoo
git remote add fabplane-local /Users/hyper/projects/me/SpinalVoodoo-fabplane.git
git fetch fabplane-local
git checkout 2749df5     # or: git submodule update from the superproject
```

On hyper-ms-lin, rsync the bare repo alongside the tree or fetch over ssh
from the Mac. When a fabplane fork exists on GitHub, push the branch there,
point `.gitmodules` at it, and delete the local bare repo.

`upstream-local-edits.patch` is the same change as that commit, kept as a
human-readable record; the submodule pointer is authoritative.

Upstream tracks its traces with Git LFS and this checkout never fetched the
LFS blobs; push to the local mirror with `--no-verify` (or
`-c lfs.allowincompletepush=true`).
