"""備份 state/ 與 reports/ 到 .env 的 BACKUP_DIRS（不含 .env）。每天 08:30 報告推送後自動執行，也可以手動：

    .venv\\Scripts\\python.exe scripts/backup.py

雲端硬碟（例如 Google 雲端硬碟電腦版）要登入 Windows 才會掛上磁碟機；沒登入時會失敗，健康檢查亮 🔴，登入後手動補一次即可。
還原：把壓縮檔解開到專案資料夾（會覆蓋 state/ 與 reports/），見 docs/ARCHITECTURE.md 第 7 節。
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from dotenv import dotenv_values  # noqa: E402

from crypto_robot import backup  # noqa: E402
from crypto_robot.core.timeutil import TAIPEI  # noqa: E402


def run() -> dict:
    dirs = backup.destinations(dotenv_values(ROOT / '.env').get('BACKUP_DIRS') or '')
    return backup.run(ROOT, datetime.now(TAIPEI).replace(tzinfo=None), dirs)


def lines(r: dict) -> list[str]:
    return [f"[備份] {'✅' if d['ok'] else '🔴'} {d['dir']}："
            + (f"{d['file']}（{d['size'] / 1024:,.0f} KB，刪除 {d['pruned']} 份舊備份）" if d['ok'] else d['error'])
            for d in r['destinations']]


def main() -> int:
    sys.stdout.reconfigure(encoding='utf-8')
    r = run()
    print('\n'.join(lines(r)))
    return 0 if all(d['ok'] for d in r['destinations']) else 1


if __name__ == '__main__':
    sys.exit(main())
