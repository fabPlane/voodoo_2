# SpinalVoodoo FPGA ports
#
#   make compile     type-check Artix and Gowin ports against the upstream core
#   make rtl         generate Artix board + Gowin Verilog
#   make rtl-artix   generate rtl/artix/AmdAc701.v
#   make rtl-gowin   generate rtl/gowin/TangMega138kPro.v
#   make lint        Verilator lint of generated Verilog
#   make test        SpinalSim tests (needs verilator on PATH)
#   make gowin       Gowin EDA bitstream (needs gw_sh and generated IP; see hw/gowin/ip.md)
#   make gowin-ip    generate Pro Dock PLLs (DDR3 + PCIe still from the GUI)
#   make artix       Vivado bitstream (needs vivado and generated IP; see hw/artix/ip.md)

SCALA_CLI ?= scala-cli
VERILATOR ?= verilator
GOWINHOME ?= $(shell bash -c '. hw/gowin/env.sh && printf %s "$$GOWINHOME"')
GOWIN_SH  ?= $(if $(wildcard $(GOWINHOME)/IDE/bin/gw_sh),$(GOWINHOME)/IDE/bin/gw_sh,gw_sh)
VIVADO    ?= vivado
ARTIX_RTL  = rtl/artix
GOWIN_RTL  = rtl/gowin
ARTIX_TOP  = AmdAc701
GOWIN_TOP  = TangMega138kPro
LINT_FLAGS = --lint-only -Wno-WIDTHTRUNC -Wno-WIDTHEXPAND -Wno-CMPCONST -Wno-UNSIGNED -Wno-UNOPTFLAT

.PHONY: compile rtl rtl-artix rtl-gowin lint test gowin gowin-ip artix clean

compile:
	$(SCALA_CLI) compile .

rtl: rtl-artix rtl-gowin

rtl-artix: $(ARTIX_RTL)/$(ARTIX_TOP).v

rtl-gowin: $(GOWIN_RTL)/$(GOWIN_TOP).v

$(ARTIX_RTL)/$(ARTIX_TOP).v: project.scala $(wildcard src/voodoo/artix/*.scala) $(wildcard src/voodoo/gowin/*.scala) $(shell find ../ext/SpinalVoodoo/src -name '*.scala')
	@mkdir -p $(ARTIX_RTL)
	$(SCALA_CLI) run . --main-class voodoo.artix.AmdAc701Gen

$(GOWIN_RTL)/$(GOWIN_TOP).v: project.scala $(wildcard src/voodoo/artix/*.scala) $(wildcard src/voodoo/gowin/*.scala) $(shell find ../ext/SpinalVoodoo/src -name '*.scala')
	@mkdir -p $(GOWIN_RTL)
	$(SCALA_CLI) run . --main-class voodoo.gowin.TangMega138kProGen

lint: $(ARTIX_RTL)/$(ARTIX_TOP).v $(GOWIN_RTL)/$(GOWIN_TOP).v
	$(VERILATOR) $(LINT_FLAGS) --top-module $(ARTIX_TOP) $(ARTIX_RTL)/$(ARTIX_TOP).v
	$(VERILATOR) $(LINT_FLAGS) --top-module $(GOWIN_TOP) $(GOWIN_RTL)/$(GOWIN_TOP).v

test:
	$(SCALA_CLI) test .

gowin-ip:
	hw/gowin/generate_ip.sh

gowin: rtl-gowin
	cd hw/gowin && $(GOWIN_SH) build.tcl

artix: rtl-artix
	cd hw/artix && $(VIVADO) -mode batch -source build.tcl

clean:
	rm -rf $(ARTIX_RTL) $(GOWIN_RTL) simWorkspace .scala-build hw/gowin/impl hw/artix/*.jou hw/artix/*.log hw/artix/.Xil
