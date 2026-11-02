from decimal import Decimal as D

import pytest

from crypto_robot.core.models import Bar
from crypto_robot.lab import kronos3h as kr
from crypto_robot.lab.direction3h import FEE, H, Series


def series(n_after=60, gap_at=None):
    start = kr.START - kr.LOOKBACK * H
    bars = []
    for i in range(kr.LOOKBACK + n_after):
        ts = start + i * H
        if ts == gap_at:
            continue
        p = D(100 + i % 7)
        bars.append(Bar(ts, p, p, p, p, D(1)))
    return Series.of(bars)


def test_direction_needs_more_than_fee():
    assert kr.direction(FEE + 1e-6) == 1
    assert kr.direction(-FEE - 1e-6) == -1
    assert kr.direction(FEE) == 0 and kr.direction(0.0) == 0


def test_exam_slots_need_full_history_and_future():
    eth = series()
    ss = kr.exam_slots(eth)
    assert ss[0] == kr.START + H                        # 2025-09-01 01:00 UTC＝台北 09:00
    assert all(s // H % 3 == 1 for s in ss)
    assert ss[-1] + 2 * H <= kr.START - kr.LOOKBACK * H + (kr.LOOKBACK + 59) * H
    gap = series(gap_at=kr.START - 100 * H)              # 第一個時點的 512 根裡缺一根 → 跳過
    assert kr.START + H not in kr.exam_slots(gap)


def test_rows_use_close_before_slot_and_3h_later():
    eth = series()
    s = kr.START + H
    rows = kr.rows(eth, {s: 0.01})
    assert len(rows) == 1
    i = eth.idx[s - H]
    assert rows[0] == (s, eth.c[i + 3] / eth.c[i] - 1, {'kronos': 1})


def test_batches_fixed_size():
    assert [len(b) for b in kr.batches(list(range(70)))] == [32, 32, 6]


def test_run_exam_refuses_missing_and_rerun(tmp_path):
    eth = {'binance': series()}
    with pytest.raises(RuntimeError, match='缺'):
        kr.run_exam(eth, {'binance': {}}, tmp_path / 'h.json', 'abc')
    preds = {'binance': {s: 0.01 for s in kr.exam_slots(eth['binance'])}}
    rec = kr.run_exam(eth, preds, tmp_path / 'h.json', 'abc')
    assert rec['passed'] is False and rec['datasets']['binance']['n'] < kr.MIN_N
    with pytest.raises(RuntimeError, match='考過'):
        kr.run_exam(eth, preds, tmp_path / 'h.json', 'abc')


def test_passed_thresholds():
    ok = {'n': 100, 'hit': 0.53, 't': 2.0, 'long_net': 0.001, 'rand95': 0.0}
    assert kr.passed(ok)
    for k, v in [('n', 99), ('hit', 0.52), ('t', 1.99), ('long_net', 0.0)]:
        assert not kr.passed({**ok, k: v})
    assert not kr.passed({**ok, 'rand95': 0.002})
