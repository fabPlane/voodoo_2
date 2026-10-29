#ifndef PCI_BAR_H
#define PCI_BAR_H

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <dirent.h>
#include <fcntl.h>
#include <string>
#include <sys/mman.h>
#include <unistd.h>

/** Host-visible BAR0 contract for TangMega138kPro / PcieTlpBarTarget. */
namespace pci_bar {
constexpr uint32_t kDefaultVendor = 0x10ee;
constexpr uint32_t kDefaultDevice = 0x0007;
// Sipeed Pro Dock PCIe demo IP (used until we regenerate with 10ee:0007).
constexpr uint32_t kSipeedVendor = 0x22c2;
constexpr uint32_t kSipeedDevice = 0x1100;
constexpr uint32_t kBarBytes = 1u << 20;
// With a write-combined map, the uncached map covers only this much (the
// register page with its chip-select aliases: status, perf, bridge regs).
constexpr uint32_t kUcBytes = 0x4000;
constexpr uint32_t kWindowReg = 0x250;
constexpr uint32_t kStatusReg = 0x254;
constexpr uint32_t kWindowMask = kBarBytes - 1u;
constexpr uint32_t kVoodooBytes = 16u << 20;
constexpr uint32_t kLfbBase = 4u << 20;
constexpr uint32_t kTexBase = 8u << 20;
// Windows 16-31: raw DDR (needs a bitstream with the raw window, B26+). Framebuffer memory at
// raw 0, texture memory at raw 4 MiB; for trace state load and framebuffer readback.
constexpr uint32_t kRawBase = 16u << 20;
constexpr uint32_t kRawTexOffset = 4u << 20;

constexpr uint32_t kStatusWedged = 1u << 0;
constexpr uint32_t kStatusReqTimeout = 1u << 1;
constexpr uint32_t kStatusRspTimeout = 1u << 2;
constexpr uint32_t kStatusBusy = 1u << 4;
constexpr uint32_t kStatusReadBusy = 1u << 5;

inline uint32_t windowIndex(uint32_t voodooAddr) { return voodooAddr >> 20; }
inline uint32_t windowOffset(uint32_t voodooAddr) { return voodooAddr & kWindowMask; }

struct Device {
  std::string bdf;
  std::string sysfs;
  uint16_t vendor = 0;
  uint16_t device = 0;
};

inline uint16_t readHex16(const std::string &path) {
  FILE *f = fopen(path.c_str(), "r");
  if (!f) return 0;
  unsigned value = 0;
  if (fscanf(f, "%x", &value) != 1) value = 0;
  fclose(f);
  return static_cast<uint16_t>(value);
}

inline bool findDevice(uint16_t vendor, uint16_t device, const char *bdf, Device *out) {
  if (!out) return false;
  if (bdf && bdf[0]) {
    out->bdf = bdf;
    out->sysfs = std::string("/sys/bus/pci/devices/") + bdf;
    out->vendor = readHex16(out->sysfs + "/vendor");
    out->device = readHex16(out->sysfs + "/device");
    return access((out->sysfs + "/resource0").c_str(), F_OK) == 0;
  }
  DIR *dir = opendir("/sys/bus/pci/devices");
  if (!dir) return false;
  const uint32_t alts[][2] = {
      {vendor, device},
      {kSipeedVendor, kSipeedDevice},
      {kDefaultVendor, kDefaultDevice},
  };
  while (dirent *ent = readdir(dir)) {
    if (ent->d_name[0] == '.') continue;
    std::string sysfs = std::string("/sys/bus/pci/devices/") + ent->d_name;
    uint16_t foundVid = readHex16(sysfs + "/vendor");
    uint16_t foundDid = readHex16(sysfs + "/device");
    bool match = false;
    for (const auto &id : alts) {
      if (foundVid == id[0] && foundDid == id[1]) {
        match = true;
        break;
      }
    }
    if (!match) continue;
    out->bdf = ent->d_name;
    out->sysfs = sysfs;
    out->vendor = foundVid;
    out->device = foundDid;
    closedir(dir);
    return true;
  }
  closedir(dir);
  return false;
}

inline bool enableDevice(const Device &dev) {
  std::string path = dev.sysfs + "/enable";
  int fd = open(path.c_str(), O_RDWR);
  if (fd < 0) return false;
  if (write(fd, "1\n", 2) < 0) {
    close(fd);
    return false;
  }
  close(fd);
  return true;
}

class Bar {
public:
  ~Bar() { close(); }

  bool open(const Device &dev) {
    close();
    std::string path = dev.sysfs + "/resource0";
    fd_ = ::open(path.c_str(), O_RDWR | O_SYNC);
    if (fd_ < 0) {
      perror(path.c_str());
      return false;
    }
    void *raw = mmap(nullptr, kBarBytes, PROT_READ | PROT_WRITE, MAP_SHARED, fd_, 0);
    if (raw == MAP_FAILED) {
      perror("mmap resource0");
      ::close(fd_);
      fd_ = -1;
      return false;
    }
    base_ = static_cast<volatile uint8_t *>(raw);
    window_ = 0xffffffffu;
    return true;
  }

