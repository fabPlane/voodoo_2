/* No-op svgalib keyboard stub for headless GLQuake (timedemo needs no input). */
#ifndef VGAKEYBOARD_STUB_H
#define VGAKEYBOARD_STUB_H
#define KEYBOARD_KEYEVENTHANDLER 0
#define KEY_EVENTPRESS   1
#define KEY_EVENTRELEASE 0
typedef void (*__keyboard_handler)(int scancode, int state);
static inline int  keyboard_init(void){ return 0; }
static inline void keyboard_close(void){}
static inline void keyboard_seteventhandler(__keyboard_handler h){ (void)h; }
static inline int  keyboard_update(void){ return 0; }
#endif
