SpinalVoodoo on Tang Retro Console 138K
=======================================

TangCore only loads FPGA bitstreams from:

  cores/console138k/<name>.bin

The live core is:

  cores/console138k/voodootang.bin

Select core -> voodootang. HDMI 640x480 after DDR3 trains.

USB pad (SNES layout from BL616 command 0x09): D-pad moves, A/Start
runs, B/Select returns. Onboard USER cycles, RESET launches.

Menu:
  3DFX LOGO SPIN      SST-1 chevron (playable, loops)
  GLIDE TEST04        gouraud triangle (playable)
  GLIDE TEST16        shameless plug / splash (playable)
  GLIDE ALPHABET      textured glyphs (playable)
  OTHER DEMOS         submenu: Tomb, Screamer 2, Extreme Assault,
                      Carmageddon, Archimedean Dynasty, Toshinden,
                      NASCAR 2, GTA, Lords of the Realm 2, Monster 3D
                      (info pages; archives in sw/glide-demos/)

stills/  CoreSim vs 86Box reference PNGs
traces/  packed Glide dumps (documentation; bitstream has its own copies)
