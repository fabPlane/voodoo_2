/*
 * rtf.cpp -- headless real-time-factor harness for Munt's mt32emu.
 *
 * Answers the mt32-t113 gate: does mt32emu render MT-32 audio faster than
 * real time on one Cortex-A7 at 1.2 GHz?
 *
 * Renders a Standard MIDI File through mt32emu into a discarded buffer, in
 * fixed-size blocks, timing each block with a monotonic clock, and reports:
 *   - overall real-time factor (CPU seconds per second of audio)
 *   - worst-case per-block RTF (the number that decides whether a DMA ring
 *     underruns, which is what actually matters)
 *   - peak simultaneously-active partial count
 *   - wall-clock milliseconds per second of audio
 *
 * ROMs are Roland's and are never bundled. Without them this program exits
 * non-zero with a message; it does not invent a number.
 *
 * This file: no copyright asserted, do as you like. It links against
 * mt32emu, which is LGPL 2.1 -- see vendor/munt/mt32emu/COPYING.LESSER.txt.
 */

#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <string>
#include <vector>
#include <algorithm>

#include <dirent.h>
#include <sys/stat.h>

#include <mt32emu/mt32emu.h>

using namespace MT32Emu;

/* ------------------------------------------------------------------ clock */

/* CLOCK_MONOTONIC: never steps, never slews backwards. On Linux/glibc this is
 * a vDSO read. Do not use gettimeofday or CLOCK_REALTIME here. */
static double monotonicSeconds() {
	struct timespec ts;
#if defined(CLOCK_MONOTONIC_RAW)
	/* _RAW is not NTP-slewed. On a board under test that matters. */
	if (clock_gettime(CLOCK_MONOTONIC_RAW, &ts) == 0) {
		return double(ts.tv_sec) + 1e-9 * double(ts.tv_nsec);
	}
#endif
	clock_gettime(CLOCK_MONOTONIC, &ts);
	return double(ts.tv_sec) + 1e-9 * double(ts.tv_nsec);
}

/* ------------------------------------------------------- tiny SMF parser */

/* Deliberately minimal: note on/off, poly and channel aftertouch, control
 * change, program change, pitch bend, sysex passthrough, and the set-tempo
 * meta event. Everything else in the file is skipped, which is correct for a
 * benchmark: we want the note load, not the text markers. */

struct MidiEvent {
	unsigned long long tick;
	unsigned long seq;             /* stable ordering within a tick */
	unsigned long samplePos;       /* filled in later, synth samples @32k */
	Bit32u shortMsg;               /* packed status|d1<<8|d2<<16, 0 if sysex */
	std::vector<Bit8u> sysex;      /* full F0..F7 message, empty if short */
	unsigned long tempoUsPerQuarter; /* nonzero => tempo change, not a MIDI msg */
};

static bool eventLess(const MidiEvent &a, const MidiEvent &b) {
	if (a.tick != b.tick) return a.tick < b.tick;
	return a.seq < b.seq;
}

class SmfParser {
public:
	std::vector<MidiEvent> events;
	int division;          /* raw MThd division word */
	int format;
	int trackCount;
	std::string error;

	SmfParser() : division(0), format(0), trackCount(0) {}

	bool parse(const char *path) {
		FILE *f = fopen(path, "rb");
		if (f == NULL) {
			error = std::string("cannot open MIDI file: ") + strerror(errno);
			return false;
		}
		fseek(f, 0, SEEK_END);
		long len = ftell(f);
		fseek(f, 0, SEEK_SET);
		if (len <= 14) { error = "MIDI file too short"; fclose(f); return false; }
		buf.resize(size_t(len));
		size_t got = fread(&buf[0], 1, size_t(len), f);
		fclose(f);
		if (got != size_t(len)) { error = "short read on MIDI file"; return false; }

		p = 0;
		if (memcmp(&buf[0], "MThd", 4) != 0) { error = "not a Standard MIDI File (no MThd)"; return false; }
		p = 4;
		unsigned long hdrLen = read32();
		if (hdrLen < 6) { error = "bad MThd length"; return false; }
		size_t hdrEnd = p + hdrLen;
		format = int(read16());
		trackCount = int(read16());
		division = int(Bit16s(read16()));
		p = hdrEnd;

		unsigned long seq = 0;
		int tracksSeen = 0;
		while (p + 8 <= buf.size()) {
			char id[5] = {0};
			memcpy(id, &buf[p], 4);
			p += 4;
			unsigned long chunkLen = read32();
			size_t chunkEnd = p + chunkLen;
			if (chunkEnd > buf.size()) chunkEnd = buf.size();
			if (memcmp(id, "MTrk", 4) == 0) {
				if (!parseTrack(chunkEnd, seq)) return false;
				tracksSeen++;
			}
			p = chunkEnd;
		}
		if (tracksSeen == 0) { error = "no MTrk chunks found"; return false; }
		std::stable_sort(events.begin(), events.end(), eventLess);
		return true;
	}

