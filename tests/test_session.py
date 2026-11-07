"""Sessions on disk, plus the File menu's New / Open / Append / Export / Recent."""
import time

import numpy as np
import pytest

from neusci_daq import config, session


def make_cfg(tmp_path, n=2, rate=2000, scale=1000.0):
    chans = [config.Channel(f"ai{i}", f"Ch{i}", "mV", scale=scale) for i in range(n)]
    return config.Config(backend="simulated", sample_rate=rate, chunk_size=100,
                         channels=chans, data_dir=str(tmp_path / "data"))


# ---------------------------------------------------------------- GUI helpers
@pytest.fixture
def app():
    import pyqtgraph as pg
    return pg.mkQApp()


@pytest.fixture
def settings(tmp_path):
    from pyqtgraph.Qt import QtCore
    return QtCore.QSettings(str(tmp_path / "settings.ini"), QtCore.QSettings.Format.IniFormat)


def pump(app, s):
    end = time.time() + s
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def record_blocks(app, w, n, secs=0.4, comment=None):
    w.btn_run.setChecked(True)
    pump(app, 0.2)
    for i in range(n):
        w.btn_rec.setChecked(True)
        pump(app, secs)
        if comment and i == 0:
            w.comment_edit.setText(comment)
            w._add_comment()
            pump(app, 0.1)
        w.btn_rec.setChecked(False)
        pump(app, 0.1)
    w.btn_run.setChecked(False)


# ---------------------------------------------------------------- pure helpers
def test_list_blocks_orders_by_number_and_ignores_others(tmp_path):
    for name in ["block_010.h5", "block_002.h5", "block_001.h5", "notes.txt", "block_x.h5", "s.mat"]:
        (tmp_path / name).write_bytes(b"")
    assert [p.name for p in session.list_blocks(tmp_path)] == ["block_001.h5", "block_002.h5", "block_010.h5"]
    assert session.next_block_path(tmp_path).name == "block_011.h5"   # never reuses a gap
    assert session.next_block_path(tmp_path / "empty").name == "block_001.h5"
    assert not session.is_session(tmp_path / "empty")


def test_add_recent_dedupes_and_limits(tmp_path):
    r = []
    for i in range(10):
        r = session.add_recent(r, tmp_path / f"s{i}")
    r = session.add_recent(r, tmp_path / "s5")
    assert len(r) == 8 and r[0].endswith("s5") and len(set(r)) == 8


# ---------------------------------------------------------------- GUI flows
def test_record_save_open_append_export_recent(tmp_path, app, settings):
    import neuscitk
    from neusci_daq.gui import MainWindow, REVIEW, LIVE

    cfg = make_cfg(tmp_path)
    w = MainWindow(cfg, settings=settings)

    # Record without naming anything: blocks go straight to data/unsaved/
    record_blocks(app, w, 2, comment="TTX applied")
    unsaved = w.session_dir
    assert unsaved.parent.name == "unsaved" and not w.session_saved
    assert "Untitled session*" in w.windowTitle()
    assert len(session.list_blocks(unsaved)) == 2

    # First Save (as): moves the folder, writes the .mat, remembers it
    sess = w.save_as(tmp_path / "my_prep")
    assert not unsaved.exists() and w.session_saved
    assert [p.name for p in session.list_blocks(sess)] == ["block_001.h5", "block_002.h5"]
    assert len(neuscitk.LabChartDataset(str(sess / "my_prep.mat")).data["Channel 1"]) == 2
    assert w.recent_sessions()[0] == str(sess)
    assert "my_prep — Live" in w.windowTitle()

    # Keep recording into the saved session; Save updates the .mat
    record_blocks(app, w, 1)
    assert [p.name for p in session.list_blocks(sess)][-1] == "block_003.h5"
    assert w.save()
    assert len(neuscitk.LabChartDataset(str(sess / "my_prep.mat")).data["Channel 1"]) == 3

    # Save As refuses a non-empty folder; a copy keeps the original
    (tmp_path / "taken").mkdir()
    (tmp_path / "taken" / "x.txt").write_text("x")
    with pytest.raises(session.SessionError, match="isn't empty"):
        w.save_as(tmp_path / "taken")
    copy = w.save_as(tmp_path / "my_prep_copy")
    assert session.list_blocks(sess) and len(session.list_blocks(copy)) == 3
    assert (copy / "my_prep_copy.mat").exists() and not (copy / "my_prep.mat").exists()

    # Open: review mode shows block 1 with its comment
    w.open_session(sess)
    assert w.mode == REVIEW and w.block_combo.count() == 3
    blk = session.read_block(sess / "block_001.h5")
    x, y = w.curves[0].getOriginalDataset()
    assert len(x) == blk["data"].shape[1]
    np.testing.assert_allclose(y, blk["data"][0])
    assert len(w.markers) == 1 and blk["comments"][0][1] == "TTX applied"
    w.block_combo.setCurrentIndex(1)
    assert len(w.markers) == 0  # block 2 has no comments

    # scrollbar moves the view
    w.window_box.setValue(0.1)
    w.scroll.setValue(w.scroll.maximum())
    lo, hi = w.plots[0].viewRange()[0]
    assert hi == pytest.approx(blk_dur := session.block_info(sess / "block_002.h5")["duration_s"], abs=0.01)

    # Export from review
    out = w.export(tmp_path / "review.mat")
    assert len(neuscitk.LabChartDataset(str(out)).data["Channel 1"]) == 3

    # Append: back to live, new block continues numbering, old blocks untouched
    before = (sess / "block_001.h5").stat().st_mtime
    w.append_session(sess)
    assert w.mode == LIVE and w.session_saved
    record_blocks(app, w, 1)
    assert [p.name for p in session.list_blocks(sess)][-1] == "block_004.h5"
    assert (sess / "block_001.h5").stat().st_mtime == before
    assert w.close()  # saved session: no prompt, auto-exports
    ds = neuscitk.LabChartDataset(str(sess / "my_prep.mat"))
    assert len(ds.data["Channel 1"]) == 4

    # Recent sessions remembered across windows
    w2 = MainWindow(cfg, settings=settings)
    assert str(sess) in w2.recent_sessions()
    w2.close()


