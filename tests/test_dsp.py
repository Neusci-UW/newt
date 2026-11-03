"""Digital filters and calculated channels: the formula engine, live and review plots,
Analysis, the config and the dialog."""
import re

import numpy as np
import pytest
from scipy import signal

from neusci_daq import config, dsp, simulation as sim
from neusci_daq.dsp import Calculation

from test_channels import app, make_cfg, pump, win  # noqa: F401  (fixtures)

FS = 20000.0
T = np.arange(int(FS)) / FS


def chunks(formula, env, size=1000):
    return np.concatenate([formula.stream({k: v[i:i + size] for k, v in env.items()}, FS)
                           for i in range(0, len(T), size)])


# ---------------------------------------------------------------- the engine
def test_offline_filters_have_no_delay_and_do_their_job():
    slow, fast = np.sin(2 * np.pi * 10 * T), 0.5 * np.sin(2 * np.pi * 2000 * T)
    env = {"Ch1": slow + fast}
    low = dsp.Formula("lowpass(Ch1, 100)", ["Ch1"]).offline(env, FS)
    assert np.std((low - slow)[2000:-2000]) < 0.01                    # 2 kHz gone, 10 Hz kept in place
    high = dsp.Formula("highpass(Ch1, 500)", ["Ch1"]).offline(env, FS)
    assert np.std((high - fast)[2000:-2000]) < 0.01
    band = dsp.Formula("bandpass(Ch1, 1000, 3000)", ["Ch1"]).offline(env, FS)
    assert np.std((band - fast)[2000:-2000]) < 0.02
    hum = dsp.Formula("notch(Ch1, 60)", ["Ch1"]).offline({"Ch1": np.sin(2 * np.pi * 60 * T)}, FS)
    assert np.std(hum) < 0.01                                         # even at the block's edges


def test_live_calculations_match_the_whole_signal_done_at_once():
    x = np.sin(2 * np.pi * 10 * T) + 0.3 * np.random.default_rng(0).standard_normal(len(T))
    env = {"Ch1": x}
    sos = dsp.design("lowpass", [100], FS)
    causal = signal.sosfilt(sos, x, zi=signal.sosfilt_zi(sos) * x[0])[0]
    assert np.allclose(chunks(dsp.Formula("lowpass(Ch1, 100)", ["Ch1"]), env), causal)
    assert np.allclose(chunks(dsp.Formula("derivative(Ch1)", ["Ch1"]), env), np.diff(np.r_[x[0], x]) * FS)
    assert np.allclose(chunks(dsp.Formula("integral(Ch1)", ["Ch1"]), env), np.cumsum(x) / FS)
    n = 20                                                           # 1 ms at 20 kHz, trailing
    c = np.cumsum(np.r_[0, np.full(n - 1, x[0]), x])
    assert np.allclose(chunks(dsp.Formula("smooth(Ch1, 1)", ["Ch1"]), env), (c[n:] - c[:-n]) / n)


def test_arithmetic_names_and_rms():
    env = {"Ch1": np.ones(100), "Channel 2": np.full(100, 3.0)}
    f = dsp.Formula('"channel 2" - 2 * CH1 + 0.5', list(env))       # case and spaces don't matter
    assert np.allclose(f.offline(env, FS), 1.5) and f.refs == {"Ch1", "Channel 2"}
    rms = dsp.Formula("rms(Ch1, 200)", ["Ch1"]).offline({"Ch1": np.sin(2 * np.pi * 10 * T)}, FS)
    assert rms[5000] == pytest.approx(1 / np.sqrt(2), abs=0.01)


@pytest.mark.parametrize("bad, says", [
    ("lowpass(Ch3, 100)", "no channel called 'Ch3'"), ("lowpass(Ch1)", "lowpass(x, Hz)"),
    ("Ch1 +", "Can't read"), ("__import__('os')", "Unknown function"), ("Ch1.real", "can only use"),
    ("5", "doesn't use any channel"), ("bandpass(Ch1, 3000, 300)", "low frequency must be below"),
    ("lowpass(Ch1, 15000)", "outside what a 20,000 Hz recording"),
])
def test_mistakes_are_explained(bad, says):
    with pytest.raises(ValueError, match=re.escape(says)):
        dsp.Formula(bad, ["Ch1"]).check(FS)


def test_presets_fill_in_the_channel():
    assert dsp.preset(0, ["Channel 2"], {"Channel 2": "mV"}) == \
        ("Channel 2 band-pass", 'bandpass("Channel 2", 300, 3000)', "mV")
    for i in range(len(dsp.PRESETS)):
        name, formula, _ = dsp.preset(i, ["Ch1", "Ch2"], {})
        dsp.Formula(formula, ["Ch1", "Ch2"]).check(FS)               # every preset works


