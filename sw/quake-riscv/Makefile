CC ?= cc
CFLAGS ?= -std=c99 -Wall -Wextra -Iinclude
SRC = src/voodoo_mmio.c src/fxhal.c src/in_usb.c src/vid_sw.c src/vid_glide.c src/pak.c
PROBE = src/host_probe.c
BUILD ?= build
CMAKE ?= cmake

.PHONY: all probe test shareware engine test-engine engine-rv32 clean

all: probe

probe: $(SRC) $(PROBE)
	$(CC) $(CFLAGS) -o host_probe $(SRC) $(PROBE)

shareware: id1/pak0.pak

id1/pak0.pak: scripts/fetch_shareware.sh
	chmod +x scripts/fetch_shareware.sh
	./scripts/fetch_shareware.sh

engine: quake-embedded/CMakeLists.txt
	$(CMAKE) -S . -B $(BUILD) -DCMAKE_BUILD_TYPE=Release
	$(CMAKE) --build $(BUILD)

test: shareware probe
	./host_probe

test-engine: shareware engine
	QUAKE_SW_PPM=sw_last.ppm $(BUILD)/quake-sw -basedir . -nosound -frames 5

engine-rv32: quake-embedded/CMakeLists.txt
	@CC_RV=$$(command -v riscv-none-elf-gcc || command -v riscv32-unknown-elf-gcc || true); \
	if [ -z "$$CC_RV" ]; then \
	  XPACK="$(CURDIR)/.toolchains/xpack-riscv-none-elf-gcc-15.2.0-1/bin"; \
	  if [ -x "$$XPACK/riscv-none-elf-gcc" ]; then CC_RV="$$XPACK/riscv-none-elf-gcc"; PATH="$$XPACK:$$PATH"; fi; \
	fi; \
	if [ -z "$$CC_RV" ]; then echo "no RV32 gcc (riscv-none-elf-gcc)"; exit 1; fi; \
	PREF=$$(echo $$CC_RV | sed 's/gcc$$//'); \
	$(CMAKE) -S . -B $(BUILD)-rv32 -DCMAKE_BUILD_TYPE=Release \
	  -DCMAKE_TOOLCHAIN_FILE=port/tang/riscv32.cmake \
	  -DCROSS_COMPILE=$$PREF; \
	$(CMAKE) --build $(BUILD)-rv32

SD ?= /Volumes/NO NAME
.PHONY: install-sd
install-sd: engine-rv32
	test -d "$(SD)/voodoo/quake" && test -d "$(SD)/cores/console138k"
	cp $(BUILD)-rv32/quake-sw "$(SD)/voodoo/quake/quake-sw.elf"
	cp $(BUILD)-rv32/quake-sw.bin "$(SD)/voodoo/quake/quake-sw.bin"
	@echo "staged $(SD)/voodoo/quake/quake-sw.elf"
	@echo "TangCore bitstream stays $(SD)/cores/console138k/voodootang.bin"

clean:
	rm -f host_probe sw_last.ppm sw_last.png
	rm -rf $(BUILD) $(BUILD)-rv32

quake-embedded/CMakeLists.txt:
	@echo "missing quake-embedded (git submodule update --init sw/quake-riscv/quake-embedded)" >&2
	@exit 1
