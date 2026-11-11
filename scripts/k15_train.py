"""第二十七輪：每季重訓 LightGBM（DESIGN.md 6.23、6.3）。**看過結果後不改**。用獨立環境執行：

    ..\\_vendor\\ml_venv\\Scripts\\python.exe -I scripts/k15_train.py dev     # 研究期 2021～2023
    ..\\_vendor\\ml_venv\\Scripts\\python.exe -I scripts/k15_train.py exam    # 研究期有組通過才准（看 state/research/k15_dev.json）

只讀 data/k15/panel.csv，只寫 state/research/k15_preds_<階段>.csv（只存有訊號的列）；不碰金鑰、網路、資料庫。
`gbm_shuf` 是對照：訓練答案打亂。LightGBM 用 CPU、單執行緒，才能完全重現。
"""
from __future__ import annotations

import csv
from array import array
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import lightgbm as lgb
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / 'data' / 'k15' / 'panel.csv'
OUT = ROOT / 'state' / 'research' / 'k15_preds_{}.csv'
DEV_JSON = ROOT / 'state' / 'research' / 'k15_dev.json'
D, BAR, H = 86_400_000, 900_000, 4
SEED = 100
GBM = {'objective': 'regression', 'device_type': 'cpu', 'num_leaves': 8, 'max_depth': 3, 'learning_rate': 0.03,
       'min_data_in_leaf': 50, 'bagging_fraction': 0.7, 'bagging_freq': 1, 'feature_fraction': 0.7, 'seed': SEED,
       'deterministic': True, 'force_col_wise': True, 'num_threads': 1, 'verbose': -1}
TREES = 100
FEATURES = ['z1', 'z4', 'z16', 'z96', 'pos16', 'vratio', 'taker4', 'fund', 'btc_z4', 'hour']


def ms(y: int, m: int, d: int = 1) -> int:
    return int(datetime(y, m, d, tzinfo=timezone.utc).timestamp() * 1000)


PERIOD = {'dev': (ms(2021, 1), ms(2024, 1)), 'exam': (ms(2024, 1), ms(2026, 11))}   # 考卷含 2026 參考段（只報告）


def quarters(a: int, b: int) -> list[tuple[int, int]]:
    out, y, m = [], *map(int, datetime.fromtimestamp(a / 1000, timezone.utc).strftime('%Y %m').split())
    while ms(y, m) < b:
        ny, nm = (y + 1, m - 9) if m > 9 else (y, m + 3)
        out.append((ms(y, m), min(ms(ny, nm), b)))
        y, m = ny, nm
    return out


def load() -> tuple:
    """省記憶體：數字直接放進 array，幣名換成編號。"""
    t, ids, x, y = array('q'), array('i'), array('d'), array('d')
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
    sym = list(names)
    return (np.frombuffer(t, dtype=np.int64), _Names(sym, ids),
            np.frombuffer(x).reshape(-1, len(FEATURES)), np.frombuffer(y))


class _Names:
    def __init__(self, names: list[str], ids: array):
        self.names, self.ids = names, ids

    def __getitem__(self, i: int) -> str:
        return self.names[self.ids[i]]


def main() -> None:
    sys.stdout.reconfigure(encoding='utf-8')
    stage = sys.argv[1] if len(sys.argv) > 1 else ''
    if stage not in PERIOD:
        sys.exit('用法：k15_train.py dev|exam')
    out = OUT.format(stage)
    if out.exists():
        sys.exit(f'{out.name} 已存在：每個階段只跑一次')
    if stage == 'exam' and not (DEV_JSON.exists() and json.loads(DEV_JSON.read_text(encoding='utf-8')).get('pick')):
        sys.exit('研究期沒有組通過，不考')
    t, sym, x, y = load()
    print(f'{len(t)} 列', flush=True)
    rng = np.random.default_rng(SEED)
    rows = []
    for a, b in quarters(*PERIOD[stage]):
        train = (t + H * BAR < a - D) & ((t // BAR) % 4 == 0)
        test = np.flatnonzero((t >= a) & (t < b))
        if train.sum() < 1000 or not len(test):
            continue
        for name, target in (('gbm', y[train]), ('gbm_shuf', rng.permutation(y[train]))):
            m = lgb.train(GBM, lgb.Dataset(x[train], target), TREES)
            lo, hi = np.percentile(m.predict(x[train]), [5, 95])
            p = m.predict(x[test])
            for i, s in zip(test, p):
                side = 1 if s >= hi else -1 if s <= lo else 0
                if side:
                    rows.append((int(t[i]), sym[i], name, side, f'{s:.6g}'))
        print(f'{datetime.fromtimestamp(a / 1000, timezone.utc):%Y-%m}：訓練 {int(train.sum())} 列、預測 {len(test)} 列', flush=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open('w', encoding='utf-8', newline='') as f:
        w = csv.writer(f)
        w.writerow(['t', 'sym', 'model', 'side', 'score'])
        w.writerows(rows)
    print(f'寫入 {out}：{len(rows)} 個訊號')


if __name__ == '__main__':
    main()
