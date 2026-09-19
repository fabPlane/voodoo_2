DEVICE       := GW5AST-LV138PG484AC1/I0
PACK_DEVICE  := GW5AST-138C
TOP          := top
THREADS      ?= 8

YOSYS        ?= yosys
NEXTPNR      ?= nextpnr-himbaechel
GOWIN_PACK   ?= gowin_pack

DEMOS        := blinky bram_test uart_hello
BUILD_DIR    := build
CST          := tang_console_138k_open.cst
OPEN_SUFFIX  := -open
OPEN_BINS    := $(addprefix $(BUILD_DIR)/,$(addsuffix $(OPEN_SUFFIX).bin,$(DEMOS)))
# Reserved SD-card name for a future complete open-source Voodoo core.
# The Gowin EDA build remains voodootang.bin.
OPEN_VOODOO_SD_NAME := voodootang-open.bin

.DEFAULT_GOAL := all
.PHONY: all bin clean print-open-core-name $(DEMOS) hdmi_colorbars hdmi_colorbars-synth
.SECONDARY:

all: $(DEMOS)

bin: $(OPEN_BINS)

$(DEMOS): %: $(BUILD_DIR)/%$(OPEN_SUFFIX).fs

# The PLL now places with our Apicula fork. CLKDIV routing is the next blocker
# before the HDMI demo can produce a complete bitstream.
hdmi_colorbars-synth: $(BUILD_DIR)/hdmi_colorbars-synth.json
hdmi_colorbars: $(BUILD_DIR)/hdmi_colorbars$(OPEN_SUFFIX).fs

print-open-core-name:
	@echo $(OPEN_VOODOO_SD_NAME)

$(BUILD_DIR):
	mkdir -p $@

$(BUILD_DIR)/%-synth.json: rtl/%.v Makefile | $(BUILD_DIR)
	$(YOSYS) -p "read_verilog -sv $<; synth_gowin -top $(TOP) -family gw5a -setundef -json $@"

$(BUILD_DIR)/%-pnr.json: $(BUILD_DIR)/%-synth.json $(CST)
	$(NEXTPNR) --threads $(THREADS) --timing-allow-fail -r \
		--freq 50 --json $< --write $@ --device $(DEVICE) --vopt cst=$(CST)

$(BUILD_DIR)/%$(OPEN_SUFFIX).fs: $(BUILD_DIR)/%-pnr.json
	$(GOWIN_PACK) --cpu_as_gpio -d $(PACK_DEVICE) -o $@ $<

$(BUILD_DIR)/%$(OPEN_SUFFIX).bin: $(BUILD_DIR)/%$(OPEN_SUFFIX).fs fs_to_bin.py
	python3 fs_to_bin.py $< $@

clean:
	rm -rf $(BUILD_DIR)
