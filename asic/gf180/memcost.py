# memcost.py MEMS_DUMP [...]: on-chip RAM area on GF180 5 V per memory, the
# cheapest of foundry SRAM macro tiles (64/128/256/512 x 8, one read port)
# and a register file, with measured per-bit costs (README "RAM cost
# correction"): flip-flops 97 + 21 per read port um2/bit, latches (DFFRAM
# style) 44 + 16 per read port. ROMs count as logic. Prints totals by group.
import re, sys

MAC = {64: 0.1006, 128: 0.1161, 256: 0.1472, 512: 0.2094}
STYLES = {'flops': (97, 21), 'latch': (44, 16)}
GROUPS = [('texturePath.dataMem', 'tex cache'), ('pciFifo', 'PCI FIFO'), ('paletteRam', 'palette'),
          ('hdmiScanout', 'video'), ('fbScanoutFetcher', 'video'), ('Reader', 'fb readers'),
          ('PlaneBuffer', 'fb write slots')]


def load(path):
    out = []
    for cell in re.split(r'\n\s*cell \$mem_v2 ', open(path).read())[1:]:
        name = cell.split('\n')[0].strip().lstrip('\\')
        def param(k):
            v = re.search(r'parameter \\' + k + r' (\S+)', cell).group(1)
            return int(v.split("'")[1], 2) if "'" in v else int(v)
        out.append((name, param('SIZE'), param('WIDTH'), param('WR_PORTS'), param('RD_PORTS')))
    return out


def cost(size, width, wr, rd, style):
    if wr == 0:
        return 0.0, 'rom'
    store, port = STYLES[style]
    best, how = size * width * (store + port * rd) * 1e-6, style
    if rd == 1:
        for depth, area in MAC.items():
            m = ((size + depth - 1) // depth) * ((width + 7) // 8) * area
            if m < best:
                best, how = m, 'macro%d' % depth
    return best, how


def group(name):
    for key, g in GROUPS:
        if key in name:
            return g
    return 'other FIFOs'


if __name__ == '__main__':
    for path in sys.argv[1:]:
        mems = load(path)
        bits = sum(s * w for _, s, w, wr, _ in mems if wr)
        for style in STYLES:
            tot = {}
            for name, s, w, wr, rd in mems:
                a, _ = cost(s, w, wr, rd, style)
                tot[group(name)] = tot.get(group(name), 0) + a
            print('%s %s: %.1f KiB, %.2f mm2 | %s' % (path, style, bits / 8192, sum(tot.values()),
                  ' | '.join('%s %.2f' % kv for kv in sorted(tot.items(), key=lambda x: -x[1]))))
