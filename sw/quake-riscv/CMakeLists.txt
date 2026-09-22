cmake_minimum_required(VERSION 3.16)
project(quake-riscv C)

# Overlay on sysprog21/quake-embedded. Their rv32emu snd.c uses scall;
# Tang/host use port/tang/snd_null.c and 320x200 software fillrect.

if(CMAKE_SYSTEM_PROCESSOR STREQUAL "riscv")
  enable_language(ASM)
endif()

set(QE ${CMAKE_CURRENT_SOURCE_DIR}/quake-embedded)

add_compile_options(-fno-common -std=gnu11)
add_compile_definitions(WINQUAKE_ENABLE_LOGGING WINQUAKE_LOGGING_EXTERNAL)
option(TANG_NET_NONE "Use WinQuake net_none (MCU / host SW smoke)" ON)

set(WQ_SRCS
  chase.c cmd.c common.c console.c crc.c cvar.c draw.c host.c host_cmd.c
  keys.c mathlib.c menu.c model.c nonintel.c screen.c sbar.c zone.c view.c
  wad.c world.c cl_demo.c cl_input.c cl_main.c cl_parse.c cl_tent.c
  d_edge.c d_fill.c d_init.c d_modech.c d_part.c d_polyse.c d_scan.c
  d_sky.c d_sprite.c d_surf.c d_vars.c d_zpoint.c net_loop.c net_main.c
  pr_cmds.c pr_edict.c pr_exec.c r_aclip.c r_alias.c r_bsp.c r_light.c
  r_draw.c r_efrag.c r_edge.c r_misc.c r_main.c r_sky.c r_sprite.c
  r_surf.c r_part.c r_vars.c sv_main.c sv_phys.c sv_move.c sv_user.c
)

if(CMAKE_SYSTEM_NAME MATCHES "(Darwin|Linux)" AND NOT TANG_NET_NONE)
  list(APPEND WQ_SRCS net_dgrm.c net_udp.c net_bsd.c)
else()
  list(APPEND WQ_SRCS net_none.c)
endif()

list(TRANSFORM WQ_SRCS PREPEND ${QE}/winquake/)

add_library(winquake OBJECT ${WQ_SRCS})
target_include_directories(winquake PUBLIC ${QE}/winquake)
target_compile_options(winquake PRIVATE -std=gnu11)
if(CMAKE_C_COMPILER_ID MATCHES "GNU|Clang")
  target_compile_options(winquake PRIVATE -Wdouble-promotion)
endif()

add_library(port OBJECT
  ${QE}/port/in_port.c
  ${QE}/port/cd_null.c
  port/tang/sys_port.c
  ${QE}/port/vid_port.c
  ${QE}/port/fio/fio_posix.c
)
target_include_directories(port PUBLIC ${QE}/include ${QE}/winquake)

add_executable(quake-sw
  port/tang/main.c
  port/tang/display.c
  port/tang/input.c
  port/tang/snd_null.c
  src/in_usb.c
)
target_include_directories(quake-sw PRIVATE
  include
  ${QE}/include
  ${QE}/winquake
)
target_link_libraries(quake-sw PRIVATE winquake port)

if(CMAKE_SYSTEM_PROCESSOR STREQUAL "riscv")
  target_sources(quake-sw PRIVATE
    port/tang/crt0.S
    port/tang/syscalls_a25.c
  )
  target_link_options(quake-sw PRIVATE
    -nostartfiles
    -T${CMAKE_CURRENT_SOURCE_DIR}/port/tang/a25.ld
    --specs=nano.specs
    -Wl,--gc-sections
    -Wl,-Map=$<TARGET_FILE_DIR:quake-sw>/quake-sw.map
    -Wl,--no-warn-rwx-segments
    -u _printf_float
  )
  target_link_libraries(quake-sw PRIVATE m c gcc)
  add_custom_command(TARGET quake-sw POST_BUILD
    COMMAND ${CMAKE_OBJCOPY} -O binary $<TARGET_FILE:quake-sw>
            $<TARGET_FILE_DIR:quake-sw>/quake-sw.bin
    COMMENT "quake-sw.bin for A25 DDR load"
  )
elseif(NOT APPLE)
  target_link_libraries(quake-sw PRIVATE m)
endif()
