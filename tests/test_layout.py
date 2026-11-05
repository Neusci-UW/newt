"""Tabs, split view, hover strips, line thickness, whole-app light/dark, stimulator chip."""
import numpy as np
import pytest
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

from neusci_daq import config, style, theme
from neusci_daq.stim import Stimulus
from neusci_daq.tabs import ONE, SIDE, STACKED

from test_channels import app, make_cfg, pump, win  # noqa: F401  (fixtures)


def shown(w):
    return w.tabs.visible()


# ---------------------------------------------------------------- tabs and split
def tab_texts(bar):
    return [bar.tabText(i) for i in range(bar.count())]


def test_starts_on_acquisition_record_with_sub_pages(win):
    assert shown(win) == ["acq"]
    pane = win.tabs.panes[0]
    assert tab_texts(pane.bar) == ["Acquisition", "Analysis", "Simulation"]
    assert tab_texts(pane.sub_bar) == ["Record", "Stimulator", "Trials"]
    assert pane.sub_bar.currentIndex() == 0 and not pane.sub_head.isHidden()


def test_sub_page_bar_only_under_tabs_that_have_sub_pages(win):
    pane = win.tabs.panes[0]
    win.tabs.pick_group(0, "analysis")
    assert shown(win) == ["analysis"] and pane.sub_head.isHidden()
    assert pane.bar.currentIndex() == 1
    win.tabs.pick_group(0, "acquisition")
    assert shown(win) == ["acq"] and not pane.sub_head.isHidden()


def test_a_tab_comes_back_to_the_sub_page_last_used(win):
    win.tabs.pick(0, "trials")
    win.tabs.pick_group(0, "analysis")
    win.tabs.pick_group(0, "acquisition")
    assert shown(win) == ["trials"]
    assert win.tabs.panes[0].sub_bar.currentIndex() == 2


def test_clicking_the_sub_page_bar_picks_the_page(win):
    pane = win.tabs.panes[0]
    pane.sub_bar.tabBarClicked.emit(1)
    assert shown(win) == ["stim"] and pane.bar.currentIndex() == 0


def test_any_page_can_go_in_either_half(win):
    """Two sub-pages of Acquisition side by side, or a sub-page beside another tab."""
    win.tabs.set_layout(SIDE)
    for left, right in (("acq", "stim"), ("trials", "acq"), ("stim", "analysis"), ("analysis", "trials"),
                        ("sim", "stim"), ("acq", "sim")):
        if win.tabs.shown[1] == left:                        # move it out of the way first,
            spare = next(p for p in win.tabs.ids if p not in (left, right, win.tabs.shown[0]))
            win.tabs.pick(1, spare)                          # or picking it would un-split
        win.tabs.pick(0, left)
        win.tabs.pick(1, right)
        assert shown(win) == [left, right], (left, right)
        for pane, pid in zip(win.tabs.panes, (left, right)):
            assert pane.page_id == pid and win.tabs.pages[pid].isVisibleTo(pane)


def test_picking_a_tab_in_a_split_avoids_the_page_the_other_half_shows(win):
    win.tabs.set_layout(SIDE)                                # Record | Stimulator
    win.tabs.pick_group(1, "analysis")                       # Record | Analysis
    win.tabs.pick_group(1, "acquisition")                    # back: Stimulator, not Record again
    assert shown(win) == ["acq", "stim"] and win.tabs.layout_mode == SIDE
    win.tabs.pick_group(0, "analysis")
    win.tabs.pick_group(1, "analysis")                       # only one page: shows it full screen
    assert win.tabs.layout_mode == ONE and shown(win) == ["analysis"]


def test_tab_bars_never_show_scroll_arrows(app, win):
    win.resize(1024, 600)
    win.show()
    win.tabs.set_layout(SIDE)                                  # narrowest tab bars
    pump(app, 0.05)
    for pane in win.tabs.panes:
        assert not pane.bar.usesScrollButtons()
        assert not any(b.isVisible() for b in pane.bar.findChildren(QtWidgets.QToolButton))


def test_pick_tabs_and_split(win):
    win.tabs.pick(0, "stim")
    assert shown(win) == ["stim"]
    win.tabs.set_layout(SIDE)
    assert shown(win) == ["stim", "acq"] and win.split_buttons[SIDE].isChecked()
    win.tabs.pick(1, "trials")
    assert shown(win) == ["stim", "trials"]
    win.tabs.set_layout(STACKED)
    assert win.tabs.splitter.orientation().name == "Vertical"
    win.tabs.set_layout(ONE)
    assert shown(win) == ["stim"] and win.split_buttons[ONE].isChecked()


def test_same_tab_in_both_halves_goes_back_to_one_tab(win):
    win.tabs.set_layout(SIDE)
    assert shown(win) == ["acq", "stim"]
    win.tabs.pick(1, "acq")                                  # already open on the left
    assert win.tabs.layout_mode == ONE and shown(win) == ["acq"]


def test_open_stim_window_shows_the_tab(win):
    win.open_stim_window()
    assert win.tabs.is_visible("stim")
    win.tabs.pick(0, "acq")
    win.tabs.set_layout(SIDE)
    win.tabs.pick(1, "trials")
    win.open_stim_window()                                   # puts it in the second half
    assert shown(win) == ["acq", "stim"]


