/* desktop_midi.c - mtp_midi, third implementation: the hub.
 *
 * One stamped-byte FIFO, several producers. On the T113 the producer is a UART
 * RX interrupt; here it is a poll() thread over file descriptors, plus whatever
 * thread CoreMIDI or the ALSA sequencer happens to call us on. The consumer is
 * always the render loop, and it cannot tell the difference -- which is the
 * property being tested.
 *
 * The FIFO is mutex-protected rather than lock-free because there is genuinely
 * more than one producer here, unlike on the target. The lock is held for a
 * memcpy of at most a few dozen bytes; the render loop's exposure is
 * microseconds, and it is the desktop, not the deadline-carrying platform.
 * DESIGN.md 3.3's lock-free ring with DMB ISH is still the target's design and
 * is not weakened by this file.
 *
 * SPDX-License-Identifier: 0BSD
 */

#include "mtp_midi.h"
#include "mtp_time.h"
#include "mtp_log.h"
#include "desktop_midi.h"
#include "desktop_midi_src.h"

#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <poll.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/stat.h>

/* Stamped bytes. DESIGN.md 3.3 sizes the target's FIFO at 256 entries, which
 * is 82 ms of wire and plenty when the producer is a 31250-baud UART that
 * physically cannot go faster. A desktop producer can: a FIFO or a redirected
 * file hands over tens of kilobytes in one read(), which is minutes of wire
 * time arriving at memcpy speed. 64 Ki entries is 512 kB, nothing here, and it
 * means a 32 kB sysex replayed from a capture is parsed rather than shredded
 * into a parse error that blames the wrong component. The overrun counter
 * still tells the truth when even this is not enough. */
#define RING_CAP   65536u
#define MAX_FDS    8
#define MAX_DESC   8

typedef struct {
    int  fd;
    int  close_on_exit;
    int  done;            /* hit EOF: stop polling it, do not spin */
    char desc[96];
} fd_source;

static mtp_midi_byte   g_ring[RING_CAP];
static unsigned        g_head, g_tail;       /* under g_mtx */
static uint32_t        g_overruns;
static uint32_t        g_peak;
static pthread_mutex_t g_mtx = PTHREAD_MUTEX_INITIALIZER;

static fd_source       g_fds[MAX_FDS];
static unsigned        g_nfds;
static char            g_desc[MAX_DESC][96];
static unsigned        g_ndesc;

static int             g_have_seq;
static int             g_have_smf;
static int             g_live_sources;       /* sources that never end */
static int             g_open;
static pthread_t       g_thread;
static volatile int    g_thread_run;
static int             g_wake_pipe[2] = { -1, -1 };

/* ------------------------------------------------------------------ */

static void add_desc(const char *fmt, ...)
{
    va_list ap;
    if (g_ndesc >= MAX_DESC) return;
    va_start(ap, fmt);
    vsnprintf(g_desc[g_ndesc], sizeof(g_desc[0]), fmt, ap);
    va_end(ap);
    g_ndesc++;
}

static void source_ended(void)
{
    pthread_mutex_lock(&g_mtx);
    if (g_live_sources > 0) g_live_sources--;
    pthread_mutex_unlock(&g_mtx);
}

void desktop_midi_push(const uint8_t *bytes, size_t n, uint32_t t_us)
{
    size_t i;
    pthread_mutex_lock(&g_mtx);
    for (i = 0; i < n; i++) {
        unsigned used = g_head - g_tail;
        if (used >= RING_CAP) { g_overruns++; continue; }
        g_ring[g_head % RING_CAP].byte  = bytes[i];
        g_ring[g_head % RING_CAP].t_us  = t_us;
        g_ring[g_head % RING_CAP].pad[0] = 0;
        g_ring[g_head % RING_CAP].pad[1] = 0;
        g_ring[g_head % RING_CAP].pad[2] = 0;
        g_head++;
        if (used + 1u > g_peak) g_peak = used + 1u;
    }
    pthread_mutex_unlock(&g_mtx);
}

/* ------------------------------------------------------------------ */
/* Source registration                                                */

static mtp_status add_fd(int fd, int close_on_exit, const char *desc)
{
    if (g_nfds >= MAX_FDS) { MTP_LOGE("too many MIDI sources"); return MTP_ERR_NOMEM; }
    g_fds[g_nfds].fd = fd;
    g_fds[g_nfds].close_on_exit = close_on_exit;
    g_fds[g_nfds].done = 0;
    snprintf(g_fds[g_nfds].desc, sizeof(g_fds[0].desc), "%s", desc);
    g_nfds++;
    g_live_sources++;
    add_desc("%s", desc);
    return MTP_OK;
}

