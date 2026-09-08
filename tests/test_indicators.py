from godquant.quant import indicators as I


def test_sma_alignment():
    out = I.sma([1, 2, 3, 4, 5], 3)
    assert out == [None, None, 2.0, 3.0, 4.0]


def test_ema_warmup():
    out = I.ema([1, 2, 3, 4, 5], 3)
    assert out[0] is None and out[1] is None and out[2] == 2.0


def test_rsi_bounds():
    closes = [10 + i * 0.5 for i in range(40)]
    out = [x for x in I.rsi(closes) if x is not None]
    assert out and all(0 <= x <= 100 for x in out)


def test_macd_keys():
    closes = [float(i) for i in range(60)]
    m = I.macd(closes)
    assert set(m) == {"macd", "signal", "hist"}
    assert len(m["macd"]) == 60


def test_bollinger_bands_order():
    closes = [100 + (i % 7) for i in range(50)]
    b = I.bollinger(closes)
    for i in range(50):
        if b["upper"][i] is not None:
            assert b["lower"][i] <= b["mid"][i] <= b["upper"][i]


def test_sharpe_zero_on_flat():
    assert I.sharpe([0.0] * 20) == 0.0


def test_max_drawdown():
    m = I.max_drawdown([100, 120, 90, 110])
    assert abs(m["max_drawdown"] - 0.25) < 1e-9
