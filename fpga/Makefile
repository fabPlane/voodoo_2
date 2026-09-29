# SpinalVoodoo FPGA ports
#
#   make compile         type-check Artix / Gowin / Chromatix ports
#   make rtl             generate Artix + Gowin + Chromatix Verilog
#   make rtl-artix       generate rtl/artix/AmdAc701.v
#   make rtl-gowin       generate rtl/gowin/TangMega138kPro.v
#   make rtl-chromatix   generate rtl/chromatix/ModRetroChromatic.v
#   make litex-chromatix LiteX elaborate (--no-compile) for ChromatiX+Voodoo
#   make lint            Verilator lint of generated Verilog
#   make test            SpinalSim tests (needs verilator on PATH)
#   make gowin           Gowin EDA bitstream (needs gw_sh and generated IP; see hw/gowin/ip.md)
#   make artix           Vivado bitstream (needs vivado and generated IP; see hw/artix/ip.md)

SCALA_CLI ?= scala-cli
VERILATOR ?= verilator
GOWIN_SH  ?= gw_sh
VIVADO    ?= vivado
PYTHON    ?= python3
ARTIX_RTL  = rtl/artix
GOWIN_RTL  = rtl/gowin
CHROMATIX_RTL = rtl/chromatix
ARTIX_TOP  = AmdAc701
GOWIN_TOP  = TangMega138kPro
CHROMATIX_TOP = ModRetroChromatic
LINT_FLAGS = --lint-only -Wno-WIDTHTRUNC -Wno-WIDTHEXPAND -Wno-CMPCONST -Wno-UNSIGNED -Wno-UNOPTFLAT

.PHONY: compile rtl rtl-artix rtl-gowin rtl-chromatix litex-chromatix lint test gowin artix clean

compile:
	$(SCALA_CLI) compile .

rtl: rtl-artix rtl-gowin rtl-chromatix

rtl-artix: $(ARTIX_RTL)/$(ARTIX_TOP).v

rtl-gowin: $(GOWIN_RTL)/$(GOWIN_TOP).v

rtl-chromatix: $(CHROMATIX_RTL)/$(CHROMATIX_TOP).v

$(ARTIX_RTL)/$(ARTIX_TOP).v: project.scala $(wildcard src/voodoo/artix/*.scala) $(wildcard src/voodoo/gowin/*.scala) $(wildcard src/voodoo/chromatix/*.scala) $(shell find ../ext/SpinalVoodoo/src -name '*.scala')
	@mkdir -p $(ARTIX_RTL)
	$(SCALA_CLI) run . --main-class voodoo.artix.AmdAc701Gen

$(GOWIN_RTL)/$(GOWIN_TOP).v: project.scala $(wildcard src/voodoo/artix/*.scala) $(wildcard src/voodoo/gowin/*.scala) $(wildcard src/voodoo/chromatix/*.scala) $(shell find ../ext/SpinalVoodoo/src -name '*.scala')
	@mkdir -p $(GOWIN_RTL)
	$(SCALA_CLI) run . --main-class voodoo.gowin.TangMega138kProGen

$(CHROMATIX_RTL)/$(CHROMATIX_TOP).v: project.scala $(wildcard src/voodoo/artix/*.scala) $(wildcard src/voodoo/gowin/*.scala) $(wildcard src/voodoo/chromatix/*.scala) $(shell find ../ext/SpinalVoodoo/src -name '*.scala')
	@mkdir -p $(CHROMATIX_RTL)
	$(SCALA_CLI) run . --main-class voodoo.chromatix.ModRetroChromaticGen

litex-chromatix: rtl-chromatix
	$(PYTHON) chromatix/voodoo_chromatix.py --build --no-compile

lint: $(ARTIX_RTL)/$(ARTIX_TOP).v $(GOWIN_RTL)/$(GOWIN_TOP).v $(CHROMATIX_RTL)/$(CHROMATIX_TOP).v
	$(VERILATOR) $(LINT_FLAGS) --top-module $(ARTIX_TOP) $(ARTIX_RTL)/$(ARTIX_TOP).v
	$(VERILATOR) $(LINT_FLAGS) --top-module $(GOWIN_TOP) $(GOWIN_RTL)/$(GOWIN_TOP).v
	$(VERILATOR) $(LINT_FLAGS) --top-module $(CHROMATIX_TOP) $(CHROMATIX_RTL)/$(CHROMATIX_TOP).v

test:
	$(SCALA_CLI) test .

gowin: rtl-gowin
	cd hw/gowin && $(GOWIN_SH) build.tcl

artix: rtl-artix
	cd hw/artix && $(VIVADO) -mode batch -source build.tcl

clean:
	rm -rf $(ARTIX_RTL) $(GOWIN_RTL) $(CHROMATIX_RTL) simWorkspace .scala-build \
		hw/gowin/impl hw/artix/*.jou hw/artix/*.log hw/artix/.Xil chromatix/build
