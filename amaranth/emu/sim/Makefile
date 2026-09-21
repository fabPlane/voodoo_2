# Verilator model + harness for the AMARANTH CoreSim.
#
# Out-of-tree copy of ext/SpinalVoodoo/emu/sim/Makefile (the submodule is
# read-only; see docs/amaranth-rewrite-plan.md §4.3 and
# docs/amaranth/spinal-naming-and-harness.md §3).
#
# Differences from upstream, and only these:
#
#   * the `$(RTL_FILE)` rule is GONE.  Upstream regenerates CoreSim.v by
#     running `java … voodoo.CoreSimGen`; if it were still here, a stale
#     timestamp would happily overwrite the Amaranth RTL with the Spinal
#     one.  RTL_FILE is now an input: `make -C amaranth rtl-core-sim`
#     produces it.  Missing or stale -> this Makefile says so and stops.
#   * SCALA_SRCS / SCALA_CLI / SIM_GEN_CLASS / the `$readmemb` sed are gone
#     with it.  Amaranth inlines memory initialisation, so there are no
#     `.bin` sidecars to rewrite.
#   * SIM_TOP is always CoreSim (no CoreDe10 interface here).  The upstream
#     top Makefile still passes SIM_INTERFACE=core down; it is accepted and
#     ignored, anything else is an error.
#   * TRACE_DIR points back into the submodule: voodoo_trace_writer.[ch] and
#     the trace format header are shared verbatim, not forked.
#
#   * VERILATOR_FLAGS gains -Wno-fatal (Phase 7.3; see the flags below).
#
# Everything else is deliberately identical to upstream — VERILATOR_FLAGS
# (including --public-flat-rw, which is what exposes the framebuffer and
# texture arrays to the harness), the -m32 CXX32 build, the OBJ_DIR layout
# and the object-file names (sim_harness.o, verilated.o,
# voodoo_trace_writer.o, VCoreSim__ALL.a).  The Glide Makefile.sim looks for
# those exact paths under $(SIM_DIR).
#
# Usage:
#   make all          # build everything (model + harness objects)
#   make clean
# Normally driven from amaranth/Makefile (`make sim-model` / `sim-build`).

.PHONY: all model harness trace-harness smoke clean

# amaranth/emu/sim -> repo root
REPO_ROOT = $(abspath $(CURDIR)/../../..)
UPSTREAM  = $(REPO_ROOT)/ext/SpinalVoodoo
AM_ROOT   = $(abspath $(CURDIR)/../..)

RTL_DIR     = $(AM_ROOT)/rtl
OBJ_DIR     = obj_dir
HARNESS_DIR = harness
TRACE_DIR   = $(UPSTREAM)/emu/trace

SIM_INTERFACE ?= core
ifneq ($(SIM_INTERFACE),core)
$(error amaranth/emu/sim only builds the CoreSim interface, not SIM_INTERFACE=$(SIM_INTERFACE))
endif

SIM_TOP     = CoreSim
RTL_FILE    = $(RTL_DIR)/$(SIM_TOP).v
SIM_LIB     = $(OBJ_DIR)/V$(SIM_TOP)__ALL.a
SIM_MK      = V$(SIM_TOP).mk
HARNESS_SRC = $(HARNESS_DIR)/sim_harness.cpp

# Verilator settings — upstream's verbatim plus -Wno-fatal (see the note
# above).  The Amaranth/yosys CoreSim.v carries ~150 warnings the Spinal one
# does not: CASEINCOMPLETE on every yosys `casez`, ASCRANGE `[-1:0]`, three
# LATCH false positives on the gamma-LUT `full_case`, and three UNOPTFLAT
# false loops (the scanout CDC FIFO's gray-to-binary chains, the TMU's
# compareMissSel priority chain).  Verilator treats warnings as fatal by
# default, so without -Wno-fatal the real core (Phase 7.2) never verilates —
# the Phase 0.5 stub was small enough not to trip any.  Same flag
# tools/diff/harness.py and the Makefile's sim-trace-check pass.
VERILATOR = verilator
VERILATOR_ROOT ?= $(shell $(VERILATOR) --getenv VERILATOR_ROOT)
VERILATOR_FLAGS = --cc --output-split 20000 --output-split-cfuncs 5000 --decorations none --public-flat-rw --x-assign fast --x-initial fast --noassert -Wno-fatal -Wno-WIDTHTRUNC -Wno-WIDTHEXPAND -Wno-CMPCONST -Wno-UNSIGNED
ifeq ($(TRACE),1)
VERILATOR_FLAGS += --trace-fst --trace-structs --trace-underscore
endif

# 32-bit compilation (must match Glide's 32-bit build)
CC32 ?= gcc -m32
CXX32 ?= $(shell d=$$(dirname "$(firstword $(CC32))") && [ -x "$$d/g++" ] && echo "$$d/g++" || echo "g++ -m32")

CCACHE := $(shell command -v ccache 2>/dev/null)
ifdef CCACHE
  OBJCACHE = ccache
endif

CXXFLAGS = -O3 -DVL_TIME_CONTEXT
ifeq ($(TRACE),1)
CXXFLAGS += -DVM_TRACE_FST
endif
CXXFLAGS += -I$(OBJ_DIR) -I$(VERILATOR_ROOT)/include -I$(VERILATOR_ROOT)/include/vltstd -I$(TRACE_DIR)

TRACE_IO_FLAGS = -D_FILE_OFFSET_BITS=64 -D_LARGEFILE_SOURCE -D_LARGEFILE64_SOURCE

VERILATE_STAMP = $(OBJ_DIR)/.$(SIM_TOP).verilate.stamp

