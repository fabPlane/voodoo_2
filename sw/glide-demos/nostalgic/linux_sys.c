/* Linux/headless replacement for the DOS sys.c glue.
 * The FPGA-sim trace pipeline renders a fixed number of frames and never
 * reads the keyboard, so the timing/input primitives are no-ops. */
#include "sys.h"

void timer_wait(void) {}
void timer_custom_wait(unsigned long ticks) { (void)ticks; }
void clock_init(void) {}
void clock_shutdown(void) {}
int  is_key_pressed(void) { return 0; }
char get_key_code(void) { return 0; }