def test_calculations_are_saved_in_the_config(tmp_path):
    c = config.Config(backend="simulated", channels=[config.Channel("ai0", "Ch1")],
                      calculations=[Calculation("Spikes", "bandpass(Ch1, 300, 3000)", "V")])
    assert config.load(config.save(c, tmp_path / "c.toml")).calculations == c.calculations
    with pytest.raises(ValueError, match="already a channel"):
        config.validate(config.Config(backend="simulated", channels=[config.Channel("ai0", "Ch1")],
                                      calculations=[Calculation("Ch1", "Ch1 * 2")]))


# ---------------------------------------------------------------- in the app
def extracellular(win):
    net = sim.default_network()
    net.recordings = [sim.Recording("ai0", 1, "extracellular", noise_v=0.02, sine_v=0.5, sine_hz=5)]
    win.cfg.sim_network = net


def test_a_calculated_channel_is_drawn_live_and_in_review(app, win):
    extracellular(win)
    win.apply_setup(sample_rate=20000)
    win.set_calculations([Calculation("Ch1 spikes", "highpass(Ch1, 100)", "V")])
    n = len(win.cfg.channels)
    assert len(win.plots) == n + 1 and win._plot_names[-1] == "Ch1 spikes"
    win.btn_run.setChecked(True)
    pump(app, 0.6)
    x, y = win.curves[n].getOriginalDataset()
    assert len(y) > 0 and abs(np.median(y)) < 0.05                     # the 5 Hz sine is gone
    win.btn_rec.setChecked(True)
    pump(app, 0.5)
    win.btn_rec.setChecked(False)
    win.btn_run.setChecked(False)
    win.open_session(win.session_dir)                                # review: the zero-phase version
    blk = win.store.read(win.review_blocks[0])
    ref = dsp.Formula("highpass(Ch1, 100)", blk["channel_names"]).offline(
        dict(zip(blk["channel_names"], blk["data"])), blk["sample_rate"])
    assert np.allclose(win.curves[len(blk["channel_names"])].getOriginalDataset()[1], ref)


def test_a_broken_calculation_says_why_instead_of_drawing(app, win):
    win.set_calculations([Calculation("Odd", "lowpass(Ch9, 100)", "V")])
    assert "no channel called 'Ch9'" in win.plots[-1].titleLabel.text
    win.btn_run.setChecked(True)
    pump(app, 0.3)                                                   # and acquiring still works
    win.btn_run.setChecked(False)


def test_analysis_can_find_spikes_on_a_filtered_channel(app, win):
    extracellular(win)
    win.apply_setup(sample_rate=20000)
    win.set_calculations([Calculation("Ch1 spikes", "bandpass(Ch1, 300, 3000)", "V")])
    win.btn_run.setChecked(True)
    pump(app, 0.2)
    win.btn_rec.setChecked(True)
    pump(app, 1.5)
    win.btn_rec.setChecked(False)
    win.btn_run.setChecked(False)
    win.show()
    win.tabs.pick(0, "analysis")
    pump(app, 0.2)
    raw = win.analysis_page.raw
    assert "Ch1 spikes" in [raw.channel.itemText(i) for i in range(raw.channel.count())]
    raw.channel.setCurrentText("Ch1 spikes")
    ev = raw.find()
    assert ev is not None and len(ev.t) > 0
    assert "Ch1 spikes" not in [win.analysis_page.stim_channel.itemData(i)
                                for i in range(win.analysis_page.stim_channel.count())]


def test_dialog_checks_rows(app, win):
    from neusci_daq.calcdialog import CalcDialog
    d = CalcDialog(win, [], ["Ch1", "Ch2"], {"Ch1": "V"}, FS)
    assert d.btn_ok.isEnabled()                                      # nothing yet: fine
    d.add(*dsp.preset(0, ["Ch1"], {"Ch1": "V"}))
    assert d.btn_ok.isEnabled() and d.calculations()[0].formula == "bandpass(Ch1, 300, 3000)"
    d.add("Ch2", "Ch1 * 2", "")                                      # clashes with a channel
    assert not d.btn_ok.isEnabled() and "already used" in d.message.text()
    d.table.item(1, 0).setText("Double")
    d.table.item(1, 1).setText("lowpass(Ch1")                        # half-typed
    assert not d.btn_ok.isEnabled() and "Can't read" in d.message.text()
    d.table.item(1, 1).setText("Ch1 * 2")
    assert d.btn_ok.isEnabled()
