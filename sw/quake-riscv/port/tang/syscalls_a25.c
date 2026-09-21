#include <errno.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <unistd.h>

#include <stdint.h>
#include <string.h>

/* Newlib hooks for Andes A25 bare metal. pak0.pak is a 20 MiB window the
 * loader places at 0x82000000 (PACK magic). HID is a 16-bit MMIO word. */

#define PAK0_BASE ((volatile unsigned char *)(uintptr_t)0x82000000u)
#define PAK0_MAX (20u * 1024u * 1024u)
#define HID_MMIO (*(volatile uint32_t *)(uintptr_t)0x10000000u)
#define PAK_FD 3

#ifndef A25_HZ
#define A25_HZ 400000000u
#endif

extern uint16_t g_tang_hid;
extern char _heap_start, _heap_end;

static char *heap_ptr;
static uint32_t pak_len;
static int pak_ok;
static off_t pak_pos;

static uint64_t rdcycle(void) {
  uint32_t hi, lo, hi2;
  do {
    __asm__ volatile("rdcycleh %0" : "=r"(hi));
    __asm__ volatile("rdcycle %0" : "=r"(lo));
    __asm__ volatile("rdcycleh %0" : "=r"(hi2));
  } while (hi != hi2);
  return ((uint64_t)hi << 32) | lo;
}

static void pak_probe(void) {
  uint32_t dir_ofs, dir_len;
  if (pak_ok) {
    return;
  }
  if (PAK0_BASE[0] != 'P' || PAK0_BASE[1] != 'A' || PAK0_BASE[2] != 'C' ||
      PAK0_BASE[3] != 'K') {
    return;
  }
  dir_ofs = (uint32_t)PAK0_BASE[4] | ((uint32_t)PAK0_BASE[5] << 8) |
            ((uint32_t)PAK0_BASE[6] << 16) | ((uint32_t)PAK0_BASE[7] << 24);
  dir_len = (uint32_t)PAK0_BASE[8] | ((uint32_t)PAK0_BASE[9] << 8) |
            ((uint32_t)PAK0_BASE[10] << 16) | ((uint32_t)PAK0_BASE[11] << 24);
  if (dir_ofs > PAK0_MAX || dir_len > PAK0_MAX) {
    return;
  }
  pak_len = dir_ofs + dir_len;
  if (pak_len > PAK0_MAX) {
    pak_len = PAK0_MAX;
  }
  pak_ok = 1;
}

void *_sbrk(ptrdiff_t incr) {
  char *prev;
  if (!heap_ptr) {
    heap_ptr = &_heap_start;
  }
  prev = heap_ptr;
  if (heap_ptr + incr > &_heap_end) {
    errno = ENOMEM;
    return (void *)-1;
  }
  heap_ptr += incr;
  return prev;
}

void _exit(int status) {
  (void)status;
  for (;;) {
    __asm__ volatile("wfi");
  }
}

int _kill(int pid, int sig) {
  (void)pid;
  (void)sig;
  errno = EINVAL;
  return -1;
}

int _getpid(void) { return 1; }

int _isatty(int fd) {
  (void)fd;
  return 1;
}

int _fstat(int fd, struct stat *st) {
  (void)fd;
  memset(st, 0, sizeof(*st));
  st->st_mode = (fd == PAK_FD) ? S_IFREG : S_IFCHR;
  st->st_size = (off_t)pak_len;
  return 0;
}

int _write(int fd, const void *buf, size_t count) {
  (void)fd;
  (void)buf;
  return (int)count;
}

int _close(int fd) {
  (void)fd;
  return 0;
}

off_t _lseek(int fd, off_t offset, int whence) {
  if (fd != PAK_FD) {
    errno = EBADF;
    return -1;
  }
  pak_probe();
  if (whence == SEEK_SET) {
    pak_pos = offset;
  } else if (whence == SEEK_CUR) {
    pak_pos += offset;
  } else if (whence == SEEK_END) {
    pak_pos = (off_t)pak_len + offset;
  }
  if (pak_pos < 0) {
    pak_pos = 0;
  }
  return pak_pos;
}

int _read(int fd, void *buf, size_t count) {
  size_t n;
  if (fd != PAK_FD) {
    errno = EBADF;
    return -1;
  }
  pak_probe();
  if (!pak_ok) {
    errno = EIO;
    return -1;
  }
  if ((uint32_t)pak_pos >= pak_len) {
    return 0;
  }
  n = count;
  if ((uint32_t)pak_pos + n > pak_len) {
    n = pak_len - (uint32_t)pak_pos;
  }
  memcpy(buf, (const void *)(PAK0_BASE + pak_pos), n);
  pak_pos += (off_t)n;
  return (int)n;
}

int _open(const char *path, int flags, ...) {
  (void)flags;
  pak_probe();
  if (path && strstr(path, "pak0.pak") && pak_ok) {
    pak_pos = 0;
    return PAK_FD;
  }
  errno = ENOENT;
  return -1;
}

int _stat(const char *path, struct stat *st) {
  memset(st, 0, sizeof(*st));
  pak_probe();
  if (path && strstr(path, "pak0.pak") && pak_ok) {
    st->st_mode = S_IFREG;
    st->st_size = (off_t)pak_len;
    return 0;
  }
  errno = ENOENT;
  return -1;
}

int _mkdir(const char *path, mode_t mode) {
  (void)path;
  (void)mode;
  return 0;
}

mode_t umask(mode_t mask) {
  (void)mask;
  return 0;
}

int mkdir(const char *path, mode_t mode) { return _mkdir(path, mode); }

int _gettimeofday(struct timeval *tv, void *tz) {
  uint64_t us;
  (void)tz;
  if (!tv) {
    return 0;
  }
  us = rdcycle() / (A25_HZ / 1000000u);
  tv->tv_sec = (time_t)(us / 1000000u);
  tv->tv_usec = (suseconds_t)(us % 1000000u);
  return 0;
}

int usleep(useconds_t usec) {
  uint64_t start = rdcycle();
  uint64_t wait = (uint64_t)usec * (A25_HZ / 1000000u);
  while (rdcycle() - start < wait) {
  }
  return 0;
}

void a25_poll_hid(void) { g_tang_hid = (uint16_t)HID_MMIO; }
