# Host build of the mt32-t113 platform layer.
#
#   cmake -S . -B build && cmake --build build && ./build/mtp_host
#
# To link the real engine, point MT32EMU_SOURCE_DIR at the vendored Munt
# (workstream A's clone) and it will be built as a static library beside us:
#
#   cmake -S . -B build -DMT32EMU_SOURCE_DIR=../../bench/vendor/munt/mt32emu
#
# Nothing is written into that directory; CMake builds it out of source.

cmake_minimum_required(VERSION 3.13)
project(mtp_host C CXX)

set(CMAKE_C_STANDARD 99)
set(CMAKE_C_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

add_executable(mtp_host
  ../src/mtp_midi_parser.c
  ../src/mtp_render.c
  host_audio_wav.c
  host_midi_file.c
  host_storage_posix.c
  host_time_posix.c
  host_log_stderr.c
  engine_fake.c
  main.c
)
target_include_directories(mtp_host PRIVATE ../include)
if(CMAKE_C_COMPILER_ID MATCHES "GNU|Clang")
  target_compile_options(mtp_host PRIVATE -Wall -Wextra -Wshadow)
endif()

find_library(M_LIB m)
if(M_LIB)
  target_link_libraries(mtp_host PRIVATE ${M_LIB})
endif()

# Optional: the real engine.
set(MT32EMU_SOURCE_DIR "" CACHE PATH "Path to munt/mt32emu")
if(MT32EMU_SOURCE_DIR AND EXISTS "${MT32EMU_SOURCE_DIR}/CMakeLists.txt")
  # Static, no tests, no shared object: the same shape the target build wants.
  set(BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)
  set(BUILD_TESTING OFF CACHE BOOL "" FORCE)
  set(libmt32emu_SHARED OFF CACHE BOOL "" FORCE)
  add_subdirectory(${MT32EMU_SOURCE_DIR} ${CMAKE_BINARY_DIR}/mt32emu EXCLUDE_FROM_ALL)
  target_sources(mtp_host PRIVATE engine_mt32emu.cpp)
  target_compile_definitions(mtp_host PRIVATE MTP_WITH_MT32EMU)
  target_link_libraries(mtp_host PRIVATE MT32Emu::mt32emu)
  message(STATUS "mtp_host: linking mt32emu from ${MT32EMU_SOURCE_DIR}")
else()
  message(STATUS "mtp_host: fake engine only "
                 "(-DMT32EMU_SOURCE_DIR=<path to munt/mt32emu> to add the real one)")
endif()

enable_testing()
add_test(NAME demo COMMAND mtp_host --seconds 1 --wav ${CMAKE_BINARY_DIR}/test.wav)
