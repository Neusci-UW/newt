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