def test_toolbar_follows_visible_tabs(app, win):
    win.resize(1300, 600)       # a narrow window moves toolbar items into its overflow menu
    win.show()
    pump(app, 0.05)
    assert win.comment_edit.isVisible() and win.window_box.isVisible() and win.btn_comments.isVisible()
    win.tabs.pick(0, "stim")
    pump(app, 0.05)
    assert not win.comment_edit.isVisible() and not win.window_box.isVisible()
    assert not win.btn_comments.isVisible()                  # it's part of the Acquisition page
    assert win.btn_run.isVisible() and win.btn_rec.isVisible() and win.rate_box.isVisible()
    win.tabs.set_layout(SIDE)                                # stim | acq
    pump(app, 0.05)
    assert win.comment_edit.isVisible()


# ---------------------------------------------------------------- hover strips
def test_strips_compact_only_when_acquisition_shares_the_screen(win):
    assert not any(s.compact for s in win.strips)
    win.tabs.set_layout(SIDE)
    assert all(s.compact and not s.expanded for s in win.strips)
    assert all(s.width() == 54 for s in win.strips)
    win.tabs.set_layout(ONE)
    assert not any(s.compact for s in win.strips) and all(s.expanded for s in win.strips)


def test_hover_expands_over_the_plot_without_squeezing_it(app, win):
    win.resize(1300, 600)
    win.show()
    win.tabs.set_layout(SIDE)
    pump(app, 0.1)
    rw, strip = win.row_widgets[0], win.strips[0]
    plot_geom = rw.content.geometry()
    strip._set_expanded(True)                                 # what entering the strip does
    assert strip.expanded and strip.width() == 180
    assert rw.content.geometry() == plot_geom                 # plot didn't move
    strip._maybe_collapse()                                    # mouse isn't over it
    assert not strip.expanded and strip.width() == 54


def test_hover_strip_stays_open_while_editing(app, win):
    win.show()
    win.tabs.set_layout(SIDE)
    pump(app, 0.05)
    strip = win.strips[0]
    strip._set_expanded(True)
    strip.name.setFocus()
    pump(app, 0.05)
    strip._maybe_collapse()
    assert strip.expanded
    win.setFocus()
    strip.clearFocus()
    pump(app, 0.05)
    strip._maybe_collapse()
    assert not strip.expanded


def test_rebuilt_strips_keep_compact_mode(win):
    win.tabs.set_layout(SIDE)
    win.add_channel()
    assert all(s.compact for s in win.strips)


# ---------------------------------------------------------------- line thickness
def test_line_thickness_typed_and_clamped(app, win):
    win.strips[1].line_width.setValue(6)
    assert win.cfg.channels[1].width == 6
    assert win.curves[1].opts["pen"].widthF() == 6
    win.set_channel_width(0, 50)
    assert win.cfg.channels[0].width == 20                   # max 20 px
    assert win.strips[0].line_width.maximum() == 20


def test_line_thickness_works_while_recording_and_is_saved(app, win, tmp_path):
    win.btn_run.setChecked(True)
    pump(app, 0.2)
    win.btn_rec.setChecked(True)
    assert win.strips[0].line_width.isEnabled()
    win.set_channel_width(0, 3)
    assert win.recorder.recording and win.backend.running   # no restart
    win.btn_rec.setChecked(False)
    win.btn_run.setChecked(False)
    back = config.load(config.save(win.cfg, tmp_path / "c.toml"))
    assert back.channels[0].width == 3 and back.channels[1].width == 1.0
    cfg = make_cfg(tmp_path)
    cfg.channels[0].width = 25
    with pytest.raises(ValueError, match="0.5 and 20"):
        config.validate(cfg)


def test_light_mode_keeps_thickness(win):
    win.set_channel_width(0, 4)
    win.set_plot_theme(theme.LIGHT)
    assert win.curves[0].opts["pen"].widthF() == 4


# ---------------------------------------------------------------- whole-app light/dark
def test_one_toggle_switches_window_and_plots(app, win):
    old_sheet, old_pal = app.styleSheet(), app.palette()
    try:
        style.apply(app, style.DARK)
        win.set_plot_theme(theme.DARK)
        assert app.palette().color(QtGui.QPalette.ColorRole.Window).name() == style.TOKENS["dark"]["ground"]
        assert win.plot_widgets[0].backgroundBrush().color().name() == "#000000"
        win.toggle_plot_theme()
        assert app.palette().color(QtGui.QPalette.ColorRole.Window).name() == style.TOKENS["light"]["ground"]
        assert win.plot_widgets[0].backgroundBrush().color().name() == "#ffffff"
        assert win.stim_page.preview.backgroundBrush().color().name() == "#ffffff"
        assert "click for dark mode" in win.btn_theme.toolTip()
    finally:
        style._applied["mode"] = None
        app.setStyleSheet(old_sheet)
        app.setPalette(old_pal)


def test_both_stylesheets_reference_existing_images():
    for mode in (style.LIGHT, style.DARK):
        sheet = style.stylesheet(mode)
        suffix = "-dark" if mode == style.DARK else ""
        for name in ("chevron-down", "chevron-up", "chevron-down-disabled", "chevron-up-disabled"):
            assert (style.ICON_DIR / f"{name}{suffix}.svg").exists()
            assert f"{name}{suffix}.svg" in sheet
        for name in ("check", "check-disabled"):
            assert (style.ICON_DIR / f"{name}.svg").exists() and f"{name}.svg" in sheet


# ---------------------------------------------------------------- stimulator tab and chip
def test_no_stimulator_menu_any_more(win):
    assert [a.text().replace("&", "") for a in win.menuBar().actions()] == ["File", "Setup", "Audio/Video", "View"]


