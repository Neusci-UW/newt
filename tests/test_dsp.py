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


# ---------------------------------------------------------------- cyclic measurements, chains
def heartbeat(bpm=72, seconds=10, fs=1000.0):
    t = np.arange(int(seconds * fs)) / fs
    beats = np.arange(0.2, seconds, 60 / bpm)
    y = np.zeros_like(t)
    for b in beats:
        y += np.exp(-((t - b) / 0.01) ** 2)                          # an R wave of 1 mV
    return t, y, beats


def test_rate_period_height_and_count_like_labchart():
    fs = 1000.0
    t, y, beats = heartbeat(fs=fs)
    env = {"ECG": y}
    bpm = dsp.Formula("60 * rate(ECG, 0.5)", ["ECG"]).offline(env, fs)
    assert bpm[int(5 * fs)] == pytest.approx(72, abs=0.5)
    assert np.isnan(bpm[:int(0.5 * fs)]).all()                       # nothing until the second beat
    assert dsp.Formula("period(ECG, 0.5)", ["ECG"]).offline(env, fs)[int(5 * fs)] == pytest.approx(60 / 72, abs=0.002)
    assert dsp.Formula("height(ECG, 0.5)", ["ECG"]).offline(env, fs)[int(5 * fs)] == pytest.approx(1, abs=0.01)
    count = dsp.Formula("count(ECG, 0.5)", ["ECG"]).offline(env, fs)
    assert count[-1] == len(beats) and count[0] == 0
    live = dsp.Formula("60 * rate(ECG, 0.5)", ["ECG"])                # the same, a chunk at a time
    got = np.concatenate([live.stream({"ECG": y[i:i + 333]}, fs) for i in range(0, len(y), 333)])
    assert np.allclose(got, bpm, equal_nan=True)
    stop = np.r_[y[:int(3 * fs)], np.zeros(int(5 * fs))]             # beats stop: the rate falls
    r = dsp.Formula("60 * rate(ECG, 0.5)", ["ECG"]).offline({"ECG": stop}, fs)
    assert r[-1] < 15                                                # 60 / 5.3 s since the last beat
    neg = dsp.Formula("count(-ECG, 0.5)", ["ECG"]).offline({"ECG": -y}, fs)   # going below -0.5
    assert neg[-1] == len(beats)


def test_analysis_channels_can_use_the_ones_above():
    fs = 1000.0
    t, y, beats = heartbeat(fs=fs)
    chain = dsp.Chain([Calculation("Big", "ECG * 2", "mV"), Calculation("BPM", "60 * rate(Big, 1)", "/min"),
                       Calculation("Odd", "Nope + 1"), Calculation("Odder", "Odd * 2")], ["ECG"], fs)
    assert chain.problems[:2] == ["", ""] and "no channel called 'Nope'" in chain.problems[2]
    assert "uses Odd" in chain.problems[3] and chain.needs["BPM"] == {"ECG"}
    out = chain.offline({"ECG": y}, fs)
    assert out[1][int(5 * fs)] == pytest.approx(72, abs=0.5) and out[2] is None and out[3] is None


def test_the_dialog_writes_and_reads_back_each_kind():
    from neusci_daq.analysischannel import KINDS, formula_of, spec_of
    defaults = {k: {key: d for key, (_, d, _, _) in settings} for k, (_, _, settings, _) in KINDS.items()}
    defaults["combine"]["other"] = "Ch2"
    for kind, v in defaults.items():
        if kind == "custom":
            continue
        f = formula_of(kind, "Channel 1", v)
        dsp.Formula(f, ["Channel 1", "Ch2"]).check(FS)                # every kind makes a working formula
        k2, src, v2 = spec_of(f)
        assert (k2, src) == (kind, "Channel 1") and all(v2[key] == pytest.approx(v[key]) if
                                                        isinstance(v[key], float) else v2[key] == v[key] for key in v2)
    assert spec_of("rms(highpass(Ch1, 100), 10)") is None             # anything else: custom


def test_add_edit_and_delete_an_analysis_channel_from_acquisition(app, win):
    from neusci_daq.analysischannel import AnalysisChannelDialog
    seen = []

    def drive(edit):
        def exec_(dlg):
            seen.append(dlg)
            edit(dlg)
            return dlg.btn_ok.isEnabled() and QtDialogAccepted
        return exec_
    from pyqtgraph.Qt import QtWidgets
    QtDialogAccepted = QtWidgets.QDialog.DialogCode.Accepted
    orig = AnalysisChannelDialog.exec
    win.btn_run.setChecked(True)
    pump(app, 0.3)
    try:
        def make_rate(d):                                             # Ch1 → rate meter, events/s, above 0.2
            d.source.setCurrentText("Ch1")
            d.kind.setCurrentIndex(d.kind.findData("rate"))
            d.values()                                                # settings for the new kind exist
            d._widgets["level"].setValue(0.2)
            d._widgets["dir"].setCurrentIndex(d._widgets["dir"].findData("above"))   # Spike Sorting says below
            d._widgets["per"].setCurrentIndex(1)
        AnalysisChannelDialog.exec = drive(make_rate)
        assert win.analysis_channel_dialog()
        c = win.cfg.calculations[-1]
        assert (c.name, c.formula, c.unit) == ("Ch1 rate", "rate(Ch1, 0.2)", "/s")
        assert seen[-1].curve.getOriginalDataset()[0] is not None      # previewed on the live data
        assert win._plot_names[-1] == "Ch1 rate"

        def smooth_it(d):                                             # a second one built on the first
            assert "Ch1 rate" in d.sources
            d.source.setCurrentText("Ch1 rate")
            d.kind.setCurrentIndex(d.kind.findData("smooth"))
            d._widgets["ms"].setValue(200)
        AnalysisChannelDialog.exec = drive(smooth_it)
        assert win.analysis_channel_dialog()
        assert win.cfg.calculations[-1].formula == 'smooth("Ch1 rate", 200)'
        pump(app, 0.3)
        assert all(not p for _, _, p in win._calcs)                   # both calculate, live

        def check_reopen(d):                                          # Edit reopens the form
            assert d.kind.currentData() == "rate" and d._widgets["level"].value() == pytest.approx(0.2)
            d._widgets["dead"].setValue(5)
        AnalysisChannelDialog.exec = drive(check_reopen)
        assert win.analysis_channel_dialog(0)
        assert win.cfg.calculations[0].formula == "rate(Ch1, 0.2, 5)"

        AnalysisChannelDialog.exec = drive(lambda d: d._delete())
        assert win.analysis_channel_dialog(1)
        assert [c.name for c in win.cfg.calculations] == ["Ch1 rate"]
    finally:
        AnalysisChannelDialog.exec = orig
        win.btn_run.setChecked(False)


