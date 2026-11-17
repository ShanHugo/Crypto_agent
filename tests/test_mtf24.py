from crypto_robot.forecast import mtf24


def _f(up, n=30):
    return {'up': up, 'n': n, 'mean': 0.0, 'last': 1.0}


def test_closed_drops_open_bar_and_caps():
    rows = [[i, 1, 1, 1, 1, 1, i + 9] for i in range(0, 6000, 10)]
    out = mtf24.closed(rows, 5995)
    assert out[-1][6] < 5995 and out[-1][0] == 5980 and len(out) == mtf24.LOOKBACK


def test_model_req_needs_min_bars_and_future_times():
    rows = [[i * 60, 1, 2, 0.5, 1.5, 10, i * 60 + 59] for i in range(40)]
    assert mtf24.model_req(rows[:10], 60, 3, 1) is None
    r = mtf24.model_req(rows, 60, 3, 1)
    assert r['y_ms'] == [39 * 60 + 60, 39 * 60 + 120, 39 * 60 + 180] and len(r['close']) == 40


def test_combine_thresholds_and_week():
    base = {k: _f(20) for k in ('1d', '4h', '1h', '15m', '5m')}
    assert mtf24.combine({**base, '1w': _f(15)})['call'] == 1
    assert mtf24.combine({**base, '1w': _f(10)})['against_week'] is True
    low = {k: _f(9) for k in base}
    c = mtf24.combine({**low, '1w': None})
    assert c['call'] == -1 and c['against_week'] is False
    assert mtf24.combine({**{k: _f(15) for k in base}, '1w': _f(30)})['call'] == 0
    assert mtf24.combine({'1w': _f(30)})['score'] is None


def test_settle_and_summary():
    up = mtf24.settle({'t': 0, 'sym': 'X', 'call': 1, 'price': 100.0}, 103.0)
    dn = mtf24.settle({'t': 1, 'sym': 'X', 'call': -1, 'price': 100.0}, 103.0)
    na = mtf24.settle({'t': 2, 'sym': 'X', 'call': 0, 'price': 100.0}, 99.0)
    assert up['hit'] is True and dn['hit'] is False and na['hit'] is None
    s = mtf24.summary([up, dn, na])
    assert s['n'] == 3 and s['called'] == 2 and s['hit'] == 0.5 and s['long'] == {'n': 1, 'hit': 1.0}
