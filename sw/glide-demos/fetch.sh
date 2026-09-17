#!/usr/bin/env bash
# Official publisher demos for DOS-era 3dfx / Glide titles.
# Does not fetch full commercial games.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
DEST="$ROOT/archives"
UA="Mozilla/5.0 (compatible; voodoo2-glide-demo-fetch/1.0)"
BASE="https://archive.org/download"

mkdir -p "$DEST"

# id  file  dest-name
# dest-name keeps collisions from TombRaiderDemo vs tomb3dem apart.
DEMOS=(
  "TombRaiderDemo tombdemo.zip tomb-raider-dos-1.zip"
  "TombRaiderDemo tombdem2.zip tomb-raider-dos-2.zip"
  "tomb3dem tomb3dem.zip tomb-raider-3dfx.zip"
  "Screamer2_1020 SCREAMER2.zip screamer-2.zip"
  "ExtremeAssault_1020 xa_demo.exe extreme-assault.exe"
  "CARMASPL splatdem.zip carmageddon-splat-pack.zip"
  "Addemo addemo.exe archimedean-dynasty.exe"
  "tsddemo_3dfx_zip tsddemo_3dfx.zip toshinden-3dfx.zip"
  "nr2demo n2demo.zip nascar-racing-2.zip"
  "GrandTheftAutoDemo gta.zip gta.zip"
  "LordsoftheRealmII_1020 lorddemo.zip lords-of-the-realm-2.zip"
  "tomb-raider-terracide Tomb%20Raider_Terracide_Monster%203D%20Demo%20Disc.7z monster-3d-demo-disc.7z"
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