def test_chip_shows_on_every_tab_while_repeating_and_stops_it(app, win):
    win.resize(1300, 600)
    win.show()
    win.stim_repeat_s = 0.2
    win.set_stimulus(Stimulus(amplitude_v=1, width_ms=1))
    win.btn_run.setChecked(True)
    pump(app, 0.2)
    assert not win.stim_chip.isVisible()
    win.stim_page.btn_repeat.click()
    pump(app, 0.05)
    assert win.stim_chip.isVisible() and win.stim_chip_text.text() == "Stimulating"
    assert "every 0.2 s" in win.stim_chip.toolTip()
    win.tabs.pick(0, "trials")                                # still visible on another tab
    pump(app, 0.05)
    assert win.stim_chip.isVisible()
    win.btn_chip_stop.click()
    pump(app, 0.1)
    assert not win.repeating and not win.stim_chip.isVisible()
    assert not win.stim_page.btn_repeat.isChecked()
    win.btn_run.setChecked(False)


def test_escape_stops_and_ctrl_f_fires(app, win):
    win.set_stimulus(Stimulus(shape="step", amplitude_v=1, duration_ms=2000))
    win.btn_run.setChecked(True)
    pump(app, 0.2)
    assert win.fire_stimulus() and win.stim_out.busy
    win._escape()
    assert not win.stim_out.busy
    win.btn_run.setChecked(False)


def test_stim_tab_output_and_record_on(app, win):
    page = win.open_stim_window()
    page.output.setCurrentText("ao1")
    page.output.activated.emit(page.output.currentIndex())
    assert win.cfg.stim_output == "ao1"
    labels = [page.monitor.itemText(i) for i in range(page.monitor.count())]
    assert labels[0].startswith("None") and "ai0 (Ch1)" in labels                 # picking it makes Ch1 the stimulus channel
    page.monitor.setCurrentIndex(page.monitor.findData("ai3"))
    page._monitor_picked()
    assert win.cfg.stim_monitor == "ai3" and [c.name for c in win.cfg.channels][-1] == "Stim"
    assert page.monitor.currentData() == "ai3"


def test_stimulator_tab_shows_the_stimulus_that_fires(win):
    """Setting the stimulus from outside the tab must update the tab, or editing a
    field would silently swap back to the old one."""
    win.set_stimulus(Stimulus(shape="pulse", amplitude_v=2, width_ms=5, count=4, interval_ms=60))
    page = win.stim_page
    assert page.p_amp.value() == 2 and page.p_count.value() == 4
    page.p_width.setValue(4)                                   # edit one field
    s = win.stimulus
    assert (s.amplitude_v, s.width_ms, s.count, s.interval_ms) == (2, 4, 4, 60)


@pytest.fixture
def scaled_win(request, tmp_path, app):
    """A window built after widening the app font by request.param. It has to be
    built after: widgets that already exist keep their size hints."""
    from pyqtgraph.Qt import QtCore
    from neusci_daq.gui import MainWindow
    old_font = app.font()
    f = QtGui.QFont(old_font)
    f.setPointSizeF(old_font.pointSizeF() * request.param)
    app.setFont(f)
    st = QtCore.QSettings(str(tmp_path / "s.ini"), QtCore.QSettings.Format.IniFormat)
    w = MainWindow(make_cfg(tmp_path), settings=st, config_path=tmp_path / "config.toml")
    w._ask_unsaved = lambda: "discard"
    try:
        yield w
    finally:
        w.close()
        app.setFont(old_font)


@pytest.mark.parametrize("scaled_win", [1.0, 1.15, 1.3], indirect=True)
@pytest.mark.parametrize("mode", [style.DARK, style.LIGHT])
def test_everything_fits_on_a_1024_px_wide_window(app, scaled_win, mode):
    """A 1280 px laptop at 125% scaling is about 1024 px wide. Uses the real stylesheet:
    unstyled widgets are narrower, so without it this passes when the app doesn't fit.
    The font is widened too: macOS's is about 5% wider than Linux's, which is how this
    failed on the Mac CI runner and passed everywhere else."""
    win = scaled_win
    old_sheet, old_pal = app.styleSheet(), app.palette()
    try:
        style.apply(app, mode)
        win.resize(1024, 640)
        win.show()
        win.set_stimulus(Stimulus(amplitude_v=1, width_ms=1))
        win.btn_run.setChecked(True)
        pump(app, 0.2)
        win.btn_rec.setChecked(True)                           # longest Record text
        win.stim_repeat_s = 12.5
        win.set_repeat(True)                                   # chip showing too
        win.btn_comments.setText("Comments (88)")              # longest button text
        pump(app, 0.2)
        for w in (win.btn_run, win.btn_rec, win.rate_box, win.window_box, win.comment_edit,
                  win.stim_chip, win.btn_comments, win.btn_theme, win.split_group):
            assert w.isVisible(), w                            # not in the overflow menu
            right = w.mapTo(win, w.rect().topRight()).x()
            assert right < win.width(), w
        # Labels give way in order: "Sampling rate" goes before "Window".
        assert not (win.lbl_rate.isVisible() and not win.lbl_window.isVisible())
        assert win.btn_rec.text().endswith("0:00") or win.btn_rec.text().endswith("0:01")
        win.stop_stimulus()
        win.btn_rec.setChecked(False)
        assert win.btn_rec.text() == "Record"
        win.btn_run.setChecked(False)
    finally:
        style._applied["mode"] = None
        app.setStyleSheet(old_sheet)
        app.setPalette(old_pal)


