"""Group alike stimuli, in the Groups window: tick measures (stimmeasures) to place each
stimulus on a map, the two directions they differ most along; the slider joins those
close on it into groups, live, until Keep these groups makes them ordinary groups. Also
what the groups are colored along, and how a combined group's average is drawn.

The panel only shows things and says what was changed; the Analysis page does the rest.
"""
from __future__ import annotations

import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

from .stimmeasures import MEASURES, SHAPE, SHAPE_NAME


def _note(text: str = "") -> QtWidgets.QLabel:
    lbl = QtWidgets.QLabel(text)
    lbl.setWordWrap(True)
    lbl.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred)
    lbl.setStyleSheet("color: gray")
    return lbl


def measure_name(key: str | None) -> str:
    return SHAPE_NAME.split(" (")[0].lower() if key == SHAPE else MEASURES[key][0] if key in MEASURES else ""


class AlikePanel(QtWidgets.QWidget):
    changed = QtCore.Signal(dict)            # what changed of the setting: feats, by, n, close
    keep_requested = QtCore.Signal()
    color_by_picked = QtCore.Signal(str)     # "" the Groups setting, "pc1" along the map, or a measure
    avg_each_picked = QtCore.Signal(bool)    # a combined group's average: each stimulus's (True) or all at once
    SIZE = 230                               # the map: a square

    def __init__(self):
        super().__init__()
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 10, 0, 0)
        lay.setSpacing(4)
        lay.addWidget(QtWidgets.QLabel("<b>Group alike stimuli</b>"))
        lay.addWidget(_note("Compare stimuli by:"))
        grid = QtWidgets.QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setVerticalSpacing(0)
        self.ticks: dict[str, QtWidgets.QCheckBox] = {}
        shape = QtWidgets.QCheckBox(SHAPE_NAME)
        shape.setToolTip("Each stimulus slid along the other until they line up best: the same shape is alike "
                         "whatever its size")
        grid.addWidget(shape, 0, 0, 1, 2)
        self.ticks[SHAPE] = shape
        for k, (key, (name, _)) in enumerate(MEASURES.items()):
            box = QtWidgets.QCheckBox(name)
            grid.addWidget(box, 1 + k // 2, k % 2)
            self.ticks[key] = box
        for box in self.ticks.values():
            box.toggled.connect(lambda *_: self.changed.emit({"feats": self.feats()}))
        lay.addLayout(grid)
        self.by = QtWidgets.QComboBox()
        self.by.addItem("Number of groups", "n")
        self.by.addItem("How close counts as alike", "close")
        self.by.setToolTip("Pick how many groups, or how close on the map two stimuli must be to share a group")
        self.by.activated.connect(lambda *_: self.changed.emit({"by": self.by.currentData()}))
        lay.addWidget(self.by)
        row = QtWidgets.QHBoxLayout()
        self.slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.value = QtWidgets.QLabel()
        self.value.setMinimumWidth(70)
        row.addWidget(self.slider, 1)
        row.addWidget(self.value)
        lay.addLayout(row)
        self.cut_note = _note()
        lay.addWidget(self.cut_note)
        self._timer = QtCore.QTimer(self, singleShot=True, interval=150)    # regroup once you stop
        self._timer.timeout.connect(self._slid)
        self.slider.valueChanged.connect(self._sliding)
        self.keep = QtWidgets.QPushButton("Keep these groups")
        self.keep.setToolTip("Make the groups the map joined ordinary groups: rename, split or drag them, and they "
                             "stay when you change the measures. Until then the list follows the map")
        self.keep.clicked.connect(self.keep_requested)
        lay.addWidget(self.keep)
        self.plot = pg.PlotWidget(background=None)
        self.plot.setFixedSize(self.SIZE, self.SIZE)
        self.plot.setMouseEnabled(False, False)
        self.plot.setMenuEnabled(False)
        self.plot.hideButtons()
        lay.addWidget(self.plot, 0, QtCore.Qt.AlignmentFlag.AlignHCenter)
        self.map_note = _note()
        lay.addWidget(self.map_note)
        form = QtWidgets.QFormLayout()
        form.setContentsMargins(0, 4, 0, 0)
        self.color_by = QtWidgets.QComboBox()
        self.color_by.setToolTip("Color the groups along a measure (or the map), lowest to highest, in the Groups "
                                 "color map (Viridis for Palette). A color you give a group stays")
        self.color_by.activated.connect(lambda *_: self.color_by_picked.emit(self.color_by.currentData() or ""))
        form.addRow("Color groups by", self.color_by)
        self.avg = QtWidgets.QComboBox()
        self.avg.addItem("All its trials", False)
        self.avg.addItem("Each stimulus", True)
        self.avg.setToolTip("A group holding several stimuli (combined, or joined by the map): one average of all "
                            "its trials, or an average for each stimulus in it, drawn together in its color")
        self.avg.activated.connect(lambda *_: self.avg_each_picked.emit(bool(self.avg.currentData())))
        form.addRow("Combined average", self.avg)
        lay.addLayout(form)
        self._n = 0                                # how many stimuli there are

    def feats(self) -> list[str]:
        return [k for k, box in self.ticks.items() if box.isChecked()]

    # -- the slider -----------------------------------------------------------------------
    def _sliding(self, v: int) -> None:
        self.value.setText(self._value_text(v))
        self._timer.start()

    def _slid(self) -> None:
        v = self.slider.value()
        if self.by.currentData() == "close":
            self.changed.emit({"close": v})
        else:
            self.changed.emit({"n": 0 if v >= self._n else v})

    def _value_text(self, v: int) -> str:
        if not self.slider.isEnabled():
            return "each its own"
        if self.by.currentData() == "close":
            return f"{v}%" if v else "0%: each its own"
        return f"{v}: each its own" if v >= self._n else f"{v} groups"

    # -- showing ---------------------------------------------------------------------------
    def show_state(self, s: dict) -> None:
        """s: feats, by, n, close (as saved); stimuli (how many); map: None or {points: [(x,
        y, color, name)], joined: [[point indices in one group]...], share, mostly};
        why (when there's no map); color_by; avg_each; can_keep."""
        self._n = n = s["stimuli"]
        for key, box in self.ticks.items():
            box.blockSignals(True)
            box.setChecked(key in s["feats"])
            box.blockSignals(False)
        m = s["map"]
        self.by.setCurrentIndex(max(0, self.by.findData(s["by"])))
        self.slider.blockSignals(True)
        self.slider.setEnabled(m is not None)
        if s["by"] == "close":
            self.slider.setRange(0, 100)
            self.slider.setValue(s["close"])
            self.cut_note.setText("Stimuli closer than this on the map share a group (100%: the two farthest apart).")
        else:
            self.slider.setRange(1, max(1, n))
            self.slider.setValue(s["n"] if 0 < s["n"] < n else n)
            self.cut_note.setText("How many groups: the stimuli closest on the map join first.")
        self.slider.blockSignals(False)
        self.value.setText(self._value_text(self.slider.value()))
        self.keep.setEnabled(s["can_keep"])
        self._draw(m)
        if m is None:
            self.map_note.setText(s["why"])
        else:
            parts = []
            for a, (where, way) in enumerate((("Main difference", "to the right"), ("Next difference", "up"))):
                key = m["mostly"][a]
                parts.append(f"<b>{where} ({round(100 * m['share'][a])}%)</b>"
                             + (f": mostly {measure_name(key)}" + (f", bigger {way}" if key in MEASURES else "")
                                if key else ""))
            self.map_note.setText("Near each other = alike. " + ". ".join(parts) + ".")
        self.color_by.blockSignals(True)
        self.color_by.clear()
        self.color_by.addItem("The Groups setting", "")
        self.color_by.addItem("Along the map", "pc1")
        self.color_by.model().item(1).setEnabled(m is not None)
        for key, (name, _) in MEASURES.items():
            self.color_by.addItem(name, key)
        self.color_by.setCurrentIndex(max(0, self.color_by.findData(s["color_by"])))
        self.color_by.blockSignals(False)
        self.avg.setCurrentIndex(1 if s["avg_each"] else 0)

    def _draw(self, m: dict | None) -> None:
        plot = self.plot
        plot.clear()
        plot.setVisible(m is not None)
        if m is None or not m["points"]:
            return
        ink = self.palette().color(QtGui.QPalette.ColorRole.WindowText)
        pts = m["points"]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        for members in m["joined"]:                # a group's stimuli, each joined to its middle
            if len(members) < 2:
                continue
            cx = sum(xs[k] for k in members) / len(members)
            cy = sum(ys[k] for k in members) / len(members)
            color = QtGui.QColor(pts[members[0]][2])
            color.setAlpha(140)
            for k in members:
                plot.addItem(pg.PlotCurveItem([cx, xs[k]], [cy, ys[k]], pen=pg.mkPen(color, width=2)))
        plot.addItem(pg.ScatterPlotItem(xs, ys, size=10, brush=[pg.mkBrush(p[2]) for p in pts],
                                        pen=pg.mkPen(self.palette().color(QtGui.QPalette.ColorRole.Base), width=1)))
        font = QtGui.QFont()
        font.setPointSizeF(max(7.0, font.pointSizeF() - 3))
        for x, y, _, name in pts:
            t = pg.TextItem(name if len(name) <= 22 else name[:21] + "…", color=ink, anchor=(-0.08, 0.5))
            t.setFont(font)
            t.setPos(x, y)
            plot.addItem(t)
        span = max(max(xs) - min(xs), max(ys) - min(ys), 1e-9) * 1.35      # square, fitted to the stimuli
        cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
        plot.setXRange(cx - span / 2 + span * 0.08, cx + span / 2 + span * 0.08, padding=0)
        plot.setYRange(cy - span / 2, cy + span / 2, padding=0)
        plot.setLabel("bottom", f"Main difference ({round(100 * m['share'][0])}%)")
        plot.setLabel("left", f"Next difference ({round(100 * m['share'][1])}%)")
        for side in ("bottom", "left"):
            plot.getAxis(side).setStyle(showValues=False)
