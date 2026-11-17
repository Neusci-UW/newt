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

Chart focus: Quick charts ▾ opens a new quick chart here, big, with its settings beside it;
Active charts ▾ opens one already made.
Make a figure turns what it shows into a figure to finish and send to the Canvas. Back to
the whiteboard puts it back where it was.
"""
from __future__ import annotations

import io

from matplotlib.figure import Figure
import numpy as np
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

from . import chartstyle as CS
from . import figspec as F
from .viewkit import scroll_column, with_slider


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


def _remove_fit_text(chart) -> None:
    t = getattr(chart, "_sp_fit_text", None)
    if t is not None:
        try:
            vb = t.parentItem()
            if vb is not None and hasattr(vb, "sigResized"):
                vb.sigResized.disconnect(t._place)
            if t.scene() is not None:
                t.scene().removeItem(t)
        except (RuntimeError, TypeError):
            pass
        chart._sp_fit_text = None


def _put_fit_text(chart, vb, html: str, color: str, where: str, mode: str) -> None:
    """The fit's text (Fit tab: Write) in a corner of the live chart's plot, staying there as
    you zoom and pan."""
    from . import style as st, theme
    back = QtGui.QColor(theme.THEMES[theme.DARK if mode == st.DARK else theme.LIGHT].background)
    back.setAlpha(215)                            # the plot's own background, so it reads in either theme
    right, bottom = "r" in where, "b" in where
    t = pg.TextItem(html=f"<span style='color:{color}'>{html}</span>", anchor=(1 if right else 0, 1 if bottom else 0),
                    fill=pg.mkBrush(back))
    t.setParentItem(vb)
    t.setZValue(40)

    def place(*_):
        r = vb.boundingRect()
        t.setPos(r.right() - 6 if right else r.left() + 6, r.bottom() - 6 if bottom else r.top() + 6)
    t._place = place
    vb.sigResized.connect(place)
    place()
    chart._sp_fit_text = t


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
        self.btn_back.setToolTip("Back to the figure you were making (the chart stays in Active charts)")
        self.focus_title = QtWidgets.QLabel()
        self.focus_title.setMinimumWidth(0)          # long names shrink, never widen the window
        self.focus_title.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred)
        self.btn_figure = QtWidgets.QPushButton("Edit as figure…")
        self.btn_figure.setToolTip("Optional: what this chart shows now, as a figure to restyle (fonts, ticks, "
                                   "fits) before it goes to the Canvas")
        fbar.addWidget(self.btn_back)
        fbar.addWidget(self._quick_button())
        fbar.addWidget(self._active_button())
        self._fbar = fbar                        # the "only the selection" tick goes here (page.only_selection_box)
        fbar.addWidget(self.focus_title, 1)
        fbar.addWidget(self.btn_figure)
        fcol.addLayout(fbar)
        self._fbar2 = QtWidgets.QHBoxLayout()        # a second row: what the charts show (page's ticks)
        self._fbar2.addStretch(1)
        fcol.addLayout(self._fbar2)
        self.focus_holder = QtWidgets.QVBoxLayout()
        self.chart_box = _AspectBox()                 # the chart, at the shape you chose
        self.focus_holder.addWidget(self.chart_box, 1)
        fcol.addLayout(self.focus_holder, 1)
        fl.addLayout(fcol, 1)
        self.focus_side = QtWidgets.QVBoxLayout()
        fl.addLayout(self.focus_side)
        self.mode.addWidget(focus_page)
        self.btn_back.clicked.connect(lambda: self.leave_focus(switch=False))
        self.btn_figure.clicked.connect(self._chart_figure)
        self.btn_chart_canvas = QtWidgets.QPushButton("Send to Canvas")
        self.btn_chart_canvas.setToolTip("Put this chart on the Plot Canvas, as it looks now (its zoom, lines, markers "
                                         "and threshold), in one step")
        self.btn_chart_canvas.clicked.connect(self._chart_to_canvas)
        fbar.addWidget(self.btn_chart_canvas)
        left = QtWidgets.QVBoxLayout()
        bar = QtWidgets.QHBoxLayout()
        bar.addWidget(self._quick_button())
        bar.addWidget(self._active_button())
        self.title = QtWidgets.QLabel()
        bar.addWidget(self.title, 1)
        self.btn_canvas = QtWidgets.QPushButton("Send to Canvas")
        self.btn_canvas.setToolTip("Put this figure on the Plot Canvas, to lay out beside others")
        self.btn_save = QtWidgets.QPushButton("Save figure…")
        self.btn_copy = QtWidgets.QPushButton("Copy")
        # moving the view: grab and drag the plot, scroll to zoom where you point, or these
        self.btn_grab = QtWidgets.QToolButton(text="✋ Grab", checkable=True, checked=True)
        self.btn_grab.setToolTip("Drag the plot to move what it shows; scroll over it to zoom where you point. "
                                 "X from / Y from follow")
        self.btn_zoom_in = QtWidgets.QToolButton(text="＋")
        self.btn_zoom_in.setToolTip("Zoom in on the middle of the plot")
        self.btn_zoom_out = QtWidgets.QToolButton(text="−")
        self.btn_zoom_out.setToolTip("Zoom out")
        self.btn_fit_view = QtWidgets.QToolButton(text="Fit view")
        self.btn_fit_view.setToolTip("Back to automatic ranges: everything plotted, fitted")
        self.view_tools = [self.btn_grab, self.btn_zoom_in, self.btn_zoom_out, self.btn_fit_view]
        for b in self.view_tools:
            bar.addWidget(b)
        bar.addSpacing(8)
        for b in (self.btn_copy, self.btn_save, self.btn_canvas):
            bar.addWidget(b)
        left.addLayout(bar)
        self.area = QtWidgets.QWidget()
        self.area.setStyleSheet("background: #e9ebee")
        al = QtWidgets.QGridLayout(self.area)
        # the figure as a picture, drawn at its size in mm (a live matplotlib canvas resizes the
        # figure to itself and draws over its old picture): what you see is what saves
        self.canvas = _FigureView(self)
        self.canvas.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._geo: dict | None = None                # where the plot sat when last drawn: for grab and zoom
        self.btn_zoom_in.clicked.connect(lambda: self.zoom_view(0.8))
        self.btn_zoom_out.clicked.connect(lambda: self.zoom_view(1.25))
        self.btn_fit_view.clicked.connect(self.fit_view)
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
        # look: whole looks at once, then a section for each part (each folds away; one ticked
        # 🔗 is the same on every figure of the Plot Canvas)
        sl.addWidget(_head("Look"))
        pre = QtWidgets.QHBoxLayout()
        pre.setSpacing(4)
        for name in F.PRESETS:
            b = QtWidgets.QPushButton(name)
            b.setToolTip(f"{name}: its text, lines, points, ticks and size, all at once")
            b.clicked.connect(lambda _=False, n=name: self._apply_look(F.PRESETS[n]))
            pre.addWidget(b)
        sl.addLayout(pre)
        cp = QtWidgets.QHBoxLayout()
        cp.setSpacing(4)
        self.btn_copy_look = QtWidgets.QPushButton("Copy look")
        self.btn_copy_look.setToolTip("Keep this figure's look (not its size) to paste on another")
        self.btn_paste_look = QtWidgets.QPushButton("Paste look")
        self.btn_paste_look.setToolTip("Give this figure the look you copied; its size stays")
        self.btn_paste_look.setEnabled(False)
        self.btn_every = QtWidgets.QPushButton("Apply to every figure")
        self.btn_every.setToolTip("Give every figure on the Plot Canvas this look (each keeps its size)")
        for b in (self.btn_copy_look, self.btn_paste_look, self.btn_every):
            cp.addWidget(b)
        sl.addLayout(cp)
        self._copied_look: dict | None = None
        self.btn_copy_look.clicked.connect(self._copy_look)
        self.btn_paste_look.clicked.connect(lambda: self._copied_look and self._apply_look(self._copied_look))
        self.btn_every.clicked.connect(self._look_to_every)

        def spin(lo, hi, step, decimals=1, unit=" pt", auto=False, tip=""):
            w = QtWidgets.QDoubleSpinBox(decimals=decimals, minimum=lo, maximum=hi, singleStep=step, suffix=unit)
            if auto:
                w.setSpecialValueText("Auto")
            w.setToolTip(tip)
            return w

        def combo(items, tip=""):
            w = QtWidgets.QComboBox()
            for label, key in items:
                w.addItem(label, key)
            w.setToolTip(tip)
            return w
        self.family = combo([(f, f) for f in F.FAMILIES], "The figure's typeface (every label, number and title)")
        self.font = spin(5, 96, 0.5, tip="The axis labels' size (and, on Auto, the others follow it)")
        self.font_ticks = spin(0, 96, 0.5, auto=True, tip="The numbers along the axes; Auto: a little smaller "
                                                          "than the labels")
        self.font_title = spin(0, 120, 0.5, auto=True, tip="The title; Auto: a little bigger than the labels")
        self.font_legend = spin(0, 72, 0.5, auto=True, tip="The legend; Auto: a little smaller than the labels")
        self.bold = combo([("None", "none"), ("Title", "title"), ("Labels and title", "labels")], "What's bold")
        self.lw = spin(0.1, 24, 0.1, decimals=2, tip="How thick the lines print (on the Canvas: a plain line's "
                                                     "width; lines drawn thicker keep their ratio to it)")
        self.axw = spin(0.1, 6, 0.05, decimals=2, tip="How thick the axis lines and tick marks print")
        self.ms = spin(0.5, 60, 0.5, tip="How big the points are")
        self.marker = combo([("● Filled circle", "circle"), ("○ Open circle", "open"), ("■ Square", "square"),
                             ("▲ Triangle", "triangle"), ("◆ Diamond", "diamond"), ("None", "none")],
                            "The points' shape")
        self.cap = spin(0, 20, 0.5, auto=True, tip="How wide the ends of the error bars are; Auto: from the "
                                                   "points' size")
        self.smooth = QtWidgets.QSpinBox(minimum=0, maximum=201, singleStep=2)
        self.smooth.setSpecialValueText("Off")
        self.smooth.setSuffix(" points")
        self.smooth.setToolTip("Smooth the lines: each point averaged with its neighbours, this many in all "
                               "(points on their own stay put). Off: as measured")
        self.err_style = combo([("Bars", "bars"), ("Shaded band", "band"), ("None", "none")],
                               "How the spread (± SEM or ± SD, set for each series) is drawn")
        self.box = combo([("Open (no top or right line)", "open"), ("Closed", "closed"), ("None (ticks only)", "none")],
                         "The lines around the plot")
        self.ticks = combo([("Outside", "out"), ("Inside", "in"), ("Across", "inout")],
                           "Which way the tick marks point from the axis")
        self.tick_len = spin(0, 40, 0.5, tip="How long the tick marks are (0: none)")
        self.nticks = QtWidgets.QSpinBox(minimum=0, maximum=12)
        self.nticks.setSpecialValueText("Auto")
        self.nticks.setToolTip("About how many numbers along each axis; Auto: as many as fit")
        self.grid = combo([("None", "none"), ("Across (y only)", "y"), ("Both ways", "xy")], "Grid lines")
        self.zero = QtWidgets.QCheckBox("Lines at zero")
        self.points = QtWidgets.QCheckBox("Each trial's point on the bars")
        self.palette = combo([("The groups' own", ""), ("Colorblind-safe", "safe"), ("Grays", "gray"),
                              ("Viridis", "viridis"), ("Bold", "bold")],
                             "The series' colors, unless you gave one its own (Series, above)")
        self.legend = QtWidgets.QComboBox()
        self.legend.addItems(F.LEGENDS)
        self.legend_frame = QtWidgets.QCheckBox("Box around it")
        self.fit = QtWidgets.QComboBox()
        for k, lab in F.FITS.items():
            self.fit.addItem(lab, k)
        self.shape = QtWidgets.QComboBox()
        for lab, a_ in SHAPES:
            self.shape.addItem(lab, a_)
        self.shape.setToolTip("The chart's shape (height to width), on screen and on the Canvas")
        self.w_mm = spin(20, 400, 1, decimals=0, unit=" mm")
        self.h_mm = spin(20, 400, 1, decimals=0, unit=" mm")
        self.link_boxes: dict[str, QtWidgets.QCheckBox] = {}

        def section(title: str, key: str | None = None, open_: bool = True) -> QtWidgets.QFormLayout:
            head = QtWidgets.QWidget()
            h = QtWidgets.QHBoxLayout(head)
            h.setContentsMargins(0, 6, 0, 0)
            fold = QtWidgets.QToolButton(text=title, checkable=True, checked=open_)
            fold.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            fold.setArrowType(QtCore.Qt.ArrowType.DownArrow if open_ else QtCore.Qt.ArrowType.RightArrow)
            fold.setAutoRaise(True)
            f_ = fold.font()
            f_.setBold(True)
            fold.setFont(f_)
            h.addWidget(fold)
            h.addStretch(1)
            if key:
                link = QtWidgets.QCheckBox("🔗 every figure")
                link.setToolTip(f"The same {title.lower()} on every figure of the Plot Canvas: change it on "
                                "one and the others follow")
                link.toggled.connect(lambda on, k=key: self._link_toggled(k, on))
                h.addWidget(link)
                self.link_boxes[key] = link
            body = QtWidgets.QWidget()
            form = QtWidgets.QFormLayout(body)
            form.setContentsMargins(14, 0, 0, 2)
            body.setVisible(open_)

            def folded(on, body=body, fold=fold):
                body.setVisible(on)
                fold.setArrowType(QtCore.Qt.ArrowType.DownArrow if on else QtCore.Qt.ArrowType.RightArrow)
            fold.toggled.connect(folded)
            sl.addWidget(head)
            sl.addWidget(body)
            return form
        t = section("Text", "text")
        t.addRow("Font", self.family)
        t.addRow("Labels", with_slider(self.font))
        t.addRow("Numbers", with_slider(self.font_ticks))
        t.addRow("Title", with_slider(self.font_title))
        t.addRow("Legend", with_slider(self.font_legend))
        t.addRow("Bold", self.bold)
        ln = section("Lines and points", "lines")
        ln.addRow("Lines", with_slider(self.lw))
        ln.addRow("Axis lines", with_slider(self.axw))
        ln.addRow("Points", with_slider(self.ms))
        ln.addRow("Point shape", self.marker)
        ln.addRow("Error caps", with_slider(self.cap))
        ln.addRow("Error bars", self.err_style)
        ln.addRow("Smooth lines", with_slider(self.smooth))
        ax_ = section("Axes", "axes")
        ax_.addRow("Box", self.box)
        ax_.addRow("Ticks", self.ticks)
        ax_.addRow("Tick length", with_slider(self.tick_len))
        ax_.addRow("About ticks", with_slider(self.nticks))
        ax_.addRow("Grid", self.grid)
        ax_.addRow(self.zero)
        ax_.addRow(self.points)
        co = section("Colors", "colors", open_=False)
        co.addRow("Palette", self.palette)
        lg = section("Legend", "legend", open_=False)
        lg.addRow("Where", self.legend)
        lg.addRow(self.legend_frame)
        sz = section("Size", open_=False)
        sz.addRow("Shape", self.shape)
        sz.addRow("Width", with_slider(self.w_mm))
        sz.addRow("Height", with_slider(self.h_mm))
        sl.addStretch(1)
        scroll = scroll_column(self.side)
        # on the right, tabs: the figure's settings, and fitting a curve to it
        self.right = QtWidgets.QTabWidget()
        self.right.setMinimumWidth(300)
        self.right.setMaximumWidth(340)
        self.right.addTab(scroll, "Figure")
        self.right.addTab(self._fit_tab(), "Fit")
        fig_lay.addWidget(self.right)
        self._fig_lay = fig_lay

        for w in (self.t_title, self.t_x, self.t_y, self.x0, self.x1, self.y0, self.y1):
            w.editingFinished.connect(self._edited)
        self.ticks.currentIndexChanged.connect(self._edited)
        for w in (self.font, self.font_ticks, self.font_title, self.font_legend, self.tick_len, self.lw, self.axw,
                  self.ms, self.cap, self.nticks, self.smooth, self.w_mm, self.h_mm):
            w.valueChanged.connect(self._edited)
        for w in (self.legend, self.kind, self.shape, self.family, self.bold, self.marker, self.err_style, self.box,
                  self.grid, self.palette):
            w.currentIndexChanged.connect(self._edited)
        self.fit.currentIndexChanged.connect(self._fit_edited)
        for w in (self.xlog, self.ylog, self.zero, self.points, self.legend_frame):
            w.toggled.connect(self._edited)
        self.btn_add.clicked.connect(self._add_series)
        self.btn_canvas.clicked.connect(self._to_canvas)
        self.btn_save.clicked.connect(self._save)
        self.btn_copy.clicked.connect(self._copy)
        self._loading = False
        self.show_spec(None)

    # -- the figure -------------------------------------------------------------------------
    def show_spec(self, spec: dict | None, live: bool = False) -> None:
        if self.focus_chart is not None and not live:
            keep = (self.data, self.editing)       # leaving puts back the figure from before; keep the caller's
            self.leave_focus(switch=False)
            self.data, self.editing = keep
        self.spec = spec
        has = spec is not None
        self.side.setEnabled(has)
        for b in (self.btn_canvas, self.btn_save, self.btn_copy):
            b.setEnabled(has)
        self.canvas.setVisible(has)
        self.empty.setVisible(not has)
        for b in self.view_tools:                  # a live chart moves on its own (drag and scroll it)
            b.setVisible(has and not live)
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
        self._show_look({**F.LOOK_DEFAULTS, **s, "box": F._box(s)})
        a = s.get("aspect")
        self.shape.setCurrentIndex(max(0, next((i for i, (_, v) in enumerate(SHAPES)
                                                if v is not None and a is not None and abs(v - a) < 1e-3),
                                               0 if a is None else -1)))
        self.fit.setCurrentIndex(max(0, self.fit.findData(s["fit"])))
        self.fit_degree.setValue(int(s.get("fit_degree", 1)))
        self.fit_degree.setEnabled(s.get("fit") == "poly")
        fx = (list(s.get("fit_x") or []) + [None, None])[:2]
        self.fit_x0.setText("" if fx[0] is None else f"{fx[0]:g}")
        self.fit_x1.setText("" if fx[1] is None else f"{fx[1]:g}")
        self.fit_color.setIcon(QtGui.QIcon(_swatch(s.get("fit_color") or F.FIT_LOOK["fit_color"])))
        self.fit_width.setValue(float(s.get("fit_width", F.FIT_LOOK["fit_width"])))
        self.fit_style.setCurrentIndex(max(0, self.fit_style.findData(s.get("fit_style", "dash"))))
        self.fit_where.setCurrentIndex(max(0, self.fit_where.findData(s.get("fit_where", "tl"))))
        self.fit_span.setCurrentIndex(max(0, self.fit_span.findData(s.get("fit_span", "plot"))))
        for k, b in self.fit_show.items():
            b.blockSignals(True)
            b.setChecked(k in (s.get("fit_show") or []))
            b.blockSignals(False)
        self.points.setChecked(s.get("points", True))
        self.w_mm.setValue(s["w_mm"])
        self.h_mm.setValue(s["h_mm"])
        self._show_links()
        self.h_mm.setEnabled(not live)                 # a live chart's height follows its Shape
        self._fill_series()
        self._shown = self._ui_values()              # what the boxes show now: edits are measured from this
        drawn = s["kind"] == "curves"
        for w in (self.kind, self.btn_add):
            w.setVisible(not drawn)
        self.from_chart.setVisible(drawn)
        self.from_chart.setText(("The live chart: these settings change it as you go, and Send to Canvas "
                                 "takes them along. What it shows is set in the Chart tab." if live else
                                 "The lines of a quick chart, as they were when you made the figure. "
                                 "Make it again from the chart to update it.") if drawn else "")
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
        kind = s["kind"] if s.get("kind") in ("curves", "image") else self.kind.currentData()   # a chart stays a chart
        if (self.w_mm.value(), self.h_mm.value()) != (s.get("w_mm"), s.get("h_mm")):
            s["auto_size"] = False                # a size you set: kept (else a chart's comes from its shape)
        ui = self._ui_values()
        s.update(kind=kind, **ui)
        if self._live():                          # only what you changed (the boxes round what they show)
            shown, self._shown = getattr(self, "_shown", {}), ui
            self._to_look({k: v for k, v in ui.items() if shown.get(k) != v})
            return
        self.draw()

    def _ui_values(self) -> dict:
        return dict(title=self.t_title.text(), xlabel=self.t_x.text(),
                    ylabel=self.t_y.text(), xlim=[_opt_float(self.x0.text()), _opt_float(self.x1.text())],
                    ylim=[_opt_float(self.y0.text()), _opt_float(self.y1.text())], xlog=self.xlog.isChecked(),
                    ylog=self.ylog.isChecked(), aspect=self.shape.currentData(), points=self.points.isChecked(),
                    **self._look_values(),
                    w_mm=self.w_mm.value(), h_mm=self.h_mm.value())

    def _look_values(self) -> dict:
        """The Look boxes as figure-spec keys."""
        box, grid = self.box.currentData(), self.grid.currentData()
        return dict(family=self.family.currentData(), font=self.font.value(), font_ticks=self.font_ticks.value(),
                    font_title=self.font_title.value(), font_legend=self.font_legend.value(),
                    bold=self.bold.currentData(), lw=self.lw.value(), axw=self.axw.value(), ms=self.ms.value(),
                    marker=self.marker.currentData(), cap=self.cap.value(), err_style=self.err_style.currentData(),
                    smooth=self.smooth.value(),
                    box=box, despine=box == "open", ticks=self.ticks.currentData(), tick_len=self.tick_len.value(),
                    nticks=self.nticks.value(), grid={"none": False, "y": "y", "xy": True}[grid],
                    zero=self.zero.isChecked(), palette=self.palette.currentData(),
                    legend=self.legend.currentText(), legend_frame=self.legend_frame.isChecked())

    def _show_look(self, v: dict) -> None:
        """Put a look in the boxes: a figure's, a preset or a copied look. What it doesn't say stays."""
        for k, w in (("font", self.font), ("font_ticks", self.font_ticks), ("font_title", self.font_title),
                     ("font_legend", self.font_legend), ("lw", self.lw), ("axw", self.axw), ("ms", self.ms),
                     ("cap", self.cap), ("tick_len", self.tick_len), ("nticks", self.nticks), ("smooth", self.smooth),
                     ("w_mm", self.w_mm),
                     ("h_mm", self.h_mm)):
            if v.get(k) is not None:
                w.setValue(int(v[k]) if k in ("nticks", "smooth") else float(v[k]))
        for k, w in (("family", self.family), ("bold", self.bold), ("marker", self.marker),
                     ("err_style", self.err_style), ("box", self.box), ("ticks", self.ticks), ("palette", self.palette)):
            if k in v:
                w.setCurrentIndex(max(0, w.findData(v[k])))
        if "grid" in v:
            g = v["grid"]
            self.grid.setCurrentIndex(self.grid.findData("y" if g == "y" else "xy" if g else "none"))
        if "zero" in v:
            self.zero.setChecked(bool(v["zero"]))
        if "legend" in v:
            self.legend.setCurrentText(v["legend"])
        if "legend_frame" in v:
            self.legend_frame.setChecked(bool(v["legend_frame"]))

    def _apply_look(self, look: dict) -> None:
        """A preset, or a copied look, on this figure: one redraw for all of it."""
        if self.spec is None:
            return
        self._loading = True
        try:
            self._show_look(look)
        finally:
            self._loading = False
        self._edited()

    def _copy_look(self) -> None:
        if self.spec is not None:
            self._copied_look = F.look_of(self._look_values())
            self.btn_paste_look.setEnabled(True)

    def _look_to_every(self) -> None:
        """This look (not the size) on every figure of the Plot Canvas."""
        if self.spec is not None:
            self.page.plot_canvas.set_look(F.look_of(self._look_values()))

    def _show_links(self) -> None:
        """Which sections are the same on every Canvas figure."""
        links = self.page.plot_canvas.links
        for key, box in self.link_boxes.items():
            box.blockSignals(True)
            box.setChecked(bool(links.get(key)))
            box.blockSignals(False)

    def _link_toggled(self, key: str, on: bool) -> None:
        """A section made the same on every Canvas figure (from this one), or let go."""
        look = F.look_of(self._look_values(), (key,)) if self.spec is not None else None
        self.page.plot_canvas.set_link(key, on, look)

    def _live(self) -> bool:
        return self.focus_chart is not None and self.mode.currentIndex() == 1

    def data_changed(self) -> None:
        """The DataPad changed (or what's plotted): read the columns again and draw."""
        if self.spec is None:
            return
        if self._live():                          # the live chart: its look, and the lines a fit can use
            self._apply_live()
            self._show_fit()
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
        if self._live():
            self._apply_live()
            return
        if self.spec is not None and self.isVisible():
            self._redraw.start()

    # -- the live chart: its look is chart.look (chartstyle), the same record its figure reads --
    _LOOK_KEYS = {"title", "xlabel", "ylabel", "xlog", "ylog", "font", "font_ticks", "font_title", "ticks",
                  "tick_len", "ms", "despine", "grid", "zero", "legend", "fit", "fit_degree", "fit_line", "fit_x",
                  *F.FIT_LOOK}

    def _to_look(self, changed: dict) -> None:
        """Settings changed in the Figure or Fit tab, into the chart's look (its one record)."""
        chart = self.focus_chart
        if chart is None or not changed:
            return
        L = chart.look
        kx, ky = float(self.data.get("kx", 1.0) or 1.0), float(self.data.get("ky", 1.0) or 1.0)
        for k, v in changed.items():
            if k == "lw":
                L["thick"] = CS.thick_of(v)
            elif k in ("xlim", "ylim"):
                lo, hi = (list(v or []) + [None, None])[:2]
                kk = kx if k == "xlim" else ky
                L[k[0]] = [lo / kk, hi / kk] if lo is not None and hi is not None and hi > lo else [None, None]
            elif k == "w_mm":                     # its width on the Canvas; the height follows the shape
                L["w_mm"] = v
            elif k == "aspect":
                L["aspect"] = v
                self.chart_box.set_aspect(v)
            elif k in ("font_ticks", "font_title", "ms"):
                L[k] = v or None
            elif k == "grid":                     # on screen: on or off; the figure: which way too
                L["grid"] = bool(v)
                L.setdefault("fig", {})["grid"] = v
            elif k == "box":
                L["despine"] = v == "open"
                L.setdefault("fig", {})["box"] = v
            elif k in F.LOOK_DEFAULTS:            # the figure's own: the chart on screen doesn't draw it
                L.setdefault("fig", {})[k] = v
            elif k in self._LOOK_KEYS:
                L[k] = list(v) if isinstance(v, list) else v
        if any(k in changed for k in ("title", "xlabel", "ylabel", "xlim", "ylim", "grid", "font", "font_ticks",
                                      "font_title")):
            chart.refresh()                       # its own labels, view and grid, drawn again under the look
        chart.apply_look()
        chart.saved()
        if "aspect" in changed or "w_mm" in changed:     # the chart's own text, in pt on the figure, changed
            QtCore.QTimer.singleShot(150, lambda: self.focus_chart is chart and self.show_spec(self._live_spec(chart),
                                                                                                 live=True))

    def _live_spec(self, chart) -> dict:
        """What the Figure tab shows for a live chart: its look, ranges left auto shown blank."""
        spec, self.data = F.from_chart(chart)
        L = chart.look
        kx, ky = float(self.data.get("kx", 1.0) or 1.0), float(self.data.get("ky", 1.0) or 1.0)
        spec.update(title=L.get("title", ""), xlabel=L.get("xlabel", ""), ylabel=L.get("ylabel", ""))
        if spec.get("w0_mm"):                     # text sizes shown as pt on the figure, at its width
            k = float(spec["w_mm"]) / float(spec["w0_mm"])
            for key in ("font", "font_ticks", "font_title"):
                if spec.get(key):
                    spec[key] = round(float(spec[key]) * k * 2) / 2
        spec["aspect"] = L.get("aspect")
        for key, axis, k_ in (("xlim", "x", kx), ("ylim", "y", ky)):
            lo, hi = (list(L.get(axis) or []) + [None, None])[:2]
            spec[key] = [lo * k_, hi * k_] if lo is not None and hi is not None else [None, None]
        return spec

    def _apply_live(self) -> None:
        """After the chart put its look on: the lines as drawn now (for fitting), and the fitted curve."""
        chart, s = self.focus_chart, self.spec
        if chart is None or s is None or not chart.plots():
            return
        try:
            self.data = F.from_chart(chart)[1]
        except Exception:
            pass
        kx, ky = float(self.data.get("kx", 1.0) or 1.0), float(self.data.get("ky", 1.0) or 1.0)
        pis = [p.getPlotItem() if hasattr(p, "getPlotItem") else p for p in chart.plots()]
        main = pis[min(int(self.data.get("plot", 0)), len(pis) - 1)]
        old = getattr(chart, "_sp_fit_item", None)
        if old is not None:
            try:
                vb = old.getViewBox()
                if vb is not None:
                    if getattr(old, "_follow", None) is not None:
                        vb.sigXRangeChanged.disconnect(old._follow)
                    vb.removeItem(old)
            except (RuntimeError, AttributeError, TypeError):
                pass
            chart._sp_fit_item = None
        _remove_fit_text(chart)
        got = F.do_fit(s, self.data) if s.get("fit") else None
        if got is not None:
            _, f, xu = got
            xs = np.linspace(float(np.nanmin(xu)), float(np.nanmax(xu)), 300)
            color = s.get("fit_color") or F.FIT_LOOK["fit_color"]
            style = {"dash": QtCore.Qt.PenStyle.DashLine, "dot": QtCore.Qt.PenStyle.DotLine}.get(
                s.get("fit_style"), QtCore.Qt.PenStyle.SolidLine)
            item = pg.PlotDataItem(xs / kx, f.predict(xs) / ky,
                                   pen=pg.mkPen(color, width=float(s.get("fit_width", 1.5)) * CS.PT_PX, style=style))
            item.setZValue(30)
            item._follow = None
            if s.get("fit_span", "plot") == "plot":       # across the whole plot, as you zoom and pan too
                xlog = bool(s.get("xlog"))

                def follow(*_, item=item, vb=main.vb, f=f):
                    lo, hi = vb.viewRange()[0]
                    if xlog:                              # the view's x is log10 of the data's
                        xx = np.logspace(lo, hi, 300) * kx
                    else:
                        xx = np.linspace(lo, hi, 300) * kx
                    with np.errstate(all="ignore"):
                        item.setData(xx / kx, f.predict(xx) / ky)
                item._follow = follow
                main.addItem(item, ignoreBounds=True)     # the curve doesn't move the view
                follow()
                main.vb.sigXRangeChanged.connect(follow)
            else:
                main.addItem(item)
            chart._sp_fit_item = item
            text = F.fit_text(s, f)
            if text:
                _put_fit_text(chart, main.vb, "<br>".join(text), color, s.get("fit_where", "tl"), self.page._mode)
        self._show_fit()                          # what it can fit through may have changed (its groups, say)

    def figure_of_chart(self) -> tuple[dict, dict]:
        """The live chart as a figure: what it shows, in its look (the same record it draws from)."""
        return F.from_chart(self.focus_chart)

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
        W, H = int(w_in * dpi * zoom), int(h_in * dpi * zoom)
        self._geo = None
        if fig.axes and self.spec.get("kind") != "image":        # where the plot is, for grab and zoom
            ax = fig.axes[0]
            pos = ax.get_position()
            self._geo = {"left": pos.x0 * W, "top": (1 - pos.y1) * H, "w": pos.width * W, "h": pos.height * H,
                         "xlim": tuple(ax.get_xlim()), "ylim": tuple(ax.get_ylim()),
                         "xlog": ax.get_xscale() == "log", "ylog": ax.get_yscale() == "log",
                         "x_moves": self.spec.get("kind") != "bar"}
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=dpi * zoom * ratio, facecolor="white")
        pix = QtGui.QPixmap()
        pix.loadFromData(buf.getvalue())
        pix.setDevicePixelRatio(ratio)
        self.canvas.setFixedSize(int(w_in * dpi * zoom), int(h_in * dpi * zoom))
        self.canvas.setPixmap(pix)

    # -- moving the view: grab and pan, zoom (the figure's X from / Y from follow) -------------------
    def in_plot(self, pos: QtCore.QPointF) -> bool:
        g = self._geo
        return g is not None and g["left"] <= pos.x() <= g["left"] + g["w"] and g["top"] <= pos.y() <= g["top"] + g["h"]

    def _show_view(self, xlim, ylim) -> None:
        """Put a view in the X from / Y from boxes and draw it."""
        self._loading = True
        try:
            for w, v in ((self.x0, xlim[0]), (self.x1, xlim[1]), (self.y0, ylim[0]), (self.y1, ylim[1])):
                w.setText("" if v is None else f"{v:.6g}")
        finally:
            self._loading = False
        self._edited()

    @staticmethod
    def _shifted(lim, frac: float, log: bool):
        """Limits moved by `frac` of their span (on a log axis, of their decades)."""
        a, b = lim
        if log and a > 0 and b > 0:
            la, lb = np.log10(a), np.log10(b)
            d = (lb - la) * frac
            return 10 ** (la + d), 10 ** (lb + d)
        d = (b - a) * frac
        return a + d, b + d

    @staticmethod
    def _scaled_about(lim, at: float, k: float, log: bool):
        """Limits shrunk (k < 1) or grown about the point `at` (0 to 1 along them)."""
        a, b = lim
        if log and a > 0 and b > 0:
            la, lb = np.log10(a), np.log10(b)
            c = la + (lb - la) * at
            return 10 ** (c - (c - la) * k), 10 ** (c + (lb - c) * k)
        c = a + (b - a) * at
        return c - (c - a) * k, c + (b - c) * k

    def pan_view(self, start: dict, dx: float, dy: float) -> None:
        """The view as it was at `start` (a _geo), dragged by dx, dy screen pixels."""
        if start is None or self.spec is None:
            return
        xlim = self._shifted(start["xlim"], -dx / start["w"], start["xlog"]) if start["x_moves"] else \
            tuple(self.spec.get("xlim") or (None, None))
        ylim = self._shifted(start["ylim"], dy / start["h"], start["ylog"])
        self._show_view(xlim, ylim)

    def zoom_view(self, k: float, pos: QtCore.QPointF | None = None) -> None:
        """Zoom in (k < 1) or out about where you point (else the middle)."""
        g = self._geo
        if g is None or self.spec is None:
            return
        fx = (pos.x() - g["left"]) / g["w"] if pos is not None else 0.5
        fy = 1 - (pos.y() - g["top"]) / g["h"] if pos is not None else 0.5
        fx, fy = min(1.0, max(0.0, fx)), min(1.0, max(0.0, fy))
        xlim = self._scaled_about(g["xlim"], fx, k, g["xlog"]) if g["x_moves"] else \
            tuple(self.spec.get("xlim") or (None, None))
        ylim = self._scaled_about(g["ylim"], fy, k, g["ylog"])
        self._show_view(xlim, ylim)

    def fit_view(self) -> None:
        """Back to automatic ranges."""
        if self.spec is not None:
            self._show_view((None, None), (None, None))

    def showEvent(self, ev):
        super().showEvent(ev)
        self.page.dock_panels(self.outer)          # pick trials and colors here too
        if self.focus_chart is None and self.spec is None and self.page.blocks:
            # nothing open yet: start on a live chart (the first one made, else a Response)
            QtCore.QTimer.singleShot(0, lambda: self.focus_chart is None and self.spec is None
                                     and self.open_quick("Response"))
            return
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
        # how the fitted curve looks, and what's written beside it
        self.fit_color = QtWidgets.QPushButton("Color…")
        self.fit_color.setToolTip("The fitted curve's color (and its text's)")
        self.fit_color.clicked.connect(self._pick_fit_color)
        self.fit_width = QtWidgets.QDoubleSpinBox(decimals=2, minimum=0.25, maximum=6, singleStep=0.25, suffix=" pt")
        self.fit_width.setToolTip("How thick the fitted curve is")
        self.fit_style = QtWidgets.QComboBox()
        for k, lab in F.FIT_STYLES.items():
            self.fit_style.addItem(lab, k)
        f.addRow("Line", _row(self.fit_color, self.fit_width, self.fit_style))
        self.fit_span = QtWidgets.QComboBox()
        for k, lab in F.FIT_SPANS.items():
            self.fit_span.addItem(lab, k)
        self.fit_span.setToolTip("Draw the fitted curve across the whole plot, or only between the first and last "
                                 "points it went through (a curve can run off far past the data)")
        f.addRow("Across", self.fit_span)
        self.fit_show = {}
        for k, lab in F.FIT_SHOW.items():
            b = QtWidgets.QCheckBox(lab)
            b.toggled.connect(self._fit_edited)
            self.fit_show[k] = b
        self.fit_show["r"].setToolTip("For a straight line, the correlation coefficient (signed like the slope); "
                                      "for other curves, √R²")
        self.fit_show["coef"].setToolTip("Each coefficient ± its standard error")
        f.addRow("Write", _row(*self.fit_show.values()))
        self.fit_where = QtWidgets.QComboBox()
        for k, lab in F.FIT_WHERE.items():
            self.fit_where.addItem(lab, k)
        self.fit_where.setToolTip("Which corner of the plot the text goes in")
        f.addRow("In the", self.fit_where)
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
        self.fit_width.valueChanged.connect(self._fit_edited)
        self.fit_style.currentIndexChanged.connect(self._fit_edited)
        self.fit_where.currentIndexChanged.connect(self._fit_edited)
        self.fit_span.currentIndexChanged.connect(self._fit_edited)
        for e in (self.fit_x0, self.fit_x1):
            e.editingFinished.connect(self._fit_edited)
        use.clicked.connect(self._fit_use_view)
        return w

    def _fit_edited(self, *_) -> None:
        if self._loading or self.spec is None:
            return
        self.spec.update(fit=self.fit.currentData(), fit_degree=self.fit_degree.value(),
                         fit_line=max(0, self.fit_line.currentIndex()),
                         fit_x=[_opt_float(self.fit_x0.text()), _opt_float(self.fit_x1.text())],
                         fit_width=self.fit_width.value(), fit_style=self.fit_style.currentData(),
                         fit_where=self.fit_where.currentData(), fit_span=self.fit_span.currentData(),
                         fit_show=[k for k, b in self.fit_show.items() if b.isChecked()])
        self.fit_degree.setEnabled(self.spec["fit"] == "poly")
        if self._live():
            self._to_look({k: self.spec[k] for k in ("fit", "fit_degree", "fit_line", "fit_x", *F.FIT_LOOK)})
        else:
            self.draw()
        self._show_fit()

    def _pick_fit_color(self) -> None:
        if self.spec is None:
            return
        c = QtWidgets.QColorDialog.getColor(QtGui.QColor(self.spec.get("fit_color") or F.FIT_LOOK["fit_color"]), self)
        if c.isValid():
            self.spec["fit_color"] = c.name()
            self.fit_color.setIcon(QtGui.QIcon(_swatch(c.name())))
            self._fit_edited()

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
        self.chart_box.put(chart, chart.look.get("aspect"))
        chart.show()
        self.page.canvas.select(chart)
        # beside it: the chart's own settings (Chart), and the look (Figure) and Fit, on this chart
        self._prev = (self.spec, self.data, self.editing)
        self._fig_lay.removeWidget(self.right)
        self.right.insertTab(0, self.page.side_scroll, "Chart")
        self.page.side_scroll.show()
        self.page.side.look.hide()                    # thickness, grid, ranges: in the Figure tab instead
        self.focus_side.addWidget(self.right)
        self.right.setCurrentIndex(0)
        self._hook = lambda: (QtCore.QTimer.singleShot(0, self._apply_live), self.chart_box.recheck()) \
            if self.focus_chart is chart else None
        chart.look_hooks.append(self._hook)           # it redrew: the fitted curve over it again
        self.focus_title.setText(f"<b>{getattr(chart, 'name', '')}</b> · its settings are on the right")
        self.mode.setCurrentIndex(1)
        self.btn_back.setVisible(getattr(self, "_prev", (None,))[0] is not None)   # a figure to go back to
        self.editing = None
        try:
            spec = self._live_spec(chart)
        except Exception:
            spec = F.new_spec(kind="curves")
        self.show_spec(spec, live=True)
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

    def _active_button(self) -> QtWidgets.QPushButton:
        """Active charts ▾: the charts already made (on the whiteboard), each to open here."""
        b = QtWidgets.QPushButton("Active charts")
        b.setToolTip("The charts you've made: pick one to open it here")
        menu = QtWidgets.QMenu(b)
        menu.setToolTipsVisible(True)
        menu.aboutToShow.connect(lambda: self._fill_active(menu))
        b.setMenu(menu)
        return b

    def _fill_active(self, menu: QtWidgets.QMenu) -> None:
        menu.clear()
        here = list(self.page.canvas.here())
        if not here:
            menu.addAction("No charts yet: make one in Quick charts").setEnabled(False)
        for chart in here:
            name = getattr(chart, "name", "") or chart.kind
            a = menu.addAction(name)
            a.setCheckable(True)
            a.setChecked(chart is self.focus_chart)           # the one open here
            a.setToolTip(f"Open this {chart.kind} chart here")
            a.triggered.connect(lambda _=False, ch=chart: self.focus(ch))

    def _fill_quick(self, menu: QtWidgets.QMenu) -> None:
        """A new chart of any kind (the ones made are in Active charts)."""
        from .canvas import GROUPS, KINDS, TOP
        menu.clear()

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

        for group, entries in GROUPS.items():
            sub = menu.addMenu(group)
            sub.setToolTipsVisible(True)
            fill(sub, entries)
        fill(menu, TOP)

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
        if getattr(self, "_hook", None) in getattr(chart, "look_hooks", []):
            chart.look_hooks.remove(self._hook)
        self.focus_chart = None
        i = self.right.indexOf(self.page.side_scroll)        # the tabs go back to the figure page
        if i >= 0:
            self.right.removeTab(i)
        self.focus_side.removeWidget(self.right)
        self._fig_lay.addWidget(self.right)
        self.page.side_scroll.setParent(self.page)
        self.chart_box.take()
        home = self._home
        try:
            home.layout().addWidget(chart, 1)
            chart.show()
        except RuntimeError:                     # its frame went (the chart was closed)
            pass
        self.page.side_scroll.hide()
        self.page.side.look.show()
        self.mode.setCurrentIndex(0)
        prev = getattr(self, "_prev", (None, {}, None))
        self.spec, self.data, self.editing = prev[0], prev[1], prev[2]
        self.show_spec(prev[0])
        if switch:
            self.page.show_sub("analysis")

    def _chart_to_canvas(self) -> None:
        """The chart, as it looks now, straight onto the Plot Canvas (no stop in figure editing)."""
        if self.focus_chart is None:
            return
        try:
            spec, data = self.figure_of_chart()
        except Exception as e:
            QtWidgets.QMessageBox.information(self, "Send to Canvas", f"Can't make a figure of it: {e}")
            return
        self.page.plot_canvas.add_panel(spec, data, linked=False)
        self.page.show_sub("plotcanvas")

    def _chart_figure(self) -> None:
        if self.focus_chart is None:
            return
        try:
            spec, data = self.figure_of_chart()
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


