"""The ? buttons: a short explanation of an Analysis tab, with a picture and a glossary of
its terms. A topic is plain data, so another tab only needs its own entry in TOPICS.
"""
from __future__ import annotations

import numpy as np
from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

U1, U2, RED, ACCENT = "#E69F00", "#d81b60", "#e0524f", "#56B4E9"
WIDTH, PANEL_H = 860, 150


class _Pen:
    """Drawing a row of numbered steps, in the app's colours."""

    def __init__(self, steps: list[tuple[str, str]]):
        pal = QtWidgets.QApplication.palette()
        self.fg = pal.color(QtGui.QPalette.ColorRole.WindowText)
        self.dark = self.fg.lightness() > 128
        self.back = QtGui.QColor("#000000" if self.dark else "#ffffff")
        self.muted = QtGui.QColor("#9a9a9a" if self.dark else "#606060")
        self.pix = QtGui.QPixmap(WIDTH * 2, (PANEL_H + 54) * 2)
        self.pix.setDevicePixelRatio(2)
        self.pix.fill(QtGui.QColor(0, 0, 0, 0))
        self.p = QtGui.QPainter(self.pix)
        self.p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        gap = 26
        n = len(steps)
        self.w = (WIDTH - (n - 1) * gap) / n
        self.xs = [k * (self.w + gap) for k in range(n)]
        for k, (title, sub) in enumerate(steps):
            self._step(k, title, sub)
            if k < n - 1:
                ax, y = self.xs[k] + self.w + 5, PANEL_H / 2
                self.line([ax, ax + gap - 10], [y, y], self.muted, 1.5)
                self.line([ax + gap - 16, ax + gap - 10, ax + gap - 16], [y - 4, y, y + 4], self.muted, 1.5)

    def _step(self, k: int, title: str, sub: str) -> None:
        p, x, w = self.p, self.xs[k], self.w
        p.setPen(QtGui.QPen(self.muted, 1))
        p.setBrush(self.back)
        p.drawRoundedRect(QtCore.QRectF(x, 0, w, PANEL_H), 6, 6)
        f = p.font()
        f.setPixelSize(13)
        f.setBold(True)
        p.setFont(f)
        p.setPen(self.fg)
        centred = QtCore.Qt.AlignmentFlag.AlignHCenter
        p.drawText(QtCore.QRectF(x, PANEL_H + 8, w, 20), centred, f"{k + 1}. {title}")
        f.setBold(False)
        f.setPixelSize(12)
        p.setFont(f)
        p.setPen(self.muted)
        p.drawText(QtCore.QRectF(x, PANEL_H + 27, w, 20), centred, sub)

    def line(self, xs, ys, color, width=1.2, alpha=255) -> None:
        c = QtGui.QColor(color)
        c.setAlpha(alpha)
        self.p.setPen(QtGui.QPen(c, width))
        self.p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        self.p.drawPolyline(QtGui.QPolygonF([QtCore.QPointF(a, b) for a, b in zip(xs, ys)]))

    def dot(self, x, y, color, r=3.2) -> None:
        self.p.setPen(QtCore.Qt.PenStyle.NoPen)
        self.p.setBrush(QtGui.QColor(color))
        self.p.drawEllipse(QtCore.QPointF(x, y), r, r)

    def dashed(self, x0, y0, x1, y1, color, style=QtCore.Qt.PenStyle.DashLine) -> None:
        self.p.setPen(QtGui.QPen(QtGui.QColor(color), 1.3, style))
        self.p.drawLine(QtCore.QPointF(x0, y0), QtCore.QPointF(x1, y1))

    def done(self) -> QtGui.QPixmap:
        self.p.end()
        return self.pix


def _spike(t: np.ndarray, a: float, width: float = 1.0) -> np.ndarray:
    return a * (-np.exp(-(t / (0.18 * width)) ** 2) + 0.5 * np.exp(-((t - 0.45 * width) / (0.3 * width)) ** 2))


def _noisy_spikes(rng, n: int, where: list[tuple[int, float]]) -> np.ndarray:
    y = rng.normal(0, 0.05, n)
    k = np.arange(-8, 14)
    for at, a in where:
        y[at - 8:at + 14] += a * (-np.exp(-(k / 2.2) ** 2) + 0.45 * np.exp(-((k - 5) / 4) ** 2))
    return y