def test_append_refuses_mismatched_config(tmp_path, app, settings):
    from neusci_daq.gui import MainWindow

    w = MainWindow(make_cfg(tmp_path), settings=settings)
    record_blocks(app, w, 1)
    sess = w.save_as(tmp_path / "s")
    w.close()

    problems, _ = session.append_check(sess, make_cfg(tmp_path, n=3))
    assert any("channels differ" in p for p in problems)
    problems, _ = session.append_check(sess, make_cfg(tmp_path, rate=4000))
    assert any("Sample rate" in p for p in problems)
    problems, warnings = session.append_check(sess, make_cfg(tmp_path, scale=10000.0))
    assert not problems and any("gain" in x for x in warnings)

    w2 = MainWindow(make_cfg(tmp_path, scale=10000.0), settings=settings)
    with pytest.raises(session.SessionError):
        w2.append_session(sess)            # warning needs confirmation
    w2.append_session(sess, force=True)    # confirmed
    w2.close()


def test_open_recent_drops_missing(tmp_path, app, settings):
    from neusci_daq.gui import MainWindow
    from pyqtgraph.Qt import QtWidgets

    w = MainWindow(make_cfg(tmp_path), settings=settings)
    settings.setValue("recent_sessions", [str(tmp_path / "gone")])
    orig = QtWidgets.QMessageBox.warning
    QtWidgets.QMessageBox.warning = staticmethod(lambda *a, **k: None)
    try:
        w.open_recent(str(tmp_path / "gone"))
    finally:
        QtWidgets.QMessageBox.warning = orig
    assert w.recent_sessions() == []
    w.close()


def test_review_handles_session_with_different_channels(tmp_path, app, settings):
    """Open works even when the session's channels differ from the current config."""
    from neusci_daq.gui import MainWindow

    w = MainWindow(make_cfg(tmp_path, n=3), settings=settings)
    record_blocks(app, w, 1)
    sess = w.save_as(tmp_path / "three")
    w.close()

    w2 = MainWindow(make_cfg(tmp_path, n=1), settings=settings)
    w2.open_session(sess)
    assert len(w2.plots) == 3
    w2.back_to_live()
    assert len(w2.plots) == 1
    w2.close()


def test_unsaved_session_prompts_before_leaving(tmp_path, app, settings):
    from neusci_daq.gui import MainWindow

    w = MainWindow(make_cfg(tmp_path), settings=settings)
    record_blocks(app, w, 1)
    unsaved = w.session_dir

    w._ask_unsaved = lambda: "cancel"
    assert not w.new_session() and w.session_dir == unsaved   # nothing changed
    assert w.close() is False                                  # window stays open

    w._ask_unsaved = lambda: "discard"
    assert w.new_session() and w.session_dir is None
    assert session.list_blocks(unsaved)                        # "Don't Save" deletes nothing
    assert w.close()


