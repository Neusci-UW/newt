"""Digital filters and channel arithmetic: calculated channels, like LabChart's channel
calculations. A calculated channel is a formula over the recorded channels:

    bandpass(Ch2, 300, 3000)          Ch1 - Ch2            2 * Ch1 + 0.5
    rms(highpass(Ch2, 100), 10)       derivative(smooth(Ch1, 1))
    lowpass("Channel 1", 100)         (a channel name with spaces goes in quotes)

Functions (frequencies in Hz, times in ms):
    lowpass(x, hz)  highpass(x, hz)  bandpass(x, lo, hi)  bandstop(x, lo, hi)  [order=4]
    notch(x, hz[, q=30])        mains hum: notch(x, 60) or notch(x, 50)
    smooth(x, ms)               moving average
    rms(x, ms)                  moving root-mean-square (an envelope)
    envelope(x, ms)             moving average of |x|
    derivative(x)               per second          integral(x)   × seconds, from the start
    abs sqrt exp log log10 sin cos tan sign  clip(x, lo, hi)  max(a, b)  min(a, b)
    + - * / ** and brackets

The recorded data is never changed: calculations are worked out for display and
analysis. Two ways, giving the same result apart from timing:
- offline, on a whole recorded block: filters run forwards and backwards (zero
  phase, no delay) and smooth/rms/envelope are centred;
- streaming, live, a chunk at a time: filters run forwards only (a short delay, as
  any live filter has) and moving windows trail; each keeps its state between chunks.
The formula is parsed into a small tree (no eval): only numbers, channel names, the
operators and the functions above are allowed.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass

import numpy as np
from scipy import signal


@dataclass
class Calculation:
    """A calculated channel: its name, formula and unit (for the axis)."""
    name: str
    formula: str
    unit: str = ""


def _key(name: str) -> str:
    """How channel names are matched in formulas: case, spaces, _ and - don't matter."""
    return re.sub(r"[\s_\-]", "", name).lower()


# -- the functions ---------------------------------------------------------------
_POINTWISE = {
    "abs": np.abs, "sqrt": lambda x: np.sqrt(np.maximum(x, 0)), "exp": np.exp,
    "log": lambda x: np.log(np.maximum(x, 1e-300)), "log10": lambda x: np.log10(np.maximum(x, 1e-300)),
    "sin": np.sin, "cos": np.cos, "tan": np.tan, "sign": np.sign,
}
_FILTERS = {"lowpass": (1, "lowpass"), "highpass": (1, "highpass"), "bandpass": (2, "bandpass"),
            "bandstop": (2, "bandstop")}
_WINDOWED = {"smooth", "rms", "envelope"}
FUNCTIONS = sorted(list(_POINTWISE) + list(_FILTERS) + list(_WINDOWED) +
                   ["notch", "derivative", "integral", "clip", "max", "min"])


def design(kind: str, freqs: list[float], fs: float, order: int = 4, q: float = 30.0) -> np.ndarray:
    """Second-order sections for a Butterworth filter (or a notch). Raises ValueError."""
    nyq = fs / 2
    for f in freqs:
        if not 0 < f < nyq:
            raise ValueError(f"{f:g} Hz is outside what a {fs:,.0f} Hz recording can show (0 to {nyq:,.0f} Hz).")
    if kind == "notch":
        b, a = signal.iirnotch(freqs[0], q, fs)
        return signal.tf2sos(b, a)
    if kind in ("bandpass", "bandstop") and not freqs[0] < freqs[1]:
        raise ValueError(f"{kind}: the low frequency must be below the high one.")
    if not 1 <= order <= 10:
        raise ValueError("Filter order must be 1 to 10.")
    return signal.butter(order, freqs if len(freqs) > 1 else freqs[0], kind, fs=fs, output="sos")


def _filter_offline(sos: np.ndarray, x: np.ndarray, settle: int = 0) -> np.ndarray:
    """Forwards and backwards (no delay). The ends are padded by `settle` samples (about
    three times how long the filter takes to settle), so a block's edges don't wobble:
    a narrow notch rings for a fraction of a second, longer than scipy's default pad."""
    pad = min(len(x) - 1, max(settle, 3 * (2 * len(sos) + 1)))
    try:
        return signal.sosfiltfilt(sos, x, padlen=pad)
    except ValueError:                       # too short for forwards-and-backwards
        return signal.sosfilt(sos, x)


