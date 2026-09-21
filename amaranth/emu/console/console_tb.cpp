#include "VTangConsole138k.h"
#include "VTangConsole138k___024root.h"
#include "verilated.h"

#include <png.h>

#include <algorithm>
#include <array>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <string>
#include <vector>

namespace {

constexpr int kWidth = 640;
constexpr int kHeight = 480;
constexpr std::size_t kDdrBytes = 12 * 1024 * 1024;

[[noreturn]] void fail(const std::string &message) {
    std::fprintf(stderr, "console-sim: %s\n", message.c_str());
    std::exit(1);
}

void write_png(const char *path, const std::vector<std::uint8_t> &rgb) {
    FILE *file = std::fopen(path, "wb");
    if (!file) fail(std::string("cannot open output ") + path);

    png_structp png = png_create_write_struct(PNG_LIBPNG_VER_STRING, nullptr, nullptr, nullptr);
    png_infop info = png ? png_create_info_struct(png) : nullptr;
    if (!png || !info) fail("libpng initialization failed");
    if (setjmp(png_jmpbuf(png))) fail("libpng write failed");

    png_init_io(png, file);
    png_set_IHDR(png, info, kWidth, kHeight, 8, PNG_COLOR_TYPE_RGB,
                 PNG_INTERLACE_NONE, PNG_COMPRESSION_TYPE_DEFAULT, PNG_FILTER_TYPE_DEFAULT);
    png_write_info(png, info);
    std::vector<png_bytep> rows(kHeight);
    for (int y = 0; y < kHeight; ++y)
        rows[y] = const_cast<png_bytep>(&rgb[static_cast<std::size_t>(y) * kWidth * 3]);
    png_write_image(png, rows.data());
    png_write_end(png, nullptr);
    png_destroy_write_struct(&png, &info);
    std::fclose(file);
}

std::size_t count_nonblack_pixels(const std::vector<std::uint8_t> &rgb) {
    std::size_t count = 0;
    for (std::size_t i = 0; i + 2 < rgb.size(); i += 3)
        if (rgb[i] || rgb[i + 1] || rgb[i + 2]) ++count;
    return count;
}

class DdrModel {
  public:
    explicit DdrModel(VTangConsole138k &dut) : dut_(dut), memory_(kDdrBytes, 0) {}

    void before_rising_edge() {
        dut_.ddr_rdDataValid = 0;
        dut_.ddr_rdDataEnd = 0;
        if (read_pending_) {
            const std::size_t byte_addr = static_cast<std::size_t>(read_addr_) * 2;
            for (int word = 0; word < 4; ++word) {
                std::uint32_t value = 0;
                for (int byte = 0; byte < 4; ++byte) {
                    const std::size_t at = byte_addr + word * 4 + byte;
                    if (at < memory_.size()) value |= std::uint32_t(memory_[at]) << (byte * 8);
                }
                dut_.ddr_rdData[word] = value;
            }
            dut_.ddr_rdDataValid = 1;
            dut_.ddr_rdDataEnd = 1;
            read_pending_ = false;
            ++reads_;
        }

        // Sample outputs before eval(): these are the values present at the
        // physical controller's rising edge. Sampling after eval catches the
        // next combinational command one cycle early and loses read replies.
        const bool cmd_en = dut_.ddr_cmdEn;
        if (cmd_en && last_cmd_en_)
            fail("DDR cmdEn remained asserted across consecutive rising edges");
        last_cmd_en_ = cmd_en;
        if (!cmd_en) return;
        const auto addr = static_cast<std::uint32_t>(dut_.ddr_addr);
        if (dut_.ddr_cmd == 1) {
            if (read_pending_) fail("more than one DDR read outstanding");
            read_addr_ = addr;
            read_pending_ = true;
            return;
        }
        if (dut_.ddr_cmd != 0) fail("unsupported DDR command");
        if (!dut_.ddr_wrDataEn || !dut_.ddr_wrDataEnd)
            fail("write command without one complete data beat");

        const std::size_t byte_addr = static_cast<std::size_t>(addr) * 2;
        const std::uint16_t mask = dut_.ddr_wrDataMask;
        if (mask == 0xffffu) ++fully_masked_writes_;
        else ++useful_writes_;
        for (int word = 0; word < 4; ++word) {
            const std::uint32_t value = dut_.ddr_wrData[word];
            for (int byte = 0; byte < 4; ++byte) {
                const int lane = word * 4 + byte;
                const std::size_t at = byte_addr + lane;
                if (!(mask & (1u << lane)) && at < memory_.size())
                    memory_[at] = static_cast<std::uint8_t>(value >> (byte * 8));
            }
        }
        ++writes_;
    }

