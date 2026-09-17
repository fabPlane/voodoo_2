/* engine_mt32emu.cpp - the real engine behind mtp_engine.
 *
 * This file is the *entire* C++ surface of the port. Everything it does is
 * justified in PORTING.md; the short version:
 *
 *   - ROMs are loaded through mtp_storage into buffers we own and handed to
 *     mt32emu as ArrayFile (mt32emu/File.h:57). We never use FileStream,
 *     which is the only thing in the library that includes <fstream>
 *     (mt32emu/FileStream.h:94) and would drag the whole iostream/locale
 *     machinery into a bare-metal link.
 *   - AnalogOutputMode_ACCURATE, because its output rate is exactly 48000 Hz
 *     (Synth.cpp:294-298) so no resampler is needed at all.
 *   - RendererType_BIT16S, documented as "Maximum emulation accuracy and
 *     speed" (Enumerations.h:157), and matching render(Bit16s*) so that no
 *     format-conversion stack buffer is used (Synth.cpp:2364).
 *   - preallocateReverbMemory(true) and configureMIDIEventQueueSysexStorage(),
 *     which together make the render path allocation-free. Measured, not
 *     assumed: see PORTING.md, "Does it allocate while rendering?".
 *   - A ReportHandler that routes printDebug into mtp_log instead of stdout.
 *
 * SPDX-License-Identifier: 0BSD */

#include "mtp_engine.h"
#include "mtp_storage.h"
#include "mtp_log.h"

#include <mt32emu/mt32emu.h>

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

namespace {

class PortReportHandler : public MT32Emu::ReportHandler {
public:
    void printDebug(const char *fmt, va_list list) {
        char buf[256];
        vsnprintf(buf, sizeof(buf), fmt, list);
        mtp_log(MTP_LOG_DEBUG, "mt32emu: %s", buf);
    }
    void showLCDMessage(const char *msg) { mtp_log(MTP_LOG_INFO, "LCD: %s", msg); }
    void onErrorControlROM() { MTP_LOGE("mt32emu: bad control ROM"); }
    void onErrorPCMROM()     { MTP_LOGE("mt32emu: bad PCM ROM"); }
    bool onMIDIQueueOverflow() {
        /* false = do not retry; playMsg/playSysex return false and our render
         * loop applies back-pressure instead. Returning true here would spin
         * inside the library, on the render thread, for ever. */
        MTP_LOGW("mt32emu: MIDI queue overflow");
        return false;
    }
};

} // namespace

struct mtp_engine {
    MT32Emu::Synth      *synth;
    PortReportHandler   *handler;
    MT32Emu::ArrayFile  *ctrl_file;
    MT32Emu::ArrayFile  *pcm_file;
    const MT32Emu::ROMImage *ctrl_img;
    const MT32Emu::ROMImage *pcm_img;
    uint8_t             *ctrl_data;
    uint8_t             *pcm_data;
};

/* Largest images mt32emu knows: 128 kB control (MT-32 v2.x), 1 MB PCM
 * (CM-32L). ROMInfo.cpp:103-117. */
static const long CTRL_CAP = 128L * 1024L;
static const long PCM_CAP  = 1024L * 1024L;

static mtp_status load_rom(const char *path, uint8_t **out, long *out_len,
                           long cap, const char *what)
{
    long len = 0;
    uint8_t *buf;
    mtp_status s;
    if (!path) { MTP_LOGE("no %s ROM path given", what); return MTP_ERR_INVAL; }
    buf = (uint8_t *)malloc((size_t)cap);
    if (!buf) return MTP_ERR_NOMEM;
    s = mtp_storage_load(path, buf, cap, &len);
    if (s != MTP_OK) {
        free(buf);
        MTP_LOGE("%s ROM '%s': %s", what, path, mtp_strerror(s));
        return s;
    }
    MTP_LOGI("%s ROM '%s': %ld bytes", what, path, len);
    *out = buf; *out_len = len;
    return MTP_OK;
}

