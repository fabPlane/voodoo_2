#!/bin/sh
# prepare-mt32emu.sh <munt/mt32emu source dir> <staging dir>
#
# Stages mt32emu's public headers and generates the config.h that upstream's
# CMake would have generated, WITHOUT writing anything into bench/.
#
# Why not just run their CMake? Two reasons, both from port/PORTING.md:
#
#  - section 4.5 says to remove src/FileStream.cpp from the source list,
#    because it is the only file that includes <fstream> and it drags the whole
#    iostream and locale machinery into the link. Upstream's CMakeLists has it
#    unconditionally in libmt32emu_CPP_SOURCES (line 153) and offers no option
#    to drop it, and bench/ is read-only here, so the file list has to be ours.
#  - section 4.2's flags (-fno-exceptions -fno-rtti -fno-threadsafe-statics,
#    freestanding, no PIC) are easier to apply to a list we own than to fight
#    into someone else's cache variables.
#
# The result is exactly the library upstream would build minus two translation
# units, compiled with the flags the port needs. It is still LGPL 2.1 and the
# static-link obligations in README.md still apply.
#
# SPDX-License-Identifier: 0BSD
set -e
SRC="$1"
OUT="$2"
[ -d "$SRC/src" ] || { echo "no mt32emu sources at $SRC"; exit 2; }
mkdir -p "$OUT/include/mt32emu/c_interface" "$OUT/include/mt32emu/sha1"

# Upstream's CMake copies the public headers into the build tree (COPYONLY,
# CMakeLists.txt:297); do the same, plus the private ones, because we compile
# the sources from here.
for h in "$SRC"/src/*.h; do cp -f "$h" "$OUT/include/mt32emu/"; done
cp -f "$SRC"/src/c_interface/*.h "$OUT/include/mt32emu/c_interface/"
cp -f "$SRC"/src/sha1/*.h        "$OUT/include/mt32emu/sha1/"

# config.h, from src/config.h.in and cmake/project_data.cmake.
V_MAJOR=$(sed -n 's/^set(libmt32emu_VERSION_MAJOR \([0-9]*\)).*/\1/p' "$SRC/cmake/project_data.cmake")
V_MINOR=$(sed -n 's/^set(libmt32emu_VERSION_MINOR \([0-9]*\)).*/\1/p' "$SRC/cmake/project_data.cmake")
V_PATCH=$(sed -n 's/^set(libmt32emu_VERSION_PATCH \([0-9]*\)).*/\1/p' "$SRC/cmake/project_data.cmake")

sed -e "s/@libmt32emu_VERSION@/${V_MAJOR}.${V_MINOR}.${V_PATCH}/" \
    -e "s/@libmt32emu_VERSION_MAJOR@/${V_MAJOR}/" \
    -e "s/@libmt32emu_VERSION_MINOR@/${V_MINOR}/" \
    -e "s/@libmt32emu_VERSION_PATCH@/${V_PATCH}/" \
    -e "s/@libmt32emu_EXPORTS_TYPE@/0/" \
    -e "s/@libmt32emu_SHARED_DEFINITION@/\/* static build: MT32EMU_SHARED undefined *\//" \
    -e "s/@libmt32emu_RUNTIME_VERSION_CHECK@/0/" \
    "$SRC/src/config.h.in" > "$OUT/include/mt32emu/config.h"

echo "staged mt32emu ${V_MAJOR}.${V_MINOR}.${V_PATCH} headers in $OUT/include"
