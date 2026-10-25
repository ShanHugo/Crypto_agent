import json
from decimal import Decimal as D

from crypto_robot.agents.data.quality import check_bars, closed_only, is_closed, missing_ranges
from crypto_robot.agents.data.sync import sync_bars
from crypto_robot.core.models import Bar
from crypto_robot.exchange.max.public import MaxPublicClient
from crypto_robot.store.bars import BarStore
from fakes import FakeSession, k_rows

P = 3_600_000
T0 = 1_700_000_000_000 // P * P


def bar(i, o='100', h='101', l='99', c='100', v='2', ts=None):
    return Bar(T0 + i * P if ts is None else ts, D(o), D(h), D(l), D(c), D(v))


def test_clean_series_ok():
    r = check_bars([bar(i) for i in range(5)], P)
    assert r.ok and r.issues == []


def test_zero_volume_is_not_blocking():
    r = check_bars([bar(0), bar(1, v='0'), bar(2)], P)
    assert r.ok
    assert r.count('zero_volume') == 1


def test_gap_blocks_and_reports_missing_range():
    bars = [bar(0), bar(1), bar(4)]
    r = check_bars(bars, P)
    assert not r.ok
    [g] = r.blocking
    assert g.kind == 'gap' and g.ts == T0 + 2 * P and '2' in g.detail
    assert missing_ranges(bars, P) == [(T0 + 2 * P, T0 + 4 * P)]


def test_duplicate_out_of_order_misaligned():
    r = check_bars([bar(0), bar(0), bar(2), bar(1), bar(0, ts=T0 + 3 * P + 5)], P)
    kinds = {i.kind for i in r.blocking}
    assert {'duplicate', 'out_of_order', 'misaligned'} <= kinds


def test_bad_ohlc_and_jump():
    r = check_bars([bar(0), bar(1, h='98')], P)
    assert [i.kind for i in r.blocking] == ['bad_ohlc']
    r = check_bars([bar(0), bar(1, o='130', h='131', c='130')], P)
    assert [i.kind for i in r.blocking] == ['jump']
    r = check_bars([bar(0), bar(1, o='110', h='111', c='110')], P)   # 10% < 15% 上限
    assert r.ok


def test_is_closed_uses_server_time_and_grace():
    assert not is_closed(T0, P, T0 + P)                 # 剛到整點，還在寬限內
    assert is_closed(T0, P, T0 + P + 10_000)
    assert closed_only([bar(0), bar(1)], P, T0 + 2 * P - 1) == [bar(0)]


def test_bar_store_roundtrip_keeps_decimal_exact(tmp_path):
    store = BarStore(tmp_path / 'bars.db')
    bars = [bar(0, c='100.123456789', l='99'), bar(1)]
    store.upsert('ethtwd', 60, bars)
    store.upsert('ethtwd', 60, [bar(1, c='100.5', h='101')])          # 覆蓋同一根
    assert store.load('ethtwd', 60)[0].close == D('100.123456789')
    assert store.load('ethtwd', 60)[1].close == D('100.5')
    assert store.load('ethtwd', 60, start_ms=T0 + P) == [bar(1, c='100.5', h='101')]
    assert (store.first_ts('ethtwd', 60), store.last_ts('ethtwd', 60)) == (T0, T0 + P)
    assert store.last_ts('btctwd', 60) is None


async def test_sync_writes_only_closed_bars_and_resumes(tmp_path):
    start_s = T0 // 1000
    server_s = start_s + 5 * 3600 + 30          # 第 5 根（index 5）剛開始，未收盤

    def k(params):
        return json.dumps([r for r in k_rows(start_s, 6) if r[0] >= params['timestamp']])

    session = FakeSession({'/api/v3/timestamp': lambda p: json.dumps({'timestamp': server_s}), '/api/v3/k': k})
    store = BarStore(tmp_path / 'bars.db')
    client = MaxPublicClient(session, backoff_sec=0)
    assert await sync_bars(client, store, 'ethtwd', 60, T0) == 5
    assert store.last_ts('ethtwd', 60) == T0 + 4 * P

    server_s += 3600                                  # 一小時後，第 5 根收盤
    assert await sync_bars(client, store, 'ethtwd', 60, T0) == 1
    k_calls = [p for path, p in session.calls if path == '/api/v3/k']
    assert k_calls[-2]['timestamp'] == start_s + 5 * 3600   # 從快取最後一根的下一根開始抓