# Trace mode sentinel: forces rebuild when TRACE= changes.  Same mechanism
# (and same file name) as upstream, because the top Makefile and the Glide
# Makefile.sim both depend on $(SIM_DIR)/obj_dir/.trace_mode.
TRACE_MODE_FILE = $(OBJ_DIR)/.trace_mode
TRACE_MODE_VAL  = $(if $(filter 1,$(TRACE)),trace,notrace)
$(shell mkdir -p $(OBJ_DIR))
$(shell [ -f $(TRACE_MODE_FILE) ] && [ "$$(cat $(TRACE_MODE_FILE))" = "$(TRACE_MODE_VAL)" ] || echo "$(TRACE_MODE_VAL)" > $(TRACE_MODE_FILE))

###############################################################################
# Targets
###############################################################################

all: model harness

# Step 1 upstream (generate Verilog from SpinalHDL) has no counterpart here.
# The RTL is an input; produce it with `make -C amaranth rtl-core-sim`.
$(RTL_FILE):
	@echo "amaranth/emu/sim: $(RTL_FILE) is missing." >&2
	@echo "  run:  make -C $(AM_ROOT) rtl-core-sim" >&2
	@false

# Step 2: Run Verilator
$(VERILATE_STAMP): $(RTL_FILE) $(TRACE_MODE_FILE)
	$(VERILATOR) $(VERILATOR_FLAGS) $(RTL_FILE) --Mdir $(OBJ_DIR)
	@touch $@

# Step 3: Build Verilator model library
model: $(SIM_LIB)

$(SIM_LIB): $(VERILATE_STAMP)
	$(MAKE) -j$(shell nproc 2>/dev/null || sysctl -n hw.ncpu) -C $(OBJ_DIR) -f $(SIM_MK) \
		CXX="$(CXX32)" \
		OBJCACHE="$(OBJCACHE)" \
		OPT_FAST="-O3" \
		OPT_SLOW="" \
		OPT_GLOBAL="-DVL_USER_WARN"

# Step 4: Build harness object + verilated runtime
HARNESS_OBJS = $(OBJ_DIR)/sim_harness.o $(OBJ_DIR)/verilated.o $(OBJ_DIR)/voodoo_trace_writer.o
ifeq ($(TRACE),1)
HARNESS_OBJS += $(OBJ_DIR)/verilated_fst_c.o
endif
harness: $(HARNESS_OBJS)

$(OBJ_DIR)/sim_harness.o: $(HARNESS_SRC) $(HARNESS_DIR)/sim_harness.h $(TRACE_MODE_FILE) | $(VERILATE_STAMP)
	$(OBJCACHE) $(CXX32) $(CXXFLAGS) -DVL_USER_WARN -c -o $@ $<

$(OBJ_DIR)/verilated.o: $(VERILATOR_ROOT)/include/verilated.cpp $(TRACE_MODE_FILE) | $(VERILATE_STAMP)
	$(OBJCACHE) $(CXX32) $(CXXFLAGS) -DVL_USER_WARN -c -o $@ $<

$(OBJ_DIR)/verilated_fst_c.o: $(VERILATOR_ROOT)/include/verilated_fst_c.cpp $(TRACE_MODE_FILE) | $(VERILATE_STAMP)
	$(OBJCACHE) $(CXX32) $(CXXFLAGS) -c -o $@ $<

# Step 5: Trace-capture harness (no Verilator dependency).  Unmodified copy
# of the upstream source: it only calls the public sim_* API.
trace-harness: $(OBJ_DIR)/sim_trace_harness.o $(OBJ_DIR)/voodoo_trace_writer.o

$(OBJ_DIR)/voodoo_trace_writer.o: $(TRACE_DIR)/voodoo_trace_writer.c $(TRACE_DIR)/voodoo_trace_writer.h $(TRACE_DIR)/voodoo_trace_format.h
	$(OBJCACHE) $(CXX32) $(CXXFLAGS) $(TRACE_IO_FLAGS) -I$(TRACE_DIR) -c -o $@ $<

$(OBJ_DIR)/sim_trace_harness.o: $(HARNESS_DIR)/sim_trace_harness.c $(HARNESS_DIR)/sim_harness.h $(TRACE_DIR)/voodoo_trace_writer.h $(TRACE_DIR)/voodoo_trace_format.h
	$(OBJCACHE) $(CXX32) $(CXXFLAGS) $(TRACE_IO_FLAGS) -I$(TRACE_DIR) -c -o $@ $<

# Step 6 (not upstream): standalone smoke test of the C API.  Links exactly
# what the Glide backend links, minus libglide2x / X11 / zlib, so the harness
# can be validated on a box that cannot build the Glide stack — and so an
# Amaranth rename that moves the public memory arrays fails here, at compile
# time, instead of silently producing black screenshots.
SMOKE_BIN = $(OBJ_DIR)/sim_smoke

smoke: $(SMOKE_BIN)
	$(SMOKE_BIN)

$(SMOKE_BIN): $(HARNESS_DIR)/sim_smoke.cpp $(HARNESS_DIR)/sim_harness.h $(HARNESS_OBJS) $(SIM_LIB)
	$(OBJCACHE) $(CXX32) $(CXXFLAGS) -c -o $(OBJ_DIR)/sim_smoke.o $<
	$(CXX32) -o $@ $(OBJ_DIR)/sim_smoke.o $(HARNESS_OBJS) $(OBJ_DIR)/verilated_threads.o \
		-Wl,--whole-archive $(SIM_LIB) -Wl,--no-whole-archive -lm -lstdc++

clean:
	rm -rf $(OBJ_DIR)
