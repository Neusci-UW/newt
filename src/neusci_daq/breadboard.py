"""The Breadboard simulator: parts pushed into a breadboard, solved as a circuit, with
probes that the simulated backend records like any input (see the Breadboard
simulator page, and backends.SimulatedBackend).

The board has COLS columns. In each column, holes a-e are one strip and f-j another
(the channel down the middle separates them). Along the top and bottom run power
rails: both "-" rails are ground (0 V), and both "+" rails are one net, so a part
can reach either from either edge.

Parts sit between two holes (a probe in one):

- input voltage: + end at `a`, - end at `b`; either the stimulator's output (the
  waveform played on the Stimulator, in V) or a fixed `value` in V
- resistor: `value` ohms
- capacitor: `value` farads
- wire: joins two holes (two strips become one net)
- switch: `value` ohms when closed, no connection when open. `mode` "toggle": flipped
  by hand (`closed`). `mode` "mosfet": `a` is the drain, `b` the source and `gate` a
  third hole; it closes while the gate is more than `vth` above the source ("n"
  channel) or below it ("p"). No current flows into the gate.
- probe: records the voltage of its hole's net on input `input` ("" = not recorded)
- ground: one hole, tied to 0 V (like a wire to a - rail)
- battery: a fixed `value` in V, + end at `a`
- diode, LED: current flows from `a` (anode) to `b` (cathode) only. Below the forward
  voltage `vf` no current flows; above it the part is `vf` in series with a small
  resistance (R_DIODE, R_LED). Checked once per sample, like a MOSFET switch. An LED
  has a `color`, which sets its `vf`, and `value` is its maximum current in A.
- push button: `value` ohms while held down (`closed`), no connection otherwise
- potentiometer: `value` ohms from `a` to `b`, with a wiper leg at `wiper`, `pos` of
  the way from `a` (0 to 1)
- inductor: `value` henries

The circuit is solved by modified nodal analysis at each sample. Capacitors use
backward Euler (i = C (v - v_prev) / dt), which is stable at any step, so an RC
charges with the right time constant at any sample rate; so do inductors, whose
currents are unknowns of their own (v = L (i - i_prev) / dt). Every net also has a
tiny conductance to ground, so a part left floating reads 0 V instead of making
the circuit unsolvable. A MOSFET switch, diode or LED is checked once per sample, from the
voltages of the sample before; each pattern of open and closed ones is solved once and kept.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np

COLS = 30
ROWS = "abcdefghij"
RAILS = ("t+", "t-", "b+", "b-")          # top + and -, bottom + and -
KINDS = ("vin", "resistor", "capacitor", "switch", "wire", "probe", "ground",
         "led", "diode", "button", "pot", "inductor", "battery")
NAMES = {"vin": "Input voltage", "resistor": "Resistor", "capacitor": "Capacitor", "switch": "Switch", "wire": "Wire",
         "probe": "Probe", "ground": "Ground", "led": "LED", "diode": "Diode", "button": "Push button",
         "pot": "Potentiometer", "inductor": "Inductor", "battery": "Battery"}
UNITS = {"vin": "V", "resistor": "Ω", "capacitor": "F", "switch": "Ω", "button": "Ω", "pot": "Ω", "inductor": "H",
         "battery": "V", "led": "A"}
DEFAULT_VALUE = {"vin": 1.0, "resistor": 10e3, "capacitor": 1e-6, "switch": 1.0, "wire": 0.0, "probe": 0.0,
                 "ground": 0.0, "led": 0.02, "diode": 0.0, "button": 1.0, "pot": 10e3, "inductor": 10e-3, "battery": 5.0}
ONE_HOLE = ("probe", "ground")
LED_VF = {"red": 2.0, "yellow": 2.1, "green": 2.2, "blue": 3.0, "white": 3.1}   # forward voltage by colour, V
DIODE_VF = 0.7
R_LED, R_DIODE = 15.0, 1.0                  # Ω, in series with vf while conducting
R_MIN = 1.0                                 # Ω, the least a potentiometer's half can be
GND = "gnd"
G_MIN = 1e-9                              # S from every net to ground


@dataclass
class Part:
    kind: str                             # one of KINDS
    a: str                                # hole: "a12" (row, column) or "t-12" (rail, column)
    b: str = ""                           # second hole; "" for a probe
    value: float = 0.0                    # V (fixed input), Ω (resistor; switch when closed), F
    source: str = "stim"                  # input voltage: "stim" (the stimulator's output) or "dc"
    input: str = ""                       # probe: the input it records on, "" = none
    noise_v: float = 0.002                # probe: recording noise (RMS)
    mode: str = "toggle"                  # switch: "toggle" (by hand) or "mosfet" (by its gate's voltage)
    closed: bool = False                  # switch, toggle: closed now
    gate: str = ""                        # switch, mosfet: the gate's hole (a = drain, b = source)
    vth: float = 2.0                      # switch, mosfet: threshold, V
    channel: str = "n"                    # switch, mosfet: "n" (on above vth) or "p" (on below -vth)
    vf: float = 0.0                       # diode, LED: forward voltage, V
    color: str = "red"                    # LED
    wiper: str = ""                       # potentiometer: the wiper's hole
    pos: float = 0.5                      # potentiometer: the wiper's place, 0 (at a) to 1 (at b)

    def holes(self) -> list[str]:
        """Every hole the part has a leg in."""
        gate = [self.gate] if self.kind == "switch" and self.mode == "mosfet" and self.gate else []
        wiper = [self.wiper] if self.kind == "pot" and self.wiper else []
        return [h for h in (self.a, self.b) if h] + gate + wiper


def new_part(kind: str, a: str, b: str = "") -> Part:
    """A part with its default settings."""
    p = Part(kind, a, "" if kind in ONE_HOLE else b, DEFAULT_VALUE[kind])
    if kind == "led":
        p.vf = LED_VF[p.color]
    elif kind == "diode":
        p.vf = DIODE_VF
    return p


def is_source(p: Part) -> bool:
    """An input voltage or a battery: a voltage source in the circuit."""
    return p.kind in ("vin", "battery")


def is_diode(p: Part) -> bool:
    return p.kind in ("diode", "led")


@dataclass
class Board:
    parts: list = field(default_factory=list)

    def probes(self) -> list[Part]:
        return [p for p in self.parts if p.kind == "probe"]

    def probe_on(self, physical: str) -> Part | None:
        """The probe recording on input `physical`, if any."""
        return next((p for p in self.probes() if p.input and p.input == physical), None)


def default_board() -> Board:
    """An RC low-pass: the stimulator's output through 10 kΩ into 1 µF to ground,
    probes on the input and on the capacitor (not yet given an input)."""
    return Board([
        Part("vin", "a3", "t-3", 1.0),
        Part("resistor", "c3", "c11", 10e3),
        Part("capacitor", "a11", "t-11", 1e-6),
        Part("probe", "e3", ""),
        Part("probe", "e11", ""),
    ])


# ---------------------------------------------------------------- holes and nets
def parse_hole(h: str) -> tuple[str, int]:
    """("a", 12) for "a12", ("t-", 12) for "t-12". Raises ValueError."""
    for rail in RAILS:
        if h.startswith(rail):
            row, num = rail, h[len(rail):]
            break
    else:
        row, num = h[:1], h[1:]
        if row not in ROWS:
            raise ValueError(f"no hole {h!r}")
    if not num.isdigit() or not 1 <= int(num) <= COLS:
        raise ValueError(f"no hole {h!r}")
    return row, int(num)


def strip_of(h: str) -> str:
    """The strip a hole belongs to, before wires: the holes it's joined to inside the board."""
    row, col = parse_hole(h)
    if row.endswith("-"):
        return GND
    if row.endswith("+"):
        return "plus"
    return f"{'L' if row in 'abcde' else 'U'}{col}"


