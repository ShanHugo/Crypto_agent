import importlib.util
from datetime import datetime
from pathlib import Path

spec = importlib.util.spec_from_file_location('devcheck', Path(__file__).resolve().parents[1] / 'scripts' / 'devcheck.py')
dc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dc)


def test_health_lists_only_non_ok_checks():
    agents = [{'key': 'a', 'checks': [{'level': 'ok', 'name': 'x', 'detail': 'fine'},
                                      {'level': 'red', 'name': 'y', 'detail': '壞了'}]}]
    assert dc.health_problems(agents) == [('red', 'a：y，壞了')]


def test_tests_pass_and_fail():
    assert dc.test_result(0, '....\n209 passed in 2.08s\n') == ('209 passed', [])
    out = 'F.\nFAILED tests/t.py::test_x - assert 1 == 2\n1 failed, 1 passed in 0.1s\n'
    summary, p = dc.test_result(1, out)
    assert summary == '1 failed, 1 passed'
    assert p == [('red', '測試沒過：FAILED tests/t.py::test_x')]


def test_tasks():
    lines = ['futures_paper|Running|2147946720', 'kronos|Ready|0', 'new|Ready|267011',
             'old|Disabled|0', 'bad|Ready|1', 'stuck|Ready|2147946720']
    assert dc.task_problems(lines) == [('yellow', '排程 old 已停用'),
                                       ('red', '排程 bad 上次結果 1（狀態 Ready）'),
                                       ('red', '排程 stuck 上次結果 2147946720（狀態 Ready）')]


def test_git():
    assert dc.git_problems('', 0) == []
    assert dc.git_problems(' M docs/a.md\n?? b.py\n', 2) == [
        ('yellow', 'git 有 2 個檔案沒 commit：docs/a.md、b.py'), ('yellow', 'git 有 2 個 commit 沒 push')]
    assert dc.git_problems('', None)[0][0] == 'yellow'


def test_summary_red_first_and_capped():
    now = datetime(2026, 11, 8, 9, 10)
    assert dc.summarize(now, 'Agent 8/8 正常', '209 passed', 5, []) == [
        'devcheck 2026-11-08 09:10', '🔴 0  🟡 0  Agent 8/8 正常  測試 209 passed  排程 5 個  全部正常']
    probs = [('yellow', f'y{i}') for i in range(13)] + [('red', 'r')]
    lines = dc.summarize(now, 'A', 'T', 5, probs)
    assert lines[1].startswith('🔴 1  🟡 13')
    assert lines[2] == '🔴 r'
    assert lines[-1] == '…還有 2 項'
