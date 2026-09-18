/* virtio_snd.c - a virtio-sound playback stream, used as a real audio device.
 *
 * This is the second of the three audio sinks (see emu_audio.c). Where the
 * default sink is a timer interrupt pretending to be a DMA engine, this one is
 * an actual device model: it has its own clock, it consumes period buffers on
 * that clock, and it tells us when a buffer is free again. Feed it late and
 * the audio it writes is wrong; reuse a buffer before its completion arrives
 * and the audio it writes is wrong. That is a much better rehearsal for the
 * T113's I2S-plus-DMAC path than anything that cannot fail.
 *
 * The mapping onto what the board will do:
 *
 *   virtio-sound                        T113
 *   ------------------------------      --------------------------------
 *   tx virtqueue                        DMAC linked descriptor list
 *   one tx request = one period         one descriptor = one block
 *   used-ring completion + IRQ          per-descriptor completion IRQ
 *   period_bytes                        block size, 128 frames (DESIGN 2.2)
 *   buffer_bytes                        ring depth x block size
 *   posted-but-uncompleted requests     blocks queued ahead of the play cursor
 *
 * Protocol constants and structure layouts are taken from the virtio 1.2
 * specification as published in Linux's uapi header,
 * /usr/include/linux/virtio_snd.h; they are restated here rather than included
 * so that this file does not depend on Linux headers being installed.
 *
 * SPDX-License-Identifier: 0BSD
 */

#include <stdint.h>
#include <stddef.h>
#include "emu.h"
#include "virtio.h"
#include "mtp_log.h"
#include "mtp_time.h"

void *dma_alloc(size_t n, size_t align);
void *memset(void *d, int c, size_t n);
int printf(const char *fmt, ...);

#define VIRTIO_ID_SOUND     25u
#define VIRTIO_F_VERSION_1  (1ull << 32)

#define VQ_CONTROL  0u
#define VQ_TX       2u

/* request codes */
#define R_PCM_INFO          0x0100u
#define R_PCM_SET_PARAMS    0x0101u
#define R_PCM_PREPARE       0x0102u
#define R_PCM_RELEASE       0x0103u
#define R_PCM_START         0x0104u
#define R_PCM_STOP          0x0105u
/* status codes */
#define S_OK                0x8000u
/* enums */
#define FMT_S16             5u
#define RATE_48000          7u
#define D_OUTPUT            0u

struct snd_hdr      { uint32_t code; };
struct snd_query    { uint32_t code; uint32_t start_id; uint32_t count; uint32_t size; };
struct snd_pcm_hdr  { uint32_t code; uint32_t stream_id; };
struct snd_set_params {
    uint32_t code; uint32_t stream_id;
    uint32_t buffer_bytes; uint32_t period_bytes; uint32_t features;
    uint8_t  channels; uint8_t format; uint8_t rate; uint8_t padding;
};
struct snd_info     { uint32_t hda_fn_nid; };
struct snd_pcm_info {
    struct snd_info hdr;
    uint32_t features;
    uint32_t formats_lo, formats_hi;
    uint32_t rates_lo, rates_hi;
    uint8_t  direction; uint8_t channels_min; uint8_t channels_max;
    uint8_t  padding[5];
};
struct snd_xfer     { uint32_t stream_id; };
struct snd_status   { uint32_t status; uint32_t latency_bytes; };

#define MAX_BLOCKS 8u

static virtio_dev  g_dev;
static virtq       g_cq, g_txq;
static int         g_ready;
static uint32_t    g_stream_id;

/* Per-block request headers and status words, in uncached memory because the
 * device reads and writes them directly. */
static struct snd_xfer   *g_xfer;
static struct snd_status *g_status;

static volatile uint32_t g_completed;
static volatile uint32_t g_max_burst;   /* completions reclaimed in one IRQ    */
static void (*g_on_complete)(void);

/* Control-queue round trip, polled. Only used during setup, where blocking is
 * fine and interrupts are not yet wanted. */
static int ctl(const void *req, uint32_t req_len, void *resp, uint32_t resp_len)
{
    virtio_sg sg[2];
    uint64_t deadline;

    sg[0].addr = (void *)(uintptr_t)req; sg[0].len = req_len; sg[0].device_writes = 0;
    sg[1].addr = resp;                   sg[1].len = resp_len; sg[1].device_writes = 1;
    if (virtq_add(&g_cq, sg, 2u) < 0) return -1;
    virtq_notify(&g_cq);

    deadline = mtp_time_us64() + 500000ull;
    while (virtq_reclaim(&g_cq, NULL) < 0) {
        if (mtp_time_us64() > deadline) {
            MTP_LOGE("virtio-snd: control request 0x%04x timed out",
                     ((const struct snd_hdr *)req)->code);
            return -1;
        }
    }
    virtio_irq_ack(&g_dev, virtio_irq_status(&g_dev));
    return 0;
}

