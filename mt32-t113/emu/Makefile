# emu/Makefile - the bare-metal image.
#
#   make              build build/mt32emu-bare.elf (+ .bin, + .map)
#   make run          boot it under qemu-system-arm and print the counters
#   make test         the conformance suite: the same assertions as
#                     port/host/test.sh, made against a Cortex-A7 from reset
#   make wav          render the demo stream to build/out.wav over semihosting
#   make virtio       render through a virtio-sound device model to a host wav
#   make size         text/data/bss and the static footprint
#   make mt32emu      also link the real mt32emu (needs bench/vendor/munt)
#   make MMU=0 ...    build with the MMU and caches left off, as bootm hands over
#   make uimage       wrap the .bin with mkimage for U-Boot `bootm` on a T113
#
# Toolchain: arm-linux-gnueabihf-gcc used as a bare-metal compiler
# (-nostdlib -ffreestanding). See FINDINGS.md for why that, and not
# arm-none-eabi, and what it costs.
#
# SPDX-License-Identifier: 0BSD

CROSS   ?= arm-linux-gnueabihf-
CC      := $(CROSS)gcc
CXX     := $(CROSS)g++
OBJCOPY := $(CROSS)objcopy
OBJDUMP := $(CROSS)objdump
SIZE    := $(CROSS)size
NM      := $(CROSS)nm

QEMU    ?= qemu-system-arm
PYTHON  ?= python3

PORT    := ../port
ROOT    := ..
BUILD   := build
TARGET  := $(BUILD)/mt32emu-bare

MMU     ?= 1

# The ABI is not negotiable: boot/BRINGUP.md 6.1 and port/PORTING.md 4.2 both
# say Cortex-A7, NEON-VFPv4, hard float, ARM (not Thumb) for the synth. bench/
# cross-builds mt32emu with exactly these, and the objects have to link.
ARCHFLAGS := -mcpu=cortex-a7 -mfpu=neon-vfpv4 -mfloat-abi=hard -marm

WARN    := -Wall -Wextra -Wshadow -Wundef
# -U_FORTIFY_SOURCE matters more than it looks. Ubuntu's GCC enables
# _FORTIFY_SOURCE=2 by default at -O2, which rewrites every printf/sprintf call
# into glibc's __printf_chk/__sprintf_chk family. Those symbols exist only in
# glibc, so a bare-metal link against mt32emu fails on four of them with no
# hint as to why. See FINDINGS.md.
COMMON  := $(ARCHFLAGS) -O2 -g -fno-builtin -fno-common \
           -ffunction-sections -fdata-sections -fno-stack-protector \
           -U_FORTIFY_SOURCE -D_FORTIFY_SOURCE=0 \
           -DEMU_MMU=$(MMU)
# -ffreestanding on the C side only. It must NOT be passed to C++: libstdc++'s
# <cstdlib> is guarded by _GLIBCXX_HOSTED, and -ffreestanding makes it hide
# std::div/std::div_t, which mt32emu's Display.cpp uses (Display.cpp:227, and
# port/PORTING.md 4.1 lists div as a symbol to retarget). See FINDINGS.md.
CFLAGS  := $(COMMON) $(WARN) -ffreestanding -std=gnu99
# port/PORTING.md 4.2, exactly. -fno-threadsafe-statics is the one that must
# not be dropped: it removes __cxa_guard_acquire/release, which is correct here
# because the only function-local static is Tables::getInstance().
CXXFLAGS:= $(COMMON) -Wall -Wextra -std=c++98 -fno-exceptions -fno-rtti \
           -fno-threadsafe-statics
ASFLAGS := $(ARCHFLAGS) -g -DEMU_MMU=$(MMU)
INC     := -Isrc -I$(PORT)/include

# -nostdlib: no crt0, no glibc. libm.a and libgcc.a are pulled in by hand at
# the end of the link; retarget.c supplies everything they ask back for.
LIBM    := $(shell $(CC) $(ARCHFLAGS) -print-file-name=libm.a)
LIBGCC  := $(shell $(CC) $(ARCHFLAGS) -print-libgcc-file-name)
LDFLAGS := -nostdlib -nostartfiles -static -T link.ld \
           -Wl,--gc-sections -Wl,-Map,$(TARGET).map -Wl,--build-id=none \
           -Wl,--no-warn-rwx-segments
LDLIBS  := $(LIBM) $(LIBGCC)

