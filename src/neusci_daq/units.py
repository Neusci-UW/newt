"""Numbers typed with units: "500 mV", "0.2 s", "1.5 kHz", "5 SD".

A field has a base unit; any SI prefix of it (and of any other unit it accepts)
is converted to it. No Qt here, so the analysis code can use it too.
"""
from __future__ import annotations

import re

# SI prefixes a typed unit may carry ("u" for people without a µ key).
_PREFIX = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "μ": 1e-6, "m": 1e-3, "k": 1e3}
_QUANTITY = re.compile(r"^\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:e[-+]?\d+)?)\s*(\S*)\s*$", re.IGNORECASE)


def _split_unit(u: str) -> tuple[float, str]:
    """'mV' -> (0.001, 'v'); 'V' -> (1, 'v'). Bases compare without case."""
    if len(u) > 1 and u[0] in _PREFIX:
        return _PREFIX[u[0]], u[1:].lower()
    return 1.0, u.lower()


def unit_factor(typed: str, target: str) -> float | None:
    """How many `target` one `typed` is, if they're the same kind of unit with
    different SI prefixes: unit_factor('s', 'ms') == 1000. None if they differ."""
    if typed == target:
        return 1.0
    (pt, bt), (pg, bg) = _split_unit(typed), _split_unit(target)
    return pt / pg if bt == bg else None


def _tidy_unit(typed: str, family: str) -> str:
    """How to show a typed unit: its prefix (u -> µ) and the family's spelling of the
    base, so 'S' shows as 's', 'us' as 'µs' and 'mv' as 'mV'."""
    base = family[1:] if len(family) > 1 and family[0] in _PREFIX else family
    prefix = typed[0] if len(typed) > 1 and typed[0] in _PREFIX else ""
    return {"u": "µ", "μ": "µ"}.get(prefix, prefix) + base


def _converter(families):
    """convert(unit) -> factor to the base unit, for `families`: [(unit, how many base
    units one of it is)]. Any SI prefix of a family's unit is accepted."""
    def convert(typed):
        for fam, k in families:
            f = unit_factor(typed, fam)
            if f is not None:
                return f * k
        return None
    return convert


def _same_kind(unit: str):
    """For a field in `unit`: accept that unit with any SI prefix (ms, s, µs; Hz, kHz)."""
    return _converter([(unit, 1.0)])


def parse_quantity(text: str, convert, default_unit: str = "") -> tuple[float, str]:
    """'500 mV' -> (the number in base units, 'mV'), using convert(unit) -> factor or None.
    A bare number is in `default_unit` ('' = base units). Raises ValueError."""
    m = _QUANTITY.match(text.replace(",", ""))
    if not m:
        raise ValueError(f"{text.strip()!r} isn't a number")
    unit = m.group(2) or default_unit
    factor = 1.0 if not unit else convert(unit)
    if factor is None:
        raise ValueError(f"{unit!r} isn't a unit this field understands")
    return float(m.group(1)) * factor, unit


def best_prefix(v: float, unit: str, prefixes=("µ", "m", "")) -> tuple[float, str]:
    """v (in `unit`, which may itself have a prefix) shown with the largest prefix that
    keeps it at least 1: (0.0374, "V") -> (37.4, "mV"). 0 stays as it is."""
    base = unit[1:] if len(unit) > 1 and unit[0] in _PREFIX else unit
    in_base = v * (unit_factor(unit, base) or 1.0)
    if in_base == 0:
        return v, unit
    factor = {p: (_PREFIX[p] if p else 1.0) for p in prefixes}
    fits = [p for p in prefixes if abs(in_base) >= factor[p] * (1 - 1e-9)]
    p = max(fits, key=factor.get) if fits else min(prefixes, key=factor.get)
    return in_base / factor[p], p + base
