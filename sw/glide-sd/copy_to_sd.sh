#!/usr/bin/env bash
# Copy a glide/ FAT tree onto a mounted TangCore / Console card.
set -euo pipefail
if [[ $# -lt 2 ]]; then
  echo "usage: $0 <mounted-card> <fat-tree-root>" >&2
  exit 2
fi
CARD="$1"
SRC="$2"
if [[ ! -d "$SRC/glide" ]]; then
  echo "$SRC/glide missing (run pack_v2g.py --fat)" >&2
  exit 1
fi
if [[ ! -d "$CARD" ]]; then
  echo "$CARD is not a mounted volume" >&2
  exit 1
fi
mkdir -p "$CARD/glide"
cp -R "$SRC/glide/." "$CARD/glide/"
echo "copied $SRC/glide -> $CARD/glide"
if [[ -d "$CARD/cores/console138k" ]]; then
  echo "TangCore cores present (voodootang.bin stays $CARD/cores/console138k/)"
fi
ls -l "$CARD/glide/PLAY.V2G"
