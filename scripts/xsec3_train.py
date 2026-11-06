"""第二十二輪：每 3 天判斷的 gbm，每月重訓（DESIGN.md 6.14）。**看過結果後不改**。用獨立環境執行：

    ..\\_vendor\\ml_venv\\Scripts\\python.exe -I scripts/xsec3_train.py selfcheck
    ..\\_vendor\\ml_venv\\Scripts\\python.exe -I scripts/xsec3_train.py dev         # 研究期，只能跑一次
    ..\\_vendor\\ml_venv\\Scripts\\python.exe -I scripts/xsec3_train.py reference   # 研究期判定寫下後才准

只讀 data/xsec/panel3_*.csv，只寫 state/research/xsec3_preds.jsonl；不碰金鑰、網路、資料庫。
模型參數與第二十一輪相同（scripts/xsec_train.py）。
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import xsec_train as base  # noqa: E402

PANEL = ROOT / 'data' / 'xsec' / 'panel3_{}.csv'
PREDS = ROOT / 'state' / 'research' / 'xsec3_preds.jsonl'
DEV_JSON = ROOT / 'state' / 'research' / 'xsec3_dev.json'
STEP = 3 * base.D
DEV, REFERENCE = base.DEV, base.EXAM


def load(shift: str) -> tuple[np.ndarray, list[str], np.ndarray, np.ndarray]:
    with Path(str(PANEL).format(shift)).open(encoding='utf-8') as f:
        rows = list(csv.reader(f))[1:]
    return (np.array([int(r[0]) for r in rows]), [r[1] for r in rows], np.array([float(r[3]) for r in rows]),
            np.array([[float(x) for x in r[4:]] for r in rows]))


def batches(ts: np.ndarray, a: int, b: int) -> list[tuple[int, list[int]]]:
    """把 [a, b) 內、持有在 b 前結束的判斷日按月分組；當月第一次是重訓日。"""
    out, last = [], None
    for t in sorted({int(x) for x in ts if a <= x and x + STEP <= b}):
        if base.month_key(t) != last:
            out.append((t, []))
            last = base.month_key(t)
        out[-1][1].append(t)
    return out


def train_mask(ts: np.ndarray, t0: int) -> np.ndarray:
    """只留持有在重訓日以前已結束的判斷（purging）。"""
    return ts + STEP <= t0


def walk(shift: str, period: tuple[int, int], names: list[str]) -> list[dict]:
    ts, syms, y, x = load(shift)
    out = []
    for n, (t0, group) in enumerate(batches(ts, *period)):
        mask = train_mask(ts, t0)
        test = np.flatnonzero(np.isin(ts, group))
        for name in names:
            target = base.shuffle_within_week(ts[mask], y[mask], base.SEED + n) if name.endswith('_shuf') else y[mask]
            pred = base.gbm(x[mask], target, x[test])
            out += [{'model': name, 'wd': shift, 'ts': int(ts[i]), 'sym': syms[i], 'pred': float(p)} for i, p in zip(test, pred)]
        print(f'{shift} {datetime.fromtimestamp(t0 / 1000, timezone.utc):%Y-%m}：訓練 {int(mask.sum())} 列、預測 {len(test)} 列', flush=True)
    return out


def append(rows: list[dict]) -> None:
    PREDS.parent.mkdir(parents=True, exist_ok=True)
    with PREDS.open('a', encoding='utf-8') as f:
        f.writelines(json.dumps(r) + '\n' for r in rows)


def selfcheck() -> None:
    t0 = base.ms(2024, 3, 4)
    ts = np.array([t0 - 2 * STEP, t0 - STEP, t0, t0 - STEP + 1])
    assert train_mask(ts, t0).tolist() == [True, True, False, False]                       # 持有剛好結束可以用；還沒結束不行
    d = [base.ms(2024, 2, 27), base.ms(2024, 3, 1), base.ms(2024, 3, 4), base.ms(2024, 3, 7)]
    b = batches(np.array(d), base.ms(2024, 2, 1), base.ms(2024, 3, 8))
    assert b == [(d[0], [d[0]]), (d[1], [d[1], d[2]])]                                     # 每月第一次重訓；期末沒結束的不算
    print('selfcheck ok')


def main() -> None:
    sys.stdout.reconfigure(encoding='utf-8')
    cmd = sys.argv[1] if len(sys.argv) > 1 else ''
    if cmd == 'selfcheck':
        selfcheck()
    elif cmd == 'dev':
        if PREDS.exists():
            sys.exit('研究期已經跑過，不重跑')
        append(walk('s0', DEV, ['gbm', 'gbm_shuf']) + walk('s1', DEV, ['gbm']))
    elif cmd == 'reference':
        if not DEV_JSON.exists():
            sys.exit('研究期判定還沒寫下，不算參考段')
        if any(json.loads(line)['ts'] >= REFERENCE[0] for line in PREDS.read_text(encoding='utf-8').splitlines()):
            sys.exit('參考段已經跑過，不重跑')
        append(walk('s0', REFERENCE, ['gbm']))
    else:
        sys.exit(__doc__)


if __name__ == '__main__':
    main()
