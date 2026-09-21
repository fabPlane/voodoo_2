"""Run one ``trace_test`` replay of the Amaranth ``CoreSim`` and keep its log.

    python -m tools.trace_check test04
    python -m tools.trace_check tomb                # traces/tomb/{trace,state}.bin
    python -m tools.trace_check quake --stop-on-stall
    python -m tools.trace_check screamer2 -- --max-mismatches 500

This is the run half of ``make sim-trace-check TEST=<name>`` (plan Phase 7.3;
the build half is ``make sim-trace-test``).  It resolves ``<name>`` the way
upstream's ``native/sim/check/%`` does (``traces/<name>.bin`` for the Glide
tests, ``traces/<name>/`` for the game frames that come with a ``state.bin``),
runs ``emu/test/obj_dir/trace_test`` from the submodule root so those relative
paths work, and writes ``test-output/<name>/`` -- the four files ``trace_test``
produces plus ``run.log``, which :mod:`tests.test_parity` reads for the
``Shutdown after N cycles`` line (``trace_test`` only records cycles in the
profile JSON for the DE10 interface).

``trace_test`` exits 1 whenever the RTL frame differs from the 86Box model's,
which the *Spinal* core does on every trace we have, so that status is
recorded (``[trace_check] trace_test exit=N``) rather than propagated; the
gate is ``tests/test_parity.py``, which compares with the Spinal core's own
result.  The exit status of this script is about the run itself: 0 when
``trace_test`` ran to its own end (or was stopped by ``--stop-on-stall``),
2 for a missing trace or binary.

``--stop-on-stall`` is for the traces on which the Spinal ``CoreSim`` itself
deadlocks (quake, screamer2, valley_of_ra, tower, test20 -- the plan's Phase
7.3 entry says why).  ``sim_stalled()`` in the upstream harness always
returns 0, so after the first bus write times out ``trace_test`` keeps going
and every remaining entry burns two more timeouts -- 40k entries at the
default 5 M cycles each is never going to finish.  With the flag the harness
timeout is ``--mmio-timeout`` cycles (1 M: no legitimate write in any trace
waits that long, whereas 100 k produced false stalls on tomb and screamer2),
``SIM_DEBUG_FB_ADDR`` is set so the harness prints its ``cpuReady/
frontdoorReady/pciFree/syncDrained/pipeBusy`` dump, and the process is
killed as soon as the first ``cmd timeout`` line appears.  The stall cycle
(``@ cycle N`` minus the timeout) is what the parity test compares.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

AMARANTH = Path(__file__).resolve().parents[1]
UPSTREAM = AMARANTH.parent / "ext" / "SpinalVoodoo"
TRACE_TEST = AMARANTH / "emu" / "test" / "obj_dir" / "trace_test"
OUTPUT = AMARANTH / "test-output"

SUMMARY_RE = re.compile(
    r"^\[trace_test\] (Replay |\d+/\d+ pixels|Artifact|Sim swap)"
    r"|^\[sim_harness\] (ERROR|WARNING|Shutdown|write-cmd-timeout addr)"
)
STALL_RE = re.compile(r"cmd timeout after (\d+) cycles @ cycle (\d+)")


def trace_source(name: str) -> str | None:
    if (UPSTREAM / "traces" / f"{name}.bin").is_file():
        return f"traces/{name}.bin"
    if (UPSTREAM / "traces" / name / "trace.bin").is_file():
        return f"traces/{name}/"
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("name", help="trace name: traces/<name>.bin or traces/<name>/")
    ap.add_argument("--stop-on-stall", action="store_true",
                    help="kill trace_test at the first bus-write timeout")
    ap.add_argument("--mmio-timeout", type=int, default=1_000_000,
                    help="SIM_MMIO_TIMEOUT_CYCLES with --stop-on-stall (default 1M)")
    ap.add_argument("--output-dir", type=Path, default=None,
                    help=f"default {OUTPUT}/<name>")
    ap.add_argument("--trace-test", type=Path, default=TRACE_TEST)
    ap.add_argument("extra", nargs="*", help="passed to trace_test after `--`")
    args = ap.parse_args(argv)

    src = trace_source(args.name)
    if src is None:
        print(f"[trace_check] no traces/{args.name}.bin or traces/{args.name}/trace.bin "
              f"under {UPSTREAM} (git lfs)", file=sys.stderr)
        return 2
    if not args.trace_test.is_file():
        print(f"[trace_check] {args.trace_test} missing: run `make sim-trace-test`",
              file=sys.stderr)
        return 2
    out = args.output_dir or OUTPUT / args.name
    out.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    if args.stop_on_stall:
        env.setdefault("SIM_MMIO_TIMEOUT_CYCLES", str(args.mmio_timeout))
        env.setdefault("SIM_DEBUG_FB_ADDR", "0")
    cmd = [str(args.trace_test), src, "--output-dir", str(out), *args.extra]
    print(f"[trace_check] cd {UPSTREAM} && "
          + " ".join(f"{k}={env[k]}" for k in ("SIM_MMIO_TIMEOUT_CYCLES",) if k in env)
          + " " + " ".join(cmd), file=sys.stderr)

    log_path = out / "run.log"
    started = time.monotonic()
    stalled = None
    with open(log_path, "w") as log:
        proc = subprocess.Popen(cmd, cwd=UPSTREAM, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        assert proc.stdout is not None
        for line in proc.stdout:
            log.write(line)
            if SUMMARY_RE.match(line):
                print(line, end="", file=sys.stderr)
            m = STALL_RE.search(line)
            if m and args.stop_on_stall and stalled is None:
                stalled = int(m.group(2)) - int(m.group(1))
                proc.kill()
        rc = proc.wait()
        wall = time.monotonic() - started
        tail = (f"[trace_check] stopped at first stall: cycle {stalled}\n" if stalled is not None
                else f"[trace_check] trace_test exit={rc}\n")
        tail += f"[trace_check] wall={wall:.0f}s\n"
        log.write(tail)
    print(tail, end="", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
