/* desktop_audio_miniaudio.c - mtp_audio, third implementation: a real sound card.
 *
 * port/host/host_audio_wav.c models the T113's DMA ring against a WAV file and
 * a virtual play cursor. This one models it against a device that is genuinely
 * pulling samples out from under us on another thread, which is the only way to
 * find out whether the ring discipline in port/src/mtp_render.c actually keeps
 * up when nothing is waiting for it.
 *
 * The mapping onto the target is exact:
 *
 *   T113                                desktop
 *   ----                                -------
 *   DMA descriptor list, block_count    ring of block_count int16 buffers
 *   DMA completion IRQ advances play    ma device callback advances play
 *   WFI until the IRQ fires             pthread_cond_timedwait
 *   DMA played an uncommitted block     callback found the ring empty
 *
 * The callback is the "interrupt": it does not allocate, does not log, does not
 * lock anything the render loop holds for long, and its only communication with
 * the render loop is three atomics.
 *
 * SPDX-License-Identifier: 0BSD
 */

#include "mtp_audio.h"
#include "mtp_log.h"
#include "mtp_time.h"
#include "desktop_audio.h"

#include "miniaudio.h"

#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <pthread.h>
#include <errno.h>
#include <time.h>

#define MAX_BLOCKS 16u

static ma_context        g_ctx;
static int               g_ctx_ready;
static ma_device         g_dev;
static int               g_dev_ready;
static mtp_audio_config  g_cfg;

static int16_t          *g_block[MAX_BLOCKS];
static int16_t          *g_acquired;

/* Ring cursors in blocks. head - tail == blocks committed but not yet played.
 * head is written only by the render loop, tail only by the audio callback. */
static atomic_uint       g_head;
static atomic_uint       g_tail;
static atomic_uint       g_underruns;
static atomic_uint       g_waiters;
static atomic_uint       g_cb_calls;
static atomic_uint       g_cb_max_frames;   /* largest single callback ask */
static atomic_uint       g_waits;            /* calls to mtp_audio_wait that slept */
static atomic_uint       g_wait_timeouts;    /* ... that woke on the timer, not the
                                              * callback: a lost signal or a slow
                                              * scheduler, and the difference
                                              * between the two is visible here */
static atomic_uint       g_wait_worst_us;
static atomic_uint       g_cb_worst_gap_us;  /* longest silence between asks */
static uint64_t          g_cb_last_us;
static unsigned          g_read_off;      /* frames consumed of block[tail] */

static pthread_mutex_t   g_mtx = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t    g_cv  = PTHREAD_COND_INITIALIZER;

/* Configuration that does not fit through mtp_audio_config. Set before open. */
static char              g_backend_name[32] = "auto";
static char              g_device_name[256];
static unsigned          g_periods = 3;
static FILE             *g_tap;
static uint64_t          g_tap_frames;
static uint32_t          g_block_period_us = 2667;

/* ------------------------------------------------------------------ */
/* WAV tap: what the speakers got, for a machine with no speakers.     */

static void wav_header(FILE *fp, uint32_t rate, uint16_t ch, uint32_t frames)
{
    uint32_t data_bytes = frames * ch * 2u;
    uint32_t riff = 36u + data_bytes;
    uint32_t byte_rate = rate * ch * 2u;
    uint16_t align = (uint16_t)(ch * 2u);
    uint8_t h[44];
    memcpy(h, "RIFF", 4);
    h[4]=(uint8_t)riff; h[5]=(uint8_t)(riff>>8); h[6]=(uint8_t)(riff>>16); h[7]=(uint8_t)(riff>>24);
    memcpy(h+8, "WAVEfmt ", 8);
    h[16]=16; h[17]=0; h[18]=0; h[19]=0;
    h[20]=1;  h[21]=0;
    h[22]=(uint8_t)ch; h[23]=(uint8_t)(ch>>8);
    h[24]=(uint8_t)rate; h[25]=(uint8_t)(rate>>8); h[26]=(uint8_t)(rate>>16); h[27]=(uint8_t)(rate>>24);
    h[28]=(uint8_t)byte_rate; h[29]=(uint8_t)(byte_rate>>8);
    h[30]=(uint8_t)(byte_rate>>16); h[31]=(uint8_t)(byte_rate>>24);
    h[32]=(uint8_t)align; h[33]=(uint8_t)(align>>8);
    h[34]=16; h[35]=0;
    memcpy(h+36, "data", 4);
    h[40]=(uint8_t)data_bytes; h[41]=(uint8_t)(data_bytes>>8);
    h[42]=(uint8_t)(data_bytes>>16); h[43]=(uint8_t)(data_bytes>>24);
    fwrite(h, 1, 44, fp);
}

