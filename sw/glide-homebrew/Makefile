# Homebrew Glide demos for the SpinalVoodoo core (Sipeed Tang Retro Console 138K)
#
#   make            # build all demos, capture traces, pack console ROMs
#   make summary    # print the Glide command mix captured from each demo
#   make roms       # just (re)pack ROMs from captured traces
#   make clean      # remove build/ and traces/ (roms/ are committed)
#
# Each demo is compiled 32-bit against the SpinalVoodoo sim Glide backend in
# trace-capture mode, run once to record its Glide command stream, then packed
# into the flat addr:data ROM that the on-chip DemoAxiLitePlayer replays.
# No FPGA or 32-bit-hostless dependency beyond the multilib toolchain.

EXT      ?= ../../ext/SpinalVoodoo
G2X       = $(EXT)/emu/glide/glide2x
GSRC      = $(G2X)/sst1/glide/src
SWLIBS    = $(EXT)/emu/glide/swlibs
LIBDIR    = $(G2X)/sst1/lib/sst1

CC32     ?= gcc -m32
CXX32    ?= g++ -m32
CC       := $(CC32)
CXX      := $(CXX32)

INC  = -I$(GSRC) -I$(G2X)/sst1/incsrc -I$(G2X)/sst1/init \
       -I$(SWLIBS)/fxmisc -I$(SWLIBS)/newpci/pcilib
CFLAGS = -Wall -O2 -ffast-math -DSST1 -DSIM_BACKEND $(INC)
LDLIBS = -lglide2x -lm -lstdc++

# Optional frame count passed to each demo (default: each demo's own default).
FRAMES ?=

APPS   := $(sort $(patsubst apps/%.c,%,$(wildcard apps/hb*.c)))
EXES   := $(addprefix build/,$(addsuffix .exe,$(APPS)))
TRACES := $(addprefix traces/,$(addsuffix .bin,$(APPS)))
ROMS   := $(addprefix roms/,$(addsuffix .bin,$(APPS)))

# Create output dirs at parse time so they can't collide with phony names.
$(shell mkdir -p build traces roms)

.PHONY: all roms traces summary clean glide-lib
all: roms

roms: $(ROMS)
traces: $(TRACES)

# Build the sim Glide backend once, in trace-capture mode (no Verilator model).
glide-lib: build/.glidelib.stamp
build/.glidelib.stamp:
	$(MAKE) -C $(EXT) native/trace/build
	touch $@

build/%.exe: apps/%.c apps/hbcommon.h build/.glidelib.stamp
	$(CXX) $(CFLAGS) $< -o $@ -L$(LIBDIR) $(LDLIBS)

# setarch -R disables ASLR so a pointer-dependent code path in the sim Glide
# backend is deterministic, giving byte-reproducible ROMs across runs.
SETARCH := $(shell command -v setarch 2>/dev/null)
RUN      = $(if $(SETARCH),$(SETARCH) -R env,env)

traces/%.bin: build/%.exe
	@echo "  CAPTURE $*"
	@$(RUN) LD_LIBRARY_PATH=$(LIBDIR) SIM_TRACE_FILE=$(abspath $@) \
		$< $(FRAMES) </dev/null >build/$*.log 2>&1 \
		|| { echo "  FAILED $* (see build/$*.log)"; tail -5 build/$*.log; exit 1; }

roms/%.bin: traces/%.bin
	@python3 tools/pack_trace.py $< -o $@

summary: $(TRACES)
	@for t in $(TRACES); do python3 tools/pack_trace.py $$t --summary; done

clean:
	rm -rf build traces
