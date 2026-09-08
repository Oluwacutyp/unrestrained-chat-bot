from godquant.quant.backtest import run_backtest
from godquant.quant.data import OHLCV, synthetic
from godquant.quant.strategies import STRATEGIES, generate


def test_buy_and_hold_profit():
    closes = [100 + i for i in range(50)]
    sig = [1] * 50
    r = run_backtest(closes, sig, commission=0, slippage=0)
    assert r.metrics["total_return"] > 0.4
    assert r.metrics["num_trades"] == 1


def test_flat_signals_no_trades():
    closes = [100.0] * 50
    r = run_backtest(closes, [0] * 50)
    assert r.metrics["num_trades"] == 0
    assert r.metrics["final_equity"] == r.metrics["initial_cash"]


def test_all_strategies_run():
    d = synthetic(n=250)
    assert isinstance(d, OHLCV)
    for name in STRATEGIES:
        sig = generate(name, d)
        assert len(sig) == len(d)
        r = run_backtest(d.close, sig)
        assert "sharpe" in r.metrics


def test_long_short_parity_of_signals():
    d = synthetic(n=100)
    sig = generate("sma_cross", d, {"fast": 5, "slow": 20})
    assert set(sig) <= {0, 1}
