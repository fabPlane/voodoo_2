/* mtp_engine.h - the seam between the render loop and the synthesiser.
 *
 * This is not a "sound engine abstraction layer"; it is the six calls the
 * render loop in src/mtp_render.c actually makes. It exists for exactly one
 * reason: so the whole port -- audio ring, MIDI parser, timing, storage --
 * can be built and run before mt32emu is linked in and before any ROMs exist.
 * host/engine_fake.c is the proof.
 *
 * Everything maps onto mt32emu 2.8 without adaptation:
 *   short_msg()        -> Synth::playMsg(msg, timestamp)          (Synth.h:384)
 *   sysex()            -> Synth::playSysex(data, len, timestamp)  (Synth.h:387)
 *   render()           -> Synth::render(Bit16s*, len)             (Synth.h:565)
 *   rendered_samples() -> Synth::getInternalRenderedSampleCount() (Synth.h:372)
 *
 * Timestamps are in *engine-native* samples, which for mt32emu is 32000 Hz
 * regardless of the output rate (mt32emu/globals.h:94, Synth.h:370-377). The
 * render loop converts; the engine never sees wall-clock time.
 *
 * SPDX-License-Identifier: 0BSD
 */
#ifndef MTP_ENGINE_H
#define MTP_ENGINE_H

#include "mtp_platform.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct mtp_engine mtp_engine;

typedef struct {
    const char *control_rom_path;  /* NULL for engines that need no ROMs */
    const char *pcm_rom_path;
    uint32_t    output_rate;       /* Hz the engine must produce          */
    uint32_t    max_partials;      /* 32 = a real MT-32                   */
    int         reverb_enabled;
} mtp_engine_config;

typedef struct {
    const char *name;

    mtp_status (*open)(const mtp_engine_config *cfg, mtp_engine **out);
    void       (*close)(mtp_engine *e);

    /* Native sample rate of the engine's internal clock, for timestamps.
     * 32000 for mt32emu. */
    uint32_t   (*timebase_rate)(mtp_engine *e);

    /* Sample count on that clock since open(). Monotonic. */
    uint32_t   (*rendered_samples)(mtp_engine *e);

    /* Enqueue. msg is status | data1<<8 | data2<<16, as mt32emu wants it.
     * Returns MTP_ERR_AGAIN if the engine's queue is full -- the render loop
     * retries next block rather than dropping. */
    mtp_status (*short_msg)(mtp_engine *e, uint32_t msg, uint32_t t_samples);
    mtp_status (*sysex)(mtp_engine *e, const uint8_t *data, uint32_t len,
                        uint32_t t_samples);

    /* Produce exactly `frames` interleaved stereo int16 frames at
     * cfg->output_rate. Must not allocate, must not block, must not fail. */
    void       (*render)(mtp_engine *e, int16_t *stereo, uint32_t frames);

    /* Optional, may be NULL: 20-char MT-32 LCD text, for a display later. */
    void       (*get_display)(mtp_engine *e, char *dst21);
} mtp_engine_vtable;

/* Engines available in this build. */
extern const mtp_engine_vtable mtp_engine_fake;     /* host/engine_fake.c    */
#ifdef MTP_WITH_MT32EMU
extern const mtp_engine_vtable mtp_engine_mt32emu;  /* host/engine_mt32emu.cpp */
#endif

#ifdef __cplusplus
}
#endif

#endif /* MTP_ENGINE_H */