	/* Convert ticks to synth sample positions (32000 Hz), applying the tempo
	 * map. Returns total length in synth samples. */
	unsigned long resolveTiming(unsigned int synthRate) {
		double samplesPerTick;
		bool smpte = (division < 0);
		double fixedSamplesPerTick = 0.0;
		if (smpte) {
			/* Negative: high byte is -fps, low byte is ticks per frame. */
			int fps = -((division >> 8) & 0xFF);
			int subframes = division & 0xFF;
			if (fps <= 0 || subframes <= 0) { fps = 25; subframes = 40; }
			fixedSamplesPerTick = double(synthRate) / (double(fps) * double(subframes));
		}
		unsigned long tempo = 500000; /* MIDI default: 120 bpm */
		unsigned long long lastTick = 0;
		double accumSamples = 0.0;
		unsigned long last = 0;
		for (size_t i = 0; i < events.size(); i++) {
			MidiEvent &e = events[i];
			if (smpte) {
				samplesPerTick = fixedSamplesPerTick;
			} else {
				int ppqn = division ? division : 96;
				samplesPerTick = (double(tempo) * 1e-6 * double(synthRate)) / double(ppqn);
			}
			accumSamples += double(e.tick - lastTick) * samplesPerTick;
			lastTick = e.tick;
			e.samplePos = (unsigned long)(accumSamples + 0.5);
			last = e.samplePos;
			if (e.tempoUsPerQuarter != 0) tempo = e.tempoUsPerQuarter;
		}
		return last;
	}

private:
	std::vector<Bit8u> buf;
	size_t p;

	Bit8u  read8()  { return p < buf.size() ? buf[p++] : Bit8u(0); }
	unsigned long read16() { unsigned long v = read8(); v = (v << 8) | read8(); return v; }
	unsigned long read32() { unsigned long v = read16(); v = (v << 16) | read16(); return v; }

	unsigned long readVarLen(size_t end) {
		unsigned long v = 0;
		for (int i = 0; i < 4 && p < end; i++) {
			Bit8u b = read8();
			v = (v << 7) | (b & 0x7F);
			if ((b & 0x80) == 0) break;
		}
		return v;
	}

