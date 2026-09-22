#!/bin/bash
# MiniGL's import table asks for the MSVC stdcall names (_grFoo@N).
# MinGW already exports grFoo@N and grFoo. Add the underscored alias too.
set -euo pipefail
echo EXPORTS
# Internal names in a MinGW .def drop the COFF underscore. The left-hand
# @N is kept when an explicit ordinal follows, which is the spelling
# MiniGL imports.
i686-w64-mingw32-nm -g --defined-only "$@" | awk '
  $2 == "T" && $3 ~ /^_(gr|gu)[A-Za-z0-9]*@[0-9]+$/ {
    pe = substr($3, 2)
    ord = 1000 + n
    n++
    print "_" pe " = " pe " @" ord
  }
'
