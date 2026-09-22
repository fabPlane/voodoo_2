#ifndef TANG_MEM_H
#define TANG_MEM_H

/* Matches voodoo.gowin.GowinAddressMap. 1 GiB DDR3: 12 MiB Voodoo, 256 MiB
 * software Quake, 256 MiB V2GL replay, the rest free. */
#define TANG_CPU_RAM_BASE 0x80000000u
#define TANG_CPU_RAM_BYTES (256u * 1024u * 1024u)
#define TANG_CPU_DDR_PHYS (16u * 1024u * 1024u)
#define TANG_PAK_BASE 0x90000000u
#define TANG_PAK_BYTES (32u * 1024u * 1024u)
#define TANG_GLIDE_PLAY_PHYS 0x11000000u
#define TANG_GLIDE_PLAY_BYTES (256u * 1024u * 1024u)
#define TANG_SW_FB_WIDTH 320
#define TANG_SW_FB_HEIGHT 200

#endif
