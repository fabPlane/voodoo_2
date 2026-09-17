# mt32-t113 bench: real-time-factor harness for Munt's mt32emu.
#
# Builds mt32emu as a STATIC library (the shape the T113 port will use) and
# links the headless harness against it.
#
# Host:
#   cmake -S bench -B bench/build-host -DCMAKE_BUILD_TYPE=Release
#   cmake --build bench/build-host -j
#
# armv7-a / Cortex-A7 / NEON-VFPv4 / hard float:
#   cmake -S bench -B bench/build-armv7 -DCMAKE_BUILD_TYPE=Release \
#         -DCMAKE_TOOLCHAIN_FILE=$PWD/bench/cmake/toolchain-armv7a-neon.cmake
#   cmake --build bench/build-armv7 -j

cmake_minimum_required(VERSION 3.13)
project(mt32_t113_bench CXX C)

set(MUNT_DIR "${CMAKE_CURRENT_SOURCE_DIR}/vendor/munt")
if(NOT EXISTS "${MUNT_DIR}/mt32emu/CMakeLists.txt")
  message(FATAL_ERROR
    "Munt is not vendored. It is deliberately not committed to this repo.\n"
    "Run:  git clone https://github.com/munt/munt.git ${MUNT_DIR}\n"
    "The commit this bench was written against is recorded in bench/README.md.")
endif()

if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
  set(CMAKE_BUILD_TYPE Release CACHE STRING "" FORCE)
endif()

set(CMAKE_CXX_STANDARD 98)          # mt32emu targets C++98; do not force newer
set(CMAKE_CXX_EXTENSIONS OFF)

# --- how mt32emu is configured -------------------------------------------
# A static library with no C wrapper: that is what a bare-metal or RTOS image
# wants, and it lets the linker garbage-collect what we never call.
set(BUILD_SHARED_LIBS      OFF CACHE BOOL "" FORCE)
set(libmt32emu_SHARED      OFF CACHE BOOL "" FORCE)
set(BUILD_TESTING          OFF CACHE BOOL "" FORCE)
set(libmt32emu_BUILD_TESTING OFF CACHE BOOL "" FORCE)
set(libmt32emu_C_INTERFACE OFF CACHE BOOL "" FORCE)
set(libmt32emu_CPP_INTERFACE ON  CACHE BOOL "" FORCE)
# The internal resampler is what --sample-rate uses when it differs from the
# synth's native 32 kHz. Keep it: on the board we will very likely run the DAC
# at 48 kHz, so its cost belongs in the measurement.
set(libmt32emu_WITH_INTERNAL_RESAMPLER ON CACHE BOOL "" FORCE)
set(libmt32emu_WITH_VERSION_TAGGING OFF CACHE BOOL "" FORCE)

add_subdirectory("${MUNT_DIR}/mt32emu" "${CMAKE_CURRENT_BINARY_DIR}/mt32emu" EXCLUDE_FROM_ALL)

# --- the harness ----------------------------------------------------------

add_executable(rtf rtf.cpp)
target_link_libraries(rtf PRIVATE mt32emu)

if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
  target_compile_options(rtf PRIVATE -Wall -Wextra)
  # -ffast-math is deliberately NOT used: mt32emu's float renderer is an
  # accuracy model, and reassociation would change the audio. If you want to
  # know what it buys, measure it as a separate build, do not make it default.
endif()

# clock_gettime lives in librt on older glibc; harmless where it does not.
include(CheckLibraryExists)
check_library_exists(rt clock_gettime "" HAVE_LIBRT)
if(HAVE_LIBRT)
  target_link_libraries(rtf PRIVATE rt)
endif()

# --- footprint ------------------------------------------------------------
# `make footprint` prints size(1) for the library and the linked harness.
# This works without ROMs and feeds the SRAM-vs-DRAM decision.

find_program(SIZE_TOOL
  NAMES ${CMAKE_SIZE} "${CMAKE_CXX_COMPILER_TARGET}-size" size
  HINTS ${CMAKE_BINARY_DIR})
if(SIZE_TOOL)
  add_custom_target(footprint
    COMMAND ${CMAKE_COMMAND} -E echo "== size(1): libmt32emu.a, per object =="
    COMMAND ${SIZE_TOOL} -t $<TARGET_FILE:mt32emu>
    COMMAND ${CMAKE_COMMAND} -E echo ""
    COMMAND ${CMAKE_COMMAND} -E echo "== size(1): linked harness =="
    COMMAND ${SIZE_TOOL} $<TARGET_FILE:rtf>
    DEPENDS rtf mt32emu
    VERBATIM)
endif()