def test_names_clash_and_bad_settings_stop_ok(app, win):
    from neusci_daq.analysischannel import AnalysisChannelDialog
    d = AnalysisChannelDialog(win, ["Ch1", "Ch2"], {"Ch1": "V"}, 2000.0, taken={"Ch1 band-pass"})
    assert d.name.text() == "Ch1 band-pass 2" and d.unit.text() == "V"    # a free name, the source's unit
    d._widgets["hi"].setValue(5000)                                   # above Nyquist at 2 kHz
    assert not d.btn_ok.isEnabled() and "outside what" in d.message.text()
    d._widgets["hi"].setValue(900)
    assert d.btn_ok.isEnabled()
    d.name.setText("Ch2")
    d.name.textEdited.emit("Ch2")
    assert not d.btn_ok.isEnabled() and "already used" in d.message.text()


def test_dialog_settings_take_typed_units(app, win):
    from neusci_daq.analysischannel import AnalysisChannelDialog

    def type_in(w, text):
        w.lineEdit().setText(text)
        w.interpretText()
    d = AnalysisChannelDialog(win, ["Ch1", "Ch2"], {"Ch1": "V", "Ch2": "mV"}, 20000.0)
    d.kind.setCurrentIndex(d.kind.findData("height"))
    type_in(d._widgets["level"], "100 mV")                           # in a V channel
    type_in(d._widgets["dead"], "0.5 s")
    assert d.current_formula() == "height(Ch1, 0.1, 500)"
    assert d._widgets["level"].text().strip() == "100 mV"
    d.source.setCurrentText("Ch2")                                   # a mV channel: the level converts
    assert d._widgets["level"].value() == pytest.approx(100) and d.current_formula() == "height(Ch2, 100, 500)"
    d.kind.setCurrentIndex(d.kind.findData("bandpass"))
    type_in(d._widgets["lo"], "0.3 kHz")
    type_in(d._widgets["hi"], "3 kHz")
    assert d.current_formula() == "bandpass(Ch2, 300, 3000)"
    d.kind.setCurrentIndex(d.kind.findData("smooth"))
    type_in(d._widgets["ms"], "500 µs")
    assert d.current_formula() == "smooth(Ch2, 0.5)"


def test_cyclic_measurements_start_from_the_spike_sorting_settings(app, win):
    from neusci_daq.analysischannel import AnalysisChannelDialog
    sorter = {"channel": "Ch2", "level": -0.12, "dir": "below", "dead": 2.0}
    d = AnalysisChannelDialog(win, ["Ch1", "Ch2"], {"Ch1": "V", "Ch2": "V"}, 20000.0, sorter=sorter)
    d.source.setCurrentText("Ch2")
    d.kind.setCurrentIndex(d.kind.findData("rate"))
    assert d.values()["level"] == pytest.approx(-0.12) and d.values()["dir"] == "below"
    assert d.values()["dead"] == 2.0 and "from Spike Sorting" in d.settings_box.title()
    assert d.current_formula() == "60 * rate(-Ch2, 0.12, 2)"
    d.source.setCurrentText("Ch1")                                   # another channel: its own level
    assert d.values()["dir"] == "below" and d.values()["level"] == pytest.approx(0.5)
    d.source.setCurrentText("Ch2")
    d._widgets["level"].setValue(-0.3)                               # typed: it stays
    d.source.setCurrentText("Ch1")
    d.source.setCurrentText("Ch2")
    assert d.values()["level"] == pytest.approx(-0.3)


def test_spike_sorting_gives_its_threshold_as_a_level(app, win):
    from test_analysis import two_stimulus_session
    two_stimulus_session(app, win)
    win.show()
    win.tabs.pick(0, "analysis")
    pump(app, 0.2)
    raw = win.analysis_page.raw
    raw.channel.setCurrentText("Ch2")
    raw.threshold.setText("100 mV")
    raw.threshold.editingFinished.emit()
    d = raw.cyclic_defaults()
    assert d["channel"] == "Ch2" and d["dir"] == "below" and d["dead"] == raw.dead.value()
    assert d["level"] == pytest.approx(raw._baseline() - 0.1)
