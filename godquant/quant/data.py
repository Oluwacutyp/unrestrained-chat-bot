"""Market data: free sources over stdlib HTTP, disk cache, synthetic fallback.

Sources (auto order): binance (crypto, no key) → stooq (stocks, no key) →
yfinance (if installed) → synthetic (offline, seeded random-walk).
"""
from __future__ import annotations

import csv
import json
import logging
import random
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from godquant.config import base_dir

log = logging.getLogger("godquant.data")


@dataclass
class OHLCV:
    time: list[int]
    open: list[float]
    high: list[float]
    low: list[float]
    close: list[float]
    volume: list[float]

    def __len__(self):
        return len(self.close)


def _get(url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "godquant/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _cache_path(symbol: str, interval: str) -> Path:
    p = base_dir() / "data"
    p.mkdir(parents=True, exist_ok=True)
    return p / f"{symbol}_{interval}.csv"


def save_cache(symbol: str, interval: str, d: OHLCV):
    p = _cache_path(symbol, interval)
    with open(p, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time", "open", "high", "low", "close", "volume"])
        for i in range(len(d)):
            w.writerow([d.time[i], d.open[i], d.high[i], d.low[i], d.close[i], d.volume[i]])


def load_cache(symbol: str, interval: str, max_age_h: float = 24) -> OHLCV | None:
    p = _cache_path(symbol, interval)
    if not p.exists():
        return None
    if time.time() - p.stat().st_mtime > max_age_h * 3600:
        return None
    t, o, h, lo, c, v = [], [], [], [], [], []
    with open(p) as f:
        for row in csv.DictReader(f):
            t.append(int(row["time"])); o.append(float(row["open"]))
            h.append(float(row["high"])); lo.append(float(row["low"]))
            c.append(float(row["close"])); v.append(float(row["volume"]))
    return OHLCV(t, o, h, lo, c, v) if c else None


def fetch_binance(symbol: str = "BTCUSDT", interval: str = "1d", limit: int = 500) -> OHLCV:
    url = (f"https://api.binance.com/api/v3/klines?symbol={symbol.upper()}"
           f"&interval={interval}&limit={min(limit, 1000)}")
    raw = json.loads(_get(url).decode())
    t, o, h, lo, c, v = [], [], [], [], [], []
    for k in raw:
        t.append(int(k[0] // 1000)); o.append(float(k[1])); h.append(float(k[2]))
        lo.append(float(k[3])); c.append(float(k[4])); v.append(float(k[5]))
    return OHLCV(t, o, h, lo, c, v)


def fetch_stooq(symbol: str = "aapl.us", interval: str = "d") -> OHLCV:
    q = urllib.parse.quote(symbol.lower())
    url = f"https://stooq.com/q/d/l/?s={q}&i={interval}"
    text = _get(url).decode()
    rows = list(csv.DictReader(text.strip().splitlines()))
    t, o, h, lo, c, v = [], [], [], [], [], []
    for r in rows:
        try:
            from datetime import datetime
            t.append(int(datetime.strptime(r["Date"], "%Y-%m-%d").timestamp()))
            o.append(float(r["Open"])); h.append(float(r["High"]))
            lo.append(float(r["Low"])); c.append(float(r["Close"]))
            v.append(float(r["Volume"] or 0))
        except (ValueError, KeyError):
            continue
    if not c:
        raise RuntimeError(f"stooq returned no rows for {symbol}")
    return OHLCV(t, o, h, lo, c, v)


def fetch_yfinance(symbol: str, period: str = "2y") -> OHLCV:
    import yfinance as yf  # type: ignore
    df = yf.download(symbol, period=period, progress=False, auto_adjust=True)
    if df is None or len(df) == 0:
        raise RuntimeError(f"yfinance: no data for {symbol}")
    t = [int(x.timestamp()) for x in df.index.to_pydatetime()]
    col = lambda n: [float(x) for x in df[n].values.ravel()]
    return OHLCV(t, col("Open"), col("High"), col("Low"), col("Close"), col("Volume"))


def synthetic(symbol: str = "SYNTH", n: int = 500, seed: int = 42,
              start: float = 100.0, drift: float = 0.0004, vol: float = 0.02) -> OHLCV:
    rng = random.Random(seed + hash(symbol) % 10_000)
    t, o, h, lo, c, v = [], [], [], [], [], []
    price, ts = start, int(time.time()) - n * 86400
    for i in range(n):
        o_ = price
        shock = rng.gauss(drift, vol)
        c_ = max(o_ * (1 + shock), 0.01)
        h_ = max(o_, c_) * (1 + abs(rng.gauss(0, vol / 3)))
        l_ = min(o_, c_) * (1 - abs(rng.gauss(0, vol / 3)))
        t.append(ts + i * 86400); o.append(o_); h.append(h_)
        lo.append(l_); c.append(c_); v.append(rng.uniform(500, 5000))
        price = c_
    return OHLCV(t, o, h, lo, c, v)


def _is_crypto(symbol: str) -> bool:
    s = symbol.upper()
    return s.endswith(("USDT", "USD", "BTC", "ETH", "BNB")) and "." not in s


def get_ohlc(symbol: str, interval: str = "1d", limit: int = 500,
             source: str = "auto", offline: bool = False) -> tuple[OHLCV, str]:
    """Returns (data, source_used). Never raises on network — falls back."""
    if offline:
        source = "synthetic"
    cached = load_cache(symbol, interval)
    if cached and len(cached) >= 50:
        return cached, "cache"
    order = [source] if source != "auto" else (
        ["binance", "stooq", "yfinance", "synthetic"] if _is_crypto(symbol)
        else ["stooq", "yfinance", "synthetic"])
    last_err: Exception | None = None
    for s in order:
        try:
            if s == "binance":
                d = fetch_binance(symbol, interval, limit)
            elif s == "stooq":
                st = symbol if "." in symbol else f"{symbol}.us"
                d = fetch_stooq(st, "d")
            elif s == "yfinance":
                d = fetch_yfinance(symbol)
            else:
                d = synthetic(symbol, n=limit)
            if len(d) >= 30:
                try:
                    save_cache(symbol, interval, d)
                except Exception:
                    pass
                return d, s
        except Exception as e:
            last_err = e
            log.debug("source %s failed for %s: %s", s, symbol, e)
    if cached:
        return cached, "cache-stale"
    log.warning("all sources failed (%s); using synthetic", last_err)
    return synthetic(symbol, n=limit), "synthetic"