def _spike_sorting_picture() -> QtGui.QPixmap:
    d = _Pen([("Detect", "Past the threshold"), ("Cut out", "Line up on the peak"),
              ("Compare shapes", "Each spike is a point"), ("Sort", "k-means makes the units")])
    rng = np.random.default_rng(3)
    w, ph = d.w, PANEL_H
    # 1: the trace, the threshold, the events
    n = 340
    where = [(45, 1.0), (115, 0.6), (190, 1.0), (255, 0.6), (305, 1.0)]
    y = _noisy_spikes(rng, n, where)
    px = d.xs[0] + 6 + np.arange(n) / n * (w - 12)
    py = 42 - y * 78
    d.line(px, py, d.fg, 1, 200)
    d.dashed(d.xs[0] + 4, 42 + 0.32 * 78, d.xs[0] + w - 4, 42 + 0.32 * 78, RED)
    for at, _ in where:
        d.dot(px[at], py[at - 2:at + 3].max(), d.fg)
    # 2: the waveforms, lined up
    t = np.linspace(-1, 2, 60)
    cx = d.xs[1] + 12 + (t - t[0]) / np.ptp(t) * (w - 24)
    for a, width in ((1.0, 1.0), (0.6, 1.5)):
        for _ in range(14):
            d.line(cx, 56 - (_spike(t, a * rng.normal(1, 0.06), width) + rng.normal(0, 0.025, len(t))) * 72,
                   d.fg, 0.8, 90)
    peak = d.xs[1] + 12 + (0 - t[0]) / np.ptp(t) * (w - 24)
    d.dashed(peak, 8, peak, ph - 8, d.muted, QtCore.Qt.PenStyle.DotLine)
    # 3 and 4: the same points, then in their units with k-means' centres
    a = np.clip(rng.normal((-0.5, 0.25), (0.14, 0.13), (40, 2)), -0.8, 0.6)
    b = np.clip(rng.normal((0.5, -0.25), (0.14, 0.13), (40, 2)), -0.6, 0.8)
    for k, (ca, cb) in ((2, (d.fg, d.fg)), (3, (U1, U2))):
        mx = d.xs[k] + w / 2
        for pts, c in ((a, ca), (b, cb)):
            for u, v in pts:
                d.dot(mx + u * w * 0.55, ph / 2 + v * ph * 0.72, c, 2.4)
    mx = d.xs[3] + w / 2
    for u, v in ((-0.5, 0.25), (0.5, -0.25)):
        ex, ey = mx + u * w * 0.55, ph / 2 + v * ph * 0.72
        d.line([ex - 5, ex + 5], [ey - 5, ey + 5], d.fg, 2)
        d.line([ex - 5, ex + 5], [ey + 5, ey - 5], d.fg, 2)
    return d.done()


def _comparison_picture() -> QtGui.QPixmap:
    d = _Pen([("Find the triggers", "Stimuli, or spikes"), ("Cut around each", "Time 0 is the trigger"),
              ("Average", "Noise cancels, the response stays")])
    rng = np.random.default_rng(4)
    w, ph = d.w, PANEL_H
    n = 400
    x = d.xs[0] + 6 + np.arange(n) / n * (w - 12)
    triggers = (70, 200, 330)
    t = np.arange(n)
    resp = sum(-np.exp(-((t - k - 14) / 7) ** 2) for k in triggers) * 0.5
    d.line(x, ph / 2 - (resp + rng.normal(0, 0.08, n)) * 60, d.fg, 1, 200)
    for k in triggers:
        d.dashed(x[k], 8, x[k], ph - 8, U1)
    t = np.linspace(-20, 80, 200)
    shape = -np.exp(-((t - 14) / 7) ** 2) * 0.5
    x = d.xs[1] + 8 + (t - t[0]) / np.ptp(t) * (w - 16)
    for _ in range(9):
        d.line(x, ph / 2 - (shape + rng.normal(0, 0.08, len(t))) * 60, d.fg, 0.8, 90)
    zero = x[np.argmin(np.abs(t))]
    d.dashed(zero, 8, zero, ph - 8, U1)
    x = d.xs[2] + 8 + (t - t[0]) / np.ptp(t) * (w - 16)
    d.line(x, ph / 2 - (shape + rng.normal(0, 0.012, len(t))) * 60, ACCENT, 2)
    zero = x[np.argmin(np.abs(t))]
    d.dashed(zero, 8, zero, ph - 8, U1)
    return d.done()