def nets(board: Board) -> dict[str, str]:
    """Each strip used on the board -> its net, after wires join strips."""
    parent: dict[str, str] = {}

    def find(s: str) -> str:
        parent.setdefault(s, s)
        while parent[s] != s:
            parent[s] = parent[parent[s]]
            s = parent[s]
        return s

    for p in board.parts:
        for h in p.holes():
            find(strip_of(h))
    for p in board.parts:
        if p.kind in ("wire", "ground"):
            ra, rb = find(strip_of(p.a)), find(GND if p.kind == "ground" else strip_of(p.b))
            if ra != rb:
                if rb == GND:                 # keep ground's name for the joined net
                    ra, rb = rb, ra
                parent[rb] = ra
    return {s: find(s) for s in parent}


def validate(board: Board) -> None:
    for p in board.parts:
        if p.kind not in KINDS:
            raise ValueError(f"unknown part {p.kind!r}")
        parse_hole(p.a)
        if p.kind not in ONE_HOLE:
            parse_hole(p.b)
            if p.a == p.b:
                raise ValueError(f"{NAMES[p.kind]}: both ends are in the same hole")
        if p.kind in ("resistor", "capacitor", "switch", "button", "pot", "inductor", "led") and not p.value > 0:
            raise ValueError(f"{NAMES[p.kind]}: the value must be above 0")
        if is_diode(p) and not p.vf > 0:
            raise ValueError(f"{NAMES[p.kind]}: the forward voltage must be above 0")
        if p.kind == "led" and p.color not in LED_VF:
            raise ValueError(f"LED: unknown colour {p.color!r}")
        if p.kind == "pot":
            parse_hole(p.wiper)
            if p.wiper in (p.a, p.b):
                raise ValueError("Potentiometer: the wiper shares a hole with an end")
            if not 0 <= p.pos <= 1:
                raise ValueError("Potentiometer: the wiper must be between its ends")
        if p.kind == "vin" and p.source not in ("stim", "dc"):
            raise ValueError("Input voltage: the source must be the stimulator or fixed")
        if p.kind == "switch":
            if p.mode not in ("toggle", "mosfet") or p.channel not in ("n", "p"):
                raise ValueError("Switch: unknown mode or channel")
            if p.mode == "mosfet":
                parse_hole(p.gate)
                if p.gate in (p.a, p.b):
                    raise ValueError("Switch: the gate shares a hole with another leg")
    inputs = [p.input for p in board.probes() if p.input]
    if len(inputs) != len(set(inputs)):
        raise ValueError("Two probes record on the same input")


