/* desktop_midi_tty.c - MIDI in from a serial line at 31250 baud.
 *
 * This is the source that matters most, because it is the only one that is
 * literally the same thing the T113 will see: 8N1 at 31250 baud, one byte every
 * 320 us, with the timing of the wire rather than the timing of a scheduler.
 * A USB-serial adapter with a 6N138 on its RX line is a MIDI DIN input; a game
 * port cable from a DOS machine's MPU-401 is the same signal at TTL level.
 *
 * 31250 is not a POSIX baud rate, and the two platforms disagree about how to
 * ask for one:
 *
 *   Linux   struct termios2 + BOTHER + c_ispeed/c_ospeed via TCSETS2.
 *           <asm/termbits.h> and <termios.h> define conflicting structs, so
 *           this file includes only the former and does its own raw setup.
 *   macOS   cfmakeraw + tcsetattr at any rate, then IOSSIOSPEED with the real
 *           rate. Apple documents this as the way to get a non-standard speed
 *           out of an FTDI or CDC-ACM driver.
 *
 * The old B38400 + ASYNC_SPD_CUST + custom_divisor route is deliberately not
 * used: it is deprecated, it is per-driver, and BOTHER has been in Linux since
 * 2.6.20.
 *
 * SPDX-License-Identifier: 0BSD
 */

#include "desktop_midi_src.h"
#include "mtp_log.h"

#include <errno.h>
#include <fcntl.h>
#include <string.h>
#include <unistd.h>

#if defined(__linux__)
  /* Must not include <termios.h> in this file: it and <asm/termbits.h> define
   * the same names differently. Everything we need is in the asm header. */
  #include <asm/termbits.h>
  #include <sys/ioctl.h>
#elif defined(__APPLE__)
  #include <termios.h>
  #include <sys/ioctl.h>
  #include <IOKit/serial/ioss.h>
#else
  #include <termios.h>
  #include <sys/ioctl.h>
#endif

#if defined(__linux__)

static int set_raw_baud(int fd, uint32_t baud)
{
    struct termios2 t;

    if (ioctl(fd, TCGETS2, &t) != 0) {
        MTP_LOGE("TCGETS2: %s", strerror(errno));
        return -1;
    }

    /* Raw 8N1, no flow control, no modem-control lines, receiver on. */
    t.c_iflag = IGNBRK;              /* no XON/XOFF, no CR/LF mangling, no
                                      * PARMRK -- a MIDI byte is any of 256 */
    t.c_oflag = 0;
    t.c_lflag = 0;                   /* not canonical, no echo, no signals */
    t.c_cflag &= ~(unsigned)(CSIZE | PARENB | CSTOPB | CRTSCTS | CBAUD | CBAUDEX);
    t.c_cflag |= CS8 | CLOCAL | CREAD | BOTHER;
    t.c_ispeed = baud;
    t.c_ospeed = baud;
    t.c_cc[VMIN]  = 0;               /* read() returns what is there */
    t.c_cc[VTIME] = 0;

    if (ioctl(fd, TCSETS2, &t) != 0) {
        MTP_LOGE("TCSETS2 (baud %u): %s", baud, strerror(errno));
        return -1;
    }

    /* Read it back: some drivers silently round to the nearest they can do,
     * and a 5% error is the difference between MIDI and noise. */
    if (ioctl(fd, TCGETS2, &t) == 0 && t.c_ospeed != baud) {
        MTP_LOGW("serial port gave %u baud, not %u -- expect framing errors",
                 (unsigned)t.c_ospeed, baud);
    }
    return 0;
}

#elif defined(__APPLE__)

static int set_raw_baud(int fd, uint32_t baud)
{
    struct termios t;
    speed_t sp = (speed_t)baud;

    if (tcgetattr(fd, &t) != 0) {
        MTP_LOGE("tcgetattr: %s", strerror(errno));
        return -1;
    }
    cfmakeraw(&t);
    t.c_cflag &= ~(tcflag_t)(CSIZE | PARENB | CSTOPB | CRTSCTS);
    t.c_cflag |= CS8 | CLOCAL | CREAD;
    t.c_cc[VMIN]  = 0;
    t.c_cc[VTIME] = 0;
    if (tcsetattr(fd, TCSANOW, &t) != 0) {
        MTP_LOGE("tcsetattr: %s", strerror(errno));
        return -1;
    }
    /* IOSSIOSPEED must come after tcsetattr: tcsetattr resets the rate. */
    if (ioctl(fd, IOSSIOSPEED, &sp) != 0) {
        MTP_LOGE("IOSSIOSPEED %u: %s (does this adapter support it?)",
                 baud, strerror(errno));
        return -1;
    }
    return 0;
}

#else

static int set_raw_baud(int fd, uint32_t baud)
{
    (void)fd;
    MTP_LOGE("no non-standard baud support on this OS (wanted %u)", baud);
    return -1;
}

#endif

int dtty_open(const char *path, uint32_t baud)
{
    int fd;

    if (!path) return -1;

    /* O_NOCTTY so a serial console does not become our controlling terminal;
     * O_NONBLOCK so open() does not wait for DCD on a port with modem lines. */
    fd = open(path, O_RDWR | O_NOCTTY | O_NONBLOCK);
    if (fd < 0) {
        MTP_LOGE("open '%s': %s", path, strerror(errno));
        if (errno == EACCES)
            MTP_LOGE("  (on Linux: usermod -aG dialout $USER, then log out and in)");
        return -1;
    }

    if (set_raw_baud(fd, baud) != 0) { close(fd); return -1; }

    /* Throw away anything that arrived while the port was mis-configured.
     * A non-blocking drain rather than tcflush(), because on Linux this file
     * cannot include <termios.h> (see the header comment). */
    {
        uint8_t junk[256];
        while (read(fd, junk, sizeof(junk)) > 0) { }
    }
    return fd;
}
