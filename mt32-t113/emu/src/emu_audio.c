/* emu_audio.c - "virtual I2S": the audio sink, implementing mtp_audio.h.
 *
 * `-M virt` has no I2S and no DAC. What it does have is an architected timer
 * that runs on the host's real clock, and that is the only part of an I2S
 * output that the code above the seam can actually observe: a periodic
 * deadline that arrives whether or not the renderer is ready.
 *
 * So the sink is a ring of blocks in non-cacheable memory (the .dma section
 * from the linker script) with a timer interrupt in the place where the DMA
 * block-completion interrupt goes on the board. Every period the interrupt
 * consumes exactly one block, or, if the renderer has not committed one,
 * increments the underrun counter. That is the same event, produced by the
 * same mechanism -- an interrupt from a free-running counter -- and the render
 * loop above cannot tell the difference, which is the whole point.
 *
 * Producer/consumer discipline is the one described in
 * port/DESIGN.md section 3.3 and it is deliberately the discipline the T113
 * driver will need: two monotonic counters, each written by exactly one side,
 * with a DMB between the payload and the index. No disabled interrupts, no
 * read-modify-write shared between contexts.
 *
 * TWO SINKS, selected at open():
 *
 *   MTP sink "discard"  (default)  the ISR checksums each block and drops it.
 *                                  Nothing slows down; underruns are honest.
 *   MTP sink "wav"                 blocks are also copied to a RAM buffer and
 *                                  written to a host file over semihosting at
 *                                  close(). The copy is in producer context
 *                                  and the file write happens once, after the
 *                                  run, so the deadline behaviour is unchanged
 *                                  -- but the buffer is finite and says so.
 *
 * WHAT THIS PROVES AND DOES NOT. It proves the render loop meets a real
 * periodic deadline driven by real hardware (an interrupt, a timer, a vector
 * table) and reports underruns honestly. It proves nothing about the T113's
 * DMAC, its descriptor chain, its I2S block, or the PCM5102A -- and nothing
 * about speed, because QEMU's TCG models neither the A7 pipeline nor its
 * caches (docs/PLAN.md section 0.5).
 *
 * SPDX-License-Identifier: 0BSD
 */

#include <stdint.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>

#include "mtp_audio.h"
#include "mtp_log.h"
#include "mtp_time.h"
#include "emu.h"
#include "semihost.h"

extern char __dma_start[], __dma_end[];
void *dma_alloc(size_t n, size_t align);
uint32_t dma_used(void);

/* virtio_snd.c */
int      vsnd_open(uint32_t rate, uint16_t channels, uint32_t period_bytes,
                   uint32_t blocks, void (*on_complete)(void));
void     vsnd_close(void);
int      vsnd_submit(unsigned slot, const void *block, uint32_t bytes);
int      vsnd_ready(void);
uint32_t vsnd_completed(void);
unsigned vsnd_outstanding(void);
uint32_t vsnd_max_burst(void);

#define MAX_BLOCKS 8u

static mtp_audio_config g_cfg;
static int16_t         *g_ring;          /* in the non-cacheable window     */
static uint32_t         g_block_samples; /* frames * channels               */

/* SPSC indices. Monotonic, never wrapped, so "queued" is a subtraction and
 * there is no shared read-modify-write anywhere. */
static volatile uint32_t g_committed;    /* producer only */
static volatile uint32_t g_played;       /* ISR only      */
static volatile uint32_t g_underruns;    /* ISR only      */

static uint32_t g_checksum;              /* ISR only      */
static int      g_open;
static int      g_started;               /* tick running?                   */
static uint32_t g_period_us;

/* Which sink. "timer" is the default and is the one whose underrun count is
 * the conformance contract; "virtio" hands the same blocks to a real device
 * model and lets the device be the clock. */
typedef enum { SINK_TIMER = 0, SINK_VIRTIO } sink_kind;
static sink_kind g_sink = SINK_TIMER;
static int       g_sink_requested_virtio;

int strcmp(const char *a, const char *b);