# ---------------------------------------------------------------- solving
class BoardState:
    """The circuit, stepped one sample at a time; keeps the capacitors' voltages
    between calls."""

    def __init__(self, board: Board, dt: float):
        self.dt = dt
        self.configure(board)

    def configure(self, board: Board) -> None:
        """Rebuild for a changed board. Capacitors on nets that still exist keep their charge."""
        old = getattr(self, "v", None), getattr(self, "node", {})
        self.board = board
        net_of = nets(board)
        self.net_of = net_of
        names = sorted({n for n in net_of.values() if n != GND})
        self.node = {n: i for i, n in enumerate(names)}
        vins = [p for p in board.parts if is_source(p)]
        inds = [p for p in board.parts if p.kind == "inductor"]
        self.vins = vins
        self.diodes = [(p, k, self._idx(p.a), self._idx(p.b)) for k, p in enumerate(board.parts) if is_diode(p)]
        n, m, nl, nd = len(names), len(vins), len(inds), len(self.diodes)
        size = n + m + nl                        # node voltages, then source currents, then inductor currents
        a = np.zeros((size, size))
        gc = np.zeros((size, size))              # the history term: capacitors' voltages, inductors' currents
        e = np.zeros((size, m + nd))             # how each source's voltage (and each diode's vf) enters
        for i in range(n):
            a[i, i] += G_MIN

        def stamp_ij(mat, i, j, g):
            if i is not None:
                mat[i, i] += g
            if j is not None:
                mat[j, j] += g
            if i is not None and j is not None:
                mat[i, j] -= g
                mat[j, i] -= g

        def stamp(mat, p, g):
            stamp_ij(mat, self._idx(p.a), self._idx(p.b), g)

        self.switches = []                       # (part, index in board.parts, a, b, gate, source)
        for k, p in enumerate(board.parts):
            if p.kind in ("switch", "button"):
                gate = self._idx(p.gate) if p.kind == "switch" and p.mode == "mosfet" else None
                self.switches.append((p, k, self._idx(p.a), self._idx(p.b), gate, self._idx(p.b)))
        for p in board.parts:
            if p.kind == "resistor":
                stamp(a, p, 1.0 / p.value)
            elif p.kind == "capacitor":
                stamp(a, p, p.value / self.dt)
                stamp(gc, p, p.value / self.dt)
            elif p.kind == "pot":
                w = self._idx(p.wiper)
                stamp_ij(a, self._idx(p.a), w, 1.0 / max(R_MIN, p.value * p.pos))
                stamp_ij(a, w, self._idx(p.b), 1.0 / max(R_MIN, p.value * (1 - p.pos)))
        for k, p in enumerate(vins):
            r, i, j = n + k, self._idx(p.a), self._idx(p.b)
            if i is not None:
                a[i, r] += 1
                a[r, i] += 1
            if j is not None:
                a[j, r] -= 1
                a[r, j] -= 1
            e[r, k] = 1.0
        for k, p in enumerate(inds):             # v_a - v_b = L (i - i_prev) / dt; i flows a -> b
            r, i, j, ldt = n + m + k, self._idx(p.a), self._idx(p.b), p.value / self.dt
            if i is not None:
                a[i, r] += 1
                a[r, i] += 1
            if j is not None:
                a[j, r] -= 1
                a[r, j] -= 1
            a[r, r] -= ldt
            gc[r, r] -= ldt
        self._a, self._gc, self._e, self._stamp, self._stamp_ij, self._m = a, gc, e, stamp, stamp_ij, m
        self._mats: dict[tuple, tuple[np.ndarray, np.ndarray]] = {}
        self.v = np.zeros(size)
        prev_v, prev_node = old
        if prev_v is not None:                   # carry charge over on nets that survive
            for name, i in self.node.items():
                if name in prev_node:
                    self.v[i] = prev_v[prev_node[name]]
        self.key = self._key(self.v)
        self.p, self.q = self._solve(self.key)

    def _key(self, v: np.ndarray) -> tuple:
        """Which switches are closed and which diodes conduct, given node voltages `v`."""
        out = []
        for p, _k, _a, _b, g, s in self.switches:
            if p.kind == "button" or p.mode == "toggle":
                out.append(bool(p.closed))
            else:
                vgs = (0.0 if g is None else v[g]) - (0.0 if s is None else v[s])
                out.append(bool(vgs > p.vth if p.channel == "n" else vgs < -p.vth))
        for p, _k, i, j in self.diodes:          # conducting while the anode is more than vf above the cathode
            vab = (0.0 if i is None else v[i]) - (0.0 if j is None else v[j])
            out.append(bool(vab > p.vf))
        return tuple(out)

    def _solve(self, key: tuple) -> tuple[np.ndarray, np.ndarray]:
        """The step matrices with the switches and diodes in `key` on (kept for next time)."""
        if key not in self._mats:
            a, e = self._a.copy(), self._e.copy()
            ns = len(self.switches)
            for (p, *_), on in zip(self.switches, key[:ns]):
                if on:
                    self._stamp(a, p, 1.0 / p.value)
            for d, ((p, _k, i, j), on) in enumerate(zip(self.diodes, key[ns:])):
                if on:                           # g (v_a - v_b - vf): a conductance, and vf as a source
                    g = 1.0 / (R_LED if p.kind == "led" else R_DIODE)
                    self._stamp_ij(a, i, j, g)
                    if i is not None:
                        e[i, self._m + d] += g
                    if j is not None:
                        e[j, self._m + d] -= g
            try:
                ainv = np.linalg.inv(a)
            except np.linalg.LinAlgError:
                raise ValueError("The circuit can't be solved: an input voltage or battery is shorted, "
                                 "or two of them are joined") from None
            self._mats[key] = (ainv @ self._gc, ainv @ e)
        return self._mats[key]

    def diode_current(self, k: int) -> float:
        """The current through the diode or LED at board.parts[k], anode to cathode, A (after the last sample)."""
        for d, (p, kk, i, j) in enumerate(self.diodes):
            if kk == k:
                if not self.key[len(self.switches) + d]:
                    return 0.0
                vab = (0.0 if i is None else self.v[i]) - (0.0 if j is None else self.v[j])
                return max(0.0, (vab - p.vf) / (R_LED if p.kind == "led" else R_DIODE))
        return 0.0

    def closed_now(self) -> dict[int, bool]:
        """Each switch's state after the last sample, by its index in board.parts."""
        return {k: on for (_p, k, *_), on in zip(self.switches, self.key)}

    def lit_now(self) -> dict[int, float]:
        """Each LED's current after the last sample, A, by its index in board.parts."""
        return {k: self.diode_current(k) for p, k, *_ in self.diodes if p.kind == "led"}

    def _idx(self, hole: str) -> int | None:
        net = self.net_of[strip_of(hole)]
        return None if net == GND else self.node[net]

    def voltage(self, hole: str) -> float:
        net = self.net_of.get(strip_of(hole))
        if net is None or net == GND:
            return 0.0
        return float(self.v[self.node[net]])

    def run(self, stim: np.ndarray, holes: list[str]) -> np.ndarray:
        """Step one sample per value of `stim` (the stimulator's output, V). Returns the
        voltage at each of `holes`, one row per hole."""
        u = np.empty((len(stim), len(self.vins) + len(self.diodes)))
        for k, p in enumerate(self.vins):
            u[:, k] = stim if p.kind == "vin" and p.source == "stim" else p.value
        for d, (p, *_) in enumerate(self.diodes):
            u[:, len(self.vins) + d] = p.vf
        cols = [self._known(h) for h in holes]
        out = np.zeros((len(holes), len(stim)))
        v, p, q, key = self.v, self.p, self.q, self.key
        gated = bool(self.diodes) or any(sw[0].kind == "switch" and sw[0].mode == "mosfet" for sw in self.switches)
        for s in range(len(stim)):
            if gated:
                k = self._key(v)
                if k != key:
                    key = k
                    p, q = self._solve(key)
            v = p @ v + q @ u[s]
            for r, c in enumerate(cols):
                if c is not None:
                    out[r, s] = v[c]
        self.v, self.p, self.q, self.key = v, p, q, key
        return out

    def _known(self, hole: str) -> int | None:
        net = self.net_of.get(strip_of(hole))
        return None if net is None or net == GND else self.node[net]


