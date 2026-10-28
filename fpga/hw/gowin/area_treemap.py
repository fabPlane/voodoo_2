#!/usr/bin/env python3
"""area_treemap.py: FPGA area treemap of the dock build.

Reads a Gowin synthesis hierarchy report (<build>_syn_rsc.xml, saved per build
by run_detached.sh in ~/.cache/gowin-builds/reports/<tag>/) and the Verilog it
was synthesised from, and writes one self-contained HTML page: a zoomable
treemap of LUT / ALU / FF / BSRAM / DSP per instance, each instance's Spinal
module and Scala source, and its largest RTL flops and memories (from the
Verilog: signals assigned with <=; memories are arrays - the GW5AST has no
LUT-RAM, so a small array is flops too).

  area_treemap.py <syn_rsc.xml> <out.html> [--verilog TangMega138kPro.v ...]
                  [--json out.json] [--title "..."]

Without --verilog the Spinal Verilog next to the report's rtl dir is not
guessed: pass it (rtl_bNNN/TangMega138kPro.v) plus the hand-written board
Verilog (fpga/hw/gowin/src, own_ddr3/rtl) to get modules and flops.
"""
import argparse, glob, html, json, os, re, sys, xml.etree.ElementTree as ET

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
SCALA_ROOTS = [os.path.join(REPO, 'fpga', 'src'), os.path.join(REPO, 'ext', 'SpinalVoodoo', 'src')]
DSP_KEYS = ['MULTALU27X18', 'MULT12X12', 'MULT27X36', 'MULTADDALU12X12', 'MULTALU18X18', 'MULT18X18', 'MULT9X9']


def num(v):
    return int(v.split('(')[0]) if v else 0


# ---------------------------------------------------------------- Verilog
DECL = re.compile(r'^\s*(?:output\s+|input\s+)?(?:reg|wire|logic)\s+(?:signed\s+)?(?:\[([^:\]]+):([^\]]+)\]\s*)?(.*?);')
PARAM = re.compile(r'\b(?:parameter|localparam)\s+(?:\[[^\]]*\]\s*)?(\w+)\s*=\s*([^,;]+)')
NBA = re.compile(r'(?<![\w.])(\w+)\s*(\[[^\]=]*\])?\s*<=(?!=)')
INST = re.compile(r'^\s*(\w+)\s+(?:#\s*\(.*?\)\s*)?(\w+)\s*\(\s*(?:$|\.)')
INST_P = re.compile(r'^\s*(\w+)\s+#\s*\(\s*$')
KEYWORDS = {'module', 'input', 'output', 'inout', 'wire', 'reg', 'assign', 'always', 'if', 'else', 'case',
            'begin', 'end', 'initial', 'function', 'task', 'localparam', 'parameter', 'genvar', 'for', 'generate',
            'integer', 'logic', 'default', 'endcase', 'posedge', 'negedge', 'or', 'and', 'not'}


def ev(expr, params):
    """Evaluate a Verilog constant expression with the module's parameters."""
    e = re.sub(r"\d+'[sS]?[dD](\d+)", r'\1', expr.strip())
    e = re.sub(r"\d+'[sS]?[hH]([0-9a-fA-F_]+)", lambda m: str(int(m.group(1).replace('_', ''), 16)), e)
    e = re.sub(r"\d+'[sS]?[bB]([01_]+)", lambda m: str(int(m.group(1).replace('_', ''), 2)), e)
    e = re.sub(r'\$clog2', 'clog2', e)
    e = re.sub(r'\b([A-Za-z_]\w*)\b', lambda m: str(params[m.group(1)]) if m.group(1) in params else m.group(1), e)
    if not re.fullmatch(r'[\d\s+\-*/()<>clog2]*', e):
        return None
    try:
        return int(eval(e.replace('/', '//'), {'clog2': lambda x: max(0, (int(x) - 1).bit_length())}))
    except Exception:
        return None