void emu_audio_set_sink(const char *name)
{
    g_sink_requested_virtio = (name && strcmp(name, "virtio") == 0);
}

const char *emu_audio_sink_name(void)
{
    return g_sink == SINK_VIRTIO ? "virtio-sound" : "timer/discard";
}

/* --- the wav tap ------------------------------------------------------- */
static const char *g_wav_path;
static int16_t    *g_wav_buf;
static uint32_t    g_wav_cap_frames;
static uint32_t    g_wav_frames;
static uint32_t    g_wav_dropped;

void emu_audio_set_wav(const char *path) { g_wav_path = path; }

/* --- the "DMA" ---------------------------------------------------------- */

/* Touch every sample of the block the consumer just took. A sink that ignored
 * its data would not catch a renderer that committed a block it had not
 * finished writing. FNV-1a over the int16s; cheap, and order-sensitive. */
static void checksum_block(uint32_t slot)
{
    const int16_t *b = g_ring + slot * g_block_samples;
    uint32_t i, h = g_checksum;
    __asm__ volatile("dmb" ::: "memory");
    for (i = 0; i < g_block_samples; i++)
        h = (h ^ (uint32_t)(uint16_t)b[i]) * 16777619u;
    g_checksum = h;
}

/* SINK_TIMER: runs in the generic timer interrupt. This is where the I2S DMA
 * engine's block-completion interrupt lands on the board. */
static void block_consumed(void)
{
    if (g_committed == g_played) {
        g_underruns++;                  /* the DMA played silence           */
        return;
    }
    checksum_block(g_played % g_cfg.block_count);
    __asm__ volatile("dmb" ::: "memory");
    g_played++;
}

/* SINK_VIRTIO: runs in the virtio-mmio used-buffer interrupt. The device has
 * finished a period and the block is free again -- the same event, from a
 * device with its own clock instead of from ours. An underrun here is the
 * device draining the last period we gave it with nothing behind it, which is
 * precisely the condition that makes an I2S DMA ring click. */
static void virtio_block_done(void)
{
    if (g_committed == g_played) { g_underruns++; return; }
    checksum_block(g_played % g_cfg.block_count);
    __asm__ volatile("dmb" ::: "memory");
    g_played++;
    if (g_committed == g_played) g_underruns++;
}

/* --- mtp_audio.h -------------------------------------------------------- */

