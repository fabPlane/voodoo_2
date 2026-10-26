#!/bin/bash
# apar.sh TAG ARGS...: asim.sh as its own systemd user unit (survives session restarts).
systemd-run --user --unit=asim-$1 --collect --setenv=PATH="$PATH" $HOME/.cache/asic-est/asim.sh "$@" 2>&1 | tail -1
