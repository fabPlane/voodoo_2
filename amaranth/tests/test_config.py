"""Checks on the Config/QFormat port.

The expected values here were produced by calling the real SpinalHDL 1.15.0
``spinal.core.QFormat`` from the JVM, so this test is a port-fidelity check,
not a restatement of the Python code.
"""

import math

from avoodoo.config import SQ, UQ, Config, QFormat, TraceConfig, Voodoo1


def test_qformat_derived_properties():
    q = SQ(16, 4)
    assert (q.width, q.fraction, q.signed) == (16, 4, True)
    assert q.non_fraction == 12
    assert q.amplify == 11
    assert q.numeric == 15
    assert q.capcity == 65536.0
    assert q.resolution == 0.0625
    assert q.min_value == -2048.0
    assert q.max_value == 2047.9375
    assert q.align_hex == 4
    assert q.align_oct == 6
    assert q.eps_in_double == math.ulp(2047.9375)
    assert str(q) == "SQ(16, 4)"


def test_qformat_unsigned():
    q = UQ(8, 0)
    assert q.non_fraction == 8
    assert q.amplify == 8
    assert q.numeric == 8
    assert q.min_value == 0.0
    assert q.max_value == 255.0
    assert q.align_hex == 2
    assert q.align_oct == 3
    assert str(q) == "UQ(8, 0)"


def test_qformat_wide_formats():
    assert SQ(32, 30).max_value == 1.9999999990686774
    assert SQ(32, 30).min_value == -2.0
    assert SQ(46, 30).non_fraction == 16
    assert SQ(46, 30).align_hex == 12
    assert SQ(46, 30).align_oct == 16


def test_qformat_algebra():
    assert SQ(16, 4) * SQ(24, 12) == SQ(40, 16)
    assert SQ(16, 4) + SQ(24, 12) == SQ(24, 12)
    assert SQ(16, 4) + SQ(16, 4) == SQ(16, 4)
    assert SQ(16, 4) + SQ(16, 8) == SQ(20, 8)
    assert SQ(16, 4) + UQ(8, 0) == SQ(16, 4)
    assert UQ(8, 0) + UQ(16, 4) == UQ(16, 4)
    assert UQ(8, 0) - UQ(16, 4) == SQ(17, 4)
    assert SQ(16, 4) * UQ(8, 0) == SQ(24, 4)
    assert UQ(8, 0) * UQ(16, 4) == UQ(24, 4)
    assert -UQ(8, 0) == SQ(9, 0)
    assert -SQ(16, 4) == SQ(16, 4)
    assert (SQ(16, 4) >> 2) == SQ(14, 2)
    assert (SQ(16, 4) << 2) == SQ(18, 6)


def test_voodoo1_matches_scala():
    cfg = Config.voodoo1()
    assert isinstance(cfg.revision, Voodoo1)
    assert cfg.vertex_format == SQ(16, 4)
    assert cfg.coefficient_format == SQ(34, 8)
    assert cfg.v_color_format == SQ(24, 12)
    assert cfg.v_depth_format == SQ(32, 12)
    assert cfg.tex_coords_format == SQ(32, 18)
    assert cfg.tex_coords_hi_format == SQ(46, 30)
    assert cfg.w_format == SQ(32, 30)
    assert cfg.tex_coords_accum_format == SQ(48, 18)
    assert cfg.w_accum_format == SQ(48, 30)
    assert cfg.max_fb_dims == (800, 600)
    assert cfg.address_width == 26
    assert cfg.mem_burst_length_width == 11
    assert cfg.fb_write_buffer_line_words == 16
    assert cfg.fb_write_buffer_count == 2
    assert cfg.tex_fill_line_words == 8
    assert cfg.use_fb_write_buffer is False
    assert cfg.use_fb_read_cache is True
    assert cfg.use_tex_fill_cache is True
    assert cfg.tex_fill_cache_slots == 16
    assert cfg.tex_fill_way_count == 2
    assert cfg.tex_fill_xor_index is False
    assert cfg.tex_fill_request_window == 16
    assert cfg.packed_tex_layout is True
    assert cfg.enable_hdmi_scanout is True
    assert cfg.hdmi_scanout_pixel_repeat_x == 1
    assert cfg.trace == TraceConfig(enabled=False)


def test_voodoo1_trace_override():
    cfg = Config.voodoo1(TraceConfig(enabled=True))
    assert cfg.trace.enabled is True
    # default is not shared between instances
    assert Config.voodoo1().trace.enabled is False


def test_qformat_is_hashable_and_frozen():
    assert len({SQ(16, 4), QFormat(16, 4, True), UQ(16, 4)}) == 2
