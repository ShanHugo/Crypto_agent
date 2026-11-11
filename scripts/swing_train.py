"""第三十一輪：波段模型每季重訓（DESIGN.md 6.27、6.3）。**看過結果後不改**。用獨立環境執行：

    ..\\_vendor\\ml_venv\\Scripts\\python.exe -I scripts/swing_train.py dev     # 研究期 2021～2024
    ..\\_vendor\\ml_venv\\Scripts\\python.exe -I scripts/swing_train.py exam    # 研究期有組通過才准（看 state/research/swing_dev.json）

只讀 data/k15/panel_swing.csv，只寫 state/research/swing_preds_<階段>.csv（只存有訊號的列）；不碰金鑰、網路、資料庫。
`gbm`：預測 5 天標準化報酬；`gbm_shuf`：訓練答案打亂的對照。CPU、單執行緒，完全重現。
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import lightgbm as lgb
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / 'data' / 'k15' / 'panel_swing.csv'
OUT = str(ROOT / 'state' / 'research' / 'swing_preds_{}.csv')
DEV_JSON = ROOT / 'state' / 'research' / 'swing_dev.json'
D = 86_400_000
SEED = 100
GBM = {'objective': 'regression', 'device_type': 'cpu', 'num_leaves': 8, 'max_depth': 3, 'learning_rate': 0.03,
       'min_data_in_leaf': 50, 'bagging_fraction': 0.7, 'bagging_freq': 1, 'feature_fraction': 0.7, 'seed': SEED,
       'deterministic': True, 'force_col_wise': True, 'num_threads': 1, 'verbose': -1}
TREES = 100
FEATURES = ['d_z1', 'd_z3', 'd_z7', 'd_z14', 'd_z30', 'd_z60', 'd_ma20', 'd_ma50', 'd_ma100', 'd_vol20', 'd_bb', 'd_qv', 'd_taker7',
            'fund3', 'fund14', 'btc_z7', 'btc_z30', 'w_ma20', 'r28', 'atr', 'd_sar_dir', 'd_sar_dist', 'h4_bb', 'h4_sar_dir', 'h4_ma20']


def ms(y: int, m: int, d: int = 1) -> int:
    return int(datetime(y, m, d, tzinfo=timezone.utc).timestamp() * 1000)


PERIOD = {'dev': (ms(2021, 1), ms(2025, 1)), 'exam': (ms(2026, 1), ms(2026, 11))}


def quarters(a: int, b: int) -> list[tuple[int, int]]:
    out, y, m = [], *map(int, datetime.fromtimestamp(a / 1000, timezone.utc).strftime('%Y %m').split())
    while ms(y, m) < b:
        ny, nm = (y + 1, m - 9) if m > 9 else (y, m + 3)
        out.append((ms(y, m), min(ms(ny, nm), b)))
        y, m = ny, nm
    return out


def load() -> tuple:
    t, sym, x, y = [], [], [], []
    with PANEL.open(encoding='utf-8', newline='') as f:
        r = csv.reader(f)
        head = next(r)
        ix = [head.index(k) for k in FEATURES]
        it, isym, iy = head.index('t'), head.index('sym'), head.index('target')
        for row in r:
            t.append(int(row[it]))
            sym.append(row[isym])
            x.append([float(row[i]) if row[i] else np.nan for i in ix])
            y.append(float(row[iy]) if row[iy] else np.nan)
    return np.array(t, dtype=np.int64), sym, np.array(x), np.array(y)


def main() -> None:
    sys.stdout.reconfigure(encoding='utf-8')
    stage = sys.argv[1] if len(sys.argv) > 1 else ''
    if stage not in PERIOD:
        sys.exit('用法：swing_train.py dev|exam')
    out = Path(OUT.format(stage))
    if out.exists():
        sys.exit(f'{out.name} 已存在：每個階段只跑一次')
    if stage == 'exam' and not (DEV_JSON.exists() and json.loads(DEV_JSON.read_text(encoding='utf-8')).get('pick')):
        sys.exit('研究期沒有組通過，不考')
    t, sym, x, y = load()
    print(f'{len(t)} 列', flush=True)
    rng = np.random.default_rng(SEED)
    rows = []
    for a, b in quarters(*PERIOD[stage]):
        train = np.flatnonzero((t + 5 * D < a - D) & ~np.isnan(y))
        test = np.flatnonzero((t >= a) & (t < b))
        if len(train) < 1000 or not len(test):
            continue
        yt = y[train]
        for name, target in (('gbm', yt), ('gbm_shuf', rng.permutation(yt))):
            m = lgb.train(GBM, lgb.Dataset(x[train], target), TREES)
            lo, hi = np.percentile(m.predict(x[train]), [5, 95])
            for i, s in zip(test, m.predict(x[test])):
                side = 1 if s >= hi else -1 if s <= lo else 0
                if side:
                    rows.append((int(t[i]), sym[i], name, side, f'{s:.6g}'))
        print(f'{datetime.fromtimestamp(a / 1000, timezone.utc):%Y-%m}：訓練 {len(train)} 列、預測 {len(test)} 列', flush=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open('w', encoding='utf-8', newline='') as f:
        w = csv.writer(f)
        w.writerow(['t', 'sym', 'model', 'side', 'score'])
        w.writerows(rows)
    print(f'寫入 {out}：{len(rows)} 個訊號')


if __name__ == '__main__':
    main()