def test_toolbar_labels_and_full_record_text_come_back_when_wide(app, win):
    win.resize(1024, 640)
    win.show()
    win.btn_run.setChecked(True)
    win.btn_rec.setChecked(True)
    pump(app, 0.1)
    win.resize(1600, 640)
    pump(app, 0.1)
    assert win.lbl_rate.isVisible() and win.lbl_window.isVisible()
    assert win.btn_rec.text().startswith("Stop recording · ")
    win.btn_rec.setChecked(False)
    win.btn_run.setChecked(False)


def test_short_record_text_keeps_its_meaning_in_a_tooltip(app, win):
    win.resize(1024, 640)
    win.show()
    win.btn_run.setChecked(True)
    win.btn_rec.setChecked(True)
    pump(app, 0.1)
    win.toolbar.setFixedWidth(300)                             # far too narrow: the window's minimum
    pump(app, 0.1)                                             # width follows the font, so resizing
    win._fit_toolbar()                                         # it may leave room (it did on Windows)
    assert win.btn_rec.text() in ("0:00", "0:01")
    assert win.btn_rec.toolTip() == "Stop recording"
    assert not win.lbl_rate.isVisible() and not win.lbl_window.isVisible()
    win.btn_rec.setChecked(False)
    assert (win.btn_rec.text(), win.btn_rec.toolTip()) == ("Record", "")
    win.btn_run.setChecked(False)


def test_closing_stops_the_redraw_timer(app, win):
    """It used to keep firing after the plots were deleted: IndexError in _refresh."""
    win.show()
    win.btn_run.setChecked(True)
    pump(app, 0.1)
    win.btn_run.setChecked(False)
    win._ask_unsaved = lambda: "discard"
    win.close()
    assert not win.timer.isActive()
    win._refresh()                                             # and is harmless if it does run


def test_split_and_theme_controls_sit_on_the_toolbar_row(app, win):
    """Same row as Start and Record, at the right end, on every tab and in split view."""
    win.resize(1300, 600)
    win.show()
    for pick, mode in (("acq", ONE), ("stim", ONE), ("trials", SIDE)):
        win.tabs.pick(0, pick)
        win.tabs.set_layout(mode)
        pump(app, 0.05)
        assert win.split_group.isVisible() and win.btn_theme.isVisible()
        mid = lambda w: w.mapTo(win, w.rect().center()).y()
        assert abs(mid(win.split_group) - mid(win.btn_run)) <= 1
        assert abs(mid(win.btn_theme) - mid(win.btn_run)) <= 1
        right = win.btn_theme.mapTo(win, win.btn_theme.rect().topRight()).x()
        assert right > win.width() - 40                      # right end of the toolbar
    for pane in win.tabs.panes:                              # nothing left on the tab bars
        assert win.tab_corner.parent() is not pane.head_layout.parentWidget()


def test_long_comment_labels_are_shortened_on_the_plot(app, win):
    win.btn_run.setChecked(True)
    pump(app, 0.2)
    win.btn_rec.setChecked(True)
    long = "Stim: 10 ms delay, Pulse 2 V, 5 ms ×4 every 60 ms"
    win.comment_edit.setText(long)
    win._add_comment()
    label = win.markers[0][1][0].label.format
    assert len(label) <= 32 and label.endswith("…")
    assert win._shown_comments[-1]["text"] == long             # full text is kept
    win.btn_rec.setChecked(False)
    win.btn_run.setChecked(False)


# ---------------------------------------------------------------- mouse wheel zoom
class _Wheel:
    """Just enough of a QGraphicsSceneWheelEvent for ViewBox.wheelEvent."""
    def __init__(self, vb, delta=-120, cmd=False, shift=False, sideways=False):
        M = QtCore.Qt.KeyboardModifier
        self._vb, self._delta, self._sideways = vb, delta, sideways
        self._mods = M.ControlModifier if cmd else M.ShiftModifier if shift else M.NoModifier
    def delta(self): return self._delta
    def modifiers(self): return self._mods
    def orientation(self):
        return QtCore.Qt.Orientation.Horizontal if self._sideways else QtCore.Qt.Orientation.Vertical
    def pos(self): return self._vb.boundingRect().center()
    def accept(self): pass
    def ignore(self): pass


def test_wheel_zooms_time_and_cmd_wheel_zooms_voltage(app, win):
    win.resize(1024, 640)
    win.show()
    pump(app, 0.05)
    vb = win.plots[0].vb
    yr = vb.viewRange()[1]
    before = win.window_box.value()
    vb.wheelEvent(_Wheel(vb, delta=-120))                    # wheel down: zoom out in time
    assert win.window_box.value() > before
    assert vb.viewRange()[1] == yr                           # voltage untouched
    vb.wheelEvent(_Wheel(vb, delta=120))
    vb.wheelEvent(_Wheel(vb, delta=120))                     # zoom in
    assert win.window_box.value() < before
    vb.wheelEvent(_Wheel(vb, delta=120, cmd=True))           # Cmd: zoom in on voltage only
    span = lambda r: r[1] - r[0]
    assert span(vb.viewRange()[1]) < span(yr)
    assert not vb.autoRangeEnabled()[1]


