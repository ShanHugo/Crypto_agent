from decimal import Decimal as D

import pytest

from crypto_robot.futures_agent import paper as pp
from crypto_robot.futures_agent.paper import Account, Position, Quote, Rule, Target

NOW = 1_800_000_000_000
RULE = Rule(D('0.001'), D(5))


def q(mid, half='0.5', mark=None):
    m = D(str(mid))
    h = D(half)
    return Quote(m - h, m + h, D(str(mark)) if mark is not None else m)


def acct(cash=300, **pos):
    a = Account('main', D(cash))
    for s, (qty, entry) in pos.items():
        a.positions[s] = Position(D(str(qty)), D(str(entry)), D(str(entry)), NOW - 1000)
    return a


def test_fill_buy_at_ask_sell_at_bid_and_fee():
    a = acct()
    t = pp.fill(a, 'A', D(1), q(100), NOW, 'rebalance')
    assert t['price'] == '100.5' and a.positions['A'].entry == D('100.5')
    assert a.cash == D(300) - D('100.5') * D('0.0005')
    a.positions['A'].mark = D(110)
    t2 = pp.close(a, 'A', q(110), NOW, 'rebalance')
    assert t2['price'] == '109.5' and 'A' not in a.positions
    # 賺 (109.5 − 100.5) × 1，手續費兩邊
    assert a.cash == D(300) + D(9) - D('100.5') * D('0.0005') - D('109.5') * D('0.0005')


def test_short_pnl_and_equity():
    a = acct(cash=300, A=(-2, 100))
    a.positions['A'].mark = D(90)
    assert pp.equity(a) == D(320) and pp.gross(a) == D(180)


def test_adding_uses_average_entry_and_partial_close_keeps_entry():
    a = acct(A=(1, 100))
    pp.fill(a, 'A', D(1), Quote(D(119), D(120), D(120)), NOW, 'r')
    assert a.positions['A'].qty == D(2) and a.positions['A'].entry == D(110)
    before = a.positions['A'].entry
    pp.fill(a, 'A', D(-1), Quote(D(130), D(131), D(130)), NOW, 'r')
    assert a.positions['A'].qty == D(1) and a.positions['A'].entry == before


def test_cannot_flip_in_one_fill():
    a = acct(A=(1, 100))
    with pytest.raises(ValueError):
        pp.fill(a, 'A', D(-2), q(100), NOW, 'r')


def test_funding_long_pays_short_receives_and_only_after_open():
    a = acct(cash=0, L=(2, 100), S=(-2, 100))
    assert pp.apply_funding(a, 'L', D('0.01'), D(100), NOW) == D(2)
    assert pp.apply_funding(a, 'S', D('0.01'), D(100), NOW) == D(-2)
    assert a.cash == D(0)
    assert pp.apply_funding(a, 'L', D('0.01'), D(100), NOW) == 0            # 同一次結算不重算
    assert pp.apply_funding(a, 'L', D('0.01'), D(100), NOW - 5000) == 0     # 建倉前的結算不算


def test_hit_stop_both_sides():
    assert pp.hit_stop(Position(D(-1), D(100), D(150), 0)) and not pp.hit_stop(Position(D(-1), D(100), D('149.9'), 0))
    assert pp.hit_stop(Position(D(1), D(100), D(50), 0)) and not pp.hit_stop(Position(D(1), D(100), D('50.1'), 0))


def rules(*syms):
    return {s: RULE for s in syms}


def test_tick_stop_closes_at_market_and_not_reopened_same_week():
    a = acct(cash=300, S=(-1, 100))
    a.week = 7
    r = pp.tick(a, {'S': q(150)}, rules('S'), Target(7, ('X',), ('S',)), NOW)
    assert 'S' not in a.positions and r['trades'][0]['reason'] == 'stop' and r['trades'][0]['price'] == '150.5'
    r2 = pp.tick(a, {'S': q(150)}, rules('S'), Target(7, ('X',), ('S',)), NOW + 60000)
    assert not r2['trades'] and 'S' not in a.positions


def test_reference_account_ignores_stop_and_kill():
    a = acct(cash=100, S=(-1, 100))
    a.week = 7
    r = pp.tick(a, {'S': q(190)}, rules('S'), None, NOW, protect=False)
    assert 'S' in a.positions and not r['trades'] and not a.ended


def test_kill_line_closes_all_and_ends():
    a = acct(cash=300, S=(-2, 100))
    a.week = 1
    r = pp.tick(a, {'S': q(146)}, rules('S'), None, NOW)      # 賠 92，權益 208 ≤ 210
    assert a.ended and not a.positions and r['trades'][0]['reason'] == 'kill'
    assert pp.tick(a, {'S': q(146)}, rules('S'), Target(9, ('A',), ('B',)), NOW + 1)['trades'] == [] and a.week == 1


def test_paused_does_not_trade_but_updates_mark():
    a = acct(cash=300, A=(1, 100))
    r = pp.tick(a, {'A': q(40)}, rules('A'), Target(9, ('B',), ('C',)), NOW, paused=True)
    assert not r['trades'] and a.positions['A'].mark == D(40) and a.week == 0


