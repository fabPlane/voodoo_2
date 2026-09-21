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
        sim-model sim-build sim-run sim-trace-check sim-clean

help:
	@echo "venv           uv sync (create/refresh .venv)"
	@echo "test           pytest"
	@echo "rtl-module MOD=Counter   elaborate one module -> rtl/<MOD>.v"
	@echo "rtl-core-sim   CoreSim.v for the Verilator Glide harness (stub until Phase 7.2)"
	@echo "sim-model      verilate CoreSim.v + build the harness objects (Linux, -m32)"
	@echo "sim-build TEST=test00   link one Glide test against the Amaranth CoreSim"
	@echo "sim-run   TEST=test00   run it and convert the screenshot to PNG"
	@echo "sim-trace-check TEST=test04   trace replay vs the 86Box ref model (64-bit)"
	@echo "rtl-console    (Phase 8) TangConsole138k.v for the Gowin build"
	@echo "diff MOD=X     Spinal-vs-Amaranth lockstep test (>=1e6 cycles x 3 seeds)"
	@echo "diff-list      modules the diff harness knows about"
	@echo "gowin-console  (Phase 8) PnR on hyper-ms-lin"
	@echo "rtl-spike      Phase 0.3 Gowin compatibility spike Verilog"
	@echo "hdmi-demo      HDMI test-pattern bitstream: gen + lint + sim, PnR on hyper-ms-lin (hw/hdmi_demo)"

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

# The screenshot/cycle parity gate (plan 7.3) does NOT go through the Glide
# stack: `native/sim/check/%` runs ext/SpinalVoodoo/emu/test/trace_test, which
# is a separate **64-bit** build replaying a captured trace against the 86Box
# reference model. It reads the harness and the RTL out of plain `=`
# variables, so it takes the Amaranth copy with three overrides and needs no
# 32-bit libraries at all. Only trace *capture* and live Glide runs need -m32.
AMTESTOBJ := $(CURDIR)/emu/test/obj_dir

# `-o $(RTL_DIR)/CoreSim.v` is load-bearing. emu/test/Makefile has its own
#     $(VERILOG): $(SCALA_SRCS) $(CORE_SIM_GEN_ARGS_FILE) $(SIM_INTERFACE_FILE)
# rule that runs `voodoo.CoreSimGen` — a Scala build inside the read-only
# submodule that regenerates the *Spinal* RTL, for a file it does not even
# write to the overridden path. `SCALA_SRCS=` alone is not enough: the other
# two prerequisites are sentinel files that `$(shell …)` recreates inside
# OBJ_DIR at parse time, so they are always newer than the Amaranth RTL.
# `-o FILE` tells make to treat that target as up to date, full stop.
# This is the same trap that made us copy emu/sim/Makefile instead of
# overriding it (doc §3.2).
sim-trace-check: rtl-core-sim
	$(MAKE) -C $(UPSTREAM)/emu/test -o $(RTL_DIR)/CoreSim.v $(AMTESTOBJ)/trace_test \
		SIM_DIR=$(AMSIM) VERILOG=$(RTL_DIR)/CoreSim.v OBJ_DIR=$(AMTESTOBJ) \
		SCALA_SRCS=
	cd $(UPSTREAM) && $(AMTESTOBJ)/trace_test traces/$(TEST).bin \
		--output-dir $(CURDIR)/test-output/$(TEST)

sim-clean:
	$(MAKE) -C $(AMSIM) clean
	rm -rf $(AMTESTOBJ) $(CURDIR)/test-output

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

# --- HDMI test-pattern demo bitstream (hardware checkpoint, hw/hdmi_demo) ---
#
# Amaranth HdmiTestPatternScanout in a second clock domain + vendor PLL/CLKDIV
# + DVI TX, on the Tang Retro Console 138K. The Verilog goes to
# hw/hdmi_demo/build/ (not rtl/, which the AGENTS.md rsync excludes), is
# linted and simulated here, then hw/hdmi_demo/run_pnr.sh rsyncs the worktree
# to hyper-ms-lin:vodoo_2-amaranth/, refuses if a gw_sh is already running
# there, runs gw_sh, and copies impl/pnr/*.fs + logs back into build/.
# Programming the board is left to the user (program_debug_sram.sh).

HDMI_DEMO_DIR := $(CURDIR)/hw/hdmi_demo
HDMI_DEMO_IP  := $(abspath $(CURDIR)/../fpga/hw/gowin/ip)
HDMI_DEMO_LINT_FLAGS := -Wno-fatal -Wno-WIDTHEXPAND -Wno-WIDTHTRUNC -Wno-DECLFILENAME \
	-Wno-UNUSEDSIGNAL -Wno-TIMESCALEMOD

.PHONY: hdmi-demo hdmi-demo-rtl hdmi-demo-lint hdmi-demo-sim hdmi-demo-pnr

hdmi-demo-rtl: venv
	$(RUN) python -m gen.gen_module avoodoo.gowin.hdmi_demo_top:HdmiDemoTop -o $(HDMI_DEMO_DIR)/build

hdmi-demo-lint: hdmi-demo-rtl
	verilator --lint-only $(HDMI_DEMO_LINT_FLAGS) --top-module HdmiDemoTop \
		$(HDMI_DEMO_DIR)/build/HdmiDemoTop.v
	verilator --lint-only $(HDMI_DEMO_LINT_FLAGS) --top-module hdmi_demo_board_top \
		$(HDMI_DEMO_DIR)/build/HdmiDemoTop.v \
		$(HDMI_DEMO_DIR)/hdmi_demo_board_top.v \
		$(HDMI_DEMO_DIR)/dvi_tx_top.v \
		$(HDMI_DEMO_IP)/Gowin_PLL_Hdmi/Gowin_PLL_Hdmi.v \
		$(HDMI_DEMO_IP)/Gowin_PLL_Hdmi/Gowin_PLL_Hdmi_mod.v \
		$(HDMI_DEMO_IP)/Gowin_PLL_Sys/pll_init.v \
		$(HDMI_DEMO_DIR)/lint/gowin_prims_stub.v

hdmi-demo-sim: venv
	$(RUN) pytest -q tests/test_hdmi_demo_top.py

hdmi-demo-pnr: hdmi-demo-rtl
	$(HDMI_DEMO_DIR)/run_pnr.sh

hdmi-demo: hdmi-demo-lint hdmi-demo-sim hdmi-demo-pnr

# --- placeholders; filled in by the phases named above ----------------------

rtl-console:
	@echo "not implemented yet (plan Phase 8.3: gen/gen_tang_console.py -> rtl/TangConsole138k.v)"; exit 1

gowin-console:
	@echo "not implemented yet (plan Phase 8.4: hw/console + gw_sh on hyper-ms-lin)"; exit 1

clean:
	rm -rf $(RTL_DIR) .pytest_cache build
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
