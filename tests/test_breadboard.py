"""The breadboard circuit model (breadboard.py): nets, solving, saving."""
import numpy as np
import pytest

from neusci_daq import breadboard as bb


def test_rc_charges_and_discharges_with_tau():
    fs = 20_000
    t, stim, (v_in, v_c) = bb.preview(bb.default_board(), fs=fs)   # 10 kΩ, 1 µF: τ = 10 ms
    at = lambda s: v_c[int(round(s * fs))]
    assert v_in[int(0.05 * fs)] == pytest.approx(1.0)
    assert at(0.03) == pytest.approx(1 - np.exp(-1), abs=0.005)           # one τ into the pulse
    top = at(0.12 - 1 / fs)
    assert at(0.13) == pytest.approx(top * np.exp(-1), abs=0.005)        # one τ after it


def test_strips_rails_and_wires_make_nets():
    n = bb.nets(bb.Board([bb.Part("wire", "a2", "j9"), bb.Part("probe", "t-4"), bb.Part("probe", "b+7")]))
    assert n[bb.strip_of("e2")] == n[bb.strip_of("f9")]                  # wired strips are one net
    assert n[bb.strip_of("t-4")] == bb.GND
    assert bb.strip_of("t+1") == bb.strip_of("b+30")                      # the + rails are joined


def test_a_shorted_source_is_refused():
    with pytest.raises(ValueError):
        bb.BoardState(bb.Board([bb.Part("vin", "a5", "c5")]), 1e-4)      # both ends on one strip


def test_two_probes_on_one_input_are_refused():
    b = bb.Board([bb.Part("probe", "a1", input="ai1"), bb.Part("probe", "a2", input="ai1")])
    with pytest.raises(ValueError):
        bb.validate(b)


def test_saved_and_read_back():
    import tomllib
    b = bb.default_board()
    b.parts[3].input = "ai1"
    back = bb.from_toml(tomllib.loads("\n".join(bb.to_toml_lines(b)))["simulated"])
    assert back == b
    assert bb.board_from_dict(__import__("dataclasses").asdict(b)) == b