SRC_ASM := src/start.S
SRC_C   := src/mmu.c src/console.c src/gic.c src/timer.c src/semihost.c \
           src/printf.c src/retarget.c src/dma.c \
           src/virtio.c src/virtio_snd.c \
           src/emu_audio.c src/emu_midi.c src/emu_storage.c src/emu_log.c \
           src/midi_vectors.c src/main.c \
           $(PORT)/src/mtp_midi_parser.c $(PORT)/src/mtp_render.c \
           $(PORT)/host/engine_fake.c

OBJ := $(addprefix $(BUILD)/,$(notdir $(SRC_ASM:.S=.o)) $(notdir $(SRC_C:.c=.o)))

# ---- the real engine, optional ------------------------------------------
# bench/vendor/munt is read only: headers are staged and objects are built
# entirely inside build/mt32emu/. See tools/prepare-mt32emu.sh for why we do
# not drive upstream's CMakeLists.
MT32EMU_SRC ?= $(ROOT)/bench/vendor/munt/mt32emu
MT32EMU_BUILD := $(BUILD)/mt32emu
MT32EMU_LIB   := $(MT32EMU_BUILD)/libmt32emu.a
MT32EMU_INC   := $(MT32EMU_BUILD)/include
MT32EMU_CFG   := $(MT32EMU_INC)/mt32emu/config.h

# Upstream's libmt32emu_CPP_SOURCES (CMakeLists.txt:148) minus two files:
#   FileStream.cpp         port/PORTING.md 4.5 -- the only <fstream> user
#   SampleRateConverter.cpp  4.6 -- not needed, and it wants RTTI and iostream
MT32EMU_CPP := Analog BReverbModel Display File LA32FloatWaveGenerator \
               LA32Ramp LA32WaveGenerator MidiStreamParser Part Partial \
               PartialManager Poly ROMInfo Synth Tables TVA TVF TVP
MT32EMU_OBJ := $(addprefix $(MT32EMU_BUILD)/,$(addsuffix .o,$(MT32EMU_CPP))) \
               $(MT32EMU_BUILD)/sha1.o

ifdef WITH_MT32EMU
CFLAGS   += -DMTP_WITH_MT32EMU
CXXFLAGS += -DMTP_WITH_MT32EMU
OBJ      += $(BUILD)/engine_mt32emu.o $(BUILD)/engine_mt32emu_fake_roms.o \
            $(BUILD)/cxxrt.o
LDLIBS   := $(MT32EMU_LIB) $(LDLIBS)
INC      += -I$(MT32EMU_INC)
endif

.PHONY: all run test wav virtio size dump clean vectors mt32emu uimage help

all: $(TARGET).elf $(TARGET).bin

$(BUILD):
	@mkdir -p $(BUILD)

$(BUILD)/%.o: src/%.S | $(BUILD)
	$(CC) $(ASFLAGS) $(INC) -c -o $@ $<

$(BUILD)/%.o: src/%.c | $(BUILD)
	$(CC) $(CFLAGS) $(INC) -c -o $@ $<

$(BUILD)/%.o: $(PORT)/src/%.c | $(BUILD)
	$(CC) $(CFLAGS) $(INC) -c -o $@ $<

$(BUILD)/%.o: $(PORT)/host/%.c | $(BUILD)
	$(CC) $(CFLAGS) $(INC) -c -o $@ $<

$(BUILD)/engine_mt32emu.o: $(PORT)/host/engine_mt32emu.cpp $(MT32EMU_LIB) | $(BUILD)
	$(CXX) $(CXXFLAGS) $(INC) -c -o $@ $<

$(BUILD)/engine_mt32emu_fake_roms.o: src/engine_mt32emu_fake_roms.cpp $(MT32EMU_LIB) | $(BUILD)
	$(CXX) $(CXXFLAGS) $(INC) -c -o $@ $<

$(BUILD)/cxxrt.o: src/cxxrt.cpp | $(BUILD)
	$(CXX) $(CXXFLAGS) $(INC) -c -o $@ $<

$(TARGET).elf: $(OBJ) link.ld
	$(CC) $(ARCHFLAGS) $(LDFLAGS) -o $@ $(OBJ) $(LDLIBS)
	@$(SIZE) $@

$(TARGET).bin: $(TARGET).elf
	$(OBJCOPY) -O binary $< $@
	@echo "raw image: $$(stat -c%s $@) bytes"

vectors:
	$(PYTHON) tools/gen_vectors.py src/

