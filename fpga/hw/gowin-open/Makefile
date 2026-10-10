# BOARD=console (Tang Console 138K, PBGA484) or BOARD=pro (Tang Mega 138K
# Pro Dock, FCPBGA676). Both are the GW5AST-138 die; only the package differs.
BOARD        ?= console
ifeq ($(BOARD),pro)
DEVICE       := GW5AST-LV138FPG676AC1/I0
CST          := tang_mega_138k_pro_open.cst
BUILD_DIR    := build-pro
else
DEVICE       := GW5AST-LV138PG484AC1/I0
CST          := tang_console_138k_open.cst
BUILD_DIR    := build
endif
PACK_DEVICE  := GW5AST-138C
# Optional: nextpnr chip database that includes the FCPBGA676A package
# (built from apicula against Gowin 1.9.12.04 data). Empty = toolchain default.
CHIPDB       ?=
NEXTPNR_CHIPDB := $(if $(CHIPDB),--chipdb $(CHIPDB),)
TOP          := top
THREADS      ?= 8

YOSYS        ?= yosys
NEXTPNR      ?= nextpnr-himbaechel
GOWIN_PACK   ?= gowin_pack

DEMOS        := blinky bram_test uart_hello
OPEN_SUFFIX  := -open
OPEN_BINS    := $(addprefix $(BUILD_DIR)/,$(addsuffix $(OPEN_SUFFIX).bin,$(DEMOS)))
# Reserved SD-card name for a future complete open-source Voodoo core.
# The Gowin EDA build remains voodootang.bin.
OPEN_VOODOO_SD_NAME := voodootang-open.bin

.DEFAULT_GOAL := all
.PHONY: all bin clean print-open-core-name $(DEMOS) blinky_pro hdmi_colorbars hdmi_colorbars-synth \
	voodoo_core voodoo_core-synth voodoo_core-pnr
.SECONDARY:

all: $(DEMOS)

# Pro Dock smoke test: make BOARD=pro blinky_pro
blinky_pro: $(BUILD_DIR)/blinky_pro$(OPEN_SUFFIX).fs

bin: $(OPEN_BINS)

$(DEMOS): %: $(BUILD_DIR)/%$(OPEN_SUFFIX).fs

# The PLL now places with our Apicula fork. CLKDIV routing is the next blocker
# before the HDMI demo can produce a complete bitstream.
hdmi_colorbars-synth: $(BUILD_DIR)/hdmi_colorbars-synth.json
hdmi_colorbars: $(BUILD_DIR)/hdmi_colorbars$(OPEN_SUFFIX).fs

# SpinalVoodoo core with PCIe/DDR3 stubbed (step 2 of STEP2-STEP3-PLAN.md):
#   make BOARD=pro CHIPDB=chipdb-GW5AST-138C-fpg676.bin voodoo_core
# Logs go to $(BUILD_DIR)/voodoo_core-{synth,pnr}.log.
VOODOO_RTL   := ../../rtl/gowin/TangMega138kPro.v
VOODOO_FREQ  ?= 50
voodoo_core-synth: $(BUILD_DIR)/voodoo_core-synth.json
voodoo_core-pnr: $(BUILD_DIR)/voodoo_core-pnr.json
voodoo_core: $(BUILD_DIR)/voodoo_core$(OPEN_SUFFIX).fs

$(BUILD_DIR)/voodoo_core-synth.json: rtl/voodoo_core_open.v $(VOODOO_RTL) | $(BUILD_DIR)
	$(YOSYS) -l $(BUILD_DIR)/voodoo_core-synth.log -p "read_verilog $(VOODOO_RTL); read_verilog -sv rtl/voodoo_core_open.v; \
		synth_gowin -top voodoo_core_open -family gw5a -setundef -json $@; stat"

$(BUILD_DIR)/voodoo_core-pnr.json: $(BUILD_DIR)/voodoo_core-synth.json $(CST)
	$(NEXTPNR) --threads $(THREADS) --timing-allow-fail -r --log $(BUILD_DIR)/voodoo_core-pnr.log \
		--report $(BUILD_DIR)/voodoo_core-report.json \
		--freq $(VOODOO_FREQ) --json $< --write $@ --device $(DEVICE) $(NEXTPNR_CHIPDB) --vopt cst=$(CST)

print-open-core-name:
	@echo $(OPEN_VOODOO_SD_NAME)

$(BUILD_DIR):
	mkdir -p $@

$(BUILD_DIR)/%-synth.json: rtl/%.v Makefile | $(BUILD_DIR)
	$(YOSYS) -p "read_verilog -sv $<; synth_gowin -top $(TOP) -family gw5a -setundef -json $@"

$(BUILD_DIR)/%-pnr.json: $(BUILD_DIR)/%-synth.json $(CST)
	$(NEXTPNR) --threads $(THREADS) --timing-allow-fail -r \
		--freq 50 --json $< --write $@ --device $(DEVICE) $(NEXTPNR_CHIPDB) --vopt cst=$(CST)

$(BUILD_DIR)/%$(OPEN_SUFFIX).fs: $(BUILD_DIR)/%-pnr.json
	$(GOWIN_PACK) --cpu_as_gpio -d $(PACK_DEVICE) -o $@ $<

$(BUILD_DIR)/%$(OPEN_SUFFIX).bin: $(BUILD_DIR)/%$(OPEN_SUFFIX).fs fs_to_bin.py
	python3 fs_to_bin.py $< $@

clean:
	rm -rf $(BUILD_DIR)
