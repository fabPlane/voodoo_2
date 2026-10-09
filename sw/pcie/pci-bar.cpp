#include "pci_bar.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>

namespace {

void usage(const char *argv0) {
  fprintf(stderr,
          "Usage: %s [--bdf BDF] [--vid HEX] [--did HEX] <command>\n"
          "  list\n"
          "  status\n"
          "  window [N]\n"
          "  peek ADDR\n"
          "  poke ADDR VALUE\n"
          "  dump ADDR COUNT\n",
          argv0);
}

bool parseHex(const char *s, uint32_t *out) {
  char *end = nullptr;
  unsigned long v = strtoul(s, &end, 0);
  if (!s || !end || *end) return false;
  *out = static_cast<uint32_t>(v);
  return true;
}

int openBar(uint16_t vid, uint16_t did, const char *bdf, pci_bar::Device *dev, pci_bar::Bar *bar) {
  if (!pci_bar::findDevice(vid, did, bdf, dev)) {
    fprintf(stderr, "No PCI device %04x:%04x%s%s\n", vid, did, bdf ? " at " : "",
            bdf ? bdf : "");
    return 1;
  }
  fprintf(stderr, "Found %s  %04x:%04x\n", dev->bdf.c_str(), dev->vendor, dev->device);
  pci_bar::enableDevice(*dev);
  if (!bar->open(*dev)) return 1;
  return 0;
}

}  // namespace

int main(int argc, char **argv) {
  uint16_t vid = pci_bar::kDefaultVendor;
  uint16_t did = pci_bar::kDefaultDevice;
  const char *bdf = nullptr;
  int arg = 1;
  while (arg < argc && strncmp(argv[arg], "--", 2) == 0) {
    if (strcmp(argv[arg], "--bdf") == 0 && arg + 1 < argc) {
      bdf = argv[++arg];
    } else if (strcmp(argv[arg], "--vid") == 0 && arg + 1 < argc) {
      uint32_t v = 0;
      if (!parseHex(argv[++arg], &v)) return 2;
      vid = static_cast<uint16_t>(v);
    } else if (strcmp(argv[arg], "--did") == 0 && arg + 1 < argc) {
      uint32_t v = 0;
      if (!parseHex(argv[++arg], &v)) return 2;
      did = static_cast<uint16_t>(v);
    } else if (strcmp(argv[arg], "-h") == 0 || strcmp(argv[arg], "--help") == 0) {
      usage(argv[0]);
      return 0;
    } else {
      usage(argv[0]);
      return 2;
    }
    ++arg;
  }
  if (arg >= argc) {
    usage(argv[0]);
    return 2;
  }

  const char *cmd = argv[arg++];
  if (strcmp(cmd, "list") == 0) {
    pci_bar::Device dev;
    if (pci_bar::findDevice(vid, did, bdf, &dev)) {
      printf("%s %04x:%04x %s\n", dev.bdf.c_str(), dev.vendor, dev.device, dev.sysfs.c_str());
      return 0;
    }
    fprintf(stderr, "No matching device. lspci -nn after a cold plug.\n");
    return 1;
  }

  pci_bar::Device dev;
  pci_bar::Bar bar;
  if (int rc = openBar(vid, did, bdf, &dev, &bar)) return rc;

  if (strcmp(cmd, "status") == 0) {
    uint32_t status = bar.status();
    uint32_t window = bar.readLocal32(pci_bar::kWindowReg);
    printf("status=0x%08x %s\n", status, pci_bar::statusText(status));
    printf("window=%u\n", window & 0xfu);
    return (status & pci_bar::kStatusWedged) ? 3 : 0;
  }

  if (strcmp(cmd, "window") == 0) {
    if (arg < argc) {
      uint32_t n = 0;
      if (!parseHex(argv[arg], &n)) return 2;
      bar.writeLocal32(pci_bar::kWindowReg, n);
    }
    printf("window=%u\n", bar.readLocal32(pci_bar::kWindowReg) & 0xfu);
    return 0;
  }

  if (strcmp(cmd, "peek") == 0 && arg < argc) {
    uint32_t addr = 0;
    if (!parseHex(argv[arg], &addr)) return 2;
    printf("0x%08x\n", bar.read32(addr));
    return 0;
  }

  if (strcmp(cmd, "poke") == 0 && arg + 1 < argc) {
    uint32_t addr = 0;
    uint32_t value = 0;
    if (!parseHex(argv[arg], &addr) || !parseHex(argv[arg + 1], &value)) return 2;
    bar.write32(addr, value);
    printf("wrote 0x%08x = 0x%08x\n", addr, value);
    return 0;
  }

  if (strcmp(cmd, "dump") == 0 && arg + 1 < argc) {
    uint32_t addr = 0;
    uint32_t count = 0;
    if (!parseHex(argv[arg], &addr) || !parseHex(argv[arg + 1], &count)) return 2;
    for (uint32_t i = 0; i < count; ++i) {
      uint32_t a = addr + i * 4u;
      printf("%08x: %08x\n", a, bar.read32(a));
    }
    return 0;
  }

  usage(argv[0]);
  return 2;
}