    std::uint64_t reads() const { return reads_; }
    std::uint64_t writes() const { return writes_; }
    std::uint64_t fully_masked_writes() const { return fully_masked_writes_; }
    std::uint64_t useful_writes() const { return useful_writes_; }

    std::uint16_t read16(std::uint32_t byte_addr) const {
        if (static_cast<std::size_t>(byte_addr) + 1 >= memory_.size()) return 0;
        return std::uint16_t(memory_[byte_addr]) |
               (std::uint16_t(memory_[byte_addr + 1]) << 8);
    }

    void write_rgb565_png(const char *path, std::uint32_t base, std::uint32_t stride) const {
        std::vector<std::uint8_t> rgb(static_cast<std::size_t>(kWidth) * kHeight * 3, 0);
        for (int y = 0; y < kHeight; ++y) {
            for (int x = 0; x < kWidth; ++x) {
                const std::size_t at = static_cast<std::size_t>(base) +
                                       static_cast<std::size_t>(y) * stride * 2 + x * 2;
                if (at + 1 >= memory_.size()) continue;
                const std::uint16_t pixel = std::uint16_t(memory_[at]) |
                                            (std::uint16_t(memory_[at + 1]) << 8);
                const std::size_t out = (static_cast<std::size_t>(y) * kWidth + x) * 3;
                rgb[out + 0] = static_cast<std::uint8_t>(((pixel >> 11) & 0x1f) * 255 / 31);
                rgb[out + 1] = static_cast<std::uint8_t>(((pixel >> 5) & 0x3f) * 255 / 63);
                rgb[out + 2] = static_cast<std::uint8_t>((pixel & 0x1f) * 255 / 31);
            }
        }
        write_png(path, rgb);
    }

  private:
    VTangConsole138k &dut_;
    std::vector<std::uint8_t> memory_;
    bool read_pending_ = false;
    std::uint32_t read_addr_ = 0;
    std::uint64_t reads_ = 0;
    std::uint64_t writes_ = 0;
    std::uint64_t fully_masked_writes_ = 0;
    std::uint64_t useful_writes_ = 0;
    bool last_cmd_en_ = false;
};

class FrameGrabber {
  public:
    explicit FrameGrabber(VTangConsole138k &dut) : dut_(dut) {
        pixels_.reserve(static_cast<std::size_t>(kWidth) * kHeight * 3);
    }

    void request() { requested_ = true; }

    void sample() {
        const bool hs_falling = last_hs_ && !dut_.hdmiHs;
        const bool vs_falling = last_vs_ && !dut_.hdmiVs;

        if (armed_) {
            ++frame_clocks_;
            if (hs_falling) ++total_lines_;
        }

        if (vs_falling) {
            if (armed_) {
                if (frame_clocks_ != 800 * 525)
                    fail("HDMI frame was " + std::to_string(frame_clocks_) +
                         " pixel clocks, expected 420000");
                if (total_lines_ != 525)
                    fail("HDMI frame was " + std::to_string(total_lines_) +
                         " total lines, expected 525");
                if (active_lines_ != kHeight)
                    fail("HDMI frame was " + std::to_string(active_lines_) +
                         " active lines, expected 480");
                complete_ = pixels_.size() == static_cast<std::size_t>(kWidth) * kHeight * 3;
            } else if (requested_) {
                // Discard the partial frame following reset and begin at a
                // real vertical-sync boundary.
                armed_ = true;
                pixels_.clear();
                active_lines_ = line_pixels_ = total_lines_ = frame_clocks_ = 0;
            }
        }

        if (armed_ && !complete_ && dut_.hdmiDe) {
            if (!last_de_) {
                if (line_pixels_ != 0) fail("active-video line did not terminate");
                ++active_lines_;
            }
            if (active_lines_ <= kHeight) {
                pixels_.push_back(dut_.hdmiR);
                pixels_.push_back(dut_.hdmiG);
                pixels_.push_back(dut_.hdmiB);
            }
            ++line_pixels_;
        } else if (armed_ && !complete_ && last_de_) {
            if (line_pixels_ != kWidth)
                fail("HDMI active width was " + std::to_string(line_pixels_) + ", expected 640");
            line_pixels_ = 0;
        }
        last_de_ = dut_.hdmiDe;
        last_hs_ = dut_.hdmiHs;
        last_vs_ = dut_.hdmiVs;
    }