mtp_status desktop_midi_add_tty(const char *path, uint32_t baud)
{
    int fd = dtty_open(path, baud ? baud : 31250u);
    char d[96];
    if (fd < 0) return MTP_ERR_IO;
    snprintf(d, sizeof(d), "tty %s @ %u baud", path, baud ? baud : 31250u);
    MTP_LOGI("midi in: %s", d);
    return add_fd(fd, 1, d);
}

mtp_status desktop_midi_add_fifo(const char *path)
{
    int fd;
    char d[96];
    if (!path) return MTP_ERR_INVAL;

    if (!strcmp(path, "-")) {
        int fl = fcntl(0, F_GETFL, 0);
        if (fl >= 0) (void)fcntl(0, F_SETFL, fl | O_NONBLOCK);
        snprintf(d, sizeof(d), "stdin");
        MTP_LOGI("midi in: %s", d);
        return add_fd(0, 0, d);
    }

    /* Create it if it is not there, and open read/write so that a writer
     * closing (amidi finishing, DOSBox exiting) does not give us a permanent
     * EOF on the read end. */
    if (mkfifo(path, 0666) != 0 && errno != EEXIST) {
        MTP_LOGE("mkfifo '%s': %s", path, strerror(errno));
        return MTP_ERR_IO;
    }
    fd = open(path, O_RDWR | O_NONBLOCK);
    if (fd < 0) {
        MTP_LOGE("open '%s': %s", path, strerror(errno));
        return MTP_ERR_IO;
    }
    snprintf(d, sizeof(d), "fifo %s", path);
    MTP_LOGI("midi in: %s", d);
    return add_fd(fd, 1, d);
}

mtp_status desktop_midi_add_smf(const char *path, int loop)
{
    mtp_status s = dsmf_open(path, loop);
    if (s != MTP_OK) return s;
    g_have_smf = 1;
    add_desc("smf %s%s", path, loop ? " (looping)" : "");
    if (loop) g_live_sources++;
    return MTP_OK;
}

mtp_status desktop_midi_add_seq(const char *connect_to)
{
    mtp_status s = dseq_open(connect_to);
    if (s != MTP_OK) return s;
    g_have_seq = 1;
    g_live_sources++;
    add_desc("%s port \"mt32-t113\"%s%s", dseq_backend_name(),
             connect_to ? " <- " : "", connect_to ? connect_to : "");
    return MTP_OK;
}

/* ------------------------------------------------------------------ */
/* The poll thread: every fd source, plus the ALSA sequencer's own fds. */

static void *poll_thread(void *arg)
{
    struct pollfd pfd[MAX_FDS + 1 + 8];
    int map[MAX_FDS];
    uint8_t buf[512];
    (void)arg;

    while (g_thread_run) {
        int live = 0, n = 0, seq_n = 0, i, rc;

        for (i = 0; i < (int)g_nfds; i++) {
            if (g_fds[i].done) continue;
            map[live] = i;
            pfd[live].fd = g_fds[i].fd;
            pfd[live].events = POLLIN;
            pfd[live].revents = 0;
            live++;
        }
        n = live;

        pfd[n].fd = g_wake_pipe[0];       /* the close() doorbell */
        pfd[n].events = POLLIN;
        pfd[n].revents = 0;
        n++;

        if (g_have_seq) {
            seq_n = dseq_fill_pollfds(&pfd[n],
                                      (int)(sizeof(pfd) / sizeof(pfd[0])) - n);
            n += seq_n;
        }

        rc = poll(pfd, (nfds_t)n, 200);
        if (rc < 0) {
            if (errno == EINTR) continue;
            MTP_LOGE("midi poll: %s", strerror(errno));
            break;
        }
        if (rc == 0) continue;

        for (i = 0; i < live; i++) {
            fd_source *src = &g_fds[map[i]];
            if (!(pfd[i].revents & (POLLIN | POLLHUP | POLLERR))) continue;
            for (;;) {
                ssize_t got = read(src->fd, buf, sizeof(buf));
                if (got > 0) {
                    /* One timestamp for the whole read. At 31250 baud a read
                     * almost always returns one or two bytes, so this is the
                     * arrival time; a burst off a FIFO is not wire-paced and
                     * has no truer time to report. */
                    desktop_midi_push(buf, (size_t)got, mtp_time_us());
                    if ((size_t)got < sizeof(buf)) break;
                } else if (got == 0) {
                    /* Writer gone for good (stdin from a file, a closed pipe).
                     * Retire the source rather than spinning on EOF. */
                    src->done = 1;
                    source_ended();
                    MTP_LOGI("midi source ended: %s", src->desc);
                    break;
                } else {
                    if (errno == EAGAIN || errno == EWOULDBLOCK) break;
                    if (errno == EINTR) continue;
                    src->done = 1;
                    source_ended();
                    MTP_LOGW("midi source '%s': %s", src->desc, strerror(errno));
                    break;
                }
            }
        }

        if (g_have_seq && seq_n > 0)
            dseq_dispatch(&pfd[live + 1], seq_n);
    }
    return NULL;
}