def test_delisted_position_exits_at_last_mark_double_cost():
    a = acct(cash=300, G=(-1, 100))
    a.week = 1
    a.positions['G'].mark = D(80)
    r = pp.tick(a, {}, {}, None, NOW)
    assert r['trades'][0]['reason'] == 'delisted' and 'G' not in a.positions
    assert a.cash == D(300) + D(20) - D(80) * D('0.0005') * 2


def test_no_quote_but_still_trading_keeps_position():
    a = acct(cash=300, A=(1, 100))
    r = pp.tick(a, {}, rules('A'), None, NOW)
    assert 'A' in a.positions and not r['trades']


def targets():
    return Target(5, ('L1', 'L2'), ('S1', 'S2'))


def book():
    return {s: Quote(D('99.9'), D('100.1'), D(100)) for s in ('L1', 'L2', 'S1', 'S2', 'OLD')}


def test_rebalance_builds_equal_positions_within_1x():
    a = acct()
    r = pp.tick(a, book(), rules('L1', 'L2', 'S1', 'S2'), targets(), NOW)
    assert a.week == 5 and len(r['trades']) == 4
    assert a.positions['L1'].qty == D('0.750') and a.positions['S1'].qty == D('-0.750')    # 300 ÷ 4 ÷ 100
    assert pp.gross(a) <= pp.equity(a)


def test_rebalance_only_once_per_week():
    a = acct()
    pp.tick(a, book(), rules('L1', 'L2', 'S1', 'S2'), targets(), NOW)
    assert not pp.tick(a, book(), rules('L1', 'L2', 'S1', 'S2'), targets(), NOW + 60000)['trades']


def test_rebalance_closes_dropped_first_and_flips_side():
    a = acct(OLD=(1, 100), L1=(-1, 100))
    r = pp.tick(a, book(), rules('L1', 'L2', 'S1', 'S2', 'OLD'), targets(), NOW)
    assert 'OLD' not in a.positions and a.positions['L1'].qty > 0
    assert r['trades'][0]['sym'] == 'OLD' and r['trades'][1]['sym'] == 'L1'


def test_rebalance_skips_small_adjust():
    a = acct(L1=('0.76', 100))                               # 目標 0.75，差 1 USDT
    r = pp.tick(a, book(), rules('L1', 'L2', 'S1', 'S2'), targets(), NOW)
    assert a.positions['L1'].qty == D('0.76') and {'sym': 'L1', 'why': 'small_adjust'} in r['skips']


def test_rebalance_skips_coin_below_min_notional():
    rs = rules('L1', 'L2', 'S1', 'S2') | {'L1': Rule(D('0.001'), D(100))}
    r = pp.tick(acct(), book(), rs, targets(), NOW)
    assert {'sym': 'L1', 'why': 'below_min'} in r['skips']


def test_rebalance_records_missing_quote():
    b = book()
    del b['S2']
    a = acct()
    r = pp.tick(a, b, rules('L1', 'L2', 'S1', 'S2'), targets(), NOW)
    assert {'sym': 'S2', 'why': 'no_quote'} in r['skips'] and 'S2' not in a.positions


def test_rebalance_gross_never_exceeds_equity_with_leftover():
    a = acct(L1=('0.79', 100), L2=('0.79', 100))             # 沒調的部位略大
    pp.tick(a, book(), rules('L1', 'L2', 'S1', 'S2'), targets(), NOW)
    assert pp.gross(a) <= pp.equity(a)


def test_rebalance_rounds_quantity_down_to_step():
    rs = rules('L1', 'L2', 'S1', 'S2') | {'L1': Rule(D('0.1'), D(5))}
    a = acct()
    pp.tick(a, book(), rs, targets(), NOW)
    assert a.positions['L1'].qty == D('0.7')


def test_account_json_roundtrip():
    a = acct(A=('-1.5', '100.25'))
    a.week, a.ended = 3, True
    b = Account.from_json(a.to_json())
    assert (b.cash, b.week, b.ended, b.positions['A'].qty, b.positions['A'].entry) == (a.cash, 3, True, D('-1.5'), D('100.25'))


def test_ledger_roundtrip_and_summary(tmp_path):
    path = tmp_path / 'f.db'
    led = pp.Ledger(path)
    a = led.load('main')
    assert a.cash == pp.INITIAL and not a.positions
    r = pp.tick(a, book(), rules('L1', 'L2', 'S1', 'S2'), targets(), NOW)
    led.save(a, r, NOW, True)
    assert led.load('main').positions['L1'].qty == D('0.750')
    led.close()
    ro = pp.Ledger(path, readonly=True)
    s = pp.summary(ro.equity_rows('main'), ro.equity_rows('reference'), ro.trades('main'), ro.events('main'), ro.load('main'))
    ro.close()
    assert s['weeks'] == 1 and s['trades'] == 4 and s['positions'] == 4 and s['half_spread'] == D('0.001')
    assert '第 1 週' in pp.text(s) and '10.0 bp' in pp.text(s)


def test_text_before_start():
    s = pp.summary([], [], [], [], Account('main'))
    assert '第一週是 2026-11-09' in pp.text(s)


def test_max_drawdown():
    assert pp.max_drawdown([D(100), D(120), D(60), D(90)]) == D('-0.5')
