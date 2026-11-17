"""Single Plot: one figure, made to look right. It plots DataPad columns (Plot… in the
DataPad, or right-click a column) and stays linked to them: change the DataPad and the
figure follows.

On the right: what's plotted (each series: Y, against X, as points or a line, every trial or
the mean ± SEM at each x, split into the groups or not), the axes (labels, where they start
and end, log scales), and the look (text size, line and point sizes, the legend, the box,
grid lines, a fitted curve) and the figure's size in mm, as it will print. Labels are
written plainly; I_B comes out with a true subscript.

Send to Canvas puts a copy on the Plot Canvas, to lay out with others; Save figure… saves
it on its own (PDF, SVG, PNG, TIFF).

Chart focus: Quick charts ▾ opens any quick chart here, big, with its settings beside it.
Make a figure turns what it shows into a figure to finish and send to the Canvas. Back to
the whiteboard puts it back where it was.
"""
from __future__ import annotations

import io

from matplotlib.figure import Figure
from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

from . import figspec as F
from .viewkit import scroll_column


def _opt_float(text: str):
    try:
        return float(text)
    except ValueError:
        return None


class _SeriesRow(QtWidgets.QFrame):
    changed = QtCore.Signal()
    remove = QtCore.Signal(object)

    def __init__(self, s: dict, names: list[str]):
        super().__init__()
        self.s = s
        self.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        f = QtWidgets.QFormLayout(self)
        f.setContentsMargins(6, 4, 6, 4)
        self.y = QtWidgets.QComboBox()
        self.x = QtWidgets.QComboBox()
        self.x.addItem("Trial order", "")
        for n in names:
            self.y.addItem(n, n)
            self.x.addItem(n, n)
        self.y.setCurrentIndex(max(0, self.y.findData(s.get("y"))))
        self.x.setCurrentIndex(max(0, self.x.findData(s.get("x", ""))))
        self.style = QtWidgets.QComboBox()
        for k, lab in F.STYLES.items():
            self.style.addItem(lab, k)
        self.style.setCurrentIndex(max(0, self.style.findData(s.get("style"))))
        self.avg = QtWidgets.QComboBox()
        for k, lab in F.AVERAGES.items():
            self.avg.addItem(lab, k)
        self.avg.setCurrentIndex(max(0, self.avg.findData(s.get("average", ""))))
        self.avg.setToolTip("Every trial its own point, or the trials with the same x averaged (an I–V curve "
                            "from repeated steps), with error bars")
        self.groups = QtWidgets.QCheckBox("Split into groups")
        self.groups.setChecked(bool(s.get("by_group", True)))
        self.label = QtWidgets.QLineEdit(s.get("label", ""))
        self.label.setPlaceholderText("legend: automatic")
        self.color = QtWidgets.QPushButton()
        self.color.setToolTip("The series' color; blank uses the groups' colors")
        self._show_color()
        rm = QtWidgets.QToolButton(text="✕")
        rm.setToolTip("Take this series off")
        rm.clicked.connect(lambda: self.remove.emit(self))
        head = QtWidgets.QHBoxLayout()
        head.addWidget(self.y, 1)
        head.addWidget(rm)
        f.addRow("Y", head)
        f.addRow("Against", self.x)
        f.addRow("Draw", self.style)
        f.addRow("Trials", self.avg)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.groups)
        row.addWidget(self.color)
        f.addRow("", row)
        f.addRow("Legend", self.label)
        for w in (self.y, self.x, self.style, self.avg):
            w.currentIndexChanged.connect(self._edited)
        self.groups.toggled.connect(self._edited)
        self.label.editingFinished.connect(self._edited)
        self.color.clicked.connect(self._pick_color)

    def _show_color(self) -> None:
        c = self.s.get("color")
        self.color.setText("Color" if not c else "")
        self.color.setStyleSheet(f"background:{c}" if c else "")

    def _pick_color(self) -> None:
        m = QtWidgets.QMenu(self)
        m.addAction("Groups' colors", lambda: self._set_color(""))
        for c in F.PALETTE:
            m.addAction(QtGui.QIcon(_swatch(c)), c, lambda c=c: self._set_color(c))
        m.addAction("Other…", self._other_color)
        m.exec(QtGui.QCursor.pos())

    def _set_color(self, c: str) -> None:
        self.s["color"] = c
        self._show_color()
        self.changed.emit()

    def _other_color(self) -> None:
        c = QtWidgets.QColorDialog.getColor(QtGui.QColor(self.s.get("color") or "#1f5fbf"), self)
        if c.isValid():
            self._set_color(c.name())

    def _edited(self, *_):
        self.s.update(y=self.y.currentData(), x=self.x.currentData(), style=self.style.currentData(),
                      average=self.avg.currentData(), by_group=self.groups.isChecked(), label=self.label.text())
        self.changed.emit()


