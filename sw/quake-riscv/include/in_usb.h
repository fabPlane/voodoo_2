#ifndef IN_USB_H
#define IN_USB_H

#include <stdint.h>

/* SNES-layout hid1 from BL616 command 0x09. */
#define USB_B (1u << 0)
#define USB_Y (1u << 1)
#define USB_SELECT (1u << 2)
#define USB_START (1u << 3)
#define USB_UP (1u << 4)
#define USB_DOWN (1u << 5)
#define USB_LEFT (1u << 6)
#define USB_RIGHT (1u << 7)
#define USB_A (1u << 8)
#define USB_X (1u << 9)
#define USB_L (1u << 10)
#define USB_R (1u << 11)

typedef struct {
  int forward, back, left, right;
  int look_up, look_down;
  int fire, jump, next_weapon, menu;
} QuakePad;

QuakePad in_usb_map(uint16_t hid);

#endif
