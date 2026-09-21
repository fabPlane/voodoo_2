# SpinalVoodoo FPGA ports
#
#   make compile     type-check Artix and Gowin ports against the upstream core
#   make rtl         generate Artix board + Gowin Verilog
#   make rtl-artix   generate rtl/artix/AmdAc701.v
#   make rtl-gowin   generate rtl/gowin/TangMega138kPro.v
#   make rtl-console generate rtl/gowin/TangConsole138k.v (Retro Console menu demo)
#   make lint        Verilator lint of generated Verilog
#   make test        SpinalSim tests (needs verilator on PATH); TEST_ARGS='-w voodoo.gowin' for a subset
#   make distclean   clean + drop .scala-build and simWorkspace (cold compile + reverilate everything)
#   make gowin       Gowin dock bitstream (PnR on hyper-ms-lin unless that box is busy)
#   make gowin-console Console bitstream (same: Linux PnR, Mac only as fallback)
#   make gowin-console-sdram Experimental Console bitstream using NESTang SDRAM
#   make artix       Vivado bitstream (needs vivado and generated IP; see hw/artix/ip.md)

SCALA_CLI ?= scala-cli
# Keep Bloop's incremental compiler state with this checkout.  Besides making
# warm local builds deterministic, this prevents a Mac daemon/cache path from
# leaking into a tree later synced to Linux (see ../AGENTS.md).
SCALA_BLOOP_DIR ?= $(abspath .scala-build/bloop-daemon)
SCALA_SERVER_FLAGS = --bloop-daemon-dir $(SCALA_BLOOP_DIR)
VERILATOR ?= verilator
VIVADO    ?= vivado
ARTIX_RTL  = rtl/artix
GOWIN_RTL  = rtl/gowin
ARTIX_TOP  = AmdAc701
GOWIN_TOP  = TangMega138kPro
CONSOLE_TOP = TangConsole138k
CONSOLE_ROMS = \
	hw/gowin/console/demo_logo_spin.bin \
	hw/gowin/console/demo_test04.bin \
	hw/gowin/console/demo_test16.bin \
	hw/gowin/console/demo_test_alphabet.bin
LINT_FLAGS = --lint-only -Wno-WIDTHTRUNC -Wno-WIDTHEXPAND -Wno-CMPCONST -Wno-UNSIGNED -Wno-UNOPTFLAT

.PHONY: compile rtl rtl-artix rtl-gowin rtl-console lint test gowin gowin-console gowin-console-sdram artix clean distclean

compile:
	$(SCALA_CLI) compile $(SCALA_SERVER_FLAGS) .

rtl: rtl-artix rtl-gowin rtl-console

rtl-artix: $(ARTIX_RTL)/$(ARTIX_TOP).v

rtl-gowin: $(GOWIN_RTL)/$(GOWIN_TOP).v

rtl-console: $(GOWIN_RTL)/$(CONSOLE_TOP).v

$(ARTIX_RTL)/$(ARTIX_TOP).v: project.scala $(wildcard src/voodoo/artix/*.scala) $(wildcard src/voodoo/gowin/*.scala) $(shell find ../ext/SpinalVoodoo/src -name '*.scala')
	@mkdir -p $(ARTIX_RTL)
	$(SCALA_CLI) run $(SCALA_SERVER_FLAGS) . -- artix

$(GOWIN_RTL)/$(GOWIN_TOP).v: project.scala $(wildcard src/voodoo/artix/*.scala) $(wildcard src/voodoo/gowin/*.scala) $(shell find ../ext/SpinalVoodoo/src -name '*.scala')
	@mkdir -p $(GOWIN_RTL)
	$(SCALA_CLI) run $(SCALA_SERVER_FLAGS) . -- gowin

$(GOWIN_RTL)/$(CONSOLE_TOP).v: project.scala $(CONSOLE_ROMS) $(wildcard src/voodoo/artix/*.scala) $(wildcard src/voodoo/gowin/*.scala) $(shell find ../ext/SpinalVoodoo/src -name '*.scala')
	@mkdir -p $(GOWIN_RTL)
	$(SCALA_CLI) run $(SCALA_SERVER_FLAGS) . -- console

lint: $(ARTIX_RTL)/$(ARTIX_TOP).v $(GOWIN_RTL)/$(GOWIN_TOP).v
	$(VERILATOR) $(LINT_FLAGS) --top-module $(ARTIX_TOP) $(ARTIX_RTL)/$(ARTIX_TOP).v
	$(VERILATOR) $(LINT_FLAGS) --top-module $(GOWIN_TOP) $(GOWIN_RTL)/$(GOWIN_TOP).v

# Subsets go through ScalaTest's runner args, not --test-only: scala-cli folds
# --test-only (like --main-class) into the project hash, so every distinct
# filter is a fresh full compile.  Runner args do not touch the hash.
#   make test TEST_ARGS='-w voodoo.gowin'        one package
#   make test TEST_ARGS='-z "OSD paints"'        test-name substring
test:
	$(SCALA_CLI) test $(SCALA_SERVER_FLAGS) . $(if $(TEST_ARGS),-- $(TEST_ARGS))

gowin: rtl-gowin
	hw/gowin/run_pnr.sh dock

gowin-console: rtl-console
	python3 hw/gowin/console/force_distributed_fifo_ram.py $(GOWIN_RTL)/$(CONSOLE_TOP).v
	hw/gowin/run_pnr.sh console

gowin-console-sdram: rtl-console
	python3 hw/gowin/console/force_distributed_fifo_ram.py $(GOWIN_RTL)/$(CONSOLE_TOP).v
	hw/gowin/run_pnr.sh console-sdram

artix: rtl-artix
	cd hw/artix && $(VIVADO) -mode batch -source build.tcl

# `clean` keeps .scala-build (Bloop incremental state, ~15 s to rebuild cold)
# and simWorkspace (SpinalSim's verilated models, minutes each). `distclean`
# drops those too.
clean:
	rm -rf $(ARTIX_RTL) $(GOWIN_RTL) hw/gowin/impl hw/artix/*.jou hw/artix/*.log hw/artix/.Xil

distclean: clean
	rm -rf simWorkspace .scala-build
