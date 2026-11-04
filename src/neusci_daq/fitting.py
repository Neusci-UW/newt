"""Curve fitting for the DataPad: polynomials of any degree and a few curves
physiology labs use (exponential decay for time constants, Boltzmann for activation,
power and log laws).

fit(model, x, y, degree) returns a Fit with the parameters, a readable equation,
R² and a predict function. Plain numpy and scipy, so it's tested without a window.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
from scipy.optimize import curve_fit

MODELS = {
    "poly": "Polynomial",
    "exp": "Exponential decay/rise",
    "boltzmann": "Boltzmann (sigmoid)",
    "power": "Power law",
    "log": "Logarithmic",
}


@dataclass
class Fit:
    model: str
    names: list[str]
    params: np.ndarray
    errors: np.ndarray                      # standard errors of the parameters
    equation: str
    r2: float
    n: int
    predict: Callable[[np.ndarray], np.ndarray] = field(repr=False)

    def table(self) -> list[tuple[str, float, float]]:
        return list(zip(self.names, self.params.tolist(), self.errors.tolist()))


def _r2(y, yhat) -> float:
    ss = np.sum((y - y.mean()) ** 2)
    return float(1 - np.sum((y - yhat) ** 2) / ss) if ss > 0 else float("nan")


def _g(v: float) -> str:
    return f"{v:.4g}"


def fit(model: str, x, y, degree: int = 1) -> Fit:
    """Fit y(x). Raises ValueError with a reason when it can't."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    ok = np.isfinite(x) & np.isfinite(y)
    x, y = x[ok], y[ok]
    if model == "poly":
        degree = int(degree)
        if len(x) < degree + 1:
            raise ValueError(f"A degree-{degree} polynomial needs at least {degree + 1} points")
        if np.ptp(x) == 0 and degree > 0:
            raise ValueError("All the x values are the same")
        x0, sx = x.mean(), (np.ptp(x) / 2 or 1.0)       # fitted in u = (x − x0)/sx for accuracy
        P = np.polynomial.Polynomial(np.polyfit((x - x0) / sx, y, degree)[::-1])
        coef = P(np.polynomial.Polynomial([-x0 / sx, 1 / sx])).coef     # back in powers of x
        coef = np.r_[coef, np.zeros(degree + 1 - len(coef))]
        coef[np.abs(coef) < 1e-12 * max(np.abs(coef).max(), 1e-300)] = 0.0   # round-off
        X = np.vander(x, degree + 1, increasing=True)
        dof = len(x) - degree - 1
        err = np.full(degree + 1, np.nan)
        if dof > 0:
            with np.errstate(all="ignore"):
                resid = np.sum((y - X @ coef) ** 2) / dof
                cov = np.linalg.pinv(X.T @ X) * resid
            err = np.sqrt(np.abs(np.diag(cov)))
        names = [f"a{k}" for k in range(degree + 1)]
        terms = [_g(coef[0])] + [f"{_g(coef[k])}·x" + (f"^{k}" if k > 1 else "") for k in range(1, degree + 1)]
        terms = [t for k, t in enumerate(terms) if coef[k] != 0] or ["0"]
        eq = "y = " + " + ".join(reversed(terms)).replace("+ -", "− ")

        def predict(xx, coef=coef):
            return np.polynomial.polynomial.polyval(np.asarray(xx, float), coef)
        return Fit(model, names, coef, err, eq, _r2(y, predict(x)), len(x), predict)

    if len(x) < 4:
        raise ValueError("Needs at least 4 points")
    if model == "exp":
        x0 = x.min()

        def f(xx, a, tau, c):
            return a * np.exp(-(xx - x0) / tau) + c
        order = np.argsort(x)
        p0 = (y[order][0] - y[order][-1], np.ptp(x) / 3 or 1.0, y[order][-1])
        names = ["A", "τ", "C"]
        eq_of = lambda p: f"y = {_g(p[0])}·e^(−(x − {_g(x0)})/{_g(p[1])}) + {_g(p[2])}"
    elif model == "boltzmann":
        def f(xx, bottom, top, x50, k):
            return bottom + (top - bottom) / (1 + np.exp(np.clip((x50 - xx) / k, -500, 500)))
        rising = np.corrcoef(x, y)[0, 1] >= 0
        p0 = (y.min(), y.max(), np.median(x), (np.ptp(x) / 10 or 1.0) * (1 if rising else -1))
        names = ["bottom", "top", "x½", "k"]
        eq_of = lambda p: f"y = {_g(p[0])} + ({_g(p[1])} − {_g(p[0])}) / (1 + e^(({_g(p[2])} − x)/{_g(p[3])}))"
    elif model == "power":
        if (x <= 0).any() or (y <= 0).any():
            raise ValueError("A power law needs every x and y above zero")
        b, la = np.polyfit(np.log(x), np.log(y), 1)

        def f(xx, a, b):
            return a * np.power(xx, b)
        p0 = (np.exp(la), b)
        names = ["a", "b"]
        eq_of = lambda p: f"y = {_g(p[0])}·x^{_g(p[1])}"
    elif model == "log":
        if (x <= 0).any():
            raise ValueError("A log fit needs every x above zero")

        def f(xx, a, b):
            return a * np.log(xx) + b
        p0 = tuple(np.polyfit(np.log(x), y, 1))
        names = ["a", "b"]
        eq_of = lambda p: f"y = {_g(p[0])}·ln(x) + {_g(p[1])}"
    else:
        raise ValueError(f"Unknown model {model!r}")
    try:
        p, cov = curve_fit(f, x, y, p0=p0, maxfev=20000)
    except (RuntimeError, ValueError) as e:
        raise ValueError(f"The fit didn't converge ({e})") from None
    err = np.sqrt(np.abs(np.diag(cov))) if np.all(np.isfinite(cov)) else np.full(len(p), np.nan)
    predict = lambda xx, p=p: f(np.asarray(xx, float), *p)
    return Fit(model, names, p, err, eq_of(p), _r2(y, predict(x)), len(x), predict)
