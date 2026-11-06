import sqlite3
import urllib.error

from crypto_robot import health as hc
from crypto_robot import positioning as pos
from datetime import datetime, timedelta, timezone


def contract(sym, **kw):
    return {'symbol': sym, 'contractType': 'PERPETUAL', 'status': 'TRADING', 'quoteAsset': 'USDT', 'underlyingType': 'COIN', **kw}


def http(code):
    return urllib.error.HTTPError('u', code, 'x', None, None)


def rows(*ts, **kw):
    return [{'symbol': 'A', 'timestamp': t, 'longShortRatio': '1.5', **kw} for t in ts]


def test_top_symbols_filters_and_sorts_by_volume():
    info = {'symbols': [contract('AUSDT'), contract('BUSDT'), contract('CUSDT'), contract('USDCUSDT'),
                        contract('DUSDT', contractType='CURRENT_QUARTER'), contract('EUSDT', status='SETTLING'),
                        contract('FUSDT', underlyingType='INDEX'), contract('GUSDC', quoteAsset='USDC')]}
    tick = [{'symbol': s, 'quoteVolume': str(v)} for s, v in
            [('AUSDT', 1), ('BUSDT', 3), ('CUSDT', 2), ('USDCUSDT', 9), ('DUSDT', 9), ('EUSDT', 9), ('FUSDT', 9), ('GUSDC', 9), ('ZUSDT', 9)]]
    assert pos.top_symbols(info, tick) == ['BUSDT', 'CUSDT', 'AUSDT']
    assert pos.top_symbols(info, tick, 2) == ['BUSDT', 'CUSDT']


def test_add_keeps_first_seen_and_first_value(tmp_path):
    db = tmp_path / 'p.db'
    s = pos.Store(db)
    assert s.add('k', 'A', rows(1, 2), 100) == 2
    assert s.add('k', 'A', rows(2, 3, longShortRatio='9'), 200) == 1    # 2 已有，3 是新的
    assert s.add('other', 'A', rows(2), 300) == 1                       # 不同種類各算各的
    s.close()
    c = sqlite3.connect(db)
    got = c.execute("SELECT ts, first_seen, payload FROM rows WHERE kind = 'k' ORDER BY ts").fetchall()
    c.close()
    assert [g[:2] for g in got] == [(1, 100), (2, 100), (3, 200)]
    assert '"1.5"' in got[1][2] and 'symbol' not in got[1][2] and 'timestamp' not in got[1][2]


def test_add_writes_nothing_when_a_row_is_malformed(tmp_path):
    s = pos.Store(tmp_path / 'p.db')
    try:
        s.add('k', 'A', [{'timestamp': 1}, {'no_time': 1}], 100)
    except KeyError:
        pass
    assert s.db.execute('SELECT COUNT(*) FROM rows').fetchone()[0] == 0
    s.close()


def fake(handler):
    calls = []

    def f(path):
        calls.append(path)
        return handler(path, len(calls))
    return f, calls


def test_collect_asks_every_kind_with_31_days_and_rerun_adds_nothing(tmp_path):
    s = pos.Store(tmp_path / 'p.db')
    f, calls = fake(lambda p, n: rows(1, 2))
    r = pos.collect(f, s, ['AUSDT', 'BUSDT'], 100, pause=0)
    assert r == {'ok': 10, 'failed': 0, 'added': 20, 'stopped': ''}
    assert len(calls) == 10 and all('period=1d&limit=31' in c for c in calls)
    assert {c.split('/')[3].split('?')[0] for c in calls} == set(pos.KINDS)
    assert pos.collect(f, s, ['AUSDT', 'BUSDT'], 200, pause=0)['added'] == 0
    s.close()
    assert pos.last_added(tmp_path / 'p.db') == {'time': 100, 'added': 20, 'failed': 0}


def test_collect_skips_single_failures(tmp_path):
    s = pos.Store(tmp_path / 'p.db')

    def h(p, n):
        if n == 2:
            raise http(400)
        if n == 3:
            raise OSError('timeout')
        if n == 4:
            return {'code': -1}
        if n == 5:
            return [{'bad': 1}]
        return rows(1)
    r = pos.collect(fake(h)[0], s, ['AUSDT', 'BUSDT'], 100, pause=0)
    s.close()
    assert r == {'ok': 6, 'failed': 4, 'added': 6, 'stopped': ''}


def test_collect_stops_at_once_when_rate_limited_and_keeps_what_it_got(tmp_path):
    for code in (429, 418):
        s = pos.Store(tmp_path / f'{code}.db')

        def h(p, n):
            if n == 3:
                raise http(code)
            return rows(1)
        f, calls = fake(h)
        r = pos.collect(f, s, ['AUSDT', 'BUSDT'], 100, pause=0)
        assert len(calls) == 3 and r['stopped'] == f'HTTP {code}' and r['added'] == 2
        s.close()
        assert pos.last_added(tmp_path / f'{code}.db')['added'] == 2


def test_collect_stops_after_many_failures_in_a_row(tmp_path):
    s = pos.Store(tmp_path / 'p.db')

    def h(p, n):
        raise OSError('down')
    f, calls = fake(h)
    r = pos.collect(f, s, ['AUSDT', 'BUSDT', 'CUSDT', 'DUSDT'], 100, pause=0)
    s.close()
    assert len(calls) == pos.MAX_FAILS_IN_ROW and r['stopped'] and r['added'] == 0
    assert pos.last_added(tmp_path / 'p.db') is None


def test_last_added_without_database(tmp_path):
    assert pos.last_added(tmp_path / 'none.db') is None


NOW = datetime(2026, 11, 20, 9, 0)


def last(days):
    t = NOW.replace(tzinfo=timezone(timedelta(hours=8))) - timedelta(days=days)
    return {'time': int(t.timestamp() * 1000), 'added': 500, 'failed': 2}


def test_health_levels_by_age():
    assert hc.check_positioning(NOW, None)[0].level == 'yellow'
    assert hc.check_positioning(NOW, last(1))[0].level == 'ok'
    assert '失敗 2' in hc.check_positioning(NOW, last(1))[0].detail
    assert hc.check_positioning(NOW, last(3))[0].level == 'ok'
    assert hc.check_positioning(NOW, last(4))[0].level == 'yellow'
    assert hc.check_positioning(NOW, last(20))[0].level == 'yellow'
    red = hc.check_positioning(NOW, last(21))[0]
    assert red.level == 'red' and red.hint and '不影響' in red.hint


def test_collect_quotes_non_ascii_symbols(tmp_path):
    s = pos.Store(tmp_path / 'p.db')
    f, calls = fake(lambda p, n: rows(1))
    pos.collect(f, s, ['龙虾USDT'], 100, pause=0)
    assert all(c.isascii() and 'symbol=%E9%BE%99%E8%99%BEUSDT&' in c for c in calls)
    got = s.db.execute('SELECT DISTINCT symbol FROM rows').fetchall()
    s.close()
    assert got == [('龙虾USDT',)]
