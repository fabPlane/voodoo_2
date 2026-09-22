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

.PHONY: all clean
all: $(BUILD)/glide2x.dll $(BUILD)/glide2-clear.exe

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

$(BUILD)/glide2x.dll: $(OBJS)
	$(CC) -o $@ $(DLL_LDFLAGS) $(OBJS) $(DLL_LIBS)

$(BUILD)/glide2-clear.exe: clear.c $(BUILD)/glide2x.dll
	$(CC) $(CFLAGS) -static-libgcc -o $@ clear.c -L$(BUILD) -lglide2x -Wl,--enable-stdcall-fixup

clean:
	rm -rf $(BUILD)
