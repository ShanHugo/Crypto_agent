from datetime import date, datetime

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


def test_agent_and_overall_levels():
    a = hc.agent('k', 'L', [hc.Check('x', 'ok', ''), hc.Check('y', 'yellow', '')])
    b = hc.agent('k2', 'L2', [hc.Check('z', 'red', '')])
    assert a['level'] == 'yellow' and hc.overall([a, b]) == 'red' and hc.overall([]) == 'ok'
