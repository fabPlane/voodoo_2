#include "in_usb.h"

QuakePad in_usb_map(uint16_t hid) {
  QuakePad p;
  p.forward = (hid & USB_UP) != 0;
  p.back = (hid & USB_DOWN) != 0;
  p.left = (hid & USB_LEFT) != 0;
  p.right = (hid & USB_RIGHT) != 0;
  p.look_up = (hid & USB_X) != 0;
  p.look_down = (hid & USB_Y) != 0;
  p.fire = (hid & USB_A) != 0;
  p.jump = (hid & USB_B) != 0;
  p.next_weapon = (hid & USB_R) != 0;
  p.menu = (hid & (USB_START | USB_SELECT)) != 0;
  return p;
}
