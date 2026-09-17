/* host_audio_wav.c - the audio sink, host stub: a WAV file.
 *
 * It models the target's ring honestly rather than just writing samples:
 * acquire() returns NULL when block_count blocks are outstanding, commit()
 * "plays" the oldest, and a virtual play cursor advances in real time only if
 * MTP_HOST_REALTIME is set. Without it the ring drains instantly, so a run is
 * as fast as the machine allows -- which is what you want for a regression
 * test and useless for a latency test. Say which one you are doing.
 *
 * SPDX-License-Identifier: 0BSD */

#include "mtp_audio.h"
#include "mtp_time.h"
#include "mtp_log.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_BLOCKS 8

static FILE            *g_fp;
static mtp_audio_config g_cfg;
static int16_t         *g_blocks[MAX_BLOCKS];
static unsigned         g_write_ix;      /* next block to hand out      */
static unsigned         g_queued;        /* committed, not yet "played" */
static int16_t         *g_acquired;
static uint32_t         g_underruns;
static uint64_t         g_frames_written;
static int              g_realtime;
static uint64_t         g_t_start_us;

static void wav_header(FILE *fp, uint32_t rate, uint16_t ch, uint32_t frames)
{
    uint32_t data_bytes = frames * ch * 2u;
    uint32_t riff = 36u + data_bytes;
    uint8_t h[44];
    memcpy(h, "RIFF", 4);
    h[4]=(uint8_t)riff; h[5]=(uint8_t)(riff>>8); h[6]=(uint8_t)(riff>>16); h[7]=(uint8_t)(riff>>24);
    memcpy(h+8, "WAVEfmt ", 8);
    h[16]=16; h[17]=0; h[18]=0; h[19]=0;      /* fmt chunk size */
    h[20]=1;  h[21]=0;                         /* PCM */
    h[22]=(uint8_t)ch; h[23]=(uint8_t)(ch>>8);
    h[24]=(uint8_t)rate; h[25]=(uint8_t)(rate>>8); h[26]=(uint8_t)(rate>>16); h[27]=(uint8_t)(rate>>24);
    {
        uint32_t byte_rate = rate * ch * 2u;
        uint16_t align = (uint16_t)(ch * 2u);
        h[28]=(uint8_t)byte_rate; h[29]=(uint8_t)(byte_rate>>8);
        h[30]=(uint8_t)(byte_rate>>16); h[31]=(uint8_t)(byte_rate>>24);
        h[32]=(uint8_t)align; h[33]=(uint8_t)(align>>8);
    }
    h[34]=16; h[35]=0;                         /* bits per sample */
    memcpy(h+36, "data", 4);
    h[40]=(uint8_t)data_bytes; h[41]=(uint8_t)(data_bytes>>8);
    h[42]=(uint8_t)(data_bytes>>16); h[43]=(uint8_t)(data_bytes>>24);
    fwrite(h, 1, 44, fp);
}

/* Set by main() before open(). */
static const char *g_path = "out.wav";
void host_audio_set_path(const char *p) { g_path = p; }
void host_audio_set_realtime(int on)    { g_realtime = on; }

mtp_status mtp_audio_open(const mtp_audio_config *cfg)
{
    unsigned i;
    if (!cfg || cfg->channels != 2 || cfg->block_count == 0
        || cfg->block_count > MAX_BLOCKS || cfg->frames_per_block == 0)
        return MTP_ERR_INVAL;
    if (g_fp) return MTP_ERR_STATE;

    g_cfg = *cfg;
    g_fp = fopen(g_path, "wb");
    if (!g_fp) { MTP_LOGE("cannot write %s", g_path); return MTP_ERR_IO; }
    wav_header(g_fp, g_cfg.sample_rate, g_cfg.channels, 0);

    for (i = 0; i < g_cfg.block_count; i++) {
        g_blocks[i] = (int16_t *)calloc(g_cfg.frames_per_block * 2u, sizeof(int16_t));
        if (!g_blocks[i]) return MTP_ERR_NOMEM;
    }
    g_write_ix = 0; g_queued = 0; g_acquired = NULL;
    g_underruns = 0; g_frames_written = 0;
    g_t_start_us = mtp_time_us64();
    MTP_LOGI("audio: %s, %u Hz, %u frames x %u blocks%s",
             g_path, g_cfg.sample_rate, g_cfg.frames_per_block,
             g_cfg.block_count, g_realtime ? ", real time" : "");
    return MTP_OK;
}

void mtp_audio_close(void)
{
    unsigned i;
    if (!g_fp) return;
    fseek(g_fp, 0, SEEK_SET);
    wav_header(g_fp, g_cfg.sample_rate, g_cfg.channels, (uint32_t)g_frames_written);
    fclose(g_fp);
    g_fp = NULL;
    for (i = 0; i < g_cfg.block_count; i++) { free(g_blocks[i]); g_blocks[i] = NULL; }
}

const mtp_audio_config *mtp_audio_get_config(void) { return &g_cfg; }

/* Advance the virtual play cursor. In non-realtime mode everything committed
 * is immediately considered played. */
static void advance_play_cursor(void)
{
    if (!g_realtime) { g_queued = 0; return; }
    {
        uint64_t elapsed = mtp_time_us64() - g_t_start_us;
        uint64_t due = elapsed * g_cfg.sample_rate / 1000000ull;
        uint64_t played_blocks = due / g_cfg.frames_per_block;
        uint64_t committed = g_frames_written / g_cfg.frames_per_block;
        if (played_blocks >= committed) g_queued = 0;
        else g_queued = (unsigned)(committed - played_blocks);
        if (g_queued > g_cfg.block_count) g_queued = g_cfg.block_count;
    }
}

int16_t *mtp_audio_acquire(void)
{
    if (!g_fp || g_acquired) return NULL;
    advance_play_cursor();
    if (g_queued >= g_cfg.block_count) return NULL;
    g_acquired = g_blocks[g_write_ix];
    return g_acquired;
}

void mtp_audio_commit(void)
{
    if (!g_acquired) return;
    fwrite(g_acquired, sizeof(int16_t), g_cfg.frames_per_block * 2u, g_fp);
    g_frames_written += g_cfg.frames_per_block;
    g_write_ix = (g_write_ix + 1u) % g_cfg.block_count;
    g_queued++;
    g_acquired = NULL;
}

unsigned mtp_audio_queued(void) { advance_play_cursor(); return g_queued; }

mtp_status mtp_audio_wait(uint32_t timeout_us)
{
    uint32_t waited = 0;
    for (;;) {
        advance_play_cursor();
        if (g_queued < g_cfg.block_count) return MTP_OK;
        if (waited >= timeout_us) return MTP_ERR_AGAIN;
        mtp_time_delay_us(200);
        waited += 200;
    }
}

uint32_t mtp_audio_underruns(void) { return g_underruns; }
