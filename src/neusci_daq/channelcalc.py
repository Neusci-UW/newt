"""Calculated channels' kinds (LabChart's Channel Calculation): each kind's label, its
settings, and how they become a formula (dsp.py works it out); `spec_of` reads a formula back
into a kind and settings. Plain Python, shared by the Qt dialog (analysischannel.py) and the
HTML front end (web/trials.py).
"""
from __future__ import annotations

import re

# setting: (label, default, unit shown, tooltip)
_LEVEL = ("level", ("Level", 0.5, "", "An event is each crossing of this level (in the source's units)"))
_DIR = ("dir", ("Direction", "above", "", "Count crossings going above the level, or going below it"))
_DEAD = ("dead", ("Dead time", 1.0, "ms", "Ignore crossings this soon after an event (noise on the edge)"))

# key: (menu label, group, [(setting key, (label, default, unit, tip))], unit rule)
KINDS = {
    "lowpass": ("Low-pass filter", "Digital filter", [("hz", ("Cut-off", 100.0, "Hz", "Keeps slower than this")),
                                                     ("order", ("Order", 4, "", "Steeper roll-off when higher"))], "{u}"),
    "highpass": ("High-pass filter", "Digital filter", [("hz", ("Cut-off", 100.0, "Hz", "Keeps faster than this")),
                                                       ("order", ("Order", 4, "", "Steeper roll-off when higher"))], "{u}"),
    "bandpass": ("Band-pass filter", "Digital filter", [("lo", ("Low cut-off", 300.0, "Hz", "")),
                                                       ("hi", ("High cut-off", 3000.0, "Hz", "")),
                                                       ("order", ("Order", 4, "", ""))], "{u}"),
    "bandstop": ("Band-stop filter", "Digital filter", [("lo", ("Low", 55.0, "Hz", "")), ("hi", ("High", 65.0, "Hz", "")),
                                                       ("order", ("Order", 4, "", ""))], "{u}"),
    "notch": ("Notch (mains hum)", "Digital filter", [("hz", ("Frequency", 60.0, "Hz", "60 Hz in the US, 50 Hz in Europe"))],
              "{u}"),
    "smooth": ("Smoothing (moving average)", "Smoothing & shape", [("ms", ("Window", 1.0, "ms", ""))], "{u}"),
    "rms": ("RMS (moving)", "Smoothing & shape", [("ms", ("Window", 10.0, "ms", ""))], "{u}"),
    "envelope": ("Rectify and smooth (envelope)", "Smoothing & shape", [("ms", ("Window", 10.0, "ms", ""))], "{u}"),
    "abs": ("Absolute value (rectify)", "Smoothing & shape", [], "{u}"),
    "derivative": ("Derivative", "Calculus", [], "{u}/s"),
    "integral": ("Integral", "Calculus", [], "{u}·s"),
    "scale": ("Scale and offset", "Arithmetic", [("a", ("Multiply by", 1.0, "", "")),
                                                ("b", ("Then add", 0.0, "", ""))], "{u}"),
    "combine": ("With another channel (+ − × ÷)", "Arithmetic", [("op", ("Operation", "−", "", "")),
                                                                ("other", ("Other channel", "", "", ""))], "{u}"),
    "rate": ("Rate meter", "Cyclic measurements", [_LEVEL, _DIR, _DEAD,
                                                   ("per", ("Per", "min", "", "Events per minute (e.g. heart "
                                                                              "rate) or per second"))], "/{per}"),
    "period": ("Period (time between events)", "Cyclic measurements", [_LEVEL, _DIR, _DEAD], "s"),
    "height": ("Cyclic height (max − min per cycle)", "Cyclic measurements", [_LEVEL, _DIR, _DEAD], "{u}"),
    "count": ("Event count", "Cyclic measurements", [_LEVEL, _DIR, _DEAD], ""),
    "custom": ("Custom formula", "Other", [("formula", ("Formula", "", "", ""))], "{u}"),
}
_NAMES = {"lowpass": "low-pass", "highpass": "high-pass", "bandpass": "band-pass", "bandstop": "band-stop",
          "notch": "no hum", "smooth": "smoothed", "rms": "RMS", "envelope": "envelope", "abs": "rectified",
          "derivative": "derivative", "integral": "integral", "scale": "scaled", "combine": "",
          "rate": "rate", "period": "period", "height": "cyclic height", "count": "count", "custom": "calc"}
FORMULA_HELP = ("Formulas use the channel names, numbers, + − × ÷ (+ - * /), ** and brackets, and:<br>"
         "<b>lowpass</b>(x, Hz) · <b>highpass</b>(x, Hz) · <b>bandpass</b>(x, low, high) · "
         "<b>bandstop</b>(x, low, high) · <b>notch</b>(x, Hz) · <b>smooth</b>(x, ms) · <b>rms</b>(x, ms) · "
         "<b>envelope</b>(x, ms) · <b>derivative</b>(x) · <b>integral</b>(x) · abs, sqrt, exp, log, sin, cos, "
         "clip(x, lo, hi), max(a, b), min(a, b).<br>"
         "A name with spaces goes in quotes: lowpass(\"Channel 1\", 100). Recordings are never changed: "
         "these are worked out for display and analysis. Recorded trials are filtered without delay; "
         "live, filters have the small delay any live filter has.")