FLOATING_TAU = 100.0                      # s; slower than this, a capacitor has no real path to discharge


def time_constants(board: Board) -> list[float]:
    """The circuit's time constants in s, slowest first: one per way its capacitors can
    charge (for one R and one C, just R·C). Capacitors held only by the tiny leak to
    ground (no resistor path) are left out. Raises ValueError if it can't be solved."""
    dt = 1e-6
    state = BoardState(board, dt)
    lam = np.linalg.eigvals(state.p).real if len(state.p) else np.zeros(0)
    lam = lam[(lam > 1e-9) & (lam < 1 - 1e-12)]
    # backward Euler: each mode is multiplied by 1 / (1 + dt/τ) per step
    taus = dt * lam / (1 - lam)
    return sorted((float(t) for t in taus if t < FLOATING_TAU), reverse=True)


def preview(board: Board, fs: float = 10_000.0, pulse_s: float = 0.1, total_s: float = 0.25) -> tuple[np.ndarray, np.ndarray, list[np.ndarray]]:
    """What the probes would show for a 1 V pulse from the stimulator (from 20 ms, `pulse_s`
    long), starting from rest: (times s, stimulus, [trace per probe])."""
    t = np.arange(int(total_s * fs)) / fs
    stim = ((t >= 0.02) & (t < 0.02 + pulse_s)).astype(float)
    state = BoardState(board, 1.0 / fs)
    probes = board.probes()
    traces = state.run(stim, [p.a for p in probes]) if probes else np.zeros((0, len(t)))
    return t, stim, list(traces)


