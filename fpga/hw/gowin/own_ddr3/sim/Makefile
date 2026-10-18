# Simulation of own_ddr3 with the Gowin GW5A primitive models.
# The GW5A DQS model is encrypted, so the plain GW2A one is extracted from
# the installed IDE at build time (nothing from Gowin is checked in).
GOWINHOME ?= $(HOME)/gowin/standard-1.9.12.04
SIMLIB := $(GOWINHOME)/IDE/simlib
RTL := ../rtl/own_ddr3.v ../rtl/own_ddr3_ctrl.v ../rtl/own_ddr3_phy.v
RD_DLY ?= -DRD_DLY0=0.3 -DRD_DLY1=0.5 -DRD_DLY2=0.9 -DRD_DLY3=1.4
TIMEOUT_US ?= 2000
SIMDEFS ?=

all: run

build/dqs_gw2a.v: $(SIMLIB)/gw2a/prim_sim.v
	mkdir -p build
	awk '/^module DQS\(/{p=1} p{print} p&&/^endmodule/{exit}' $< > build/dqs_raw.v
	python3 patch_dqs.py build/dqs_raw.v $@

PRIMS := GSR OSER8 OSER8_MEM IDES8_MEM IOBUF ELVDS_IOBUF ELVDS_OBUF CLKDIV IODELAY
build/prims_gw5a.v: $(SIMLIB)/gw5a/prim_sim.v
	mkdir -p build
	awk -v want="$(PRIMS)" 'BEGIN{n=split(want,w," "); for(i=1;i<=n;i++) k[w[i]]=1} \
	  /^module /{m=$$2; sub(/[ (;].*/,"",m); p=(m in k)} p{print} /^endmodule/{p=0}' $< > $@

build/tb.vvp: tb.v ddr3_model.v $(RTL) build/dqs_gw2a.v build/prims_gw5a.v
	iverilog -g2012 -DOWN_DDR3_SIM $(SIMDEFS) $(RD_DLY) -DTIMEOUT_US=$(TIMEOUT_US) -o $@ \
	  tb.v ddr3_model.v $(RTL) build/dqs_gw2a.v build/prims_gw5a.v

run: build/tb.vvp
	cd build && vvp -n tb.vvp $(PLUSARGS)

clean:
	rm -rf build
.PHONY: all run clean