class _FigureView(QtWidgets.QLabel):
    """The figure as a picture: grab the plot and drag to move what it shows, scroll over it to
    zoom where you point (Single Plot does the moving: pan_view, zoom_view)."""

    def __init__(self, page: "SinglePlotPage"):
        super().__init__()
        self.page = page
        self.setMouseTracking(True)
        self._press: tuple | None = None          # where a drag began, and the view then

    def _grab_on(self) -> bool:
        return self.page.btn_grab.isChecked() and self.page.spec is not None and not self.page._live()

    def mousePressEvent(self, ev):
        pos = ev.position()
        if ev.button() == QtCore.Qt.MouseButton.LeftButton and self._grab_on() and self.page.in_plot(pos):
            self._press = (pos, dict(self.page._geo))
            self.setCursor(QtCore.Qt.CursorShape.ClosedHandCursor)
            ev.accept()
            return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        pos = ev.position()
        if self._press is not None:
            start, geo = self._press
            self.page.pan_view(geo, pos.x() - start.x(), pos.y() - start.y())
            ev.accept()
            return
        self.setCursor(QtCore.Qt.CursorShape.OpenHandCursor if self._grab_on() and self.page.in_plot(pos)
                       else QtCore.Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        if self._press is not None:
            self._press = None
            self.setCursor(QtCore.Qt.CursorShape.OpenHandCursor)
            ev.accept()
            return
        super().mouseReleaseEvent(ev)

    def wheelEvent(self, ev):
        pos = ev.position()
        if self._grab_on() and self.page.in_plot(pos) and ev.angleDelta().y():
            self.page.zoom_view(0.85 if ev.angleDelta().y() > 0 else 1 / 0.85, pos)
            ev.accept()
            return
        super().wheelEvent(ev)


SHAPES = [("Fill the window", None), ("4 : 3", 0.75), ("3 : 2", 2 / 3), ("16 : 9", 9 / 16),
          ("1 : 1 square", 1.0), ("3 : 4 tall", 4 / 3), ("2 : 3 tall", 1.5)]


class _AspectBox(QtWidgets.QWidget):
    """Holds the live chart so its main plot has a chosen shape (height / width), centred;
    None fills the box. The chart's bars and other stacked plots share its height, so the
    size is found in a few steps, each measuring what the plot got."""

    def __init__(self):
        super().__init__()
        self.child, self.aspect = None, None
        self._size = None                         # (w, h) of the chart, while shaped
        self._steps = 0

    def put(self, w, aspect=None) -> None:
        self.child, self.aspect = w, aspect
        w.setParent(self)
        w.show()
        self._restart()

    def take(self):
        w, self.child = self.child, None
        return w

    def set_aspect(self, aspect) -> None:
        self.aspect = aspect
        self._restart()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._restart()

    def _restart(self) -> None:
        self._size, self._steps = None, 0
        self._place()

    def _place(self) -> None:
        if self.child is None:
            return
        r = self.rect()
        if not self.aspect:
            self.child.setGeometry(r)
            return
        w, h = self._size or (r.width(), int(r.width() * self.aspect))
        h = min(h, r.height())
        self.child.setGeometry(QtCore.QRect(r.x() + (r.width() - w) // 2, r.y() + (r.height() - h) // 2, w, h))
        if self._steps < 6:                       # once its layout has settled, measure the plot
            QtCore.QTimer.singleShot(80, self._settle)

    def recheck(self) -> None:
        """The chart redrew (its plots may be new): check the shape again."""
        if self.aspect and self.child is not None:
            self._steps = min(self._steps, 3)
            QtCore.QTimer.singleShot(80, self._settle)

    def _settle(self) -> None:
        if self.child is None or not self.aspect:
            return
        try:
            plot = self.child.plots()[self.child.main_plot()]
        except Exception:
            return
        r = self.rect()
        cw, ch, pw_, ph = self.child.width(), self.child.height(), plot.width(), plot.height()
        if not (cw and ch and pw_ and ph):
            return
        target = pw_ * self.aspect
        if abs(ph - target) <= 2:
            return
        self._steps += 1
        share = max(0.15, ph / ch)                # how much of the chart's height the plot gets
        h = ch + (target - ph) / share
        w = cw
        if h > r.height():                        # too tall for the room: narrower instead
            w = int(cw * r.height() / h)
            h = r.height()
        self._size = (max(50, int(w)), max(50, int(h)))
        self._place()


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
