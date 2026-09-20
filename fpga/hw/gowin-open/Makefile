DEVICE       := GW5AST-LV138PG484AC1/I0
PACK_DEVICE  := GW5AST-138C
TOP          := top
THREADS      ?= 8

YOSYS        ?= yosys
NEXTPNR      ?= nextpnr-himbaechel
GOWIN_PACK   ?= gowin_pack
NEXTPNR_CHIPDB ?=
CHIPDB_ARG   := $(if $(NEXTPNR_CHIPDB),--chipdb $(NEXTPNR_CHIPDB),)

DEMOS        := blinky blinky_local button_toggle button_latch reset_toggle reset_shift bram_test uart_hello gpio_probe gpio_probe_key1 jtag_mailbox jtag_clock_probe
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

$(BUILD_DIR)/jtag_mailbox-synth.json: rtl/jtag_mailbox.v rtl/gw_jtag_primitive.v Makefile | $(BUILD_DIR)
	$(YOSYS) -p "read_verilog -sv -DOPEN_SOURCE $< rtl/gw_jtag_primitive.v; synth_gowin -top $(TOP) -family gw5a -setundef -json $@"

$(BUILD_DIR)/jtag_clock_probe-synth.json: rtl/jtag_clock_probe.v rtl/gw_jtag_primitive.v Makefile | $(BUILD_DIR)
	$(YOSYS) -p "read_verilog -sv -DOPEN_SOURCE $< rtl/gw_jtag_primitive.v; synth_gowin -top $(TOP) -family gw5a -setundef -json $@"

$(BUILD_DIR)/%-pnr.json: $(BUILD_DIR)/%-synth.json $(CST)
	$(NEXTPNR) --threads $(THREADS) --timing-allow-fail -r \
		$(CHIPDB_ARG) \
		--freq 50 --json $< --write $@ --device $(DEVICE) \
		--vopt cst=$(CST) --vopt sspi_as_gpio $(EXTRA_VOPTS)

# Diagnostic fallback: keep the external clock on ordinary routing so the
# GW5AST global-clock network can be tested independently.
$(BUILD_DIR)/blinky_local-pnr.json: EXTRA_VOPTS += --vopt disable_gp_clock_routing
$(BUILD_DIR)/button_toggle-pnr.json: EXTRA_VOPTS += --vopt disable_gp_clock_routing
$(BUILD_DIR)/reset_toggle-pnr.json: EXTRA_VOPTS += --vopt disable_gp_clock_routing
$(BUILD_DIR)/reset_shift-pnr.json: EXTRA_VOPTS += --vopt disable_gp_clock_routing

$(BUILD_DIR)/%$(OPEN_SUFFIX).fs: $(BUILD_DIR)/%-pnr.json
	$(GOWIN_PACK) --cpu_as_gpio --sspi_as_gpio --mspi_as_gpio \
		--ready_as_gpio --done_as_gpio --reconfign_as_gpio \
		-d $(PACK_DEVICE) -o $@ $<

$(BUILD_DIR)/%$(OPEN_SUFFIX).bin: $(BUILD_DIR)/%$(OPEN_SUFFIX).fs fs_to_bin.py
	python3 fs_to_bin.py $< $@

clean:
	rm -rf $(BUILD_DIR)