mtp_status mtp_audio_open(const mtp_audio_config *cfg)
{
    uint32_t bytes, period_us;

    if (g_open) return MTP_ERR_STATE;
    if (!cfg || cfg->channels != 2u || cfg->frames_per_block == 0u ||
        cfg->block_count == 0u || cfg->block_count > MAX_BLOCKS ||
        cfg->sample_rate == 0u)
        return MTP_ERR_INVAL;

    g_cfg = *cfg;
    g_block_samples = (uint32_t)cfg->frames_per_block * cfg->channels;
    bytes = g_block_samples * cfg->block_count * 2u;
    if (bytes > (uint32_t)(__dma_end - __dma_start)) {
        MTP_LOGE("audio ring %u B does not fit the %u B uncached window",
                 bytes, (unsigned)(__dma_end - __dma_start));
        return MTP_ERR_NOMEM;
    }

    g_ring = (int16_t *)dma_alloc(bytes, 64u);
    if (!g_ring) {
        MTP_LOGE("audio: no room in the %u B uncached window",
                 (unsigned)(__dma_end - __dma_start));
        return MTP_ERR_NOMEM;
    }
    memset(g_ring, 0, bytes);
    g_committed = 0u;
    g_played    = 0u;
    g_underruns = 0u;
    g_checksum  = 2166136261u;

    if (g_wav_path) {
        /* 4 MB caps a run at ~21 s of 48 kHz stereo. Beyond that the sink
         * counts what it could not keep and says so; it does not stop the run
         * and it does not stall the deadline. */
        g_wav_cap_frames = (4u * 1024u * 1024u) / (2u * cfg->channels);
        g_wav_buf = (int16_t *)malloc((size_t)g_wav_cap_frames * cfg->channels * 2u);
        if (!g_wav_buf) {
            MTP_LOGW("no room for the wav buffer; discarding audio instead");
            g_wav_cap_frames = 0u;
        }
        g_wav_frames = 0u;
        g_wav_dropped = 0u;
    }

    period_us = (uint32_t)(((uint64_t)cfg->frames_per_block * 1000000ull)
                           / cfg->sample_rate);
    if (period_us == 0u) period_us = 1u;

    g_sink = SINK_TIMER;
    if (g_sink_requested_virtio) {
        if (vsnd_open(cfg->sample_rate, cfg->channels,
                      g_block_samples * 2u, cfg->block_count,
                      virtio_block_done) == 0) {
            g_sink = SINK_VIRTIO;
        } else {
            /* A documented dead end is better than a silent one: say so and
             * carry on with the sink that always works. */
            MTP_LOGW("audio: virtio-sound unavailable, falling back to the "
                     "timer sink");
        }
    }

    g_open = 1;
    g_started = 0;
    g_period_us = period_us;
    /* The tick -- the "DMA" -- does NOT start here. On the board the DMA is
     * started once the ring has been primed, because a DMA engine that begins
     * on an empty ring plays a block of silence and reports an underrun for
     * it, and that underrun is an artefact of the start-up order rather than
     * a rendering failure. mtp_audio.h promises open() is safe to call early;
     * this is how that promise is kept. The clock starts on the first commit,
     * and every deadline after it is absolute. */

    MTP_LOGI("audio: %u Hz, %u frames/block, ring %u, block period %u us%s",
             cfg->sample_rate, cfg->frames_per_block, cfg->block_count,
             period_us, g_wav_path ? ", wav tap on" : "");
    MTP_LOGI("audio: sink = %s", emu_audio_sink_name());
    MTP_LOGI("audio: ring at %p, %u B, Normal Non-cacheable",
             (void *)g_ring, bytes);
    return MTP_OK;
}

static void wav_flush(void)
{
    uint8_t hdr[44];
    uint32_t data_bytes, i;
    int h;

    if (!g_wav_path || g_wav_frames == 0u) return;
    data_bytes = g_wav_frames * g_cfg.channels * 2u;

    h = semihost_open(g_wav_path, SEMIHOST_W);
    if (h < 0) {
        MTP_LOGE("wav: host would not open '%s' (is semihosting on?)",
                 g_wav_path);
        return;
    }
#define PUT32(o, v) do { hdr[o]=(uint8_t)(v); hdr[(o)+1]=(uint8_t)((v)>>8); \
                         hdr[(o)+2]=(uint8_t)((v)>>16); hdr[(o)+3]=(uint8_t)((v)>>24); } while (0)
#define PUT16(o, v) do { hdr[o]=(uint8_t)(v); hdr[(o)+1]=(uint8_t)((v)>>8); } while (0)
    memcpy(hdr + 0, "RIFF", 4);  PUT32(4, 36u + data_bytes);
    memcpy(hdr + 8, "WAVEfmt ", 8); PUT32(16, 16u);
    PUT16(20, 1u); PUT16(22, g_cfg.channels);
    PUT32(24, g_cfg.sample_rate);
    PUT32(28, g_cfg.sample_rate * g_cfg.channels * 2u);
    PUT16(32, (uint16_t)(g_cfg.channels * 2u)); PUT16(34, 16u);
    memcpy(hdr + 36, "data", 4); PUT32(40, data_bytes);
#undef PUT32
#undef PUT16
    semihost_write(h, hdr, 44u);

    /* 64 KB at a time: one semihosting round trip per chunk rather than per
     * block, and small enough that a JTAG probe can do it too. */
    for (i = 0; i < data_bytes; ) {
        uint32_t n = data_bytes - i;
        if (n > 65536u) n = 65536u;
        if (semihost_write(h, (const uint8_t *)g_wav_buf + i, n) < 0) break;
        i += n;
    }
    semihost_close(h);
    MTP_LOGI("wav: %s, %u frames (%u.%03u s)%s", g_wav_path, g_wav_frames,
             g_wav_frames / g_cfg.sample_rate,
             (g_wav_frames % g_cfg.sample_rate) * 1000u / g_cfg.sample_rate,
             g_wav_dropped ? " TRUNCATED" : "");
    if (g_wav_dropped)
        MTP_LOGW("wav: %u frames did not fit the buffer", g_wav_dropped);
}

