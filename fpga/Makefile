# SpinalVoodoo FPGA ports
#
#   make compile     type-check Artix and Gowin ports against the upstream core
#   make rtl         generate Artix board + Gowin Verilog
#   make rtl-artix   generate rtl/artix/AmdAc701.v
#   make rtl-gowin   generate rtl/gowin/TangMega138kPro.v
#   make rtl-console generate rtl/gowin/TangConsole138k.v (Retro Console menu demo)
#   make lint        Verilator lint of generated Verilog
#   make test        SpinalSim tests (needs verilator on PATH)
#   make gowin       Gowin dock bitstream (PnR on hyper-ms-lin unless that box is busy)
#   make gowin-console Console bitstream (same: Linux PnR, Mac only as fallback)
#   make gowin-console-sdram Experimental Console bitstream using NESTang SDRAM
#   make artix       Vivado bitstream (needs vivado and generated IP; see hw/artix/ip.md)

SCALA_CLI ?= scala-cli
VERILATOR ?= verilator
VIVADO    ?= vivado
ARTIX_RTL  = rtl/artix
GOWIN_RTL  = rtl/gowin
ARTIX_TOP  = AmdAc701
GOWIN_TOP  = TangMega138kPro
CONSOLE_TOP = TangConsole138k
LINT_FLAGS = --lint-only -Wno-WIDTHTRUNC -Wno-WIDTHEXPAND -Wno-CMPCONST -Wno-UNSIGNED -Wno-UNOPTFLAT

.PHONY: compile rtl rtl-artix rtl-gowin rtl-console lint test gowin gowin-console gowin-console-sdram artix clean

compile:
	$(SCALA_CLI) compile .

rtl: rtl-artix rtl-gowin rtl-console

rtl-artix: $(ARTIX_RTL)/$(ARTIX_TOP).v

rtl-gowin: $(GOWIN_RTL)/$(GOWIN_TOP).v

rtl-console: $(GOWIN_RTL)/$(CONSOLE_TOP).v

$(ARTIX_RTL)/$(ARTIX_TOP).v: project.scala $(wildcard src/voodoo/artix/*.scala) $(wildcard src/voodoo/gowin/*.scala) $(shell find ../ext/SpinalVoodoo/src -name '*.scala')
	@mkdir -p $(ARTIX_RTL)
	$(SCALA_CLI) run . --main-class voodoo.artix.AmdAc701Gen

$(GOWIN_RTL)/$(GOWIN_TOP).v: project.scala $(wildcard src/voodoo/artix/*.scala) $(wildcard src/voodoo/gowin/*.scala) $(shell find ../ext/SpinalVoodoo/src -name '*.scala')
	@mkdir -p $(GOWIN_RTL)
	$(SCALA_CLI) run . --main-class voodoo.gowin.TangMega138kProGen

$(GOWIN_RTL)/$(CONSOLE_TOP).v: project.scala $(wildcard src/voodoo/artix/*.scala) $(wildcard src/voodoo/gowin/*.scala) $(shell find ../ext/SpinalVoodoo/src -name '*.scala')
	@mkdir -p $(GOWIN_RTL)
	$(SCALA_CLI) run . --main-class voodoo.gowin.TangConsole138kGen

lint: $(ARTIX_RTL)/$(ARTIX_TOP).v $(GOWIN_RTL)/$(GOWIN_TOP).v
	$(VERILATOR) $(LINT_FLAGS) --top-module $(ARTIX_TOP) $(ARTIX_RTL)/$(ARTIX_TOP).v
	$(VERILATOR) $(LINT_FLAGS) --top-module $(GOWIN_TOP) $(GOWIN_RTL)/$(GOWIN_TOP).v

test:
	$(SCALA_CLI) test .

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

clean:
	rm -rf $(ARTIX_RTL) $(GOWIN_RTL) simWorkspace .scala-build hw/gowin/impl hw/artix/*.jou hw/artix/*.log hw/artix/.Xil