def _moving_mean(x: np.ndarray, n: int, centred: bool, tail: np.ndarray | None = None):
    """Mean of each n samples: centred (offline), or the n ending at each sample, using
    `tail` (the previous chunk's last n-1 samples) to carry on from a previous chunk."""
    if n <= 1:
        return x.copy(), np.empty(0)
    if centred:
        k = np.ones(n) / n
        pad = np.pad(x, (n // 2, n - 1 - n // 2), mode="edge")
        return np.convolve(pad, k, mode="valid"), None
    head = tail if tail is not None and len(tail) == n - 1 else np.full(n - 1, x[0] if len(x) else 0.0)
    full = np.concatenate([head, x])
    c = np.cumsum(np.insert(full, 0, 0.0))
    return (c[n:] - c[:-n]) / n, full[-(n - 1):]


# -- the tree ---------------------------------------------------------------------
class _Node:
    def run(self, env: dict, fs: float, state: dict | None):
        """Evaluate: env maps channel names to arrays; state is None offline, else a dict
        kept between chunks (each node keeps its own entry)."""
        raise NotImplementedError


class _Const(_Node):
    def __init__(self, v):
        self.v = float(v)

    def run(self, env, fs, state):
        return self.v


class _Chan(_Node):
    def __init__(self, name):
        self.name = name

    def run(self, env, fs, state):
        return np.asarray(env[self.name], float)


class _Bin(_Node):
    OPS = {ast.Add: np.add, ast.Sub: np.subtract, ast.Mult: np.multiply, ast.Div: np.divide, ast.Pow: np.power}

    def __init__(self, op, a, b):
        self.op, self.a, self.b = self.OPS[type(op)], a, b

    def run(self, env, fs, state):
        with np.errstate(all="ignore"):
            return self.op(self.a.run(env, fs, state), self.b.run(env, fs, state))


class _Neg(_Node):
    def __init__(self, a):
        self.a = a

    def run(self, env, fs, state):
        return -self.a.run(env, fs, state)


class _Call(_Node):
    def __init__(self, name, args: list[_Node], consts: list[float]):
        self.name, self.args, self.consts = name, args, consts
        self._sos = None             # designed once per sample rate

    def _get_sos(self, fs):
        if self._sos is None or self._sos[0] != fs:
            name, c = self.name, self.consts
            if name == "notch":
                sos = design("notch", [c[0]], fs, q=c[1] if len(c) > 1 else 30.0)
            else:
                n_freq, kind = _FILTERS[name]
                order = int(c[n_freq]) if len(c) > n_freq else 4
                sos = design(kind, c[:n_freq], fs, order)
            self._sos = (fs, sos)
        return self._sos[1]

    def _settle(self, fs) -> int:
        """Samples for three settling times: a notch ~Q/(π f), a Butterworth ~order/(2π f)."""
        c = self.consts
        if self.name == "notch":
            tau = (c[1] if len(c) > 1 else 30.0) / (np.pi * c[0])
        else:
            n_freq = _FILTERS[self.name][0]
            order = c[n_freq] if len(c) > n_freq else 4
            tau = order / (2 * np.pi * min(c[:n_freq]))
        return int(3 * tau * fs)

    def run(self, env, fs, state):
        name = self.name
        vals = [a.run(env, fs, state) for a in self.args]
        x = vals[0] if vals else None
        mine = None if state is None else state.setdefault(id(self), {})
        if name in _POINTWISE:
            with np.errstate(all="ignore"):
                return _POINTWISE[name](x)
        if name in ("max", "min"):
            return (np.maximum if name == "max" else np.minimum)(vals[0], vals[1])
        if name == "clip":
            return np.clip(x, self.consts[0], self.consts[1])
        x = np.asarray(x, float) * np.ones(1)           # a constant here acts as a flat trace
        if name in _FILTERS or name == "notch":
            sos = self._get_sos(fs)
            if mine is None:
                return _filter_offline(sos, x, self._settle(fs))
            if "zi" not in mine:
                mine["zi"] = signal.sosfilt_zi(sos) * (x[0] if len(x) else 0.0)
            y, mine["zi"] = signal.sosfilt(sos, x, zi=mine["zi"])
            return y
        if name in _WINDOWED:
            n = max(1, int(round(self.consts[0] / 1000 * fs)))
            src = x * x if name == "rms" else np.abs(x) if name == "envelope" else x
            m, tail = _moving_mean(src, n, mine is None, None if mine is None else mine.get("tail"))
            if mine is not None:
                mine["tail"] = tail
            return np.sqrt(np.maximum(m, 0)) if name == "rms" else m
        if name == "derivative":
            if mine is None:
                return np.gradient(x, 1 / fs) if len(x) > 1 else np.zeros_like(x)
            prev = mine.get("prev", x[0] if len(x) else 0.0)
            d = np.diff(np.concatenate([[prev], x])) * fs
            if len(x):
                mine["prev"] = x[-1]
            return d
        if name == "integral":
            start = 0.0 if mine is None else mine.get("sum", 0.0)
            out = start + np.cumsum(x) / fs
            if mine is not None and len(out):
                mine["sum"] = out[-1]
            return out
        raise ValueError(f"Unknown function {name}")


# -- parsing --------------------------------------------------------------------------
# name: (number of trace arguments, numbers needed, numbers allowed)
_SIGNATURES = {**{f: (1, 0, 0) for f in _POINTWISE},
               "lowpass": (1, 1, 2), "highpass": (1, 1, 2), "bandpass": (1, 2, 3), "bandstop": (1, 2, 3),
               "notch": (1, 1, 2), "smooth": (1, 1, 1), "rms": (1, 1, 1), "envelope": (1, 1, 1),
               "derivative": (1, 0, 0), "integral": (1, 0, 0), "clip": (1, 2, 2), "max": (2, 0, 0),
               "min": (2, 0, 0)}
_USAGE = {"lowpass": "lowpass(x, Hz)", "highpass": "highpass(x, Hz)", "bandpass": "bandpass(x, low Hz, high Hz)",
          "bandstop": "bandstop(x, low Hz, high Hz)", "notch": "notch(x, Hz)", "smooth": "smooth(x, ms)",
          "rms": "rms(x, ms)", "envelope": "envelope(x, ms)", "clip": "clip(x, low, high)",
          "max": "max(a, b)", "min": "min(a, b)"}


class Formula:
    """A parsed calculated-channel formula over `channels` (their names)."""

    def __init__(self, text: str, channels: list[str]):
        self.text = text.strip()
        if not self.text:
            raise ValueError("The formula is empty.")
        self._names = {_key(c): c for c in channels}
        self.refs: set[str] = set()
        try:
            tree = ast.parse(self.text, mode="eval")
        except SyntaxError as e:
            raise ValueError(f"Can't read the formula: {e.msg} (at character {e.offset}).") from None
        self.root = self._build(tree.body)
        if not self.refs:
            raise ValueError("The formula doesn't use any channel.")
        self.state: dict = {}

    def _channel(self, name: str) -> _Chan:
        real = self._names.get(_key(name))
        if real is None:
            have = ", ".join(sorted(self._names.values())) or "none"
            raise ValueError(f"There's no channel called {name!r} (channels: {have}).")
        self.refs.add(real)
        return _Chan(real)

    def _number(self, node, what: str) -> float:
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -self._number(node.operand, what)
        raise ValueError(f"{what} must be a number.")

    def _build(self, node) -> _Node:
        if isinstance(node, ast.Constant):
            if isinstance(node.value, str):
                return self._channel(node.value)         # "Channel 1"
            if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
                return _Const(node.value)
        if isinstance(node, ast.Name):
            if node.id in _SIGNATURES:
                raise ValueError(f"{node.id} is a function: write {_USAGE.get(node.id, node.id + '(x)')}.")
            return self._channel(node.id)
        if isinstance(node, ast.BinOp) and type(node.op) in _Bin.OPS:
            return _Bin(node.op, self._build(node.left), self._build(node.right))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            inner = self._build(node.operand)
            return _Neg(inner) if isinstance(node.op, ast.USub) else inner
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            name = node.func.id
            if name not in _SIGNATURES:
                raise ValueError(f"Unknown function {name}(). Available: {', '.join(FUNCTIONS)}.")
            if node.keywords:
                raise ValueError(f"{name}(): give the values in order, e.g. {_USAGE.get(name, name + '(x)')}.")
            n_tr, need, allow = _SIGNATURES[name]
            args = node.args
            if not n_tr + need <= len(args) <= n_tr + allow:
                raise ValueError(f"{name}() takes {_USAGE.get(name, name + '(x)')}.")
            traces = [self._build(a) for a in args[:n_tr]]
            consts = [self._number(a, f"{name}()'s settings") for a in args[n_tr:]]
            call = _Call(name, traces, consts)
            return call
        raise ValueError("The formula can only use numbers, channel names, + - * / **, brackets and "
                         f"the functions ({', '.join(FUNCTIONS)}).")

    def check(self, fs: float) -> None:
        """Raise ValueError if a filter can't be made at this sample rate."""
        for node in _walk(self.root):
            if isinstance(node, _Call) and (node.name in _FILTERS or node.name == "notch"):
                node._get_sos(fs)

    def offline(self, env: dict, fs: float) -> np.ndarray:
        """The calculated trace of a whole recorded block (zero-phase filters)."""
        return np.broadcast_to(self.root.run(env, fs, None), np.shape(env[next(iter(self.refs))])).astype(float)

    def stream(self, env: dict, fs: float) -> np.ndarray:
        """The next chunk of a live calculation; state carries over to the next chunk."""
        out = self.root.run(env, fs, self.state)
        return np.broadcast_to(out, np.shape(env[next(iter(self.refs))])).astype(float)

    def reset(self) -> None:
        """Start the live calculation afresh (filters settle, integrals restart)."""
        self.state = {}


def _walk(node):
    yield node
    for attr in ("a", "b"):
        if hasattr(node, attr):
            yield from _walk(getattr(node, attr))
    for child in getattr(node, "args", []):
        yield from _walk(child)


PRESETS = [
    # (menu text, name pattern, formula pattern, unit pattern); {ch} is the channel
    ("Band-pass filter (spikes: 300–3000 Hz)", "{ch} band-pass", "bandpass({q}, 300, 3000)", "{u}"),
    ("Low-pass filter (100 Hz)", "{ch} low-pass", "lowpass({q}, 100)", "{u}"),
    ("High-pass filter (100 Hz)", "{ch} high-pass", "highpass({q}, 100)", "{u}"),
    ("Notch out mains hum (60 Hz)", "{ch} no hum", "notch({q}, 60)", "{u}"),
    ("Notch out mains hum (50 Hz)", "{ch} no hum", "notch({q}, 50)", "{u}"),
    ("Smoothed (1 ms average)", "{ch} smoothed", "smooth({q}, 1)", "{u}"),
    ("RMS envelope (10 ms)", "{ch} RMS", "rms({q}, 10)", "{u}"),
    ("Derivative (dV/dt)", "d{ch}/dt", "derivative({q})", "{u}/s"),
    ("Integral", "∫{ch}", "integral({q})", "{u}·s"),
    ("Difference of two channels", "{ch} − {ch2}", "{q} - {q2}", "{u}"),
]


def preset(i: int, channels: list[str], units: dict[str, str]) -> tuple[str, str, str]:
    """(name, formula, unit) for preset i using the first channel(s)."""
    _, name, formula, unit = PRESETS[i]
    ch = channels[0] if channels else "Ch1"
    ch2 = channels[1] if len(channels) > 1 else ch
    quote = lambda c: c if re.fullmatch(r"[A-Za-z_]\w*", c) else f'"{c}"'
    fill = dict(ch=ch, ch2=ch2, q=quote(ch), q2=quote(ch2), u=units.get(ch, "V"))
    return name.format(**fill), formula.format(**fill), unit.format(**fill)
