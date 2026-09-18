# Install script for directory: /home/user/vodoo_2/mt32-t113/bench/vendor/munt/mt32emu

# Set the install prefix
if(NOT DEFINED CMAKE_INSTALL_PREFIX)
  set(CMAKE_INSTALL_PREFIX "/usr/local")
endif()
string(REGEX REPLACE "/$" "" CMAKE_INSTALL_PREFIX "${CMAKE_INSTALL_PREFIX}")

# Set the install configuration name.
if(NOT DEFINED CMAKE_INSTALL_CONFIG_NAME)
  if(BUILD_TYPE)
    string(REGEX REPLACE "^[^A-Za-z0-9_]+" ""
           CMAKE_INSTALL_CONFIG_NAME "${BUILD_TYPE}")
  else()
    set(CMAKE_INSTALL_CONFIG_NAME "Release")
  endif()
  message(STATUS "Install configuration: \"${CMAKE_INSTALL_CONFIG_NAME}\"")
endif()

# Set the component getting installed.
if(NOT CMAKE_INSTALL_COMPONENT)
  if(COMPONENT)
    message(STATUS "Install component: \"${COMPONENT}\"")
    set(CMAKE_INSTALL_COMPONENT "${COMPONENT}")
  else()
    set(CMAKE_INSTALL_COMPONENT)
  endif()
endif()

# Install shared libraries without execute permission?
if(NOT DEFINED CMAKE_INSTALL_SO_NO_EXE)
  set(CMAKE_INSTALL_SO_NO_EXE "1")
endif()

# Is this installation the result of a crosscompile?
if(NOT DEFINED CMAKE_CROSSCOMPILING)
  set(CMAKE_CROSSCOMPILING "FALSE")
endif()

# Set default install directory permissions.
if(NOT DEFINED CMAKE_OBJDUMP)
  set(CMAKE_OBJDUMP "/usr/bin/objdump")
endif()

if(CMAKE_INSTALL_COMPONENT STREQUAL "Devel" OR NOT CMAKE_INSTALL_COMPONENT)
  file(INSTALL DESTINATION "${CMAKE_INSTALL_PREFIX}/lib" TYPE STATIC_LIBRARY FILES "/home/user/vodoo_2/mt32-t113/bench/build-host/mt32emu/libmt32emu.a")
endif()

if(CMAKE_INSTALL_COMPONENT STREQUAL "Devel" OR NOT CMAKE_INSTALL_COMPONENT)
  file(INSTALL DESTINATION "${CMAKE_INSTALL_PREFIX}/include" TYPE DIRECTORY FILES "/home/user/vodoo_2/mt32-t113/bench/build-host/mt32emu/include/mt32emu")
endif()

if(CMAKE_INSTALL_COMPONENT STREQUAL "Devel" OR NOT CMAKE_INSTALL_COMPONENT)
  file(INSTALL DESTINATION "${CMAKE_INSTALL_PREFIX}/include" TYPE FILE FILES "/home/user/vodoo_2/mt32-t113/bench/build-host/mt32emu/include/mt32emu.h")
endif()

if(CMAKE_INSTALL_COMPONENT STREQUAL "Devel" OR NOT CMAKE_INSTALL_COMPONENT)
  file(INSTALL DESTINATION "${CMAKE_INSTALL_PREFIX}/lib/pkgconfig" TYPE FILE FILES "/home/user/vodoo_2/mt32-t113/bench/build-host/mt32emu/mt32emu.pc")
endif()

if(CMAKE_INSTALL_COMPONENT STREQUAL "Devel" OR NOT CMAKE_INSTALL_COMPONENT)
  if(EXISTS "$ENV{DESTDIR}${CMAKE_INSTALL_PREFIX}/lib/cmake/MT32Emu/MT32EmuTargets.cmake")
    file(DIFFERENT _cmake_export_file_changed FILES
         "$ENV{DESTDIR}${CMAKE_INSTALL_PREFIX}/lib/cmake/MT32Emu/MT32EmuTargets.cmake"
         "/home/user/vodoo_2/mt32-t113/bench/build-host/mt32emu/CMakeFiles/Export/e0b64b0db13410be25f9f48f15d8db90/MT32EmuTargets.cmake")
    if(_cmake_export_file_changed)
      file(GLOB _cmake_old_config_files "$ENV{DESTDIR}${CMAKE_INSTALL_PREFIX}/lib/cmake/MT32Emu/MT32EmuTargets-*.cmake")
      if(_cmake_old_config_files)
        string(REPLACE ";" ", " _cmake_old_config_files_text "${_cmake_old_config_files}")
        message(STATUS "Old export file \"$ENV{DESTDIR}${CMAKE_INSTALL_PREFIX}/lib/cmake/MT32Emu/MT32EmuTargets.cmake\" will be replaced.  Removing files [${_cmake_old_config_files_text}].")
        unset(_cmake_old_config_files_text)
        file(REMOVE ${_cmake_old_config_files})
      endif()
      unset(_cmake_old_config_files)
    endif()
    unset(_cmake_export_file_changed)
  endif()
  file(INSTALL DESTINATION "${CMAKE_INSTALL_PREFIX}/lib/cmake/MT32Emu" TYPE FILE FILES "/home/user/vodoo_2/mt32-t113/bench/build-host/mt32emu/CMakeFiles/Export/e0b64b0db13410be25f9f48f15d8db90/MT32EmuTargets.cmake")
  if(CMAKE_INSTALL_CONFIG_NAME MATCHES "^([Rr][Ee][Ll][Ee][Aa][Ss][Ee])$")
    file(INSTALL DESTINATION "${CMAKE_INSTALL_PREFIX}/lib/cmake/MT32Emu" TYPE FILE FILES "/home/user/vodoo_2/mt32-t113/bench/build-host/mt32emu/CMakeFiles/Export/e0b64b0db13410be25f9f48f15d8db90/MT32EmuTargets-release.cmake")
  endif()
endif()

if(CMAKE_INSTALL_COMPONENT STREQUAL "Devel" OR NOT CMAKE_INSTALL_COMPONENT)
  file(INSTALL DESTINATION "${CMAKE_INSTALL_PREFIX}/lib/cmake/MT32Emu" TYPE FILE FILES
    "/home/user/vodoo_2/mt32-t113/bench/build-host/mt32emu/cmake/MT32EmuConfig.cmake"
    "/home/user/vodoo_2/mt32-t113/bench/build-host/mt32emu/cmake/MT32EmuConfigVersion.cmake"
    )
endif()

if(CMAKE_INSTALL_COMPONENT STREQUAL "Runtime" OR NOT CMAKE_INSTALL_COMPONENT)
  file(INSTALL DESTINATION "${CMAKE_INSTALL_PREFIX}/share/doc/munt/libmt32emu" TYPE FILE FILES
    "/home/user/vodoo_2/mt32-t113/bench/vendor/munt/mt32emu/AUTHORS.txt"
    "/home/user/vodoo_2/mt32-t113/bench/vendor/munt/mt32emu/COPYING.txt"
    "/home/user/vodoo_2/mt32-t113/bench/vendor/munt/mt32emu/COPYING.LESSER.txt"
    "/home/user/vodoo_2/mt32-t113/bench/vendor/munt/mt32emu/NEWS.txt"
    "/home/user/vodoo_2/mt32-t113/bench/vendor/munt/mt32emu/README.md"
    "/home/user/vodoo_2/mt32-t113/bench/vendor/munt/mt32emu/TODO.txt"
    )
endif()

