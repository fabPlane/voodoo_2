#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
APP=/private/tmp/voodoo-tang-debug-console
MODULE_CACHE=/private/tmp/voodoo-swift-module-cache

swiftc -module-cache-path "$MODULE_CACHE" -framework AppKit \
    "$SCRIPT_DIR/debug_console_gui.swift" -o "$APP"
exec "$APP" --monitor "$SCRIPT_DIR/run_debug_uart_monitor.sh"
