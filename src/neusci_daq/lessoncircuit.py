"""A lesson's circuit, rebuilt on the Breadboard simulator.

A lesson page describes its membrane circuit (lessons/breadboard.js) as compartments,
each a capacitor with branches of a conductance in series with a battery, joined by
axial conductances, with a current injected into some. Units are F, S, V, A and s, or
the same per cm² (a per-cm² circuit is built as a 1 cm² patch).

By default the parts have the cell's own values: the same mV, µF (or pF), kΩ (or GΩ)
and ms as the lesson. With `scale: "breadboard"` the circuit is rescaled instead to
parts you could buy and voltages a DAQ reads easily:

- time: a power of ten, so the slowest time constant lands between 10 and 100 ms
  (easy to see on a scope or the Record page at 10 kHz)
- voltage: 1 mV in the cell is 10 mV on the board, so a -70 mV rest reads -0.7 V
- impedance: resistances around 10 kΩ and capacitors in µF, snapped to the values
  sold (E6 capacitors, E12 resistors). A branch the lesson changes (`vary`) is a
  potentiometer wired as a variable resistor, set to the lesson's value.

In either, an injected current becomes the stimulator's output through a series resistor ten
times the compartment's own resistance (so it barely loads the membrane), smaller if the
pulse would otherwise pass 9 V, or a fixed input voltage when the current is steady (`dc`).

Layout, per compartment, left to right: the stimulus column (if any), the node column,
then one column per branch. The node is a column's lower and upper strips joined by a
wire across the channel: the capacitor goes from it to the top ground rail, its probe
sits in it, and each branch's resistor leaves from rows g-j (up to four); the probe,
the stimulus resistor and axial resistors take the holes left (c, b, d, then j). Each branch's battery sits from its
own column's lower strip to the top ground rail, joined to the resistor by a wire or a
switch across the channel. A branch can also be a push button (`button`), or a synapse
the Stimulator opens (`gate: "stim"`): a MOSFET switch whose gate the Stimulator's
output drives from one more column; its `train` ({n, interval, width}, in the lesson's
time) says what pulses to play.
"""
from __future__ import annotations

import math

from . import breadboard as bb

V_SCALE = 10.0                           # breadboard scale: board volts per cell volt (1 mV -> 10 mV)
R_TARGET = 10e3                          # Ω, where the circuit's resistances are centred
E6 = (1.0, 1.5, 2.2, 3.3, 4.7, 6.8)
E12 = (1.0, 1.2, 1.5, 1.8, 2.2, 2.7, 3.3, 3.9, 4.7, 5.6, 6.8, 8.2)
V_MAX = 10.0                             # V, the most an input or battery should be
V_PULSE = 9.0                            # V, the most a stimulus pulse is made


def _snap(x: float, series=E12) -> float:
    """The nearest value of an E series (on a log scale)."""
    d = 10 ** math.floor(math.log10(x))
    best = min([m * d for m in series] + [10 * d], key=lambda v: abs(math.log(v / x)))
    return float(f"{best:.3g}")


def _snap_up(x: float, series=E12) -> float:
    d = 10 ** math.floor(math.log10(x))
    return float(f"{next(v for v in [m * d for m in series] + [10 * d] if v >= x * 0.999):.3g}")


def fmt(v: float, unit: str) -> str:
    for f, p in ((1e6, "M"), (1e3, "k"), (1, ""), (1e-3, "m"), (1e-6, "µ"), (1e-9, "n")):
        if abs(v) >= f * 0.999:
            return f"{v / f:.3g} {p}{unit}"
    return f"{v:.3g} {unit}"


