#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
OUTPUT=/private/tmp/voodoo-debug-uart-monitor

if ! command -v pkg-config >/dev/null 2>&1 || ! pkg-config --exists libftdi1; then
    echo "libftdi is required. On macOS: brew install libftdi pkg-config" >&2
    exit 1
fi

cc -O2 -Wall -Wextra \
    $(pkg-config --cflags libftdi1) \
    "$SCRIPT_DIR/debug_uart_monitor.c" \
    -o "$OUTPUT" \
    $(pkg-config --libs libftdi1)

exec "$OUTPUT" "$@"
