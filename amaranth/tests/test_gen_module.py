"""The gen_module CLI must be able to elaborate and emit Verilog."""

import pytest

from gen import gen_module


def test_registry_entries_resolve():
    for name in gen_module.REGISTRY:
        assert gen_module.resolve(name) is not None


def test_emit_counter(tmp_path):
    path = gen_module.emit("Counter", tmp_path, {"width": 12})
    assert path == tmp_path / "Counter.v"
    text = path.read_text()
    assert "module Counter(" in text
    for port in ("en", "count", "carry", "clk", "rst"):
        assert port in text


def test_emit_by_dotted_spec(tmp_path):
    path = gen_module.emit("avoodoo.example:Counter", tmp_path)
    assert path == tmp_path / "Counter.v"
    assert "module Counter(" in path.read_text()


def test_main_writes_file(tmp_path, capsys):
    assert gen_module.main(["Counter", "width=4", "-o", str(tmp_path)]) == 0
    assert (tmp_path / "Counter.v").exists()
    assert "wrote" in capsys.readouterr().out


def test_unknown_module_is_a_clean_error(tmp_path):
    with pytest.raises(SystemExit):
        gen_module.emit("NoSuchModule", tmp_path)


def test_bad_parameter_is_a_clean_error():
    with pytest.raises(SystemExit):
        gen_module.parse_params(["width"])


def test_parse_params_coercion():
    assert gen_module.parse_params(["a=1", "b=2.5", "c=x", "d=True"]) == {
        "a": 1,
        "b": 2.5,
        "c": "x",
        "d": True,
    }