def _swatch(c: str) -> QtGui.QPixmap:
    p = QtGui.QPixmap(12, 12)
    p.fill(QtGui.QColor(c))
    return p


class SinglePlotPage(QtWidgets.QWidget):
    """The figure, big, and its settings beside it."""

    def __init__(self, page):
        super().__init__()
        self.page = page                         # the Analysis page: its DataPad is the data
        self.spec: dict | None = None
        self.data: dict = {}
        self.editing = None                      # the Plot Canvas figure being changed, if any
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        self.outer = lay                          # the DataPad's left rail (Trials, Groups…) docks at its start
        self.focus_chart = None                  # a whiteboard chart open in focus mode
        self.mode = QtWidgets.QStackedWidget()
        lay.addWidget(self.mode, 1)
        fig_page = QtWidgets.QWidget()
        fig_lay = QtWidgets.QHBoxLayout(fig_page)
        fig_lay.setContentsMargins(0, 0, 0, 0)
        self.mode.addWidget(fig_page)
        focus_page = QtWidgets.QWidget()
        fl = QtWidgets.QHBoxLayout(focus_page)
        fl.setContentsMargins(0, 0, 0, 0)
        fcol = QtWidgets.QVBoxLayout()
        fbar = QtWidgets.QHBoxLayout()
        self.btn_back = QtWidgets.QPushButton("◀ Figures")
        self.btn_back.setToolTip("Back to the figure you were making (the chart stays in Quick charts › Your charts)")
        self.focus_title = QtWidgets.QLabel()
        self.focus_title.setMinimumWidth(0)          # long names shrink, never widen the window
        self.focus_title.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred)
        self.btn_figure = QtWidgets.QPushButton("Make a figure")
        self.btn_figure.setToolTip("What this chart shows now, as a figure to finish here and send to the Canvas")
        fbar.addWidget(self.btn_back)
        fbar.addWidget(self._quick_button())
        self._fbar = fbar                        # the "only the selection" tick goes here (page.only_selection_box)
        fbar.addWidget(self.focus_title, 1)
        fbar.addWidget(self.btn_figure)
        fcol.addLayout(fbar)
        self._fbar2 = QtWidgets.QHBoxLayout()        # a second row: what the charts show (page's ticks)
        self._fbar2.addStretch(1)
        fcol.addLayout(self._fbar2)
        self.focus_holder = QtWidgets.QVBoxLayout()
        fcol.addLayout(self.focus_holder, 1)
        fl.addLayout(fcol, 1)
        self.focus_side = QtWidgets.QVBoxLayout()
        fl.addLayout(self.focus_side)
        self.mode.addWidget(focus_page)
        self.btn_back.clicked.connect(lambda: self.leave_focus(switch=False))
        self.btn_figure.clicked.connect(self._chart_figure)
        self.btn_chart_canvas = QtWidgets.QPushButton("Send to Canvas")
        self.btn_chart_canvas.setToolTip("Put this chart on the Plot Canvas as a figure, as it looks now, in one step")
        self.btn_chart_canvas.clicked.connect(self._chart_to_canvas)
        fbar.addWidget(self.btn_chart_canvas)
        left = QtWidgets.QVBoxLayout()
        bar = QtWidgets.QHBoxLayout()
        bar.addWidget(self._quick_button())
        self.title = QtWidgets.QLabel()
        bar.addWidget(self.title, 1)
        self.btn_canvas = QtWidgets.QPushButton("Send to Canvas")
        self.btn_canvas.setToolTip("Put this figure on the Plot Canvas, to lay out beside others")
        self.btn_save = QtWidgets.QPushButton("Save figure…")
        self.btn_copy = QtWidgets.QPushButton("Copy")
        for b in (self.btn_copy, self.btn_save, self.btn_canvas):
            bar.addWidget(b)
        left.addLayout(bar)
        self.area = QtWidgets.QWidget()
        self.area.setStyleSheet("background: #e9ebee")
        al = QtWidgets.QGridLayout(self.area)
        # the figure as a picture, drawn at its size in mm (a live matplotlib canvas resizes the
        # figure to itself and draws over its old picture): what you see is what saves
        self.canvas = QtWidgets.QLabel()
        self.canvas.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.canvas.setStyleSheet("background: white")
        al.addWidget(self.canvas, 0, 0, QtCore.Qt.AlignmentFlag.AlignCenter)
        self._redraw = QtCore.QTimer(self, singleShot=True, interval=60)   # many edits: draw once
        self._redraw.timeout.connect(self._render)
        self.empty = QtWidgets.QLabel("Make a figure from the DataPad: Plot…, or right-click a column › Plot against.")
        self.empty.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        al.addWidget(self.empty, 0, 0)
        left.addWidget(self.area, 1)
        fig_lay.addLayout(left, 1)

        self.side = QtWidgets.QWidget()
        sl = QtWidgets.QVBoxLayout(self.side)
        sl.setContentsMargins(8, 0, 0, 0)
        # what's plotted
        self.data_head = _head("Data")
        sl.addWidget(self.data_head)
        self.kind = QtWidgets.QComboBox()
        self.kind.addItem("Y against X", "xy")
        self.kind.addItem("Bars by group", "bar")
        sl.addWidget(self.kind)
        self.series_box = QtWidgets.QVBoxLayout()
        sl.addLayout(self.series_box)
        self.btn_add = QtWidgets.QPushButton("+ Series")
        sl.addWidget(self.btn_add)
        self.from_chart = QtWidgets.QLabel()
        self.from_chart.setWordWrap(True)
        sl.addWidget(self.from_chart)
        # axes
        sl.addWidget(_head("Axes"))
        f = QtWidgets.QFormLayout()
        self.t_title = QtWidgets.QLineEdit()
        self.t_x = QtWidgets.QLineEdit()
        self.t_y = QtWidgets.QLineEdit()
        for w, tip in ((self.t_x, "x axis label; blank: the column's name and unit"),
                       (self.t_y, "y axis label; blank: the column's name and unit"),
                       (self.t_title, "Above the plot; blank: none")):
            w.setPlaceholderText("automatic" if w is not self.t_title else "none")
            w.setToolTip(tip + ". I_B gives I with a subscript B")
        self.x0, self.x1, self.y0, self.y1 = (QtWidgets.QLineEdit() for _ in range(4))
        for w in (self.x0, self.x1, self.y0, self.y1):
            w.setPlaceholderText("auto")
            w.setMaximumWidth(70)
        self.xlog = QtWidgets.QCheckBox("log")
        self.ylog = QtWidgets.QCheckBox("log")
        f.addRow("Title", self.t_title)
        f.addRow("X label", self.t_x)
        f.addRow("X from", _row(self.x0, QtWidgets.QLabel("to"), self.x1, self.xlog))
        f.addRow("Y label", self.t_y)
        f.addRow("Y from", _row(self.y0, QtWidgets.QLabel("to"), self.y1, self.ylog))
        sl.addLayout(f)
        # look
        sl.addWidget(_head("Look"))
        g = QtWidgets.QFormLayout()
        self.font = QtWidgets.QDoubleSpinBox(decimals=1, minimum=5, maximum=30, singleStep=0.5, suffix=" pt")
        self.lw = QtWidgets.QDoubleSpinBox(decimals=1, minimum=0.2, maximum=8, singleStep=0.2, suffix=" pt")
        self.ms = QtWidgets.QDoubleSpinBox(decimals=1, minimum=0.5, maximum=20, singleStep=0.5, suffix=" pt")
        self.legend = QtWidgets.QComboBox()
        self.legend.addItems(F.LEGENDS)
        self.despine = QtWidgets.QCheckBox("Open box (no top and right lines)")
        self.grid = QtWidgets.QCheckBox("Grid lines")
        self.zero = QtWidgets.QCheckBox("Lines at zero")
        self.points = QtWidgets.QCheckBox("Each trial's point on the bars")
        self.fit = QtWidgets.QComboBox()
        for k, lab in F.FITS.items():
            self.fit.addItem(lab, k)
        self.w_mm = QtWidgets.QDoubleSpinBox(decimals=0, minimum=20, maximum=400, suffix=" mm")
        self.h_mm = QtWidgets.QDoubleSpinBox(decimals=0, minimum=20, maximum=400, suffix=" mm")
        self.font.setToolTip("The axis labels' size (and, on Auto, the others follow it)")
        self.font_ticks = QtWidgets.QDoubleSpinBox(decimals=1, minimum=0, maximum=30, singleStep=0.5, suffix=" pt")
        self.font_ticks.setSpecialValueText("Auto")
        self.font_ticks.setToolTip("The numbers along the axes; Auto: a little smaller than the labels")
        self.font_title = QtWidgets.QDoubleSpinBox(decimals=1, minimum=0, maximum=40, singleStep=0.5, suffix=" pt")
        self.font_title.setSpecialValueText("Auto")
        self.font_title.setToolTip("The title; Auto: a little bigger than the labels")
        self.ticks = QtWidgets.QComboBox()
        for lab, key in (("Outside", "out"), ("Inside", "in"), ("Across", "inout")):
            self.ticks.addItem(lab, key)
        self.ticks.setToolTip("Which way the tick marks point from the axis")
        self.tick_len = QtWidgets.QDoubleSpinBox(decimals=1, minimum=0, maximum=15, singleStep=0.5, suffix=" pt")
        self.tick_len.setToolTip("How long the tick marks are (0: none)")
        g.addRow("Labels", self.font)
        g.addRow("Numbers", self.font_ticks)
        g.addRow("Title", self.font_title)
        g.addRow("Ticks", _row(self.ticks, self.tick_len))
        g.addRow("Lines", self.lw)
        g.addRow("Points", self.ms)
        g.addRow("Legend", self.legend)
        g.addRow(self.despine)
        g.addRow(self.grid)
        g.addRow(self.zero)
        g.addRow(self.points)
        g.addRow("Size", _row(self.w_mm, QtWidgets.QLabel("×"), self.h_mm))
        sl.addLayout(g)
        sl.addStretch(1)
        scroll = scroll_column(self.side)
        # on the right, tabs: the figure's settings, and fitting a curve to it
        self.right = QtWidgets.QTabWidget()
        self.right.setMinimumWidth(300)
        self.right.setMaximumWidth(340)
        self.right.addTab(scroll, "Figure")
        self.right.addTab(self._fit_tab(), "Fit")
        fig_lay.addWidget(self.right)

        for w in (self.t_title, self.t_x, self.t_y, self.x0, self.x1, self.y0, self.y1):
            w.editingFinished.connect(self._edited)
        self.ticks.currentIndexChanged.connect(self._edited)
        for w in (self.font, self.font_ticks, self.font_title, self.tick_len, self.lw, self.ms, self.w_mm, self.h_mm):
            w.valueChanged.connect(self._edited)
        for w in (self.legend, self.kind):
            w.currentIndexChanged.connect(self._edited)
        self.fit.currentIndexChanged.connect(self._fit_edited)
        for w in (self.xlog, self.ylog, self.despine, self.grid, self.zero, self.points):
            w.toggled.connect(self._edited)
        self.btn_add.clicked.connect(self._add_series)
        self.btn_canvas.clicked.connect(self._to_canvas)
        self.btn_save.clicked.connect(self._save)
        self.btn_copy.clicked.connect(self._copy)
        self._loading = False
        self.show_spec(None)

    # -- the figure -------------------------------------------------------------------------
    def show_spec(self, spec: dict | None) -> None:
        if self.focus_chart is not None:
            self.leave_focus(switch=False)
        self.spec = spec
        has = spec is not None
        self.side.setEnabled(has)
        for b in (self.btn_canvas, self.btn_save, self.btn_copy):
            b.setEnabled(has)
        self.canvas.setVisible(has)
        self.empty.setVisible(not has)
        self.btn_canvas.setText("Update on Canvas" if self.editing is not None else "Send to Canvas")
        if not has:
            self.title.setText("")
            return
        self._loading = True
        s = spec
        self.kind.setCurrentIndex(max(0, self.kind.findData(s["kind"])))
        self.t_title.setText(s["title"])
        self.t_x.setText(s["xlabel"])
        self.t_y.setText(s["ylabel"])
        for w, v in ((self.x0, s["xlim"][0]), (self.x1, s["xlim"][1]), (self.y0, s["ylim"][0]),
                     (self.y1, s["ylim"][1])):
            w.setText("" if v is None else f"{v:g}")
        self.xlog.setChecked(s["xlog"])
        self.ylog.setChecked(s["ylog"])
        self.font.setValue(s["font"])
        self.font_ticks.setValue(s.get("font_ticks", 0.0) or 0.0)
        self.font_title.setValue(s.get("font_title", 0.0) or 0.0)
        self.ticks.setCurrentIndex(max(0, self.ticks.findData(s.get("ticks", "out"))))
        self.tick_len.setValue(s.get("tick_len", 3.5))
        self.lw.setValue(s["lw"])
        self.ms.setValue(s["ms"])
        self.legend.setCurrentText(s["legend"])
        self.fit.setCurrentIndex(max(0, self.fit.findData(s["fit"])))
        self.fit_degree.setValue(int(s.get("fit_degree", 1)))
        self.fit_degree.setEnabled(s.get("fit") == "poly")
        fx = (list(s.get("fit_x") or []) + [None, None])[:2]
        self.fit_x0.setText("" if fx[0] is None else f"{fx[0]:g}")
        self.fit_x1.setText("" if fx[1] is None else f"{fx[1]:g}")
        self.despine.setChecked(s["despine"])
        self.grid.setChecked(s["grid"])
        self.zero.setChecked(s["zero"])
        self.points.setChecked(s.get("points", True))
        self.w_mm.setValue(s["w_mm"])
        self.h_mm.setValue(s["h_mm"])
        self._fill_series()
        drawn = s["kind"] == "curves"
        for w in (self.kind, self.btn_add):
            w.setVisible(not drawn)
        self.from_chart.setVisible(drawn)
        self.from_chart.setText("The lines of a quick chart, as they were when you made the figure. "
                                "Make it again from the chart to update it." if drawn else "")
        self._loading = False
        self.data_changed()

    def _fill_series(self) -> None:
        while self.series_box.count():
            w = self.series_box.takeAt(0).widget()
            if w:
                w.deleteLater()
        names = self.page.pad_view.pad.names()
        for s in self.spec["series"]:
            row = _SeriesRow(s, names)
            row.changed.connect(self.data_changed)
            row.remove.connect(self._remove_series)
            self.series_box.addWidget(row)

    def _add_series(self) -> None:
        names = self.page.pad_view.pad.names()
        if not names:
            return
        last = self.spec["series"][-1] if self.spec["series"] else {"x": "", "y": names[0]}
        self.spec["series"].append(F.new_series(last["x"], last["y"]))
        self._fill_series()
        self.data_changed()

    def _remove_series(self, row) -> None:
        self.spec["series"].remove(row.s)
        self._fill_series()
        self.data_changed()

    def _edited(self, *_):
        if self._loading or self.spec is None:
            return
        s = self.spec
        s.update(kind=self.kind.currentData(), title=self.t_title.text(), xlabel=self.t_x.text(),
                 ylabel=self.t_y.text(), xlim=[_opt_float(self.x0.text()), _opt_float(self.x1.text())],
                 ylim=[_opt_float(self.y0.text()), _opt_float(self.y1.text())], xlog=self.xlog.isChecked(),
                 ylog=self.ylog.isChecked(), font=self.font.value(), font_ticks=self.font_ticks.value(),
                 font_title=self.font_title.value(), ticks=self.ticks.currentData(), tick_len=self.tick_len.value(),
                 lw=self.lw.value(), ms=self.ms.value(),
                 legend=self.legend.currentText(), despine=self.despine.isChecked(),
                 grid=self.grid.isChecked(), zero=self.zero.isChecked(), points=self.points.isChecked(),
                 w_mm=self.w_mm.value(), h_mm=self.h_mm.value())
        self.draw()

    def data_changed(self) -> None:
        """The DataPad changed (or what's plotted): read the columns again and draw."""
        if self.spec is None:
            return
        if self.spec["kind"] == "curves":
            self.title.setText(f"<b>{self.data.get('chart', 'Chart')}</b> · drawn from a quick chart")
            self.draw()
            self._show_fit()
            return
        self.data = self.page.pad_view.snapshot(F.columns_used(self.spec))
        cols = ", ".join(s["y"] for s in self.spec["series"])
        self.title.setText(f"<b>{cols}</b> · {len(self.data.get('rows', []))} trials, linked to the DataPad")
        self.draw()
        self._show_fit()

    def draw(self) -> None:
        if self.spec is not None and self.isVisible():
            self._redraw.start()

    def _render(self) -> None:
        """Draw the figure at its size, scaled to fit the area, sharp on high-density screens."""
        if self.spec is None or not self.isVisible():
            return
        w_in, h_in = self.spec["w_mm"] / 25.4, self.spec["h_mm"] / 25.4
        avail = self.area.size()
        dpi = self.logicalDpiX() or 96
        zoom = min(1.6, max(0.4, min((avail.width() - 40) / (w_in * dpi), (avail.height() - 40) / (h_in * dpi))))
        ratio = self.devicePixelRatioF() or 1.0
        fig = Figure(figsize=(w_in, h_in), dpi=dpi * zoom * ratio)
        try:
            F.draw(fig, self.spec, self.data)
            fig.tight_layout()
        except Exception as e:                      # a bad range or label: say so, don't crash
            fig.clear()
            fig.text(0.5, 0.5, f"Can't draw this: {e}", ha="center", va="center", wrap=True)
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=dpi * zoom * ratio, facecolor="white")
        pix = QtGui.QPixmap()
        pix.loadFromData(buf.getvalue())
        pix.setDevicePixelRatio(ratio)
        self.canvas.setFixedSize(int(w_in * dpi * zoom), int(h_in * dpi * zoom))
        self.canvas.setPixmap(pix)

    def showEvent(self, ev):
        super().showEvent(ev)
        self.page.dock_panels(self.outer)          # pick trials and colors here too
        if self.spec is not None and self.mode.currentIndex() == 0:
            self.data_changed()

    def hideEvent(self, ev):
        super().hideEvent(ev)
        # the chart stays open here while you're on another tab: no whiteboard to go back to

    def _maybe_leave(self) -> None:
        if not self.isVisible():                 # another tab: the chart goes home to the whiteboard
            self.leave_focus(switch=False)

    # -- fitting -------------------------------------------------------------------------------
    def _fit_tab(self) -> QtWidgets.QWidget:
        """Fit a curve through one line of the figure, over an x range: its equation, each
        parameter ± standard error, and R²; drawn on the figure as a red dashed line."""
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(8, 8, 8, 8)
        f = QtWidgets.QFormLayout()
        self.fit_line = QtWidgets.QComboBox()
        self.fit_line.setToolTip("Which line (or series) to fit")
        self.fit.setToolTip("The curve to fit. Polynomial (degree 1: a straight line), exponential decay or rise "
                            "(time constants), Boltzmann (activation curves), power and log laws")
        self.fit_degree = QtWidgets.QSpinBox(minimum=0, maximum=9, value=1)
        self.fit_degree.setToolTip("The polynomial's degree: 1 a straight line, 2 a parabola…")
        self.fit_x0, self.fit_x1 = QtWidgets.QLineEdit(), QtWidgets.QLineEdit()
        for e in (self.fit_x0, self.fit_x1):
            e.setPlaceholderText("all")
            e.setMaximumWidth(80)
        use = QtWidgets.QPushButton("Use the x axis's range")
        use.setToolTip("Fit just what the figure's x axis shows")
        f.addRow("Fit", self.fit)
        f.addRow("Degree", self.fit_degree)
        f.addRow("Through", self.fit_line)
        f.addRow("x from", _row(self.fit_x0, QtWidgets.QLabel("to"), self.fit_x1))
        f.addRow("", use)
        v.addLayout(f)
        self.fit_result = QtWidgets.QLabel("Pick a model to fit.")
        self.fit_result.setWordWrap(True)
        self.fit_result.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        self.fit_result.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        self.fit_result.setMargin(8)
        v.addWidget(self.fit_result)
        copy = QtWidgets.QPushButton("Copy the results")
        copy.clicked.connect(lambda: QtWidgets.QApplication.clipboard().setText(
            QtGui.QTextDocumentFragment.fromHtml(self.fit_result.text()).toPlainText()))
        v.addWidget(copy)
        v.addStretch(1)
        self.fit_degree.valueChanged.connect(self._fit_edited)
        self.fit_line.currentIndexChanged.connect(self._fit_edited)
        for e in (self.fit_x0, self.fit_x1):
            e.editingFinished.connect(self._fit_edited)
        use.clicked.connect(self._fit_use_view)
        return w

    def _fit_edited(self, *_) -> None:
        if self._loading or self.spec is None:
            return
        self.spec.update(fit=self.fit.currentData(), fit_degree=self.fit_degree.value(),
                         fit_line=max(0, self.fit_line.currentIndex()),
                         fit_x=[_opt_float(self.fit_x0.text()), _opt_float(self.fit_x1.text())])
        self.fit_degree.setEnabled(self.spec["fit"] == "poly")
        self.draw()
        self._show_fit()

    def _fit_use_view(self) -> None:
        lo, hi = self.spec.get("xlim") or [None, None] if self.spec else (None, None)
        self.fit_x0.setText("" if lo is None else f"{lo:g}")
        self.fit_x1.setText("" if hi is None else f"{hi:g}")
        self._fit_edited()

    def _show_fit(self) -> None:
        """The lines to pick from, and the result, for this figure and its numbers."""
        if self.spec is None:
            return
        names = [n for n, _, _ in F.fit_lines(self.spec, self.data)]
        keep = self.spec.get("fit_line", 0)
        self.fit_line.blockSignals(True)
        self.fit_line.clear()
        self.fit_line.addItems(names)
        self.fit_line.setCurrentIndex(min(keep, len(names) - 1) if names else -1)
        self.fit_line.blockSignals(False)
        self.fit_result.setText(F.fit_report(self.spec, self.data) if self.spec.get("fit") else
                                "Pick a model to fit: the fitted curve shows on the figure as a red dashed line.")

    # -- chart focus --------------------------------------------------------------------------
    def focus(self, chart) -> None:
        """Open a whiteboard chart here, big, with its settings."""
        if self.focus_chart is chart:
            self.page.show_sub("singleplot")
            return
        self.leave_focus(switch=False)
        frame = chart.dock
        if frame is None:
            return
        self.focus_chart, self._home = chart, frame
        frame.layout().removeWidget(chart)
        self.focus_holder.addWidget(chart, 1)
        chart.show()
        self.focus_side.addWidget(self.page.side_scroll)
        self.page.side_scroll.show()
        self.page.canvas.select(chart)
        self.focus_title.setText(f"<b>{getattr(chart, 'name', '')}</b> · its settings are on the right")
        self.mode.setCurrentIndex(1)
        self.page.show_sub("singleplot")
        if hasattr(chart, "refresh"):
            chart.refresh()

    def _quick_button(self) -> QtWidgets.QPushButton:
        """Quick charts ▾: any whiteboard chart (Response, Raster, Rate…), opened here to look at
        and change (one already on the whiteboard is opened, else one is put there)."""
        b = QtWidgets.QPushButton("Quick charts")
        b.setToolTip("Open a quick chart here: the response, spikes, rates, cycles… It's also on the "
                     "DataPad's whiteboard")
        menu = QtWidgets.QMenu(b)
        menu.setToolTipsVisible(True)
        menu.aboutToShow.connect(lambda: self._fill_quick(menu))
        b.setMenu(menu)
        return b

    def _fill_quick(self, menu: QtWidgets.QMenu) -> None:
        from .canvas import GROUPS, KINDS
        menu.clear()
        canvas = self.page.canvas

        def fill(into, entries):
            for e in entries:
                if isinstance(e, dict):
                    for title, sub in e.items():
                        m = into.addMenu(title)
                        m.setToolTipsVisible(True)
                        fill(m, sub)
                    continue
                c = KINDS[e]
                a = into.addAction(c.kind)
                a.setToolTip(c.tip)
                a.triggered.connect(lambda _=False, k=c.kind: self.open_quick(k, new=True))

        # the charts already made, each to open here; then a new one of any kind
        here = list(canvas.here())
        head = menu.addAction(f"Your charts ({len(here)})" if here else "No charts yet: make one below")
        head.setEnabled(False)
        for chart in here:
            name = getattr(chart, "name", "") or chart.kind
            a = menu.addAction(("● " if chart is self.focus_chart else "    ") + name)
            a.setToolTip(f"Open this {chart.kind} chart here")
            a.triggered.connect(lambda _=False, ch=chart: self.focus(ch))
        menu.addSeparator()
        new = menu.addAction("New chart")
        new.setEnabled(False)
        for group, entries in GROUPS.items():
            sub = menu.addMenu(group)
            sub.setToolTipsVisible(True)
            fill(sub, entries)

    def open_quick(self, kind: str, new: bool = False) -> None:
        """A quick chart of this kind, here in chart focus: a new one (new), else the one already
        made, else a new one."""
        canvas = self.page.canvas
        chart = None if new else canvas.first(kind)
        if chart is None:
            chart = canvas.add(kind)
        if chart is not None:
            self.focus(chart)

    def leave_focus(self, switch: bool = True) -> None:
        chart = self.focus_chart
        if chart is None:
            return
        self.focus_chart = None
        self.focus_holder.removeWidget(chart)
        home = self._home
        try:
            home.layout().addWidget(chart, 1)
            chart.show()
        except RuntimeError:                     # its frame went (the chart was closed)
            pass
        self.page.side_scroll.hide()
        self.mode.setCurrentIndex(0)
        if switch:
            self.page.show_sub("analysis")

    def _chart_to_canvas(self) -> None:
        """The chart, as it looks now, straight onto the Plot Canvas (no stop in figure editing)."""
        if self.focus_chart is None:
            return
        try:
            spec, data = F.from_chart(self.focus_chart)
        except Exception as e:
            QtWidgets.QMessageBox.information(self, "Send to Canvas", f"Can't make a figure of it: {e}")
            return
        self.page.plot_canvas.add_panel(spec, data, linked=False)
        self.page.show_sub("plotcanvas")

    def _chart_figure(self) -> None:
        if self.focus_chart is None:
            return
        try:
            spec, data = F.from_chart(self.focus_chart)
        except Exception as e:
            QtWidgets.QMessageBox.information(self, "Make a figure", f"Can't make a figure of it: {e}")
            return
        self.leave_focus(switch=False)
        self.editing = None
        self.data = data
        self.show_spec(spec)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self.draw()

    # -- out --------------------------------------------------------------------------------
    def _to_canvas(self) -> None:
        self.editing = self.page.plot_canvas.add_panel(F.clone(self.spec), dict(self.data), edited=self.editing)
        self.btn_canvas.setText("Update on Canvas")
        self.page.show_sub("plotcanvas")

    def _save(self) -> None:
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save the figure", "figure.pdf",
                                                        "PDF (*.pdf);;SVG (*.svg);;PNG (*.png);;TIFF (*.tif)")
        if path:
            F.save(self.spec, self.data, path)

    def _copy(self) -> None:
        import io
        buf = io.BytesIO()
        fig = Figure(figsize=(self.spec["w_mm"] / 25.4, self.spec["h_mm"] / 25.4))
        F.draw(fig, self.spec, self.data)
        fig.tight_layout()
        fig.savefig(buf, format="png", dpi=200, facecolor="white")
        QtWidgets.QApplication.clipboard().setImage(QtGui.QImage.fromData(buf.getvalue()))

    def state(self) -> dict | None:
        return F.clone(self.spec) if self.spec else None


def _head(text: str) -> QtWidgets.QLabel:
    lab = QtWidgets.QLabel(f"<b>{text}</b>")
    lab.setContentsMargins(0, 8, 0, 2)
    return lab


def _row(*ws) -> QtWidgets.QWidget:
    w = QtWidgets.QWidget()
    lay = QtWidgets.QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    for x in ws:
        lay.addWidget(x)
    lay.addStretch(1)
    return w
