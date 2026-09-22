#!/usr/bin/env bash
#
# Build the containerized toolchains for the Voodoo/Glide work. These keep the
# awkward/old toolchains off the host: `glide32` (32-bit i386 Linux Glide +
# Mesa/GLQuake) and `rvquake` (RISC-V bare-metal cross toolchain).
#
#   ./docker/build-images.sh            # build both
#   ./docker/build-images.sh glide32    # just the i386 Glide image
#   ./docker/build-images.sh rvquake    # just the RISC-V image
#
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUDO="${SUDO:-sudo}"

# Ensure the daemon is configured/running (vfs storage driver).
"$HERE/setup-docker.sh"

build() { # <tag> <dockerfile>
  echo "[build-images] building $1 from $2 ..."
  $SUDO docker build -t "$1" -f "$HERE/$2" "$HERE"
}

case "${1:-all}" in
  glide32) build glide32:latest glide32.Dockerfile ;;
  rvquake) build rvquake:latest rvquake.Dockerfile ;;
  all)
    build glide32:latest glide32.Dockerfile
    build rvquake:latest rvquake.Dockerfile
    ;;
  *) echo "usage: $0 [all|glide32|rvquake]" >&2; exit 2 ;;
esac

echo "[build-images] images:"
$SUDO docker images | grep -E 'REPOSITORY|glide32|rvquake' || $SUDO docker images
