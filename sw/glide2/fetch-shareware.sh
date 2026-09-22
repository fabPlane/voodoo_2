#!/usr/bin/env bash
# Download three freely distributed Windows shareware/demo zips.
# These are publisher demos from archive.org, not registered games.
# Nothing here is committed; archives/ is gitignored.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
DEST="$ROOT/archives"
UA="Mozilla/5.0 (compatible; voodoo2-glide-demo-fetch/1.0)"
BASE="https://archive.org/download"

mkdir -p "$DEST"

# identifier  remote-file  local-name
# HexenII_1020 is Heroes of Might and Magic II, not the Raven game, so it
# is not fetched.
DEMOS=(
  "QuakeII_1020 q2_test.zip quake2-demo.zip"
  "glquake glquake.exe glquake.exe"
  "msdos_Quake106_shareware_201702 msdos_Quake106_shareware.zip quake106-shareware.zip"
  "MDK_1020 MDK.zip mdk-demo.zip"
)

fetch_one() {
  local id="$1" file="$2" out="$3"
  local url="$BASE/$id/$file"
  local path="$DEST/$out"
  if [[ -f "$path" && -s "$path" ]]; then
    echo "have $out"
    return 0
  fi
  echo "get  $out"
  curl -L --fail --retry 3 --retry-delay 2 \
    -A "$UA" \
    --progress-bar \
    -o "$path.part" \
    "$url"
  mv "$path.part" "$path"
}

for row in "${DEMOS[@]}"; do
  # shellcheck disable=SC2086
  set -- $row
  fetch_one "$1" "$2" "$3"
done

echo
echo "archives in $DEST"
ls -lh "$DEST"
