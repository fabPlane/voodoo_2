/* mtp_render.c - the render loop. See DESIGN.md, "How the render loop stays
 * ahead of DMA". SPDX-License-Identifier: 0BSD */

#include "mtp_render.h"
#include "mtp_audio.h"
#include "mtp_midi.h"
#include "mtp_time.h"
#include "mtp_log.h"

#include <string.h>

#define MIDI_BATCH 64u

/* ------------------------------------------------------------------ */
/* MIDI -> engine                                                     */

static uint32_t engine_now_plus(mtp_render_ctx *ctx)
{
    uint32_t now = ctx->engine->rendered_samples(ctx->inst);
    if (ctx->lookahead_frames == 0u) return now;
    {
        /* lookahead is expressed in output frames; convert to engine ticks */
        uint64_t rate = ctx->engine->timebase_rate(ctx->inst);
        uint64_t ahead = (uint64_t)ctx->lookahead_frames * rate
                       / (ctx->output_rate ? ctx->output_rate : 1u);
        return now + (uint32_t)ahead;
    }
}

static void on_message(const mtp_midi_msg *m, void *user)
{
    mtp_render_ctx *ctx = (mtp_render_ctx *)user;
    uint32_t ts = engine_now_plus(ctx);

    switch (m->kind) {
    case MTP_MSG_REALTIME:
        /* Clock, start, stop, active sensing. The MT-32 ignores all of them
         * except that Active Sensing timeout is a real feature of hardware
         * units; we do not emulate it. Pass them through so the engine can
         * count them, but do not let them occupy a queue slot on failure. */
        ctx->engine->short_msg(ctx->inst, m->msg, ts);
        break;

    case MTP_MSG_SHORT:
        ctx->stats.short_msgs++;
        if (ctx->engine->short_msg(ctx->inst, m->msg, ts) != MTP_OK)
            ctx->stats.engine_backpressure++;
        break;

    case MTP_MSG_SYSEX:
        ctx->stats.sysex_msgs++;
        if (ctx->engine->sysex(ctx->inst, m->sysex, m->sysex_len, ts) != MTP_OK) {
            /* The engine's sysex storage is full. Stash it and retry next
             * block rather than dropping a patch dump on the floor. Only one
             * slot: a second failure in the same window is a real overflow and
             * is counted. */
            ctx->stats.engine_backpressure++;
            if (ctx->retry_len == 0u && m->sysex_len <= sizeof(ctx->retry_buf)) {
                memcpy(ctx->retry_buf, m->sysex, m->sysex_len);
                ctx->retry_len = m->sysex_len;
                ctx->retry_ts  = ts;
            } else {
                MTP_LOGW("sysex dropped (%u bytes)", (unsigned)m->sysex_len);
            }
        }
        break;
    }
}

static void drain_midi(mtp_render_ctx *ctx)
{
    mtp_midi_byte batch[MIDI_BATCH];
    size_t n;

    if (ctx->retry_len != 0u) {
        if (ctx->engine->sysex(ctx->inst, ctx->retry_buf, ctx->retry_len,
                               engine_now_plus(ctx)) == MTP_OK) {
            ctx->retry_len = 0u;
        } else {
            /* Still full. Do not read more MIDI this round: back-pressure all
             * the way to the UART FIFO is better than silently reordering. */
            return;
        }
    }

    for (;;) {
        n = mtp_midi_read(batch, MIDI_BATCH);
        if (n == 0u) break;
        ctx->stats.midi_bytes += (uint32_t)n;
        mtp_midi_parser_feed(&ctx->parser, batch, n);
        if (ctx->retry_len != 0u) break;   /* engine went full mid-batch */
        if (n < MIDI_BATCH) break;
    }
}

/* ------------------------------------------------------------------ */

mtp_status mtp_render_init(mtp_render_ctx *ctx,
                           const mtp_engine_vtable *engine,
                           mtp_engine *inst,
                           uint32_t frames_per_block,
                           uint32_t output_rate,
                           uint32_t target_queued,
                           uint32_t lookahead_frames)
{
    if (!ctx || !engine || !inst || frames_per_block == 0u)
        return MTP_ERR_INVAL;

    memset(ctx, 0, sizeof(*ctx));
    ctx->engine           = engine;
    ctx->inst             = inst;
    ctx->frames_per_block = frames_per_block;
    ctx->output_rate      = output_rate;
    ctx->target_queued    = target_queued;
    ctx->lookahead_frames = lookahead_frames;
    ctx->stats.min_queued = 0xFFFFFFFFu;
    mtp_midi_parser_init(&ctx->parser, ctx->sysex_buf, MTP_SYSEX_MAX,
                         on_message, ctx);
    return MTP_OK;
}

unsigned mtp_render_pump(mtp_render_ctx *ctx, unsigned max_blocks)
{
    unsigned produced = 0;

    while (produced < max_blocks) {
        int16_t *block;
        uint32_t t0, t1, t2;
        unsigned queued;

        /* MIDI first, always: an event that arrives one microsecond before we
         * render a block should land in that block, not the next one. */
        t0 = mtp_time_us();
        drain_midi(ctx);

        block = mtp_audio_acquire();
        if (block == NULL) break;            /* ring full: we are ahead */

        t1 = mtp_time_us();
        ctx->engine->render(ctx->inst, block, ctx->frames_per_block);
        t2 = mtp_time_us();

        mtp_audio_commit();
        produced++;
        ctx->stats.blocks++;

        if ((t2 - t1) > ctx->stats.worst_render_us)
            ctx->stats.worst_render_us = t2 - t1;
        if ((t2 - t0) > ctx->stats.worst_block_us)
            ctx->stats.worst_block_us = t2 - t0;

        queued = mtp_audio_queued();
        if (queued < ctx->stats.min_queued) ctx->stats.min_queued = queued;

        /* Once we are the configured distance ahead of the DMA, stop. Filling
         * the ring to the brim would only add latency; leaving a block free
         * means an interrupt can be serviced without us spinning. */
        if (queued >= ctx->target_queued) break;
    }

    ctx->stats.underruns = mtp_audio_underruns();
    return produced;
}

void mtp_render_run(mtp_render_ctx *ctx, uint32_t max_blocks)
{
    uint32_t idle_blocks = 0;

    while (ctx->stats.blocks < max_blocks) {
        unsigned n = mtp_render_pump(ctx, 8);
        if (n == 0u) {
            /* Ring is full. On the target this is a WFI; on the host the stub
             * returns immediately and we simply loop. */
            if (mtp_audio_wait(5000u) != MTP_OK) {
                MTP_LOGW("audio sink stalled");
                break;
            }
            continue;
        }
        if (mtp_midi_eof()) {
            /* Source exhausted: keep rendering for a while so releases and
             * reverb tails finish, then stop. 1 second at block granularity. */
            idle_blocks += n;
            if (idle_blocks * ctx->frames_per_block > ctx->output_rate) break;
        } else {
            idle_blocks = 0;
        }
    }
}