	bool parseTrack(size_t end, unsigned long &seq) {
		unsigned long long tick = 0;
		Bit8u runningStatus = 0;
		while (p < end) {
			tick += readVarLen(end);
			if (p >= end) break;
			Bit8u status = buf[p];
			if (status & 0x80) {
				p++;
				if (status < 0xF0) runningStatus = status;
			} else {
				if (runningStatus == 0) { error = "running status with no prior status byte"; return false; }
				status = runningStatus;
			}

			if (status == 0xFF) {
				/* Meta event */
				Bit8u type = read8();
				unsigned long len = readVarLen(end);
				if (type == 0x51 && len == 3 && p + 3 <= end) {
					unsigned long t = (unsigned long)buf[p] << 16;
					t |= (unsigned long)buf[p + 1] << 8;
					t |= (unsigned long)buf[p + 2];
					MidiEvent e;
					e.tick = tick; e.seq = seq++; e.samplePos = 0;
					e.shortMsg = 0; e.tempoUsPerQuarter = t ? t : 1;
					events.push_back(e);
				}
				p += len;
				if (type == 0x2F) break; /* end of track */
			} else if (status == 0xF0 || status == 0xF7) {
				unsigned long len = readVarLen(end);
				if (p + len > end) len = end > p ? (unsigned long)(end - p) : 0;
				MidiEvent e;
				e.tick = tick; e.seq = seq++; e.samplePos = 0;
				e.shortMsg = 0; e.tempoUsPerQuarter = 0;
				if (status == 0xF0) {
					/* SMF stores F0 without the leading F0; mt32emu wants a
					 * well-formed message, so put it back. */
					e.sysex.push_back(0xF0);
					e.sysex.insert(e.sysex.end(), buf.begin() + p, buf.begin() + p + len);
				} else {
					/* F7 escape: raw bytes, already framed by whoever wrote it.
					 * Pass through only if it looks like a complete message. */
					e.sysex.insert(e.sysex.end(), buf.begin() + p, buf.begin() + p + len);
					if (e.sysex.empty() || e.sysex[0] != 0xF0) e.sysex.clear();
				}
				p += len;
				if (!e.sysex.empty()) events.push_back(e);
			} else if (status >= 0x80 && status <= 0xEF) {
				Bit8u hi = status & 0xF0;
				int nData = (hi == 0xC0 || hi == 0xD0) ? 1 : 2;
				Bit8u d1 = read8();
				Bit8u d2 = (nData == 2) ? read8() : 0;
				MidiEvent e;
				e.tick = tick; e.seq = seq++; e.samplePos = 0;
				e.tempoUsPerQuarter = 0;
				e.shortMsg = Bit32u(status) | (Bit32u(d1 & 0x7F) << 8) | (Bit32u(d2 & 0x7F) << 16);
				events.push_back(e);
			} else {
				/* F1..F6, F8..FE -- system common/realtime. Not meaningful in a
				 * file; skip conservatively. */
				if (status == 0xF2) { read8(); read8(); }
				else if (status == 0xF1 || status == 0xF3) { read8(); }
			}
		}
		return true;
	}
};

/* ---------------------------------------------------------- ROM discovery */

struct LoadedRom {
	std::string path;
	FileStream *file;
	const ROMImage *image;
	LoadedRom() : file(NULL), image(NULL) {}
};

static void listDir(const char *dir, std::vector<std::string> &out, std::string &err) {
	DIR *d = opendir(dir);
	if (d == NULL) {
		err = std::string("cannot open ROM directory '") + dir + "': " + strerror(errno);
		return;
	}
	struct dirent *ent;
	while ((ent = readdir(d)) != NULL) {
		std::string name = ent->d_name;
		if (name == "." || name == "..") continue;
		std::string full = std::string(dir) + "/" + name;
		struct stat st;
		if (stat(full.c_str(), &st) != 0) continue;
		if (!S_ISREG(st.st_mode)) continue;
		out.push_back(full);
	}
	closedir(d);
	std::sort(out.begin(), out.end());
}

/* ----------------------------------------------------------- report sink */

class QuietReportHandler : public ReportHandler {
public:
	bool verbose;
	bool sawControlRomError;
	bool sawPcmRomError;
	QuietReportHandler() : verbose(false), sawControlRomError(false), sawPcmRomError(false) {}
	void printDebug(const char *fmt, va_list list) {
		if (!verbose) return;
		fprintf(stderr, "[mt32emu] ");
		vfprintf(stderr, fmt, list);
		fprintf(stderr, "\n");
	}
	void onErrorControlROM() { sawControlRomError = true; }
	void onErrorPCMROM() { sawPcmRomError = true; }
	void showLCDMessage(const char *) {}
};

/* ------------------------------------------------------------------ usage */

