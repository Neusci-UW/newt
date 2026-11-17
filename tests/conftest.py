import os

import pytest

# GUI tests run without a display.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def _no_dialog_about_analysis(monkeypatch):
    """Closing a window in a test doesn't stop to ask about unsaved analysis."""
    from neusci_daq.gui import MainWindow
    monkeypatch.setattr(MainWindow, "_ask_unsaved_analysis", lambda self: "discard")
    monkeypatch.setattr(MainWindow, "_ask_unsaved_file", lambda self: "discard")


@pytest.fixture(autouse=True)
def _no_waiting_dialogs(monkeypatch):
    """A file dialog or message box nobody patched fails the test at once, instead of waiting
    forever for a click that never comes."""
    from pyqtgraph.Qt import QtWidgets

    def stuck(name):
        def fail(*args, **kwargs):
            raise AssertionError(f"{name} would wait for a click: patch it in the test")
        return staticmethod(fail)
    for cls, names in ((QtWidgets.QFileDialog, ("getSaveFileName", "getOpenFileName", "getExistingDirectory")),
                       (QtWidgets.QMessageBox, ("information", "warning", "critical", "question"))):
        for n in names:
            monkeypatch.setattr(cls, n, stuck(f"{cls.__name__}.{n}"))


@pytest.fixture(autouse=True)
def _free_windows(_no_dialog_about_analysis, _no_waiting_dialogs):
    """Delete every window a test leaves behind. Closing only hides a window: each one kept
    its plots and timers alive, so the suite grew ~35 MB and a little slower with every test
    (4.5 s alone, 36 s at test 170). Runs before the dialog patches above are undone."""
    yield
    import gc
    from pyqtgraph.Qt import QtCore, QtWidgets
    app = QtWidgets.QApplication.instance()
    if app is None:
        return
    from neusci_daq.gui import MainWindow
    for w in app.topLevelWidgets():
        if isinstance(w, MainWindow):            # its plots and pages go with it
            if w.isVisible():
                w.close()
            w.deleteLater()
    QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
    app.processEvents()
    gc.collect()
