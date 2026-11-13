"""The Analysis canvas: an endless whiteboard of charts (see board.py). It starts empty and
nothing is put on it for you: you choose every chart and where it goes. + Add chart, or
right-click empty space, opens any chart, as many times as you like. Click a chart for its
settings, beside the board; shift-click or drag a box to pick several and change them
together.

A layout is what's on the board, where, and as big as you made it, and the view. The board
you work on is "Board"; Save… keeps it as a layout of your own, to come back to. Each layout
keeps its charts while you look at another, and how you left it is remembered.
"""
from __future__ import annotations

import json
from pathlib import Path

from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

from . import board as B
from . import figure
from . import style
from .measurecharts import DataPadTableChart, ScatterChart
from .plotzoom import AxisGroup
from .responsecharts import (FitChart, LoopChart, PhaseChart, PsthChart, ResponseChart, ResponseCycleChart,
                             SpikeCycleChart, StaChart, StimulusCycleChart)
from .spikecharts import IntervalsChart, PcaChart, RasterChart, RateChart, SizesChart, StimulusChart, WaveformsChart
from .waveformview import PhasePlotChart, SlopeChart, VoltsChart

CHARTS = (StimulusChart, RasterChart, RateChart, PcaChart, WaveformsChart, SizesChart, IntervalsChart,
          ResponseChart, PsthChart, StaChart, StimulusCycleChart, ResponseCycleChart, SpikeCycleChart, PhaseChart,
          LoopChart, FitChart, PhasePlotChart, VoltsChart, SlopeChart, DataPadTableChart, ScatterChart)
KINDS = {c.kind: c for c in CHARTS}
# the Add chart menu's headings: what runs along each chart's x axis (its y axis is beside its name)
GROUPS = {"Time in the trial (s)": ("Response", "Stimulus", "Stimulus fit", "Raster", "Rate"),
          "Time from an event (ms)": ("Stimulus-triggered rate", "Spike-triggered average", "Waveforms", "V",
                                      "dV/dt"),
          "Phase of the cycle (°)": ("Stimulus cycle", "Response by cycle", "Spikes by cycle", "Spikes per phase"),
          "One signal against another": ("Response against stimulus", "V against dV/dt"),
          "A spike property (count)": ("AP sizes", "Intervals", "PCA map"),
          "One value per trial": ("DataPad table", "Scatter")}
BOARD = "Board"                    # the board you work on; Save… keeps it as a layout of your own
SIZE = {"DataPad table": (560, 260), "Response": (560, 320)}     # (w, h) a new chart starts at; else 400 × 280