    bool complete() const { return complete_; }

    const std::vector<std::uint8_t> &pixels() const { return pixels_; }
    int lines() const { return active_lines_; }

  private:
    VTangConsole138k &dut_;
    std::vector<std::uint8_t> pixels_;
    int active_lines_ = 0;
    int line_pixels_ = 0;
    int total_lines_ = 0;
    int frame_clocks_ = 0;
    bool armed_ = false;
    bool requested_ = false;
    bool complete_ = false;
    bool last_de_ = false;
    bool last_hs_ = false;
    bool last_vs_ = false;
};

}  // namespace

int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv);
    const char *output = argc > 1 ? argv[1] : "output/boot-menu.png";
    const std::string scenario = argc > 2 ? argv[2] : "boot";
    const std::array<std::string, 4> demo_names = {"logo", "test04", "test16", "alphabet"};
    const auto demo_it = std::find(demo_names.begin(), demo_names.end(), scenario);
    const bool run_demo = demo_it != demo_names.end();
    const int demo_index = run_demo ? static_cast<int>(demo_it - demo_names.begin()) : -1;
    if (!run_demo && scenario != "boot") fail("unknown scenario: " + scenario);
    std::filesystem::create_directories(std::filesystem::path(output).parent_path());
    auto framebuffer_path = std::filesystem::path(output);
    framebuffer_path.replace_extension();
    const auto first_framebuffer_path = framebuffer_path.string() + "-ddr.png";
    const auto delayed_framebuffer_path = framebuffer_path.string() + "-ddr-late.png";

    VTangConsole138k dut;
    dut.clk = dut.ctrlClk = dut.hdmiClk = dut.ddrClk = 0;
    dut.reset = dut.ctrlReset = dut.hdmiReset = dut.ddrReset = 1;
    dut.ddr_cmdReady = 1;
    dut.ddr_wrDataRdy = 1;
    dut.ddr_rdDataValid = dut.ddr_rdDataEnd = 0;
    for (int i = 0; i < 4; ++i) dut.ddr_rdData[i] = 0;
    dut.ddr_initCalibComplete = 0;
    dut.hid1 = dut.hid2 = 0;
    dut.keyUserN = dut.keyResetN = 1;
    dut.uartLink = 0;
    dut.uartLastCmd = 0;
    dut.uartRxCount = dut.uartHidCount = 0;
    dut.usbOnline = 0;
    dut.eval();

    DdrModel ddr(dut);
    FrameGrabber grabber(dut);
    if (!run_demo) grabber.request();

    // Picoseconds. These reproduce the board domains closely: core 30 MHz,
    // HDMI 25 MHz, control 50 MHz, and DDR UI 75 MHz.
    std::uint64_t now = 0;
    std::uint64_t next_core = 16'667;
    std::uint64_t next_hdmi = 20'000;
    std::uint64_t next_ctrl = 10'000;
    std::uint64_t next_ddr = 6'667;
    constexpr std::uint64_t kResetRelease = 2'000'000;
    constexpr std::uint64_t kCalibDone = 10'000'000;
    constexpr std::uint64_t kMenuStart = 90'000'000'000ULL;
    constexpr std::uint64_t kMenuStep = 2'000'000'000ULL;
    constexpr std::uint64_t kMenuPress = 500'000'000ULL;
    constexpr std::uint64_t kTimeout = 8'000'000'000'000ULL;
    std::uint16_t last_player_index = 0;
    bool demo_capture_requested = false;
    bool first_swap_seen = false;
    std::uint64_t first_swap_time = 0;
    std::uint32_t fastfill_fires = 0;
    std::uint64_t nonzero_fifo_heads = 0;
    std::uint64_t nonzero_hdmi_pixels = 0;
    std::uint64_t first_nonzero_hdmi = 0;
    std::uint64_t last_nonzero_hdmi = 0;

    while (!grabber.complete() && now < kTimeout) {
        now = std::min({next_core, next_hdmi, next_ctrl, next_ddr});
        const bool core_event = now == next_core;
        const bool hdmi_event = now == next_hdmi;
        const bool ctrl_event = now == next_ctrl;
        const bool ddr_event = now == next_ddr;
        const bool ddr_rising = ddr_event && !dut.ddrClk;
        const bool hdmi_rising = hdmi_event && !dut.hdmiClk;
        const bool core_rising = core_event && !dut.clk;

        if (now >= kResetRelease) dut.reset = dut.ctrlReset = dut.hdmiReset = dut.ddrReset = 0;
        if (now >= kCalibDone) dut.ddr_initCalibComplete = 1;
        if (run_demo) {
            // Walk the real menu through the TangCore SNES-layout HID input,
            // then press A on the requested demo.
            dut.hid1 = 0;
            for (int i = 0; i < demo_index; ++i) {
                const auto down_at = kMenuStart + static_cast<std::uint64_t>(i) * kMenuStep;
                if (now >= down_at && now < down_at + kMenuPress)
                    dut.hid1 |= 1u << 5;  // SnesPad.Down
            }
            const auto select_at = kMenuStart + static_cast<std::uint64_t>(demo_index) * kMenuStep;
            if (now >= select_at && now < select_at + kMenuPress)
                dut.hid1 |= 1u << 8;  // SnesPad.A
        }
        if (ddr_rising) ddr.before_rising_edge();
        if (run_demo && core_rising &&
            dut.rootp->TangConsole138k__DOT__core_1__DOT__core_1__DOT__pixelPipeline_1__DOT__fastfillWriter__DOT__io_cmd_fire) {
            ++fastfill_fires;
            std::printf("fastfill fire %u at %.3f ms captured=%08x draw=%08x "
                        "swap=%u/%u\n", fastfill_fires,
                        now / 1.0e9,
                        unsigned(dut.rootp->TangConsole138k__DOT__core_1__DOT__core_1__DOT__pixelPipeline_1__DOT__fastfillWriter__DOT__regsCapture_routing_colorBaseAddr),
                        unsigned(dut.rootp->TangConsole138k__DOT__core_1__DOT__core_1__DOT__controlPlane_framebufferLayout_draw_colorBaseAddr),
                        unsigned(dut.rootp->TangConsole138k__DOT__core_1__DOT__core_1__DOT__controlPlane_swapBuffer__DOT__state_4),
                        unsigned(dut.rootp->TangConsole138k__DOT__core_1__DOT__core_1__DOT__controlPlane_swapBuffer__DOT__swapCountReg));
        }

        if (core_event) { dut.clk = !dut.clk; next_core += 16'667; }
        if (hdmi_event) { dut.hdmiClk = !dut.hdmiClk; next_hdmi += 20'000; }
        if (ctrl_event) {
            // Once the first completed frame is displayed, freeze the demo
            // command source so this capture observes one stable front buffer.
            if (!first_swap_seen) dut.ctrlClk = !dut.ctrlClk;
            next_ctrl += 10'000;
        }
        if (ddr_event) { dut.ddrClk = !dut.ddrClk; next_ddr += 6'667; }
        dut.eval();

        if (hdmi_rising && now >= kResetRelease) {
            if (first_swap_seen &&
                dut.rootp->TangConsole138k__DOT__core_1__DOT__core_1__DOT__hdmiScanout__DOT__pixelFifo_io_pop_ready &&
                dut.rootp->TangConsole138k__DOT__core_1__DOT__core_1__DOT__hdmiScanout__DOT__pixelFifo__DOT__ram_spinal_port1 != 0)
                ++nonzero_fifo_heads;
            if (first_swap_seen && dut.hdmiDe && (dut.hdmiR || dut.hdmiG || dut.hdmiB)) {
                if (first_nonzero_hdmi == 0) first_nonzero_hdmi = now;
                last_nonzero_hdmi = now;
                ++nonzero_hdmi_pixels;
            }
            if (run_demo && !demo_capture_requested) {
                const std::uint16_t player_index = dut.jtagWrites_5 & 0xffffu;
                if (player_index != last_player_index &&
                    (player_index == 113 || player_index == 289 || player_index == 291 ||
                     player_index == 360 || player_index == 369 || player_index == 371 ||
                     player_index == 388 || player_index == 423)) {
                    std::printf("%s progress: %.3f ms player=%u writes=%llu\n",
                                scenario.c_str(),
                                now / 1.0e9, unsigned(player_index),
                                static_cast<unsigned long long>(ddr.writes()));
                }
                // The player can run ahead into its next loop while the PCI
                // FIFO is still draining. Arm only when SwapBuffer confirms
                // that the first queued swap completed at vertical retrace.
                const auto swap_count = dut.rootp->TangConsole138k__DOT__core_1__DOT__core_1__DOT__controlPlane_swapBuffer__DOT__swapCountReg;
                if (swap_count != 0 && !first_swap_seen) {
                    const auto front = dut.rootp->TangConsole138k__DOT__core_1__DOT__core_1__DOT__controlPlane_framebufferLayout_front;
                    const auto draw = dut.rootp->TangConsole138k__DOT__core_1__DOT__core_1__DOT__controlPlane_framebufferLayout_draw_colorBaseAddr;
                    const auto prefetch = dut.rootp->TangConsole138k__DOT__core_1__DOT__core_1__DOT__hdmiScanout__DOT__prefetchBase;
                    const auto stride = unsigned(dut.rootp->TangConsole138k__DOT__core_1__DOT__core_1__DOT__regBank_sim_reg_214_f4) << 6;
                    std::printf("first swap completed at %.3f ms; writes=%llu nonzero=%u\n",
                                now / 1.0e9,
                                static_cast<unsigned long long>(ddr.writes()),
                                dut.jtagWrites_12);
                    std::printf("framebuffer: front=%08x draw=%08x prefetch=%08x stride=%u\n",
                                front, draw, prefetch, stride);
                    ddr.write_rgb565_png(first_framebuffer_path.c_str(), front, stride);
                    first_swap_seen = true;
                    first_swap_time = now;
                }
                last_player_index = player_index;
            }
            if (run_demo && first_swap_seen && !demo_capture_requested &&
                now >= first_swap_time + 200'000'000'000ULL) {
                const auto front = dut.rootp->TangConsole138k__DOT__core_1__DOT__core_1__DOT__controlPlane_framebufferLayout_front;
                const auto draw = dut.rootp->TangConsole138k__DOT__core_1__DOT__core_1__DOT__controlPlane_framebufferLayout_draw_colorBaseAddr;
                const auto prefetch = dut.rootp->TangConsole138k__DOT__core_1__DOT__core_1__DOT__hdmiScanout__DOT__prefetchBase;
                const auto stride = unsigned(dut.rootp->TangConsole138k__DOT__core_1__DOT__core_1__DOT__regBank_sim_reg_214_f4) << 6;
                std::printf("delayed capture: front=%08x draw=%08x prefetch=%08x\n", front, draw, prefetch);
                ddr.write_rgb565_png(delayed_framebuffer_path.c_str(), front, stride);
                grabber.request();
                demo_capture_requested = true;
            }
            grabber.sample();
        }
    }

    dut.final();
    if (!grabber.complete()) {
        std::fprintf(stderr,
                     "console-sim status: writes5=%08x writes12=%08x writes15=%08x "
                     "debug1=%08x debug2=%08x leds(calib/done/wedge)=%u/%u/%u "
                     "DDR reads=%llu writes=%llu\n",
                     dut.jtagWrites_5, dut.jtagWrites_12, dut.jtagWrites_15,
                     dut.jtagDebug_1, dut.jtagDebug_2,
                     unsigned(dut.ledCalib), unsigned(dut.ledDone), unsigned(dut.ledWedge),
                     static_cast<unsigned long long>(ddr.reads()),
                     static_cast<unsigned long long>(ddr.writes()));
        fail("timed out after " + std::to_string(now / 1'000'000'000.0) + " ms with " +
             std::to_string(grabber.lines()) + " active lines");
    }
    write_png(output, grabber.pixels());
    const auto captured_nonblack = count_nonblack_pixels(grabber.pixels());
    std::printf("captured %dx%d %s frame to %s; DDR reads=%llu writes=%llu; sim %.3f ms\n",
                kWidth, kHeight, scenario.c_str(), output,
                static_cast<unsigned long long>(ddr.reads()),
                static_cast<unsigned long long>(ddr.writes()), now / 1.0e9);
    std::printf("captured non-black pixels: %zu\n", captured_nonblack);
    std::printf("DDR counters: debug3=%08x debug4=%08x writes1=%08x writes2=%08x\n",
                dut.jtagDebug_3, dut.jtagDebug_4, dut.jtagWrites_1, dut.jtagWrites_2);
    std::printf("scanout: debug1=%08x debug2=%08x cache=%08x%08x "
                "read=%08x expected=%08x occupancy=%08x remaining=%08x\n",
                dut.jtagDebug_1, dut.jtagDebug_2, dut.jtagDebug_6, dut.jtagDebug_5,
                dut.jtagDebug_7, dut.jtagDebug_8, dut.jtagDebug_9, dut.jtagDebug_10);
    std::printf("scanout fills: hits=%u misses=%u bursts=%u beats=%u stalls=%u "
                "sample-meta=%08x nonzero=%u last=%08x\n",
                dut.jtagDebug_11, dut.jtagDebug_12, dut.jtagDebug_13,
                dut.jtagDebug_14, dut.jtagDebug_15, dut.jtagFramebuffer_1,
                dut.jtagFramebuffer_2, dut.jtagFramebuffer_3);
    std::printf("DDR write masks: useful=%llu fully-masked=%llu\n",
                static_cast<unsigned long long>(ddr.useful_writes()),
                static_cast<unsigned long long>(ddr.fully_masked_writes()));
    if (run_demo)
        std::printf("%s status: player=%u nonzero-writes=%u busy=%08x status=%08x\n",
                    scenario.c_str(), unsigned(dut.jtagWrites_5 & 0xffffu), dut.jtagWrites_12,
                    dut.jtagWrites_13, dut.jtagWrites_15);
    if (run_demo) {
        std::printf("visible nonzero: fifo-heads=%llu hdmi-pixels=%llu frame-aligned=%u\n",
                    static_cast<unsigned long long>(nonzero_fifo_heads),
                    static_cast<unsigned long long>(nonzero_hdmi_pixels),
                    unsigned(dut.rootp->TangConsole138k__DOT__core_1__DOT__core_1__DOT__hdmiScanout__DOT__hdmi_frameAligned));
        std::printf("visible interval: first=%.3f ms last=%.3f ms\n",
                    first_nonzero_hdmi / 1.0e9, last_nonzero_hdmi / 1.0e9);
        std::printf("scanout samples: meta=%08x hash=%08x last=%08x\n",
                    dut.jtagFramebuffer_1, dut.jtagFramebuffer_2, dut.jtagFramebuffer_3);
        std::printf("  %08x %08x %08x %08x %08x %08x\n",
                    dut.jtagFramebuffer_4, dut.jtagFramebuffer_5, dut.jtagFramebuffer_6,
                    dut.jtagFramebuffer_7, dut.jtagFramebuffer_8, dut.jtagFramebuffer_9);
        std::printf("  %08x %08x %08x %08x %08x %08x\n",
                    dut.jtagFramebuffer_10, dut.jtagFramebuffer_11, dut.jtagFramebuffer_12,
                    dut.jtagFramebuffer_13, dut.jtagFramebuffer_14, dut.jtagFramebuffer_15);
    }
    if (run_demo) {
        const auto *r = dut.rootp;
        std::printf("hdmi internals: run=%u armed=%u aligned=%u underflow=%u "
                    "gamma0=%06x gamma16=%06x gamma32=%06x\n",
                    unsigned(r->TangConsole138k__DOT__core_1__DOT__core_1__DOT__hdmiScanout__DOT__scanoutRun),
                    unsigned(r->TangConsole138k__DOT__core_1__DOT__core_1__DOT__hdmiScanout__DOT__scanoutRun_buffercc__DOT__buffers_1),
                    unsigned(r->TangConsole138k__DOT__core_1__DOT__core_1__DOT__hdmiScanout__DOT__hdmi_frameAligned),
                    unsigned(r->TangConsole138k__DOT__core_1__DOT__core_1__DOT__hdmiScanout__DOT__hdmi_underflowReg),
                    r->TangConsole138k__DOT__core_1__DOT__core_1__DOT__regBank__DOT__gammaLut_0,
                    r->TangConsole138k__DOT__core_1__DOT__core_1__DOT__regBank__DOT__gammaLut_16,
                    r->TangConsole138k__DOT__core_1__DOT__core_1__DOT__regBank__DOT__gammaLut_32);
        std::printf("fastfill: active=%u clip=(%u,%u)-(%u,%u) pos=(%u,%u) "
                    "nextY=%u rowEnd=%u last=%u fires=%u base=%08x\n",
                    unsigned(r->TangConsole138k__DOT__core_1__DOT__core_1__DOT__pixelPipeline_1__DOT__fastfillWriter__DOT__active),
                    unsigned(r->TangConsole138k__DOT__core_1__DOT__core_1__DOT__pixelPipeline_1__DOT__fastfillWriter__DOT__clipLeftCapture),
                    unsigned(r->TangConsole138k__DOT__core_1__DOT__core_1__DOT__pixelPipeline_1__DOT__fastfillWriter__DOT__clipLowYCapture),
                    unsigned(r->TangConsole138k__DOT__core_1__DOT__core_1__DOT__pixelPipeline_1__DOT__fastfillWriter__DOT__clipRightCapture),
                    unsigned(r->TangConsole138k__DOT__core_1__DOT__core_1__DOT__pixelPipeline_1__DOT__fastfillWriter__DOT__clipHighYCapture),
                    unsigned(r->TangConsole138k__DOT__core_1__DOT__core_1__DOT__pixelPipeline_1__DOT__fastfillWriter__DOT__curWordX),
                    unsigned(r->TangConsole138k__DOT__core_1__DOT__core_1__DOT__pixelPipeline_1__DOT__fastfillWriter__DOT__curY),
                    unsigned(r->TangConsole138k__DOT__core_1__DOT__core_1__DOT__pixelPipeline_1__DOT__fastfillWriter__DOT__nextYWide),
                    unsigned(r->TangConsole138k__DOT__core_1__DOT__core_1__DOT__pixelPipeline_1__DOT__fastfillWriter__DOT__atRowEnd),
                    unsigned(r->TangConsole138k__DOT__core_1__DOT__core_1__DOT__pixelPipeline_1__DOT__fastfillWriter__DOT__isLastWord),
                    fastfill_fires,
                    unsigned(r->TangConsole138k__DOT__core_1__DOT__core_1__DOT__pixelPipeline_1__DOT__fastfillWriter__DOT__regsCapture_routing_colorBaseAddr));
        if (captured_nonblack < 100)
            fail(scenario + " capture is effectively black");
        if (r->TangConsole138k__DOT__core_1__DOT__core_1__DOT__hdmiScanout__DOT__hdmi_underflowReg)
            fail("HDMI scanout underflowed during " + scenario + " capture");
    }
    return 0;
}
