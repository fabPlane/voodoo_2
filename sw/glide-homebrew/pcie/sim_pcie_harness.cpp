/*
 * sim_pcie_harness.cpp - sim_harness.h backend that drives the real board.
 *
 * The SpinalVoodoo Glide2x "sim" backend talks to hardware only through
 * sim_init / sim_write / sim_read / sim_idle_wait / sim_shutdown. This file
 * implements them over the Tang Mega 138K Pro Dock's PCIe BAR0 (see
 * sw/pcie/pci_bar.h for the 1 MiB window + status-word contract), so an
 * unmodified Glide program renders on the FPGA instead of in Verilator.
 *
 * Environment:
 *   VOODOO_PCI_BDF=0000:05:00.0   pick a device (default: first match)
 *   VOODOO_PCI_STATS=1            print MMIO counts at shutdown
 *
 * SPDX-License-Identifier: MIT
 */
#include "../../pcie/pci_bar.h"

#include <chrono>
#include <cstdlib>

extern "C" {
#include "sim_harness.h"
}

namespace {
pci_bar::Device g_dev;
pci_bar::Bar g_bar;
bool g_open = false;
uint64_t g_writes = 0, g_reads = 0, g_idle_polls = 0;

// SST-1 status register (Voodoo address 0x000).
constexpr uint32_t kSstBusy = 1u << 9;
constexpr uint32_t kPciFifoFreeMask = 0x3fu;

bool checkBridge(const char *where) {
  // All-ones = read timed out while the core was busy; Bar::wedged() retries.
  if (g_bar.wedged()) {
    uint32_t s = g_bar.status();
    fprintf(stderr, "[pcie] %s: host bridge wedged or gone (%s)\n", where, pci_bar::statusText(s));
    return false;
  }
  return true;
}
}  // namespace

extern "C" int sim_init(void) {
  if (g_open) return 0;
  const char *bdf = getenv("VOODOO_PCI_BDF");
  if (!pci_bar::findDevice(pci_bar::kDefaultVendor, pci_bar::kDefaultDevice, bdf, &g_dev)) {
    fprintf(stderr, "[pcie] no Voodoo PCIe device found (set VOODOO_PCI_BDF?)\n");
    return -1;
  }
  pci_bar::enableDevice(g_dev);  // best effort; fails harmlessly if already enabled
  if (!g_bar.open(g_dev)) {
    fprintf(stderr, "[pcie] cannot map %s/resource0 (run as root)\n", g_dev.sysfs.c_str());
    return -1;
  }
  g_open = true;
  fprintf(stderr, "[pcie] Voodoo on %s (%04x:%04x), bridge %s\n", g_dev.bdf.c_str(),
          g_dev.vendor, g_dev.device, pci_bar::statusText(g_bar.status()));
  return checkBridge("init") ? 0 : -1;
}

extern "C" void sim_shutdown(void) {
  if (!g_open) return;
  if (getenv("VOODOO_PCI_STATS"))
    fprintf(stderr, "[pcie] %llu writes, %llu reads, %llu idle polls; bridge %s\n",
            (unsigned long long)g_writes, (unsigned long long)g_reads,
            (unsigned long long)g_idle_polls, pci_bar::statusText(g_bar.status()));
  g_bar.close();
  g_open = false;
}

extern "C" void sim_write(uint32_t addr, uint32_t data) {
  g_writes++;
  g_bar.write32(addr & 0xfffffcu, data);
}

extern "C" void sim_write16(uint32_t addr, uint16_t data) {
  g_writes++;
  g_bar.write16(addr & 0xfffffeu, data);
}

extern "C" uint32_t sim_read(uint32_t addr) {
  g_reads++;
  return g_bar.read32(addr & 0xfffffcu);
}

extern "C" uint32_t sim_idle_wait(void) {
  // Wait for the core to drain: SST busy clear and the PCI FIFO empty.
  auto start = std::chrono::steady_clock::now();
  for (;;) {
    g_idle_polls++;
    uint32_t s = g_bar.read32(0);
    if (!(s & kSstBusy) && (s & kPciFifoFreeMask) == kPciFifoFreeMask) return s;
    if ((g_idle_polls & 0x3ff) == 0) {
      if (!checkBridge("idle_wait")) return s;
      if (std::chrono::steady_clock::now() - start > std::chrono::seconds(5)) {
        fprintf(stderr, "[pcie] idle_wait: still busy after 5 s (status 0x%08x)\n", s);
        return s;
      }
    }
  }
}

// Unused by libglide2x but part of sim_harness.h; keep the link complete for
// tools that expect the full API.
extern "C" void sim_tick(int) {}
extern "C" void sim_flush_fb_cache(void) {}
