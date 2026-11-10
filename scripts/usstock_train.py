"""第二十六輪：每年初重訓 ridge、gbm（DESIGN 6.22）。**看過結果後不改**。用獨立環境執行：

    ..\\_vendor\\ml_venv\\Scripts\\python.exe -I scripts/usstock_train.py selfcheck
    ..\\_vendor\\ml_venv\\Scripts\\python.exe -I scripts/usstock_train.py dev            # 研究期，只能跑一次
    ..\\_vendor\\ml_venv\\Scripts\\python.exe -I scripts/usstock_train.py exam <組>      # 研究期通過才准（gbm 或 blend）

只讀 data/usstock/panel.csv，只寫 state/research/usstock_preds.jsonl；不碰金鑰、網路、資料庫。
模型設定和第二十一輪相同（scripts/xsec_train.py）；LightGBM 用 CPU、單執行緒，才能完全重現。
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import lightgbm as lgb
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / 'data' / 'usstock' / 'panel.csv'
PREDS = ROOT / 'state' / 'research' / 'usstock_preds.jsonl'
DEV_JSON = ROOT / 'state' / 'research' / 'usstock_dev.json'
RIDGE_LAMBDA = 100.0
SEED = 100
GBM = {'objective': 'regression', 'device_type': 'cpu', 'num_leaves': 8, 'max_depth': 3, 'learning_rate': 0.03,
       'min_data_in_leaf': 50, 'bagging_fraction': 0.7, 'bagging_freq': 1, 'feature_fraction': 0.7, 'seed': SEED,
       'deterministic': True, 'force_col_wise': True, 'num_threads': 1, 'verbose': -1}
TREES = 100
FIRST_TRAIN = date(2005, 1, 3)
DEV = (date(2012, 1, 2), date(2019, 12, 30))
EXAM = (date(2020, 1, 6), date(2025, 12, 29))
EXAM_MODELS = {'gbm': ['gbm'], 'blend': ['gbm', 'ridge']}


def load() -> tuple[np.ndarray, list[str], np.ndarray, np.ndarray]:
    with PANEL.open(encoding='utf-8') as f:
        rows = list(csv.reader(f))[1:]
    return (np.array([date.fromisoformat(r[0]).toordinal() for r in rows]), [r[1] for r in rows],
            np.array([float(r[3]) for r in rows]), np.array([[float(x) for x in r[6:]] for r in rows]))


def batches(days: np.ndarray, a: date, b: date) -> list[tuple[int, list[int]]]:
    """[a, b) 內、持有在 b 前結束的週按年分組；當年第一週是重訓日。"""
    out, last = [], None
    for d in sorted({int(x) for x in days if a.toordinal() <= x and x + 7 <= b.toordinal()}):
        y = date.fromordinal(d).year
        if y != last:
            out.append((d, []))
            last = y
        out[-1][1].append(d)
    return out


def train_mask(days: np.ndarray, t0: int) -> np.ndarray:
    """2005 年起、持有在重訓日以前已結束的週（purging）。"""
    return (days >= FIRST_TRAIN.toordinal()) & (days + 7 <= t0)


def shuffle_within_week(days: np.ndarray, y: np.ndarray, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    out = y.copy()
    for d in np.unique(days):
        i = np.flatnonzero(days == d)
        out[i] = y[rng.permutation(i)]
    return out


def ridge(x: np.ndarray, y: np.ndarray, test: np.ndarray) -> np.ndarray:
    beta = np.linalg.solve(x.T @ x + RIDGE_LAMBDA * np.eye(x.shape[1]), x.T @ y)
    return test @ beta


def gbm(x: np.ndarray, y: np.ndarray, test: np.ndarray) -> np.ndarray:
    return lgb.train(GBM, lgb.Dataset(x, y), TREES).predict(test)


FIT = {'ridge': ridge, 'gbm': gbm}


def walk(period: tuple[date, date], names: list[str]) -> list[dict]:
    days, syms, y, x = load()
    out = []
    for n, (t0, week_list) in enumerate(batches(days, *period)):
        mask = train_mask(days, t0)
        test = np.flatnonzero(np.isin(days, week_list))
        for name in names:
            base, shuf = name.removesuffix('_shuf'), name.endswith('_shuf')
            target = shuffle_within_week(days[mask], y[mask], SEED + n) if shuf else y[mask]
            pred = FIT[base](x[mask], target, x[test])
            out += [{'model': name, 'monday': date.fromordinal(int(days[i])).isoformat(), 'sym': syms[i], 'pred': float(p)}
                    for i, p in zip(test, pred)]
        print(f'{date.fromordinal(t0).year}：訓練 {int(mask.sum())} 列、預測 {len(test)} 列', flush=True)
    return out


def append(rows: list[dict]) -> None:
    PREDS.parent.mkdir(parents=True, exist_ok=True)
    with PREDS.open('a', encoding='utf-8') as f:
        f.writelines(json.dumps(r) + '\n' for r in rows)


def selfcheck() -> None:
    o = date(2024, 3, 4).toordinal()
    days = np.array([o - 14, o - 7, o, o - 6, date(2004, 12, 27).toordinal()])
    assert train_mask(days, o).tolist() == [True, True, False, False, False]          # 剛好結束可用；沒結束、2005 前不用
    wk = [date(2018, 12, 24), date(2019, 1, 7), date(2019, 12, 23), date(2020, 1, 6)]
    b = batches(np.array([d.toordinal() for d in wk]), date(2018, 1, 1), date(2020, 1, 6))
    assert b == [(wk[0].toordinal(), [wk[0].toordinal()]), (wk[1].toordinal(), [wk[1].toordinal(), wk[2].toordinal()])]
    s = shuffle_within_week(np.array([1, 1, 1, 2, 2]), np.array([0.1, 0.2, 0.3, 0.4, 0.5]), 0)
    assert sorted(s[:3].tolist()) == [0.1, 0.2, 0.3] and sorted(s[3:].tolist()) == [0.4, 0.5]
    p = ridge(np.array([[1.0], [-1.0]]), np.array([1.0, -1.0]), np.array([[1.0]]))
    assert abs(p[0] - 2 / 102) < 1e-12
    rng = np.random.default_rng(0)
    xs, ys = rng.uniform(-0.5, 0.5, (3000, 8)), rng.uniform(-0.5, 0.5, 3000)
    assert (gbm(xs, ys, xs[:50]) == gbm(xs, ys, xs[:50])).all()
    print('selfcheck ok', lgb.__version__, np.__version__)


def main() -> None:
    sys.stdout.reconfigure(encoding='utf-8')
    cmd = sys.argv[1] if len(sys.argv) > 1 else ''
    if cmd == 'selfcheck':
        selfcheck()
    elif cmd == 'dev':
        if PREDS.exists():
            sys.exit('研究期已經跑過，不重跑')
        append(walk(DEV, ['ridge', 'gbm', 'ridge_shuf', 'gbm_shuf']))
    elif cmd == 'exam' and len(sys.argv) == 3:
        name = sys.argv[2]
        pick = json.loads(DEV_JSON.read_text(encoding='utf-8')).get('pick') if DEV_JSON.exists() else None
        if name != pick or name not in EXAM_MODELS:
            sys.exit(f'研究期通過的是 {pick}，不准考 {name}')
        if any(json.loads(line)['monday'] >= EXAM[0].isoformat() for line in PREDS.read_text(encoding='utf-8').splitlines()):
            sys.exit('考卷已經跑過，不重跑')
        append(walk(EXAM, EXAM_MODELS[name]))
    else:
        sys.exit(__doc__)


if __name__ == '__main__':
    main()
