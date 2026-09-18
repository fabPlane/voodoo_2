/* desktop_status.h - the honest runtime readout.
 *
 * Four things this build has that neither port/host nor the T113 has: a real
 * clock, a real sound card pulling at it, a terminal, and nothing else to do.
 * So it reports, continuously, the numbers that decide whether the design
 * works: underruns, ring occupancy, MIDI parse errors, and a real-time factor.
 *
 * ABOUT THE REAL-TIME FACTOR. It is measured on whatever machine you are
 * sitting at. docs/PLAN.md section 0 gates the whole project on an RTF of 0.5
 * to 0.6 on one Cortex-A7 at 1.2 GHz, and this number is not that number and
 * cannot be converted into it: different ISA, different cache, different memory
 * system, a PCM ROM that fits in an x86 L2 and will not fit in the A7's. Every
 * string this file prints says so, because a number printed next to the word
 * "real-time factor" will otherwise be quoted as if it were the gate.
 *
 * SPDX-License-Identifier: 0BSD
 */
#ifndef DESKTOP_STATUS_H
#define DESKTOP_STATUS_H

#include "mtp_platform.h"

typedef struct {
    uint64_t wall_us;          /* since the render loop started          */
    uint64_t audio_us;         /* audio committed, in playing time       */
    uint64_t render_us;        /* CPU spent inside mtp_render_pump()     */
    uint32_t blocks;
    uint32_t underruns;
    unsigned queued;           /* ring occupancy right now               */
    uint32_t min_queued;       /* lowest ever seen after a commit        */
    uint32_t block_period_us;
    uint32_t worst_render_us;
    uint32_t midi_bytes;
    uint32_t short_msgs;
    uint32_t sysex_msgs;
    uint32_t realtime_msgs;
    uint32_t midi_overruns;    /* bytes lost by the FIFO                 */
    uint32_t parse_dropped;    /* orphan data bytes                      */
    uint32_t parse_truncated;  /* sysex longer than 32 kB                */
    uint32_t parse_aborted;    /* sysex interrupted by a status byte     */
    uint32_t backpressure;     /* engine refused an event                */
    uint32_t ring_peak;        /* peak MIDI FIFO occupancy, in bytes     */
    /* How the audio device behaved. On the T113 these describe the I2S DMA
     * completion interrupt; here they describe the OS, and the difference is
     * the single biggest way this build is not the target. */
    uint32_t dev_calls;
    uint32_t dev_max_frames;   /* largest single request                  */
    uint32_t dev_worst_gap_us; /* longest interval between requests       */
    uint32_t waits;            /* times the render loop slept             */
    uint32_t wait_timeouts;    /* ... and woke on its own timer           */
    uint32_t wait_worst_us;
} desktop_status_sample;

void desktop_status_init(unsigned interval_ms, int enabled);

/* Rate-limited one-line readout on stderr. Safe to call every iteration. */
void desktop_status_tick(const desktop_status_sample *s);

/* Erases the in-progress status line so a log line can be written over it.
 * Called by desktop_log.c; a no-op when the status line is off. */
void desktop_status_clear_line(void);

/* The full report, at the end of a run. */
void desktop_status_summary(const desktop_status_sample *s,
                            const char *engine_name,
                            uint32_t sample_rate,
                            uint32_t frames_per_block,
                            uint32_t ring_blocks);

#endif /* DESKTOP_STATUS_H */
