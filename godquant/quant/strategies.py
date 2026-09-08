"""Built-in strategy library. Every strategy: signals(data, **params) -> list[int].

Signals align 1:1 with closes. Strategies may only use information available
at bar t (indicators are causal by construction). Position is persistent:
signal holds until flipped (the backtester handles flip/flat transitions).
"""
from __future__ import annotations

from godquant.quant import indicators as I
from godquant.quant.data import OHLCV

STRATEGIES: dict[str, dict] = {}


def register(name: str, defaults: dict, grid: dict):
    def deco(fn):
        STRATEGIES[name] = {"fn": fn, "defaults": defaults, "grid": grid,
                            "doc": (fn.__doc__ or "").strip()}
        return fn
    return deco


def _hold(n: int, entries: list[bool], exits: list[bool], start_flat=True) -> list[int]:
    sig, pos = [0] * n, 0
    for i in range(n):
        if pos == 0 and entries[i]:
            pos = 1
        elif pos != 0 and exits[i]:
            pos = 0
        sig[i] = pos
    return sig


@register("sma_cross",
          {"fast": 20, "slow": 50},
          {"fast": [10, 20, 30], "slow": [50, 100, 200]})
def sma_cross(d: OHLCV, fast: int = 20, slow: int = 50) -> list[int]:
    """Classic trend filter: long when SMA(fast) > SMA(slow)."""
    f, s = I.sma(d.close, fast), I.sma(d.close, slow)
    n = len(d)
    entries = [bool(f[i] and s[i] and f[i] > s[i]) for i in range(n)]  # type: ignore
    exits = [bool(f[i] and s[i] and f[i] <= s[i]) for i in range(n)]  # type: ignore
    return _hold(n, entries, exits)


@register("ema_macd",
          {"fast": 12, "slow": 26, "signal": 9},
          {"fast": [8, 12], "slow": [21, 26], "signal": [7, 9]})
def ema_macd(d: OHLCV, fast: int = 12, slow: int = 26, signal: int = 9) -> list[int]:
    """MACD momentum: long when MACD line crosses above signal line."""
    m = I.macd(d.close, fast, slow, signal)
    n = len(d)
    entries, exits = [False] * n, [False] * n
    for i in range(1, n):
        ml, sg = m["macd"][i], m["signal"][i]
        pl, ps = m["macd"][i - 1], m["signal"][i - 1]
        if None in (ml, sg, pl, ps):
            continue
        if pl <= ps and ml > sg:  # type: ignore
            entries[i] = True
        elif pl >= ps and ml < sg:  # type: ignore
            exits[i] = True
    return _hold(n, entries, exits)


@register("rsi_meanrev",
          {"period": 14, "oversold": 30, "overbought": 70},
          {"period": [7, 14, 21], "oversold": [20, 30], "overbought": [70, 80]})
def rsi_meanrev(d: OHLCV, period: int = 14, oversold: float = 30,
                overbought: float = 70) -> list[int]:
    """Mean reversion: buy oversold RSI, exit on overbought."""
    r = I.rsi(d.close, period)
    n = len(d)
    entries = [bool(x is not None and x < oversold) for x in r]
    exits = [bool(x is not None and x > overbought) for x in r]
    return _hold(n, entries, exits)


@register("bollinger_breakout",
          {"period": 20, "mult": 2.0},
          {"period": [20, 30], "mult": [1.5, 2.0, 2.5]})
def bollinger_breakout(d: OHLCV, period: int = 20, mult: float = 2.0) -> list[int]:
    """Volatility breakout: long on close above upper band, exit below mid."""
    b = I.bollinger(d.close, period, mult)
    n = len(d)
    entries = [bool(b["upper"][i] and d.close[i] > b["upper"][i]) for i in range(n)]  # type: ignore
    exits = [bool(b["mid"][i] and d.close[i] < b["mid"][i]) for i in range(n)]  # type: ignore
    return _hold(n, entries, exits)


@register("donchian_trend",
          {"entry": 20, "exit": 10},
          {"entry": [20, 55], "exit": [10, 20]})
def donchian_trend(d: OHLCV, entry: int = 20, exit: int = 10) -> list[int]:
    """Turtle-style channel breakout with trailing exit."""
    n = len(d)
    entries, exits = [False] * n, [False] * n
    for i in range(entry, n):
        if d.high[i] >= max(d.high[i - entry:i]):
            entries[i] = True
    for i in range(exit, n):
        if d.low[i] <= min(d.low[i - exit:i]):
            exits[i] = True
    return _hold(n, entries, exits)


def list_strategies() -> str:
    lines = []
    for name, meta in STRATEGIES.items():
        lines.append(f"- {name}: {meta['doc']} defaults={meta['defaults']}")
    return "\n".join(lines)


def generate(name: str, d: OHLCV, params: dict | None = None) -> list[int]:
    if name not in STRATEGIES:
        raise ValueError(f"unknown strategy '{name}'. Available: {sorted(STRATEGIES)}")
    meta = STRATEGIES[name]
    p = dict(meta["defaults"])
    if params:
        p.update(params)
    return meta["fn"](d, **p)
