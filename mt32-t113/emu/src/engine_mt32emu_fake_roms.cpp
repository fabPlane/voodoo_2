/* engine_mt32emu_fake_roms.cpp - the real mt32emu, opened on fabricated ROMs.
 *
 * WHY THIS EXISTS. MT-32 ROMs are Roland's and are absent from this repository
 * by design, so port/host/engine_mt32emu.cpp -- which loads them through
 * mtp_storage and lets ROMImage::makeROMImage() identify them by size and
 * SHA-1 -- can only ever be exercised as far as "no such file". That proves
 * the link and the error path, which is worth something, but it leaves the
 * interesting half untested: the C++ runtime, operator new against the bump
 * arena, the static constructors, Tables::getInstance(), the ~949 KiB of
 * allocations Synth::open() makes, and the LA32 render path itself.
 *
 * mt32emu's own test suite has the answer. src/test/FakeROMs.cpp fabricates
 * ROM images the library accepts, by constructing ArrayFile with the *expected*
 * SHA-1 digest rather than the real one (File.h:60, ArrayFile's three-argument
 * constructor) and filling in only the few control-ROM fields the synth
 * actually reads. workstream D used exactly this fixture for the allocation
 * measurements in port/PORTING.md section 3.
 *
 * This file is that fixture, reduced to what we need and rewritten against the
 * public API plus one test accessor:
 *
 *   MT32Emu::ROMInfo::getAllROMInfos()        public   (ROMInfo.h:75)
 *   MT32Emu::ArrayFile(data, size, sha1)      public   (File.h:60)
 *   MT32Emu::Test::getControlROMMap(name)     needs -DMT32EMU_WITH_TESTING
 *                                             (Synth.cpp:2912-2922)
 *
 * It could not go through mtp_storage instead, because the whole trick is to
 * bypass the SHA-1 check: a file-shaped path would be hashed for real and
 * refused. That is the right behaviour and we are not changing it.
 *
 * THE AUDIO IS NOT MT-32 AUDIO. The PCM ROM is all zeroes and the control ROM
 * is mostly zeroes; what comes out is the emulator running correctly on
 * meaningless data. This engine exists to prove the machinery runs, not to
 * make a sound. Real ROMs go through the unmodified engine_mt32emu.cpp.
 *
 * Registered as engine "mt32emu-fakerom". SPDX-License-Identifier: 0BSD
 */

#include "mtp_engine.h"
#include "mtp_log.h"

#include <mt32emu/mt32emu.h>
#include <mt32emu/Structures.h>

#include <stdlib.h>
#include <string.h>
#include <stdio.h>

namespace MT32Emu {
namespace Test {
    const ControlROMMap *getControlROMMap(const char *shortName);
}
}

namespace {

/* Same strings FakeROMs.cpp uses, because Synth copies them out of the control
 * ROM and shows them on the (emulated) LCD. */
const char STARTUP_DISPLAY_MESSAGE[] = "Starting up...      ";
const char ERROR_DISPLAY_MESSAGE[]   = "SysEx error!        ";

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
    /* false: do not retry inside the library on the render thread. The render
     * loop applies back-pressure instead. port/DESIGN.md section 3.4. */
    bool onMIDIQueueOverflow() { return false; }
};

const MT32Emu::ROMInfo *find_rom(const char *short_name)
{
    for (const MT32Emu::ROMInfo * const *ri = MT32Emu::ROMInfo::getAllROMInfos();
         *ri != NULL; ri++)
        if (strcmp(short_name, (*ri)->shortName) == 0) return *ri;
    return NULL;
}

MT32Emu::Bit8u *make_control_rom(const MT32Emu::ROMInfo *info)
{
    MT32Emu::Bit8u *data = new MT32Emu::Bit8u[info->fileSize];
    const MT32Emu::ControlROMMap *map;

    memset(data, 0, info->fileSize);
    map = MT32Emu::Test::getControlROMMap(info->shortName);
    if (map == NULL) { delete[] data; return NULL; }

    /* The max tables, so that any value in a sysex is accepted. */
    memset(data + map->patchMaxTable,  0x7f, 16);
    memset(data + map->rhythmMaxTable, 0x7f, 4);
    memset(data + map->systemMaxTable, 0x7f, 23);
    memset(data + map->timbreMaxTable, 0x7f, 72);

    memcpy(data + map->startupMessage,    STARTUP_DISPLAY_MESSAGE, 21);
    memcpy(data + map->sysexErrorMessage, ERROR_DISPLAY_MESSAGE,   21);

    {
        MT32Emu::Bit32u groups = 4u;
        MT32Emu::SoundGroup *tbl =
            reinterpret_cast<MT32Emu::SoundGroup *>(data + map->soundGroupsTable);
        data[map->soundGroupsCount] = MT32Emu::Bit8u(groups);
        for (MT32Emu::Bit32u i = 0; i < groups; i++) {
            memcpy(tbl[i].name, "Group 1|", 8);
            tbl[i].name[6] = MT32Emu::Bit8u(tbl[i].name[6] + i);
        }
        for (int i = -128; i < 0; i++)
            data[map->soundGroupsTable + i] = MT32Emu::Bit8u(i & 1);
    }
    return data;
}

} // namespace

struct mtp_engine {
    MT32Emu::Synth      *synth;
    PortReportHandler   *handler;
    const MT32Emu::ROMImage *ctrl_img;
    const MT32Emu::ROMImage *pcm_img;
};

