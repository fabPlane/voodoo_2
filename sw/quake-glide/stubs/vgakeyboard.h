/* svgalib keyboard stub for headless GLQuake; events come from webinput.h. */
#ifndef VGAKEYBOARD_STUB_H
#define VGAKEYBOARD_STUB_H
#include "webinput.h"
#define KEYBOARD_KEYEVENTHANDLER 0
#define KEY_EVENTPRESS   1
#define KEY_EVENTRELEASE 0
typedef void (*__keyboard_handler)(int scancode, int state);
static inline int  keyboard_init(void){ webinput_open(); return 0; }
static inline void keyboard_close(void){}
static inline void keyboard_seteventhandler(__keyboard_handler h){ webinput_key = h; }
static inline int  keyboard_update(void){ return webinput_poll(); }
#endif
