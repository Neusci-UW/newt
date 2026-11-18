"""Where the Breadboard's holes are drawn, where a new part's legs go, and the key that
names each leg: plain Python, shared by the Qt page (breadpage.py) and the HTML one
(web/breadboard.py). Positions are in board units (PITCH between holes); see
breadboard.py for the board itself.
"""
from __future__ import annotations

import math

from . import breadboard as bb

PITCH = 20                                    # board units between holes
ROW_Y = {"t+": 20, "t-": 40, **{r: 80 + 20 * i for i, r in enumerate("abcde")},
         **{r: 200 + 20 * i for i, r in enumerate("fghij")}, "b+": 300, "b-": 320}
X0, W, H = 40, 40 + bb.COLS * PITCH + 20, 345
ROWS = ["t+", "t-", *"abcde", *"fghij", "b+", "b-"]   # top to bottom
SPAN = 4                                      # columns a part dropped from the strip spans


def hole_xy(hole: str) -> tuple[float, float]:
    row, col = bb.parse_hole(hole)
    return float(X0 + (col - 1) * PITCH), float(ROW_Y[row])


def nearest_hole(x: float, y: float) -> str | None:
    col = round((x - X0) / PITCH) + 1
    if not 1 <= col <= bb.COLS or abs(x - (X0 + (col - 1) * PITCH)) > 8:
        return None
    row = min(ROW_Y, key=lambda r: abs(ROW_Y[r] - y))
    if abs(ROW_Y[row] - y) > 8:
        return None
    return f"{row}{col}"


def shifted(hole: str, drow: int, dcol: int) -> str | None:
    """`hole` moved down `drow` rows and right `dcol` columns, or None off the board."""
    row, col = bb.parse_hole(hole)
    r, c = ROWS.index(row) + drow, col + dcol
    return f"{ROWS[r]}{c}" if 0 <= r < len(ROWS) and 1 <= c <= bb.COLS else None


def gate_hole(a: str, b: str) -> str | None:
    """A free hole beside the middle of a part from `a` to `b`, for a MOSFET's gate leg."""
    (ax, ay), (bx, by) = hole_xy(a), hole_xy(b)
    mx, my = (ax + bx) / 2, (ay + by) / 2
    dx, dy = bx - ax, by - ay
    n = math.hypot(dx, dy) or 1.0
    for d in (PITCH, -PITCH, 2 * PITCH, -2 * PITCH):
        px, py = mx - dy / n * d, my + dx / n * d
        col = min(max(round((px - X0) / PITCH) + 1, 1), bb.COLS)    # snap to a column, then a row
        row = min(ROW_Y, key=lambda r: abs(ROW_Y[r] - py))
        h = f"{row}{col}"
        if abs(ROW_Y[row] - py) < PITCH and h not in (a, b):
            return h
    return None


def placed_part(kind: str, a: str, b: str = "") -> bb.Part | None:
    """A new part from `a` to `b` with its defaults; a potentiometer gets its wiper beside
    the middle. None if there's no room for the wiper."""
    part = bb.new_part(kind, a, b)
    if kind == "pot":
        part.wiper = gate_hole(a, b) or ""
        if not part.wiper:
            return None
    return part


def dropped_part(kind: str, hole: str) -> bb.Part | None:
    """A part dropped on `hole`: a probe or ground there, anything else from there across SPAN columns."""
    if kind in bb.ONE_HOLE:
        return bb.new_part(kind, hole)
    end = shifted(hole, 0, SPAN) or shifted(hole, 0, -SPAN)
    return placed_part(kind, hole, end)


# ---------------------------------------------------------------- the key: what each leg of a part is
# (leg, tag on the drawing, name, where it goes and what it does)
_ENDS = [("a", "1", "end", "the first hole you click"), ("b", "2", "end", "the second hole; either way round works")]


def key_legs(part: bb.Part) -> list[tuple[str, str, str, str]]:
    k = part.kind
    if k in ("vin", "battery"):
        what = "the stimulator's output" if k == "vin" and part.source == "stim" else "the fixed voltage"
        return [("a", "+", "+ end", f"first hole: held at {what} above the − end"),
                ("b", "−", "− end", "second hole: usually ground (a − rail)")]
    if k in ("led", "diode"):
        return [("a", "+", "anode", "first hole: toward the higher voltage"),
                ("b", "−", "cathode", "second hole: toward ground")]
    if k == "switch" and part.mode == "mosfet":
        return [("a", "D", "drain", "first hole"),
                ("b", "S", "source", "second hole: the gate is measured against it"),
                ("gate", "G", "gate", "placed beside the middle; draws no current")]
    if k == "pot":
        return [("a", "1", "end", "first hole"), ("b", "2", "end", "second hole"),
                ("wiper", "W", "wiper", "placed beside the middle: taps the resistance part-way along")]
    if k == "probe":
        return [("a", "P", "probe", "one hole: records the voltage of its strip")]
    if k == "ground":
        return [("a", "0", "ground", "one hole: ties its strip to 0 V")]
    return _ENDS


def key_text(part: bb.Part) -> str:
    k = part.kind
    if k == "vin":
        return ("Drives its + end to the stimulator's output, so whatever the Stimulus tab sends plays into the circuit."
                if part.source == "stim" else "Holds its + end a fixed voltage above its − end.")
    if k == "switch":
        if part.mode == "mosfet":
            side = "above" if part.channel == "n" else "below"
            return f"Joins drain to source while the gate is more than V<sub>th</sub> {side} the source; otherwise connects nothing."
        return "Joins its two ends while closed; double-click it, or use Close it, to flip it."
    return {
        "resistor": "Lets current through in proportion to the voltage across it (Ohm's law). Either way round.",
        "capacitor": "Stores charge: current flows in while the voltage across it changes, then stops. Either way round.",
        "wire": "Joins two strips into one, so they're at the same voltage.",
        "probe": "Records its strip's voltage on the input you pick, for Acquisition.",
        "ground": "The 0 V reference, the same as a wire to a − rail.",
        "battery": "Holds its + end a fixed voltage above its − end.",
        "led": "Lights when + is more than its forward voltage above −, brighter with more current. "
               "Put a resistor in series to keep the current under its maximum.",
        "diode": "Lets current flow from + to − only, once + is more than its forward voltage above −.",
        "button": "Joins its two ends only while you hold the mouse down on it.",
        "pot": "A resistor with a third leg: the wiper splits it in two, so the wiper's voltage is a set fraction "
               "of the way between the ends (a divider).",
        "inductor": "Resists changes in current: the voltage across it is L times how fast the current changes. Either way round.",
    }.get(k, "")
