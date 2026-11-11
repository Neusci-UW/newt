"""The Analysis canvas: charts in dock panels that snap beside or under each other, stack as
tabs, or float off onto a second screen. Add chart opens any chart, as many times as you
like. A layout is a set of charts and how they're arranged: Spike Sorting, Response and
Waveforms are built in, and Save layout adds your own. Each layout keeps its
charts while you look at another, and how you left it is remembered.
"""
from __future__ import annotations

import base64
import json

from pyqtgraph.Qt import QtCore, QtWidgets

from . import style
from .plotzoom import AxisGroup
from .responsecharts import (FitChart, LoopChart, PhaseChart, PsthChart, ResponseCycleChart, SpikeCycleChart, StaChart,
                             StimulusCycleChart, TracesChart)
from .spikecharts import (BlocksChart, IntervalsChart, PcaChart, RasterChart, RateChart, SizesChart, StimulusChart,
                          WaveformsChart)
from .waveformview import PhasePlotChart, SlopeChart, VoltsChart

CHARTS = (StimulusChart, BlocksChart, RasterChart, RateChart, PcaChart, WaveformsChart, SizesChart, IntervalsChart,
          TracesChart, PsthChart, StaChart, StimulusCycleChart, ResponseCycleChart, SpikeCycleChart, PhaseChart,
          LoopChart, FitChart, PhasePlotChart, VoltsChart, SlopeChart)
KINDS = {c.kind: c for c in CHARTS}
GROUPS = ("Spikes", "Response", "Waveforms")

# A built-in layout: a chart's kind, or ("h" or "v", [(share of the room, what's there)]).
BUILT_IN = {
    "Spike Sorting": ("h", [(3, ("v", [(1, "Stimulus"), (3, "Trials"), (2, "Raster"), (2, "Rate")])),
                            (2, ("v", [(1, "PCA map"), (1, "Waveforms"), (1, "AP sizes"), (1, "Intervals")]))]),
    "Response": ("h", [(1, ("v", [(5, "Traces"),
                                  (3, ("h", [(1, "Stimulus-triggered rate"), (1, "Spike-triggered average"),
                                             (1, "Response against stimulus")])),
                                  (2, "Stimulus fit")])),
                       (1, ("v", [(1, "Stimulus cycle"), (2, "Response by cycle"), (2, "Spikes by cycle"),
                                  (2, "Spikes per phase")]))]),
    "Waveforms": ("h", [(3, "V against dV/dt"), (2, ("v", [(1, "V"), (1, "dV/dt")]))]),
}


def _first(tree) -> str:
    return tree if isinstance(tree, str) else _first(tree[1][0][1])


class TitleBar(QtWidgets.QWidget):
    """What a chart's dock has along its top: its name, what's its own, and Pin, Copy and ×.
    Dragging it moves the chart; clicking it picks the chart."""

    def __init__(self, dock: "ChartDock"):
        super().__init__()
        self.dock = dock
        self.setObjectName("chartBar")
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground)
        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(8, 2, 4, 2)
        row.setSpacing(6)
        self.name = QtWidgets.QLabel()
        self.tag = QtWidgets.QLabel()
        for w in (self.name, self.tag):       # a long name gives way, so a narrow chart can be narrow
            w.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred)
        row.addWidget(self.name, 2)
        row.addWidget(self.tag, 1)
        chart = dock.chart
        self.btn_pin = self._button("Pin", "Keep this chart on the trials selected now", row)
        self.btn_pin.setCheckable(True)
        self.btn_pin.setVisible(chart.uses_blocks)
        self.btn_pin.clicked.connect(chart.set_pinned)
        self.btn_set = self._button("⚙", "Open this chart's settings", row)
        self.btn_set.setStyleSheet("QToolButton { font-size: 18px; padding: 0; }")
        self.btn_set.clicked.connect(lambda: dock.canvas.open_settings(chart))
        self.btn_copy = self._button("Copy", "Open another of this chart, with the same settings", row)
        self.btn_copy.clicked.connect(lambda: dock.canvas.copy(chart))
        self.btn_close = self._button("×", "Close this chart", row)
        self.btn_close.clicked.connect(lambda: dock.canvas.close_chart(chart))

    @staticmethod
    def _button(text: str, tip: str, row) -> QtWidgets.QToolButton:
        b = QtWidgets.QToolButton(text=text, autoRaise=True)
        b.setToolTip(tip)
        row.addWidget(b)
        return b

    def mousePressEvent(self, ev):
        self.dock.canvas.select(self.dock.chart)
        ev.ignore()                               # the dock still gets it, to drag