static void usage(const char *argv0) {
	fprintf(stderr,
"mt32emu real-time-factor harness (mt32-t113 bench)\n"
"\n"
"usage: %s --rom-dir DIR --midi FILE.mid [options]\n"
"\n"
"required:\n"
"  --rom-dir DIR       directory holding MT-32 or CM-32L control and PCM ROM\n"
"                      dumps. ROMs are Roland's; dump your own.\n"
"  --midi FILE         Standard MIDI File to render.\n"
"\n"
"options:\n"
"  --sample-rate N     output sample rate in Hz (default: the synth's native\n"
"                      rate for the chosen analog mode, i.e. 32000 for\n"
"                      digital/coarse). Any other value engages mt32emu's\n"
"                      internal resampler, which is NOT free -- see --src-quality.\n"
"  --duration SEC      stop after SEC seconds of audio (default: whole file,\n"
"                      plus --tail seconds of release).\n"
"  --block N           render block size in frames (default 256).\n"
"  --partials N        max partials (default 32, the hardware number).\n"
"  --analog MODE       digital | coarse | accurate | oversampled (default coarse).\n"
"  --renderer TYPE     int | float (default int -- mt32emu's own default and\n"
"                      the faster one; float is the higher-quality model).\n"
"  --src-quality Q     fastest | fast | good | best (default good). Only used\n"
"                      when --sample-rate differs from the native rate.\n"
"  --reverb on|off     default on. Reverb is a real share of the cost.\n"
"  --tail SEC          extra seconds rendered after the last MIDI event\n"
"                      (default 2.0) so releases are included.\n"
"  --warmup SEC        seconds of audio rendered and timed but discarded from\n"
"                      the statistics (default 0.0). Use on a board to let the\n"
"                      caches and the governor settle.\n"
"  --csv FILE          write per-block timings to FILE for offline analysis.\n"
"  --machine ID        restrict ROM matching to a machine configuration\n"
"                      (see --list-machines). Needed to select CM-32L when\n"
"                      you have the paired half-ROM dumps.\n"
"  --list-machines     print the machine configurations this build knows and exit.\n"
"  --list-roms         print every ROM this build recognises and exit.\n"
"  --verbose           pass mt32emu's debug output through to stderr.\n"
"  --help              this text.\n"
"\n"
"exit status: 0 on a completed measurement, 1 on any error (including missing\n"
"ROMs). A missing ROM is an error, never a default.\n",
		argv0);
}

static void listMachines() {
	Bit32u count = 0;
	const MachineConfiguration * const *all = MachineConfiguration::getAllMachineConfigurations(&count);
	printf("machine configurations known to this mt32emu build (%u):\n", unsigned(count));
	for (Bit32u i = 0; i < count; i++) {
		printf("  %-24s", all[i]->getMachineID());
		Bit32u rc = 0;
		const ROMInfo * const *roms = all[i]->getCompatibleROMInfos(&rc);
		for (Bit32u j = 0; j < rc; j++) {
			printf(" %s", roms[j]->shortName);
		}
		printf("\n");
	}
}

static const char *romTypeName(ROMInfo::Type t) {
	switch (t) {
		case ROMInfo::PCM: return "PCM";
		case ROMInfo::Control: return "Control";
		case ROMInfo::Reverb: return "Reverb";
	}
	return "?";
}

static const char *pairTypeName(ROMInfo::PairType t) {
	switch (t) {
		case ROMInfo::Full: return "full";
		case ROMInfo::FirstHalf: return "first-half";
		case ROMInfo::SecondHalf: return "second-half";
		case ROMInfo::Mux0: return "mux0";
		case ROMInfo::Mux1: return "mux1";
	}
	return "?";
}

static void listRoms() {
	Bit32u count = 0;
	const ROMInfo * const *all = ROMInfo::getAllROMInfos(&count);
	printf("%-22s %-8s %-12s %9s  %s\n", "short name", "type", "pair", "bytes", "description");
	for (Bit32u i = 0; i < count; i++) {
		printf("%-22s %-8s %-12s %9lu  %s\n",
			all[i]->shortName, romTypeName(all[i]->type), pairTypeName(all[i]->pairType),
			(unsigned long)all[i]->fileSize, all[i]->description);
	}
}

/* ------------------------------------------------------------------- main */