static mtp_status fr_open(const mtp_engine_config *cfg, mtp_engine **out)
{
    mtp_engine *e = (mtp_engine *)calloc(1, sizeof(mtp_engine));
    const MT32Emu::ROMInfo *ctrl_info, *pcm_info;
    MT32Emu::Bit8u *ctrl_data, *pcm_data;

    if (!e) return MTP_ERR_NOMEM;

    ctrl_info = find_rom("ctrl_mt32_1_07");
    if (!ctrl_info) ctrl_info = find_rom("ctrl_mt32_1_06");
    if (!ctrl_info) ctrl_info = find_rom("ctrl_mt32_1_05");
    pcm_info  = find_rom("pcm_mt32");
    if (!ctrl_info || !pcm_info) {
        MTP_LOGE("mt32emu: this build knows no MT-32 ROM descriptors");
        free(e);
        return MTP_ERR_NOENT;
    }
    MTP_LOGI("fake ROMs: '%s' (%u B) + '%s' (%u B)",
             ctrl_info->shortName, (unsigned)ctrl_info->fileSize,
             pcm_info->shortName, (unsigned)pcm_info->fileSize);

    ctrl_data = make_control_rom(ctrl_info);
    if (!ctrl_data) {
        MTP_LOGE("mt32emu: no control ROM map for '%s' -- was the library built "
                 "without -DMT32EMU_WITH_TESTING?", ctrl_info->shortName);
        free(e);
        return MTP_ERR_INVAL;
    }
    pcm_data = new MT32Emu::Bit8u[pcm_info->fileSize];
    memset(pcm_data, 0, pcm_info->fileSize);

    /* The three-argument ArrayFile: hand the library the digest it expects
     * instead of letting it hash our fabrication. */
    e->ctrl_img = MT32Emu::ROMImage::makeROMImage(
        new MT32Emu::ArrayFile(ctrl_data, ctrl_info->fileSize, ctrl_info->sha1Digest));
    e->pcm_img = MT32Emu::ROMImage::makeROMImage(
        new MT32Emu::ArrayFile(pcm_data, pcm_info->fileSize, pcm_info->sha1Digest));
    if (!e->ctrl_img || !e->ctrl_img->getROMInfo() ||
        !e->pcm_img  || !e->pcm_img->getROMInfo()) {
        MTP_LOGE("mt32emu: the library refused the fabricated ROM images");
        free(e);
        return MTP_ERR_IO;
    }
    MTP_LOGI("ROMs: %s + %s",
             e->ctrl_img->getROMInfo()->description,
             e->pcm_img->getROMInfo()->description);

    e->handler = new PortReportHandler;
    e->synth   = new MT32Emu::Synth(e->handler);

    /* Every one of these is a decision from port/PORTING.md, applied exactly as
     * port/host/engine_mt32emu.cpp applies it. */
    e->synth->selectRendererType(MT32Emu::RendererType_BIT16S);
    e->synth->preallocateReverbMemory(true);
    e->synth->configureMIDIEventQueueSysexStorage(64u * 1024u);
    e->synth->setMIDIDelayMode(MT32Emu::MIDIDelayMode_IMMEDIATE);

    if (!e->synth->open(*e->ctrl_img, *e->pcm_img,
                        cfg->max_partials ? cfg->max_partials : 32u,
                        MT32Emu::AnalogOutputMode_ACCURATE)) {
        MTP_LOGE("mt32emu: Synth::open failed");
        free(e);
        return MTP_ERR_IO;
    }
    e->synth->setReverbEnabled(cfg->reverb_enabled != 0);

    MTP_LOGI("engine: mt32emu %s, %u partials, %u Hz out (fabricated ROMs: "
             "the machinery is real, the sound is not)",
             MT32Emu::Synth::getLibraryVersionString(),
             e->synth->getPartialCount(),
             e->synth->getStereoOutputSampleRate());
    if (e->synth->getStereoOutputSampleRate() != cfg->output_rate)
        MTP_LOGW("engine outputs %u Hz, caller asked for %u Hz",
                 e->synth->getStereoOutputSampleRate(), cfg->output_rate);

    *out = e;
    return MTP_OK;
}

static void fr_close(mtp_engine *e)
{
    if (!e) return;
    delete e->synth;
    delete e->handler;
    free(e);
}

static uint32_t fr_timebase(mtp_engine *) { return MT32Emu::SAMPLE_RATE; }
static uint32_t fr_rendered(mtp_engine *e)
{ return e->synth->getInternalRenderedSampleCount(); }
static mtp_status fr_short(mtp_engine *e, uint32_t msg, uint32_t ts)
{ return e->synth->playMsg(msg, ts) ? MTP_OK : MTP_ERR_AGAIN; }
static mtp_status fr_sysex(mtp_engine *e, const uint8_t *d, uint32_t len, uint32_t ts)
{ return e->synth->playSysex(d, len, ts) ? MTP_OK : MTP_ERR_AGAIN; }
static void fr_render(mtp_engine *e, int16_t *stereo, uint32_t frames)
{ e->synth->render(stereo, frames); }
static void fr_display(mtp_engine *e, char *dst21)
{ e->synth->getDisplayState(dst21, false); }

extern "C" const mtp_engine_vtable mtp_engine_mt32emu_fakerom = {
    "mt32emu-fakerom",
    fr_open, fr_close, fr_timebase, fr_rendered,
    fr_short, fr_sysex, fr_render, fr_display
};
