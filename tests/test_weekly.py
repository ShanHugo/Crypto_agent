import json
from datetime import date, datetime
from decimal import Decimal

from crypto_robot import health as hc
from crypto_robot import weekly


def run(day, status, before, after=None, eth='100', usdt='30', weights_after=None):
    return {'date': day, 'status': status, 'note': '', 'equity_before': before, 'equity_after': after,
            'prices': json.dumps({'eth': eth, 'usdt': usdt}),
            'weights_after': json.dumps(weights_after) if weights_after else None, 'weights_before': None}


W = {'eth': '0.5', 'usdt': '0.375', 'twd': '0.125'}
RUNS = [
    run('2026-10-27', 'done', '6000', '5990', weights_after=W),
    run('2026-10-28', 'hold', '6100', '6100', eth='104'),
    run('2026-10-29', 'blocked', '6050', None, eth='102'),
    run('2026-11-02', 'hold', '5800', '5800', eth='90', usdt='30'),
]
ORDERS = [
    {'date': '2026-10-27', 'asset': 'eth', 'side': 'buy', 'volume': '30', 'executed': '30', 'avg_price': '100.5', 'status': 'done'},
    {'date': '2026-10-27', 'asset': 'usdt', 'side': 'buy', 'volume': '70', 'executed': '70', 'avg_price': '30', 'status': 'done'},
]
PAPER = [{'date': '2026-10-26', 'equity': '6000'}, {'date': '2026-10-27', 'equity': '6000'},
         {'date': '2026-11-02', 'equity': '5850'}]
PAPER_TRADES = [{'date': '2026-10-27', 'asset': 'eth', 'side': 'buy', 'avg_price': '100.4'}]


def test_build_compares_from_first_trading_day():
    r = weekly.build(date(2026, 11, 2), RUNS, ORDERS, PAPER, PAPER_TRADES)
    assert r['live_start'] == date(2026, 10, 27)
    assert r['live']['since_deposit'] == Decimal('5800') / Decimal('6000') - 1
    c = r['compare']
    assert c['真單'] == Decimal('5800') / Decimal('5990') - 1
    assert c['主模擬帳戶'] == Decimal('5850') / Decimal('6000') - 1
    assert c['一直持有 ETH'] == Decimal('-0.1')
    # ETH 50% 跌 10%、USDT 不變、台幣不變
    assert c['同比例不再平衡'] == Decimal('-0.05')
    assert r['gap'] == c['真單'] - c['主模擬帳戶']
    assert r['drawdown'] == Decimal('5800') / Decimal('6100') - 1


def test_build_week_ops_and_fills():
    r = weekly.build(date(2026, 11, 2), RUNS, ORDERS, PAPER, PAPER_TRADES)
    ops = r['ops']
    assert ops['days'] == 7                     # 10/27～11/02
    assert ops['ok'] == 3 and ops['trade_days'] == 1
    assert '2026-10-29 blocked' in ops['problems'] and '2026-10-30 missing' in ops['problems']
    eth = next(f for f in r['fills'] if f['asset'] == 'eth')
    assert eth['slip_bp'] == Decimal('50')       # 100.5 vs 中間價 100
    assert round(eth['vs_paper_bp'], 4) == round((Decimal('100.5') / Decimal('100.4') - 1) * 10000, 4)
    usdt = next(f for f in r['fills'] if f['asset'] == 'usdt')
    assert usdt['vs_paper_bp'] is None
    assert r['gates']['days'] == 4 and r['gates']['problems'] == 1


def test_week_change_uses_last_value_before_the_week():
    r = weekly.build(date(2026, 11, 9), RUNS + [run('2026-11-09', 'hold', '6380', '6380', eth='110')], ORDERS, PAPER, [])
    assert r['week']['live'] == Decimal('6380') / Decimal('5800') - 1
    assert r['fills'] == []


def test_text_without_live_runs():
    t = weekly.text(weekly.build(date(2026, 11, 2), [], [], PAPER, []))
    assert '還沒有' in t


def test_text_has_no_advice_words():
    t = weekly.text(weekly.build(date(2026, 11, 2), RUNS, ORDERS, PAPER, PAPER_TRADES))
    assert '真單第 4 天' in t and '主模擬帳戶' in t
    for word in ('建議', '應該', '買進', '加碼'):
        assert word not in t.replace('不給建議', '')


def test_max_drawdown():
    assert weekly.max_drawdown([Decimal(x) for x in (100, 120, 90, 130)]) == Decimal(90) / Decimal(120) - 1
    assert weekly.max_drawdown([Decimal(1)]) == 0


def test_check_weekly():
    assert hc.check_weekly(datetime(2026, 10, 30, 9, 0), set())[0].level == 'ok'
    assert hc.check_weekly(datetime(2026, 11, 2, 8, 0), set())[0].level == 'ok'          # 週一 08:35 以前
    assert hc.check_weekly(datetime(2026, 11, 2, 9, 0), set())[0].level == 'red'
    assert hc.check_weekly(datetime(2026, 11, 5, 9, 0), {date(2026, 11, 2)})[0].level == 'ok'
    assert hc.check_weekly(datetime(2026, 11, 9, 8, 0), {date(2026, 11, 2)})[0].level == 'ok'