class _Toolbar(QtWidgets.QFrame):
    """The board's tools, floating along its bottom."""

    def __init__(self, canvas: "ChartCanvas"):
        super().__init__()
        self.setObjectName("boardBar")
        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(6, 4, 6, 4)
        row.setSpacing(4)
        self.tools = QtWidgets.QButtonGroup(self)
        self.btn = {}
        for key, text, tip in ((B.SELECT, "Select", "Pick charts: click, shift-click, or drag a box over empty space"),
                               (B.HAND, "Hand", "Drag the board to move around it (or scroll, or drag with the "
                                                "middle button)"),
                               (B.MEASURE, "Measure", "Drag across any chart to measure part of it: a stretch of "
                                                      "time, ms from each event, part of each cycle (Phase), or a "
                                                      "range of values. On the PCA map and Scatter, draw around "
                                                      "the points. The numbers go in a DataPad table"),
                               (B.EXPORT, "Export", "Drag a box anywhere, over the charts too, to save what's in it "
                                                    "as a figure: PDF, SVG, PNG or TIFF, or copy it. Click to export "
                                                    "the last box again")):
            b = QtWidgets.QToolButton(text=text, checkable=True, autoRaise=True)
            b.setToolTip(tip)
            self.tools.addButton(b)
            self.btn[key] = b
            row.addWidget(b)
        self.btn[B.SELECT].setChecked(True)
        self.unit = QtWidgets.QButtonGroup(self)
        self.btn_time = QtWidgets.QToolButton(text="Time", checkable=True, autoRaise=True)
        self.btn_time.setToolTip("Measure windows in time: on charts against time, such as Response, or in ms "
                                 "from each event, on the triggered and spike charts")
        self.btn_phase = QtWidgets.QToolButton(text="Phase", checkable=True, autoRaise=True)
        self.btn_phase.setToolTip("Measure the same part of every stimulus cycle (degrees): on charts against "
                                  "phase, such as Response by cycle, or drag across one cycle of a chart against "
                                  "time")
        for b in (self.btn_time, self.btn_phase):
            self.unit.addButton(b)
            row.addWidget(b)
        self.btn_time.setChecked(True)
        row.addSpacing(8)
        self.btn_add = QtWidgets.QPushButton("+ Add chart")
        self.btn_add.setToolTip("Put another chart on the board (or right-click empty space). Any chart can be "
                                "open more than once")
        self.btn_add.setMenu(canvas.add_menu)
        row.addWidget(self.btn_add)
        self.btn_tidy = QtWidgets.QToolButton(text="Tidy up", autoRaise=True)
        self.btn_tidy.setToolTip("Line the charts up in rows, keeping their sizes")
        row.addWidget(self.btn_tidy)
        row.addSpacing(8)
        self.btn_out = QtWidgets.QToolButton(text="−", autoRaise=True)
        self.btn_out.setToolTip("Zoom out (⌘/Ctrl and scroll, or pinch)")
        self.zoom = QtWidgets.QLabel("100%")
        self.zoom.setMinimumWidth(40)
        self.zoom.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.btn_in = QtWidgets.QToolButton(text="+", autoRaise=True)
        self.btn_in.setToolTip("Zoom in")
        self.btn_fit = QtWidgets.QToolButton(text="Fit", autoRaise=True)
        self.btn_fit.setToolTip("Show every chart")
        for w in (self.btn_out, self.zoom, self.btn_in, self.btn_fit):
            row.addWidget(w)
        row.addSpacing(8)
        self.btn_grid = QtWidgets.QToolButton(text="Grid", checkable=True, autoRaise=True)
        self.btn_grid.setToolTip("Show or hide the board's dots. The charts still snap to them when hidden")
        self.btn_grid.setChecked(True)
        row.addWidget(self.btn_grid)
        self.btn_color = QtWidgets.QToolButton(autoRaise=True)
        self.btn_color.setToolTip("The board's color: click to choose it, right-click to go back to the default. "
                                  "Kept on this computer, for light and dark mode each")
        self.btn_color.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        row.addWidget(self.btn_color)

    def show_color(self, color: str, edge: str) -> None:
        """The swatch: the board's color, outlined so it shows on the toolbar."""
        pix = QtGui.QPixmap(28, 28)                    # 2x, so it stays sharp on scaled displays
        pix.setDevicePixelRatio(2.0)
        pix.fill(QtCore.Qt.GlobalColor.transparent)
        p = QtGui.QPainter(pix)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        p.setPen(QtGui.QPen(QtGui.QColor(edge), 1))
        p.setBrush(QtGui.QColor(color))
        p.drawRoundedRect(QtCore.QRectF(1.5, 1.5, 11, 11), 3, 3)
        p.end()
        self.btn_color.setIcon(QtGui.QIcon(pix))

    def restyle(self, mode: str) -> None:
        t = style.tokens(mode)
        self.setStyleSheet(f"#boardBar {{ background: {t['panel']}; border: 1px solid {t['line']}; "
                           f"border-radius: 10px; }} #boardBar QToolButton, #boardBar QLabel {{ color: {t['ink']}; }} "
                           f"#boardBar QToolButton:checked {{ background: {t['selection']}; "
                           f"color: {t['focus']}; border-radius: 6px; }}")


