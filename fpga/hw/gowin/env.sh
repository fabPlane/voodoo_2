# Discover Gowin EDA for the Tang Mega 138K Pro Dock flow.
# Source from the shell, or let the Makefile include it.
#
# Priority: GOWINHOME already set, then Standard (needed for GW5AST-138
# FPG676), then a sibling Education install, then /opt and $HOME/opt.

gowin_env_root() {
  local here
  here="$(CDPATH= cd -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  (CDPATH= cd -- "$here/../../.." && pwd)
}

if [ -z "${GOWINHOME:-}" ]; then
  _repo="$(gowin_env_root)"
  for _d in \
    "${HOME}/gowin/standard-1.9.12.04" \
    "${HOME}/opt/gowin" \
    /opt/gowin \
    "$_repo/../voodoo-benchmark/tools/gowin-1.9.11.03-education"
  do
    if [ -x "$_d/IDE/bin/gw_sh" ]; then
      GOWINHOME="$(CDPATH= cd -- "$_d" && pwd)"
      break
    fi
  done
  unset _d _repo
fi

if [ -n "${GOWINHOME:-}" ]; then
  export GOWINHOME
  export PATH="$GOWINHOME/IDE/bin:$GOWINHOME/Programmer/bin:$PATH"
  export LD_LIBRARY_PATH="$GOWINHOME/IDE/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
  # Gowin's bundled libfreetype is too old for this distro's fontconfig
  # (FT_Done_MM_Var). Same preload as .cursor/install.sh.
  _sys_ft="/usr/lib/x86_64-linux-gnu/libfreetype.so.6"
  _sys_z="/lib/x86_64-linux-gnu/libz.so.1"
  if [ -e "$_sys_ft" ] && [ -e "$_sys_z" ]; then
    export LD_PRELOAD="$_sys_ft:$_sys_z${LD_PRELOAD:+:$LD_PRELOAD}"
  fi
  unset _sys_ft _sys_z
  # gw_sh is a Qt app; keep CLI usable without a desktop session.
  if [ -z "${DISPLAY:-}" ] && [ -z "${WAYLAND_DISPLAY:-}" ]; then
    export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}"
  fi
fi
