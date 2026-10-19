# iverilog sim of SerDes_Top with own_pcie_ctrl / own_upar_arbiter.
#   make            build + run
#   make VCD=1      also dump tb.vcd
GOWINHOME ?= $(HOME)/gowin/standard-1.9.12.04
PRIM      := $(GOWINHOME)/IDE/simlib/gw5a/prim_sim.v
SERDES    ?= $(abspath ../../ip/PCIE_Controller_Top/serdes.v)
RTL       := ../rtl/own_pcie_ctrl.v ../rtl/own_upar_arbiter.v ../rtl/own_pcie_probe.v

all: run

build/serdes_own.v: $(SERDES) ../tools/gen_serdes.py
	@mkdir -p build
	python3 ../tools/gen_serdes.py --own $(SERDES) $@

build/serdes_probe.v: $(SERDES) ../tools/gen_serdes.py
	@mkdir -p build
	python3 ../tools/gen_serdes.py --own --probe $(SERDES) $@

build/gtr12_models.v: $(PRIM) mk_models.py $(wildcard models/*.vh)
	@mkdir -p build
	python3 mk_models.py $(PRIM) $@

build/tb: tb.v build/serdes_own.v build/gtr12_models.v $(RTL)
	iverilog -g2012 -Wall -Wno-timescale -Wno-implicit-dimensions -o $@ -s tb \
	    tb.v build/serdes_own.v build/gtr12_models.v $(RTL)

# Same test with own_pcie_probe watching own_pcie_ctrl: checks the probe.
build/tb_probe: tb.v build/serdes_probe.v build/gtr12_models.v $(RTL)
	iverilog -g2012 -Wall -Wno-timescale -Wno-implicit-dimensions -DPROBE -o $@ -s tb \
	    tb.v build/serdes_probe.v build/gtr12_models.v $(RTL)

run: build/tb build/tb_probe
	vvp -n build/tb $(if $(VCD),+vcd)
	vvp -n build/tb_probe

clean:
	rm -rf build tb.vcd

.PHONY: all run clean
