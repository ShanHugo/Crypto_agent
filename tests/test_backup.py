import json
import sqlite3
import zipfile
from datetime import datetime

from crypto_robot import backup
from crypto_robot import health as hc

NOW = datetime(2026, 10, 27, 8, 31)


def project(tmp_path):
    root = tmp_path / 'proj'
    (root / 'state' / 'live').mkdir(parents=True)
    (root / 'state' / 'research').mkdir()
    (root / 'reports').mkdir()
    db = sqlite3.connect(root / 'state' / 'paper_main.db')
    db.execute('CREATE TABLE t (x)')
    db.execute('INSERT INTO t VALUES (42)')
    db.commit()                                   # 刻意不關閉：模擬有程式開著資料庫
    (root / 'state' / 'research' / 'holdout.json').write_text('{}', encoding='utf-8')
    (root / 'reports' / 'r.md').write_text('# r', encoding='utf-8')
    (root / '.env').write_text('MAX_API_KEY=secret', encoding='utf-8')
    (root / 'logs').mkdir()
    (root / 'logs' / 'a.log').write_text('x', encoding='utf-8')
    return root, db


def test_backup_contents_restore_and_status(tmp_path):
    root, db = project(tmp_path)
    dest = tmp_path / 'drive'
    r = backup.run(root, NOW, [dest])
    db.close()
    assert r['destinations'][0]['ok']
    z = zipfile.ZipFile(dest / 'crypto_robot_20261027_0831.zip')
    names = set(z.namelist())
    assert {'state/paper_main.db', 'state/research/holdout.json', 'reports/r.md'} <= names
    assert not any(n.endswith('.env') or n.startswith('logs/') for n in names)
    z.extract('state/paper_main.db', tmp_path / 'restore')
    assert sqlite3.connect(tmp_path / 'restore' / 'state' / 'paper_main.db').execute('SELECT x FROM t').fetchone() == (42,)
    assert json.loads((root / 'state' / 'backup' / 'last.json').read_text(encoding='utf-8'))['destinations'][0]['ok']


def test_prune_keeps_latest(tmp_path):
    for i in range(5):
        (tmp_path / f'crypto_robot_2026102{i}_0830.zip').write_text('x')
    assert backup.prune(tmp_path, keep=2) == 3
    assert sorted(p.name for p in tmp_path.iterdir()) == ['crypto_robot_20261023_0830.zip', 'crypto_robot_20261024_0830.zip']


def test_failures_are_recorded(tmp_path):
    root, db = project(tmp_path)
    blocker = tmp_path / 'not_a_dir'
    blocker.write_text('x')                       # 目的地是檔案 → 建資料夾失敗
    r = backup.run(root, NOW, [blocker, tmp_path / 'ok'])
    db.close()
    assert [d['ok'] for d in r['destinations']] == [False, True]
    assert backup.run(root, NOW, [])['destinations'][0]['ok'] is False
    assert backup.destinations(r'G:\a; D:\b ;') == [backup.Path(r'G:\a'), backup.Path(r'D:\b')]


def test_check_backup_levels():
    ok = {'time': '2026-10-27T08:31:00', 'destinations': [{'dir': 'G:', 'ok': True, 'file': 'f.zip', 'size': 13000}]}
    bad = {'time': '2026-10-27T08:31:00', 'destinations': [{'dir': 'G:', 'ok': False, 'error': '找不到路徑'}]}
    assert hc.check_backup(NOW, ok, True)[0].level == 'ok'
    assert hc.check_backup(NOW, bad, True)[0].level == 'red'
    assert hc.check_backup(datetime(2026, 11, 2), ok, True)[0].level == 'red'
    assert hc.check_backup(NOW, None, True)[0].level == 'yellow'
    assert hc.check_backup(NOW, None, False)[0].level == 'yellow'