def test_window_can_never_be_longer_than_the_data(app, win):
    win.window_box.setValue(5.0)
    win.show()
    win.btn_run.setChecked(True)
    pump(app, 0.3)
    win.btn_run.setChecked(False)
    pump(app, 0.05)
    wb, vb = win.window_box, win.plots[0].vb
    assert wb.value() == wb.maximum() < 1.0                  # only this much was recorded
    wb.setValue(wb.maximum() + 10)
    x0, x1 = vb.viewRange()[0]
    assert x1 - x0 == pytest.approx(wb.value(), rel=0.01)    # the box and the chart agree
    vb.wheelEvent(_Wheel(vb, delta=-120))                    # zooming out stops at the data too
    assert wb.value() == wb.maximum()
    win.btn_run.setChecked(True)
    pump(app, 0.05)
    assert wb.value() == 5.0 and wb.maximum() == 600         # the next run gets its Window back
    win.btn_run.setChecked(False)


def test_a_plot_with_no_data_has_nothing_to_zoom_out_to(app):
    from neusci_daq.plotzoom import plot_widget
    vb = plot_widget().getPlotItem().vb
    before = vb.viewRange()
    vb.zoom_time(2.0)
    vb.wheelEvent(_Wheel(vb, delta=-120, cmd=True))
    assert vb.viewRange() == before


def test_wheel_in_review_keeps_the_time_under_the_mouse(app, win):
    win.window_box.setValue(0.5)
    win.btn_run.setChecked(True)
    pump(app, 0.2)
    win.btn_rec.setChecked(True)
    pump(app, 2.0)
    win.btn_rec.setChecked(False)
    win.btn_run.setChecked(False)
    win.open_session(win.session_dir)
    win.window_box.setValue(0.5)
    win.scroll.setValue(500)                                 # view 0.5-1.0 s
    vb = win.plots[0].vb
    x0, x1 = vb.viewRange()[0]
    win._zoom_time(0.5, x=0.75)                              # zoom in around 0.75 s
    assert win.window_box.value() == pytest.approx(0.25)
    assert vb.viewRange()[0][0] == pytest.approx(0.625, abs=0.002)


def test_wheel_zoom_works_while_acquisition_is_stopped(app, win):
    win.resize(1024, 640)
    win.show()
    win.window_box.setValue(2)
    win.btn_run.setChecked(True)
    pump(app, 2.5)                                           # fill the 2 s window
    win.btn_run.setChecked(False)
    pump(app, 0.05)
    vb = win.plots[0].vb
    n = len(win.buf_t)
    t_end = win.buf_t[-1] - win._axis_zero
    win._zoom_time(0.25, x=t_end - 1.0)                      # zoom in around 1 s before the end
    lo, hi = vb.viewRange()[0]
    assert win.window_box.value() == pytest.approx(0.5)
    assert hi - lo == pytest.approx(0.5) and lo < t_end - 1.0 < hi   # the view moved with it
    assert len(win.buf_t) == n                               # the trace wasn't trimmed
    win._zoom_time(4.0, x=t_end - 1.0)                       # and back out: all of it again
    lo, hi = vb.viewRange()[0]
    assert hi - lo == pytest.approx(2.0, abs=1e-3) and lo <= win.buf_t[0] - win._axis_zero + 0.01   # stops at the last sample
    yr = vb.viewRange()[1]
    vb.wheelEvent(_Wheel(vb, delta=120, cmd=True))           # Cmd: voltage zoom while stopped too
    assert vb.viewRange()[1][1] - vb.viewRange()[1][0] < yr[1] - yr[0]
    win.window_box.setValue(1)                               # typing a Window redraws too
    lo, hi = vb.viewRange()[0]
    assert hi - lo == pytest.approx(1.0)


def test_shift_wheel_moves_through_time(app, win):
    win.resize(1024, 640)
    win.show()
    win.window_box.setValue(2)
    win.btn_run.setChecked(True)
    pump(app, 2.5)
    vb = win.plots[0].vb
    running = vb.viewRange()[0]
    vb.wheelEvent(_Wheel(vb, delta=120, shift=True))         # running: the newest data stays put
    assert vb.viewRange()[0] == running
    win.btn_run.setChecked(False)
    pump(app, 0.05)
    win.window_box.setValue(0.5)
    lo = vb.viewRange()[0][0]
    vb.wheelEvent(_Wheel(vb, delta=120, shift=True))         # wheel up: earlier, by a tenth of Window
    assert vb.viewRange()[0][0] == pytest.approx(lo - 0.05)
    vb.wheelEvent(_Wheel(vb, delta=-120, sideways=True))     # sideways scroll (macOS Shift+wheel): later
    assert vb.viewRange()[0][0] == pytest.approx(lo)
    assert win.window_box.value() == pytest.approx(0.5)      # no zoom
    for _ in range(100):
        vb.wheelEvent(_Wheel(vb, delta=120, shift=True))     # stops at the start of the trace
    assert vb.viewRange()[0][0] == pytest.approx(win.buf_t[0] - win._axis_zero)