static int simple_pcm_cmd(uint32_t code)
{
    struct snd_pcm_hdr *req = (struct snd_pcm_hdr *)dma_alloc(sizeof(*req), 16u);
    struct snd_hdr *resp = (struct snd_hdr *)dma_alloc(sizeof(*resp), 16u);
    if (!req || !resp) return -1;
    req->code = code;
    req->stream_id = g_stream_id;
    resp->code = 0u;
    if (ctl(req, sizeof(*req), resp, sizeof(*resp)) < 0) return -1;
    if (resp->code != S_OK) {
        MTP_LOGE("virtio-snd: command 0x%04x returned status 0x%04x",
                 code, resp->code);
        return -1;
    }
    return 0;
}

static void snd_isr(void)
{
    uint32_t st = virtio_irq_status(&g_dev);
    if (st & 1u) {                      /* used buffer notification */
        uint32_t burst = 0u;
        while (virtq_reclaim(&g_txq, NULL) >= 0) {
            g_completed++;
            burst++;
            if (g_on_complete) g_on_complete();
        }
        /* How many periods the device hands back at once. QEMU's audio
         * subsystem services its backends on a ~10 ms timer, so it takes
         * several 2.67 ms periods in one go -- which is why the ring depth
         * this sink needs is set by the consumer's service granularity and
         * not only by the renderer's slack. See FINDINGS.md. */
        if (burst > g_max_burst) g_max_burst = burst;
    }
    virtio_irq_ack(&g_dev, st);
}

