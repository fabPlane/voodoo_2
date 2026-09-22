#ifndef TANG_SW_H
#define TANG_SW_H

#include <stdint.h>

void tang_set_hid(uint16_t hid);
extern uint16_t g_tang_hid;
unsigned char *VID_SwFramebuffer(void);
void a25_poll_hid(void);

#endif