def parse_verilog(paths, defines=()):
    mods = {}
    for p in paths:
        cur = None
        pending_param_type = None
        with open(p, errors='replace') as f:
            lines = f.read().split('\n')
        active = [True]  # `ifdef stack: is this line compiled
        for ln in lines:
            s = ln.split('//')[0]
            m = re.match(r'^\s*`(ifdef|ifndef|else|endif)\b\s*(\w*)', s)
            if m:
                k = m.group(1)
                if k in ('ifdef', 'ifndef'):
                    on = (m.group(2) in defines) == (k == 'ifdef')
                    active.append(active[-1] and on)
                elif k == 'else' and len(active) > 1:
                    parent = active[-2]
                    active[-1] = parent and not active[-1]
                elif k == 'endif' and len(active) > 1:
                    active.pop()
                continue
            if not active[-1]:
                continue
            m = re.match(r'^\s*module\s+(\w+)', s)
            if m:
                cur = mods.setdefault(m.group(1), {'file': p, 'insts': {}, 'decl': {}, 'ffs': set(), 'mems': set(), 'params': {}})
                pending_param_type = None
                continue
            if cur is None:
                continue
            if re.match(r'^\s*endmodule', s):
                cur = None
                continue
            for pm in PARAM.finditer(s):
                v = ev(pm.group(2), cur['params'])
                if v is not None:
                    cur['params'][pm.group(1)] = v
            d = DECL.match(s)
            if d:
                if d.group(1) is not None:
                    hi, lo = ev(d.group(1), cur['params']), ev(d.group(2), cur['params'])
                    w = abs(hi - lo) + 1 if hi is not None and lo is not None else 1
                else:
                    w = 1
                for part in d.group(3).split(','):
                    nm = re.match(r'\s*(\w+)\s*(?:\[([^:\]]+):([^\]]+)\])?', part)
                    if not nm or nm.group(1) in KEYWORDS:
                        continue
                    depth = 0
                    if nm.group(2):
                        a_, b_ = ev(nm.group(2), cur['params']), ev(nm.group(3), cur['params'])
                        depth = abs(a_ - b_) + 1 if a_ is not None and b_ is not None else 2
                    cur['decl'][nm.group(1)] = (w, depth)
                continue
            if pending_param_type:
                m = re.match(r'^\s*\)\s*(\w+)\s*\(', s)
                if m:
                    cur['insts'][m.group(1)] = pending_param_type
                    pending_param_type = None
                    continue
            m = INST_P.match(s)
            if m and m.group(1) not in KEYWORDS:
                pending_param_type = m.group(1)
                continue
            m = INST.match(s)
            if m and m.group(1) not in KEYWORDS and m.group(2) not in KEYWORDS:
                cur['insts'][m.group(2)] = m.group(1)
            for n in NBA.finditer(s):
                cur['ffs'].add(n.group(1))
    out = {}
    for name, m in mods.items():
        ffs, mems = [], []
        for sig in m['ffs']:
            w, depth = m['decl'].get(sig, (1, 0))
            (mems if depth else ffs).append((sig, w, depth))
        # Memories that are read but never written with <= (ROMs, written in loops)
        for sig, (w, depth) in m['decl'].items():
            if depth and sig not in m['ffs']:
                mems.append((sig, w, depth))
        ffs.sort(key=lambda x: -x[1])
        mems.sort(key=lambda x: -(x[1] * x[2]))
        out[name] = {'file': m['file'], 'insts': {k: v for k, v in m['insts'].items() if k},
                     'ffs': ffs, 'mems': mems}
    # drop instance entries whose type is not a known module (false matches)
    for m in out.values():
        m['insts'] = {k: v for k, v in m['insts'].items() if v in out or v.startswith(('Gowin', 'PLL', 'SerDes', 'PCIE', 'DQS', 'DDR', 'OSER', 'IDES', 'IODELAY', 'CLKDIV', 'DHCE'))}
    return out


# ---------------------------------------------------------------- Scala
def scala_index():
    idx = {}
    pat = re.compile(r'^\s*(?:(?:final|abstract|private|sealed)\s+)*(?:case\s+)?class\s+(\w+)', re.M)
    for root in SCALA_ROOTS:
        for p in glob.glob(os.path.join(root, '**', '*.scala'), recursive=True):
            try:
                txt = open(p, errors='replace').read()
            except OSError:
                continue
            for m in pat.finditer(txt):
                line = txt.count('\n', 0, m.start()) + 1
                idx.setdefault(m.group(1), (os.path.relpath(p, REPO), line))
    return idx


SPINAL_LIB = {'StreamFifo', 'StreamFifoCC', 'BufferCC', 'StreamFifoLowLatency', 'PulseCCByToggle', 'StreamCCByToggle',
              'FlowCCByToggle', 'StreamArbiter', 'StreamFork', 'StreamDemux', 'StreamMux', 'BmbArbiter', 'BmbDecoder',
              'BmbUnburstify', 'BmbToAxi4SharedBridge', 'Axi4SharedToAxi4', 'StreamFifoMultiChannelSharedSpace',
              'StreamJoin', 'BmbCcFifo', 'BmbAligner', 'BmbDownSizerBridge', 'BmbUpSizerBridge', 'Axi4ReadOnlyArbiter',
              'Axi4WriteOnlyArbiter', 'Axi4SharedArbiter', 'BmbSyncRemover', 'BmbLengthSplitter', 'BmbInvalidateMonitor'}


def source_of(mtype, vmods, sidx):
    if mtype is None:
        return None, None
    base = re.sub(r'_\d+$', '', mtype)
    if base in sidx:
        return base, '%s:%d' % sidx[base]
    if base in SPINAL_LIB:
        return base, 'SpinalHDL lib'
    vm = vmods.get(mtype)
    if vm and not vm['file'].endswith('TangMega138kPro.v'):
        return mtype, os.path.relpath(vm['file'], REPO)
    if vm:
        return base, 'Spinal (generated; class not found in repo)'
    return mtype, 'Gowin primitive / IP'


# ---------------------------------------------------------------- tree
MIN_CELLS = 40