class ChartDock(QtWidgets.QDockWidget):
    def __init__(self, chart, canvas: "ChartCanvas"):
        super().__init__(chart.name)
        self.chart, self.canvas = chart, canvas
        chart.dock = self
        self.setObjectName(chart.id)
        self.bar = TitleBar(self)
        self.setTitleBarWidget(self.bar)
        self.setWidget(chart)
        self.dockLocationChanged.connect(lambda *_: canvas.changed())
        self.topLevelChanged.connect(lambda *_: canvas.changed())

    def show_title(self, name: str, tag: str, pinned: bool) -> None:
        self.bar.name.setText(f"<b>{name}</b>")
        self.bar.tag.setText(tag)
        self.bar.btn_pin.setChecked(pinned)

    def set_locked(self, on: bool) -> None:
        F = QtWidgets.QDockWidget.DockWidgetFeature
        self.setFeatures(F.NoDockWidgetFeatures if on else F.DockWidgetMovable | F.DockWidgetFloatable)
        self.bar.btn_close.setVisible(not on)
        self.bar.btn_copy.setVisible(not on)


class Workspace(QtWidgets.QMainWindow):
    """One layout's charts."""

    def __init__(self, canvas: "ChartCanvas", name: str):
        super().__init__()
        self.canvas, self.name = canvas, name
        self.setWindowFlags(QtCore.Qt.WindowType.Widget)
        self.setDockNestingEnabled(True)
        O = QtWidgets.QMainWindow.DockOption
        self.setDockOptions(O.AllowNestedDocks | O.AllowTabbedDocks | O.GroupedDragging)   # tabs drag out one by one
        self.charts: list = []
        self.axes: dict[str, AxisGroup] = {}
        self._arrange = None                      # how to size the docks, once they're on screen
        self.hint = QtWidgets.QLabel("No charts here. Use Add chart to put some on the canvas.", self)
        self.hint.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.hint.hide()

    def show_hint(self) -> None:
        self.hint.setVisible(not self.charts)
        self.hint.setGeometry(self.rect())

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self.hint.setGeometry(self.rect())

    def showEvent(self, ev):
        super().showEvent(ev)
        if self._arrange is not None:
            arrange, self._arrange = self._arrange, None
            QtCore.QTimer.singleShot(0, arrange)