int main(int argc, char **argv) {
	const char *romDir = NULL;
	const char *midiPath = NULL;
	const char *csvPath = NULL;
	const char *machineId = NULL;
	double wantRate = 0.0;
	double duration = 0.0;
	double tail = 2.0;
	double warmup = 0.0;
	unsigned int blockFrames = 256;
	unsigned int maxPartials = DEFAULT_MAX_PARTIALS;
	AnalogOutputMode analogMode = AnalogOutputMode_COARSE;
	RendererType rendererType = RendererType_BIT16S;
	SamplerateConversionQuality srcQuality = SamplerateConversionQuality_GOOD;
	bool reverb = true;
	bool verbose = false;

	for (int i = 1; i < argc; i++) {
		std::string a = argv[i];
		const char *next = (i + 1 < argc) ? argv[i + 1] : NULL;
		#define NEED(x) do { if (!next) { fprintf(stderr, "error: %s needs a value\n", x); return 1; } i++; } while (0)
		if (a == "--help" || a == "-h") { usage(argv[0]); return 0; }
		else if (a == "--list-machines") { listMachines(); return 0; }
		else if (a == "--list-roms") { listRoms(); return 0; }
		else if (a == "--rom-dir") { NEED("--rom-dir"); romDir = next; }
		else if (a == "--midi") { NEED("--midi"); midiPath = next; }
		else if (a == "--csv") { NEED("--csv"); csvPath = next; }
		else if (a == "--machine") { NEED("--machine"); machineId = next; }
		else if (a == "--sample-rate") { NEED("--sample-rate"); wantRate = atof(next); }
		else if (a == "--duration") { NEED("--duration"); duration = atof(next); }
		else if (a == "--tail") { NEED("--tail"); tail = atof(next); }
		else if (a == "--warmup") { NEED("--warmup"); warmup = atof(next); }
		else if (a == "--block") { NEED("--block"); blockFrames = (unsigned int)atoi(next); }
		else if (a == "--partials") { NEED("--partials"); maxPartials = (unsigned int)atoi(next); }
		else if (a == "--verbose") { verbose = true; }
		else if (a == "--reverb") {
			NEED("--reverb");
			reverb = (strcmp(next, "on") == 0 || strcmp(next, "1") == 0 || strcmp(next, "true") == 0);
		}
		else if (a == "--analog") {
			NEED("--analog");
			if (!strcmp(next, "digital")) analogMode = AnalogOutputMode_DIGITAL_ONLY;
			else if (!strcmp(next, "coarse")) analogMode = AnalogOutputMode_COARSE;
			else if (!strcmp(next, "accurate")) analogMode = AnalogOutputMode_ACCURATE;
			else if (!strcmp(next, "oversampled")) analogMode = AnalogOutputMode_OVERSAMPLED;
			else { fprintf(stderr, "error: unknown --analog mode '%s'\n", next); return 1; }
		}
		else if (a == "--renderer") {
			NEED("--renderer");
			if (!strcmp(next, "int") || !strcmp(next, "bit16s")) rendererType = RendererType_BIT16S;
			else if (!strcmp(next, "float")) rendererType = RendererType_FLOAT;
			else { fprintf(stderr, "error: unknown --renderer '%s'\n", next); return 1; }
		}
		else if (a == "--src-quality") {
			NEED("--src-quality");
			if (!strcmp(next, "fastest")) srcQuality = SamplerateConversionQuality_FASTEST;
			else if (!strcmp(next, "fast")) srcQuality = SamplerateConversionQuality_FAST;
			else if (!strcmp(next, "good")) srcQuality = SamplerateConversionQuality_GOOD;
			else if (!strcmp(next, "best")) srcQuality = SamplerateConversionQuality_BEST;
			else { fprintf(stderr, "error: unknown --src-quality '%s'\n", next); return 1; }
		}
		else { fprintf(stderr, "error: unknown argument '%s' (try --help)\n", argv[i]); return 1; }
		#undef NEED
	}

	if (romDir == NULL || midiPath == NULL) {
		usage(argv[0]);
		fprintf(stderr, "\nerror: --rom-dir and --midi are both required.\n");
		return 1;
	}
	if (blockFrames == 0 || blockFrames > 65536) {
		fprintf(stderr, "error: --block must be 1..65536\n");
		return 1;
	}
	if (maxPartials == 0 || maxPartials > 256) {
		fprintf(stderr, "error: --partials must be 1..256\n");
		return 1;
	}

	printf("mt32emu %s, harness block=%u frames, partials=%u\n",
		Synth::getLibraryVersionString(), blockFrames, maxPartials);

	/* -------------------------------------------------- pick the ROM filter */

	const ROMInfo * const *romFilter = NULL;
	if (machineId != NULL) {
		Bit32u count = 0;
		const MachineConfiguration * const *all = MachineConfiguration::getAllMachineConfigurations(&count);
		for (Bit32u i = 0; i < count && romFilter == NULL; i++) {
			if (strcmp(all[i]->getMachineID(), machineId) == 0) {
				romFilter = all[i]->getCompatibleROMInfos();
			}
		}
		if (romFilter == NULL) {
			fprintf(stderr, "error: unknown machine id '%s'. Run --list-machines.\n", machineId);
			return 1;
		}
	}

	/* ---------------------------------------------------------- find ROMs */

	std::vector<std::string> files;
	std::string dirErr;
	listDir(romDir, files, dirErr);
	if (!dirErr.empty()) {
		fprintf(stderr, "error: %s\n", dirErr.c_str());
		fprintf(stderr,
			"\nMT-32 and CM-32L ROMs are copyrighted by Roland and are deliberately\n"
			"absent from this repository. Dump them from hardware you own and point\n"
			"--rom-dir at the directory containing them.\n");
		return 1;
	}

	std::vector<LoadedRom> roms;
	const ROMImage *control = NULL;
	const ROMImage *pcm = NULL;
	std::vector<const ROMImage *> partialControl, partialPcm;
	std::vector<const ROMImage *> merged;

	for (size_t i = 0; i < files.size(); i++) {
		LoadedRom r;
		r.path = files[i];
		r.file = new FileStream();
		if (!r.file->open(files[i].c_str())) { delete r.file; continue; }
		r.image = romFilter ? ROMImage::makeROMImage(r.file, romFilter)
		                    : ROMImage::makeROMImage(r.file);
		if (r.image == NULL || r.image->getROMInfo() == NULL) {
			if (r.image) ROMImage::freeROMImage(r.image);
			delete r.file;
			continue;
		}
		const ROMInfo *info = r.image->getROMInfo();
		if (verbose) {
			printf("  candidate %-40s %-8s %-12s %s\n", files[i].c_str(),
				romTypeName(info->type), pairTypeName(info->pairType), info->description);
		}
		roms.push_back(r);
		if (info->pairType == ROMInfo::Full) {
			if (info->type == ROMInfo::Control && control == NULL) control = r.image;
			else if (info->type == ROMInfo::PCM && pcm == NULL) pcm = r.image;
		} else {
			if (info->type == ROMInfo::Control) partialControl.push_back(r.image);
			else if (info->type == ROMInfo::PCM) partialPcm.push_back(r.image);
		}
	}

	/* Pair up half/mux dumps if we still need a full image. */
	for (int pass = 0; pass < 2; pass++) {
		std::vector<const ROMImage *> &pool = pass == 0 ? partialControl : partialPcm;
		const ROMImage *&slot = pass == 0 ? control : pcm;
		for (size_t a = 0; slot == NULL && a < pool.size(); a++) {
			for (size_t b = 0; slot == NULL && b < pool.size(); b++) {
				if (a == b) continue;
				const ROMImage *m = ROMImage::mergeROMImages(pool[a], pool[b]);
				if (m != NULL) { slot = m; merged.push_back(m); }
			}
		}
	}

	if (control == NULL || pcm == NULL) {
		fprintf(stderr,
			"error: could not assemble a usable ROM set from '%s'.\n"
			"       control ROM: %s\n"
			"       PCM ROM:     %s\n"
			"\n"
			"Found %u file(s) in that directory, %u of which mt32emu recognised.\n"
			"\n"
			"MT-32 and CM-32L ROMs are copyrighted by Roland and are deliberately\n"
			"absent from this repository. Dump them from hardware you own.\n"
			"A working MT-32 set is a 64 KiB control ROM (e.g. MT32_CONTROL.ROM,\n"
			"v1.07) plus a 512 KiB PCM ROM (MT32_PCM.ROM). A CM-32L set is a\n"
			"64 KiB control ROM plus a 1 MiB PCM ROM, or the two 512 KiB halves\n"
			"together with --machine cm32l_1_02.\n"
			"Run --list-roms to see every dump this build recognises by SHA-1,\n"
			"and --verbose to see which of your files were matched.\n",
			romDir,
			control ? "ok" : "MISSING",
			pcm ? "ok" : "MISSING",
			unsigned(files.size()), unsigned(roms.size()));
		return 1;
	}

	printf("control ROM: %s\n", control->getROMInfo()->description);
	printf("PCM ROM:     %s\n", pcm->getROMInfo()->description);

	/* ------------------------------------------------------- parse the SMF */

	SmfParser smf;
	if (!smf.parse(midiPath)) {
		fprintf(stderr, "error: %s\n", smf.error.c_str());
		return 1;
	}
	unsigned long lastEventSample = smf.resolveTiming(SAMPLE_RATE);
	printf("MIDI:        %s (format %d, %d tracks, division %d, %u events, last event at %.2f s)\n",
		midiPath, smf.format, smf.trackCount, smf.division,
		unsigned(smf.events.size()), double(lastEventSample) / double(SAMPLE_RATE));

	/* ------------------------------------------------------- open the synth */

	QuietReportHandler handler;
	handler.verbose = verbose;
	Synth synth(&handler);
	synth.selectRendererType(rendererType);
	synth.setReverbEnabled(reverb);
	/* Avoid the reverb model allocating inside the render call. Bare metal will
	 * want this too. */
	synth.preallocateReverbMemory(true);

	if (!synth.open(*control, *pcm, maxPartials, analogMode)) {
		fprintf(stderr, "error: Synth::open() failed%s%s.\n",
			handler.sawControlRomError ? " (control ROM rejected)" : "",
			handler.sawPcmRomError ? " (PCM ROM rejected)" : "");
		return 1;
	}

	const double nativeRate = double(Synth::getStereoOutputSampleRate(analogMode));
	double outRate = wantRate > 0.0 ? wantRate : nativeRate;
	SampleRateConverter *src = NULL;
	if (outRate != nativeRate) {
		double supported = SampleRateConverter::getSupportedOutputSampleRate(outRate);
		if (supported != outRate) {
			printf("note: requested %.0f Hz is not supported exactly; using %.0f Hz\n", outRate, supported);
			outRate = supported;
		}
		src = new SampleRateConverter(synth, outRate, srcQuality);
	}

	printf("analog mode native rate %.0f Hz, output rate %.0f Hz%s, renderer %s, reverb %s\n",
		nativeRate, outRate, src ? " (via internal resampler)" : " (direct)",
		rendererType == RendererType_FLOAT ? "float" : "int16",
		reverb ? "on" : "off");

	/* -------------------------------------------------------- render loop */

	/* Total audio to render, in output frames. */
	double totalSeconds = duration > 0.0
		? duration
		: (double(lastEventSample) / double(SAMPLE_RATE)) + tail;
	if (totalSeconds <= 0.0) { fprintf(stderr, "error: nothing to render\n"); return 1; }
	unsigned long long totalFrames = (unsigned long long)(totalSeconds * outRate);
	unsigned long long warmupFrames = (unsigned long long)(warmup * outRate);

	std::vector<Bit16s> buffer(size_t(blockFrames) * 2);
	std::vector<float> fbuffer;
	std::vector<PartialState> partialStates(maxPartials);

	FILE *csv = NULL;
	if (csvPath) {
		csv = fopen(csvPath, "w");
		if (csv == NULL) {
			fprintf(stderr, "error: cannot write --csv '%s': %s\n", csvPath, strerror(errno));
			return 1;
		}
		fprintf(csv, "block,audio_start_s,render_s,block_rtf,active_partials\n");
	}

	size_t nextEvent = 0;
	unsigned long long framesDone = 0;
	unsigned long long blocksCounted = 0;
	double totalRenderSeconds = 0.0;
	double worstBlockSeconds = 0.0;
	double worstBlockRtf = 0.0;
	unsigned long long worstBlockIndex = 0;
	unsigned int peakPartials = 0;
	unsigned long long blockIndex = 0;
	unsigned long queueDrops = 0;

	/* Ratio between output frames and synth-internal samples, used only to
	 * decide which MIDI events to enqueue before each block. */
	const double synthSamplesPerOutFrame = double(SAMPLE_RATE) / outRate;

	const double runStart = monotonicSeconds();

	while (framesDone < totalFrames) {
		unsigned int thisBlock = blockFrames;
		if (totalFrames - framesDone < thisBlock) thisBlock = (unsigned int)(totalFrames - framesDone);

		/* Enqueue every MIDI event that falls inside this block, timestamped in
		 * synth samples. mt32emu dequeues and processes them inside render(),
		 * so their cost is counted where it belongs. */
		Bit32u synthNow = synth.getInternalRenderedSampleCount();
		double blockEndSynth = double(synthNow) + double(thisBlock) * synthSamplesPerOutFrame;
		while (nextEvent < smf.events.size() &&
		       double(smf.events[nextEvent].samplePos) < blockEndSynth) {
			const MidiEvent &e = smf.events[nextEvent];
			Bit32u ts = e.samplePos < synthNow ? synthNow : Bit32u(e.samplePos);
			if (e.tempoUsPerQuarter != 0) {
				/* tempo marker, already folded into the timing; nothing to send */
			} else if (!e.sysex.empty()) {
				if (!synth.playSysex(&e.sysex[0], Bit32u(e.sysex.size()), ts)) queueDrops++;
			} else {
				if (!synth.playMsg(e.shortMsg, ts)) queueDrops++;
			}
			nextEvent++;
		}

		const double t0 = monotonicSeconds();
		if (src != NULL) {
			src->getOutputSamples(&buffer[0], thisBlock);
		} else {
			synth.render(&buffer[0], thisBlock);
		}
		const double t1 = monotonicSeconds();

		/* Stats gathering is outside the timed region on purpose. */
		const double blockSeconds = t1 - t0;
		const double blockAudioSeconds = double(thisBlock) / outRate;
		const double blockRtf = blockSeconds / blockAudioSeconds;

		unsigned int active = 0;
		synth.getPartialStates(&partialStates[0]);
		for (unsigned int i = 0; i < maxPartials; i++) {
			if (partialStates[i] != PartialState_INACTIVE) active++;
		}

		if (framesDone >= warmupFrames) {
			blocksCounted++;
			totalRenderSeconds += blockSeconds;
			if (blockSeconds > worstBlockSeconds) {
				worstBlockSeconds = blockSeconds;
				worstBlockRtf = blockRtf;
				worstBlockIndex = blockIndex;
			}
			if (active > peakPartials) peakPartials = active;
			if (csv) {
				fprintf(csv, "%llu,%.6f,%.9f,%.6f,%u\n",
					(unsigned long long)blockIndex,
					double(framesDone) / outRate, blockSeconds, blockRtf, active);
			}
		}

		framesDone += thisBlock;
		blockIndex++;
	}

	const double runEnd = monotonicSeconds();
	if (csv) fclose(csv);

	const double audioSeconds = double(totalFrames - warmupFrames) / outRate;
	const double rtf = audioSeconds > 0.0 ? totalRenderSeconds / audioSeconds : 0.0;

	printf("\n=== result ===\n");
	printf("audio rendered            : %.3f s (%llu frames @ %.0f Hz, %llu blocks of %u)\n",
		audioSeconds, (unsigned long long)(totalFrames - warmupFrames), outRate,
		(unsigned long long)blocksCounted, blockFrames);
	if (warmupFrames > 0) printf("warm-up discarded         : %.3f s\n", warmup);
	printf("render CPU time           : %.3f s\n", totalRenderSeconds);
	printf("wall clock incl. overhead : %.3f s\n", runEnd - runStart);
	printf("real-time factor (overall): %.4f\n", rtf);
	printf("worst-case block RTF      : %.4f  (block %llu, %.3f ms for %.3f ms of audio)\n",
		worstBlockRtf, (unsigned long long)worstBlockIndex,
		worstBlockSeconds * 1e3, (double(blockFrames) / outRate) * 1e3);
	printf("wall clock per s of audio : %.3f ms\n", rtf * 1e3);
	printf("peak active partials      : %u of %u\n", peakPartials, maxPartials);
	if (queueDrops) printf("WARNING: %lu MIDI events dropped (queue full) -- raise setMIDIEventQueueSize\n", queueDrops);
	printf("\ngate (mt32-t113 docs/PLAN.md): target overall and worst-case RTF <= 0.6\n");
	printf("verdict on THIS machine   : %s\n",
		worstBlockRtf <= 0.6 ? "within target" : (worstBlockRtf <= 1.0 ? "real time but over target" : "SLOWER THAN REAL TIME"));
	printf("NOTE: this number is only meaningful on the target silicon. On a host\n");
	printf("      x86 machine it says nothing about a 1.2 GHz Cortex-A7.\n");

	synth.close();
	delete src;
	for (size_t i = 0; i < merged.size(); i++) ROMImage::freeROMImage(merged[i]);
	for (size_t i = 0; i < roms.size(); i++) {
		ROMImage::freeROMImage(roms[i].image);
		delete roms[i].file;
	}
	return 0;
}