# ---------------------------------------------------------------- saving
def to_toml_lines(board: Board) -> list[str]:
    lines = []
    for p in board.parts:
        lines += ["", "[[simulated.breadboard]]", f'part = "{p.kind}"', f'a = "{p.a}"']
        if p.b:
            lines.append(f'b = "{p.b}"')
        if p.kind in ("vin", "resistor", "capacitor", "switch", "button", "pot", "inductor", "battery", "led"):
            lines.append(f"value = {p.value:g}")
        if is_diode(p):
            lines.append(f"vf = {p.vf:g}")
        if p.kind == "led":
            lines.append(f'color = "{p.color}"')
        if p.kind == "pot":
            lines += [f'wiper = "{p.wiper}"', f"pos = {p.pos:g}"]
        if p.kind == "switch":
            lines.append(f'mode = "{p.mode}"')
            if p.mode == "toggle":
                lines.append(f"closed = {'true' if p.closed else 'false'}")
            else:
                lines += [f'gate = "{p.gate}"', f"vth = {p.vth:g}", f'channel = "{p.channel}"']
        if p.kind == "vin":
            lines.append(f'source = "{p.source}"')
        if p.kind == "probe":
            lines += [f'input = "{p.input}"', f"noise_v = {p.noise_v:g}"]
    return lines


def from_toml(sim: dict) -> Board:
    raw = sim.get("breadboard")
    if raw is None:
        return default_board()
    parts = []
    for d in raw:
        kind = d.get("part", "")
        parts.append(Part(kind, str(d.get("a", "")), str(d.get("b", "")), float(d.get("value", DEFAULT_VALUE.get(kind, 0.0))),
                          str(d.get("source", "stim")), str(d.get("input", "")), float(d.get("noise_v", 0.002)),
                          str(d.get("mode", "toggle")), bool(d.get("closed", False)), str(d.get("gate", "")),
                          float(d.get("vth", 2.0)), str(d.get("channel", "n")),
                          vf=float(d.get("vf", LED_VF.get(str(d.get("color", "red")), 2.0) if kind == "led" else DIODE_VF if kind == "diode" else 0.0)),
                          color=str(d.get("color", "red")), wiper=str(d.get("wiper", "")), pos=float(d.get("pos", 0.5))))
    board = Board(parts)
    validate(board)
    return board


def board_from_dict(d: dict) -> Board:
    """A board from asdict(board), as saved in a .newt file."""
    return Board([Part(**p) for p in d.get("parts", [])])


def copy_board(board: Board) -> Board:
    return board_from_dict(asdict(board))