/* ------------------------------------------------------------------ */

void desktop_audio_set_backend(const char *name)
{
    if (name) { snprintf(g_backend_name, sizeof(g_backend_name), "%s", name); }
}

void desktop_audio_set_device(const char *name)
{
    if (name) { snprintf(g_device_name, sizeof(g_device_name), "%s", name); }
}

void desktop_audio_set_periods(unsigned n)
{
    if (n >= 2u && n <= 8u) g_periods = n;
}

mtp_status desktop_audio_set_tap(const char *path)
{
    if (!path) return MTP_OK;
    g_tap = fopen(path, "wb");
    if (!g_tap) { MTP_LOGE("cannot write tap wav '%s'", path); return MTP_ERR_IO; }
    return MTP_OK;
}

static int backend_from_name(const char *name, ma_backend *out)
{
    struct { const char *n; ma_backend b; } map[] = {
        { "wasapi",    ma_backend_wasapi    },
        { "coreaudio", ma_backend_coreaudio },
        { "alsa",      ma_backend_alsa      },
        { "pulse",     ma_backend_pulseaudio},
        { "pulseaudio",ma_backend_pulseaudio},
        { "jack",      ma_backend_jack      },
        { "sndio",     ma_backend_sndio     },
        { "oss",       ma_backend_oss       },
        { "null",      ma_backend_null      }
    };
    size_t i;
    for (i = 0; i < sizeof(map)/sizeof(map[0]); i++) {
        if (!strcmp(name, map[i].n)) { *out = map[i].b; return 1; }
    }
    return 0;
}

/* ------------------------------------------------------------------ */
/* The "DMA interrupt".                                               */

static void data_callback(ma_device *dev, void *out, const void *in,
                          ma_uint32 frame_count)
{
    int16_t *dst = (int16_t *)out;
    unsigned fpb = g_cfg.frames_per_block;
    unsigned remaining = (unsigned)frame_count;
    unsigned signalled = 0;

    (void)dev; (void)in;

    {
        uint64_t now = mtp_time_us64();
        if (g_cb_last_us) {
            uint32_t gap = (uint32_t)(now - g_cb_last_us);
            if (gap > atomic_load_explicit(&g_cb_worst_gap_us, memory_order_relaxed))
                atomic_store_explicit(&g_cb_worst_gap_us, gap, memory_order_relaxed);
        }
        g_cb_last_us = now;
    }
    atomic_fetch_add_explicit(&g_cb_calls, 1u, memory_order_relaxed);
    if (frame_count > atomic_load_explicit(&g_cb_max_frames, memory_order_relaxed))
        atomic_store_explicit(&g_cb_max_frames, frame_count, memory_order_relaxed);

    while (remaining > 0u) {
        unsigned head = atomic_load_explicit(&g_head, memory_order_acquire);
        unsigned tail = atomic_load_explicit(&g_tail, memory_order_relaxed);
        unsigned avail, n;

        if (head == tail) {
            /* The ring ran dry. On the T113 this is the DMA engine replaying a
             * stale descriptor and it is audible. Emit silence for one block's
             * worth and count it exactly as the target would.
             *
             * Except before the first commit: the device starts inside
             * mtp_audio_open(), a few hundred microseconds before the render
             * loop exists, and the silence it plays until then is the same
             * silence the target's ring holds at start-up (mtp_audio.h:
             * "opening early is safe"). Counting that would put two or three
             * phantom underruns on every run and teach the reader to ignore
             * the number that matters most. */
            n = remaining < fpb ? remaining : fpb;
            memset(dst, 0, (size_t)n * g_cfg.channels * sizeof(int16_t));
            if (head != 0u)
                atomic_fetch_add_explicit(&g_underruns, 1u, memory_order_relaxed);
            dst += (size_t)n * g_cfg.channels;
            remaining -= n;
            continue;
        }

        avail = fpb - g_read_off;
        n = remaining < avail ? remaining : avail;
        memcpy(dst,
               g_block[tail % g_cfg.block_count] + (size_t)g_read_off * g_cfg.channels,
               (size_t)n * g_cfg.channels * sizeof(int16_t));
        dst += (size_t)n * g_cfg.channels;
        remaining -= n;
        g_read_off += n;

        if (g_read_off == fpb) {
            g_read_off = 0;
            atomic_store_explicit(&g_tail, tail + 1u, memory_order_release);
            signalled = 1;
        }
    }

    /* Wake the render loop only if it is actually waiting, and never block the
     * audio thread to do it: a missed signal costs at most one timed-wait cap. */
    if (signalled && atomic_load_explicit(&g_waiters, memory_order_acquire) != 0u) {
        if (pthread_mutex_trylock(&g_mtx) == 0) {
            pthread_cond_broadcast(&g_cv);
            pthread_mutex_unlock(&g_mtx);
        }
    }
}