void mtp_audio_close(void)
{
    if (!g_open) return;
    if (g_started) emu_tick_stop();
    g_started = 0;
    if (g_sink == SINK_VIRTIO) vsnd_close();
    g_open = 0;
    wav_flush();
    g_wav_buf = NULL;
    g_wav_frames = 0u;
}

const mtp_audio_config *mtp_audio_get_config(void) { return &g_cfg; }

int16_t *mtp_audio_acquire(void)
{
    if (!g_open) return NULL;
    if (g_committed - g_played >= g_cfg.block_count) return NULL;
    return g_ring + (g_committed % g_cfg.block_count) * g_block_samples;
}

void mtp_audio_commit(void)
{
    if (!g_open) return;

    if (g_wav_buf && g_wav_cap_frames) {
        if (g_wav_frames + g_cfg.frames_per_block <= g_wav_cap_frames) {
            memcpy(g_wav_buf + (size_t)g_wav_frames * g_cfg.channels,
                   g_ring + (g_committed % g_cfg.block_count) * g_block_samples,
                   g_block_samples * 2u);
            g_wav_frames += g_cfg.frames_per_block;
        } else {
            g_wav_dropped += g_cfg.frames_per_block;
        }
    }

    /* Publish the payload before the index. On a single core this is belt and
     * braces; it is here because port/DESIGN.md section 3.3 says to write it
     * as if the consumer were another core, and on the T113 the consumer is a
     * DMA engine, which is worse. */
    __asm__ volatile("dmb" ::: "memory");

    if (g_sink == SINK_VIRTIO) {
        /* Hand the block itself to the device -- no copy. It stays untouched
         * until its completion arrives, because acquire() will not return it
         * again until g_played has passed it. */
        unsigned slot = g_committed % g_cfg.block_count;
        if (vsnd_submit(slot, g_ring + slot * g_block_samples,
                        g_block_samples * 2u) < 0) {
            MTP_LOGW("audio: virtio tx queue refused a period");
            return;                     /* do not advance: the block is ours */
        }
        g_committed++;
        return;
    }

    g_committed++;
    if (!g_started) {
        g_started = 1;
        emu_tick_start(g_period_us, block_consumed);
    }
}

unsigned mtp_audio_queued(void)
{
    return (unsigned)(g_committed - g_played);
}

mtp_status mtp_audio_wait(uint32_t timeout_us)
{
    uint32_t t0 = mtp_time_us();

    if (g_committed - g_played < g_cfg.block_count) return MTP_OK;
    if (timeout_us == 0u) return MTP_ERR_AGAIN;

    for (;;) {
        /* The superloop form from port/DESIGN.md section 6.4: WFI until the
         * sink interrupt frees a block. Under an RTOS this becomes
         * rt_sem_take() and nothing above changes. */
        emu_wfi();
        if (g_committed - g_played < g_cfg.block_count) return MTP_OK;
        if (mtp_time_us() - t0 >= timeout_us) return MTP_ERR_AGAIN;
    }
}

uint32_t mtp_audio_underruns(void) { return g_underruns; }

/* Not part of mtp_audio.h: the emulator's own evidence that the samples were
 * read at the deadline. main() prints it. */
uint32_t emu_audio_checksum(void) { return g_checksum; }
uint32_t emu_audio_played(void)   { return g_played; }
uint32_t emu_audio_dma_used(void) { return dma_used(); }
uint32_t emu_audio_virtio_completed(void)
{ return g_sink == SINK_VIRTIO ? vsnd_completed() : 0u; }
uint32_t emu_audio_virtio_max_burst(void)
{ return g_sink == SINK_VIRTIO ? vsnd_max_burst() : 0u; }
