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
constexpr uint32_t kWindowReg = 0x250;
constexpr uint32_t kStatusReg = 0x254;
constexpr uint32_t kWindowMask = kBarBytes - 1u;
constexpr uint32_t kVoodooBytes = 16u << 20;
constexpr uint32_t kLfbBase = 4u << 20;
constexpr uint32_t kTexBase = 8u << 20;

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

  void close() {
    if (base_) {
      munmap(const_cast<uint8_t *>(reinterpret_cast<volatile uint8_t *>(base_)), kBarBytes);
      base_ = nullptr;
    }
    if (fd_ >= 0) {
      ::close(fd_);
      fd_ = -1;
    }
    window_ = 0xffffffffu;
  }

  bool ok() const { return base_ != nullptr; }

  uint32_t readLocal32(uint32_t offset) const {
    return *reinterpret_cast<volatile uint32_t *>(base_ + offset);
  }

  void writeLocal32(uint32_t offset, uint32_t value) {
    *reinterpret_cast<volatile uint32_t *>(base_ + offset) = value;
    __sync_synchronize();
  }

  uint16_t readLocal16(uint32_t offset) const {
    return *reinterpret_cast<volatile uint16_t *>(base_ + offset);
  }

  void writeLocal16(uint32_t offset, uint16_t value) {
    *reinterpret_cast<volatile uint16_t *>(base_ + offset) = value;
    __sync_synchronize();
  }

  void selectWindow(uint32_t index) {
    if (window_ == index) return;
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

  void write32(uint32_t voodooAddr, uint32_t value) {
    selectWindow(windowIndex(voodooAddr));
    writeLocal32(windowOffset(voodooAddr), value);
  }

  uint16_t read16(uint32_t voodooAddr) {
    selectWindow(windowIndex(voodooAddr));
    return readLocal16(windowOffset(voodooAddr));
  }

  void write16(uint32_t voodooAddr, uint16_t value) {
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
  uint32_t window_ = 0xffffffffu;
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