def build_tree(xml_path, vmods, sidx):
    root = ET.parse(xml_path).getroot()

    def res(e, total):
        g = (lambda k: num(e.get('T_' + k))) if total else (lambda k: int(e.get(k, '0').split('(')[0]))
        return {'lut': g('Lut'), 'alu': g('Alu'), 'ff': g('Register'), 'bsram': g('Bsram'),
                'dsp': sum(g(k) for k in DSP_KEYS)}

    def walk(e, mtype, path):
        name = e.get('name')
        cls, src = source_of(mtype, vmods, sidx)
        vm = vmods.get(mtype) if mtype else None
        node = {'name': name, 'module': mtype, 'cls': cls, 'src': src,
                'self': res(e, False), 'total': res(e, True), 'children': []}
        if vm:
            node['ffs'] = [[s, w] for s, w, _ in vm['ffs'][:12]]
            node['ffBits'] = sum(w for _, w, _ in vm['ffs'])
            node['mems'] = [[s, w, d] for s, w, d in vm['mems'][:6]]
        for c in e:
            ctype = vm['insts'].get(c.get('name')) if vm else None
            node['children'].append(walk(c, ctype, path + [name]))
        # Fold tiny blocks (synchronisers, small stream stages) into the parent's own logic.
        keep = []
        for c in node['children']:
            t = c['total']
            if t['lut'] + t['alu'] + t['ff'] < MIN_CELLS and not t['bsram'] and not t['dsp']:
                for k in node['self']:
                    node['self'][k] += t[k]
            else:
                keep.append(c)
        node['children'] = keep
        if not keep:
            node['self'] = dict(node['total'])
        node['children'].sort(key=lambda n: -(n['total']['lut'] + n['total']['alu'] + n['total']['ff']))
        return node

    return walk(root, root.get('name'), [])


def dump(node, depth=0, minv=800):
    t = node['total']
    v = t['lut'] + t['alu'] + t['ff']
    if v < minv and t['bsram'] < 4 and depth:
        return
    print('%s%-34s %-28s LUT%6d ALU%6d FF%6d BS%4d DSP%3d  %s' % (
        '  ' * depth, node['name'][:34], (node['module'] or '?')[:28], t['lut'], t['alu'], t['ff'], t['bsram'], t['dsp'], node['src'] or ''))
    for c in node['children']:
        dump(c, depth + 1, minv)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('xml')
    ap.add_argument('out', nargs='?')
    ap.add_argument('--verilog', nargs='*', default=[])
    ap.add_argument('--define', nargs='*', default=['USE_OWN_DDR3'], help='Verilog `defines (board_top)')
    ap.add_argument('--json')
    ap.add_argument('--title', default='Dock FPGA area')
    ap.add_argument('--subtitle', default='')
    ap.add_argument('--pnr', help='PnR .rpt.txt for chip totals')
    ap.add_argument('--notes', help='JSON list of {title, est, detail, paths: ["board_top/u_voodoo/..."]}')
    ap.add_argument('--foot', default='')
    ap.add_argument('--dump', type=int, help='print the tree (min LUT+ALU+FF) and exit')
    a = ap.parse_args()
    vfiles = []
    for v in a.verilog:
        vfiles += sorted(glob.glob(v)) if any(ch in v for ch in '*?') else [v]
    vmods = parse_verilog(vfiles, set(a.define))
    tree = build_tree(a.xml, vmods, scala_index())
    if a.dump is not None:
        dump(tree, 0, a.dump)
        return
    chip = {}
    if a.pnr:
        txt = open(a.pnr, errors='replace').read()
        for key, pat in [('logic', r'Logic\s*\|\s*(\d+)/(\d+)'), ('ff', r'Register\s*\|\s*(\d+)/(\d+)'),
                         ('cls', r'CLS\s*\|\s*(\d+)/(\d+)'), ('bsram', r'BSRAM\s*\|\s*(\d+)/(\d+)'),
                         ('dsp', r'DSP\s*\|\s*(\d+)/(\d+)')]:
            m = re.search(pat, txt)
            if m:
                chip[key] = [int(m.group(1)), int(m.group(2))]
    # Source strings once: nodes carry an index into data['srcs'].
    srcs = []
    def intern(n):
        if n.get('src') is not None:
            if n['src'] not in srcs:
                srcs.append(n['src'])
            n['src'] = srcs.index(n['src'])
        for c in n['children']:
            intern(c)
    intern(tree)
    data = {'srcs': srcs, 'title': a.title, 'subtitle': a.subtitle, 'chip': chip, 'tree': tree, 'foot': a.foot,
            'notes': json.load(open(a.notes)) if a.notes else []}
    if a.json:
        json.dump(data, open(a.json, 'w'))
    if a.out:
        tpl = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'area_treemap.html')).read()
        page = tpl.replace('/*DATA*/null', json.dumps(data, separators=(',', ':'))).replace('{{TITLE}}', html.escape(a.title))
        open(a.out, 'w').write(page)
        print('wrote', a.out, len(page), 'bytes')


if __name__ == '__main__':
    main()
