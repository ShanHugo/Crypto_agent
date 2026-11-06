"""備份無法重建的資料：state/（模擬帳戶、影子帳戶、真單紀錄、真實帳戶讀取紀錄、考卷結果）與 reports/。

不備份：.env（金鑰、webhook 等同密碼）、logs/、data/（K 線快取，可重新下載）、*.lock（常駐程序鎖著，讀了會失敗）、*.tmp（寫到一半的暫存檔）。
SQLite 檔用 SQLite 自己的 backup API 先做一致的快照再壓縮，避免剛好有程式在寫入時備到半個檔。
目的地：.env 的 BACKUP_DIRS（多個用 ; 分隔）；每個目的地保留最近 KEEP 份；寫完重新開檔驗證、確認關鍵檔都在。
結果記在 state/backup/last.json，健康檢查會看。
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

INCLUDE = ['state', 'reports']
KEY_FILES = ['state/paper_main.db', 'state/live/live.db', 'state/research/holdout.json']   # 存在就一定要在備份裡
KEEP = 40
PREFIX = 'crypto_robot_'
SKIP_SUFFIXES = ('-wal', '-shm', '-journal', '.lock', '.tmp')
STATUS = Path('state') / 'backup' / 'last.json'


def destinations(raw: str | None = None) -> list[Path]:
    raw = (os.getenv('BACKUP_DIRS', '') if raw is None else raw).strip()
    return [Path(p.strip()) for p in raw.split(';') if p.strip()]


def files_to_backup(root: Path) -> list[Path]:
    out = []
    for name in INCLUDE:
        p = root / name
        if p.is_file():
            out.append(p)
        elif p.is_dir():
            out += [f for f in p.rglob('*') if f.is_file() and not f.name.endswith(SKIP_SUFFIXES)
                    and f.relative_to(root).as_posix() != STATUS.as_posix()]
    return sorted(out)


def _snapshot_db(src: Path, tmpdir: Path) -> Path:
    dst = tmpdir / src.name
    s = sqlite3.connect(f'file:{src.as_posix()}?mode=ro', uri=True)
    d = sqlite3.connect(dst)
    try:
        s.backup(d)
    finally:                     # sqlite3 的 with 只會 commit，不會關閉；Windows 上沒關就刪不掉暫存檔
        d.close()
        s.close()
    return dst


def make_zip(root: Path, dest: Path, stamp: str) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    final = dest / f'{PREFIX}{stamp}.zip'
    tmp = final.with_suffix('.zip.tmp')
    with tempfile.TemporaryDirectory() as td, zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as z:
        for i, f in enumerate(files_to_backup(root)):
            src = f
            if f.suffix == '.db':
                snap_dir = Path(td) / str(i)
                snap_dir.mkdir()
                src = _snapshot_db(f, snap_dir)
            z.write(src, f.relative_to(root).as_posix())
    with zipfile.ZipFile(tmp) as z:
        bad = z.testzip()
        if bad:
            raise RuntimeError(f'壓縮檔損壞：{bad}')
        names = set(z.namelist())
    missing = [k for k in KEY_FILES if (root / k).exists() and k not in names]
    if missing:
        raise RuntimeError(f'關鍵檔沒有被備份：{missing}')
    if '.env' in names:
        raise RuntimeError('.env 不能進備份')
    tmp.replace(final)
    return final


def prune(dest: Path, keep: int = KEEP) -> int:
    old = sorted(dest.glob(f'{PREFIX}*.zip'))[:-keep]
    for p in old:
        p.unlink()
    return len(old)


def run(root: Path, now: datetime, dirs: list[Path]) -> dict:
    stamp = now.strftime('%Y%m%d_%H%M')
    result = {'time': now.isoformat(timespec='seconds'), 'files': len(files_to_backup(root)), 'destinations': []}
    if not dirs:
        result['destinations'].append({'dir': '（沒有設定 BACKUP_DIRS）', 'ok': False, 'error': '.env 沒有 BACKUP_DIRS'})
    for dest in dirs:
        try:
            f = make_zip(root, dest, stamp)
            result['destinations'].append({'dir': str(dest), 'ok': True, 'file': f.name, 'size': f.stat().st_size, 'pruned': prune(dest)})
        except Exception as e:      # noqa: BLE001 — 一個目的地失敗不影響其他目的地
            result['destinations'].append({'dir': str(dest), 'ok': False, 'error': str(e)[:300]})
    status = root / STATUS
    status.parent.mkdir(parents=True, exist_ok=True)
    status.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding='utf-8')
    return result