int vsnd_open(uint32_t rate, uint16_t channels, uint32_t period_bytes,
              uint32_t blocks, void (*on_complete)(void))
{
    uint64_t got = 0;
    uint32_t streams, i;

    if (rate != 48000u) {
        MTP_LOGW("virtio-snd: only 48000 Hz is wired up here, asked for %u", rate);
        return -1;
    }
    if (blocks > MAX_BLOCKS) return -1;

    if (virtio_mmio_find(VIRTIO_ID_SOUND, &g_dev) < 0) {
        MTP_LOGW("virtio-snd: no virtio-sound device on any mmio transport");
        return -1;
    }
    MTP_LOGI("virtio-snd: transport slot %u at %p, irq %u",
             g_dev.slot, (void *)g_dev.base, g_dev.irq);

    if (virtio_dev_init(&g_dev, VIRTIO_F_VERSION_1, &got) < 0) return -1;
    if (virtq_setup(&g_dev, VQ_CONTROL, &g_cq) < 0) goto fail;
    if (virtq_setup(&g_dev, VQ_TX, &g_txq) < 0) goto fail;
    if (virtio_dev_ready(&g_dev) < 0) goto fail;

    streams = virtio_config_read32(&g_dev, 4u);     /* config.streams */
    MTP_LOGI("virtio-snd: %u jacks, %u streams, %u chmaps",
             virtio_config_read32(&g_dev, 0u), streams,
             virtio_config_read32(&g_dev, 8u));
    if (streams == 0u) { MTP_LOGE("virtio-snd: device has no PCM streams"); goto fail; }

    /* Pick the first output stream rather than assuming stream 0 is one. */
    {
        struct snd_query *q = (struct snd_query *)dma_alloc(sizeof(*q), 16u);
        uint32_t n = streams;
        size_t resp_len = sizeof(struct snd_hdr) + n * sizeof(struct snd_pcm_info);
        uint8_t *resp = (uint8_t *)dma_alloc(resp_len, 16u);
        struct snd_pcm_info *info;
        if (!q || !resp) goto fail;
        memset(resp, 0, resp_len);
        q->code = R_PCM_INFO; q->start_id = 0u; q->count = n;
        q->size = (uint32_t)sizeof(struct snd_pcm_info);
        if (ctl(q, sizeof(*q), resp, (uint32_t)resp_len) < 0) goto fail;
        if (((struct snd_hdr *)resp)->code != S_OK) {
            MTP_LOGE("virtio-snd: PCM_INFO failed, status 0x%04x",
                     ((struct snd_hdr *)resp)->code);
            goto fail;
        }
        info = (struct snd_pcm_info *)(resp + sizeof(struct snd_hdr));
        g_stream_id = 0xFFFFFFFFu;
        for (i = 0; i < n; i++) {
            if (info[i].direction == D_OUTPUT &&
                info[i].channels_min <= channels && info[i].channels_max >= channels) {
                g_stream_id = i;
                MTP_LOGI("virtio-snd: stream %u is output, %u..%u channels",
                         i, info[i].channels_min, info[i].channels_max);
                break;
            }
        }
        if (g_stream_id == 0xFFFFFFFFu) {
            MTP_LOGE("virtio-snd: no output stream takes %u channels", channels);
            goto fail;
        }
    }

    {
        struct snd_set_params *p =
            (struct snd_set_params *)dma_alloc(sizeof(*p), 16u);
        struct snd_hdr *resp = (struct snd_hdr *)dma_alloc(sizeof(*resp), 16u);
        if (!p || !resp) goto fail;
        memset(p, 0, sizeof(*p));
        p->code         = R_PCM_SET_PARAMS;
        p->stream_id    = g_stream_id;
        p->buffer_bytes = period_bytes * blocks;
        p->period_bytes = period_bytes;
        p->features     = 0u;
        p->channels     = (uint8_t)channels;
        p->format       = FMT_S16;
        p->rate         = RATE_48000;
        resp->code = 0u;
        if (ctl(p, sizeof(*p), resp, sizeof(*resp)) < 0) goto fail;
        if (resp->code != S_OK) {
            MTP_LOGE("virtio-snd: SET_PARAMS (buffer %u, period %u) status 0x%04x",
                     p->buffer_bytes, p->period_bytes, resp->code);
            goto fail;
        }
    }

    if (simple_pcm_cmd(R_PCM_PREPARE) < 0) goto fail;

    g_xfer   = (struct snd_xfer *)dma_alloc(sizeof(*g_xfer) * MAX_BLOCKS, 16u);
    g_status = (struct snd_status *)dma_alloc(sizeof(*g_status) * MAX_BLOCKS, 16u);
    if (!g_xfer || !g_status) goto fail;
    for (i = 0; i < MAX_BLOCKS; i++) { g_xfer[i].stream_id = g_stream_id;
                                       g_status[i].status = 0u; }

    g_on_complete = on_complete;
    g_completed = 0u;
    g_max_burst = 0u;
    emu_gic_set_handler(g_dev.irq, snd_isr);
    emu_gic_enable(g_dev.irq, 0x80u);
    emu_irq_enable();

    if (simple_pcm_cmd(R_PCM_START) < 0) goto fail;

    g_ready = 1;
    MTP_LOGI("virtio-snd: stream %u started, period %u B, buffer %u B",
             g_stream_id, period_bytes, period_bytes * blocks);
    return 0;

fail:
    virtio_dev_fail(&g_dev);
    return -1;
}

void vsnd_close(void)
{
    if (!g_ready) return;
    simple_pcm_cmd(R_PCM_STOP);
    simple_pcm_cmd(R_PCM_RELEASE);
    g_ready = 0;
}

/* Post one period. `block` must stay untouched until its completion arrives --
 * which is exactly the audio ring's rule, so the caller need do nothing
 * special beyond not reusing a block it has not seen played. */
int vsnd_submit(unsigned slot, const void *block, uint32_t bytes)
{
    virtio_sg sg[3];
    if (!g_ready || slot >= MAX_BLOCKS) return -1;

    g_status[slot].status = 0u;
    sg[0].addr = &g_xfer[slot];   sg[0].len = (uint32_t)sizeof(struct snd_xfer);
    sg[0].device_writes = 0;
    sg[1].addr = (void *)(uintptr_t)block; sg[1].len = bytes;
    sg[1].device_writes = 0;
    sg[2].addr = &g_status[slot]; sg[2].len = (uint32_t)sizeof(struct snd_status);
    sg[2].device_writes = 1;

    if (virtq_add(&g_txq, sg, 3u) < 0) return -1;
    virtq_notify(&g_txq);
    return 0;
}

int      vsnd_ready(void)       { return g_ready; }
uint32_t vsnd_completed(void)   { return g_completed; }
unsigned vsnd_outstanding(void) { return virtq_outstanding(&g_txq); }
uint32_t vsnd_max_burst(void)   { return g_max_burst; }
