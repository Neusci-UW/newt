"""Quick charts of DataPad columns, live (pyqtgraph) and linked to the DataPad: change a
column there and these follow. Rows are as the DataPad makes them (each trial, segment,
pulse…), ticked rows only.

  Scatter  Y against X, one point per row: one or more Y columns, X a column or the row order.
           Its series are its Y columns, each in its own color under its DataPad name (not the
           Trials list's groups: the rows are only numbers here).
  Bars     each group's mean ± SEM (or SD) of one or more columns, each row's value as a dot.

They start empty: the student chooses what to plot (nothing is picked for them). The
DataPad's Scatter and Plot… open these, on the columns chosen there, big in chart focus.

Edit points: with it on, a click on a row's dot leaves that row out (its tick in the
DataPad, so its trial leaves Analysis too); it stays as a hollow circle, and a click puts it
back. Add a point… types in a point by hand (a ◆, kept with the chart; Bars count it in its
group's mean); with Edit points on, a click deletes it.
"""
from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

from . import colors
from . import padmodel as P
from .chartbase import Chart, dot_size
from .plotzoom import ClickToScrollPlot, RoomyAxis, WheelViewBox, fit_title, light_grid
from .viewkit import label_axis

ROW_ORDER = "#row"                 # Scatter's X: the rows in order, 1, 2, 3…
NO_GROUP = "Not in a group"
_SUBS = str.maketrans("0123456789+-=()aehijklmnoprstuvx", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₕᵢⱼₖₗₘₙₒₚᵣₛₜᵤᵥₓ")


def plain_name(name: str) -> str:
    """A name for plain text (an axis's tick labels): I_x → Iₓ where Unicode has the
    subscript, else the two run together (I_B → IB), never with the _."""
    base, _, sub = name.partition("_")
    if not sub:
        return base
    small = sub.translate(_SUBS)
    return base + (small if all(c != o for c, o in zip(small, sub)) else sub)


class PadViewBox(WheelViewBox):
    """Zooms both ways, as a plot of points should (the app's other plots zoom time): the
    wheel or a pinch round the pointer; over an axis, just that axis. Room past the data on
    every side, so dots at the edges show whole and you can zoom out a little."""
    limit_pad = (0.15, 0.15)

    def wheelEvent(self, ev, axis=None):
        pg.ViewBox.wheelEvent(self, ev, axis)
        ev.accept()


class PadPlot(ClickToScrollPlot):
    """A plot widget for the DataPad charts: a trackpad pinch zooms it too (once its chart
    is picked, on the board, as the wheel does)."""

    def viewportEvent(self, ev):
        if ev.type() == QtCore.QEvent.Type.NativeGesture and \
                ev.gestureType() == QtCore.Qt.NativeGestureType.ZoomNativeGesture and self._chart_picked():
            vb = self.getPlotItem().vb
            at = vb.mapSceneToView(self.mapToScene(ev.position().toPoint()))
            s = 1.0 / max(0.2, 1.0 + ev.value())
            vb.scaleBy((s, s), center=at)
            ev.accept()
            return True
        return super().viewportEvent(ev)


def pad_plot() -> PadPlot:
    pw = PadPlot(viewBox=PadViewBox(), axisItems={"left": RoomyAxis("left")})
    light_grid(pw.getPlotItem())
    fit_title(pw.getPlotItem())
    return pw


def _number(text: str):
    try:
        return float(text.replace(",", "."))
    except ValueError:
        return None


class SquareBox(QtWidgets.QWidget):
    """Holds a plot so the box inside its axes (not counting the legend beside it) is square,
    as big as fits, centred. While `shaped()` says something else shapes the chart (a shape
    picked in Single Plot's chart focus), it just fills: two boxes each shaping it would
    shrink it in turns."""

    def __init__(self, plot, shaped=lambda: False):
        super().__init__()
        self.plot, self.shaped = plot, shaped
        plot.setParent(self)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(40)
        plot.getPlotItem().vb.sigResized.connect(lambda *_: QtCore.QTimer.singleShot(0, self.place))

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self.place()

    def place(self) -> None:
        """Size the plot so its box is square: the axes and title take what they take, the
        box the rest. Run again once the plot has laid out, as the axes' widths can change."""
        r = self.rect()
        if self.shaped():
            if self.plot.geometry() != r:
                self.plot.setGeometry(r)
            return
        box = self.plot.getPlotItem().vb.geometry()
        ex, ey = (self.plot.width() - box.width(), self.plot.height() - box.height()) \
            if box.width() > 0 and box.height() > 0 else (60.0, 50.0)
        side = max(20.0, min(r.width() - ex, r.height() - ey))
        w, h = int(round(side + ex)), int(round(side + ey))
        want = QtCore.QRect(r.x() + (r.width() - w) // 2, r.y() + (r.height() - h) // 2, w, h)
        if want != self.plot.geometry():
            self.plot.setGeometry(want)


class ColumnList(QtWidgets.QListWidget):
    """The DataPad's number columns, each with a tick: the ones to plot."""

    changed = QtCore.Signal()

    def __init__(self, tip: str):
        super().__init__()
        self.setToolTip(tip)
        self.setMaximumHeight(130)
        self.itemChanged.connect(lambda *_: self.changed.emit())

    def ticked(self) -> list[str]:
        return [self.item(k).data(QtCore.Qt.ItemDataRole.UserRole) for k in range(self.count())
                if self.item(k).checkState() == QtCore.Qt.CheckState.Checked]

    def show_columns(self, names: list[str], shown: dict[str, str], ticked: list[str]) -> None:
        self.blockSignals(True)
        self.clear()
        for n in names:
            it = QtWidgets.QListWidgetItem(shown.get(n, n))
            it.setData(QtCore.Qt.ItemDataRole.UserRole, n)
            it.setFlags(it.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(QtCore.Qt.CheckState.Checked if n in ticked else QtCore.Qt.CheckState.Unchecked)
            self.addItem(it)
        if not names:
            it = QtWidgets.QListWidgetItem("No DataPad columns yet")
            it.setFlags(QtCore.Qt.ItemFlag.NoItemFlags)
            self.addItem(it)
        self.blockSignals(False)


class PadChart(Chart):
    """What the DataPad charts share: following the DataPad, a ticked list of its columns
    (saved as the field `list_key`), hover and click on a row's dot."""
    uses_units = False
    uses_blocks = False
    list_key = "y"

    def __init__(self, page, cid: str = ""):
        super().__init__(page, cid)
        self.plot = pad_plot()
        p = self.plot.getPlotItem()
        p.showGrid(x=True, y=True, alpha=0.2)
        for side in ("left", "bottom"):
            p.getAxis(side).enableAutoSIPrefix(False)
        self._hits: list[tuple[int, str]] = []           # each dot's (trial from 1, column)
        self.dots = pg.ScatterPlotItem(pen=None, hoverable=True, tip=self._tip)
        self.dots.setZValue(5)
        self.dots.sigClicked.connect(self._clicked)
        self.typed: list[dict] = []                      # points typed in by hand, kept with the chart
        self.removed: list[dict] = []                    # rows left out from here: hollow, a click puts them back
        self._typed_at: list[int] = []                   # each ◆'s place in self.typed
        self._removed_at: list[int] = []                 # each hollow circle's place in self.removed
        self.typed_dots = pg.ScatterPlotItem(symbol="d", hoverable=True, tip=self._typed_tip)
        self.typed_dots.setZValue(6)
        self.typed_dots.sigClicked.connect(self._typed_clicked)
        self.hollow = pg.ScatterPlotItem(hoverable=True, tip=self._hollow_tip)
        self.hollow.setZValue(4)
        self.hollow.sigClicked.connect(self._hollow_clicked)
        self.legend = pg.LegendItem()                    # beside the plot, not over the dots
        p.legend = self.legend                           # (named curves join it; the theme colors it)
        self._legend_shown = False
        self._hooked = False

    def plots(self) -> list:
        return [self.plot]

    # -- the DataPad -----------------------------------------------------------------------------
    def _hook_pad(self) -> None:
        """Follow the DataPad (made after the board: a chart put back with the board waits for it)."""
        pad = getattr(self.page, "pad_view", None)
        if pad is None:
            QtCore.QTimer.singleShot(0, self._hook_pad)
            return
        if not self._hooked:
            self._hooked = True
            pad.columns_changed.connect(self.pad_changed)
            pad.table_changed.connect(self.pad_changed)
            pad.went_stale.connect(self.refresh)         # trials regrouped: draw again (from a fresh table)
        self.pad_changed()

    def _pad_ready(self) -> bool:
        """The DataPad's table is up to date (worked out now if it was waiting). False: it was
        just worked out, and that draws this again."""
        pad = getattr(self.page, "pad_view", None)
        if pad is None:
            return False
        if pad._stale:
            pad.fresh()
            return False
        return True

    def show_legend(self) -> None:
        """The legend in its column right of the plot while it has entries, else no room for it."""
        p = self.plot.getPlotItem()
        on = bool(self.legend.items)
        if on and not self._legend_shown:
            self.legend.setSizePolicy(QtWidgets.QSizePolicy.Policy.Fixed, QtWidgets.QSizePolicy.Policy.Fixed)
            p.layout.addItem(self.legend, 2, 3)
            p.layout.setAlignment(self.legend, QtCore.Qt.AlignmentFlag.AlignTop)
        elif not on and self._legend_shown:
            p.layout.removeItem(self.legend)
        self.legend.setVisible(on)
        self._legend_shown = on

    def pad_changed(self) -> None:
        """The DataPad changed: its columns, or their numbers."""
        self._fill_columns()
        self.refresh()

    def _numeric(self) -> list[str]:
        pad = getattr(self.page, "pad_view", None)
        return [] if pad is None else [c.name for c in pad.pad.columns if c.name not in pad.tab.text]

    def _shown(self, names) -> dict[str, str]:
        pad = getattr(self.page, "pad_view", None)
        return {n: pad._shown_name(n) for n in names} if pad is not None else {}

    def _html(self, name: str) -> str:
        return P.html_name(self._shown([name]).get(name, name))

    def _fill_columns(self) -> None:
        names = self._numeric()
        want = self._wanted.pop(self.list_key, None)     # ticked when saved, listed now
        ticked = [want] if isinstance(want, str) else list(want) if want else self.columns.ticked()
        self.columns.show_columns(names, self._shown(names), ticked)
        if want and not set(ticked) <= set(names):
            self._wanted[self.list_key] = want           # not all there yet: keep waiting for them
        self._apply_wanted()

    def state(self) -> dict:
        s = super().state()
        if self.list_key not in self._wanted:
            s["fields"][self.list_key] = self.columns.ticked()
        s["typed"] = [dict(t) for t in self.typed]
        s["removed"] = [dict(r) for r in self.removed]
        return s

    def restore(self, state: dict) -> None:
        super().restore(state)
        self.typed = [dict(t) for t in state.get("typed", [])]
        self.removed = [dict(r) for r in state.get("removed", [])]
        self._fill_columns()
        self.refresh()

    def tick(self, names: list[str]) -> None:
        self._wanted.pop(self.list_key, None)
        self.columns.show_columns(self._numeric(), self._shown(self._numeric()), list(names))

    # -- editing the points ----------------------------------------------------------------------
    def add_edit_controls(self) -> None:
        """Edit points and Add a point…, last in its settings."""
        self.edit_box = QtWidgets.QCheckBox("Edit points")
        self.edit_box.setToolTip("Click a dot to leave its row out (unticked in the DataPad, so its trial leaves "
                                 "Analysis too); click a hollow one to put it back; click a ◆ you typed to delete it")
        add = QtWidgets.QPushButton("Add a point…")
        add.setToolTip("Type in a point by hand (from a paper, say): drawn as a ◆ and kept with this chart")
        add.clicked.connect(self.add_point)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.edit_box)
        row.addWidget(add)
        row.addStretch(1)
        self.form.addRow(row)

    def fit_sets(self) -> list[tuple[str, np.ndarray, np.ndarray]]:
        """What Single Plot's Fit can go through: [(name, x, y)] (none for bars)."""
        return list(getattr(self, "_fit_sets", []))

    def editing(self) -> bool:
        return getattr(self, "edit_box", None) is not None and self.edit_box.isChecked()

    def _removed_entry(self, row: int, col: str, x: float, y: float) -> dict:
        return {"trial": row - 1, "col": col, "y": float(y)}

    def leave_out(self, trial: int) -> None:
        """Leave this trial's row out (its DataPad tick), keeping its dots here, hollow, to put back."""
        xs, ys = self.dots.getData()
        self.removed += [self._removed_entry(r, c, x, y) for (r, c), x, y in zip(self._hits, xs, ys) if r - 1 == trial]
        self.saved()
        self.page.set_hidden(("block", trial), True)

    def put_back(self, trial: int) -> None:
        self.removed = [e for e in self.removed if e["trial"] != trial]
        self.saved()
        self.page.set_hidden(("block", trial), False)

    def _still_out(self) -> list[dict]:
        """The rows left out from here that are still out (one put back elsewhere is forgotten)."""
        out = self.page.pad_view.excluded()
        self.removed = [e for e in self.removed if e["trial"] in out]
        return self.removed

    def _draw_hollow(self, places: list[tuple[int, float, float, str]]) -> None:
        """(entry, x, y, color) for each left-out row shown."""
        self._removed_at = [k for k, _, _, _ in places]
        self.hollow.setData([x for _, x, _, _ in places], [y for _, _, y, _ in places],
                            pen=[pg.mkPen(c, width=1.5) for *_, c in places], brush=None,
                            size=self.point_size.value(), data=np.arange(len(places)))

    def _draw_typed(self, places: list[tuple[int, float, float, str]]) -> None:
        """(entry, x, y, color) for each typed point shown."""
        self._typed_at = [k for k, _, _, _ in places]
        ink = self.plot.getPlotItem().getAxis("left").pen().color()
        self.typed_dots.setData([x for _, x, _, _ in places], [y for _, _, y, _ in places],
                                brush=[pg.mkBrush(c) for *_, c in places], pen=pg.mkPen(ink, width=1),
                                size=self.point_size.value() + 3, data=np.arange(len(places)))
        if places:
            self.legend.addItem(pg.ScatterPlotItem(symbol="d", pen=pg.mkPen(ink, width=1), brush=None, size=9),
                                "typed in")

    def _typed_tip(self, x=None, y=None, data=None) -> str:
        return "Typed in by hand" + ("\nClick to delete it" if self.editing() else
                                     "\nTurn on Edit points to delete it")

    def _hollow_tip(self, x=None, y=None, data=None) -> str:
        if data is None or not 0 <= int(data) < len(self._removed_at):
            return ""
        e = self.removed[self._removed_at[int(data)]]
        return f"Trial {e['trial'] + 1}, left out\nClick to put it back"

    def _typed_clicked(self, _item, points, *_) -> None:
        if self.editing() and len(points) and points[0].data() is not None:
            self.typed.pop(self._typed_at[int(points[0].data())])
            self.saved()
            self.refresh()

    def _hollow_clicked(self, _item, points, *_) -> None:
        if len(points) and points[0].data() is not None:
            self.put_back(self.removed[self._removed_at[int(points[0].data())]]["trial"])

    def _number_box(self) -> QtWidgets.QLineEdit:
        box = QtWidgets.QLineEdit()
        box.setValidator(QtGui.QDoubleValidator())
        box.setPlaceholderText("a number")
        return box

    def _ask(self, title: str, rows: list[tuple[str, QtWidgets.QWidget]], numbers: list) -> bool:
        """A small dialog: these rows, OK once every number box holds a number."""
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle(title)
        f = QtWidgets.QFormLayout(dlg)
        for label, w in rows:
            f.addRow(label, w)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok
                                        | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        ok = bb.button(QtWidgets.QDialogButtonBox.StandardButton.Ok)
        ok.setText("Add")

        def ready(*_) -> None:
            ok.setEnabled(all(_number(b.text()) is not None for b in numbers))
        for b in numbers:
            b.textChanged.connect(ready)
        ready()
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        f.addRow(bb)
        return bool(dlg.exec())

    # -- a row's dot -----------------------------------------------------------------------------
    def _tip(self, x=None, y=None, data=None) -> str:
        if data is None or not 0 <= int(data) < len(self._hits):
            return ""
        row, col = self._hits[int(data)]
        what = f"Click to leave trial {row} out" if self.editing() else "Click to show the trial"
        return f"Trial {row}: {self._shown([col]).get(col, col)} {y:.4g}\n{what}"

    def _clicked(self, _item, points, *_) -> None:
        if len(points) and points[0].data() is not None:
            row = self._hits[int(points[0].data())][0]
            if self.editing():
                self.leave_out(row - 1)
            else:
                self.page.show_trial(row - 1)

    def _prompt(self, text: str) -> None:
        p = self.plot.getPlotItem()
        self.dots.setData([], [])
        self.typed_dots.setData([], [])
        self.hollow.setData([], [])
        self._hits, self._typed_at, self._removed_at, self._fit_sets = [], [], [], []
        p.setTitle(text)
        for side in ("bottom", "left"):
            p.setLabel(side, "")
        p.getAxis("bottom").setTicks(None)
        self.legend.clear()
        self.show_legend()


class ScatterChart(PadChart):
    kind = "Scatter"
    y = "DataPad columns"
    tip = "Plot DataPad columns against another, one point per row"

    def __init__(self, page, cid: str = ""):
        super().__init__(page, cid)
        self.square = SquareBox(self.plot, self._shaped_in_focus)   # square here, in chart focus and its figure
        self.body.addWidget(self.square)
        for it in (self.hollow, self.dots, self.typed_dots):
            self.plot.getPlotItem().addItem(it)
        self.columns = ColumnList("Tick the DataPad columns that run up: one or more")
        self.columns.changed.connect(lambda: (self.refresh(), self.saved()))
        self.form.addRow("Y", self.columns)
        self.xs = QtWidgets.QComboBox()
        self.xs.setToolTip("The DataPad column that runs across (or the rows in order)")
        self.xs.setPlaceholderText("Choose…")            # nothing picked for you
        self.field("x", "X", self.xs)
        swap = QtWidgets.QPushButton("Swap X and Y")
        swap.setToolTip("With one Y ticked: it runs across, X runs up")
        swap.clicked.connect(self._swap)
        self.form.addRow(swap)
        self.logs = {}
        for key, text in (("xlog", "Log X"), ("ylog", "Log Y")):
            b = QtWidgets.QCheckBox(text)
            b.setToolTip("A log scale (leaves out rows at zero or below)")
            b.toggled.connect(lambda on, key=key: self.set_look(key, on))
            self.logs[key] = b
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.logs["xlog"])
        row.addWidget(self.logs["ylog"])
        row.addStretch(1)
        self.form.addRow(row)
        self.look_hooks.append(self._show_logs)
        self.point_size = dot_size(self.main.settings, "scatter_point_size", 7, 20, "The size of the dots")
        self.field("dots", "Dot size", self.point_size)
        self.add_edit_controls()
        self._hook_pad()
        self.apply_theme(self._mode)

    def _shaped_in_focus(self) -> bool:
        """Open in chart focus with a shape picked there: that shapes it, not the square box."""
        sp = getattr(self.page, "single_plot", None)
        return sp is not None and sp.focus_chart is self and bool(sp.chart_box.aspect)

    def _fill_columns(self) -> None:
        names = self._numeric()
        keep = self.xs.currentData()
        self.xs.blockSignals(True)
        self.xs.clear()
        self.xs.addItem("Row order", ROW_ORDER)
        for n, s in self._shown(names).items():
            self.xs.addItem(s, n)
        self.xs.setCurrentIndex(self.xs.findData(keep) if keep is not None else -1)
        self.xs.blockSignals(False)
        super()._fill_columns()

    def choose(self, x: str, ys) -> None:
        """Plot these columns (the ones the student chose in the DataPad)."""
        self._fill_columns()
        self.xs.blockSignals(True)
        self.xs.setCurrentIndex(self.xs.findData(x or ROW_ORDER))
        self.xs.blockSignals(False)
        self.tick([ys] if isinstance(ys, str) else ys)
        self.refresh()
        self.saved()

    def _swap(self) -> None:
        x, ys = self.xs.currentData(), self.columns.ticked()
        if x in (None, ROW_ORDER) or len(ys) != 1:
            return                                       # the row order can't run up
        self.look["xlog"], self.look["ylog"] = self.look["ylog"], self.look["xlog"]
        self.choose(ys[0], [x])

    def _removed_entry(self, row: int, col: str, x: float, y: float) -> dict:
        return {"trial": row - 1, "col": col, "y": float(y), "x": float(x), "xcol": self.xs.currentData()}

    def add_point(self) -> None:
        """Type in a point: X, Y (and which Y column, with several)."""
        xk, ys = self.xs.currentData(), self.columns.ticked()
        if xk is None or not ys:
            return
        xb, yb = self._number_box(), self._number_box()
        col = QtWidgets.QComboBox()
        for c in ys:
            col.addItem(self._shown([c]).get(c, c), c)
        rows = [("X: " + ("row" if xk == ROW_ORDER else plain_name(self._shown([xk]).get(xk, xk))), xb)]
        rows += [("Column", col)] if len(ys) > 1 else []
        rows += [("Y", yb)]
        if self._ask("Add a point", rows, [xb, yb]):
            self.typed.append({"x": _number(xb.text()), "y": _number(yb.text()), "col": col.currentData(),
                               "xcol": xk})
            self.saved()
            self.refresh()

    def _show_logs(self) -> None:
        for key, b in self.logs.items():
            b.blockSignals(True)
            b.setChecked(bool(self.look.get(key)))
            b.blockSignals(False)

    def draw(self) -> None:
        self.legend.clear()
        p = self.plot.getPlotItem()
        xk, ys = self.xs.currentData(), self.columns.ticked()
        if xk is not None and ys and not self._pad_ready():
            return
        if xk is None or not ys or getattr(self.page, "pad_view", None) is None:
            self._prompt("Choose Y and X in this chart's settings" if self._numeric()
                         else "Add columns to the DataPad, then choose Y and X")
            return
        snap = self.page.pad_view.snapshot([c for c in ys + [xk] if c != ROW_ORDER])
        rows = snap["rows"]
        x = np.arange(1, len(rows) + 1, dtype=float) if xk == ROW_ORDER \
            else np.asarray(snap["cols"].get(xk, [np.nan] * len(rows)), float)
        xl = "row" if xk == ROW_ORDER else self._html(xk)
        units = {snap["units"].get(c, "") for c in ys}
        yl = ", ".join(self._html(c) for c in ys)
        label_axis(p, "bottom", xl, "" if xk == ROW_ORDER else snap["units"].get(xk, ""))
        label_axis(p, "left", yl, units.pop() if len(units) == 1 else "")
        p.setTitle(f"{yl} against {xl}")
        X, Y, B, self._hits, self._fit_sets = [], [], [], [], []

        def color_of(c: str) -> str:                     # a series' color: its Y column's
            return colors.PALETTE[ys.index(c) % len(colors.PALETTE)]
        for c in ys:
            y = np.asarray(snap["cols"].get(c, [np.nan] * len(rows)), float)
            keep = np.isfinite(x) & np.isfinite(y)
            if self.look.get("xlog"):
                keep &= x > 0
            if self.look.get("ylog"):
                keep &= y > 0
            idx = np.flatnonzero(keep)
            name = plain_name(self._shown([c]).get(c, c))
            mine = [(t["x"], t["y"]) for t in self.typed if t.get("xcol") == xk and t["col"] == c]
            self._fit_sets.append((name, np.concatenate([x[idx], [t[0] for t in mine]]),
                                   np.concatenate([y[idx], [t[1] for t in mine]])))
            X += list(x[idx])
            Y += list(y[idx])
            self._hits += [(rows[i], c) for i in idx]
            B += [pg.mkBrush(color_of(c))] * len(idx)
            self.legend.addItem(pg.ScatterPlotItem(pen=None, brush=color_of(c), size=8), self._html(c))
        self.dots.setData(X, Y, brush=B, size=self.point_size.value(), data=np.arange(len(X)))
        self._draw_hollow([(k, e["x"], e["y"], color_of(e["col"])) for k, e in enumerate(self._still_out())
                           if e.get("xcol") == xk and e["col"] in ys])
        self._draw_typed([(k, t["x"], t["y"], color_of(t["col"])) for k, t in enumerate(self.typed)
                          if t.get("xcol") == xk and t["col"] in ys])
        self.show_legend()
        p.vb.enableAutoRange()


class BarsChart(PadChart):
    kind = "Bars"
    y = "DataPad columns"
    tip = "Compare the groups: each group's mean ± SEM of DataPad columns"
    list_key = "cols"

    def __init__(self, page, cid: str = ""):
        super().__init__(page, cid)
        self.body.addWidget(self.plot)
        self.columns = ColumnList("Tick the DataPad columns to compare across the groups")
        self.columns.changed.connect(lambda: (self.refresh(), self.saved()))
        self.form.addRow("Columns", self.columns)
        self.error = QtWidgets.QComboBox()
        self.error.addItem("± SEM", "sem")
        self.error.addItem("± SD", "sd")
        self.error.addItem("None", "")
        self.error.setToolTip("The bar round each mean: the standard error of the mean, the standard "
                              "deviation, or none")
        self.field("error", "Error bars", self.error)
        self.each = QtWidgets.QCheckBox("Show each row")
        self.each.setChecked(True)
        self.each.setToolTip("Each row's value as a dot over its group's bar")
        self.field("each", "", self.each)
        self.point_size = dot_size(self.main.settings, "bars_point_size", 6, 20, "The size of the dots")
        self.field("dots", "Dot size", self.point_size)
        self.add_edit_controls()
        self._items: list = []
        self._hook_pad()
        self.apply_theme(self._mode)

    def choose(self, cols) -> None:
        """Compare these columns (the ones the student chose in the DataPad)."""
        self._fill_columns()
        self.tick([cols] if isinstance(cols, str) else cols)
        self.refresh()
        self.saved()

    def _removed_entry(self, row: int, col: str, x: float, y: float) -> dict:
        return {"trial": row - 1, "col": col, "y": float(y), "group": self.page.pad_view.src.group(row - 1)[0]}

    def _groups(self) -> list[str]:
        snap = self.page.pad_view.snapshot([])
        return [g for g in snap["colors"] if g in snap["groups"]] + ([""] if "" in snap["groups"] else [])

    def add_point(self) -> None:
        """Type in a value: its column and group (it counts in that group's mean)."""
        cols = self.columns.ticked()
        if not cols or getattr(self.page, "pad_view", None) is None:
            return
        col, grp, vb = QtWidgets.QComboBox(), QtWidgets.QComboBox(), self._number_box()
        for c in cols:
            col.addItem(self._shown([c]).get(c, c), c)
        for g in self._groups():
            grp.addItem(g or NO_GROUP, g)
        rows = ([("Column", col)] if len(cols) > 1 else []) + [("Group", grp), ("Value", vb)]
        if self._ask("Add a point", rows, [vb]):
            self.typed.append({"col": col.currentData(), "group": grp.currentData(), "y": _number(vb.text())})
            self.saved()
            self.refresh()

    def draw(self) -> None:
        self.legend.clear()
        p = self.plot.getPlotItem()
        for it in self._items:
            p.removeItem(it)
        self._items = []
        for it in (self.dots, self.typed_dots, self.hollow):
            if it.scene() is not None:
                p.removeItem(it)
        cols = self.columns.ticked()
        if cols and not self._pad_ready():
            return
        if not cols or getattr(self.page, "pad_view", None) is None:
            self._prompt("Tick the columns to compare in this chart's settings" if self._numeric()
                         else "Add columns to the DataPad, then tick them here")
            return
        snap = self.page.pad_view.snapshot(cols)
        rows, groups = snap["rows"], snap["groups"]
        order = [g for g in snap["colors"] if g in groups] + ([""] if "" in groups else [])
        G = max(1, len(order))
        ink = self.plot.getPlotItem().getAxis("left").pen().color()
        X, Y, B, self._hits, ticks, typed, hollow = [], [], [], [], [], [], []
        out = self._still_out()
        for j, c in enumerate(cols):
            y = np.asarray(snap["cols"].get(c, [np.nan] * len(rows)), float)
            for k, g in enumerate(order):
                at = j * (G + 1) + k
                color = snap["colors"].get(g, "#969696")
                idx = [i for i in range(len(rows)) if groups[i] == g and np.isfinite(y[i])]
                mine = [t for t in enumerate(self.typed) if t[1]["col"] == c and t[1].get("group") == g]
                if len(cols) == 1:
                    ticks.append((at, g or NO_GROUP))
                hollow += [(n, at, e["y"], color) for n, e in enumerate(out) if e["col"] == c and e.get("group") == g]
                if not idx and not mine:
                    continue
                vals = np.concatenate([y[idx], [t["y"] for _, t in mine]])   # typed values count too
                mean = float(np.mean(vals))
                sd = float(np.std(vals, ddof=1)) if len(vals) > 1 else 0.0
                err = {"sem": sd / np.sqrt(len(vals)), "sd": sd}.get(self.error.currentData(), 0.0)
                bar = pg.PlotDataItem([at - 0.4, at - 0.4, at + 0.4, at + 0.4], [0, mean, mean, 0],
                                      pen=pg.mkPen(color, width=1.5), fillLevel=0, brush=self.faded(color, 110),
                                      name=(g or NO_GROUP) if len(cols) > 1 and j == 0 else None)
                self._items.append(bar)
                if err > 0:                              # an I-beam, drawn as one line
                    lo, hi, cap = mean - err, mean + err, 0.15
                    self._items.append(pg.PlotDataItem([at - cap, at + cap, at, at, at - cap, at + cap],
                                                       [lo, lo, lo, hi, hi, hi], pen=pg.mkPen(ink, width=1.5)))
                spread = at + 0.5 * ((np.arange(len(vals)) * 0.618) % 1.0 - 0.5)   # across the bar, the same each time
                typed += [(n, spread[len(idx) + m], t["y"], color) for m, (n, t) in enumerate(mine)]
                if self.each.isChecked():
                    X += list(spread[:len(idx)])
                    Y += list(y[idx])
                    B += [pg.mkBrush(self.faded(color, 230))] * len(idx)
                    self._hits += [(rows[i], c) for i in idx]
            if len(cols) > 1:
                ticks.append((j * (G + 1) + (G - 1) / 2, plain_name(self._shown([c]).get(c, c))))
        for it in self._items:
            p.addItem(it)                                # a named bar (several columns) joins the legend
        for it in (self.hollow, self.dots, self.typed_dots):
            p.addItem(it)
        self.dots.setData(X, Y, brush=B, pen=pg.mkPen(ink, width=0.6), size=self.point_size.value(),
                          data=np.arange(len(X)))
        self._draw_hollow(hollow)
        self._draw_typed(typed)
        units = {snap["units"].get(c, "") for c in cols}
        p.getAxis("bottom").setTicks([ticks])
        p.setLabel("bottom", "")
        yl = ", ".join(self._html(c) for c in cols)
        label_axis(p, "left", yl, units.pop() if len(units) == 1 else "")
        p.setTitle(f"{yl} by group")
        self.show_legend()
        p.vb.enableAutoRange()