def test_voltage_auto_scale_can_be_turned_off(app, win):
    win.resize(1024, 640)
    win.show()
    assert win.act_auto_y.isChecked()                        # on by default
    win.btn_run.setChecked(True)
    pump(app, 0.5)
    vb = win.plots[0].vb
    assert vb.autoRangeEnabled()[1]
    win.act_auto_y.trigger()                                 # View > Auto-scale voltage: off
    held = vb.viewRange()[1]
    pump(app, 0.5)
    assert not win.auto_scale_y and vb.viewRange()[1] == held  # the range stays put
    assert win.settings.value("auto_scale_y", type=bool) is False
    win.apply_setup(sample_rate=1000)                        # rebuilt plots: fitted once, then held
    pump(app, 0.5)
    vb = win.plots[0].vb
    lo, hi = vb.viewRange()[1]
    y = win.curves[0].getOriginalDataset()[1]
    assert not vb.autoRangeEnabled()[1] and lo <= np.median(y) <= hi and hi - lo < 5   # fits the trace
    win.act_auto_y.trigger()                                 # back on
    assert win.auto_scale_y and vb.autoRangeEnabled()[1]
    win.btn_run.setChecked(False)


def test_wheel_zoom_after_changing_the_rate_while_stopped(app, win):
    """A rate change clears the trace and rebuilds the plots; zooming must still work."""
    win.resize(1024, 640)
    win.show()
    win.btn_run.setChecked(True)
    pump(app, 0.5)
    win.btn_run.setChecked(False)
    win.apply_setup(sample_rate=1000)
    pump(app, 0.05)
    vb = win.plots[0].vb
    assert vb.viewRange()[0] == pytest.approx([0, win.window_box.value()])
    vb.wheelEvent(_Wheel(vb, delta=-120))                    # zoom out
    lo, hi = vb.viewRange()[0]
    assert win.window_box.value() > 10 and hi - lo == pytest.approx(win.window_box.value())


def test_wheel_zoom_never_gets_stuck(app, win):
    """Zoomed all the way in, or with a trackpad's small scrolls, zoom must still move."""
    win.resize(1024, 640)
    win.show()
    vb = win.plots[0].vb
    for _ in range(40):
        vb.wheelEvent(_Wheel(vb, delta=120))                 # all the way in
    assert win.window_box.value() == pytest.approx(0.01)
    vb.wheelEvent(_Wheel(vb, delta=-120))                    # one notch back out
    assert win.window_box.value() == pytest.approx(0.0135)
    for w in (0.01, 0.1, 1.0, 13.5):                         # trackpad-sized scrolls, both ways
        win.window_box.setValue(w)
        vb.wheelEvent(_Wheel(vb, delta=-2))
        assert win.window_box.value() > w
        win.window_box.setValue(w)
        vb.wheelEvent(_Wheel(vb, delta=2))
        assert win.window_box.value() < w or w == 0.01
    assert win.window_box.text().strip() in ("13.4 s",)      # stays readable


def test_trials_and_stimulator_plots_use_the_same_wheel_rules(app, win):
    from neusci_daq.plotzoom import WheelViewBox
    win.resize(1024, 640)
    win.show()
    tp = win.trials_page
    for pw in (tp.preview, tp.curve_plot, win.stim_page.preview):
        vb = pw.getPlotItem().vb
        assert isinstance(vb, WheelViewBox)
        vb.clamp_to_data = False                             # the wheel rules, whatever's plotted
        vb.setRange(xRange=(0, 10), yRange=(-1, 1), padding=0)
        vb.wheelEvent(_Wheel(vb, delta=120))                 # wheel: time zooms in, voltage stays
        (lo, hi), yr = vb.viewRange()
        assert hi - lo < 10 and yr == pytest.approx([-1, 1])
        vb.wheelEvent(_Wheel(vb, delta=-120))
        vb.wheelEvent(_Wheel(vb, delta=-120))                # and back out past where it started
        lo, hi = vb.viewRange()[0]
        assert hi - lo > 10
        span = hi - lo
        vb.wheelEvent(_Wheel(vb, delta=-120, shift=True))    # Shift: later by a tenth, no zoom
        assert vb.viewRange()[0] == pytest.approx([lo + span / 10, hi + span / 10])
        vb.wheelEvent(_Wheel(vb, delta=120, cmd=True))       # Cmd: voltage zooms, time stays
        assert vb.viewRange()[1][1] - vb.viewRange()[1][0] < 2
        assert vb.viewRange()[0] == pytest.approx([lo + span / 10, hi + span / 10])


# ---------------------------------------------------------------- zoomed-out traces
def test_zoomed_out_traces_average_without_shimmer(app):
    import numpy as np
    import pyqtgraph as pg
    from neusci_daq.plotzoom import TraceCurve
    pw = pg.PlotWidget()
    pw.resize(600, 300)
    pw.show()
    curve = TraceCurve()
    pw.getPlotItem().addItem(curve)
    fs = 20000
    rng = np.random.default_rng(0)
    y_all = rng.standard_normal(fs * 12)

    def show(start):                                         # a 10 s view, scrolled by `start` samples
        x = np.arange(start, start + fs * 10) / fs
        pw.getPlotItem().setXRange(x[0], x[-1], padding=0)
        curve.setData(x, y_all[start:start + fs * 10])
        app.processEvents()
        return curve.getData()

    TraceCurve.mode = "mean"
    x1, y1 = show(0)
    assert len(x1) < 2000                                    # ~1 point per pixel, not 200,000
    assert np.std(y1) < 0.2                                  # an average: the noise is smoothed out
    x2, y2 = show(37)                                        # scroll by a few samples
    common = np.intersect1d(np.round(x1, 9), np.round(x2, 9))
    assert len(common) > 0.9 * len(x1)                       # the same groups, not regrouped
    i1 = np.isin(np.round(x1, 9), common)
    i2 = np.isin(np.round(x2, 9), common)
    assert np.allclose(y1[i1], y2[i2])                       # with the same values: no shimmer
    assert len(curve.getOriginalDataset()[0]) == fs * 10     # the full trace is kept

    TraceCurve.mode = "peak"
    curve.refresh()
    _, yp = curve.getData()
    assert yp.max() > 3                                      # min/max keeps the extremes
    TraceCurve.mode = "mean"
    pw.close()
    pw.deleteLater()                                         # not left to the garbage collector,
    pump(app, 0.05)                                          # which crashes pyqtgraph later


