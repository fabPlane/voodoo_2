# Local Verilator experiment for the generated Spinal Tang Console top.

VERILATOR ?= verilator
TOP       := TangConsole138k
RTL_DIR   := ../../../fpga/rtl/gowin
RTL       := $(RTL_DIR)/$(TOP).v
OBJ_DIR   := obj_dir
BIN       := $(OBJ_DIR)/V$(TOP)
OUTPUT    := output/boot-menu.png
SCENARIO  ?= boot
DEMOS     := logo test04 test16 alphabet
PNG_FLAGS := $(shell pkg-config --cflags --libs libpng)

.PHONY: all model run run-demos $(addprefix run-,$(DEMOS)) clean

all: run

model: $(BIN)

$(BIN): console_tb.cpp $(RTL)
	$(VERILATOR) --cc --exe --build -j 8 -O2 \
		--top-module $(TOP) --Mdir $(OBJ_DIR) \
		-Wno-fatal -Wno-WIDTHTRUNC -Wno-WIDTHEXPAND \
		-CFLAGS "-O2 -std=c++17 $(shell pkg-config --cflags libpng)" \
		-LDFLAGS "$(shell pkg-config --libs libpng)" \
		$(RTL) console_tb.cpp

run: $(BIN)
	mkdir -p output
	cd $(RTL_DIR) && ../../../amaranth/emu/console/$(BIN) \
		../../../amaranth/emu/console/$(OUTPUT) $(SCENARIO)

$(addprefix run-,$(DEMOS)): run-%: $(BIN)
	mkdir -p output
	cd $(RTL_DIR) && ../../../amaranth/emu/console/$(BIN) \
		../../../amaranth/emu/console/output/$*.png $*

run-demos: $(addprefix run-,$(DEMOS))

clean:
	rm -rf $(OBJ_DIR) output
