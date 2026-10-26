# slicheck.py REF.bmp N OUT_k0.bmp [OUT_k1.bmp ...]: per SLI chip k, count
# mismatches vs REF on its own image rows (row % N == k, both bottom-up and
# top-down tested) and merge the chips' rows into one image for a full diff.
import sys
from PIL import Image
ref = Image.open(sys.argv[1]).convert('RGB'); n = int(sys.argv[2]); outs = sys.argv[3:]
w, h = ref.size; pr = ref.load()
for k, f in enumerate(outs):
    im = Image.open(f).convert('RGB'); pi = im.load()
    for name, own in (('top-down', lambda y: y % n == k), ('bottom-up', lambda y: (h - 1 - y) % n == k)):
        bad = sum(1 for y in range(h) if own(y) for x in range(w) if pi[x, y] != pr[x, y])
        rows = sum(1 for y in range(h) if own(y))
        print(f'chip {k}: {name:9s} rows {rows}: {bad} px differ')
