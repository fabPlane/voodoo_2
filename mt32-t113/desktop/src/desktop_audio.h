/* desktop_audio.h - the desktop-only knobs on the mtp_audio implementation.
 *
 * mtp_audio.h itself has no notion of a backend or a device name, and should
 * not: on the T113 there is exactly one I2S port. These setters are the
 * desktop's equivalent of the target's pinmux, and are called before
 * mtp_audio_open(). Nothing above the seam includes this header.
 *
 * SPDX-License-Identifier: 0BSD
 */
#ifndef DESKTOP_AUDIO_H
#define DESKTOP_AUDIO_H

#include "mtp_platform.h"

/* "auto", "alsa", "pulse", "jack", "coreaudio", "sndio", "oss", "null". */
void desktop_audio_set_backend(const char *name);

/* Substring match against the device name; empty means the system default. */
void desktop_audio_set_device(const char *name);

/* Device-level periods behind our ring. 2 is the lowest latency, 3 is safer. */
void desktop_audio_set_periods(unsigned n);

/* Write every block we commit to a WAV file as well as to the device. This is
 * how a machine with no sound card still produces something you can listen to
 * somewhere else. */
mtp_status desktop_audio_set_tap(const char *path);

/* How the device actually asked for its samples: the number of callbacks and
 * the largest single request. A device that asks for 1024 frames at a time
 * cannot be served by a 3 x 128 frame ring, and the number says so. */
void desktop_audio_callback_stats(uint32_t *calls, uint32_t *max_frames,
                                  uint32_t *worst_gap_us);

/* How long the render loop spent asleep waiting for the device, and how often
 * it woke on its own timer rather than on the device's callback. On the T113
 * this is "did the DMA interrupt arrive", and it is the same question. */
void desktop_audio_wait_stats(uint32_t *waits, uint32_t *timeouts,
                              uint32_t *worst_us);

void desktop_audio_list_devices(void);

#endif /* DESKTOP_AUDIO_H */