def test_save_without_recording_does_nothing(tmp_path, app, settings):
    from neusci_daq.gui import MainWindow

    w = MainWindow(make_cfg(tmp_path), settings=settings)
    assert w.save() is False
    with pytest.raises(session.SessionError, match="Nothing"):
        w.save_as(tmp_path / "x")
    w.close()


def test_restart_discards_leftover_chunks_from_previous_run(tmp_path, app, settings):
    """After Stop then Start, data left over from the old run must not be drawn.

    Each run's timestamps start again from 0, so an old chunk (with large
    timestamps) would be drawn on top of the new run, joined to it by a long line.
    """
    from neusci_daq.backends import Chunk
    from neusci_daq.gui import MainWindow

    w = MainWindow(make_cfg(tmp_path), settings=settings)
    w.btn_run.setChecked(True)
    pump(app, 0.3)
    w.btn_run.setChecked(False)
    # A chunk from the old run, still waiting when Start is pressed again.
    n = len(w.cfg.channels)
    w.display_q.put(Chunk(np.zeros((n, 100)), 100.0 + np.arange(100) / w.cfg.sample_rate, 200_000))
    w.btn_run.setChecked(True)
    pump(app, 0.3)
    w.btn_run.setChecked(False)
    assert w.buf_t.size, "nothing was acquired after restarting"
    assert np.all(np.diff(w.buf_t) > 0), "time went backwards in the display buffer"
    assert w.buf_t.max() < 100, "data from the previous run was drawn"
    w.close()

def _panel(win):
    """The Blocks panel as data: [(top-level data, [child data])]."""
    from pyqtgraph.Qt import QtCore
    role = QtCore.Qt.ItemDataRole.UserRole
    t = win.blocks_panel.tree
    return [(t.topLevelItem(r).data(0, role),
             [t.topLevelItem(r).child(c).data(0, role) for c in range(t.topLevelItem(r).childCount())])
            for r in range(t.topLevelItemCount())]


def _item(win, data):
    from pyqtgraph.Qt import QtCore
    return next(it for it in win.blocks_panel.items() if it.data(0, QtCore.Qt.ItemDataRole.UserRole) == data)


def test_blocks_panel_browses_and_returns_to_the_same_session(app, settings, tmp_path):
    from neusci_daq.gui import LIVE, REVIEW, MainWindow
    win = MainWindow(make_cfg(tmp_path), settings=settings, config_path=tmp_path / "config.toml")
    win._ask_unsaved = lambda: "discard"
    assert _panel(win) == [("live", [])] and win.blocks_panel.tree.isEnabled()
    assert not win.blocks_panel.stim_ch.isEnabled()                               # no session yet
    win.btn_run.setChecked(True)
    pump(app, 0.2)
    for _ in range(2):
        win.btn_rec.setChecked(True)
        pump(app, 0.3)
        assert not win.blocks_panel.tree.isEnabled()                        # not while recording
        win.btn_rec.setChecked(False)
    assert _panel(win) == [("live", []), (("page", "All trials"), [("block", 0), ("block", 1)])]
    assert win.blocks_panel.stim_ch.isEnabled()
    sess = win.session_dir
    win._activate(("block", 1))                # the second block
    assert win.mode == REVIEW and win.review_dir == sess and win.block_combo.currentIndex() == 1
    assert _panel(win)[0][0] == "back"
    assert str(sess) not in win.recent_sessions()                    # an untitled session isn't "recent"
    win._activate(("block", 0))
    assert win.block_combo.currentIndex() == 0
    win._activate("back")                      # Back to Live
    assert win.mode == LIVE and win.session_dir == sess and len(win.blocks) == 2
    win.btn_run.setChecked(True)
    pump(app, 0.2)
    win.btn_rec.setChecked(True)                                     # carries on in the same session
    pump(app, 0.3)
    win.btn_rec.setChecked(False)
    win.btn_run.setChecked(False)
    assert win.session_dir == sess and len(win.blocks) == 3 and len(_panel(win)[1][1]) == 3
    win.open_session(sess)                                           # a plain Open isn't "browsing"
    assert _panel(win)[0][0] == ("page", "All trials")
    win.back_to_live()
    assert win.session_dir is None and not win.blocks                # so it starts afresh, as before
    win.close()


