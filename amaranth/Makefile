# Amaranth port of SpinalVoodoo. See README.md and ../docs/amaranth-rewrite-plan.md.
#
# Everything runs inside the uv-managed venv in .venv; `uv run` creates/syncs it
# on demand, so `make test` works from a clean checkout.

UV ?= $(shell command -v uv 2>/dev/null || echo $(HOME)/.local/bin/uv)
RUN := $(UV) run --project $(CURDIR)
RTL_DIR := $(CURDIR)/rtl

# --- out-of-tree Glide/Verilator harness (plan Phase 0.5, doc §3) ----------
# The SpinalVoodoo submodule is read-only, so the Amaranth build drives its
# Makefiles with command-line overrides (which beat in-makefile `=`
# assignments and propagate to sub-makes through MAKEFLAGS).
UPSTREAM := $(abspath $(CURDIR)/../ext/SpinalVoodoo)
AMSIM    := $(CURDIR)/emu/sim
GLIDE_TESTS := emu/glide/glide2x/sst1/glide/tests
TEST ?= test00

# SIM_RTL is only used for the `output/<test>/CoreSim.v` convenience symlink,
# which upstream builds as `../../$(SIM_RTL)` from ext/SpinalVoodoo — hence a
# path relative to the submodule root rather than an absolute one.
SIM_OVERRIDES := \
	SIM_DIR=$(AMSIM) \
	SIM_MODEL=CoreSim \
	SIM_STAMP=$(AMSIM)/obj_dir/VCoreSim__ALL.a \
	SIM_RTL=../../amaranth/rtl/CoreSim.v \
	SCALA_SRCS=

.PHONY: help venv test rtl-module rtl-core-sim rtl-console diff diff-list gowin-console clean \
        sim-model sim-build sim-run sim-clean

help:
	@echo "venv           uv sync (create/refresh .venv)"
	@echo "test           pytest"
	@echo "rtl-module MOD=Counter   elaborate one module -> rtl/<MOD>.v"
	@echo "rtl-core-sim   CoreSim.v for the Verilator Glide harness (stub until Phase 7.2)"
	@echo "sim-model      verilate CoreSim.v + build the harness objects (Linux, -m32)"
	@echo "sim-build TEST=test00   link one Glide test against the Amaranth CoreSim"
	@echo "sim-run   TEST=test00   run it and convert the screenshot to PNG"
	@echo "rtl-console    (Phase 8) TangConsole138k.v for the Gowin build"
	@echo "diff MOD=X     Spinal-vs-Amaranth lockstep test (>=1e6 cycles x 3 seeds)"
	@echo "diff-list      modules the diff harness knows about"
	@echo "gowin-console  (Phase 8) PnR on hyper-ms-lin"
	@echo "rtl-spike      Phase 0.3 Gowin compatibility spike Verilog"

venv:
	$(UV) sync --project $(CURDIR)

test: venv
	$(RUN) pytest

rtl-module: venv
	@test -n "$(MOD)" || { echo "usage: make rtl-module MOD=<Name>"; exit 2; }
	$(RUN) python -m gen.gen_module $(MOD) -o $(RTL_DIR) $(PARAMS)

rtl-spike: venv
	$(RUN) python -m gen.gen_module SpikeBlinky -o $(RTL_DIR)

# --- Glide / Verilator system harness (plan Phase 0.5) ----------------------
#
# Only `rtl-core-sim` runs on the Mac.  Everything below is -m32 and so
# Linux-only (AGENTS.md): rsync the tree to hyper-ms-lin and run it there.

rtl-core-sim: venv
	$(RUN) python -m gen.gen_core_sim -o $(RTL_DIR)

sim-model: rtl-core-sim
	$(MAKE) -C $(AMSIM) all

# Builds libglide2x.so against amaranth/emu/sim and links one test binary.
# Goes through the upstream file-based chain rather than `native/sim/build/%`
# so the Glide objects are not `clean`ed on every invocation.
sim-build: sim-model
	$(MAKE) -C $(UPSTREAM) $(GLIDE_TESTS)/$(TEST).exe $(SIM_OVERRIDES)

sim-run: sim-build
	$(MAKE) -C $(UPSTREAM) native/sim/run/$(TEST) $(SIM_OVERRIDES)

sim-clean:
	$(MAKE) -C $(AMSIM) clean

# --- Spinal-vs-Amaranth lockstep diff (plan §4.2 / task 0.4) ---------------
#
# `make diff MOD=X` is the "a module is done" gate: the plan requires >=1e6
# cycles across >=3 seeds.  `make test` runs the same harness at 20k cycles
# through tests/diff/, which is enough to catch a broken port in seconds.

DIFF_SEEDS  ?= 1,2,3
DIFF_CYCLES ?= 1000000
DIFF_ARGS   ?=

diff: venv
	@test -n "$(MOD)" || { echo "usage: make diff MOD=<Name>   (make diff-list)"; exit 2; }
	$(RUN) python -m tools.diff.harness $(MOD) \
		--seeds $(DIFF_SEEDS) --cycles $(DIFF_CYCLES) $(DIFF_ARGS)

diff-list: venv
	$(RUN) python -m tools.diff.harness --list

# --- placeholders; filled in by the phases named above ----------------------

rtl-console:
	@echo "not implemented yet (plan Phase 8.3: gen/gen_tang_console.py -> rtl/TangConsole138k.v)"; exit 1

gowin-console:
	@echo "not implemented yet (plan Phase 8.4: hw/console + gw_sh on hyper-ms-lin)"; exit 1

clean:
	rm -rf $(RTL_DIR) .pytest_cache build
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
