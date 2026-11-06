from datetime import date, datetime, timedelta, timezone

from crypto_robot import health as hc

LOG = """===== 2026/10/26 週一  1:35:40.12
2026-10-26 已經執行過，不重複執行
----- exit 0
===== 2026/10/27 週二  1:35:41.00
Traceback (most recent call last):
RuntimeError: boom
----- exit 1""".splitlines()


def snaps(*days, note=''):
    return [{'date': d, 'action': 'hold', 'note': note} for d in days]


def levels(checks):
    return {c.name: c.level for c in checks}


def test_parse_runs_reads_date_time_and_exit():
    runs = hc.parse_runs(LOG)
    assert [(r['date'], r['time'], r['exit']) for r in runs] == [
        (date(2026, 10, 26), (1, 35), 0), (date(2026, 10, 27), (1, 35), 1)]
    assert runs[1]['lines'][-1] == 'RuntimeError: boom'


def test_expected_date_before_and_after_schedule():
    assert hc.expected_date(datetime(2026, 10, 27, 1, 39), hc.PAPER_AT) == date(2026, 10, 26)
    assert hc.expected_date(datetime(2026, 10, 27, 1, 40), hc.PAPER_AT) == date(2026, 10, 27)


def test_paper_ok_missed_and_failed():
    now = datetime(2026, 10, 28, 9, 0)
    ok = hc.check_paper(now, snaps('2026-10-26', '2026-10-27', '2026-10-28'), hc.parse_runs(LOG[:3]), False)
    assert set(levels(ok).values()) == {'ok'}
    bad = levels(hc.check_paper(now, snaps('2026-10-25', '2026-10-26'), hc.parse_runs(LOG), False))
    assert bad == {'今天凌晨執行': 'red', '最近 7 天': 'yellow', '上次排程': 'red'}
    paused = levels(hc.check_paper(now, snaps('2026-10-26'), [], True))
    assert paused['今天凌晨執行'] == 'yellow'
    noted = hc.check_paper(now, snaps('2026-10-28', note='盤口深度不足，只部分成交'), [], False)
    assert levels(noted)['最新紀錄備註'] == 'yellow'
    assert hc.check_paper(now, [], [], False)[0].level == 'yellow'


RETRY_LOG = """===== 2026/10/28  1:35:40.00
RuntimeError: MAX 維護中
----- exit 1
===== 2026/10/28  2:35:40.00
ConnectionError: timeout
----- exit 1
===== 2026/10/28  3:35:41.00
動作：不動作
----- exit 0""".splitlines()


def test_attempts_text_lists_failed_times_and_last_error():
    runs = hc.parse_runs(RETRY_LOG)
    assert hc.attempts_text(runs, date(2026, 10, 28)) == '01:35、02:35 執行失敗（ConnectionError: timeout）'
    assert hc.attempts_text(runs, date(2026, 10, 27)) == ''


def test_paper_retrying_recovered_and_whole_window_failed():
    runs = hc.parse_runs(RETRY_LOG[:6])                     # 只有兩次失敗
    retrying = levels(hc.check_paper(datetime(2026, 10, 28, 3, 0), snaps('2026-10-27'), runs, False))
    assert retrying['今天凌晨執行'] == 'yellow' and retrying['上次排程'] == 'yellow'
    failed = hc.check_paper(datetime(2026, 10, 28, 8, 5), snaps('2026-10-27'), runs, False)
    assert levels(failed)['今天凌晨執行'] == 'red' and '01:35、02:35' in failed[0].detail
    recovered = hc.check_paper(datetime(2026, 10, 28, 9, 0), snaps('2026-10-27', '2026-10-28'), hc.parse_runs(RETRY_LOG), False)
    assert levels(recovered)['今天凌晨執行'] == 'yellow' and '重試後成功' in recovered[0].detail
    assert levels(recovered)['上次排程'] == 'ok'


def test_report_ignores_manual_runs_before_0830():
    log = ['===== 2026/10/26 週一  1:56:10.61', 'preview', '----- exit 0']
    now = datetime(2026, 10, 26, 10, 0)
    assert levels(hc.check_report(now, hc.parse_runs(log), True, date(2026, 10, 25)))['今天的報告'] == 'red'
    log += ['===== 2026/10/26 週一  8:30:31.00', 'sent', '----- exit 0']
    assert levels(hc.check_report(now, hc.parse_runs(log), True, date(2026, 10, 25)))['今天的報告'] == 'ok'
    assert levels(hc.check_report(now, [], False, date(2026, 10, 26)))['Discord'] == 'red'
    assert levels(hc.check_report(datetime(2026, 10, 26, 8, 0), [], True, date(2026, 10, 26)))['今天的報告'] == 'ok'


