#!/usr/bin/env bash
# Official Quake 1.06 shareware (id Software). Contains id1/pak0.pak and the
# recorded WinQuake demos demo1.dem / demo2.dem / demo3.dem.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CACHE="$ROOT/.cache"
ID1="$ROOT/id1"
ZIP="$CACHE/quake106.zip"
MIRROR="${QUAKE106_URL:-https://ftp.gwdg.de/pub/misc/ftp.idsoftware.com/idstuff/quake/quake106.zip}"
# Quake wiki: shareware / registered 1.06 pak0.pak
PAK_MD5="5906e5998fc3d896ddaf5e6a62e03abb"

mkdir -p "$CACHE" "$ID1"

if [[ ! -f "$ZIP" ]]; then
  echo "downloading Quake shareware 1.06"
  curl -L --fail --retry 3 -o "$ZIP" "$MIRROR"
fi

unzip -o -q "$ZIP" -d "$CACHE/quake106"

RESOURCE="$(find "$CACHE/quake106" -iname 'resource.1' | head -n 1)"
if [[ -z "$RESOURCE" ]]; then
  echo "resource.1 missing from quake106.zip" >&2
  exit 1
fi

extract_lha() {
  local dest="$1"
  mkdir -p "$dest"
  if command -v lha >/dev/null 2>&1; then
    (cd "$dest" && lha xqf "$RESOURCE")
    return
  fi
  if command -v lhasa >/dev/null 2>&1; then
    (cd "$dest" && lhasa xf "$RESOURCE")
    return
  fi
  echo "need lha/lhasa to unpack resource.1 (brew install lhasa)" >&2
  exit 1
}

EXTRACT="$CACHE/resource"
rm -rf "$EXTRACT"
extract_lha "$EXTRACT"

PAK="$(find "$EXTRACT" -iname 'pak0.pak' | head -n 1)"
if [[ -z "$PAK" ]]; then
  echo "pak0.pak missing from resource.1" >&2
  exit 1
fi

GOT="$(md5 -q "$PAK" 2>/dev/null || md5sum "$PAK" | awk '{print $1}')"
if [[ "$GOT" != "$PAK_MD5" ]]; then
  echo "pak0.pak md5 $GOT != $PAK_MD5" >&2
  exit 1
fi

cp "$PAK" "$ID1/pak0.pak"
# Shareware notices that ship with the demo (unique names: macOS/FAT32
# are case-insensitive, so readme.txt would clobber DATA.txt).
copy_named() {
  local src_name="$1"
  local dest_name="$2"
  local f
  f="$(find "$EXTRACT" -iname "$src_name" | head -n 1)"
  if [[ -n "$f" ]]; then
    cp "$f" "$ID1/$dest_name"
  fi
}
copy_named slicnse.txt slicnse.txt
copy_named licinfo.txt licinfo.txt
copy_named readme.txt quake-readme.txt
echo "installed $ID1/pak0.pak"
