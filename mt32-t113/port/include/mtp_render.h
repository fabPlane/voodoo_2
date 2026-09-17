/* mtp_render.h - the render loop. Portable; the same code runs on the host
 * stub and on the T113. SPDX-License-Identifier: 0BSD */
#ifndef MTP_RENDER_H
#define MTP_RENDER_H

#include "mtp_platform.h"
#include "mtp_engine.h"
#include "mtp_midi_parser.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    uint32_t blocks;              /* audio blocks committed                */
    uint32_t underruns;           /* from the audio sink                   */
    uint32_t midi_bytes;
    uint32_t short_msgs;
    uint32_t sysex_msgs;
    uint32_t engine_backpressure; /* times the engine queue was full       */
    uint32_t worst_render_us;     /* worst single-block render time        */
    uint32_t worst_block_us;      /* worst whole-iteration time            */
    uint32_t min_queued;          /* lowest ring occupancy seen after a
                                   * commit: the real safety margin        */
} mtp_render_stats;

typedef struct {
    const mtp_engine_vtable *engine;
    mtp_engine              *inst;
    uint32_t                 frames_per_block;
    uint32_t                 output_rate;
    uint32_t                 target_queued; /* blocks to stay ahead by     */
    /* Schedule events this many output frames into the future so that the
     * relative arrival times of bytes within one batch survive. 0 = enqueue
     * at the engine's current position (lowest latency). See DESIGN.md. */
    uint32_t                 lookahead_frames;
    mtp_midi_parser          parser;
    uint8_t                  sysex_buf[MTP_SYSEX_MAX];
    /* staging for a sysex the engine refused: retried next block */
    uint8_t                  retry_buf[MTP_SYSEX_MAX];
    uint32_t                 retry_len;
    uint32_t                 retry_ts;
    mtp_render_stats         stats;
} mtp_render_ctx;

mtp_status mtp_render_init(mtp_render_ctx *ctx,
                           const mtp_engine_vtable *engine,
                           mtp_engine *inst,
                           uint32_t frames_per_block,
                           uint32_t output_rate,
                           uint32_t target_queued,
                           uint32_t lookahead_frames);

/* One iteration: drain MIDI, fill and commit as many free audio blocks as the
 * ring will take, up to max_blocks. Returns the number committed. Never
 * blocks -- the caller decides whether to wait or to do something else. */
unsigned mtp_render_pump(mtp_render_ctx *ctx, unsigned max_blocks);

/* Run until the MIDI source reports EOF and the engine has gone quiet, or
 * until max_blocks have been produced. This is the host stub's main loop; the
 * target uses mtp_render_pump() inside its own superloop. */
void mtp_render_run(mtp_render_ctx *ctx, uint32_t max_blocks);

#ifdef __cplusplus
}
#endif

#endif /* MTP_RENDER_H */
