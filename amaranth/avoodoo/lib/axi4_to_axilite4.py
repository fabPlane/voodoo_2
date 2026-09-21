"""``Axi4ToAxiLite4`` -- 32-bit AXI4 to AXI4-Lite, one outstanding, single-beat.

Transliteration of ``fpga/src/voodoo/artix/Axi4ToAxiLite4.scala`` (135 lines),
plan Phase 1.5.  The 7-series AXI PCIe IP (``artix/AmdAc701.scala``) exposes
BAR0 as AXI4; host register access is 32-bit MMIO (``AWLEN``/``ARLEN`` = 0)
and extra beats are ignored.  The Gowin Console does not instantiate it (its
BAR arrives as AXI4-Lite from the TLP target already), but it is part of the
same boundary library.

Same registers, same names: ``state`` (``idle``/``write``/``read``), ``id``,
``addr``, ``wdata``, ``wstrb``, ``awSent``, ``wSent``, ``arSent`` -- all
``RegInit``, so every one of them resets.
"""

from __future__ import annotations

from amaranth import Module, Signal
from amaranth.lib import enum, wiring
from amaranth.lib.wiring import In, Out

from avoodoo.lib.axi import (RESP_DECERR, RESP_OKAY, RESP_SLVERR, Axi4Config,
                             Axi4Signature, AxiLite4Config, AxiLite4Signature)
from avoodoo.lib.stream import fire

__all__ = ["axi_config", "Axi4ToAxiLite4"]


def axi_config(address_width: int, id_width: int = 4) -> Axi4Config:
    """``Axi4ToAxiLite4.axiConfig(addressWidth, idWidth = 4)``.

    ``useLock``/``useQos``/``useRegion``/``useCache`` off, ``useProt``/
    ``useBurst``/``useLen``/``useSize`` on -- i.e. ``aw``/``ar`` carry
    ``addr, id, len, size, burst, prot``.
    """
    return Axi4Config(
        address_width=address_width,
        data_width=32,
        id_width=id_width,
        use_lock=False,
        use_qos=False,
        use_region=False,
        use_cache=False,
        use_prot=True,
        use_burst=True,
        use_len=True,
        use_size=True,
    )


class State(enum.Enum, shape=2):
    """``object State extends SpinalEnum { idle, write, read }``."""

    idle = 0
    write = 1
    read = 2