class ChartCanvas(QtWidgets.QWidget):
    def __init__(self, page):
        super().__init__()
        self.page, self.settings = page, page.main.settings
        self.spaces: dict[str, Workspace] = {}
        self.space: Workspace | None = None
        self.active = None                        # the chart whose settings are showing
        self._made = 0
        self._quiet = False                       # building a layout: nothing to remember yet
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.stack = QtWidgets.QStackedWidget()
        lay.addWidget(self.stack)
        self.options = QtWidgets.QStackedWidget()   # the clicked chart's settings
        self.nothing = QtWidgets.QLabel("Press Settings on a chart to see its settings here.")
        self.nothing.setWordWrap(True)
        self.options.addWidget(self.nothing)
        self._save_timer = QtCore.QTimer(self, singleShot=True, interval=400)
        self._save_timer.timeout.connect(self.save)

        self.btn_add = QtWidgets.QPushButton("+ Add chart")
        self.btn_add.setToolTip("Put another chart on the canvas. Any chart can be open more than once")
        self.add_menu = QtWidgets.QMenu(self.btn_add)
        self.add_menu.aboutToShow.connect(self._fill_add_menu)
        self.btn_add.setMenu(self.add_menu)
        self.layouts = QtWidgets.QComboBox()
        self.layouts.setToolTip("The charts on the canvas and how they're arranged. Each layout remembers how you left it")
        self.layouts.setMinimumContentsLength(9)
        self.layouts.setSizeAdjustPolicy(QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.btn_save = QtWidgets.QPushButton("Save…")
        self.btn_save.setToolTip("Keep these charts and their settings as a new layout of your own")
        self.btn_save.clicked.connect(self._save_dialog)
        self.btn_reset = QtWidgets.QPushButton("Reset")
        self.btn_reset.clicked.connect(self._reset_pressed)
        self.lock = QtWidgets.QCheckBox("Lock")
        self.lock.setToolTip("Lock the layout: stop the charts being moved, closed or added to, say for a class")
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
        for w in (self.btn_add, self.layouts, self.btn_save, self.btn_reset, self.lock, self.bars):
            row.addWidget(w)
        self._list_layouts()
        self.layouts.currentTextChanged.connect(self.show_layout)
        QtWidgets.QApplication.instance().focusChanged.connect(self._focus_moved)

    # -- layouts -------------------------------------------------------------------------------
    def own_layouts(self) -> list[str]:
        return json.loads(self.settings.value("analysis/layouts", "[]"))

    def _list_layouts(self) -> None:
        self.layouts.blockSignals(True)
        keep = self.layouts.currentText()
        self.layouts.clear()
        self.layouts.addItems([*BUILT_IN, *self.own_layouts()])
        self.layouts.setCurrentText(keep)
        self.layouts.blockSignals(False)

    def start(self) -> None:
        """Open the layout last used."""
        name = self.settings.value("analysis/layout", "Spike Sorting")
        self.show_layout(name if self.layouts.findText(name) >= 0 else "Spike Sorting")

    def show_layout(self, name: str) -> None:
        if self.space is not None and self.space.name == name:
            return
        self.save()
        if name not in self.spaces:
            self._open(name)
        self.space = self.spaces[name]
        self.stack.setCurrentWidget(self.space)
        self.layouts.blockSignals(True)
        self.layouts.setCurrentText(name)
        self.layouts.blockSignals(False)
        self.settings.setValue("analysis/layout", name)
        self.btn_reset.setText("Reset" if name in BUILT_IN else "Delete")
        self.btn_reset.setToolTip("Put this layout back as it came" if name in BUILT_IN else "Delete this layout")
        self.select(self.space.charts[0] if self.space.charts else None)
        self.page.layout_changed()

    def _open(self, name: str) -> None:
        self._quiet = True
        space = Workspace(self, name)
        self.spaces[name] = space
        self.stack.addWidget(space)
        saved = self.settings.value(f"analysis/layout/{name}", "")
        if saved:
            self._restore(space, json.loads(saved))
        elif name in BUILT_IN:
            self._build(space, BUILT_IN[name])
        self.relink_all(space)
        space.show_hint()
        for c in space.charts:
            c.dock.set_locked(self.lock.isChecked())
            c.collapse()
        self._quiet = False

    def _build(self, space: Workspace, tree) -> None:
        """Place a built-in layout: each level's first charts, split apart, then what's in them."""
        first = self._make_dock(space, _first(tree))
        space.addDockWidget(QtCore.Qt.DockWidgetArea.TopDockWidgetArea, first)
        sizes: list = []
        self._place(space, tree, first, sizes)

        def arrange():
            for docks, shares, way in sizes:
                space.resizeDocks(docks, [s * 1000 for s in shares], way)
        space._arrange = arrange

    def _place(self, space: Workspace, tree, first: "ChartDock", sizes: list) -> None:
        if isinstance(tree, str):
            return
        way = QtCore.Qt.Orientation.Horizontal if tree[0] == "h" else QtCore.Qt.Orientation.Vertical
        heads = [first]
        for _, part in tree[1][1:]:
            head = self._make_dock(space, _first(part))
            space.splitDockWidget(heads[-1], head, way)
            heads.append(head)
        for head, (_, part) in zip(heads, tree[1]):
            self._place(space, part, head, sizes)
        sizes.insert(0, (heads, [share for share, _ in tree[1]], way))

    def _make_chart(self, space: Workspace, kind: str, cid: str | None = None):
        self._made += 1
        chart = KINDS[kind](self.page, cid or f"{kind} {self._made}")
        space.charts.append(chart)
        dock = ChartDock(chart, self)
        self.options.addWidget(chart.wrapped_options())
        chart.options_panel = self.options.widget(self.options.count() - 1)
        chart.channels_changed()
        dock.set_locked(self.lock.isChecked())
        return chart

    def _make_dock(self, space: Workspace, kind: str) -> ChartDock:
        chart = self._make_chart(space, kind)
        chart.auto_hide = True
        return chart.dock

    def state_of(self, space: Workspace) -> dict:
        return {"charts": [{"id": c.id, "kind": c.kind, **c.state()} for c in space.charts],
                "docks": base64.b64encode(bytes(space.saveState())).decode()}

    def _restore(self, space: Workspace, state: dict) -> None:
        for s in state["charts"]:
            if s["kind"] == "Blocks":             # its name before Trials
                s = {**s, "kind": "Trials", "name": "Trials" if s.get("name") == "Blocks" else s.get("name")}
            if s["kind"] in KINDS:
                chart = self._make_chart(space, s["kind"], s["id"])
                chart.restore(s)
                chart.channels_changed()
                chart.show_title()
                space.addDockWidget(QtCore.Qt.DockWidgetArea.TopDockWidgetArea, chart.dock)
        docks = QtCore.QByteArray(base64.b64decode(state["docks"]))
        space.restoreState(docks)

        def arrange():
            space.restoreState(docks)
            for c in space.charts:
                c.collapse(True)
        space._arrange = arrange

    def changed(self) -> None:
        """Something a layout remembers changed: save it once the changes stop."""
        if not self._quiet:
            self._save_timer.start()

    def save(self) -> None:
        self._save_timer.stop()
        if self.space is not None and not self.space._arrange:
            self.settings.setValue(f"analysis/layout/{self.space.name}", json.dumps(self.state_of(self.space)))

    def save_as(self, name: str) -> None:
        """Keep these charts as a new layout, and go to it."""
        own = self.own_layouts()
        if name not in own and name not in BUILT_IN:
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
        """A built-in layout back as it came, or your own deleted."""
        name = self.space.name
        self.settings.remove(f"analysis/layout/{name}")
        self.space = None
        self._drop(name)
        if name in BUILT_IN:
            self.show_layout(name)
        else:
            self.settings.setValue("analysis/layouts", json.dumps([n for n in self.own_layouts() if n != name]))
            self._list_layouts()
            self.show_layout("Spike Sorting")

    def _reset_pressed(self) -> None:
        if self.space.name in BUILT_IN:
            self.reset()
            return
        ask = QtWidgets.QMessageBox.question(self, "Delete layout", f"Delete the layout {self.space.name}?")
        if ask == QtWidgets.QMessageBox.StandardButton.Yes:
            self.reset()

    def app_state(self) -> dict:
        """Every layout opened, and the one showing, for an .argus file."""
        self.save()
        return {"layout": self.space.name if self.space is not None else "Spike Sorting",
                "layouts": {name: self.state_of(space) for name, space in self.spaces.items()},
                "own": self.own_layouts(), "lock": self.lock.isChecked(), "bars": self.bars.isChecked()}

    def restore_app_state(self, s: dict) -> None:
        """The layouts of an .argus file, in place of what's on screen. Your own layouts stay
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
        self.show_layout(s["layout"] if self.layouts.findText(s["layout"]) >= 0 else "Spike Sorting")

    def _lock_toggled(self, on: bool) -> None:
        self.settings.setValue("analysis/lock", on)
        self.btn_add.setEnabled(not on)
        for c in self.every():
            c.dock.set_locked(on)

    # -- charts --------------------------------------------------------------------------------
    def every(self) -> list:
        """The charts of every layout opened."""
        return [c for space in self.spaces.values() for c in space.charts]

    def here(self) -> list:
        """The charts of the layout showing."""
        return list(self.space.charts) if self.space is not None else []

    def first(self, kind: str):
        return next((c for c in self.here() if c.kind == kind), None)

    def _fill_add_menu(self) -> None:
        menu = self.add_menu
        menu.clear()
        for group in GROUPS:
            menu.addSection(group)
            for c in CHARTS:
                if c.group == group:
                    n = sum(x.kind == c.kind for x in self.here())
                    action = menu.addAction(c.kind + (f"  ({n} open)" if n else ""))
                    action.setToolTip(c.tip)
                    action.triggered.connect(lambda _=False, k=c.kind: self.add(k))
        menu.addSeparator()
        menu.addAction("Clear all charts", self.clear_all)

    def add(self, kind: str, beside=None):
        """Open a chart, beside the one given or the clicked one."""
        space = self.space
        chart = self._make_chart(space, kind)
        chart.name = self._free_name(kind)
        chart.name_box.setText(chart.name)
        chart.show_title()
        self._dock_beside(space, chart, beside or self.active)
        chart.collapse()
        self.relink_all(space)
        space.show_hint()
        self.select(chart)
        self.page.layout_changed()
        self.changed()
        return chart

    def _dock_beside(self, space: Workspace, chart, other) -> None:
        if other is not None and other.dock is not None and other in space.charts and other is not chart:
            space.splitDockWidget(other.dock, chart.dock, QtCore.Qt.Orientation.Horizontal)
        else:
            space.addDockWidget(QtCore.Qt.DockWidgetArea.TopDockWidgetArea, chart.dock)

    def _free_name(self, kind: str) -> str:
        names = {c.name for c in self.here()}
        return next(n for n in (kind if k == 1 else f"{kind} {k}" for k in range(1, 10000)) if n not in names)

    def copy(self, chart):
        """Another of this chart, with its settings, and the same size."""
        space, width, height = self.space, chart.dock.width(), chart.dock.height()
        new = self.add(chart.kind, chart)
        name = new.name
        new.restore({**chart.state(), "name": name, "auto_hide": False})
        new.channels_changed()
        new.show_title()
        new.refresh()
        self.page.raw.pins_changed()

        def same_size():
            if chart in space.charts and new in space.charts:
                space.resizeDocks([chart.dock, new.dock], [width, width], QtCore.Qt.Orientation.Horizontal)
                space.resizeDocks([new.dock], [height], QtCore.Qt.Orientation.Vertical)
        same_size()
        QtCore.QTimer.singleShot(0, same_size)    # once the new dock has its place
        return new

    def close_chart(self, chart) -> None:
        space = next(s for s in self.spaces.values() if chart in s.charts)
        space.charts.remove(chart)
        self._forget(chart)
        space.removeDockWidget(chart.dock)
        chart.dock.deleteLater()
        self.relink_all(space)
        space.show_hint()
        self.select(space.charts[0] if space.charts else None)
        self.page.raw.pins_changed()
        self.page.layout_changed()
        self.changed()

    def clear_all(self) -> None:
        for c in self.here():
            self.close_chart(c)

    def select(self, chart) -> None:
        """Show this chart's settings, and pick it out."""
        if self.active is chart:
            return
        for c in self.here():
            c.dock.bar.setStyleSheet(self._bar_style(c is chart))
        self.active = chart
        self.options.setCurrentWidget(chart.options_panel if chart is not None else self.nothing)
        if chart is not None and chart.help == "cycles":
            chart._side.insertWidget(2, self.page.repeats)
            self.page.repeats.show()
        self.page.windows["chart"].setWindowTitle(f"{chart.name} settings" if chart is not None else "Chart settings")

    def open_settings(self, chart) -> None:
        """Open the settings window on this chart. While it's open, clicking another chart
        shows that one's."""
        self.select(chart)
        self.page.windows["chart"].open_()

    def _bar_style(self, on: bool) -> str:
        t = style.tokens(self.page._mode)
        return f"#chartBar {{ background: {t['selection'] if on else t['panel']}; " \
               f"border-bottom: 2px solid {t['focus'] if on else t['line']}; }} " \
               f"#chartBar QLabel, #chartBar QToolButton {{ color: {t['ink']}; }}"

    def _focus_moved(self, _old, new) -> None:
        w = new
        while w is not None:
            if hasattr(w, "stale") and w in self.here():
                self.select(w)
                return
            w = w.parentWidget()

    # -- zooming together ----------------------------------------------------------------------
    def relink(self, chart) -> None:
        """A chart's plots changed: link it again."""
        for space in self.spaces.values():
            if chart in space.charts:
                self.relink_all(space)

    def relink_all(self, space: Workspace) -> None:
        """Charts of a kind (against time, against phase) zoom together, unless unlinked, and
        line up when they're stacked."""
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

    def apply_theme(self, mode: str) -> None:
        for c in self.every():
            c.apply_theme(mode)
            c.dock.bar.setStyleSheet(self._bar_style(c is self.active))

    def hideEvent(self, ev):
        super().hideEvent(ev)
        self.save()