  // Write-combined BAR0 mapping (/dev/voodoo_bar0, voodoo_dma.ko; needs a
  // B173+ bitstream that takes multi-dword write TLPs). Writes into the
  // Voodoo map then go through it: ascending stores within a 64-byte line
  // merge into one TLP. Order is kept by an sfence before any store that is
  // not above the previous one in the same line, around command registers
  // (triangle / fastfill / swap / nop) and before every uncached access.
  //
  // Linux PAT keeps one memory type per physical range: a write-combined map
  // overlapping the uncached resource0 map silently came out uncached too
  // (2026-10-30: one TLP per dword, ~160 ns per write). So the uncached map
  // shrinks to the first kUcBytes (status, perf and bridge registers, the
  // window register) and only [kUcBytes, 1 MiB) is mapped write-combined;
  // accesses there go through it (reads after an sfence). Register writes
  // use the alias kUcBytes up (the register decode ignores address bits
  // 14-20). Needs voodoo_dma.ko with offset support.
  bool openWc() {
    wcFd_ = ::open("/dev/voodoo_bar0", O_RDWR);
    if (wcFd_ < 0) { perror("/dev/voodoo_bar0"); return false; }
    // Drop the full uncached map first: while its PAT reservation exists a
    // write-combined map of any part of the BAR is downgraded to uncached.
    munmap(const_cast<uint8_t *>(reinterpret_cast<volatile uint8_t *>(base_)), kBarBytes);
    base_ = nullptr;
    void *raw = mmap(nullptr, kBarBytes - kUcBytes, PROT_READ | PROT_WRITE, MAP_SHARED, wcFd_, kUcBytes);
    if (raw == MAP_FAILED) {
      perror("mmap voodoo_bar0 (needs offset support)");
      ::close(wcFd_); wcFd_ = -1;
      void *full = mmap(nullptr, kBarBytes, PROT_READ | PROT_WRITE, MAP_SHARED, fd_, 0);
      if (full != MAP_FAILED) base_ = static_cast<volatile uint8_t *>(full);
      return false;
    }
    void *uc = mmap(nullptr, kUcBytes, PROT_READ | PROT_WRITE, MAP_SHARED, fd_, 0);
    if (uc == MAP_FAILED) { perror("mmap resource0 (uncached part)"); base_ = nullptr; return false; }
    base_ = static_cast<volatile uint8_t *>(uc);
    ucBytes_ = kUcBytes;
    wcMap_ = static_cast<volatile uint8_t *>(raw);
    wc_ = wcMap_ - kUcBytes; // indexed by BAR offset; valid from kUcBytes up
    return true;
  }
  // Where an uncached-style access at `offset` goes once the BAR is split.
  volatile uint8_t *at(uint32_t offset) const { return offset < ucBytes_ ? base_ + offset : wc_ + offset; }
  void afterStore(uint32_t offset) const {
    if (offset >= ucBytes_) __asm__ __volatile__("sfence" ::: "memory");
    else __sync_synchronize();
  }

  void fenceWc() const {
    if (wcDirty_) { __asm__ __volatile__("sfence" ::: "memory"); wcDirty_ = false; wcLast_ = 0xffffffffu; }
  }

  void close() {
    fenceWc();
    if (wcMap_) {
      munmap(const_cast<uint8_t *>(reinterpret_cast<volatile uint8_t *>(wcMap_)), kBarBytes - kUcBytes);
      wcMap_ = nullptr;
      wc_ = nullptr;
    }
    if (wcFd_ >= 0) { ::close(wcFd_); wcFd_ = -1; }
    if (base_) {
      munmap(const_cast<uint8_t *>(reinterpret_cast<volatile uint8_t *>(base_)), ucBytes_);
      base_ = nullptr;
    }
    ucBytes_ = kBarBytes;
    if (fd_ >= 0) {
      ::close(fd_);
      fd_ = -1;
    }
    window_ = 0xffffffffu;
  }

  bool ok() const { return base_ != nullptr; }

  uint32_t readLocal32(uint32_t offset) const {
    fenceWc();
    if (offset >= ucBytes_) __asm__ __volatile__("sfence" ::: "memory");
    return *reinterpret_cast<volatile uint32_t *>(at(offset));
  }

  void writeLocal32(uint32_t offset, uint32_t value) {
    fenceWc();
    *reinterpret_cast<volatile uint32_t *>(at(offset)) = value;
    afterStore(offset);
  }

  uint16_t readLocal16(uint32_t offset) const {
    fenceWc();
    if (offset >= ucBytes_) __asm__ __volatile__("sfence" ::: "memory");
    return *reinterpret_cast<volatile uint16_t *>(at(offset));
  }

  void writeLocal16(uint32_t offset, uint16_t value) {
    fenceWc();
    *reinterpret_cast<volatile uint16_t *>(at(offset)) = value;
    afterStore(offset);
  }

