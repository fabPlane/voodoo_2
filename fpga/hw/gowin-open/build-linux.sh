#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REMOTE_HOST=${REMOTE_HOST:-hyper-ms-lin}
REMOTE_DIR=/home/hyper/projects/me/vodoo_2/fpga/hw/gowin-open
TOOLCHAIN=/home/hyper/opt/oss-cad-suite-20260918/bin
TARGET=${1:-all}

ssh -o ConnectTimeout=8 -o BatchMode=yes "$REMOTE_HOST" "mkdir -p '$REMOTE_DIR'"
rsync -a --delete --exclude build/ "$SCRIPT_DIR/" "$REMOTE_HOST:$REMOTE_DIR/"
ssh -o ConnectTimeout=8 -o BatchMode=yes "$REMOTE_HOST" \
    "export PATH='$TOOLCHAIN':\$PATH; make -C '$REMOTE_DIR' '$TARGET'"
mkdir -p "$SCRIPT_DIR/build"
rsync -a "$REMOTE_HOST:$REMOTE_DIR/build/" "$SCRIPT_DIR/build/"
