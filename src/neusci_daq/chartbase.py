"""A chart on the Analysis canvas. Any chart can be open more than once. What every copy
shares is on the page (the recordings, gain, stimulus fit, the spikes found, the repeats);
what's set per copy is on the chart: its name, units, pinned blocks, zoom link and the
settings it adds with field().

A chart only draws while it can be seen: refresh() marks it stale, and it catches up when
it's shown.
"""
from __future__ import annotations

from dataclasses import replace

import numpy as np
from pyqtgraph.Qt import QtGui, QtWidgets

from . import analysis as A
from . import style, theme
from .helpview import add_help
from .plotzoom import add_buttons
from .viewkit import View, show_scale_bar, shrink_combos, style_plot, with_slider


class Chart(View):
    kind = ""                      # its name in Add chart
    group = ""                     # the heading it's under there
    tip = ""                       # what it shows
    link = ""                      # the charts it zooms with: "time", "phase", or none
    help = ""                      # its help topic
    uses_units = True              # can show its own units
    uses_blocks = True             # can be pinned to chosen blocks

    def __init__(self, page, cid: str = ""):
        super().__init__(page)
        self._mode = page._mode
        self.id = cid
        self.name = self.kind
        self.dock = None
        self.pin: dict | None = None          # {"stems": blocks, "picked": those of them, "pages": names}
        self.own_units: set[int] | None = None
        self.linked = True
        self.stale = True
        self.auto_hide = False                # from a built-in layout: gives up its room when empty
        self.collapsed = False                # nothing to show: its dock gives up its room
        self._fields: dict[str, QtWidgets.QWidget] = {}
        self._wanted: dict[str, object] = {}  # saved choices for channels not listed yet
        self.body = QtWidgets.QVBoxLayout(self)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(2)
        self.options = self._make_options()

    # -- its settings ---------------------------------------------------------------------
    def _make_options(self) -> QtWidgets.QWidget:
        side = QtWidgets.QWidget()
        self._side = QtWidgets.QVBoxLayout(side)
        self._side.setContentsMargins(0, 0, 0, 0)
        self._side.setSpacing(8)
        self.heading = QtWidgets.QLabel()
        self._side.addWidget(self.heading)
        box = QtWidgets.QGroupBox("This chart")
        f = QtWidgets.QFormLayout(box)
        self.name_box = QtWidgets.QLineEdit(self.name)
        self.name_box.setToolTip("What this chart is called in its title bar")
        self.name_box.editingFinished.connect(self._renamed)
        f.addRow("Name", self.name_box)
        self.units_box = QtWidgets.QComboBox()
        self.units_box.addItem("The units ticked", False)
        self.units_box.addItem("Its own", True)
        self.units_box.setToolTip("Which units this chart shows: the ones ticked in the Units table, or its own")
        self.units_box.currentIndexChanged.connect(lambda *_: self._units_mode_changed())
        self.unit_ticks = QtWidgets.QWidget()
        self._ticks = QtWidgets.QVBoxLayout(self.unit_ticks)
        self._ticks.setContentsMargins(0, 0, 0, 0)
        self._ticks.setSpacing(2)
        self.unit_ticks.hide()
        self.pin_box = QtWidgets.QCheckBox("Pin to these trials")
        self.pin_box.setToolTip("Keep this chart on the trials selected now, while the others follow the selection")
        self.pin_box.toggled.connect(self.set_pinned)
        self.pin_note = QtWidgets.QLabel()
        self.pin_note.setWordWrap(True)
        self.link_box = QtWidgets.QCheckBox("Zoom with the others")
        self.link_box.setChecked(True)
        self.link_box.setToolTip("Zoom and pan with the other charts against time, or against phase. Off: on its own")
        self.link_box.toggled.connect(self._link_toggled)
        if self.uses_units:
            f.addRow("Units", self.units_box)
            f.addRow(self.unit_ticks)
        # a chart's own trials are its own Groups, set with the Groups of the settings panel
        self.pin_box.hide()
        self.pin_note.hide()
        if self.link:
            f.addRow(self.link_box)
        self._side.addWidget(box)
        self.settings_box = QtWidgets.QGroupBox("Settings")
        self.form = QtWidgets.QFormLayout(self.settings_box)
        self.settings_box.hide()
        self._side.addWidget(self.settings_box)
        if self.help:
            add_help(self.settings_box, self.help)
            self.settings_box.show()
        self._side.addStretch(1)
        return side

    def field(self, key: str, label: str, widget: QtWidgets.QWidget, then=None) -> QtWidgets.QWidget:
        """A setting of this copy: shown with the chart's settings and saved with the layout.
        Changing it calls then() (default: draw again)."""
        self._fields[key] = widget
        self.settings_box.show()
        shown = with_slider(widget) if isinstance(widget, QtWidgets.QAbstractSpinBox) else widget
        if label:
            self.form.addRow(label, shown)
        else:
            self.form.addRow(shown)
        changed = {QtWidgets.QCheckBox: "toggled", QtWidgets.QComboBox: "currentIndexChanged"}
        signal = next((getattr(widget, s) for t, s in changed.items() if isinstance(widget, t)), None) \
            or widget.valueChanged
        signal.connect(lambda *_: self._setting_changed(then))
        return widget

    def _setting_changed(self, then) -> None:
        (then or self.refresh)()
        self.saved()

    def saved(self) -> None:
        """Something the layout remembers changed."""
        self.page.canvas.changed()

    def state(self) -> dict:
        """What's set on this copy, as plain values."""
        fields = {}
        for key, w in self._fields.items():
            if isinstance(w, QtWidgets.QCheckBox):
                fields[key] = w.isChecked()
            elif isinstance(w, QtWidgets.QComboBox):
                fields[key] = w.currentData() if w.currentData() is not None else w.currentText()
            else:
                fields[key] = w.value()
        return {"name": self.name, "pin": self.pin, "linked": self.linked, "auto_hide": self.auto_hide,
                "fields": {**self._wanted, **fields},
                "units": None if self.own_units is None else sorted(self.own_units)}

    def restore(self, state: dict) -> None:
        self.name = state.get("name", self.name)
        self.name_box.setText(self.name)
        self.pin = state.get("pin")
        self.linked = state.get("linked", True)
        self.auto_hide = state.get("auto_hide", False)
        units = state.get("units")
        self.own_units = None if units is None else set(units)
        self.units_box.blockSignals(True)
        self.units_box.setCurrentIndex(0 if self.own_units is None else 1)
        self.units_box.blockSignals(False)
        self.unit_ticks.setVisible(self.own_units is not None)
        for w, on in ((self.pin_box, self.pin is not None), (self.link_box, self.linked)):
            w.blockSignals(True)
            w.setChecked(on)
            w.blockSignals(False)
        self._wanted = dict(state.get("fields", {}))
        self._apply_wanted()
        self._show_pin()

    def _apply_wanted(self) -> None:
        for key, value in list(self._wanted.items()):
            w = self._fields.get(key)
            if w is None:
                continue
            w.blockSignals(True)
            if isinstance(w, QtWidgets.QCheckBox):
                w.setChecked(bool(value))
                self._wanted.pop(key)
            elif isinstance(w, QtWidgets.QComboBox):
                i = w.findData(value)
                i = i if i >= 0 else w.findText(str(value))
                if i >= 0:
                    w.setCurrentIndex(i)
                    self._wanted.pop(key)
            else:
                w.setValue(value)
                self._wanted.pop(key)
            w.blockSignals(False)

    def fill(self, combo: QtWidgets.QComboBox, names: list[str], first: tuple[str, object] | None = None,
             default: str | None = None) -> None:
        """List these channels (after `first`), keeping the choice, else `default`."""
        keep = combo.currentData() if combo.currentData() is not None else combo.currentText()
        combo.blockSignals(True)
        combo.clear()
        if first:
            combo.addItem(*first)
        for n in names:
            combo.addItem(n, n)
        i = combo.findData(keep) if combo.count() else -1
        if i < 0 and default is not None:
            i = combo.findData(default)
        combo.setCurrentIndex(max(0, i))
        combo.blockSignals(False)
        self._apply_wanted()

    def channels_changed(self) -> None:
        """The session's channels changed: list them in this chart's settings."""

    def stimulus_changed(self) -> None:
        """Another channel is the stimulus."""

    def _link_toggled(self, on: bool) -> None:
        self.linked = on
        self.page.canvas.link_changed(self)

    def _renamed(self) -> None:
        name = self.name_box.text().strip()
        if name and name != self.name:
            self.name = name
            self.show_title()
            if self.page.canvas.active is self:
                self.page.side.update_for_picked()
            self.saved()

    def show_title(self) -> None:
        self.heading.setText(f"<b>{self.name}</b>")
        if self.dock is not None:
            self.dock.setWindowTitle(self.name)
            self.dock.show_title(self.name, self.tag(), self.pin is not None)

    def tag(self) -> str:
        """What this copy has of its own, for its title bar."""
        parts = []
        if self.own_units is not None:
            parts.append(", ".join(f"Unit {u + 1}" for u in sorted(self.own_units)) or "no units")
        if self.pin is not None:
            parts.append(", ".join(self.pin["pages"]) or (f"{len(self.pin['stems'])} trial(s)" if self.pin["stems"]
                                                          else "no groups"))
        return " · ".join(parts)

    # -- what it shows -------------------------------------------------------------------------
    @property
    def finder(self):
        return self.page.raw

    @property
    def data(self):
        return self.page.blocks

    def blocks(self) -> list[int]:
        """The blocks it shows: those it's pinned to, or those chosen."""
        if self.pin is None:
            return self.finder.chosen_or_none()
        where = {p.stem: i for i, p in enumerate(self.page._paths)}
        return self.finder.usable([where[s] for s in self.pin["stems"] if s in where], self.pin.get("own", False))

    def plan(self, chosen: list[int]):
        """Which of these to average and which to show as trials (see AnalysisPage.plan)."""
        if self.pin is None:
            return self.page.plan(chosen)
        where = {p.stem: i for i, p in enumerate(self.page._paths)}
        return self.page.plan(chosen, [where[s] for s in self.pin["picked"] if s in where], self.pin["pages"])

    def set_pinned(self, on: bool) -> None:
        """Hold on to the blocks selected now, or follow the selection again."""
        if on:
            stems = [self.page._paths[i].stem for i in self.finder.chosen_or_none()]
            self.pin = {"stems": stems, "pages": list(self.page.picked_pages),
                        "picked": [self.page._paths[i].stem for i in self.page.picked]}
        else:
            self.pin = None
        self.pin_box.blockSignals(True)
        self.pin_box.setChecked(on)
        self.pin_box.blockSignals(False)
        self._show_pin()
        self.show_title()
        self.saved()
        self.page.raw.pins_changed()
        self.refresh()

    def set_own_groups(self, names: list[str] | None) -> None:
        """Show just these groups, whatever the page shows (they're its own), or follow the
        page again (None)."""
        if names is None:
            self.pin = None
        else:
            page = self.page
            stems = [page._paths[i].stem for n, blocks in page.shown if n in names for i in blocks]
            self.pin = {"stems": stems, "pages": list(names), "picked": stems, "own": True}
        self.pin_box.blockSignals(True)
        self.pin_box.setChecked(self.pin is not None)
        self.pin_box.blockSignals(False)
        self._show_pin()
        self.show_title()
        self.saved()
        self.page.raw.pins_changed()
        self.refresh()

    def _show_pin(self) -> None:
        self.pin_note.setVisible(self.pin is not None)
        if self.pin is not None:
            self.pin_note.setText(f"{len(self.pin['stems'])} trial(s). Stays put when the selection changes")

    def units(self) -> set[int]:
        """The units it shows (0-based)."""
        if self.own_units is None:
            return self.finder.shown_units()
        ev = self.page.events
        return self.own_units & set(range(ev.n_units if ev is not None else 0))

    def events(self) -> A.Events | None:
        """The spikes found, in the blocks it shows."""
        ev = self.page.events
        if ev is None:
            return None
        return replace_events(ev, np.isin(ev.block, self.blocks()))

    def _units_mode_changed(self) -> None:
        on = bool(self.units_box.currentData())
        self.own_units = (set(self.finder.shown_units()) if on else None)
        self.unit_ticks.setVisible(on)
        self._fill_units()
        self.show_title()
        self.saved()
        self.units_changed()

    def _fill_units(self) -> None:
        """A tick for each unit, while it has its own."""
        while self._ticks.count():
            self._ticks.takeAt(0).widget().deleteLater()
        ev = self.page.events
        if self.own_units is None or ev is None:
            return
        for u in range(ev.n_units):
            box = QtWidgets.QCheckBox(f"Unit {u + 1}")
            box.setToolTip("Show this unit on this chart")
            box.setIcon(style.dot_icon(self.finder.color_of(u)))
            box.setChecked(u in self.own_units)
            box.toggled.connect(lambda on, u=u: self.show_unit(u, on))
            self._ticks.addWidget(box)

    def show_unit(self, u: int, on: bool) -> None:
        self.own_units = (self.own_units or set()) | {u} if on else (self.own_units or set()) - {u}
        self.show_title()
        self.saved()
        self.units_changed()

    # -- drawing ---------------------------------------------------------------------------------
    def plots(self) -> list:
        """Its plot widgets. The first is the one it zooms with the others by."""
        return []

    def draw(self) -> None:
        raise NotImplementedError

    def empty(self) -> bool:
        """Nothing to show, so its dock gives up its room."""
        return False

    def refresh(self) -> None:
        """What it shows has changed: draw it now if it can be seen, else when it next can."""
        self.stale = True
        self.collapse()
        if self.isVisible():
            self.redraw()

    def redraw(self) -> None:
        self.stale = False
        self.draw()
        self.show_bars()
        for pw in self.plots():
            add_buttons(pw)
        measure = getattr(self.page, "measure", None)
        if measure is not None:
            measure.decorate(self)

    def measure_channel(self, k: int) -> str:
        """The channel a window dragged across plot k measures: the one the spikes are found on."""
        return self.page.raw.channel.currentText()

    def collapse(self, always: bool = False) -> None:
        """Give up its dock's room while it has nothing to show, unless you opened it: a chart
        you asked for stays, and says why it's empty."""
        empty = self.auto_hide and self.empty()
        if self.dock is not None and (always or empty != self.collapsed):
            self.collapsed = empty
            self.dock.setVisible(not empty)

    def showEvent(self, ev):
        super().showEvent(ev)
        if self.stale:
            self.redraw()

    def events_changed(self) -> None:
        """Other spikes were found."""
        self._fill_units()
        self.refresh()

    def units_changed(self) -> None:
        """Other units are ticked, or their colours changed."""
        self.refresh()

    def current_changed(self) -> None:
        """Another block is highlighted."""

    def threshold_changed(self) -> None:
        """The threshold moved."""

    def rescale(self) -> None:
        """Fit its y axis to its data again."""
        for pw in self.plots():
            pw.getPlotItem().enableAutoRange(axis="y")
            pw.getPlotItem().vb.updateAutoRange()     # now: a chart off screen on the board isn't painted

    def listen_for_mark(self, on_drag) -> None:
        """Dragging across this chart marks one repeat, if it's a trace of the stimulus."""

    def scale_bars(self) -> list[tuple]:
        """(plot, y unit, x unit) for each of its plots that can swap its axes for a scale bar."""
        return []

    def show_bars(self) -> None:
        for pw, y_unit, x_unit in self.scale_bars():
            show_scale_bar(pw, self.page.canvas.bars.isChecked(), self._fg(), y_unit, x_unit)

    def time_range(self, end: float) -> None:
        """Its plot runs from 0 to `end` (s); a new end shows all of it."""
        for pw in self.plots():
            pw.getPlotItem().vb.bounds[0] = (0.0, end) if end else None
        if end and end != getattr(self, "_end", None):
            self.plots()[0].getPlotItem().setXRange(0, end, padding=0)
        self._end = end

    def apply_theme(self, mode: str) -> None:
        self._mode = mode
        th = theme.THEMES[theme.DARK if mode == style.DARK else theme.LIGHT]
        for pw in self.plots():
            style_plot(pw, th)
        self.show_bars()

    def wrapped_options(self) -> QtWidgets.QWidget:
        """Its settings, for the settings window."""
        self.show_title()
        self._show_pin()
        shrink_combos(self.options)
        return self.options

    def faded(self, color, alpha: int) -> QtGui.QColor:
        """`color` this much see-through (0 to 255), on top of its own opacity."""
        c = QtGui.QColor(color)
        c.setAlpha(round(alpha * c.alphaF()))
        return c


def replace_events(ev: A.Events, keep: np.ndarray) -> A.Events:
    """The same search, with only these spikes."""
    if keep.all():
        return ev
    return replace(ev, block=ev.block[keep], t=ev.t[keep], unit=ev.unit[keep], amplitude=ev.amplitude[keep],
                   waves=ev.waves[keep])


def dot_size(settings, key: str, default: int, top: int, tip: str) -> QtWidgets.QSpinBox:
    """A dot size, starting from the last one used."""
    box = QtWidgets.QSpinBox(minimum=1, maximum=top)
    box.setValue(settings.value(key, default, type=int))
    box.setToolTip(tip)
    box.valueChanged.connect(lambda v: settings.setValue(key, v))
    return box