class Axi4ToAxiLite4(wiring.Component):
    """``case class Axi4ToAxiLite4(addressWidth, idWidth = 4)``.

    ``axi`` is the AXI4 slave port, ``lite`` the AXI4-Lite master.  A write
    is accepted only once both ``AW`` and ``W`` are valid (and wins over a
    pending read); it then drives ``aw``/``w`` on the lite side until each
    has been taken, waits for ``b`` and echoes it (``SLVERR``/``DECERR`` both
    come back as ``SLVERR``).  Reads mirror that on ``ar``/``r``, always with
    ``last`` set.
    """

    def __init__(self, address_width: int, id_width: int = 4):
        self.address_width = address_width
        self.id_width = id_width
        self.axi_config = axi_config(address_width, id_width)
        self.lite_config = AxiLite4Config(address_width=address_width, data_width=32)
        super().__init__({
            "axi": In(Axi4Signature(self.axi_config)),
            "lite": Out(AxiLite4Signature(self.lite_config)),
        })

    def elaborate(self, platform):
        m = Module()
        axi, lite = self.axi, self.lite

        state = Signal(State, name="state")
        id_ = Signal(self.id_width, name="id")
        addr = Signal(self.address_width, name="addr")
        wdata = Signal(32, name="wdata")
        wstrb = Signal(4, name="wstrb")
        aw_sent = Signal(name="awSent")
        w_sent = Signal(name="wSent")
        ar_sent = Signal(name="arSent")
        # The two `resp` fields are assigned under `when`, which would make
        # yosys emit the packed payload port as a `reg` and every constant
        # slice of it (`r.last := True`) as an `always @*` block with an empty
        # sensitivity list -- which Verilator never evaluates.  Routing the
        # conditional fields through a wire keeps the port an `assign`.
        b_resp = Signal(2, name="bResp")
        r_resp = Signal(2, name="rResp")

        # ---- defaults ----
        m.d.comb += [
            axi.aw.ready.eq(0),
            axi.w.ready.eq(0),
            axi.b.valid.eq(0),
            axi.b.payload.id.eq(id_),
            axi.b.payload.resp.eq(b_resp),
            b_resp.eq(RESP_OKAY),
            axi.ar.ready.eq(0),
            axi.r.valid.eq(0),
            axi.r.payload.id.eq(id_),
            axi.r.payload.data.eq(lite.r.payload.data),
            axi.r.payload.resp.eq(r_resp),
            r_resp.eq(RESP_OKAY),
            axi.r.payload.last.eq(1),

            lite.aw.valid.eq(0),
            lite.aw.payload.addr.eq(addr),
            lite.aw.payload.prot.eq(0),
            lite.w.valid.eq(0),
            lite.w.payload.data.eq(wdata),
            lite.w.payload.strb.eq(wstrb),
            lite.b.ready.eq(0),
            lite.ar.valid.eq(0),
            lite.ar.payload.addr.eq(addr),
            lite.ar.payload.prot.eq(0),
            lite.r.ready.eq(0),
        ]

        lite_b_err = ((lite.b.payload.resp == RESP_SLVERR)
                      | (lite.b.payload.resp == RESP_DECERR))
        lite_r_err = ((lite.r.payload.resp == RESP_SLVERR)
                      | (lite.r.payload.resp == RESP_DECERR))

        with m.Switch(state):
            with m.Case(State.idle):
                m.d.sync += [aw_sent.eq(0), w_sent.eq(0), ar_sent.eq(0)]
                with m.If(axi.aw.valid & axi.w.valid):
                    m.d.sync += [
                        id_.eq(axi.aw.payload.id),
                        addr.eq(axi.aw.payload.addr),
                        wdata.eq(axi.w.payload.data),
                        wstrb.eq(axi.w.payload.strb),
                        state.eq(State.write),
                    ]
                    m.d.comb += [axi.aw.ready.eq(1), axi.w.ready.eq(1)]
                with m.Elif(axi.ar.valid):
                    m.d.sync += [
                        id_.eq(axi.ar.payload.id),
                        addr.eq(axi.ar.payload.addr),
                        state.eq(State.read),
                    ]
                    m.d.comb += axi.ar.ready.eq(1)
            with m.Case(State.write):
                m.d.comb += [
                    lite.aw.valid.eq(~aw_sent),
                    lite.w.valid.eq(~w_sent),
                    lite.b.ready.eq(1),
                ]
                with m.If(fire(lite.aw)):
                    m.d.sync += aw_sent.eq(1)
                with m.If(fire(lite.w)):
                    m.d.sync += w_sent.eq(1)
                with m.If(lite.b.valid):
                    m.d.comb += axi.b.valid.eq(1)
                    with m.If(lite_b_err):
                        m.d.comb += b_resp.eq(RESP_SLVERR)
                    with m.If(axi.b.ready):
                        m.d.sync += state.eq(State.idle)
            with m.Case(State.read):
                m.d.comb += [
                    lite.ar.valid.eq(~ar_sent),
                    lite.r.ready.eq(1),
                ]
                with m.If(fire(lite.ar)):
                    m.d.sync += ar_sent.eq(1)
                with m.If(lite.r.valid):
                    m.d.comb += axi.r.valid.eq(1)
                    with m.If(lite_r_err):
                        m.d.comb += r_resp.eq(RESP_SLVERR)
                    with m.If(axi.r.ready):
                        m.d.sync += state.eq(State.idle)
        return m