/* ------------------------------------------------------------------ */

mtp_status mtp_midi_open(uint32_t baud)
{
    (void)baud;   /* per-source; desktop_midi_add_tty() takes it */
    if (g_open) return MTP_ERR_STATE;

    if (g_nfds == 0u && !g_have_seq && !g_have_smf)
        MTP_LOGW("no MIDI source configured: the synth will be silent");

    if (g_nfds > 0u || g_have_seq) {
        if (pipe(g_wake_pipe) != 0) return MTP_ERR_IO;
        (void)fcntl(g_wake_pipe[0], F_SETFL, O_NONBLOCK);
        g_thread_run = 1;
        if (pthread_create(&g_thread, NULL, poll_thread, NULL) != 0) {
            MTP_LOGE("cannot start MIDI thread");
            return MTP_ERR_IO;
        }
    }
    g_open = 1;
    return MTP_OK;
}

void mtp_midi_close(void)
{
    unsigned i;
    if (!g_open) return;
    if (g_thread_run) {
        char c = 'x';
        g_thread_run = 0;
        if (g_wake_pipe[1] >= 0) { ssize_t w = write(g_wake_pipe[1], &c, 1); (void)w; }
        pthread_join(g_thread, NULL);
    }
    if (g_have_seq) dseq_close();
    if (g_have_smf) dsmf_close();
    for (i = 0; i < g_nfds; i++)
        if (g_fds[i].close_on_exit && g_fds[i].fd >= 0) close(g_fds[i].fd);
    if (g_wake_pipe[0] >= 0) { close(g_wake_pipe[0]); close(g_wake_pipe[1]); }
    g_wake_pipe[0] = g_wake_pipe[1] = -1;
    g_nfds = 0;
    g_open = 0;
}

size_t mtp_midi_read(mtp_midi_byte *dst, size_t max)
{
    size_t n = 0;

    /* The SMF source is pulled rather than pushed: it is paced against the same
     * clock the render loop reads, so its events land on the sample they were
     * written for instead of on a scheduler tick. */
    if (g_have_smf) dsmf_pump();

    pthread_mutex_lock(&g_mtx);
    while (n < max && g_head != g_tail) {
        dst[n] = g_ring[g_tail % RING_CAP];
        g_tail++;
        n++;
    }
    pthread_mutex_unlock(&g_mtx);
    return n;
}

uint32_t mtp_midi_overruns(void)
{
    uint32_t v;
    pthread_mutex_lock(&g_mtx);
    v = g_overruns;
    pthread_mutex_unlock(&g_mtx);
    return v;
}

uint32_t mtp_midi_frame_errors(void)
{
    /* A desktop tty reports framing errors only if we ask for PARMRK, which
     * would corrupt the byte stream. The counter stays 0 here and means
     * something only on the target. */
    return 0;
}

int mtp_midi_eof(void)
{
    int empty, live;
    pthread_mutex_lock(&g_mtx);
    empty = (g_head == g_tail);
    live  = g_live_sources;
    pthread_mutex_unlock(&g_mtx);
    if (live > 0) return 0;                    /* a wire is never finished */
    if (g_have_smf) return dsmf_eof() && empty;
    return empty;
}

uint32_t desktop_midi_ring_peak(void)
{
    uint32_t v;
    pthread_mutex_lock(&g_mtx);
    v = g_peak;
    pthread_mutex_unlock(&g_mtx);
    return v;
}

unsigned desktop_midi_source_count(void) { return g_ndesc; }

const char *desktop_midi_source_desc(unsigned i)
{
    return i < g_ndesc ? g_desc[i] : "";
}

void desktop_midi_list_ports(void) { dseq_list(); }
