# Host build of the mt32-t113 platform layer.
#
#   make                 fake engine only, no dependencies beyond a C compiler
#   make run             build and run the built-in demo
#   make MT32EMU_LIB=/path/to/libmt32emu.a MT32EMU_INC=/path/to/include
#                        also builds the real mt32emu engine
#
# There is a CMakeLists.txt beside this file that does the same thing and can
# build mt32emu itself; this Makefile exists so that a clean checkout with
# nothing but cc and make still produces a running binary.

CC      ?= cc
CXX     ?= c++
CFLAGS  ?= -O2 -g -Wall -Wextra -Wshadow -std=c99
CXXFLAGS?= -O2 -g -Wall -Wextra
INC     := -I../include
LDLIBS  := -lm

SRC_C := ../src/mtp_midi_parser.c \
         ../src/mtp_render.c \
         host_audio_wav.c \
         host_midi_file.c \
         host_storage_posix.c \
         host_time_posix.c \
         host_log_stderr.c \
         engine_fake.c \
         main.c

OBJ := $(addprefix build/,$(notdir $(SRC_C:.c=.o)))
LINK := $(CC)

ifdef MT32EMU_LIB
CFLAGS   += -DMTP_WITH_MT32EMU
CXXFLAGS += -DMTP_WITH_MT32EMU
OBJ      += build/engine_mt32emu.o
LDLIBS   := $(MT32EMU_LIB) $(LDLIBS)
LINK     := $(CXX)
MT32EMU_INC ?= .
endif

all: build/mtp_host

build/mtp_host: $(OBJ)
	@mkdir -p $(dir $@)
	$(LINK) -o $@ $(OBJ) $(LDLIBS)

VPATH := ../src

build/%.o: %.c
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) $(INC) -c -o $@ $<

build/engine_mt32emu.o: engine_mt32emu.cpp
	@mkdir -p $(dir $@)
	$(CXX) $(CXXFLAGS) $(INC) -I$(MT32EMU_INC) -c -o $@ $<

run: all
	cd build && ./mtp_host --seconds 2

test: all
	cd build && ./mtp_host --seconds 2 --wav demo.wav && \
	  ls -l demo.wav

clean:
	rm -rf build

.PHONY: all run test clean
