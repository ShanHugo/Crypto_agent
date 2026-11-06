"""第二十四輪（DESIGN 6.17）：研究期判定寫下之後，才補算 2025-01～2026-10 的 ridge（只當參考段）。ml_venv 執行：

    ..\\_vendor\\ml_venv\\Scripts\\python.exe -I scripts/xsec_blend_ridge.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import xsec_train as tr  # noqa: E402

DEV = ROOT / 'state' / 'research' / 'xsec_blend_dev.json'
OUT = ROOT / 'state' / 'research' / 'xsec_blend_ridge.jsonl'


def main() -> None:
    sys.stdout.reconfigure(encoding='utf-8')
    if not DEV.exists():
        sys.exit('研究期判定還沒寫下，不准算參考段')
    if OUT.exists():
        sys.exit('ridge 已經補算過，不重跑')
    rows = tr.walk('mon', tr.EXAM, ['ridge'])
    OUT.write_text(''.join(json.dumps(r) + '\n' for r in rows), encoding='utf-8')
    print(f'ridge 參考段 {len(rows)} 列')


if __name__ == '__main__':
    main()