/* ------------------------------------------------------------------ */

mtp_status mtp_audio_open(const mtp_audio_config *cfg)
{
    ma_device_config dcfg;
    ma_context_config ccfg;
    ma_backend backend;
    ma_backend *backends = NULL;
    ma_uint32 backend_count = 0;
    unsigned i;

    if (!cfg || cfg->channels != 2 || cfg->block_count == 0
        || cfg->block_count > MAX_BLOCKS || cfg->frames_per_block == 0
        || cfg->sample_rate == 0)
        return MTP_ERR_INVAL;
    if (g_dev_ready) return MTP_ERR_STATE;

    g_cfg = *cfg;
    g_block_period_us = (uint32_t)((1000000ull * cfg->frames_per_block)
                                   / cfg->sample_rate);
    if (g_block_period_us == 0u) g_block_period_us = 1u;

    for (i = 0; i < g_cfg.block_count; i++) {
        g_block[i] = (int16_t *)calloc((size_t)g_cfg.frames_per_block * 2u,
                                       sizeof(int16_t));
        if (!g_block[i]) return MTP_ERR_NOMEM;
    }
    atomic_store(&g_head, 0u);
    atomic_store(&g_tail, 0u);
    atomic_store(&g_underruns, 0u);
    atomic_store(&g_waiters, 0u);
    g_read_off = 0;
    g_acquired = NULL;

    ccfg = ma_context_config_init();
    if (strcmp(g_backend_name, "auto") != 0) {
        if (!backend_from_name(g_backend_name, &backend)) {
            MTP_LOGE("unknown audio backend '%s'", g_backend_name);
            return MTP_ERR_INVAL;
        }
        backends = &backend;
        backend_count = 1;
    }
    if (ma_context_init(backends, backend_count, &ccfg, &g_ctx) != MA_SUCCESS) {
        MTP_LOGE("no audio backend available (tried '%s')", g_backend_name);
        return MTP_ERR_IO;
    }
    g_ctx_ready = 1;

    dcfg = ma_device_config_init(ma_device_type_playback);
    dcfg.playback.format   = ma_format_s16;
    dcfg.playback.channels = g_cfg.channels;
    dcfg.sampleRate        = g_cfg.sample_rate;
    dcfg.periodSizeInFrames = g_cfg.frames_per_block;
    dcfg.periods           = g_periods;
    dcfg.performanceProfile = ma_performance_profile_low_latency;
    dcfg.noPreSilencedOutputBuffer = MA_TRUE;  /* we always fill every frame */
    dcfg.dataCallback      = data_callback;

    /* A named device, if the user asked for one. */
    if (g_device_name[0]) {
        ma_device_info *infos = NULL;
        ma_uint32 count = 0, k;
        static ma_device_id chosen;
        int found = 0;
        if (ma_context_get_devices(&g_ctx, &infos, &count, NULL, NULL) == MA_SUCCESS) {
            for (k = 0; k < count; k++) {
                if (strstr(infos[k].name, g_device_name) != NULL) {
                    chosen = infos[k].id;
                    dcfg.playback.pDeviceID = &chosen;
                    MTP_LOGI("audio device: %s", infos[k].name);
                    found = 1;
                    break;
                }
            }
        }
        if (!found)
            MTP_LOGW("no playback device matching '%s'; using the default",
                     g_device_name);
    }

    if (ma_device_init(&g_ctx, &dcfg, &g_dev) != MA_SUCCESS) {
        MTP_LOGE("cannot open playback device");
        ma_context_uninit(&g_ctx);
        g_ctx_ready = 0;
        return MTP_ERR_IO;
    }
    g_dev_ready = 1;

    if (g_tap) wav_header(g_tap, g_cfg.sample_rate, g_cfg.channels, 0);

    if (ma_device_start(&g_dev) != MA_SUCCESS) {
        MTP_LOGE("cannot start playback device");
        ma_device_uninit(&g_dev);
        ma_context_uninit(&g_ctx);
        g_dev_ready = g_ctx_ready = 0;
        return MTP_ERR_IO;
    }

    MTP_LOGI("audio: %s, %u Hz, %u frames x %u ring blocks (%.2f ms), "
             "device period %u x %u",
             ma_get_backend_name(g_ctx.backend),
             g_cfg.sample_rate, g_cfg.frames_per_block, g_cfg.block_count,
             1000.0 * g_cfg.frames_per_block * g_cfg.block_count / g_cfg.sample_rate,
             (unsigned)g_dev.playback.internalPeriodSizeInFrames,
             (unsigned)g_dev.playback.internalPeriods);
    if (g_dev.playback.internalSampleRate != g_cfg.sample_rate)
        MTP_LOGW("device native rate is %u Hz; miniaudio is resampling to %u. "
                 "This is a desktop convenience the T113 will not have.",
                 (unsigned)g_dev.playback.internalSampleRate, g_cfg.sample_rate);
    return MTP_OK;
}