# ---- mt32emu, built out of source ---------------------------------------
# Nothing under bench/ is written to. The flags are port/PORTING.md 4.2's,
# and FileStream.cpp is excluded there (4.5) by the toolchain file.
mt32emu:
	$(MAKE) $(MT32EMU_LIB)
	@rm -f $(BUILD)/*.o $(TARGET).elf   # the app objects need -DMTP_WITH_MT32EMU
	$(MAKE) WITH_MT32EMU=1 all

$(MT32EMU_CFG):
	@test -d $(MT32EMU_SRC)/src || { echo "no $(MT32EMU_SRC); clone munt into bench/vendor first"; exit 2; }
	@mkdir -p $(MT32EMU_BUILD)
	./tools/prepare-mt32emu.sh $(MT32EMU_SRC) $(MT32EMU_BUILD)

# -DMT32EMU_WITH_TESTING exports MT32Emu::Test::getControlROMMap
# (Synth.cpp:2912-2922). src/engine_mt32emu_fake_roms.cpp needs it to fabricate
# a control ROM the synth will open; see that file for why.
$(MT32EMU_BUILD)/%.o: $(MT32EMU_SRC)/src/%.cpp $(MT32EMU_CFG)
	$(CXX) $(CXXFLAGS) -DMT32EMU_WITH_TESTING -I$(MT32EMU_INC) -I$(MT32EMU_INC)/mt32emu -c -o $@ $<

$(MT32EMU_BUILD)/sha1.o: $(MT32EMU_SRC)/src/sha1/sha1.cpp $(MT32EMU_CFG)
	$(CXX) $(CXXFLAGS) -I$(MT32EMU_INC) -I$(MT32EMU_INC)/mt32emu -c -o $@ $<

$(MT32EMU_LIB): $(MT32EMU_OBJ)
	$(CROSS)ar rcs $@ $(MT32EMU_OBJ)
	@$(SIZE) -t $(MT32EMU_OBJ) | tail -1

# ---- running -------------------------------------------------------------
# -M virt -cpu cortex-a7: QEMU has no T113/R528/D1 model and never will
# (boot/BRINGUP.md 6.4), so this is a generic ARMv7-A machine that happens to
# put its RAM at 0x40000000, which is also the T113's DRAM base.
QEMUFLAGS := -M virt -cpu cortex-a7 -m 256 -nographic -nodefaults \
             -serial mon:stdio -no-reboot \
             -semihosting-config enable=on,target=native

ARGS ?= --midi demo --seconds 2

run: all
	$(QEMU) $(QEMUFLAGS),arg=mt32emu-bare,$(subst $(space),$(comma),$(strip $(foreach a,$(ARGS),arg=$(a)))) -kernel $(TARGET).elf

empty :=
space := $(empty) $(empty)
comma := ,

wav: all
	$(QEMU) $(QEMUFLAGS),arg=x,arg=--midi,arg=demo,arg=--seconds,arg=4,arg=--wav,arg=$(BUILD)/out.wav -kernel $(TARGET).elf
	@ls -l $(BUILD)/out.wav

# Sink 2: a real device model. -global virtio-mmio.force-legacy=false is not
# optional -- QEMU's virtio-mmio transport defaults to the legacy (version 1)
# interface, and src/virtio.c only implements the modern one. -net none stops
# the default NIC failing on a missing efi-virtio.rom.
VIRTIOFLAGS := -net none -global virtio-mmio.force-legacy=false \
               -audiodev wav,id=snd0,path=$(BUILD)/virtio.wav \
               -device virtio-sound-device,audiodev=snd0
VRING ?= 6

virtio: all
	$(QEMU) $(QEMUFLAGS),arg=x,arg=--midi,arg=demo,arg=--seconds,arg=4,arg=--sink,arg=virtio,arg=--ring,arg=$(VRING) \
	        $(VIRTIOFLAGS) -kernel $(TARGET).elf
	@ls -l $(BUILD)/virtio.wav

test: all
	./test.sh

size: all
	@$(SIZE) -A $(TARGET).elf
	@echo
	@echo "ten largest symbols:"
	@$(NM) --size-sort -S -r $(TARGET).elf | head -10

dump: all
	$(OBJDUMP) -d $(TARGET).elf > $(TARGET).lst
	@echo "$(TARGET).lst"

# ---- for the board -------------------------------------------------------
# boot/BRINGUP.md 4.1 recommendation (1): wrap the payload and use bootm, so
# that cleanup_before_linux() hands over with MMU and D-cache off. That is the
# state src/start.S already assumes, which is why this is one command.
uimage: $(TARGET).bin
	mkimage -A arm -O linux -T kernel -C none \
	        -a 0x40200000 -e 0x40200000 -n mt32-t113 \
	        -d $(TARGET).bin $(TARGET).uimg

clean:
	rm -rf $(BUILD)

help:
	@sed -n '2,20p' Makefile