static mtp_status me_open(const mtp_engine_config *cfg, mtp_engine **out)
{
    mtp_engine *e = (mtp_engine *)calloc(1, sizeof(mtp_engine));
    long ctrl_len = 0, pcm_len = 0;
    if (!e) return MTP_ERR_NOMEM;

    if (load_rom(cfg->control_rom_path, &e->ctrl_data, &ctrl_len, CTRL_CAP,
                 "control") != MTP_OK) goto fail;
    if (load_rom(cfg->pcm_rom_path, &e->pcm_data, &pcm_len, PCM_CAP,
                 "PCM") != MTP_OK) goto fail;

    e->ctrl_file = new MT32Emu::ArrayFile(e->ctrl_data, (size_t)ctrl_len);
    e->pcm_file  = new MT32Emu::ArrayFile(e->pcm_data,  (size_t)pcm_len);

    /* makeROMImage identifies the image by size + SHA1 (ROMInfo.h:52-54). A
     * NULL result means "this is not a ROM mt32emu recognises", which is a far
     * more useful error than a synth that opens and plays nothing. */
    e->ctrl_img = MT32Emu::ROMImage::makeROMImage(e->ctrl_file);
    e->pcm_img  = MT32Emu::ROMImage::makeROMImage(e->pcm_file);
    if (!e->ctrl_img || !e->ctrl_img->getROMInfo()) {
        MTP_LOGE("control ROM not recognised (wrong file, or a half image)");
        goto fail;
    }
    if (!e->pcm_img || !e->pcm_img->getROMInfo()) {
        MTP_LOGE("PCM ROM not recognised (wrong file, or a half image)");
        goto fail;
    }
    MTP_LOGI("ROMs: %s + %s",
             e->ctrl_img->getROMInfo()->description,
             e->pcm_img->getROMInfo()->description);

    e->handler = new PortReportHandler;
    e->synth   = new MT32Emu::Synth(e->handler);

    e->synth->selectRendererType(MT32Emu::RendererType_BIT16S);
    e->synth->preallocateReverbMemory(true);
    e->synth->configureMIDIEventQueueSysexStorage(64u * 1024u);
    e->synth->setMIDIDelayMode(MT32Emu::MIDIDelayMode_IMMEDIATE);

    if (!e->synth->open(*e->ctrl_img, *e->pcm_img,
                        cfg->max_partials ? cfg->max_partials : 32u,
                        MT32Emu::AnalogOutputMode_ACCURATE)) {
        MTP_LOGE("Synth::open failed");
        goto fail;
    }
    e->synth->setReverbEnabled(cfg->reverb_enabled != 0);

    if (e->synth->getStereoOutputSampleRate() != cfg->output_rate) {
        MTP_LOGW("engine outputs %u Hz, caller asked for %u Hz",
                 e->synth->getStereoOutputSampleRate(), cfg->output_rate);
    }
    MTP_LOGI("engine: mt32emu %s, %u partials, %u Hz out",
             MT32Emu::Synth::getLibraryVersionString(),
             e->synth->getPartialCount(),
             e->synth->getStereoOutputSampleRate());

    *out = e;
    return MTP_OK;

fail:
    if (e->synth) delete e->synth;
    if (e->handler) delete e->handler;
    if (e->ctrl_img) MT32Emu::ROMImage::freeROMImage(e->ctrl_img);
    if (e->pcm_img)  MT32Emu::ROMImage::freeROMImage(e->pcm_img);
    delete e->ctrl_file; delete e->pcm_file;
    free(e->ctrl_data); free(e->pcm_data);
    free(e);
    return MTP_ERR_IO;
}

static void me_close(mtp_engine *e)
{
    if (!e) return;
    delete e->synth;
    if (e->ctrl_img) MT32Emu::ROMImage::freeROMImage(e->ctrl_img);
    if (e->pcm_img)  MT32Emu::ROMImage::freeROMImage(e->pcm_img);
    delete e->ctrl_file; delete e->pcm_file;
    delete e->handler;
    free(e->ctrl_data); free(e->pcm_data);
    free(e);
}

static uint32_t me_timebase(mtp_engine *)  { return MT32Emu::SAMPLE_RATE; }

static uint32_t me_rendered(mtp_engine *e)
{
    return e->synth->getInternalRenderedSampleCount();
}

static mtp_status me_short(mtp_engine *e, uint32_t msg, uint32_t ts)
{
    return e->synth->playMsg(msg, ts) ? MTP_OK : MTP_ERR_AGAIN;
}

static mtp_status me_sysex(mtp_engine *e, const uint8_t *d, uint32_t len,
                           uint32_t ts)
{
    return e->synth->playSysex(d, len, ts) ? MTP_OK : MTP_ERR_AGAIN;
}

static void me_render(mtp_engine *e, int16_t *stereo, uint32_t frames)
{
    e->synth->render(stereo, frames);
}

static void me_display(mtp_engine *e, char *dst21)
{
    e->synth->getDisplayState(dst21, false);
}

extern "C" const mtp_engine_vtable mtp_engine_mt32emu = {
    "mt32emu",
    me_open,
    me_close,
    me_timebase,
    me_rendered,
    me_short,
    me_sysex,
    me_render,
    me_display
};