  void selectWindow(uint32_t index) {
    if (window_ == index) return;
    fenceWc();
    // Byte store: the target treats an 8-bit write at 0x250 as "select
    // window" in every window (the Voodoo never takes byte writes), so the
    // LFB / texture word at offset 0x250 of each megabyte stays usable.
    *reinterpret_cast<volatile uint8_t *>(base_ + kWindowReg) = static_cast<uint8_t>(index);
    __sync_synchronize();
    window_ = index;
  }

  uint32_t read32(uint32_t voodooAddr) {
    selectWindow(windowIndex(voodooAddr));
    return readLocal32(windowOffset(voodooAddr));
  }

  // Flow control (on by default). Every read must finish inside the host root
  // port's completion timeout and waits behind earlier posted writes, so
  // writes into the Voodoo map are only issued while the SST status says the
  // core's 64-entry PCI FIFO has room. Without this a write can stall behind a
  // busy core for milliseconds: B28+ then wedges the BAR (writes dropped,
  // reads all-ones) rather than let the AMD host shut down. Raw-window writes
  // go straight to DDR and don't count.
  void setFlowControl(bool on) { flow_ = on; budget_ = 0; }
  uint64_t flowPolls() const { return flowPolls_; }

  void waitForRoom(uint32_t voodooAddr) {
    if (!flow_ || voodooAddr >= kRawBase) return;
    while (budget_ == 0) {
      flowPolls_++;
      uint32_t s = read32(0);
      if (s == 0xffffffffu) return;  // wedged / gone: don't spin, let the caller see it
      uint32_t freeEntries = s & 0x3fu;
      budget_ = freeEntries > 4 ? freeEntries - 4 : 0;
    }
    budget_--;
  }

  void write32(uint32_t voodooAddr, uint32_t value) {
    waitForRoom(voodooAddr);
    selectWindow(windowIndex(voodooAddr));
    if (!wc_ || voodooAddr >= kRawBase) {
      writeLocal32(windowOffset(voodooAddr), value);
      return;
    }
    uint32_t off = windowOffset(voodooAddr);
    if (off < ucBytes_) {
      // Register space: write the alias in the write-combined part.
      if (voodooAddr < 0x400000u) off |= kUcBytes;
      else { writeLocal32(off, value); return; }
    }
    uint32_t reg = voodooAddr & 0x3fcu;
    bool cmd = voodooAddr < 0x400000u &&
               (reg == 0x080 || reg == 0x100 || reg == 0x120 || reg == 0x124 || reg == 0x128);
    if (cmd || off <= wcLast_ || (off >> 6) != (wcLast_ >> 6)) fenceWc();
    *reinterpret_cast<volatile uint32_t *>(wc_ + off) = value;
    wcDirty_ = true;
    wcLast_ = off;
    if (cmd) fenceWc();
  }

  uint16_t read16(uint32_t voodooAddr) {
    selectWindow(windowIndex(voodooAddr));
    return readLocal16(windowOffset(voodooAddr));
  }

  void write16(uint32_t voodooAddr, uint16_t value) {
    waitForRoom(voodooAddr);
    selectWindow(windowIndex(voodooAddr));
    writeLocal16(windowOffset(voodooAddr), value);
  }

  // The bridge status word only exists in register space (window 0); in the
  // LFB / texture windows offset 0x254 is ordinary memory.
  uint32_t status() {
    selectWindow(0);
    return readLocal32(kStatusReg);
  }

  uint32_t window() const { return window_; }

  // A read that the FPGA can't complete in time (the core is busy behind a
  // stalled write, e.g. a full PCI FIFO during heavy rendering) comes back
  // all-ones. Treat that as "busy, ask again", not as a wedged bridge.
  bool wedged() {
    for (int i = 0; i < 300; ++i) {   // ~2 s worst case (7 ms per timed-out read)
      uint32_t s = status();
      if (s != 0xffffffffu) return (s & kStatusWedged) != 0;
    }
    return true;
  }

private:
  int fd_ = -1;
  volatile uint8_t *base_ = nullptr;
  int wcFd_ = -1;
  volatile uint8_t *wc_ = nullptr;
  volatile uint8_t *wcMap_ = nullptr; // the write-combined mapping itself
  uint32_t ucBytes_ = kBarBytes;        // uncached part of BAR0
  mutable bool wcDirty_ = false;
  mutable uint32_t wcLast_ = 0xffffffffu;
  uint32_t window_ = 0xffffffffu;
  bool flow_ = true;
  uint32_t budget_ = 0;
  uint64_t flowPolls_ = 0;
};

inline const char *statusText(uint32_t status) {
  static char buf[96];
  snprintf(buf, sizeof(buf), "wedged=%u req_to=%u rsp_to=%u busy=%u read=%u dropped=%u",
           (status & kStatusWedged) != 0, (status & kStatusReqTimeout) != 0,
           (status & kStatusRspTimeout) != 0, (status & kStatusBusy) != 0,
           (status & kStatusReadBusy) != 0, (status >> 8) & 0xffu);
  return buf;
}

}  // namespace pci_bar

#endif