class ChartCanvas(QtWidgets.QWidget):
    tool_changed = QtCore.Signal(str, str)          # tool, measure unit ("time" or "phase")
    picked_changed = QtCore.Signal()

    def __init__(self, page):
        super().__init__()
        self.page, self.settings = page, page.main.settings
        self.spaces: dict[str, B.Board] = {}
        self.space: B.Board | None = None
        self.active = None                        # the chart whose settings are showing
        self._made = 0
        self._quiet = False                       # building a layout: nothing to remember yet
        self._mode = style.LIGHT
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.stack = QtWidgets.QStackedWidget()
        lay.addWidget(self.stack)
        self.options = QtWidgets.QStackedWidget()   # the picked chart's settings
        self.nothing = QtWidgets.QWidget()
        self.options.addWidget(self.nothing)
        self._save_timer = QtCore.QTimer(self, singleShot=True, interval=400)
        self._save_timer.timeout.connect(self.save)

        self.add_menu = QtWidgets.QMenu(self)
        self.add_menu.aboutToShow.connect(lambda: self._fill_add_menu(self.add_menu, None))
        self.floating = _Toolbar(self)
        self.floating.tools.buttonClicked.connect(lambda *_: self._tool_picked())
        self.floating.unit.buttonClicked.connect(lambda *_: self._unit_picked())
        self.floating.btn_tidy.clicked.connect(lambda: self.space.tidy())
        self.floating.btn_in.clicked.connect(lambda: self.space.zoom_about(self.space.zoom * 1.25))
        self.floating.btn_out.clicked.connect(lambda: self.space.zoom_about(self.space.zoom / 1.25))
        self.floating.btn_fit.clicked.connect(lambda: self.space.fit_all())
        self.floating.btn_grid.toggled.connect(lambda on: self.space.set_grid(on))
        self.floating.btn_color.clicked.connect(self._pick_board_color)
        self.floating.btn_color.customContextMenuRequested.connect(self._board_color_menu)
        self._show_board_color()
        self.btn_add = self.floating.btn_add

        self.layouts = QtWidgets.QComboBox()
        self.layouts.setToolTip("What's on the board: Board, the one you work on, or a layout you saved. Each "
                                "remembers how you left it")
        self.layouts.setMinimumContentsLength(9)
        self.layouts.setSizeAdjustPolicy(QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.btn_save = QtWidgets.QPushButton("Save layout…")
        self.btn_save.setToolTip("Keep these charts, where they are and their settings, as a layout of your own")
        self.btn_save.clicked.connect(self._save_dialog)
        self.btn_reset = QtWidgets.QPushButton("Clear")
        self.btn_reset.clicked.connect(self._reset_pressed)
        self.lock = QtWidgets.QCheckBox("Lock")
        self.lock.setToolTip("Lock the board: stop the charts being moved, resized, closed or added to, say for a class")
        self.lock.setChecked(self.settings.value("analysis/lock", False, type=bool))
        self.lock.toggled.connect(self._lock_toggled)
        self.bars = QtWidgets.QCheckBox("Scale bars")
        self.bars.setToolTip("Swap the axes of the plots against time for small scale bars, as in a published "
                             "figure. Drag a bar to move it; its numbers follow the zoom")
        self.bars.setChecked(self.settings.value("analysis/scale_bars", False, type=bool))
        self.bars.toggled.connect(lambda on: self.settings.setValue("analysis/scale_bars", on))
        self.bars.toggled.connect(lambda *_: [c.show_bars() for c in self.every()])
        self.toolbar = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(self.toolbar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        for w in (self.layouts, self.btn_save, self.btn_reset, self.lock, self.bars):
            row.addWidget(w)
        self._list_layouts()
        self.layouts.currentTextChanged.connect(self.show_layout)
        QtWidgets.QApplication.instance().focusChanged.connect(self._focus_moved)
        QtWidgets.QApplication.instance().installEventFilter(self)     # a click in a chart picks it

    # -- tools ---------------------------------------------------------------------------------
    def tool(self) -> str:
        return next(k for k, b in self.floating.btn.items() if b.isChecked())

    def measure_unit(self) -> str:
        return "phase" if self.floating.btn_phase.isChecked() else "time"

    def set_tool(self, tool: str, unit: str | None = None) -> None:
        self.floating.btn[tool].setChecked(True)
        if unit is not None:
            (self.floating.btn_phase if unit == "phase" else self.floating.btn_time).setChecked(True)
        self._tool_picked()

    def _unit_picked(self) -> None:
        self.floating.btn[B.MEASURE].setChecked(True)     # picking Time or Phase picks Measure
        self._tool_picked()

    def _tool_picked(self) -> None:
        tool = self.tool()
        for space in self.spaces.values():
            space.set_tool(tool)
        self.tool_changed.emit(tool, self.measure_unit())

    # -- layouts -------------------------------------------------------------------------------
    def own_layouts(self) -> list[str]:
        return [n for n in json.loads(self.settings.value("analysis/layouts", "[]")) if n != BOARD]

    def _list_layouts(self) -> None:
        self.layouts.blockSignals(True)
        keep = self.layouts.currentText()
        self.layouts.clear()
        self.layouts.addItems([BOARD, *self.own_layouts()])
        self.layouts.setCurrentText(keep)
        self.layouts.blockSignals(False)

    def start(self) -> None:
        """Open the layout last used."""
        name = self.settings.value("analysis/layout", BOARD)
        self.show_layout(name if self.layouts.findText(name) >= 0 else BOARD)

    def show_layout(self, name: str) -> None:
        if self.space is not None and self.space.name == name:
            return
        self.save()
        if name not in self.spaces:
            self._open(name)
        self.space = self.spaces[name]
        self.stack.setCurrentWidget(self.space)
        self.space.set_toolbar(self.floating)
        self.floating.btn_grid.blockSignals(True)
        self.floating.btn_grid.setChecked(self.space.grid)
        self.floating.btn_grid.blockSignals(False)
        self.layouts.blockSignals(True)
        self.layouts.setCurrentText(name)
        self.layouts.blockSignals(False)
        self.settings.setValue("analysis/layout", name)
        self.btn_reset.setText("Clear" if name == BOARD else "Delete")
        self.btn_reset.setToolTip("Take every chart off the board" if name == BOARD else "Delete this layout")
        self.floating.zoom.setText(f"{round(self.space.zoom * 100)}%")
        self._picked()
        self.page.layout_changed()

    def _open(self, name: str) -> None:
        self._quiet = True
        space = B.Board()
        space.name = name
        space.charts = []
        space.axes = {}
        space.apply_theme(self._mode)
        space.set_color(self.board_color())
        space.set_tool(self.tool())
        space.set_locked(self.lock.isChecked())
        space.picked_changed.connect(lambda s=space: self._picked() if s is self.space else None)
        space.moved.connect(self.changed)
        space.context_requested.connect(self._context_menu)
        space.delete_requested.connect(self.close_picked)
        space.step_requested.connect(self.page.step_list)
        space.export_requested.connect(self.export)
        space.zoom_changed.connect(lambda z: self.floating.zoom.setText(f"{round(z * 100)}%"))
        self.spaces[name] = space
        self.stack.addWidget(space)
        saved = self.settings.value(f"analysis/layout/{name}", "")
        if saved:
            self._restore(space, json.loads(saved))
        self.relink_all(space)
        self._quiet = False

    def _make_chart(self, space: B.Board, kind: str, cid: str | None = None):
        self._made += 1
        chart = KINDS[kind](self.page, cid or f"{kind} {self._made}")
        space.charts.append(chart)
        self.options.addWidget(chart.wrapped_options())
        chart.options_panel = self.options.widget(self.options.count() - 1)
        chart.channels_changed()
        chart.apply_theme(self._mode)
        return chart

    def state_of(self, space: B.Board) -> dict:
        charts = []
        for c in space.charts:
            p = c.dock.place
            charts.append({"id": c.id, "kind": c.kind, **c.state(), "place": [p.x(), p.y(), p.width(), p.height()]})
        return {"charts": charts, "view": space.state()}

    def _restore(self, space: B.Board, state: dict) -> None:
        loose = []
        states = state.get("charts", [])
        names = {s.get("name") for s in states}
        for s in states:
            if s["kind"] in ("Blocks", "Trials", "Traces"):
                s = self._as_response(s, names)
            if s["kind"] not in KINDS:
                continue
            chart = self._make_chart(space, s["kind"], s["id"])
            chart.restore(s)
            chart.channels_changed()
            chart.show_title()
            place = s.get("place")
            if place:
                self._put(space, chart, QtCore.QRectF(*place))
            else:                                 # from before the whiteboard: lined up below
                w, h = SIZE.get(s["kind"], (400, 280))
                loose.append(self._put(space, chart, QtCore.QRectF(0, 0, w, h)))
            chart.show_title()
        if loose:
            space.tidy()
        if "view" in state:
            space.restore(state["view"])

    def _as_response(self, s: dict, names: set) -> dict:
        """Response, from what it was called before (Traces), and from the Trials chart (once
        Blocks) it took over: its channel ticked, with the threshold and the spikes' dots."""
        old = s["kind"]
        name = s.get("name")
        if name in ("Traces", "Trials", "Blocks"):
            name = next(n for n in ("Response" if k == 1 else f"Response {k}" for k in range(1, 10000))
                        if n not in names)
            names.add(name)
        s = {**s, "kind": ResponseChart.kind, "name": name}
        if old == "Traces":
            return s
        fields = dict(s.get("fields", {}))
        channel = fields.pop("channel", "") or self.page.raw.channel.currentText()
        size = fields.pop("dots", None)
        fields.update({"threshold": True, "dots": True, **({"dot_size": size} if size is not None else {})})
        return {**s, "fields": fields, "ticked": [[channel, 0]] if channel else [], "spikes_set": True}

    def changed(self) -> None:
        """Something a layout remembers changed: save it once the changes stop."""
        if not self._quiet:
            self._save_timer.start()

    def save(self) -> None:
        self._save_timer.stop()
        if self.space is not None:
            try:
                self.settings.setValue(f"analysis/layout/{self.space.name}", json.dumps(self.state_of(self.space)))
            except RuntimeError:                  # closing: the settings are gone already
                pass

    def save_as(self, name: str) -> None:
        """Keep these charts as a layout of your own, and go to it."""
        if name == BOARD:
            return
        own = self.own_layouts()
        if name not in own:
            self.settings.setValue("analysis/layouts", json.dumps([*own, name]))
        self.settings.setValue(f"analysis/layout/{name}", json.dumps(self.state_of(self.space)))
        self._drop(name)
        self._list_layouts()
        self.show_layout(name)

    def _save_dialog(self) -> None:
        name, ok = QtWidgets.QInputDialog.getText(self, "Save layout", "Name of the layout:")
        if ok and name.strip():
            self.save_as(name.strip())

    def _drop(self, name: str) -> None:
        """Close a layout's charts."""
        space = self.spaces.pop(name, None)
        if space is not None:
            for c in list(space.charts):
                self._forget(c)
            if self.floating.parent() is space:
                self.floating.setParent(None)
            self.stack.removeWidget(space)
            space.deleteLater()

    def _forget(self, chart) -> None:
        if self.page.repeats.parent() is chart.options:
            self.page.repeats.setParent(self.page)    # it's every phase chart's: it outlives this one
            self.page.repeats.hide()
        self.options.removeWidget(chart.options_panel)
        chart.options_panel.deleteLater()
        if self.active is chart:
            self.active = None

    def reset(self) -> None:
        """Board cleared, or your own layout deleted."""
        name = self.space.name
        self.settings.remove(f"analysis/layout/{name}")
        self.space = None
        self._drop(name)
        if name != BOARD:
            self.settings.setValue("analysis/layouts", json.dumps([n for n in self.own_layouts() if n != name]))
            self._list_layouts()
        self.show_layout(BOARD if name != BOARD else name)

    def _reset_pressed(self) -> None:
        what = "Take every chart off the board?" if self.space.name == BOARD else f"Delete the layout {self.space.name}?"
        ask = QtWidgets.QMessageBox.question(self, self.btn_reset.text(), what)
        if ask == QtWidgets.QMessageBox.StandardButton.Yes:
            self.reset()

    def app_state(self) -> dict:
        """Every layout opened, and the one showing, for a .newt file."""
        self.save()
        return {"layout": self.space.name if self.space is not None else BOARD,
                "layouts": {name: self.state_of(space) for name, space in self.spaces.items()},
                "own": self.own_layouts(), "lock": self.lock.isChecked(), "bars": self.bars.isChecked()}

    def restore_app_state(self, s: dict) -> None:
        """The layouts of a .newt file, in place of what's on screen. Your own layouts stay
        in the menu; one of the same name as the file's takes the file's charts."""
        own = self.own_layouts()
        self.settings.setValue("analysis/layouts", json.dumps(own + [n for n in s["own"] if n not in own]))
        for name, state in s["layouts"].items():
            self.settings.setValue(f"analysis/layout/{name}", json.dumps(state))
        self.space = None
        for name in list(self.spaces):
            self._drop(name)
        self._list_layouts()
        self.lock.setChecked(s["lock"])
        self.bars.setChecked(s["bars"])
        self.show_layout(s["layout"] if self.layouts.findText(s["layout"]) >= 0 else BOARD)

    def _lock_toggled(self, on: bool) -> None:
        self.settings.setValue("analysis/lock", on)
        self.btn_add.setEnabled(not on)
        for space in self.spaces.values():
            space.set_locked(on)

    # -- charts --------------------------------------------------------------------------------
    def every(self) -> list:
        """The charts of every layout opened."""
        return [c for space in self.spaces.values() for c in space.charts]

    def here(self) -> list:
        """The charts of the layout showing."""
        return list(self.space.charts) if self.space is not None else []

    def first(self, kind: str):
        return next((c for c in self.here() if c.kind == kind), None)

    def _fill_add_menu(self, menu: QtWidgets.QMenu, at) -> None:
        menu.clear()
        for group, kinds in GROUPS.items():
            menu.addSection(group)
            for kind in kinds:
                c = KINDS[kind]
                n = sum(x.kind == c.kind for x in self.here())
                # Qt puts what follows the tab on the right, where a shortcut would go
                action = menu.addAction(c.kind + (f"  ({n} open)" if n else "") + f"\t{c.y}")
                action.setToolTip(c.tip)
                action.setStatusTip(c.tip)
                action.triggered.connect(lambda _=False, k=c.kind: self.add(k, at=at))
        menu.setToolTipsVisible(True)

    def _context_menu(self, at: QtCore.QPointF, where: QtCore.QPoint) -> None:
        """Right-click on empty board: the charts, to put one there."""
        menu = QtWidgets.QMenu(self)
        menu.addSection("Add a chart here")
        self._fill_add_menu(menu, at)
        menu.addSeparator()
        menu.addAction("Tidy up", self.space.tidy)
        menu.addAction("Show every chart", self.space.fit_all)
        menu.exec(where)

    def add(self, kind: str, beside=None, at: QtCore.QPointF | None = None):
        """Open a chart: at `at` (board place), beside the one given, else where there's room."""
        space = self.space
        name = self._free_name(kind)
        chart = self._make_chart(space, kind)
        chart.name = name
        chart.name_box.setText(chart.name)
        w, h = SIZE.get(kind, (400, 280))
        if beside is not None and beside.dock is not None and beside in space.charts:
            p = beside.dock.place
            place = space.free_place(p.width(), p.height(), QtCore.QPointF(p.right() + B.GRID, p.top()))
        else:
            place = space.free_place(w, h, at)
        frame = self._put(space, chart, place)
        chart.show_title()
        self.relink_all(space)
        space.pick([frame])
        self.page.layout_changed()
        self.changed()
        return chart

    def _put(self, space: B.Board, chart, place: QtCore.QRectF) -> B.ChartFrame:
        frame = space.add(chart, place)
        frame.bar.btn_copy.clicked.connect(lambda: self.copy(chart))
        frame.bar.btn_close.clicked.connect(lambda: self.close_chart(chart))
        return frame

    def _free_name(self, kind: str) -> str:
        names = {c.name for c in self.here()}
        return next(n for n in (kind if k == 1 else f"{kind} {k}" for k in range(1, 10000)) if n not in names)

    def copy(self, chart):
        """Another of this chart, with its settings, the same size, beside it."""
        new = self.add(chart.kind, chart)
        name = new.name
        new.restore({**chart.state(), "name": name})
        new.channels_changed()
        new.show_title()
        new.refresh()
        self.page.raw.pins_changed()
        return new

    def close_chart(self, chart) -> None:
        space = next(s for s in self.spaces.values() if chart in s.charts)
        space.charts.remove(chart)
        self._forget(chart)
        space.remove(chart.dock)
        self.relink_all(space)
        self.page.raw.pins_changed()
        self.page.layout_changed()
        self._picked()
        self.changed()

    def close_picked(self) -> None:
        for c in self.picked():
            self.close_chart(c)

    def clear_all(self) -> None:
        for c in self.here():
            self.close_chart(c)

    def picked(self) -> list:
        """The charts picked on the board showing."""
        if self.space is None:
            return []
        frames = self.space.picked()
        return [c for c in self.space.charts if c.dock in frames]

    def select(self, chart, add: bool = False) -> None:
        """Pick this chart (and show its settings), or none."""
        if self.space is None:
            return
        self.space.pick([chart.dock] if chart is not None and chart.dock is not None else [], add=add)

    def _picked(self) -> None:
        picked = self.picked()
        chart = picked[0] if len(picked) == 1 else None
        self.active = chart
        self.options.setCurrentWidget(chart.options_panel if chart is not None else self.nothing)
        if chart is not None and chart.help == "cycles":
            chart._side.insertWidget(2, self.page.repeats)
            self.page.repeats.show()
        self.picked_changed.emit()

    def open_settings(self, chart) -> None:
        """Show this chart's settings beside the board."""
        self.select(chart)

    def eventFilter(self, obj, ev) -> bool:
        if ev.type() == QtCore.QEvent.Type.MouseButtonPress and isinstance(obj, QtWidgets.QWidget) \
                and self.space is not None:
            w = obj
            while w is not None and not isinstance(w, B.ChartFrame):
                if isinstance(w, (B.TitleBar, B.Grip)):     # they pick for themselves
                    return False
                w = w.parentWidget()
            if w is not None and w.board is self.space and not w.picked:
                shift = bool(ev.modifiers() & QtCore.Qt.KeyboardModifier.ShiftModifier)
                self.space.pick([w], add=shift)
        return False

    # -- figures -------------------------------------------------------------------------------
    def export(self, area: QtCore.QRectF) -> None:
        """Save the part of the board in `area` as a figure, asking how."""
        dlg = figure.ExportDialog(self, area, self.settings)
        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        dlg.remember()
        fmt = dlg.format()
        path = None
        if fmt != "copy":
            folder = self.settings.value("export/folder", str(Path.home()))
            path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save figure", str(Path(folder) / f"figure.{fmt}"),
                                                            f"{fmt.upper()} (*.{fmt})")
            if not path:
                return
            self.settings.setValue("export/folder", str(Path(path).parent))
        try:
            figure.save(self.space, area, fmt, path, dpi=dlg.dpi.value(), light=dlg.light.isChecked())
        except OSError as e:
            QtWidgets.QMessageBox.warning(self, "Export figure", str(e))
            return
        self.page.main.statusBar().showMessage("Figure copied" if fmt == "copy" else f"Figure saved: {path}", 6000)

    def _focus_moved(self, _old, new) -> None:
        w = new
        while w is not None:
            if hasattr(w, "stale") and w in self.here():
                if w.dock is not None and not w.dock.picked:
                    self.select(w)
                return
            w = w.parentWidget()

    # -- zooming together ----------------------------------------------------------------------
    def relink(self, chart) -> None:
        """A chart's plots changed: link it again."""
        for space in self.spaces.values():
            if chart in space.charts:
                self.relink_all(space)

    def relink_all(self, space: B.Board) -> None:
        """Charts of a kind (against time, against phase) zoom together, unless unlinked."""
        by_link: dict[str, list] = {}
        for c in space.charts:
            if c.link:
                by_link.setdefault(c.link, []).append(c)
        for link, charts in by_link.items():
            joined = [c for c in charts if c.linked and c.plots()]
            for c in charts:
                if c.plots():
                    root = joined[0].plots()[0] if joined and c in joined and c is not joined[0] else None
                    c.plots()[0].getPlotItem().setXLink(root)
            group = space.axes.setdefault(link, AxisGroup())
            group.set_plots([pw for c in joined for pw in c.plots()])

    def link_changed(self, chart) -> None:
        self.relink(chart)
        self.changed()

    # -- telling the charts --------------------------------------------------------------------
    def channels_changed(self) -> None:
        for c in self.every():
            c.channels_changed()

    def refresh(self) -> None:
        for c in self.every():
            c.refresh()

    def refresh_cycles(self) -> None:
        for c in self.every():
            if c.help == "cycles":
                c.refresh()

    def stimulus_changed(self) -> None:
        for c in self.every():
            c.stimulus_changed()

    def events_changed(self) -> None:
        for c in self.every():
            c.events_changed()

    def units_changed(self) -> None:
        for c in self.every():
            c.units_changed()

    def current_changed(self) -> None:
        for c in self.here():
            c.current_changed()

    def threshold_changed(self) -> None:
        for c in self.every():
            c.threshold_changed()

    def rescale(self) -> None:
        for c in self.here():
            c.rescale()

    def pinned_blocks(self) -> list[int]:
        """The blocks charts are pinned to."""
        return sorted({i for c in self.every() if c.pin is not None for i in c.blocks()})

    # -- the board's color: one of your own for each mode, kept on this computer ---------------
    def board_color(self) -> str | None:
        return self.settings.value(f"analysis/board_color/{self._mode}", "") or None

    def _show_board_color(self) -> None:
        color = self.board_color()
        for space in self.spaces.values():
            space.set_color(color)
        t = style.tokens(self._mode)
        self.floating.show_color(color or t["ground"], t["subtle"])

    def _pick_board_color(self) -> None:
        now = QtGui.QColor(self.board_color() or style.tokens(self._mode)["ground"])
        color = QtWidgets.QColorDialog.getColor(now, self, "Board color")
        if color.isValid():
            self.settings.setValue(f"analysis/board_color/{self._mode}", color.name())
            self._show_board_color()

    def _board_color_menu(self, at: QtCore.QPoint) -> None:
        menu = QtWidgets.QMenu(self)
        default = menu.addAction("Default")
        default.setToolTip("The light or dark mode's own board color")
        default.triggered.connect(self._default_board_color)
        menu.exec(self.floating.btn_color.mapToGlobal(at))

    def _default_board_color(self) -> None:
        self.settings.remove(f"analysis/board_color/{self._mode}")
        self._show_board_color()

    def apply_theme(self, mode: str) -> None:
        self._mode = mode
        self.floating.restyle(mode)
        for space in self.spaces.values():
            space.apply_theme(mode)
        self._show_board_color()
        for c in self.every():
            c.apply_theme(mode)

    def hideEvent(self, ev):
        super().hideEvent(ev)
        self.save()
