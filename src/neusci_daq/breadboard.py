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

The circuit is solved by modified nodal analysis at each sample. Capacitors use
backward Euler (i = C (v - v_prev) / dt), which is stable at any step, so an RC
charges with the right time constant at any sample rate. Every net also has a
tiny conductance to ground, so a part left floating reads 0 V instead of making
the circuit unsolvable. A MOSFET switch is checked once per sample, from the voltages
of the sample before; each pattern of open and closed switches is solved once and kept.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np

COLS = 30
ROWS = "abcdefghij"
RAILS = ("t+", "t-", "b+", "b-")          # top + and -, bottom + and -
KINDS = ("vin", "resistor", "capacitor", "switch", "wire", "probe")
NAMES = {"vin": "Input voltage", "resistor": "Resistor", "capacitor": "Capacitor", "switch": "Switch", "wire": "Wire", "probe": "Probe"}
UNITS = {"vin": "V", "resistor": "Ω", "capacitor": "F", "switch": "Ω"}
DEFAULT_VALUE = {"vin": 1.0, "resistor": 10e3, "capacitor": 1e-6, "switch": 1.0, "wire": 0.0, "probe": 0.0}
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

    def holes(self) -> list[str]:
        """Every hole the part has a leg in."""
        gate = [self.gate] if self.kind == "switch" and self.mode == "mosfet" and self.gate else []
        return [h for h in (self.a, self.b) if h] + gate


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
        if p.kind == "wire":
            ra, rb = find(strip_of(p.a)), find(strip_of(p.b))
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
        if p.kind != "probe":
            parse_hole(p.b)
            if p.a == p.b:
                raise ValueError(f"{NAMES[p.kind]}: both ends are in the same hole")
        if p.kind in ("resistor", "capacitor", "switch") and not p.value > 0:
            raise ValueError(f"{NAMES[p.kind]}: the value must be above 0")
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
        vins = [p for p in board.parts if p.kind == "vin"]
        self.vins = vins
        n, m = len(names), len(vins)
        a = np.zeros((n + m, n + m))
        gc = np.zeros((n + m, n + m))            # capacitor conductances: the history term
        e = np.zeros((n + m, m))                  # how each source's voltage enters
        for i in range(n):
            a[i, i] += G_MIN

        def stamp(mat, p, g):
            i, j = self._idx(p.a), self._idx(p.b)
            if i is not None:
                mat[i, i] += g
            if j is not None:
                mat[j, j] += g
            if i is not None and j is not None:
                mat[i, j] -= g
                mat[j, i] -= g

        self.switches = []                       # (part, index in board.parts, a, b, gate, source)
        for k, p in enumerate(board.parts):
            if p.kind == "switch":
                gate = self._idx(p.gate) if p.mode == "mosfet" else None
                self.switches.append((p, k, self._idx(p.a), self._idx(p.b), gate, self._idx(p.b)))
        for p in board.parts:
            if p.kind == "resistor":
                stamp(a, p, 1.0 / p.value)
            elif p.kind == "capacitor":
                stamp(a, p, p.value / self.dt)
                stamp(gc, p, p.value / self.dt)
        for k, p in enumerate(vins):
            r, i, j = n + k, self._idx(p.a), self._idx(p.b)
            if i is not None:
                a[i, r] += 1
                a[r, i] += 1
            if j is not None:
                a[j, r] -= 1
                a[r, j] -= 1
            e[r, k] = 1.0
        self._a, self._gc, self._e, self._stamp = a, gc, e, stamp
        self._mats: dict[tuple, tuple[np.ndarray, np.ndarray]] = {}
        self.v = np.zeros(n + m)
        prev_v, prev_node = old
        if prev_v is not None:                   # carry charge over on nets that survive
            for name, i in self.node.items():
                if name in prev_node:
                    self.v[i] = prev_v[prev_node[name]]
        self.key = self._key(self.v)
        self.p, self.q = self._solve(self.key)

    def _key(self, v: np.ndarray) -> tuple:
        """Which switches are closed, given node voltages `v`."""
        out = []
        for p, _k, _a, _b, g, s in self.switches:
            if p.mode == "toggle":
                out.append(bool(p.closed))
            else:
                vgs = (0.0 if g is None else v[g]) - (0.0 if s is None else v[s])
                out.append(bool(vgs > p.vth if p.channel == "n" else vgs < -p.vth))
        return tuple(out)

    def _solve(self, key: tuple) -> tuple[np.ndarray, np.ndarray]:
        """The step matrices with the switches in `key` closed (kept for next time)."""
        if key not in self._mats:
            a = self._a.copy()
            for (p, *_), on in zip(self.switches, key):
                if on:
                    self._stamp(a, p, 1.0 / p.value)
            try:
                ainv = np.linalg.inv(a)
            except np.linalg.LinAlgError:
                raise ValueError("The circuit can't be solved: an input voltage is shorted, "
                                 "or two input voltages are joined") from None
            self._mats[key] = (ainv @ self._gc, ainv @ self._e)
        return self._mats[key]

    def closed_now(self) -> dict[int, bool]:
        """Each switch's state after the last sample, by its index in board.parts."""
        return {k: on for (_p, k, *_), on in zip(self.switches, self.key)}

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
        u = np.empty((len(stim), len(self.vins)))
        for k, p in enumerate(self.vins):
            u[:, k] = stim if p.source == "stim" else p.value
        cols = [self._known(h) for h in holes]
        out = np.zeros((len(holes), len(stim)))
        v, p, q, key = self.v, self.p, self.q, self.key
        gated = any(sw[0].mode == "mosfet" for sw in self.switches)
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
        if p.kind in ("vin", "resistor", "capacitor", "switch"):
            lines.append(f"value = {p.value:g}")
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
                          float(d.get("vth", 2.0)), str(d.get("channel", "n"))))
    board = Board(parts)
    validate(board)
    return board


def board_from_dict(d: dict) -> Board:
    """A board from asdict(board), as saved in a .newt file."""
    return Board([Part(**p) for p in d.get("parts", [])])


def copy_board(board: Board) -> Board:
    return board_from_dict(asdict(board))
