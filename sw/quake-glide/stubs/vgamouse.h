/* svgalib mouse stub for headless GLQuake; events come from webinput.h. */
#ifndef VGAMOUSE_STUB_H
#define VGAMOUSE_STUB_H
#include "webinput.h"
#define MOUSE_MICROSOFT        1
#define MOUSE_MOUSESYSTEMS     2
#define MOUSE_MMSERIES         3
#define MOUSE_LOGITECH         4
#define MOUSE_BUSMOUSE         5
#define MOUSE_PS2              6
#define MOUSE_LEFTBUTTON       4
#define MOUSE_MIDDLEBUTTON     2
#define MOUSE_RIGHTBUTTON      1
#define MOUSE_DEFAULTSAMPLERATE 150
typedef void (*__mouse_handler)(int button, int dx, int dy);
static inline int  mouse_init(char *dev, int type, int rate){ (void)dev;(void)type;(void)rate; return 0; }
static inline void mouse_seteventhandler(__mouse_handler h){ webinput_mouse = h; }
static inline void mouse_close(void){}
static inline int  mouse_update(void){ return 0; }  /* keyboard_update drains both */
static inline int  vga_getmousetype(void){ return MOUSE_PS2; }
#endif