def test_system_flags_tracked_env_and_leaked_webhook():
    now = datetime(2026, 10, 26, 10, 0)
    checks = hc.check_system(True, {'a': 'https://discord.com/api/webhooks/1/x'}, {'variant': 'v', 'passed': True}, None, now)
    assert levels(checks)['.env 沒進 git'] == 'red' and levels(checks)['紀錄檔沒有 webhook'] == 'red'
    clean = hc.check_system(False, {'a': 'ok'}, None, datetime(2026, 10, 20), now)
    assert set(levels(clean).values()) == {'ok'}


def test_stray_secret_files_flagged():
    names = ['MAX API.txt', 'requirements.txt', 'README.md', 'CLAUDE.md', '.env', '.env.example', 'my_secret.json', 'notes.txt']
    assert hc.stray_secret_files(names) == ['MAX API.txt', 'my_secret.json']
    now = datetime(2026, 10, 26, 10, 0)
    assert levels(hc.check_system(False, {}, None, None, now, ['MAX API.txt']))['專案資料夾沒有金鑰檔'] == 'red'
    assert levels(hc.check_system(False, {}, None, None, now, []))['專案資料夾沒有金鑰檔'] == 'ok'


def test_agent_and_overall_levels():
    a = hc.agent('k', 'L', [hc.Check('x', 'ok', ''), hc.Check('y', 'yellow', '')])
    b = hc.agent('k2', 'L2', [hc.Check('z', 'red', '')])
    assert a['level'] == 'yellow' and hc.overall([a, b]) == 'red' and hc.overall([]) == 'ok'


def test_check_kronos():
    d = date
    before = hc.check_kronos(datetime(2026, 11, 3, 7, 0), set())          # 08:00 前看前一天，還沒開始
    assert levels(before) == {'Kronos 預測': 'ok'}
    assert levels(hc.check_kronos(datetime(2026, 11, 3, 9, 0), {d(2026, 11, 3)})) == {'Kronos 預測': 'ok'}
    assert levels(hc.check_kronos(datetime(2026, 11, 3, 9, 0), set())) == {'Kronos 預測': 'yellow'}
    assert levels(hc.check_kronos(datetime(2026, 11, 5, 9, 0), {d(2026, 11, 3)})) == {'Kronos 預測': 'yellow'}
    assert levels(hc.check_kronos(datetime(2026, 11, 6, 9, 0), {d(2026, 11, 3)})) == {'Kronos 預測': 'red'}


def _beat(now, **kw):
    ms = int(now.replace(tzinfo=timezone(timedelta(hours=8))).timestamp() * 1000)
    return {'time': ms, 'last_quote_ms': ms, 'error': None, 'equity': '300', 'positions': 20} | kw


def _monday(y=2026, m=11, d=9):
    return int(datetime(y, m, d, tzinfo=timezone.utc).timestamp() * 1000)


def test_futures_paper_before_first_week_is_ok():
    assert [c.level for c in hc.check_futures_paper(datetime(2026, 11, 8, 12), None, None)] == ['ok']


def test_futures_paper_no_heartbeat_is_red_with_known_hint():
    c = hc.check_futures_paper(datetime(2026, 11, 9, 12), None, None)[0]
    assert c.level == 'red' and '不影響真單' in c.hint


def test_futures_paper_stale_heartbeat_red_and_bug_goes_to_developer():
    now = datetime(2026, 11, 10, 12)
    stale = _beat(now - timedelta(hours=3))
    assert hc.check_futures_paper(now, stale, _monday())[0].level == 'red'
    bug = hc.check_futures_paper(now, _beat(now, error='KeyError', kind='bug'), _monday())[0]
    assert bug.level == 'red' and '開發者' in bug.hint


def test_futures_paper_network_error_yellow_then_red_after_2h():
    now = datetime(2026, 11, 10, 12)
    ms = _beat(now)['time']
    assert hc.check_futures_paper(now, _beat(now, error='連線', kind='network', last_quote_ms=ms - 3_600_000), _monday())[0].level == 'yellow'
    assert hc.check_futures_paper(now, _beat(now, error='連線', kind='network', last_quote_ms=ms - 3 * 3_600_000), _monday())[0].level == 'red'


def test_futures_paper_weekly_target():
    now = datetime(2026, 11, 9, 8, 30)
    assert hc.check_futures_paper(now, _beat(now), None)[-1].level == 'ok'             # 週一 09:00 前還可以等
    now = datetime(2026, 11, 9, 10)
    assert hc.check_futures_paper(now, _beat(now), None)[-1].level == 'yellow'
    now = datetime(2026, 11, 10, 10)
    assert hc.check_futures_paper(now, _beat(now), None)[-1].level == 'red'
    assert hc.check_futures_paper(now, _beat(now), _monday())[-1].level == 'ok'


def test_futures_paper_ended_and_paused_are_yellow():
    now = datetime(2026, 11, 10, 10)
    levels = {c.name: c.level for c in hc.check_futures_paper(now, _beat(now, ended=True), _monday())}
    assert levels['模擬狀態'] == 'yellow'
    levels = {c.name: c.level for c in hc.check_futures_paper(now, _beat(now, paused=True), _monday())}
    assert levels['模擬狀態'] == 'yellow'
