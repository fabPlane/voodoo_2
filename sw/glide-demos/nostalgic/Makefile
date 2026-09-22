# nostalgic-glide-demo -> SpinalVoodoo FPGA simulator
#
# Builds efliks' open-source (GPLv3) 3Dfx Glide demo against OUR sim Glide
# backend (trace-capture libglide2x), runs it headless to record the Glide
# command stream, and renders a PNG through the 86Box reference model.
#
# This is the clean answer to the "glide2x passthrough" problem: rather than
# fighting DOSBox-X's port-0x600 passthrough with closed demos that statically
# link a real-PCI Glide, we take an OPEN-SOURCE program that calls Glide through
# the standard library and link it directly against our libglide2x. No DOSBox,
# no PCI probing, no binary patching -- the exact same path our SDK tests and
# homebrew demos use.
#
#   make            # build + capture torus trace
#   make render     # render trace -> PNG (shots/)
#   make FRAMES=4   # fewer frames = smaller trace / faster render
#   make cube       # textured cube (requires the external texture pack; see README)
#   make clean

EXT     ?= ../../../ext/SpinalVoodoo
NGDIR   ?= ../../../ext/nostalgic-glide-demo
G2X      = $(EXT)/emu/glide/glide2x
GSRC     = $(G2X)/sst1/glide/src
SWLIBS   = $(EXT)/emu/glide/swlibs
LIBDIR   = $(G2X)/sst1/lib/sst1

CC32     ?= gcc -m32
CC       := $(CC32)
INC       = -I$(NGDIR) -I$(GSRC) -I$(G2X)/sst1/incsrc -I$(G2X)/sst1/init \
            -I$(SWLIBS)/fxmisc -I$(SWLIBS)/newpci/pcilib
CFLAGS    = -O2 -ffast-math -w -std=gnu99 -DSST1 -DSIM_BACKEND -DGLIDE_BUILD $(INC)
LDLIBS    = -lglide2x -lm -lstdc++

# Upstream sources that make up the Glide render path (all portable C -- the
# DOS-only bits live in sys.c/vga13h.c which we replace with linux_sys.c).
NGSRC = bumpobj rsglide math3d envmap texman texglide bitmap bumpmain template
NGOBJS = $(addprefix $(NGDIR)/,$(addsuffix .c,$(NGSRC)))

FRAMES ?= 32
SETARCH := $(shell command -v setarch 2>/dev/null)
RUN      = $(if $(SETARCH),$(SETARCH) -R env,env)

.PHONY: all render cube clean glide-lib
all: traces/torus.bin

# Build the sim Glide backend once, in trace-capture mode.
glide-lib:
	$(MAKE) -C $(EXT) native/trace/build

build/nostalgic.exe: driver.c linux_sys.c $(NGOBJS) | glide-lib
	@mkdir -p build
	$(CC) $(CFLAGS) driver.c linux_sys.c $(NGOBJS) -o $@ -L$(LIBDIR) $(LDLIBS)

# Run from the submodule dir so assets/models/torus.3d resolves.
traces/torus.bin: build/nostalgic.exe
	@mkdir -p traces
	@echo "  CAPTURE torus ($(FRAMES) frames)"
	@cd $(NGDIR) && $(RUN) LD_LIBRARY_PATH=$(abspath $(LIBDIR)) \
		NG_OBJ=torus SIM_TRACE_FILE=$(abspath $@) \
		$(abspath build/nostalgic.exe) $(FRAMES) </dev/null >/dev/null 2>&1
	@ls -la $@

cube: build/nostalgic.exe
	@mkdir -p traces
	@echo "  CAPTURE cube ($(FRAMES) frames) -- needs external texture pack"
	@cd $(NGDIR) && $(RUN) LD_LIBRARY_PATH=$(abspath $(LIBDIR)) \
		NG_OBJ=cube SIM_TRACE_FILE=$(abspath traces/cube.bin) \
		$(abspath build/nostalgic.exe) $(FRAMES) </dev/null

# Render captured traces to PNG via the 86Box reference model (reuses the
# homebrew render helper's trace_test build).
TT = $(EXT)/emu/test/obj_dir/trace_test
render: traces/torus.bin
	@# Bootstrap the reference renderer (CoreSim tie-off + 86Box + trace_test)
	@# via the homebrew helper only if trace_test hasn't been built yet.
	@test -x $(TT) || RENDER_ONLY_BUILD=1 bash ../../glide-homebrew/render/render.sh >/dev/null 2>&1 || true
	@mkdir -p shots
	@for t in traces/*.bin; do \
		n=$$(basename $$t .bin); \
		echo "  RENDER $$n"; \
		$(TT) $(abspath $$t) --ref-only --output-dir $(abspath shots) >shots/$$n.log 2>&1; \
	done
	@ls -la shots/*.png

clean:
	rm -rf build traces shots