void mtp_audio_close(void)
{
    unsigned i;
    if (g_dev_ready) { ma_device_uninit(&g_dev); g_dev_ready = 0; }
    if (g_ctx_ready) { ma_context_uninit(&g_ctx); g_ctx_ready = 0; }
    if (g_tap) {
        fseek(g_tap, 0, SEEK_SET);
        wav_header(g_tap, g_cfg.sample_rate, g_cfg.channels,
                   (uint32_t)g_tap_frames);
        fclose(g_tap);
        g_tap = NULL;
    }
    for (i = 0; i < MAX_BLOCKS; i++) { free(g_block[i]); g_block[i] = NULL; }
}

const mtp_audio_config *mtp_audio_get_config(void) { return &g_cfg; }

int16_t *mtp_audio_acquire(void)
{
    unsigned head, tail;
    if (!g_dev_ready || g_acquired) return NULL;
    head = atomic_load_explicit(&g_head, memory_order_relaxed);
    tail = atomic_load_explicit(&g_tail, memory_order_acquire);
    if (head - tail >= g_cfg.block_count) return NULL;
    g_acquired = g_block[head % g_cfg.block_count];
    return g_acquired;
}

void mtp_audio_commit(void)
{
    unsigned head;
    if (!g_acquired) return;
    if (g_tap) {
        fwrite(g_acquired, sizeof(int16_t),
               (size_t)g_cfg.frames_per_block * g_cfg.channels, g_tap);
        g_tap_frames += g_cfg.frames_per_block;
    }
    head = atomic_load_explicit(&g_head, memory_order_relaxed);
    atomic_store_explicit(&g_head, head + 1u, memory_order_release);
    g_acquired = NULL;
}

unsigned mtp_audio_queued(void)
{
    unsigned head = atomic_load_explicit(&g_head, memory_order_relaxed);
    unsigned tail = atomic_load_explicit(&g_tail, memory_order_acquire);
    return head - tail;
}

