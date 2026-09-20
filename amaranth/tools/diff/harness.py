"""Spinal-vs-Amaranth lockstep differential harness (plan §4.2, task 0.4).

    python -m tools.diff.harness FpxxDiffWrap
    python -m tools.diff.harness StreamWhileDiffWrap --seeds 1,2,3 --cycles 1000000
    python -m tools.diff.harness --list

What it does, in order:

1. **Spinal side.** If ``fpga/rtl/diff/<Module>_spinal.v`` is missing or older
   than any Scala source under ``fpga/src`` or ``ext/SpinalVoodoo/src``, run
   ``scala-cli run . --main-class voodoo.diff.GenOne -- <Module> rtl/diff``.
2. **Amaranth side.** Elaborate the registry's factory and emit
   ``<build>/<Module>_am.v`` (top module ``<Module>_am``).
3. **Wrapper.** Pair the two port lists through the committed port map
   (``fpga/src/voodoo/diff/portmaps/<Module>.json``) plus the per-module
   override table, then emit an Amaranth top ``diff_top`` that instantiates
   both as ``Instance()``s, drives every input from one wrapper port, and
   exposes ``s_<port>`` / ``a_<port>`` for every output together with a
   per-output ``mm`` bit vector and the OR'd ``mismatch``.
4. **Driver.** Build with Verilator and run :mod:`tools.diff.driver` under
   cocotb: seeded constrained-random stimulus honouring the Spinal stream
   contract, ``mismatch == 0`` asserted every cycle.

On a mismatch the run stops, the failing seed and cycle are reported with the
names of the differing signals, and -- unless ``--no-waves`` -- the harness
re-runs *just that seed* up to the failing cycle with tracing on, which puts
an FST next to the build.  Re-running with tracing rather than tracing the
whole 1e6-cycle sweep is what keeps the dump a few megabytes instead of a few
hundred: the stimulus is a pure function of the seed, so the replay is exact.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

HERE = Path(__file__).resolve()
AM_ROOT = HERE.parent.parent.parent            # amaranth/
REPO = AM_ROOT.parent                          # repo root
FPGA = REPO / "fpga"
SPINAL_RTL = FPGA / "rtl" / "diff"
PORTMAPS = FPGA / "src" / "voodoo" / "diff" / "portmaps"
PORTS_PY = FPGA / "src" / "voodoo" / "diff" / "ports.py"
BUILD_ROOT = AM_ROOT / "build" / "diff"

sys.path.insert(0, str(AM_ROOT))

from tools.diff import vlog                      # noqa: E402
from tools.diff.naming import amaranth_port_name  # noqa: E402
from tools.diff.registry import DiffEntry, names, resolve  # noqa: E402

VERILATOR_ARGS = [
    "-Wno-fatal",
    "-Wno-WIDTHTRUNC",
    "-Wno-WIDTHEXPAND",
    "-Wno-CMPCONST",
    "-Wno-UNSIGNED",
    "-Wno-DECLFILENAME",
    # A zero-width Amaranth port (`stream.Signature(0)`, i.e. Spinal's
    # `Stream(NoData)`) is spelled `[-1:0]` by yosys and left unconnected by
    # the wrapper, since it carries no bits and Spinal emits no twin for it.
    "-Wno-ASCRANGE",
    "-Wno-PINMISSING",
    "--x-assign",
    "fast",
    "--x-initial",
    "fast",
]


# ---------------------------------------------------------------------------
# 1. Spinal side
# ---------------------------------------------------------------------------


def _scala_sources() -> list[Path]:
    roots = [FPGA / "src", REPO / "ext" / "SpinalVoodoo" / "src"]
    out: list[Path] = []
    for root in roots:
        if root.is_dir():
            out += list(root.rglob("*.scala"))
    for extra in (FPGA / "project.scala",):
        if extra.is_file():
            out.append(extra)
    return out


def ensure_spinal(module: str, *, force: bool = False, quiet: bool = False) -> Path:
    """Generate ``<module>_spinal.v`` if it is missing or stale."""
    target = SPINAL_RTL / f"{module}_spinal.v"
    if not force and target.is_file():
        newest = max((p.stat().st_mtime for p in _scala_sources()), default=0.0)
        if target.stat().st_mtime >= newest:
            return target
        reason = "stale"
    else:
        reason = "missing" if not target.is_file() else "forced"

    if shutil.which("scala-cli") is None:
        raise SystemExit(
            f"{target} is {reason} and scala-cli is not on PATH. Generate it on a "
            f"machine that has one:\n"
            f"  cd fpga && scala-cli run . --main-class voodoo.diff.GenOne -- {module} rtl/diff"
        )
    cmd = ["scala-cli", "run", ".", "--main-class", "voodoo.diff.GenOne", "--", module, "rtl/diff"]
    if not quiet:
        print(f"[diff] Spinal RTL {reason}; running: (cd fpga && {' '.join(cmd)})", flush=True)
    subprocess.run(cmd, cwd=FPGA, check=True,
                   stdout=subprocess.DEVNULL if quiet else None)
    if not target.is_file():
        raise SystemExit(f"GenOne did not produce {target}")
    # GenOne writes into rtl/diff but leaves the mtime at "now"; nothing else
    # to do -- the staleness check above will be satisfied next time.
    return target


def load_portmap(entry: DiffEntry, spinal_v: Path, build: Path) -> dict:
    """The committed port map, or a freshly generated one if it is absent/stale."""
    committed = PORTMAPS / f"{entry.portmap_stem}.json"
    spinal_ports = vlog.parse_ports(spinal_v)
    if committed.is_file():
        pm = json.loads(committed.read_text())
        if {p["name"] for p in pm["ports"]} == set(spinal_ports):
            return pm
        print(f"[diff] committed port map {committed.name} does not match "
              f"{spinal_v.name}; regenerating", flush=True)
    out = build / "portmap"
    subprocess.run([sys.executable, str(PORTS_PY), str(spinal_v), "-o", str(out)],
                   check=True, stdout=subprocess.DEVNULL)
    generated = out / f"{spinal_v.stem.removesuffix('_spinal')}.json"
    return json.loads(generated.read_text())


# ---------------------------------------------------------------------------
# 2. Amaranth side
# ---------------------------------------------------------------------------


def _factory(spec: str):
    import importlib

    mod_name, _, attr = spec.partition(":")
    if not attr:
        raise SystemExit(f"factory {spec!r} must be 'package.module:Attr'")
    return getattr(importlib.import_module(mod_name), attr)


def emit_amaranth(entry: DiffEntry, module: str, build: Path) -> tuple[Path, object]:
    """Elaborate the Amaranth side; return the Verilog path and the DUT object.

    The DUT itself is returned because the *signature* -- not the Verilog -- is
    what says where each bundle leaf sits inside a packed port
    (:mod:`tools.diff.amports`).
    """
    from amaranth.back import verilog
    from amaranth.lib import wiring

    dut = _factory(entry.factory)(**entry.params)
    ports = None
    if not isinstance(dut, wiring.Component):
        if not hasattr(dut, "ports"):
            raise SystemExit(f"{entry.factory} is not a wiring.Component and has no ports()")
        ports = dut.ports()
    text = verilog.convert(dut, name=f"{module}_am", ports=ports)
    build.mkdir(parents=True, exist_ok=True)
    path = build / f"{module}_am.v"
    path.write_text(text)
    return path, dut


# ---------------------------------------------------------------------------
# 3. Pairing + wrapper
# ---------------------------------------------------------------------------


@dataclass
class Pair:
    spinal: str
    amaranth: str        # fully-qualified Amaranth leaf path
    am_port: str         # the Verilog port that leaf lives in
    am_offset: int       # its bit offset inside that port
    width: int
    direction: str       # "input" | "output", as seen from the DUT
    role: str            # valid | ready | payload | none | clock | reset
    stream: str | None


def pair_ports(pm: dict, entry: DiffEntry, spinal_v: Path, am_v: Path,
               dut) -> tuple[list[Pair], dict[str, int]]:
    """Pair every Spinal port with an Amaranth signature leaf.

    Returns the pairs and the Amaranth *port* widths, which the wrapper needs
    because several Spinal ports can share one packed Amaranth port.
    """
    from tools.diff.amports import amaranth_fields

    spinal_ports = vlog.parse_ports(spinal_v)
    am_ports = vlog.parse_ports(am_v)
    fields = amaranth_fields(dut)

    pairs: list[Pair] = []
    problems: list[str] = []
    used: set[str] = set()

    for p in pm["ports"]:
        name = p["name"]
        if name not in spinal_ports:
            problems.append(f"port map lists {name!r}, which is not in {spinal_v.name}")
            continue
        want = amaranth_port_name(p["path"], spinal_name=name, overrides=entry.overrides)
        if p["role"] in ("clock", "reset"):
            # Driven by the wrapper's own sync domain; there is nothing to pair.
            continue
        field = fields.get(want)
        if field is None:
            problems.append(
                f"{name!r} -> {want!r}: no such leaf in the Amaranth signature. Add an "
                f"override to tools/diff/registry.py, or fix the signature. "
                f"Candidates: {_closest(want, fields)}"
            )
            continue
        if field.port not in am_ports:
            problems.append(f"{want!r} claims port {field.port!r}, absent from {am_v.name}")
            continue
        sp = spinal_ports[name]
        if sp.width != field.width:
            problems.append(f"{name!r} is {sp.width} bits, {want!r} is {field.width} bits")
            continue
        if sp.direction != am_ports[field.port].direction:
            problems.append(
                f"{name!r} is {sp.direction}, {field.port!r} is "
                f"{am_ports[field.port].direction}"
            )
            continue
        used.add(want)
        pairs.append(Pair(name, want, field.port, field.offset, sp.width,
                          sp.direction, p["role"], p["stream"]))

    unpaired = sorted(set(fields) - used)
    if unpaired:
        problems.append(f"Amaranth signature leaves with no Spinal twin: {unpaired}")

    # Every bit of every Amaranth port must be accounted for, or the wrapper
    # would leave part of an input floating.
    covered: dict[str, int] = {}
    for pair in pairs:
        covered[pair.am_port] = covered.get(pair.am_port, 0) + pair.width
    for port, width in covered.items():
        if width != am_ports[port].width:
            problems.append(
                f"port {port!r} is {am_ports[port].width} bits but its paired leaves "
                f"cover {width}"
            )

    if problems:
        raise SystemExit("[diff] port pairing failed:\n  " + "\n  ".join(problems))
    return pairs, {n: p.width for n, p in am_ports.items()}


def _closest(want: str, have) -> list[str]:
    import difflib

    return difflib.get_close_matches(want, list(have), n=3, cutoff=0.4)


def emit_wrapper(module: str, pairs: list[Pair], am_port_widths: dict[str, int],
                 build: Path, *, has_clk: bool, has_rst: bool) -> tuple[Path, dict]:
    """Emit ``diff_top.v`` and the spec JSON the cocotb driver reads."""
    from amaranth import ClockSignal, Elaboratable, Instance, Module, ResetSignal, Signal
    from amaranth.back import verilog

    inputs = [p for p in pairs if p.direction == "input"]
    outputs = [p for p in pairs if p.direction == "output"]
    if not outputs:
        raise SystemExit(f"[diff] {module} has no outputs to compare")

    # Amaranth only emits clk/rst when the module actually has a `sync` domain.
    am_has_clk = "clk" in am_port_widths
    am_has_rst = "rst" in am_port_widths
    if has_clk != am_has_clk or has_rst != am_has_rst:
        print(f"[diff] note: Spinal clk/reset={has_clk}/{has_rst}, "
              f"Amaranth clk/rst={am_has_clk}/{am_has_rst} — one side is "
              f"combinational where the other is not, which is itself suspicious "
              f"unless the module really has no state.", flush=True)

    # One signal per Amaranth *port*; several Spinal leaves may slice into it.
    am_ports_used = sorted({p.am_port for p in pairs})

    class DiffTop(Elaboratable):
        def __init__(self):
            self.in_sigs = {p.spinal: Signal(p.width, name=f"in_{p.spinal}") for p in inputs}
            self.s_sigs = {p.spinal: Signal(p.width, name=f"s_{p.spinal}") for p in outputs}
            self.a_sigs = {p.spinal: Signal(p.width, name=f"a_{p.spinal}") for p in outputs}
            self.am_sigs = {
                n: Signal(am_port_widths[n], name=f"am_{n}") for n in am_ports_used
            }
            self.mm = Signal(len(outputs), name="mm")
            self.mismatch = Signal(name="mismatch")
            self.mismatch_sticky = Signal(name="mismatch_sticky")

        def elaborate(self, platform):
            m = Module()
            sp_conn: dict = {}
            am_conn = {}
            for name, sig in self.am_sigs.items():
                # Direction is decided by the pairs that use this port.
                any_pair = next(p for p in pairs if p.am_port == name)
                am_conn[("i_" if any_pair.direction == "input" else "o_") + name] = sig

            for p in inputs:
                sig = self.in_sigs[p.spinal]
                sp_conn[f"i_{p.spinal}"] = sig
                if p.am_port == p.amaranth:
                    m.d.comb += self.am_sigs[p.am_port].eq(sig)
                else:
                    m.d.comb += self.am_sigs[p.am_port][
                        p.am_offset : p.am_offset + p.width].eq(sig)
            for p in outputs:
                sp_conn[f"o_{p.spinal}"] = self.s_sigs[p.spinal]
                m.d.comb += self.a_sigs[p.spinal].eq(
                    self.am_sigs[p.am_port][p.am_offset : p.am_offset + p.width]
                )
            if has_clk:
                sp_conn["i_clk"] = ClockSignal()
            if has_rst:
                # Spinal SYNC/HIGH `reset` and Amaranth's synchronous `rst` are
                # the same contract (spinal-naming-and-harness.md §0).
                sp_conn["i_reset"] = ResetSignal()
            if am_has_clk:
                am_conn["i_clk"] = ClockSignal()
            if am_has_rst:
                am_conn["i_rst"] = ResetSignal()

            m.submodules.spinal = Instance(f"{module}_spinal", **sp_conn)
            m.submodules.am = Instance(f"{module}_am", **am_conn)

            for i, p in enumerate(outputs):
                m.d.comb += self.mm[i].eq(self.s_sigs[p.spinal] != self.a_sigs[p.spinal])
            m.d.comb += self.mismatch.eq(self.mm.any())
            # A registered flag, which also guarantees the wrapper has clk/rst
            # ports even when the DUT pair is purely combinational.
            m.d.sync += self.mismatch_sticky.eq(self.mismatch_sticky | self.mismatch)
            return m

    top = DiffTop()
    port_list = (
        list(top.in_sigs.values())
        + list(top.s_sigs.values())
        + list(top.a_sigs.values())
        + [top.mm, top.mismatch, top.mismatch_sticky]
    )
    text = verilog.convert(top, name="diff_top", ports=port_list)
    path = build / "diff_top.v"
    path.write_text(text)

    spec = {
        "module": module,
        "has_clock": has_clk,
        "has_reset": has_rst,
        "inputs": [asdict(p) for p in inputs],
        "outputs": [asdict(p) for p in outputs],
    }
    (build / "diff_spec.json").write_text(json.dumps(spec, indent=2) + "\n")
    return path, spec


# ---------------------------------------------------------------------------
# 4. Run
# ---------------------------------------------------------------------------


def run_sim(module: str, build: Path, sources: list[Path], *, seeds: list[int],
            cycles: int, valid_density: float, ready_density: float,
            reset_cycles: int, waves: bool, verbose: bool) -> dict:
    from cocotb_tools.runner import get_runner

    runner = get_runner("verilator")
    runner.build(
        sources=[str(s) for s in sources],
        hdl_toplevel="diff_top",
        build_dir=str(build / ("sim-waves" if waves else "sim")),
        build_args=VERILATOR_ARGS,
        waves=waves,
        always=True,
        clean=False,
    )
    report = build / ("report-waves.json" if waves else "report.json")
    if report.exists():
        report.unlink()
    env = {
        "DIFF_SPEC": str(build / "diff_spec.json"),
        "DIFF_REPORT": str(report),
        "DIFF_SEEDS": ",".join(str(s) for s in seeds),
        "DIFF_CYCLES": str(cycles),
        "DIFF_VALID_DENSITY": str(valid_density),
        "DIFF_READY_DENSITY": str(ready_density),
        "DIFF_RESET_CYCLES": str(reset_cycles),
        "DIFF_VERBOSE": "1" if verbose else "0",
        "PYTHONPATH": str(AM_ROOT) + os.pathsep + os.environ.get("PYTHONPATH", ""),
    }
    failed = False
    try:
        runner.test(
            hdl_toplevel="diff_top",
            test_module="tools.diff.driver",
            build_dir=str(build / ("sim-waves" if waves else "sim")),
            # Run from the build dir, not from amaranth/: cocotb drops
            # `<test>.result.xml` and (with waves) `dump.vcd` into `test_dir`,
            # and `tools.diff.driver` is importable via PYTHONPATH below.
            test_dir=str(build / ("sim-waves" if waves else "sim")),
            results_xml=str(build / ("results-waves.xml" if waves else "results.xml")),
            waves=waves,
            extra_env=env,
        )
    except SystemExit:
        failed = True
    except Exception as exc:  # cocotb surfaces sim failures as exceptions too
        print(f"[diff] cocotb reported: {exc}", flush=True)
        failed = True

    if report.exists():
        return json.loads(report.read_text())
    return {"ok": not failed, "reason": "driver wrote no report"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("module", nargs="?", help="registry name, e.g. FpxxDiffWrap")
    ap.add_argument("--list", action="store_true", help="list diffable modules")
    ap.add_argument("--seeds", default="1,2,3",
                    help="comma-separated stimulus seeds (default 1,2,3)")
    ap.add_argument("--cycles", type=int, default=20_000,
                    help="cycles per seed (default 20000; the plan wants >=1e6 for done)")
    ap.add_argument("--valid-density", type=float, default=0.6,
                    help="probability an input stream asserts valid (default 0.6)")
    ap.add_argument("--ready-density", type=float, default=0.6,
                    help="probability an output stream asserts ready (default 0.6)")
    ap.add_argument("--reset-cycles", type=int, default=8,
                    help="cycles of reset before each seed (default 8)")
    ap.add_argument("--no-waves", action="store_true",
                    help="do not re-run a failing seed with FST tracing")
    ap.add_argument("--force-gen", action="store_true", help="re-run GenOne unconditionally")
    ap.add_argument("--build-dir", type=Path, default=None)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    if args.list or not args.module:
        from tools.diff.registry import REGISTRY

        for name in names():
            e = REGISTRY[name]
            mark = "!" if e.pending else " "
            print(f"{mark} {name:22s} {e.note}")
        print("\n! = Amaranth side not written yet; see tools/diff/registry.py")
        return 0 if args.list else 2

    module = args.module
    entry = resolve(module)
    if entry.pending:
        print(f"[diff] {module}: {entry.pending}", file=sys.stderr)
        return 3

    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
    build = args.build_dir or (BUILD_ROOT / module)
    build.mkdir(parents=True, exist_ok=True)

    spinal_v = ensure_spinal(entry.spinal, force=args.force_gen)
    am_v, dut = emit_amaranth(entry, module, build)
    pm = load_portmap(entry, spinal_v, build)
    pairs, am_port_widths = pair_ports(pm, entry, spinal_v, am_v, dut)
    spinal_ports = vlog.parse_ports(spinal_v)
    wrapper_v, spec = emit_wrapper(
        module, pairs, am_port_widths, build,
        has_clk="clk" in spinal_ports, has_rst="reset" in spinal_ports,
    )

    print(f"[diff] {module}: {len(pairs)} ports paired "
          f"({len(spec['inputs'])} in, {len(spec['outputs'])} out); "
          f"{len(seeds)} seed(s) x {args.cycles} cycles", flush=True)

    result = run_sim(
        module, build, [spinal_v, am_v, wrapper_v],
        seeds=seeds, cycles=args.cycles,
        valid_density=args.valid_density, ready_density=args.ready_density,
        reset_cycles=args.reset_cycles, waves=False, verbose=args.verbose,
    )

    if result.get("ok"):
        print(f"[diff] {module}: PASS — {len(seeds)} seed(s) x {args.cycles} cycles, "
              f"no mismatch", flush=True)
        return 0

    seed = result.get("seed")
    cycle = result.get("cycle")
    print(f"[diff] {module}: FAIL at seed {seed}, cycle {cycle}", file=sys.stderr)
    for d in result.get("differences", []):
        print(f"[diff]   {d['port']}: spinal={d['spinal']:#x} amaranth={d['amaranth']:#x}",
              file=sys.stderr)

    if not args.no_waves and seed is not None and cycle is not None:
        print(f"[diff] replaying seed {seed} with tracing to cycle {cycle}", file=sys.stderr)
        run_sim(module, build, [spinal_v, am_v, wrapper_v],
                seeds=[seed], cycles=cycle + 1,
                valid_density=args.valid_density, ready_density=args.ready_density,
                reset_cycles=args.reset_cycles, waves=True, verbose=args.verbose)
        for fst in sorted((build / "sim-waves").rglob("*.fst")):
            print(f"[diff]   wave: {fst}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
