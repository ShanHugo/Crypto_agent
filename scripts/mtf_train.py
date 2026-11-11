"""第二十八輪：每季重訓 LightGBM（DESIGN.md 6.24、6.3）；第三十輪（6.26）擴充：加技術指標、研究期 2024～2025。**看過結果後不改**。用獨立環境執行：

    ..\\_vendor\\ml_venv\\Scripts\\python.exe -I scripts/mtf_train.py dev     # 研究期 2024～2025
    ..\\_vendor\\ml_venv\\Scripts\\python.exe -I scripts/mtf_train.py exam    # 研究期有組通過才准（看 state/research/ta_dev.json）

只讀 data/k15/panel_ta.csv，只寫 state/research/ta_preds_<階段>.csv（只存有訊號的列）；不碰金鑰、網路、資料庫。
`mtf` 用 25 項（第二十八輪）；`mtf_ta` 用 39 項（再加 14 項技術指標）；`mtf_ta_shuf` 是 `mtf_ta` 的對照（訓練答案打亂）。CPU、單執行緒，完全重現。
"""
from __future__ import annotations

import csv
import json
import sys
from array import array
from datetime import datetime, timezone
from pathlib import Path

import lightgbm as lgb
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / 'data' / 'k15' / 'panel_ta.csv'
OUT = str(ROOT / 'state' / 'research' / 'ta_preds_{}.csv')
DEV_JSON = ROOT / 'state' / 'research' / 'ta_dev.json'
D, BAR, H = 86_400_000, 900_000, 16
SEED = 100
GBM = {'objective': 'regression', 'device_type': 'cpu', 'num_leaves': 8, 'max_depth': 3, 'learning_rate': 0.03,
       'min_data_in_leaf': 50, 'bagging_fraction': 0.7, 'bagging_freq': 1, 'feature_fraction': 0.7, 'seed': SEED,
       'deterministic': True, 'force_col_wise': True, 'num_threads': 1, 'verbose': -1}
TREES = 100
BASE = ['z1', 'z4', 'z16', 'z96', 'pos16', 'vratio', 'taker4', 'fund', 'btc_z4', 'hour']
HTF = ['d_z1', 'd_z3', 'd_z7', 'd_z30', 'd_ma20', 'd_ma50', 'd_vol20', 'd_fund3',
       'h4_z1', 'h4_z6', 'h4_ma20', 'h4_pos6', 'h1_z1', 'h1_z4', 'h1_ma24']
TA = ['ta_ma7', 'ta_ma25', 'ta_ma99', 'ta_ema12', 'ta_ema26', 'ta_macd', 'ta_bb_pct', 'ta_bb_w', 'ta_sar_dir', 'ta_sar_dist', 'ta_avl',
      'h4_bb_pct', 'h4_sar_dir', 'h4_sar_dist']
FEATURES = BASE + HTF + TA


def ms(y: int, m: int, d: int = 1) -> int:
    return int(datetime(y, m, d, tzinfo=timezone.utc).timestamp() * 1000)


PERIOD = {'dev': (ms(2024, 1), ms(2026, 1)), 'exam': (ms(2026, 1), ms(2026, 11))}


def quarters(a: int, b: int) -> list[tuple[int, int]]:
    out, y, m = [], *map(int, datetime.fromtimestamp(a / 1000, timezone.utc).strftime('%Y %m').split())
    while ms(y, m) < b:
        ny, nm = (y + 1, m - 9) if m > 9 else (y, m + 3)
        out.append((ms(y, m), min(ms(ny, nm), b)))
        y, m = ny, nm
    return out


def load() -> tuple:
    """省記憶體：float32 陣列，幣名換成編號。"""
    t, ids, x, y = array('q'), array('i'), array('f'), array('f')
    names: dict[str, int] = {}
    nan = float('nan')
    with PANEL.open(encoding='utf-8', newline='') as f:
        r = csv.reader(f)
        head = next(r)
        ix = [head.index(k) for k in FEATURES]
        it, isym, iy = head.index('t'), head.index('sym'), head.index('target')
        for row in r:
            t.append(int(row[it]))
            ids.append(names.setdefault(row[isym], len(names)))
            x.extend(float(row[i]) if row[i] else nan for i in ix)
            y.append(float(row[iy]))
    return (np.frombuffer(t, dtype=np.int64), list(names), ids,
            np.frombuffer(x, dtype=np.float32).reshape(-1, len(FEATURES)), np.frombuffer(y, dtype=np.float32))


def main() -> None:
    sys.stdout.reconfigure(encoding='utf-8')
    stage = sys.argv[1] if len(sys.argv) > 1 else ''
    if stage not in PERIOD:
        sys.exit('用法：mtf_train.py dev|exam')
    out = Path(OUT.format(stage))
    if out.exists():
        sys.exit(f'{out.name} 已存在：每個階段只跑一次')
    if stage == 'exam' and not (DEV_JSON.exists() and json.loads(DEV_JSON.read_text(encoding='utf-8')).get('pick')):
        sys.exit('研究期沒有組通過，不考')
    t, names, ids, x, y = load()
    print(f'{len(t)} 列', flush=True)
    rng = np.random.default_rng(SEED)
    nb = len(BASE) + len(HTF)
    rows = []
    for a, b in quarters(*PERIOD[stage]):
        train = np.flatnonzero((t + H * BAR < a - D) & ((t // BAR) % H == 0))
        test = np.flatnonzero((t >= a) & (t < b))
        if len(train) < 1000 or not len(test):
            continue
        yt = y[train]
        for name, cols, target in (('mtf', slice(0, nb), yt), ('mtf_ta', slice(None), yt), ('mtf_ta_shuf', slice(None), rng.permutation(yt))):
            m = lgb.train(GBM, lgb.Dataset(x[train][:, cols], target), TREES)
            lo, hi = np.percentile(m.predict(x[train][:, cols]), [5, 95])
            p = m.predict(x[test][:, cols])
            for i, s in zip(test, p):
                side = 1 if s >= hi else -1 if s <= lo else 0
                if side:
                    rows.append((int(t[i]), names[ids[i]], name, side, f'{s:.6g}'))
        print(f'{datetime.fromtimestamp(a / 1000, timezone.utc):%Y-%m}：訓練 {len(train)} 列、預測 {len(test)} 列', flush=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open('w', encoding='utf-8', newline='') as f:
        w = csv.writer(f)
        w.writerow(['t', 'sym', 'model', 'side', 'score'])
        w.writerows(rows)
    print(f'寫入 {out}：{len(rows)} 個訊號')


if __name__ == '__main__':
    main()
