"""End-to-end check that a zero-width payload does not break the harness.

SpinalHDL's ``NoData`` is an empty Bundle, so ``slave Stream(NoData)`` emits
``io_cmd_valid`` and ``io_cmd_ready`` and **no payload port**.  The natural
Amaranth spelling, ``stream.Signature(unsigned(0))``, keeps a zero-width
``payload`` member, and ``amaranth-yosys`` emits it as
``input [-1:0] cmd__payload``.

Three things therefore have to line up, and all three were wrong at once
before this test existed: ``vlog`` must read ``[-1:0]`` as **zero** bits (not
two), ``amports`` must not report a leaf that has no Spinal twin and no bits,
and the wrapper must leave that Instance pin unconnected without upsetting
Verilator.

Rather than depend on a real Spinal module, the reference here is a
hand-written ``*_spinal.v`` in exactly the shape SpinalHDL produces for this
interface, so the test runs anywhere Verilator does -- no ``scala-cli``, no
generated RTL.  ``ports.py`` turns it into a port map the same way it does
for the real thing.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from amaranth import Module, Signal
from amaranth.back import verilog
from amaranth.hdl import unsigned
from amaranth.lib import stream, wiring
from amaranth.lib.wiring import In, Out

AM_ROOT = Path(__file__).resolve().parents[2]
if str(AM_ROOT) not in sys.path:
    sys.path.insert(0, str(AM_ROOT))

from tools.diff import harness  # noqa: E402
from tools.diff.registry import DiffEntry  # noqa: E402

MODULE = "NoDataEcho"

# What SpinalHDL emits for `slave Stream(NoData)` plus a one-deep hold: valid
# and ready, no payload. Written out rather than generated so the test has no
# scala-cli dependency; SYNC / active-high reset, matching GenOne's default.
SPINAL_V = """\
// Generator : SpinalHDL v1.15.0 (hand-written stand-in, tests/diff/test_diff_nodata.py)
// Component : NoDataEcho_spinal

`timescale 1ns/1ps

module NoDataEcho_spinal (
  input  wire          io_cmd_valid,
  output wire          io_cmd_ready,
  input  wire          io_done,
  output wire          io_busy,
  input  wire          clk,
  input  wire          reset
);

  reg busy;

  assign io_cmd_ready = (! busy);
  assign io_busy = busy;

  always @(posedge clk) begin
    if(reset) begin
      busy <= 1'b0;
    end else begin
      if(busy) begin
        if(io_done) begin
          busy <= 1'b0;
        end
      end else begin
        busy <= io_cmd_valid;
      end
    end
  end

endmodule
"""


class NoDataEcho(wiring.Component):
    """The same thing in Amaranth, with a ``stream.Signature(0)`` command."""

    def __init__(self):
        super().__init__({
            "cmd": In(stream.Signature(unsigned(0))),
            "done": In(1),
            "busy": Out(1),
        })

    def elaborate(self, platform):
        m = Module()
        busy = Signal(name="busy")
        m.d.comb += [self.cmd.ready.eq(~busy), self.busy.eq(busy)]
        with m.If(busy):
            with m.If(self.done):
                m.d.sync += busy.eq(0)
        with m.Else():
            m.d.sync += busy.eq(self.cmd.valid)
        return m


def _build(tmp_path, **entry_kw):
    """Generate, pair and wrap the fixture; return what ``run_sim`` needs."""
    spinal_v = tmp_path / f"{MODULE}_spinal.v"
    spinal_v.write_text(SPINAL_V)

    dut = NoDataEcho()
    am_v = tmp_path / f"{MODULE}_am.v"
    am_v.write_text(verilog.convert(dut, name=f"{MODULE}_am"))

    subprocess.run(
        [sys.executable, str(harness.PORTS_PY), str(spinal_v), "-o", str(tmp_path / "pm")],
        check=True, stdout=subprocess.DEVNULL,
    )
    pm = json.loads((tmp_path / "pm" / f"{MODULE}.json").read_text())

    entry = DiffEntry(spinal=MODULE, factory=f"{__name__}:NoDataEcho",
                      note="test fixture", **entry_kw)
    pairs, am_widths = harness.pair_ports(pm, entry, spinal_v, am_v, dut)
    wrapper_v, spec = harness.emit_wrapper(
        MODULE, pairs, am_widths, tmp_path, has_clk=True, has_rst=True,
        pinned=entry.pinned, responder=entry.responder)
    return spinal_v, am_v, wrapper_v, pairs, spec


def _run(tmp_path, sources, **kw):
    opts = dict(seeds=[1, 2], cycles=2000, valid_density=0.6, ready_density=0.6,
                reset_cycles=8, waves=False, verbose=False)
    opts.update(kw)
    return harness.run_sim(MODULE, tmp_path, list(sources), **opts)


def test_zero_width_payload_pairs_and_diffs(tmp_path):
    if shutil.which("verilator") is None:
        pytest.skip("verilator not on PATH")

    spinal_v, am_v, wrapper_v, pairs, _spec = _build(tmp_path)

    # yosys really does emit the zero-width port, and vlog must read it as 0.
    assert harness.vlog.parse_ports(am_v)["cmd__payload"].width == 0
    # The Spinal side has no payload port, so nothing pairs with one -- and the
    # unpaired-leaf check in pair_ports() must not trip over it either.
    assert {p.spinal for p in pairs} == {"io_cmd_valid", "io_cmd_ready", "io_done", "io_busy"}
    # The wrapper must not try to drive the zero-width pin.
    assert "cmd__payload" not in wrapper_v.read_text()

    result = _run(tmp_path, (spinal_v, am_v, wrapper_v))
    assert result.get("ok"), result


def test_pinned_input_is_held(tmp_path):
    """``DiffEntry.pinned`` takes a port away from the generator."""
    if shutil.which("verilator") is None:
        pytest.skip("verilator not on PATH")

    spinal_v, am_v, wrapper_v, _pairs, spec = _build(tmp_path, pinned={"io_done": 1})
    assert spec["pinned"] == {"io_done": 1}
    result = _run(tmp_path, (spinal_v, am_v, wrapper_v))
    assert result.get("ok"), result


def test_pinned_port_must_be_an_input(tmp_path):
    with pytest.raises(SystemExit, match="io_busy"):
        _build(tmp_path, pinned={"io_busy": 0})


def test_responder_drives_a_claimed_input(tmp_path):
    """``DiffEntry.responder``: ``io_done`` follows ``io_busy`` through a model.

    ``AckWhen`` reads the Spinal-side ``io_busy`` on the falling edge and
    drives ``io_done`` for the next rising edge, which exercises the whole
    observe -> drive path through cocotb.
    """
    if shutil.which("verilator") is None:
        pytest.skip("verilator not on PATH")

    spinal_v, am_v, wrapper_v, _pairs, spec = _build(
        tmp_path, responder="tools.diff.responders:ack_when_busy")
    assert spec["responder"] == "tools.diff.responders:ack_when_busy"
    result = _run(tmp_path, (spinal_v, am_v, wrapper_v))
    assert result.get("ok"), result
    # and it really did drive: the report carries the responder's counters
    for seed in ("1", "2"):
        assert result["responder_stats"][seed]["AckWhen"]["acks"] > 0
