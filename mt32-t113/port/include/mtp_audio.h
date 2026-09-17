/* mtp_audio.h - the audio sink.
 *
 * Model: the platform owns a ring of fixed-size blocks that an I2S DMA engine
 * plays back to back, for ever. The application acquires the next free block,
 * fills it with interleaved stereo int16, and commits it. It never memcpys and
 * it never blocks inside the driver.
 *
 * The whole interface is five calls plus two counters. Everything harder --
 * sample rate conversion, mixing, volume -- is the application's problem,
 * on purpose: see PORTING.md, "Sample rate: why 48 kHz is free".
 *
 * Threading: acquire/commit are called from exactly one context (the render
 * loop). The DMA completion interrupt only advances the play cursor. That is
 * a single-producer/single-consumer ring and the implementation is responsible
 * for the barriers.
 *
 * SPDX-License-Identifier: 0BSD
 */
#ifndef MTP_AUDIO_H
#define MTP_AUDIO_H

#include "mtp_platform.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    uint32_t sample_rate;       /* 48000 on the target; see PORTING.md      */
    uint16_t channels;          /* 2. Present so a mono build is a compile
                                 * error rather than a silent half-speed bug */
    uint16_t frames_per_block;  /* 128 on the target                        */
    uint8_t  block_count;       /* ring depth, 3 on the target              */
} mtp_audio_config;

/* Opens the sink and starts the DMA. Until the first block is committed the
 * ring holds silence, so opening early is safe. */
mtp_status mtp_audio_open(const mtp_audio_config *cfg);
void       mtp_audio_close(void);

/* The negotiated configuration -- an implementation may not be able to hit the
 * requested sample rate exactly and says so here rather than lying. */
const mtp_audio_config *mtp_audio_get_config(void);

/* Next writable block: frames_per_block * channels int16 samples, or NULL if
 * every block is already queued. Never blocks. Must be paired with commit. */
int16_t *mtp_audio_acquire(void);

/* Hands the acquired block to the DMA ring. */
void mtp_audio_commit(void);

/* Blocks queued but not yet played, 0..block_count. This is the render loop's
 * only feedback signal: it is how far ahead of the DMA we are. */
unsigned mtp_audio_queued(void);

/* Waits until at least one block is free, or timeout_us elapses.
 * Under an RTOS this is a semaphore take; in a superloop it is WFI in a loop.
 * timeout_us == 0 means poll once and return immediately.
 * Returns MTP_OK if a block is now free, MTP_ERR_AGAIN on timeout. */
mtp_status mtp_audio_wait(uint32_t timeout_us);

/* Monotonic count of blocks the DMA played that the application had not
 * committed -- i.e. audible dropouts. The number that decides whether the
 * design works. */
uint32_t mtp_audio_underruns(void);

#ifdef __cplusplus
}
#endif

#endif /* MTP_AUDIO_H */
