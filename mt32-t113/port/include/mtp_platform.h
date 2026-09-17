/* mtp_platform.h - common types for the mt32-t113 platform layer.
 *
 * "mtp" = MT32 Port. Everything the synth core needs from below sits behind
 * these five headers: audio, midi, storage, time, log. Nothing else.
 *
 * Language: C99/C11, not C++. The implementations are interrupt handlers and
 * register pokes, which is the one place we do not want the C++ runtime
 * (exception tables, static guards, operator new) anywhere near. mt32emu is
 * C++ and includes these headers through the extern "C" guards below; see
 * PORTING.md, section "Why the interface is C".
 *
 * SPDX-License-Identifier: 0BSD
 */
#ifndef MTP_PLATFORM_H
#define MTP_PLATFORM_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    MTP_OK         =  0,
    MTP_ERR_IO     = -1,  /* device or medium failed                  */
    MTP_ERR_NOENT  = -2,  /* no such file                             */
    MTP_ERR_NOMEM  = -3,  /* caller's buffer too small, or arena full  */
    MTP_ERR_INVAL  = -4,  /* bad argument                             */
    MTP_ERR_AGAIN  = -5,  /* would block; try later                   */
    MTP_ERR_STATE  = -6   /* not open / already open                  */
} mtp_status;

const char *mtp_strerror(mtp_status s);

#ifdef __cplusplus
}
#endif

#endif /* MTP_PLATFORM_H */
