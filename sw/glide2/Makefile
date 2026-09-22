# Cross-build the trace-capture Glide2 runtime as a Win32 glide2x.dll.
#
# The sources stay in ext/SpinalVoodoo. This makefile only adds the MinGW
# compile, so the submodule gitlink is not part of the change.

ROOT := $(abspath ../..)
EMU  := $(ROOT)/ext/SpinalVoodoo/emu
GLIDE := $(EMU)/glide
SST1 := $(GLIDE)/glide2x/sst1
SRC  := $(SST1)/glide/src
SW   := $(GLIDE)/swlibs
SIM  := $(EMU)/sim
TRACE := $(EMU)/trace

CC := i686-w64-mingw32-gcc
BUILD := build

CDEFS := -DINIT_LINUX -DSIM_BACKEND -DGLIDE_HARDWARE -DGLIDE_DEFAULT_GAMMA=1.3f
CDEFS += -DGLIDE_LIB=1 -DSST1 -DGLIDE_USE_C_TRISETUP -DFX_DLL_ENABLE -DWIN32

CFLAGS := -std=gnu89 -fcommon -w -O2 -ffast-math
CFLAGS += -I$(BUILD)/include
CFLAGS += -I$(SRC) -I$(SST1)/incsrc -I$(SST1)/init -I$(SST1)/init/initvg
CFLAGS += -I$(SW)/fxmisc -I$(SW)/newpci/pcilib -I$(SW)/fxmemmap
CFLAGS += -I$(SIM)/harness -I$(TRACE)
CFLAGS += $(CDEFS)
CFLAGS += -include $(abspath mingw_compat.h)

DLL_LDFLAGS := -shared -static-libgcc -Wl,--add-stdcall-alias -Wl,--enable-stdcall-fixup
DLL_LDFLAGS += -Wl,--out-implib,$(BUILD)/libglide2x.dll.a
DLL_LIBS := -lgdi32 -luser32

GLIDE_SRCS := \
	gsplash.c g3df.c gu.c guclip.c gpci.c gump.c diglide.c disst.c ditex.c \
	gbanner.c gerror.c gmovie.c digutex.c ddgump.c gaa.c gdraw.c gglide.c \
	glfb.c gsst.c gtex.c gtexdl.c gutex.c gxdraw.c gx11present.c

INIT_SRCS := \
	init.c gdebug.c vgdrvr.c gamma.c dac.c video.c parse.c sli.c util.c \
	info.c print.c sst1init.c

PCI_SRCS := sst1_pci.c fxmsr.c fxpci.c fxsim.c fxos.c fximg.c
HARNESS_SRCS := sim_trace_harness.c voodoo_trace_writer.c

OBJS := $(addprefix $(BUILD)/,$(GLIDE_SRCS:.c=.o) $(INIT_SRCS:.c=.o) $(PCI_SRCS:.c=.o) $(HARNESS_SRCS:.c=.o))
OBJS += $(BUILD)/cpuid.o $(BUILD)/win32_io.o

VPATH := $(SRC):$(SST1)/init:$(SST1)/init/initvg:$(SW)/newpci/pcilib:$(SW)/fxmisc:$(SIM)/harness:$(TRACE)

TESTS_DIR := $(SST1)/glide/tests
SDK_TESTS := $(patsubst $(TESTS_DIR)/%.c,$(BUILD)/tests/%.exe,$(wildcard $(TESTS_DIR)/test*.c))

TEST_CFLAGS := -std=gnu89 -fcommon -w -O2 -ffast-math
TEST_CFLAGS += -I$(TESTS_DIR) -I$(SRC) -I$(SST1)/incsrc -I$(SST1)/init -I$(SW)/fxmisc
TEST_CFLAGS += -DSST1 -DSIM_BACKEND -DWIN32

.PHONY: all tests clean
all: $(BUILD)/glide2x.dll $(BUILD)/glide2-clear.exe
tests: $(SDK_TESTS)

$(BUILD)/include/fxinline.h $(BUILD)/include/fxgasm.h:
	mkdir -p $(BUILD)/include
	printf '%s\n' '#ifndef __FX_INLINE_H__' '#define __FX_INLINE_H__' '' \
	  '/* The # of 2-byte entries in the hw fog table */' \
	  '#define kInternalFogTableEntryCount 0x40' '' \
	  '#endif /* __FX_INLINE_H__ */' > $(BUILD)/include/fxinline.h
	: > $(BUILD)/include/fxgasm.h

$(BUILD)/%.o: %.c $(BUILD)/include/fxinline.h
	@mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -c $< -o $@

$(BUILD)/cpuid.o: $(SRC)/cpudetect_sim.c $(BUILD)/include/fxinline.h
	@mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -c $< -o $@

$(BUILD)/win32_io.o: win32_io.c
	@mkdir -p $(BUILD)
	$(CC) -O2 -c $< -o $@

$(BUILD)/exports.def: $(OBJS) gen-exports.sh
	bash gen-exports.sh $(OBJS) > $@

$(BUILD)/glide2x.dll: $(OBJS) $(BUILD)/exports.def
	$(CC) -o $@ $(DLL_LDFLAGS) $(OBJS) $(BUILD)/exports.def $(DLL_LIBS)

$(BUILD)/glide2-clear.exe: clear.c $(BUILD)/glide2x.dll
	$(CC) $(CFLAGS) -static-libgcc -o $@ clear.c -L$(BUILD) -lglide2x -Wl,--enable-stdcall-fixup

$(BUILD)/tests/glide2x.dll: $(BUILD)/glide2x.dll
	mkdir -p $(BUILD)/tests
	cp -f $< $@

$(BUILD)/tests/tlib.o: $(TESTS_DIR)/tlib.c $(BUILD)/include/fxinline.h
	mkdir -p $(BUILD)/tests
	$(CC) $(TEST_CFLAGS) -c $< -o $@

$(BUILD)/tests/%.o: $(TESTS_DIR)/%.c $(BUILD)/include/fxinline.h
	mkdir -p $(BUILD)/tests
	$(CC) $(TEST_CFLAGS) -c $< -o $@

$(BUILD)/tests/%.exe: $(BUILD)/tests/%.o $(BUILD)/tests/tlib.o $(BUILD)/tests/glide2x.dll
	$(CC) -static-libgcc -o $@ $(BUILD)/tests/$*.o $(BUILD)/tests/tlib.o \
		-L$(BUILD) -lglide2x -luser32 -lgdi32 -Wl,--enable-stdcall-fixup

clean:
	rm -rf $(BUILD)