def test_pages_are_named_blocks_take_their_name_and_can_move(app, settings, tmp_path):
    from neusci_daq import pages
    from neusci_daq.gui import MainWindow
    win = MainWindow(make_cfg(tmp_path), settings=settings, config_path=tmp_path / "config.toml")
    win._ask_unsaved = lambda: "discard"
    win.btn_run.setChecked(True)
    pump(app, 0.2)
    for _ in range(3):
        win.btn_rec.setChecked(True)
        pump(app, 0.25)
        win.btn_rec.setChecked(False)
    win.btn_run.setChecked(False)
    win.rename_page("All trials", "Baseline")
    texts = [it.text(0) for it in win.blocks_panel.items()]
    assert texts[1].startswith("Baseline  (3)") and texts[2].startswith("Baseline 1")
    assert texts[4].startswith("Baseline 3")
    win.move_block(2, "Drug")                                        # into a new page
    texts = [it.text(0) for it in win.blocks_panel.items()]
    assert any(t.startswith("Drug  (1)") for t in texts) and any(t.startswith("Drug 1") for t in texts)
    saved = pages.load(win.session_dir)
    assert saved["renamed"] == {"All trials": "Baseline"} and saved["moved"] == {"block_003": "Drug"}
    win.rename_page("Drug", "Drug A")                                # its block follows
    assert pages.load(win.session_dir)["moved"] == {"block_003": "Drug A"}
    win.move_block(2, "Baseline")                                    # back home: no longer "moved"
    assert pages.load(win.session_dir)["moved"] == {}
    win.move_block(0, "Somewhere")
    assert win.blocks_panel.btn_reset.isEnabled()
    win.reset_pages()                                                # forget names and moves
    saved = pages.load(win.session_dir)
    assert saved["renamed"] == {} and saved["moved"] == {}
    assert [it.text(0) for it in win.blocks_panel.items()][1].startswith("All trials  (3)")
    assert not win.blocks_panel.btn_reset.isEnabled()
    win.close()


def test_selected_blocks_are_overlaid(app, settings, tmp_path):
    from neusci_daq.gui import REVIEW, MainWindow
    win = MainWindow(make_cfg(tmp_path), settings=settings, config_path=tmp_path / "config.toml")
    win._ask_unsaved = lambda: "discard"
    win.show()
    win.btn_run.setChecked(True)
    pump(app, 0.2)
    for _ in range(3):
        win.btn_rec.setChecked(True)
        pump(app, 0.25)
        win.btn_rec.setChecked(False)
    win.btn_run.setChecked(False)
    _item(win, ("block", 0)).setSelected(True)                       # Cmd-click two blocks, from live
    _item(win, ("block", 2)).setSelected(True)
    pump(app, 0.05)
    assert win.mode == REVIEW and win._overlay_sel == [0, 2]
    assert len(win._overlay_items) == len(win.plots)                 # one overlay per channel plot
    rows = win._overlay_items[0][1].getOriginalDataset()[1].shape[0]
    assert rows == 2
    assert _item(win, ("block", 0)).isSelected() and _item(win, ("block", 2)).isSelected()
    win.step_block(1)                                                # stepping keeps the overlay
    assert len(win._overlay_items) == len(win.plots)
    win.blocks_panel.tree.clearSelection()
    _item(win, ("page", "All trials")).setSelected(True)                 # a whole page
    pump(app, 0.05)
    assert win._overlay_sel == [0, 1, 2]
    win.blocks_panel.tree.clearSelection()
    _item(win, ("block", 1)).setSelected(True)                       # one block: no overlay
    pump(app, 0.05)
    assert win._overlay_sel == [] and win._overlay_items == []
    win.close()



