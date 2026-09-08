"""Technical indicators + performance stats. Pure Python, numpy-optional.

All functions accept any sequence of floats. `None` pads warmup periods so
outputs align 1:1 with inputs (critical: no lookahead, no misalignment).
"""
from __future__ import annotations

import math

try:
    import numpy as _np  # optional accelerator
    _HAS_NP = True
except Exception:
    _np = None
    _HAS_NP = False


def _as_list(x) -> list[float]:
    if _HAS_NP and isinstance(x, _np.ndarray):
        return x.astype(float).tolist()
    return [float(v) for v in x]


def sma(values, period: int) -> list[float | None]:
    v = _as_list(values)
    out: list[float | None] = [None] * len(v)
    if period <= 0 or len(v) < period:
        return out
    s = sum(v[:period])
    out[period - 1] = s / period
    for i in range(period, len(v)):
        s += v[i] - v[i - period]
        out[i] = s / period
    return out


def ema(values, period: int) -> list[float | None]:
    v = _as_list(values)
    out: list[float | None] = [None] * len(v)
    if period <= 0 or len(v) < period:
        return out
    k = 2.0 / (period + 1)
    e = sum(v[:period]) / period
    out[period - 1] = e
    for i in range(period, len(v)):
        e = v[i] * k + e * (1 - k)
        out[i] = e
    return out


def rsi(closes, period: int = 14) -> list[float | None]:
    c = _as_list(closes)
    out: list[float | None] = [None] * len(c)
    if len(c) < period + 1 or period <= 0:
        return out
    gains, losses = [], []
    for i in range(1, period + 1):
        d = c[i] - c[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    ag, al = sum(gains) / period, sum(losses) / period
    out[period] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(period + 1, len(c)):
        d = c[i] - c[i - 1]
        ag = (ag * (period - 1) + max(d, 0)) / period
        al = (al * (period - 1) + max(-d, 0)) / period
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def macd(closes, fast: int = 12, slow: int = 26, signal: int = 9):
    c = _as_list(closes)
    ef, es = ema(c, fast), ema(c, slow)
    line: list[float | None] = [
        (a - b) if (a is not None and b is not None) else None
        for a, b in zip(ef, es)]
    valid = [x for x in line if x is not None]
    sig_raw = ema(valid, signal) if len(valid) >= signal else [None] * len(valid)
    sig: list[float | None] = [None] * len(c)
    j = len(c) - len(valid)
    for i, s in enumerate(sig_raw):
        if j + i >= 0:
            sig[j + i] = s
    hist = [(l - s) if (l is not None and s is not None) else None
            for l, s in zip(line, sig)]
    return {"macd": line, "signal": sig, "hist": hist}


def bollinger(closes, period: int = 20, mult: float = 2.0):
    c = _as_list(closes)
    mid = sma(c, period)
    upper: list[float | None] = [None] * len(c)
    lower: list[float | None] = [None] * len(c)
    for i in range(period - 1, len(c)):
        w = c[i - period + 1:i + 1]
        m = sum(w) / period
        var = sum((x - m) ** 2 for x in w) / period
        sd = math.sqrt(var)
        upper[i] = m + mult * sd
        lower[i] = m - mult * sd
    return {"mid": mid, "upper": upper, "lower": lower}


def atr(highs, lows, closes, period: int = 14) -> list[float | None]:
    h, lo, c = _as_list(highs), _as_list(lows), _as_list(closes)
    out: list[float | None] = [None] * len(c)
    if len(c) < period + 1:
        return out
    trs = []
    for i in range(1, len(c)):
        trs.append(max(h[i] - lo[i], abs(h[i] - c[i - 1]), abs(lo[i] - c[i - 1])))
    a = sum(trs[:period]) / period
    out[period] = a
    for i in range(period + 1, len(c)):
        a = (a * (period - 1) + trs[i - 1]) / period
        out[i] = a
    return out


def stochastic(highs, lows, closes, k: int = 14, d: int = 3):
    h, lo, c = _as_list(highs), _as_list(lows), _as_list(closes)
    kf: list[float | None] = [None] * len(c)
    for i in range(k - 1, len(c)):
        hh, ll = max(h[i - k + 1:i + 1]), min(lo[i - k + 1:i + 1])
        kf[i] = 50.0 if hh == ll else 100 * (c[i] - ll) / (hh - ll)
    valid = [x for x in kf if x is not None]
    df = sma(valid, d) if len(valid) >= d else [None] * len(valid)
    out_d: list[float | None] = [None] * len(c)
    j = len(c) - len(valid)
    for i, s in enumerate(df):
        out_d[j + i] = s
    return {"k": kf, "d": out_d}


def obv(closes, volumes) -> list[float]:
    c, v = _as_list(closes), _as_list(volumes)
    out = [0.0] * len(c)
    for i in range(1, len(c)):
        out[i] = out[i - 1] + (v[i] if c[i] > c[i - 1] else (-v[i] if c[i] < c[i - 1] else 0))
    return out


# ---------- performance stats ----------
def returns(equity: list[float]) -> list[float]:
    return [equity[i] / equity[i - 1] - 1 for i in range(1, len(equity)) if equity[i - 1]]


def sharpe(rets: list[float], periods_per_year: int = 365, rf: float = 0.0) -> float:
    if len(rets) < 2:
        return 0.0
    m = sum(rets) / len(rets)
    var = sum((r - m) ** 2 for r in rets) / (len(rets) - 1)
    sd = math.sqrt(var)
    return 0.0 if sd == 0 else (m - rf) * math.sqrt(periods_per_year) / sd


def sortino(rets: list[float], periods_per_year: int = 365) -> float:
    if len(rets) < 2:
        return 0.0
    m = sum(rets) / len(rets)
    downside = [r for r in rets if r < 0]
    if not downside:
        return float("inf") if m > 0 else 0.0
    var = sum(r ** 2 for r in downside) / len(downside)
    sd = math.sqrt(var)
    return 0.0 if sd == 0 else m * math.sqrt(periods_per_year) / sd


def max_drawdown(equity: list[float]) -> dict:
    peak, mdd, top, bot = -1e18, 0.0, 0, 0
    for i, e in enumerate(equity):
        if e > peak:
            peak, top = e, i
        dd = (peak - e) / peak if peak else 0
        if dd > mdd:
            mdd, bot = dd, i
    return {"max_drawdown": mdd, "peak_idx": top, "trough_idx": bot}
