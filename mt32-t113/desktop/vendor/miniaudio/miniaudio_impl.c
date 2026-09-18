/* miniaudio_impl.c - the one translation unit that compiles miniaudio.
 *
 * miniaudio is vendored (vendor/miniaudio/miniaudio.h, v0.11.25) rather than
 * taken as a system dependency, and its licence is in vendor/miniaudio/LICENSE:
 * a choice of Unlicense (public domain) or MIT-0. Either is compatible with
 * this repository and neither adds an obligation to anyone who builds it.
 *
 * Everything above the device layer is switched off. We need exactly one thing
 * from this library -- a callback that asks for interleaved 16-bit stereo at a
 * fixed rate on ALSA, PulseAudio, JACK, CoreAudio or nothing at all -- and the
 * decoders, the resource manager and the node graph are 80% of its compile time
 * and all of its file-format attack surface.
 *
 * SPDX-License-Identifier: 0BSD  (this file; miniaudio itself is Unlicense/MIT-0)
 */

#define MA_NO_DECODING
#define MA_NO_ENCODING
#define MA_NO_WAV
#define MA_NO_FLAC
#define MA_NO_MP3
#define MA_NO_GENERATION
#define MA_NO_RESOURCE_MANAGER
#define MA_NO_NODE_GRAPH
#define MA_NO_ENGINE

/* Apple's notarisation refuses binaries that dlopen system frameworks, and we
 * link them explicitly in CMakeLists.txt instead. */
#if defined(__APPLE__)
  #define MA_NO_RUNTIME_LINKING
#endif

#define MINIAUDIO_IMPLEMENTATION
#include "miniaudio.h"