def _cycles_picture() -> QtGui.QPixmap:
    d = _Pen([("Find the cycles", "Each rise through the middle"), ("Cut each one", "Stretched to 0–360°"),
              ("Overlay", "By phase, not time")])
    rng = np.random.default_rng(6)
    w, ph = d.w, PANEL_H
    n = 400
    x = d.xs[0] + 6 + np.arange(n) / n * (w - 12)
    a = np.arange(n) / n * 4 * 2 * np.pi
    d.line(x, ph / 2 - np.sin(a) * 45, d.fg, 1.3)
    for k in range(5):
        at = min(n - 1, k * n // 4)
        d.dashed(x[at], 8, x[at], ph - 8, U1)
    t = np.linspace(0, 2 * np.pi, 120)
    for k, gain in enumerate((1.0, 0.8, 0.65)):
        x = d.xs[1] + 8 + k * (w - 16) / 3 + t / (2 * np.pi) * ((w - 16) / 3 - 6)
        d.line(x, ph / 2 - (gain * np.sin(t - 0.6) + rng.normal(0, 0.03, len(t))) * 40, ACCENT, 1.2)
    x = d.xs[2] + 8 + t / (2 * np.pi) * (w - 16)
    for gain in (1.0, 0.8, 0.65):
        d.line(x, ph / 2 - (gain * np.sin(t - 0.6) + rng.normal(0, 0.03, len(t))) * 50, ACCENT, 1, 150)
    d.line(x, ph / 2 - 0.82 * np.sin(t - 0.6) * 50, d.fg, 2)
    return d.done()


def _waveforms_picture() -> QtGui.QPixmap:
    d = _Pen([("Take a spike", "From Spike Sorting"), ("Find its slope", "dV/dt at each moment"),
              ("Plot one on the other", "Time drops out, a loop is left")])
    w, ph = d.w, PANEL_H
    t = np.linspace(-1, 3, 300)
    v = -np.exp(-(t / 0.3) ** 2) + 0.4 * np.exp(-((t - 0.8) / 0.6) ** 2)
    dv = np.gradient(v, t)
    for k, y, color in ((0, v, d.fg), (1, dv / np.abs(dv).max(), ACCENT)):
        x = d.xs[k] + 8 + (t - t[0]) / np.ptp(t) * (w - 16)
        d.line(x, ph / 2 - y * 55, color, 1.6)
    mid = d.xs[2] + w / 2
    d.line(mid + (v + 0.3) * (w - 40) / 1.5, ph / 2 - dv / np.abs(dv).max() * 55, U1, 1.8)
    d.dashed(d.xs[2] + 8, ph / 2, d.xs[2] + w - 8, ph / 2, d.muted, QtCore.Qt.PenStyle.DotLine)
    return d.done()


def _datapad_picture() -> QtGui.QPixmap:
    d = _Pen([("Pick a window", "When, in each block"), ("Measure", "One number per column"),
              ("Compare", "Table, scatter or fit")])
    rng = np.random.default_rng(5)
    w, ph = d.w, PANEL_H
    n = 300
    x = d.xs[0] + 6 + np.arange(n) / n * (w - 12)
    t = np.arange(n)
    d.p.setPen(QtCore.Qt.PenStyle.NoPen)
    shade = QtGui.QColor(ACCENT)
    shade.setAlpha(60)
    d.p.setBrush(shade)
    d.p.drawRect(QtCore.QRectF(x[110], 2, x[190] - x[110], ph - 4))
    d.line(x, ph / 2 - (-np.exp(-((t - 150) / 18) ** 2) * 0.8 + rng.normal(0, 0.05, n)) * 50, d.fg, 1, 200)
    # a table of numbers
    p = d.p
    f = p.font()
    f.setPixelSize(12)
    p.setFont(f)
    x0 = d.xs[1] + 16
    for r, row in enumerate((("Block", "Peak"), ("1", "41 mV"), ("2", "37 mV"), ("3", "30 mV"), ("4", "26 mV"))):
        p.setPen(d.fg if r else d.muted)
        for c, text in enumerate(row):
            p.drawText(QtCore.QPointF(x0 + c * (d.w - 32) * 0.5, 30 + r * 24), text)
    x0, y0 = d.xs[2] + 20, ph - 20
    d.line([x0, x0, d.xs[2] + w - 12], [12, y0, y0], d.muted, 1)
    xs = np.linspace(0, 1, 12)
    for a, b in zip(xs, 0.15 + 0.7 * xs + rng.normal(0, 0.05, 12)):
        d.dot(x0 + 6 + a * (w - 44), y0 - b * (ph - 40), ACCENT)
    d.line([x0 + 6, x0 + 6 + (w - 44)], [y0 - 0.15 * (ph - 40), y0 - 0.85 * (ph - 40)], U1, 1.5)
    return d.done()


TOPICS = {
    "spike_sorting": {
        "title": "How Find action potentials works",
        "picture": _spike_sorting_picture,
        "text": "Argus finds every place the trace passes the <b>threshold</b> and cuts out a short <b>waveform</b> "
                "around each peak. Spikes from the same cell look alike, so it reduces each waveform to its 3 "
                "<b>principal components</b>, which turns each spike into a point, and <b>k-means</b> groups "
                "nearby points into <b>units</b>.",
        "warning": "Picking a number of units always gives you that many groups (or fewer if one is tiny), even "
                   "if the spikes are all one cell. Check that the units really differ in Waveforms and AP sizes.",
        "glossary": (
            ("Baseline", "The trace's middle level (its median). Thresholds are measured from here."),
            ("Noise SD", "How much the trace wobbles without spikes. 5 SD means 5 times that wobble."),
            ("Threshold", "How far from the baseline the trace must go to count as an event."),
            ("Method", "Manual: you set the threshold. Bimodal: Argus puts it between noise and spikes."),
            ("Direction", "Which way spikes go from the baseline: down, up or either."),
            ("Biphasic", "Keep only events that also swing the other way, as real spikes do."),
            ("Dead time", "After an event, no other can start for this long."),
            ("Waveform before / after", "How much of each spike to cut out around its peak."),
            ("Units", "How many groups to sort spikes into. Auto picks the number that separates best."),
            ("Unit", "A group of spikes with the same shape, likely from one cell."),
            ("Principal components", "The main ways the spike shapes differ. Argus uses the first 3."),
            ("k-means", "Groups points around k centres (the × marks), each point joining its nearest."),
        ),
    },
    "comparison": {
        "title": "How Response works",
        "picture": _comparison_picture,
        "text": "The traces show the chosen blocks' channels on one time axis, as voltage or its rate of change, "
                "the same way as Spike Sorting: a picked page's average, or picked blocks' trials. "
                "Below, the chosen blocks line up on a <b>trigger</b> and are averaged: what's random "
                "cancels and what follows the trigger stays. The <b>stimulus-triggered rate</b> asks how spiking "
                "changes after a stimulus, and the <b>spike-triggered average</b> asks what a channel does around "
                "each spike.",
        "warning": "Both averages need the events from Spike Sorting, so find action potentials there first. "
                   "They use the units ticked there.",
        "glossary": (
            ("dV/dt, d²V/dt²", "How fast the voltage changes, and how fast that changes."),
            ("Smooth for d/dt", "Averages the trace first, since differentiating amplifies noise."),
            ("Trigger", "The moment each trace is lined up on: time 0."),
            ("Bin", "The width of each bar in the stimulus-triggered rate."),
            ("Before / after", "How much to show on each side of the trigger."),
            ("Spread", "SD shows how much trials vary; SEM how sure the average is."),
        ),
    },
    "cycles": {
        "title": "How Stimulus cycles work",
        "picture": _cycles_picture,
        "text": "With a repeating stimulus, Argus cuts the chosen blocks into single <b>cycles</b> and overlays them "
                "by stimulus <b>phase</b> instead of time. It finds them by matching the stimulus's shape against "
                "itself, then by its rises through its middle level; orange marks show the cuts on its trace. Or give "
                "the <b>repeat length</b>, or mark one on the traces. Picked pages show their average and picked "
                "blocks their own, with every cycle behind it coloured from first to last.",
        "warning": "A fixed delay lands at a different phase at each frequency: 5 ms is 18° at 10 Hz but 90° at 50 Hz. "
                   "Cycles are stretched to line up, so compare timing between frequencies with care.",
        "glossary": (
            ("Cycle", "One repeat of the stimulus, from one rise through its middle to the next."),
            ("Phase", "How far through a cycle: 0° at the rise, 180° halfway, 360° at the next rise."),
            ("Repeat length", "How long one repeat is. 0 lets Argus find it."),
            ("Left out", "Repeats too unlike the rest to use, counted in the Stimulus title."),
            ("Skip first", "Leaves out the first cycles of each block, while the response settles."),
            ("Spikes per phase", "A histogram of when in the cycle spikes happen, per cycle."),
            ("Vector strength", "How tightly spikes lock to one phase: 1 is exact, near 0 is none."),
            ("Response against stimulus", "A loop: open if the response lags, a line if it doesn't."),
        ),
    },
    "waveforms": {
        "title": "How Waveforms works",
        "picture": _waveforms_picture,
        "text": "This shows the shape of the <b>action potentials</b> found on Spike Sorting. The <b>phase plot</b> "
                "puts V against <b>dV/dt</b>, so each spike becomes a loop: time drops out, and differences in how "
                "fast a spike rises and falls stand out. Thick lines are each unit's average.",
        "warning": "With an extracellular recording this is the shape of the recorded signal, not the membrane "
                   "voltage. Find action potentials on Spike Sorting first.",
        "glossary": (
            ("dV/dt", "How fast the voltage changes, at each moment of the spike."),
            ("Phase plot", "V against dV/dt: the spike as a loop, with no time axis."),
            ("Smooth for dV/dt", "Averages the spike first, since differentiating amplifies noise."),
            ("Each spike", "Draws single spikes behind the average, up to 150 of each unit."),
            ("Unit", "A group of spikes with the same shape, likely from one cell."),
        ),
    },
    "datapad": {
        "title": "How DataPad works",
        "picture": _datapad_picture,
        "text": "DataPad turns traces into numbers. Each <b>column</b> is a channel and something to measure on it, "
                "the <b>window</b> says when in each block to measure, and each <b>row</b> is a block, or the "
                "average of several. Then read the table, or plot one column against another and <b>fit</b> a "
                "curve to the points.",
        "warning": "",
        "glossary": (
            ("Column", "A channel and a measurement, such as the peak of Ch1."),
            ("Row", "One block or average, measured in every column."),
            ("Window", "The stretch of each block that's measured."),
            ("Repeat every", "Measures the window again further along, adding a row each time."),
            ("Recompute", "Measures every row again with the settings as they are now."),
            ("Scatter", "One column against another, a point per row."),
            ("Fit", "The curve that comes closest to the points."),
            ("Spread", "SD shows how much rows vary; SEM how sure the average is."),
        ),
    },
}


class HelpDialog(QtWidgets.QDialog):
    def __init__(self, key: str, parent=None):
        super().__init__(parent)
        topic = TOPICS[key]
        self.setWindowTitle(topic["title"])
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(10)
        v.addWidget(QtWidgets.QLabel(f"<span style='font-size:17px; font-weight:600'>{topic['title']}</span>"))
        picture = QtWidgets.QLabel()
        picture.setPixmap(topic["picture"]())
        v.addWidget(picture)
        text = QtWidgets.QLabel(topic["text"])
        text.setWordWrap(True)
        v.addWidget(text)
        if topic["warning"]:
            warning = QtWidgets.QLabel(f"<span style='color:{U1}'>{topic['warning']}</span>")
            warning.setWordWrap(True)
            v.addWidget(warning)
        v.addWidget(QtWidgets.QLabel("<span style='font-size:14px; font-weight:600'>Glossary</span>"))
        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(6)
        terms = topic["glossary"]
        half = (len(terms) + 1) // 2
        top = QtCore.Qt.AlignmentFlag.AlignTop
        for k, (term, meaning) in enumerate(terms):
            r, c = k % half, 2 * (k // half)
            name = QtWidgets.QLabel(f"<b>{term}</b>")
            name.setFixedWidth(118)
            name.setWordWrap(True)
            name.setAlignment(top)
            what = QtWidgets.QLabel(meaning)
            what.setWordWrap(True)
            what.setAlignment(top)
            grid.addWidget(name, r, c)
            grid.addWidget(what, r, c + 1)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        v.addLayout(grid)
        self.resize(WIDTH + 40, self.sizeHint().height())


class _Corner(QtCore.QObject):
    """Keeps a button in its parent's top right corner."""

    def __init__(self, parent: QtWidgets.QWidget, button: QtWidgets.QWidget):
        super().__init__(parent)
        self.button = button
        parent.installEventFilter(self)

    def eventFilter(self, obj, ev):
        if ev.type() == QtCore.QEvent.Type.Resize:
            self.button.move(obj.width() - self.button.width() - 4, 0)
        return False


def add_help(box: QtWidgets.QWidget, key: str) -> QtWidgets.QToolButton:
    """A ? button in the top right corner of `box`, opening the help on `key`."""
    button = QtWidgets.QToolButton(box, text="?", autoRaise=True)
    button.setToolTip("How this works, with a glossary of its terms")
    button.setFixedSize(20, 20)
    button.clicked.connect(lambda: HelpDialog(key, box.window()).exec())
    _Corner(box, button)
    return button
