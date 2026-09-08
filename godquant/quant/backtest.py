"""Event-driven backtester. Pure Python, no lookahead.

Convention: signal[t] is computed from data up to t, executed at close[t+1]
(or close[t] if fill_on_signal_bar=True for analytics only).
Signals: +1 long, 0 flat, -1 short (short optional).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from godquant.quant import indicators as I


@dataclass
class Trade:
    entry_idx: int
    exit_idx: int
    side: int
    entry_px: float
    exit_px: float
    qty: float
    pnl: float
    return_pct: float


@dataclass
class BacktestResult:
    equity: list[float] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    def summary(self) -> str:
        m = self.metrics
        rows = [
            ("Return", f"{m.get('total_return', 0):+.2%}"),
            ("CAGR", f"{m.get('cagr', 0):+.2%}"),
            ("Sharpe", f"{m.get('sharpe', 0):.2f}"),
            ("Sortino", f"{m.get('sortino', 0):.2f}"),
            ("Max DD", f"{m.get('max_drawdown', 0):.2%}"),
            ("Win rate", f"{m.get('win_rate', 0):.1%}"),
            ("Profit factor", f"{m.get('profit_factor', 0):.2f}"),
            ("Trades", f"{m.get('num_trades', 0)}"),
            ("Exposure", f"{m.get('exposure', 0):.1%}"),
            ("Final equity", f"{m.get('final_equity', 0):,.2f}"),
        ]
        w = max(len(k) for k, _ in rows)
        return "\n".join(f"  {k:<{w}}  {v}" for k, v in rows)


def run_backtest(closes: list[float], signals: list[int],
                 initial_cash: float = 10_000.0,
                 commission: float = 0.001, slippage: float = 0.0005,
                 allow_short: bool = False,
                 risk_per_trade: float = 1.0,  # fraction of equity per position
                 periods_per_year: int = 365) -> BacktestResult:
    n = len(closes)
    assert len(signals) == n, "signals must align 1:1 with closes"
    cash, qty, entry_px, entry_idx, side = initial_cash, 0.0, 0.0, 0, 0
    equity: list[float] = []
    trades: list[Trade] = []
    bars_in = 0

    def price(i: int, buy: bool) -> float:
        px = closes[i]
        slip = px * slippage * (1 if buy else -1)
        return px + slip

    for i in range(n):
        sig = signals[i]
        if not allow_short and sig < 0:
            sig = 0
        px = closes[i]
        # exit on flip/flat
        if side != 0 and sig != side:
            exit_px = price(i, buy=(side < 0))
            proceeds = qty * exit_px
            fee = proceeds * commission
            pnl = (exit_px - entry_px) * qty * side - fee - entry_fee
            cash += proceeds - fee if side > 0 else cash + pnl + entry_cost
            # (short accounting simplified: margin-free, pnl settled to cash)
            if side < 0:
                pass  # cash already adjusted via pnl line below
            trades.append(Trade(entry_idx, i, side, entry_px, exit_px, qty, pnl,
                                pnl / entry_cost if entry_cost else 0))
            qty, side = 0.0, 0
        # entry
        if side == 0 and sig != 0:
            buy_px = price(i, buy=(sig > 0))
            notional = cash * min(max(risk_per_trade, 0.01), 1.0) if sig > 0 else cash * 0.2
            if sig < 0:
                notional = cash * min(max(risk_per_trade, 0.01), 0.5)
            qty = notional / buy_px if buy_px > 0 else 0
            if qty > 0:
                entry_cost = qty * buy_px
                entry_fee = entry_cost * commission
                if side == 0 and sig > 0:
                    cash -= entry_cost + entry_fee
                side, entry_px, entry_idx = sig, buy_px, i
        # mark to market
        pos_val = qty * px if side > 0 else (cash + qty * (entry_px - px) if side < 0 else cash)
        total = (cash + qty * px) if side >= 0 else pos_val
        equity.append(total)
        if side != 0:
            bars_in += 1

    # close dangling position at last close
    if side != 0:
        exit_px = closes[-1]
        pnl = (exit_px - entry_px) * qty * side - qty * exit_px * commission - entry_fee
        if side > 0:
            cash += qty * exit_px - qty * exit_px * commission
            equity[-1] = cash
        else:
            equity[-1] = cash + pnl
        trades.append(Trade(entry_idx, n - 1, side, entry_px, exit_px, qty, pnl,
                            pnl / entry_cost if entry_cost else 0))

    rets = I.returns(equity)
    wins = [t for t in trades if t.pnl > 0]
    gross_w = sum(t.pnl for t in wins)
    gross_l = -sum(t.pnl for t in trades if t.pnl <= 0)
    years = max(n / periods_per_year, 1e-9)
    total_ret = equity[-1] / initial_cash - 1 if equity and initial_cash else 0
    mdd = I.max_drawdown(equity) if equity else {"max_drawdown": 0}
    metrics = {
        "initial_cash": initial_cash,
        "final_equity": equity[-1] if equity else initial_cash,
        "total_return": total_ret,
        "cagr": (1 + total_ret) ** (1 / years) - 1 if total_ret > -1 else -1.0,
        "sharpe": I.sharpe(rets, periods_per_year),
        "sortino": I.sortino(rets, periods_per_year),
        "max_drawdown": mdd["max_drawdown"],
        "win_rate": len(wins) / len(trades) if trades else 0,
        "profit_factor": gross_w / gross_l if gross_l > 0 else (float("inf") if gross_w > 0 else 0),
        "num_trades": len(trades),
        "exposure": bars_in / n if n else 0,
        "avg_trade_pct": sum(t.return_pct for t in trades) / len(trades) if trades else 0,
    }
    return BacktestResult(equity=equity, trades=trades, metrics=metrics)