def test_zoomed_out_trace_mode_is_a_remembered_menu_choice(app, win, tmp_path):
    from neusci_daq.gui import MainWindow
    from neusci_daq.plotzoom import TraceCurve
    assert win.act_trace_mode["mean"].isChecked()            # average by default
    win.act_trace_mode["peak"].trigger()
    assert TraceCurve.mode == "peak" and win.settings.value("trace_mode") == "peak"
    w2 = MainWindow(make_cfg(tmp_path), settings=win.settings)
    assert w2.act_trace_mode["peak"].isChecked()
    w2.close()
    w2.deleteLater()
    pump(app, 0.05)
    win.set_trace_mode("mean")


def test_zooming_time_out_doesnt_shrink_the_auto_scaled_voltage_axis(app, win):
    """Averaging shrinks noise when zoomed out; auto-scale must follow the samples,
    or the voltage axis zooms along with time."""
    from neusci_daq import simulation as sim
    win.resize(1200, 700)
    win.show()
    win.cfg.sim_network = sim.default_network()
    win.cfg.sim_network.recordings = [sim.Recording("ai0", 0, "extracellular", noise_v=0.05)]
    win.window_box.setValue(0.2)
    win.btn_run.setChecked(True)
    pump(app, 2.5)
    vb = win.plots[0].vb
    spans = []
    for w in (0.2, 2.0):
        win.window_box.setValue(w)
        pump(app, 0.4)
        lo, hi = vb.viewRange()[1]
        spans.append(hi - lo)
    win.btn_run.setChecked(False)
    assert spans[1] > 0.9 * spans[0]


def test_real_wheel_events_zoom_only_time(app, win):
    win.resize(1200, 700)
    win.show()
    pump(app, 0.1)
    pw = win.plot_widgets[0]
    vb = pw.getPlotItem().vb
    win.set_auto_scale_y(False)
    vb.setYRange(-1, 1, padding=0)
    pos = QtCore.QPointF(pw.width() / 2, pw.height() / 2)
    for pixel in (False, True):                              # a mouse wheel, then a trackpad
        before = win.window_box.value()
        d = 120 if not pixel else 12
        ev = QtGui.QWheelEvent(pos, pw.mapToGlobal(pos), QtCore.QPoint(0, d if pixel else 0), QtCore.QPoint(0, d),
                               QtCore.Qt.MouseButton.NoButton, QtCore.Qt.KeyboardModifier.NoModifier,
                               QtCore.Qt.ScrollPhase.NoScrollPhase, False)
        QtWidgets.QApplication.sendEvent(pw.viewport(), ev)
        pump(app, 0.05)
        assert win.window_box.value() < before
        assert vb.viewRange()[1] == pytest.approx([-1, 1])
    win.set_auto_scale_y(True)


class _Drag:
    """Enough of a pyqtgraph drag event for WheelViewBox.mouseDragEvent."""
    def __init__(self, vb, a, b, finish, mods):
        self._a, self._b, self._finish, self._mods = vb.mapViewToScene(pg.Point(*a)), vb.mapViewToScene(pg.Point(*b)), finish, mods
        self._vb = vb
    def button(self):
        return QtCore.Qt.MouseButton.LeftButton
    def modifiers(self):
        return self._mods
    def isFinish(self):
        return self._finish
    def buttonDownPos(self, *_):
        return self._vb.mapFromScene(self._a)
    def pos(self):
        return self._vb.mapFromScene(self._b)
    def accept(self):
        pass
    def ignore(self):
        pass


def _box_drag(vb, a, b, mods=QtCore.Qt.KeyboardModifier.ControlModifier):
    vb.mouseDragEvent(_Drag(vb, a, b, False, mods))
    vb.mouseDragEvent(_Drag(vb, a, b, True, mods))


def test_ctrl_drag_zooms_to_a_box_and_ctrl_click_goes_back(app):
    from neusci_daq.plotzoom import plot_widget
    pw = plot_widget()
    pw.resize(600, 400)
    pw.show()
    pw.plot(np.linspace(0, 10, 1000), np.sin(np.linspace(0, 10, 1000)))
    vb = pw.getPlotItem().vb
    pump(app, 0.05)
    vb.setRange(xRange=(0, 10), yRange=(-1, 1), padding=0)
    before = vb.viewRange()
    _box_drag(vb, (2, -0.5), (4, 0.5))
    (x0, x1), (y0, y1) = vb.viewRange()
    assert (x0, x1) == pytest.approx((2, 4), abs=0.02) and (y0, y1) == pytest.approx((-0.5, 0.5), abs=0.02)
    _box_drag(vb, (2.5, 0), (3, 0.25), mods=QtCore.Qt.KeyboardModifier.MetaModifier)   # Ctrl on a Mac
    assert vb.viewRange()[0] == pytest.approx([2.5, 3], abs=0.02)
    vb.zoom_back()
    vb.zoom_back()                                                   # Ctrl+click twice: back to the start
    assert vb.viewRange()[0] == pytest.approx(before[0], abs=0.02)
    pw.close()


