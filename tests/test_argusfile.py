"""The .newt file: a whole session in one file, saved and opened again."""
import json
import os
import zipfile

import numpy as np
import pytest

from neusci_daq import argusfile, session
from neusci_daq.argusfile import ArgusError

from test_channels import app, make_cfg, pump, win  # noqa: F401  (fixtures)


def _folder(tmp_path, n=2):
    f = tmp_path / "sess"
    f.mkdir()
    for i in range(1, n + 1):
        (f / f"block_{i:03d}.h5").write_bytes(os.urandom(1000))
    (f / "pages.json").write_text("{}")
    (f / "sess.mat").write_bytes(b"remade")                         # left out
    return f


def test_a_folder_packs_and_unpacks_the_same(tmp_path):
    f = _folder(tmp_path)
    path = tmp_path / "lab.newt"
    files = argusfile.save(path, f, {"hello": 1})
    assert set(files) == {"block_001.h5", "block_002.h5", "pages.json"}
    folder, state, back = argusfile.open_file(path, tmp_path / "data")
    assert state == {"hello": 1} and back == files and folder.parent == tmp_path / "data" / "open"
    for name in files:
        assert (folder / name).read_bytes() == (f / name).read_bytes()
    assert argusfile.files_of(folder) == files                      # unchanged, as far as a later save can tell
    again, _, _ = argusfile.open_file(path, tmp_path / "data")      # opened again: the folder is reused
    assert again == folder


def test_saving_again_adds_only_what_changed(tmp_path):
    f = _folder(tmp_path, 3)
    path = tmp_path / "lab.newt"
    argusfile.save(path, f, {})
    (f / "pages.json").write_text('{"renamed": {"a": "b"}}')
    (f / "block_003.h5").unlink()
    (f / "block_004.h5").write_bytes(os.urandom(500))
    argusfile.save(path, f, {"v": 2})
    with zipfile.ZipFile(path) as z:
        names = [i.filename for i in z.infolist()]
    assert names.count("session/block_001.h5") == 1                 # not written twice
    assert names.count("session/pages.json") == 2                   # the newest copy counts
    folder, state, files = argusfile.open_file(path, tmp_path / "data")
    assert state == {"v": 2} and "block_003.h5" not in files and (folder / "block_004.h5").exists()
    assert json.loads((folder / "pages.json").read_text()) == {"renamed": {"a": "b"}}
    assert not (folder / "block_003.h5").exists()


def test_a_file_mostly_old_copies_is_written_again(tmp_path):
    f = _folder(tmp_path, 1)
    path = tmp_path / "lab.newt"
    for k in range(4):
        (f / "block_001.h5").write_bytes(os.urandom(5000))
        argusfile.save(path, f, {"k": k})
    with zipfile.ZipFile(path) as z:
        assert [i.filename for i in z.infolist()].count("session/block_001.h5") <= 2


def test_bad_files_say_so(tmp_path):
    with pytest.raises(ArgusError):
        argusfile.save(tmp_path / "x.newt", tmp_path, {})        # nothing recorded
    (tmp_path / "bad.newt").write_text("not a zip")
    with pytest.raises(ArgusError):
        argusfile.open_file(tmp_path / "bad.newt", tmp_path)


def test_save_reopens_argus_as_it_was(app, win, tmp_path):
    from neusci_daq.gui import MainWindow
    from neusci_daq.stim import Stimulus
    from test_analysis import stim_session
    stim_session(app, win)
    win.show()
    win.tabs.pick(0, "analysis")
    pump(app, 0.2)
    ap = win.analysis_page
    ap.raw.channel.setCurrentText("Ch2")
    ev = ap.raw.find()
    ap.canvas.add("Response")
    ap.canvas.copy(ap.chart("Response")).name_box.setText("Mine")
    place = ap.chart("Response").dock.place.topLeft()
    ap.canvas.here()[-1]._renamed()
    win.set_stimulus(Stimulus(shape="sine", amplitude_v=0.5, freq_hz=7, duration_ms=300))
    from neusci_daq.tabs import SIDE
    win.tabs.set_layout(SIDE)
    path = tmp_path / "lab.newt"
    win.save_argus(path)
    assert path.exists() and not win._has_unsaved() and win._argus() == path.resolve()
    assert "lab.newt" in win.windowTitle()
    ap.set_page_color(ap.shown[0][0], "#123456")                    # a change since: unsaved
    assert win._has_unsaved()
    asked = []
    win._ask_unsaved_file = lambda: asked.append(1) or "cancel"
    assert not win._resolve_unsaved() and asked
    win.save()                                                      # into the same file, no dialog
    assert ap.raw.events is not None                                # a search waiting to run ran first
    assert not win._has_unsaved()
    w2 = MainWindow(make_cfg(tmp_path / "other"), settings=win.settings)
    w2._ask_unsaved = lambda: "discard"
    w2.show()
    w2.open_file(path)
    pump(app, 0.3)
    a2 = w2.analysis_page
    assert "Couldn't" not in w2.statusBar().currentMessage(), w2.statusBar().currentMessage()
    assert w2.mode == "review" and len(w2.review_blocks) == len(session.list_blocks(win.session_dir))
    assert w2.stimulus.freq_hz == 7 and w2.tabs.layout_mode == SIDE
    assert a2.canvas.space.name == "Board" and "Mine" in [c.name for c in a2.canvas.here()]
    assert a2.chart("Response").dock.place.topLeft() == place          # where it was on the board
    assert a2.raw.events is not None and len(a2.raw.events.t) == len(ev.t)
    assert a2.group_color(0) == "#123456"
    assert not w2._has_unsaved()
    w2.close()