_OPS = {"+": "+", "−": "-", "×": "*", "÷": "/"}


def quote(name: str) -> str:
    return name if re.fullmatch(r"[A-Za-z_]\w*", name) else f'"{name}"'


def _g(v) -> str:
    return f"{float(v):g}"


def formula_of(kind: str, src: str, v: dict) -> str:
    """The formula for a kind of calculation of channel `src` with settings v."""
    q = quote(src)
    if kind in ("lowpass", "highpass"):
        return f"{kind}({q}, {_g(v['hz'])}" + (f", {int(v['order'])})" if int(v["order"]) != 4 else ")")
    if kind in ("bandpass", "bandstop"):
        return f"{kind}({q}, {_g(v['lo'])}, {_g(v['hi'])}" + (f", {int(v['order'])})" if int(v["order"]) != 4 else ")")
    if kind == "notch":
        return f"notch({q}, {_g(v['hz'])})"
    if kind in ("smooth", "rms", "envelope"):
        return f"{kind}({q}, {_g(v['ms'])})"
    if kind in ("abs", "derivative", "integral"):
        return f"{kind}({q})"
    if kind == "scale":
        a, b = float(v["a"]), float(v["b"])
        out = q if a == 1 else f"{_g(a)} * {q}"
        return out if b == 0 else f"{out} {'+' if b > 0 else '-'} {_g(abs(b))}"
    if kind == "combine":
        return f"{q} {_OPS.get(v['op'], v['op'])} {quote(v['other'])}"
    if kind in ("rate", "period", "height", "count"):
        x, level = (q, float(v["level"])) if v["dir"] == "above" else (f"-{q}", -float(v["level"]))
        dead = "" if float(v["dead"]) == 1 else f", {_g(v['dead'])}"
        body = f"{kind}({x}, {_g(level)}{dead})"
        return f"60 * {body}" if kind == "rate" and v.get("per") == "min" else body
    return v.get("formula", "")


_NUM = r"(-?[\d.]+(?:e-?\d+)?)"
_SRC = r'("[^"]+"|[A-Za-z_]\w*)'


def spec_of(formula: str) -> tuple[str, str, dict] | None:
    """(kind, source, settings) for a formula this dialog writes; None for others."""
    f = formula.strip()
    unq = lambda s: s[1:-1] if s.startswith('"') else s
    m = re.fullmatch(rf"(lowpass|highpass)\({_SRC}, {_NUM}(?:, (\d+))?\)", f)
    if m:
        return m[1], unq(m[2]), {"hz": float(m[3]), "order": int(m[4] or 4)}
    m = re.fullmatch(rf"(bandpass|bandstop)\({_SRC}, {_NUM}, {_NUM}(?:, (\d+))?\)", f)
    if m:
        return m[1], unq(m[2]), {"lo": float(m[3]), "hi": float(m[4]), "order": int(m[5] or 4)}
    m = re.fullmatch(rf"notch\({_SRC}, {_NUM}\)", f)
    if m:
        return "notch", unq(m[1]), {"hz": float(m[2])}
    m = re.fullmatch(rf"(smooth|rms|envelope)\({_SRC}, {_NUM}\)", f)
    if m:
        return m[1], unq(m[2]), {"ms": float(m[3])}
    m = re.fullmatch(rf"(abs|derivative|integral)\({_SRC}\)", f)
    if m:
        return m[1], unq(m[2]), {}
    m = re.fullmatch(rf"(60 \* )?(rate|period|height|count)\((-?){_SRC}, {_NUM}(?:, {_NUM})?\)", f)
    if m and (not m[1] or m[2] == "rate"):
        below = m[3] == "-"
        v = {"level": -float(m[5]) if below else float(m[5]), "dir": "below" if below else "above",
             "dead": float(m[6] or 1)}
        if m[2] == "rate":
            v["per"] = "min" if m[1] else "s"
        return m[2], unq(m[4]), v
    m = re.fullmatch(rf"(?:{_NUM} \* )?{_SRC}(?: ([+-]) {_NUM})?", f)
    if m:                                             # a plain copy is a scale of 1
        b = float(m[4]) * (1 if m[3] == "+" else -1) if m[3] else 0.0
        return "scale", unq(m[2]), {"a": float(m[1] or 1), "b": b}
    m = re.fullmatch(rf"{_SRC} ([-+*/]) {_SRC}", f)
    if m:
        op = {v: k for k, v in _OPS.items()}[m[2]]
        return "combine", unq(m[1]), {"op": op, "other": unq(m[3])}
    return None


def unit_for(kind: str, src_unit: str, v: dict) -> str:
    return KINDS[kind][3].format(u=src_unit, per=v.get("per", "s"))


def auto_name(kind: str, src: str, v: dict, taken) -> str:
    """The name a new channel gets until you type one: "Ch1 band-pass", "Ch1 band-pass 2"..."""
    base = (f"{src} {v.get('op', '')} {v.get('other', '')}" if kind == "combine" else f"{src} {_NAMES[kind]}").strip()
    name, n = base, 2
    while name in taken:
        name, n = f"{base} {n}", n + 1
    return name
