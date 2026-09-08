"""Risk engine: sizing, Kelly, VaR/CVaR, portfolio heat, kill-switches."""
from __future__ import annotations

import math


def position_size_fixed_risk(equity: float, risk_pct: float, entry: float,
                             stop: float) -> dict:
    """Shares/contracts so that a stop-out loses exactly risk_pct of equity."""
    risk_amt = equity * risk_pct
    per_unit = abs(entry - stop)
    if per_unit <= 0:
        return {"qty": 0.0, "risk_amt": risk_amt, "note": "entry==stop"}
    qty = risk_amt / per_unit
    return {"qty": qty, "risk_amt": risk_amt,
            "notional": qty * entry,
            "leverage": (qty * entry) / equity if equity else 0}


def kelly_fraction(win_rate: float, avg_win: float, avg_loss: float,
                   cap: float = 0.25, half: bool = True) -> float:
    """Kelly stake fraction. Returns 0 for no edge. Half-Kelly by default."""
    if avg_loss <= 0 or win_rate <= 0 or win_rate >= 1:
        return 0.0
    b = avg_win / avg_loss
    f = win_rate - (1 - win_rate) / b
    f = max(f, 0.0)
    if half:
        f /= 2
    return min(f, cap)


def var_historical(returns: list[float], confidence: float = 0.95) -> float:
    """Historical VaR (positive number = expected worst loss) at confidence."""
    if not returns:
        return 0.0
    s = sorted(returns)
    idx = max(0, int((1 - confidence) * len(s)))
    return max(-s[idx], 0.0)


def cvar_historical(returns: list[float], confidence: float = 0.95) -> float:
    if not returns:
        return 0.0
    s = sorted(returns)
    idx = max(1, int((1 - confidence) * len(s)))
    tail = s[:idx]
    return max(-sum(tail) / len(tail), 0.0)


def risk_report(equity: float, risk_pct: float, entry: float, stop: float,
                win_rate: float = 0.5, avg_win: float = 1.0,
                avg_loss: float = 1.0,
                open_heat: float = 0.0, day_pnl_pct: float = 0.0) -> str:
    size = position_size_fixed_risk(equity, risk_pct, entry, stop)
    kelly = kelly_fraction(win_rate, avg_win, avg_loss)
    heat_after = open_heat + risk_pct
    verdict = "APPROVE"
    reasons = []
    if risk_pct > 0.02:
        verdict, reasons = "VETO", ["risk/trade > 2%"]
    if heat_after > 0.06:
        verdict, reasons = "VETO", [f"portfolio heat {heat_after:.1%} > 6%"]
    if day_pnl_pct <= -0.03:
        verdict, reasons = "HALT", ["daily kill-switch (-3%)"]
    lines = [
        f"Equity ${equity:,.2f} | risk/trade {risk_pct:.2%} (${size['risk_amt']:,.2f})",
        f"Entry {entry} Stop {stop} → qty {size['qty']:.4f} "
        f"(notional ${size['notional']:,.2f}, lev {size['leverage']:.2f}x)",
        f"Half-Kelly suggestion: {kelly:.2%} of equity",
        f"Portfolio heat after fill: {heat_after:.1%} (limit 6%)",
        f"VERDICT: {verdict}" + (f" — {'; '.join(reasons)}" if reasons else ""),
    ]
    return "\n".join(lines)
