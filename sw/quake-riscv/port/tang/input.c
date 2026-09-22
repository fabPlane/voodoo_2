#include "in_usb.h"
#include "tang_sw.h"

#include <quakembd.h>
#include <quakedef.h>

/* BL616 hid1 word. MCU firmware pokes this; host tests call tang_set_hid. */
uint16_t g_tang_hid;

void tang_set_hid(uint16_t hid) { g_tang_hid = hid; }

#ifndef __riscv
void a25_poll_hid(void) {}
#endif

typedef struct {
  uint16_t bit;
  uint32_t key;
} PadBind;

static const PadBind k_binds[] = {
    {USB_UP, K_UPARROW}, {USB_DOWN, K_DOWNARROW},
    {USB_LEFT, K_LEFTARROW}, {USB_RIGHT, K_RIGHTARROW},
    {USB_A, K_CTRL}, {USB_B, K_SPACE},
    {USB_R, '/'}, {USB_START, K_ESCAPE}, {USB_SELECT, K_ESCAPE},
};

#define KEYQ_CAP 32
static key_event_t keyq[KEYQ_CAP];
static int keyq_r, keyq_w, keyq_n;
static uint16_t last_hid;
static int look_x, look_y;

static void push_key(uint32_t keycode, uint8_t down) {
  if (keyq_n >= KEYQ_CAP) {
    return;
  }
  keyq[keyq_w].keycode = keycode;
  keyq[keyq_w].state = down;
  keyq_w = (keyq_w + 1) % KEYQ_CAP;
  keyq_n++;
}

static void poll_pad(void) {
  QuakePad pad;
  unsigned i;
  uint16_t hid;
  uint16_t edge;

  a25_poll_hid();
  hid = g_tang_hid;
  edge = hid ^ last_hid;

  if (edge) {
    for (i = 0; i < sizeof(k_binds) / sizeof(k_binds[0]); i++) {
      uint16_t bit = k_binds[i].bit;
      if (edge & bit) {
        push_key(k_binds[i].key, (hid & bit) ? 1 : 0);
      }
    }
    last_hid = hid;
  }

  pad = in_usb_map(hid);
  look_x = 0;
  look_y = 0;
  if (pad.look_up) {
    look_y -= 4;
  }
  if (pad.look_down) {
    look_y += 4;
  }
}

int qembd_dequeue_key_event(key_event_t *e) {
  poll_pad();
  if (keyq_n <= 0) {
    return -1;
  }
  *e = keyq[keyq_r];
  keyq_r = (keyq_r + 1) % KEYQ_CAP;
  keyq_n--;
  return 0;
}

int qembd_get_mouse_movement(mouse_movement_t *movement) {
  poll_pad();
  movement->x = look_x;
  movement->y = look_y;
  look_x = 0;
  look_y = 0;
  return 0;
}