def build(spec: dict) -> tuple[bb.Board, list[str]]:
    """The board for a lesson's circuit, and notes on how it was scaled. Raises ValueError."""
    comps = spec.get("compartments") or []
    links = spec.get("links") or []
    if not comps:
        raise ValueError("the lesson sent no circuit")
    for c in comps:
        if not c.get("C", 0) > 0:
            raise ValueError("every compartment needs a capacitance")
        c["channels"] = [b for b in c.get("channels", []) if b.get("g", 0) > 0]
    # Each compartment's resting conductance (switches as they are now, synapses shut), for the time constants
    gtot = [sum(b["g"] for b in c["channels"] if b.get("closed", True) and not (b.get("button") or b.get("gate")))
            for c in comps]
    for lk in links:
        gtot[lk["from"]] += lk["g"] * 0.5            # a rough share of the axial path
        gtot[lk["to"]] += lk["g"] * 0.5
    if not all(g > 0 for g in gtot):
        raise ValueError("a compartment has no conductance: its voltage would never settle")
    taus = [c["C"] / g for c, g in zip(comps, gtot)]
    real = spec.get("scale") != "breadboard"
    v_scale = 1.0 if real else V_SCALE

    def snap(x, series=E12):           # real values stay as they are (to 3 figures)
        return float(f"{x:.3g}") if real else _snap(x, series)

    def snap_up(x, series=E12):
        return float(f"{x:.3g}") if real else _snap_up(x, series)
    if real:
        t_scale = r_scale = 1.0
    else:
        # Time: a power of ten taking the slowest time constant to 10-100 ms
        t_scale = 10 ** math.ceil(math.log10(0.01 / max(taus)))
        # Impedance: centre the resistances on R_TARGET, then snap the first capacitor
        rs = [1 / b["g"] for c in comps for b in c["channels"]] + [1 / lk["g"] for lk in links]
        r_scale = R_TARGET / math.exp(sum(math.log(r) for r in rs) / len(rs))     # board Ω per cell Ω
        c_board0 = snap(comps[0]["C"] * t_scale / r_scale, E6)
        r_scale = t_scale * comps[0]["C"] / c_board0            # the first τ comes out exactly

    def ohms(g):
        return 1 / g * r_scale

    parts: list[bb.Part] = []
    notes: list[str] = []
    col = 1
    nodes: list[int] = []
    free_axial: list[list[str]] = []      # each node's holes left for axial resistors
    gate_notes: list[str] = []            # synapses the Stimulator opens
    for ci, c in enumerate(comps):
        stim = c.get("stim")
        nb = len(c["channels"])
        nlinks = sum((lk["from"] == ci) + (lk["to"] == ci) for lk in links)
        ngated = sum(b.get("gate") == "stim" for b in c["channels"])
        width = (1 if stim else 0) + 1 + nb + ngated
        # The node's free holes: branches leave from the upper strip; the probe, the
        # stimulus and axial resistors take what's left, lower strip first.
        upper = [f"{r}{col + bool(stim)}" for r in "ghij"]
        lower = [f"{r}{col + bool(stim)}" for r in "cbd"]
        if nb > len(upper):
            raise ValueError(f"{c.get('name', 'a compartment')} has {nb} branches; the board fits 4")
        others = lower + upper[nb:]
        if 1 + bool(stim) + nlinks > len(others):
            raise ValueError(f"{c.get('name', 'a compartment')} has too many branches and connections for one column")
        if col + width - 1 > bb.COLS:
            raise ValueError("the circuit is too big for the breadboard (fewer compartments, or fewer branches)")
        s = col if stim else 0
        n = col + (1 if stim else 0)
        nodes.append(n)
        parts += [bb.Part("wire", f"e{n}", f"f{n}"),
                  bb.Part("capacitor", f"a{n}", f"t-{n}", snap(c["C"] * t_scale / r_scale, E6)),
                  bb.Part("probe", others.pop(0))]
        stim_hole = others.pop(0) if stim else ""
        free_axial.append(others)
        m = n
        for k, b in enumerate(c["channels"]):
            m, start = m + 1, upper[k]
            row = start[0]
            end = f"{row}{m}"
            r = ohms(b["g"])
            if b.get("vary"):
                gmin = b.get("gmin") or b["g"] / 4
                value = _snap_up(ohms(gmin), (1, 2, 5))       # a pot's usual 1-2-5 values
                pot = bb.Part("pot", start, end, value)
                pot.wiper = f"f{m}"           # a variable resistor: node to wiper (wiper to b is shorted)
                pot.pos = min(1.0, r / value)
                parts.append(pot)
            else:
                parts.append(bb.Part("resistor", start, end, snap(r)))
            across = f"{'i' if row == 'j' else 'j'}{m}"      # joins the branch's two strips
            if b.get("gate") == "stim":
                # A synapse the Stimulator opens: an n-channel switch, its source on the battery side,
                # its gate driven by the Stimulator's output from the next column
                g_col = m + 1
                parts += [bb.Part("switch", across, f"e{m}", 1.0, mode="mosfet", gate=f"f{g_col}"),
                          bb.Part("vin", f"h{g_col}", f"b-{g_col}", 1.0, source="stim")]
                tr, what = b.get("train") or {}, b.get("name", "A synapse")
                if tr.get("width"):
                    n_in = int(tr.get("n", 1))
                    what += (f": {n_in} pulse{'s' if n_in != 1 else ''} of 5 V, {tr['width'] * t_scale * 1e3:.3g} ms long"
                             + (f", {tr['interval'] * t_scale * 1e3:.3g} ms apart" if n_in > 1 and tr.get("interval") else ""))
                gate_notes.append(what)
                m = g_col
            elif b.get("button"):
                parts.append(bb.Part("button", f"e{m}", across, 1.0))
            elif "switch" in b:
                parts.append(bb.Part("switch", f"e{m}", across, 1.0, closed=bool(b.get("closed", True))))
            else:
                parts.append(bb.Part("wire", f"e{m}", across))
            bm = m - 1 if b.get("gate") == "stim" else m      # the branch's own column
            if b.get("E") is None:                            # no battery: straight to ground
                parts.append(bb.Part("wire", f"a{bm}", f"t-{bm}"))
                continue
            e = round(b["E"] * v_scale, 4)
            if abs(e) > V_MAX:
                notes.append(f"{b.get('name', 'a battery')}: {e:+.1f} V is beyond ±{V_MAX:g} V")
            # + end at a: a battery always shows a positive voltage, turned round when negative
            parts.append(bb.Part("battery", f"a{bm}", f"t-{bm}", abs(e)) if e >= 0 else
                         bb.Part("battery", f"t-{bm}", f"a{bm}", abs(e)))
        if stim:
            rin = ohms(sum(b["g"] for b in c["channels"]) or gtot[ci])
            # Ten times the compartment's resistance, so it barely loads it, unless the pulse would then
            # pass V_PULSE: a big current (a spike's stimulus) gets a smaller resistor instead
            i_board = abs(stim.get("I", 0.0)) / r_scale * v_scale        # board volts per board ohm
            rs_board = snap_up(10 * rin)
            if i_board * rs_board > V_PULSE:
                rs_board = snap(V_PULSE / i_board)
                if rs_board < 3 * rin:
                    notes.append(f"{c.get('name', 'The cell')}: the stimulus resistor is only {rs_board / rin:.1f}× the "
                                 "membrane's resistance, so it loads the membrane while the Stimulator is at 0 V")
            v = round(stim.get("I", 0.0) * rs_board / r_scale * v_scale, 4)
            sv = ("+" if v >= 0 else "−") + fmt(abs(v), "V")
            parts.append(bb.Part("resistor", f"j{s}", stim_hole, rs_board))
            if stim.get("dc"):
                parts.append(bb.Part("vin", f"g{s}", f"b-{s}", v, source="dc"))
                notes.append(f"{c.get('name', 'The cell')}: the injected current is a fixed {sv} "
                             f"through {fmt(rs_board, 'Ω')}")
            else:
                parts.append(bb.Part("vin", f"g{s}", f"b-{s}", 1.0, source="stim"))
                long = f", {stim['width'] * t_scale * 1e3:.3g} ms long" if stim.get("width") else ""
                notes.append(f"{c.get('name', 'The cell')}: inject the current as a {sv} pulse{long} "
                             f"on the Stimulator (through {fmt(rs_board, 'Ω')})")
        col += width + 1
    for lk in links:
        a, b = lk["from"], lk["to"]
        parts.append(bb.Part("resistor", free_axial[a].pop(0), free_axial[b].pop(0), snap(ohms(lk["g"]))))
    if gate_notes:
        notes.append("Opened by the Stimulator (5 V opens it): " + "; ".join(gate_notes))
    board = bb.Board(parts)
    bb.validate(board)
    ms = max(taus) * t_scale * 1e3
    notes.insert(0, f"The cell's own values (slowest τ ≈ {ms:.3g} ms)" if real else
                 f"Scaled: time ×{t_scale:g} (slowest τ ≈ {ms:.0f} ms), 1 mV in the cell = 10 mV on the board")
    return board, notes