def test_box_zoom_in_review_sets_window_and_position(app, win, tmp_path):
    win.resize(1200, 800)
    win.show()
    win.btn_run.setChecked(True)
    pump(app, 0.2)
    win.btn_rec.setChecked(True)
    pump(app, 1.2)
    win.btn_rec.setChecked(False)
    win.btn_run.setChecked(False)
    win.open_session(win.session_dir)
    pump(app, 0.1)
    vb = win.plots[0].vb
    y0, y1 = vb.viewRange()[1]
    lo, hi = y0 + 0.25 * (y1 - y0), y0 + 0.75 * (y1 - y0)            # a box inside the data
    _box_drag(vb, (0.2, lo), (0.6, hi))
    assert win.window_box.value() == pytest.approx(0.4, abs=0.01)
    assert win.plots[1].vb.viewRange()[0] == pytest.approx([0.2, 0.6], abs=0.01)   # every plot follows
    assert vb.viewRange()[1] == pytest.approx([lo, hi], rel=0.02)


def test_stacked_plots_line_up(app, win):
    win.resize(1200, 800)
    win.show()
    win.curves[0].setData(np.arange(10.0), np.full(10, -123456.0))   # long tick labels on one plot
    win.curves[1].setData(np.arange(10.0), np.full(10, 0.5))
    win.plots[0].enableAutoRange()
    pump(app, 0.3)
    xs = [p.vb.sceneBoundingRect().left() for p in win.plots]
    widths = [p.getAxis("left").width() for p in win.plots]
    assert max(widths) - min(widths) < 0.5 and max(xs) - min(xs) < 0.5
    raw = win.analysis_page.raw
    win.tabs.pick(0, "analysis")
    pump(app, 0.2)
    ws = [pw.getPlotItem().getAxis("left").width() for pw in (raw.overlay, raw.raster, raw.rate_plot)]
    assert max(ws) - min(ws) < 0.5


def test_charts_stop_at_the_edges_of_their_data(app):
    from neusci_daq.plotzoom import TraceCurve, plot_widget
    pw = plot_widget()
    pw.resize(600, 400)
    pw.show()
    c = TraceCurve()
    pw.getPlotItem().addItem(c)
    c.setData(np.linspace(1, 3, 500), np.sin(np.linspace(0, 20, 500)))
    vb = pw.getPlotItem().vb
    pump(app, 0.05)
    vb.setRange(xRange=(-5, 20), yRange=(-9, 9), padding=0)          # far past it
    (x0, x1), (y0, y1) = vb.viewRange()
    assert (x0, x1) == pytest.approx((1, 3)) and y0 >= -1.05 and y1 <= 1.05
    vb.setXRange(1.5, 2, padding=0)                                  # zoomed in, then panned off the end
    vb.translateBy(x=5)
    assert vb.viewRange()[0] == pytest.approx([2.5, 3])
    other = plot_widget()                                            # a linked plot follows, whatever it holds
    other.resize(600, 200)
    other.show()
    other.plot([2.0, 2.2], [0, 1])
    other.setXLink(pw)
    vb.setXRange(1.2, 2.8, padding=0)
    pump(app, 0.05)
    assert other.getPlotItem().vb.viewRange()[0] == pytest.approx([1.2, 2.8], abs=0.05)
    other.close()
    pw.close()


@pytest.mark.parametrize("scale", [1.0, 1.15])
@pytest.mark.parametrize("tab", ["analysis", "sim"])
def test_tall_tabs_fit_a_short_laptop_screen(app, tmp_path, tab, scale):
    """A 1280 × 800 laptop at 125% leaves about 1024 × 560 for the window. Analysis used to
    force it to 1,158 × 935 (one long message and a tall settings column); now the
    settings scroll and the messages wrap. `scale` widens the font as macOS's is: the
    Analysis Blocks panel once left 4 px to spare here and didn't fit on the Mac runner.
    Built as Argus starts (font and stylesheet first, then the window): styling a window
    that already exists leaves some sizes as they were, and hid that."""
    from pyqtgraph.Qt import QtCore
    from neusci_daq.gui import MainWindow
    old_sheet, old_pal, old_font = app.styleSheet(), app.palette(), app.font()
    f = QtGui.QFont(old_font)
    f.setPointSizeF(old_font.pointSizeF() * scale)
    app.setFont(f)
    style.apply(app, style.DARK)
    st = QtCore.QSettings(str(tmp_path / "s.ini"), QtCore.QSettings.Format.IniFormat)
    win = MainWindow(make_cfg(tmp_path), settings=st, config_path=tmp_path / "config.toml")
    win._ask_unsaved = lambda: "discard"
    try:
        if tab == "sim" and not win.sim_page:
            pytest.skip("no Simulation tab with this backend")
        win.resize(1024, 560)
        win.show()
        win.tabs.pick(0, tab)
        pump(app, 0.2)
        assert (win.width(), win.height()) == (1024, 560)
        page = win.analysis_page if tab == "analysis" else win.sim_page
        scrolls = [s for s in page.findChildren(QtWidgets.QScrollArea)
                   if s.isVisible() and s.verticalScrollBar().maximum() > 0]
        assert scrolls, "the settings should scroll, not be cut off"
    finally:
        win.close()
        win.deleteLater()
        pump(app, 0.05)
        style._applied["mode"] = None
        app.setFont(old_font)
        app.setStyleSheet(old_sheet)
        app.setPalette(old_pal)
