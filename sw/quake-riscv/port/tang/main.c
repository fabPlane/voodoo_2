#include <quakembd.h>

#include <stdio.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include <sys/time.h>
#include <unistd.h>

void Sys_SetFPCW(void) {}

uint64_t qembd_get_us_time(void) {
  struct timeval tp;
  static int inited;
  static int secbase;

  gettimeofday(&tp, NULL);
  if (!inited) {
    inited = 1;
    secbase = (int)tp.tv_sec;
    return (uint64_t)tp.tv_usec;
  }
  return ((uint64_t)(tp.tv_sec - secbase) * 1000000ull) + (uint64_t)tp.tv_usec;
}

void qembd_udelay(uint32_t us) { usleep(us); }

void *qembd_allocmain(size_t size) { return malloc(size); }

void qembd_set_relative_mode(bool enabled) { (void)enabled; }

int main(int argc, char **argv) {
  char *fwd[64];
  int n = 0;
  int i;
  int have_basedir = 0;

  setvbuf(stdout, NULL, _IONBF, 0);
  setvbuf(stderr, NULL, _IONBF, 0);

  for (i = 1; i < argc; i++) {
    if (strcmp(argv[i], "-basedir") == 0) {
      have_basedir = 1;
    }
  }

  fwd[n++] = argv[0];
  if (!have_basedir) {
    fwd[n++] = "-basedir";
    fwd[n++] = ".";
  }
  for (i = 1; i < argc && n < 62; i++) {
    fwd[n++] = argv[i];
  }
  fwd[n] = 0;
  return qembd_main(n, fwd);
}
