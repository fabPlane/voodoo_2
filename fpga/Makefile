# SpinalVoodoo FPGA ports
#
#   make compile     type-check Artix and Gowin ports against the upstream core
#   make rtl         generate Artix and Gowin Verilog
#   make rtl-artix   generate rtl/artix/CoreArtix.v
#   make rtl-gowin   generate rtl/gowin/TangMega138kPro.v
#   make lint        Verilator lint of generated Verilog
#   make test        SpinalSim tests (needs verilator on PATH)
#   make gowin       Gowin EDA bitstream (needs gw_sh and generated IP; see hw/gowin/ip.md)

SCALA_CLI ?= scala-cli
VERILATOR ?= verilator
GOWIN_SH  ?= gw_sh
ARTIX_RTL  = rtl/artix
GOWIN_RTL  = rtl/gowin
ARTIX_TOP  = CoreArtix
GOWIN_TOP  = TangMega138kPro
LINT_FLAGS = --lint-only -Wno-WIDTHTRUNC -Wno-WIDTHEXPAND -Wno-CMPCONST -Wno-UNSIGNED -Wno-UNOPTFLAT

.PHONY: compile rtl rtl-artix rtl-gowin lint test gowin clean

compile:
	$(SCALA_CLI) compile .

rtl: rtl-artix rtl-gowin

rtl-artix: $(ARTIX_RTL)/$(ARTIX_TOP).v

rtl-gowin: $(GOWIN_RTL)/$(GOWIN_TOP).v

$(ARTIX_RTL)/$(ARTIX_TOP).v: project.scala $(wildcard src/voodoo/artix/*.scala) $(wildcard src/voodoo/gowin/*.scala) $(shell find ../ext/SpinalVoodoo/src -name '*.scala')
	@mkdir -p $(ARTIX_RTL)
	$(SCALA_CLI) run . --main-class voodoo.artix.CoreArtixGen

$(GOWIN_RTL)/$(GOWIN_TOP).v: project.scala $(wildcard src/voodoo/artix/*.scala) $(wildcard src/voodoo/gowin/*.scala) $(shell find ../ext/SpinalVoodoo/src -name '*.scala')
	@mkdir -p $(GOWIN_RTL)
	$(SCALA_CLI) run . --main-class voodoo.gowin.TangMega138kProGen

lint: $(ARTIX_RTL)/$(ARTIX_TOP).v $(GOWIN_RTL)/$(GOWIN_TOP).v
	$(VERILATOR) $(LINT_FLAGS) --top-module $(ARTIX_TOP) $(ARTIX_RTL)/$(ARTIX_TOP).v
	$(VERILATOR) $(LINT_FLAGS) --top-module $(GOWIN_TOP) $(GOWIN_RTL)/$(GOWIN_TOP).v

test:
	$(SCALA_CLI) test .

gowin: rtl-gowin
	cd hw/gowin && $(GOWIN_SH) build.tcl

clean:
	rm -rf $(ARTIX_RTL) $(GOWIN_RTL) simWorkspace .scala-build hw/gowin/impl
