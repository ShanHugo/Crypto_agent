import json
from datetime import datetime
from decimal import Decimal as D

from crypto_robot import health as hc
from crypto_robot.live import readonly
from fakes import FakeSession

INFO = json.dumps({'email': 'x@example.com', 'level': 2, 'current_vip_level': {'level': 0, 'maker_fee': 0.0008, 'taker_fee': 0.0016}})
ACCOUNTS = json.dumps([{'currency': 'twd', 'balance': '3000', 'locked': '0'}, {'currency': 'eth', 'balance': '0.02', 'locked': '0.01'},
                       {'currency': 'max', 'balance': '5', 'locked': '0'}])
PRICES = {'eth': D(80000), 'usdt': D('32.5')}


def session(ip='1.2.3.4', info=INFO):
    return FakeSession({'https://api.ipify.org': ip, '/api/v3/info': info, '/api/v3/wallet/spot/accounts': ACCOUNTS})


def test_valuation_counts_locked_and_skips_unpriced():
    bal = {'twd': {'balance': D(3000), 'locked': D(0)}, 'eth': {'balance': D('0.02'), 'locked': D('0.01')},
           'max': {'balance': D(5), 'locked': D(0)}}
    assert readonly.valuation(bal, PRICES) == D(3000) + D('0.03') * 80000


async def test_snapshot_ok_and_summary_has_no_ip():
    rec = await readonly.read_snapshot(session(), 'K' * 40, 'S' * 40, 1_700_000_000_000, PRICES, ref_ip='1.2.3.4')
    assert rec['ok'] and not rec['ip_changed'] and rec['ip'] == '1.2.3.4'
    assert D(rec['equity']) == D(5400) and rec['other'] == ['max'] and 'max' not in rec['balances']
    text = readonly.summary(rec)
    assert '5,400' in text and '1.2.3.4' not in text


async def test_snapshot_records_auth_failure_and_ip_change():
    rec = await readonly.read_snapshot(session(ip='5.6.7.8', info=(401, '{"error":{"code":2001}}')), 'K' * 40, 'S' * 40,
                                       1_700_000_000_000, PRICES, ref_ip='1.2.3.4')
    assert not rec['ok'] and rec['ip_changed'] and '401' in rec['error']
    assert '白名單' in readonly.summary(rec) and '5.6.7.8' not in readonly.summary(rec)


def test_records_and_ref_ip_files(tmp_path):
    readonly.append(tmp_path / 'b.jsonl', {'a': 1})
    readonly.append(tmp_path / 'b.jsonl', {'a': 2})
    assert readonly.load(tmp_path / 'b.jsonl') == [{'a': 1}, {'a': 2}]
    assert readonly.ref_ip(tmp_path / 'ip.json') is None
    readonly.set_ref_ip(tmp_path / 'ip.json', '1.2.3.4')
    assert readonly.ref_ip(tmp_path / 'ip.json') == '1.2.3.4'


def rec(date, ok=True, ip_changed=False, source='scheduled'):
    return {'date': date, 'time': '01:36', 'source': source, 'ok': ok, 'ip_changed': ip_changed,
            'error': None if ok else 'HTTP 401', 'equity': '6000'}


def lv(checks):
    return {c.name: c.level for c in checks}


def test_check_live_levels():
    now = datetime(2026, 10, 28, 9, 0)
    assert lv(hc.check_live(now, [], False, False)) == {'金鑰': 'ok'}
    assert lv(hc.check_live(now, [], True, False))['白名單 IP'] == 'yellow'
    assert lv(hc.check_live(now, [rec('2026-10-27', source='manual')], True, True))['今天凌晨讀取'] == 'ok'   # 排程還沒讀過
    ok = lv(hc.check_live(now, [rec('2026-10-28')], True, True))
    assert set(ok.values()) == {'ok'}
    missed = lv(hc.check_live(now, [rec('2026-10-27')], True, True))
    assert missed['今天凌晨讀取'] == 'yellow'
    bad = lv(hc.check_live(now, [rec('2026-10-28', ok=False, ip_changed=True)], True, True))
    assert bad['公開 IP'] == 'red' and bad['真實餘額'] == 'red'