def test_channels_can_be_overlaid_in_one_plot(app, settings, tmp_path):
    from neusci_daq.gui import MainWindow
    win = MainWindow(make_cfg(tmp_path, n=3), settings=settings, config_path=tmp_path / "config.toml")
    win._ask_unsaved = lambda: "discard"
    win.resize(1200, 800)
    win.show()
    win.btn_run.setChecked(True)
    pump(app, 0.3)
    win.btn_overlay_ch.setChecked(True)                              # live
    pump(app, 0.3)
    first = win.plots[0]
    assert len(win._mirrors) == 2                                    # each on its own y axis
    assert [m.getViewBox() for _, m in win._mirrors] == win._multi._extras() and len(win._multi.axes) == 2
    assert all(vb.state["autoRange"][1] for vb in win._multi._extras())   # fitted to the height
    win.set_overlay_axes(False)                                      # View: one shared axis
    pump(app, 0.2)
    assert all(m.getViewBox() is win.plots[0].vb for _, m in win._mirrors) and not win._multi.axes
    win.set_overlay_axes(True)
    pump(app, 0.2)
    assert not win.plot_widgets[1].isVisible() and win.strips[1].isVisible()   # controls stay
    x, y = win._mirrors[0][1].getOriginalDataset()
    assert len(x) > 0 and np.array_equal(y, win.curves[1].getOriginalDataset()[1])   # follows the data
    win.btn_rec.setChecked(True)
    pump(app, 0.3)
    win.btn_rec.setChecked(False)
    win.btn_run.setChecked(False)
    win.open_session(win.session_dir)                                # review: overlaid too
    pump(app, 0.1)
    assert len(win._mirrors) == 2 and not win.plot_widgets[2].parentWidget().isVisible()
    assert settings.value("overlay_channels", type=bool) is True     # remembered
    win.btn_overlay_ch.setChecked(False)
    pump(app, 0.1)
    assert win._mirrors == [] and win.plot_widgets[2].isVisible()
    win.close()


def _recorded_session(app, win, n=2):
    win.btn_run.setChecked(True)
    pump(app, 0.2)
    for _ in range(n):
        win.btn_rec.setChecked(True)
        pump(app, 0.3)
        win.btn_rec.setChecked(False)
        pump(app, 0.05)
    win.btn_run.setChecked(False)
    folder = win.session_dir
    win.save_as(folder.parent.parent / "saved_session")
    return win.session_dir


def test_back_to_live_from_an_opened_session_asks_and_can_add_to_it(app, settings, tmp_path):
    from neusci_daq.gui import MainWindow
    win = MainWindow(make_cfg(tmp_path), settings=settings, config_path=tmp_path / "config.toml")
    win._ask_unsaved = lambda: "discard"
    folder = _recorded_session(app, win)
    win.new_session()
    win.open_session(folder)                                         # a dataset loaded from disk
    asked = []
    win._ask_leave_review = lambda path: asked.append(path) or None  # Cancel: stay in review
    win._activate("live")
    assert asked == [folder] and win.mode == "review"
    win._ask_leave_review = lambda path: "append"                    # add to it
    win._activate("live")
    assert win.mode == "live" and win.session_dir == folder and len(win.blocks) == 2
    win.btn_run.setChecked(True)
    pump(app, 0.2)
    win.btn_rec.setChecked(True)
    pump(app, 0.3)
    win.btn_rec.setChecked(False)
    win.btn_run.setChecked(False)
    assert [b.name for b in session.list_blocks(folder)] == ["block_001.h5", "block_002.h5", "block_003.h5"]
    win.open_session(folder)
    win._ask_leave_review = lambda path: "new"                       # or a new session: nothing lost
    win._activate("live")
    assert win.mode == "live" and win.session_dir is None and len(session.list_blocks(folder)) == 3
    win.close()


def test_adding_to_a_recording_made_with_another_setup_can_take_its_setup(app, settings, tmp_path):
    from neusci_daq.gui import MainWindow
    from neusci_daq.channels import ChannelRow
    from neusci_daq.config import Channel
    win = MainWindow(make_cfg(tmp_path), settings=settings, config_path=tmp_path / "config.toml")
    win._ask_unsaved = lambda: "discard"
    folder = _recorded_session(app, win)
    mat = win.export()                                               # as a LabChart .mat ...
    win.new_session()
    win.apply_setup(rows=[ChannelRow(Channel("ai0", "Other"))], sample_rate=1000)   # ... then a new setup
    imported = win.open_mat(mat)
    win._ask_leave_review = lambda path: "append"
    win._ask_adopt_setup = lambda path, problems: False             # No: stay
    assert not win.leave_review() and win.mode == "review"
    win._ask_adopt_setup = lambda path, problems: True
    assert win.leave_review()
    assert [c.name for c in win.cfg.channels] == ["Ch0", "Ch1"] and win.cfg.sample_rate == 2000
    assert win.mode == "live" and win.session_dir == imported and len(win.blocks) == 2
    win.close()