mtp_status mtp_audio_wait(uint32_t timeout_us)
{
    struct timespec deadline;
    uint32_t cap;
    uint64_t t_enter;
    mtp_status rc = MTP_ERR_AGAIN;

    if (!g_dev_ready) return MTP_ERR_STATE;
    if (mtp_audio_queued() < g_cfg.block_count) return MTP_OK;
    if (timeout_us == 0u) return MTP_ERR_AGAIN;

    /* Cap each wait at one block period so that a signal lost to trylock
     * contention costs 2.7 ms of extra latency, never a stall. */
    cap = timeout_us;

    clock_gettime(CLOCK_REALTIME, &deadline);

    atomic_fetch_add_explicit(&g_waiters, 1u, memory_order_release);
    atomic_fetch_add_explicit(&g_waits, 1u, memory_order_relaxed);
    t_enter = mtp_time_us64();
    pthread_mutex_lock(&g_mtx);
    for (;;) {
        struct timespec step;
        uint32_t slice = cap < g_block_period_us ? cap : g_block_period_us;

        if (mtp_audio_queued() < g_cfg.block_count) { rc = MTP_OK; break; }
        if (slice == 0u) { rc = MTP_ERR_AGAIN; break; }

        step = deadline;
        step.tv_nsec += (long)slice * 1000L;
        step.tv_sec  += step.tv_nsec / 1000000000L;
        step.tv_nsec %= 1000000000L;
        deadline = step;
        cap -= slice;

        if (pthread_cond_timedwait(&g_cv, &g_mtx, &deadline) == ETIMEDOUT)
            atomic_fetch_add_explicit(&g_wait_timeouts, 1u, memory_order_relaxed);
    }
    pthread_mutex_unlock(&g_mtx);
    atomic_fetch_sub_explicit(&g_waiters, 1u, memory_order_release);
    {
        uint32_t took = (uint32_t)(mtp_time_us64() - t_enter);
        if (took > atomic_load_explicit(&g_wait_worst_us, memory_order_relaxed))
            atomic_store_explicit(&g_wait_worst_us, took, memory_order_relaxed);
    }
    return rc;
}

void desktop_audio_callback_stats(uint32_t *calls, uint32_t *max_frames,
                                  uint32_t *worst_gap_us)
{
    if (calls)        *calls        = atomic_load(&g_cb_calls);
    if (max_frames)   *max_frames   = atomic_load(&g_cb_max_frames);
    if (worst_gap_us) *worst_gap_us = atomic_load(&g_cb_worst_gap_us);
}

void desktop_audio_wait_stats(uint32_t *waits, uint32_t *timeouts,
                              uint32_t *worst_us)
{
    if (waits)    *waits    = atomic_load(&g_waits);
    if (timeouts) *timeouts = atomic_load(&g_wait_timeouts);
    if (worst_us) *worst_us = atomic_load(&g_wait_worst_us);
}

uint32_t mtp_audio_underruns(void)
{
    return atomic_load_explicit(&g_underruns, memory_order_relaxed);
}

/* ------------------------------------------------------------------ */

void desktop_audio_list_devices(void)
{
    ma_context ctx;
    ma_device_info *play = NULL, *cap = NULL;
    ma_uint32 nplay = 0, ncap = 0, i;
    ma_backend backend;
    ma_backend *backends = NULL;
    ma_uint32 backend_count = 0;

    if (strcmp(g_backend_name, "auto") != 0 &&
        backend_from_name(g_backend_name, &backend)) {
        backends = &backend; backend_count = 1;
    }
    if (ma_context_init(backends, backend_count, NULL, &ctx) != MA_SUCCESS) {
        printf("no audio backend available\n");
        return;
    }
    printf("backend: %s\n", ma_get_backend_name(ctx.backend));
    if (ma_context_get_devices(&ctx, &play, &nplay, &cap, &ncap) == MA_SUCCESS) {
        for (i = 0; i < nplay; i++)
            printf("  playback %2u: %s%s\n", i, play[i].name,
                   play[i].isDefault ? "  (default)" : "");
    }
    ma_context_uninit(&ctx);
}
