"""第二十一輪：每月重訓 ridge、gbm（DESIGN.md 6.12）。**看過結果後不改**。用獨立環境執行：

    ..\\_vendor\\ml_venv\\Scripts\\python.exe -I scripts/xsec_train.py selfcheck
    ..\\_vendor\\ml_venv\\Scripts\\python.exe -I scripts/xsec_train.py dev            # 研究期，只能跑一次
    ..\\_vendor\\ml_venv\\Scripts\\python.exe -I scripts/xsec_train.py exam <模型>    # 研究期通過才准

只讀 data/xsec/panel_*.csv，只寫 state/research/xsec_preds.jsonl；不碰金鑰、網路、資料庫。
LightGBM 用 CPU、單執行緒，才能完全重現。
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
PANEL = ROOT / 'data' / 'xsec' / 'panel_{}.csv'
PREDS = ROOT / 'state' / 'research' / 'xsec_preds.jsonl'
DEV_JSON = ROOT / 'state' / 'research' / 'xsec_dev.json'
D = 86_400_000
W = 7 * D
RIDGE_LAMBDA = 100.0
SEED = 100
GBM = {'objective': 'regression', 'device_type': 'cpu', 'num_leaves': 8, 'max_depth': 3, 'learning_rate': 0.03,
       'min_data_in_leaf': 50, 'bagging_fraction': 0.7, 'bagging_freq': 1, 'feature_fraction': 0.7, 'seed': SEED,
       'deterministic': True, 'force_col_wise': True, 'num_threads': 1, 'verbose': -1}
TREES = 100


def ms(y: int, m: int, d: int) -> int:
    return int(datetime(y, m, d, tzinfo=timezone.utc).timestamp() * 1000)


DEV = (ms(2022, 1, 3), ms(2024, 12, 30))
EXAM = (ms(2025, 1, 1), ms(2026, 11, 1))


def load(wd: str) -> tuple[np.ndarray, list[str], np.ndarray, np.ndarray]:
    with Path(str(PANEL).format(wd)).open(encoding='utf-8') as f:
        rows = list(csv.reader(f))[1:]
    return (np.array([int(r[0]) for r in rows]), [r[1] for r in rows], np.array([float(r[3]) for r in rows]),
            np.array([[float(x) for x in r[4:]] for r in rows]))


def month_key(t: int) -> tuple[int, int]:
    d = datetime.fromtimestamp(t / 1000, timezone.utc)
    return d.year, d.month


def batches(ts: np.ndarray, a: int, b: int) -> list[tuple[int, list[int]]]:
    """把 [a, b) 內、7 天持有在 b 前結束的週按月分組；當月第一週是重訓日。"""
    out, last = [], None
    for t in sorted({int(x) for x in ts if a <= x and x + W <= b}):
        if month_key(t) != last:
            out.append((t, []))
            last = month_key(t)
        out[-1][1].append(t)
    return out


def train_mask(ts: np.ndarray, t0: int) -> np.ndarray:
    """只留報酬在重訓日以前已結束的週（purging）。"""
    return ts + W <= t0


def shuffle_within_week(ts: np.ndarray, y: np.ndarray, seed: int) -> np.ndarray:
    """對照組：每週內打亂答案。"""
    rng = np.random.default_rng(seed)
    out = y.copy()
    for t in np.unique(ts):
        i = np.flatnonzero(ts == t)
        out[i] = y[rng.permutation(i)]
    return out


def ridge(x: np.ndarray, y: np.ndarray, test: np.ndarray) -> np.ndarray:
    beta = np.linalg.solve(x.T @ x + RIDGE_LAMBDA * np.eye(x.shape[1]), x.T @ y)
    return test @ beta


def gbm(x: np.ndarray, y: np.ndarray, test: np.ndarray) -> np.ndarray:
    return lgb.train(GBM, lgb.Dataset(x, y), TREES).predict(test)


FIT = {'ridge': ridge, 'gbm': gbm}


def walk(wd: str, period: tuple[int, int], names: list[str]) -> list[dict]:
    """每月重訓，只增不減。xxx_shuf 是對照組（訓練答案每週內打亂、每月 seed 固定）。"""
    ts, syms, y, x = load(wd)
    out = []
    for n, (t0, week_list) in enumerate(batches(ts, *period)):
        mask = train_mask(ts, t0)
        test = np.flatnonzero(np.isin(ts, week_list))
        for name in names:
            base, shuf = name.removesuffix('_shuf'), name.endswith('_shuf')
            target = shuffle_within_week(ts[mask], y[mask], SEED + n) if shuf else y[mask]
            pred = FIT[base](x[mask], target, x[test])
            out += [{'model': name, 'wd': wd, 'ts': int(ts[i]), 'sym': syms[i], 'pred': float(p)} for i, p in zip(test, pred)]
        print(f'{wd} {datetime.fromtimestamp(t0 / 1000, timezone.utc):%Y-%m}：訓練 {int(mask.sum())} 列、預測 {len(test)} 列', flush=True)
    return out


def append(rows: list[dict]) -> None:
    PREDS.parent.mkdir(parents=True, exist_ok=True)
    with PREDS.open('a', encoding='utf-8') as f:
        f.writelines(json.dumps(r) + '\n' for r in rows)


def selfcheck() -> None:
    t0 = ms(2024, 3, 4)
    ts = np.array([t0 - 2 * W, t0 - W, t0, t0 - W + 1])
    assert train_mask(ts, t0).tolist() == [True, True, False, False]                       # 持有剛好結束可以用；還沒結束不行
    wk = [ms(2024, 2, 26), ms(2024, 3, 4), ms(2024, 3, 11), ms(2024, 4, 1)]
    b = batches(np.array(wk), ms(2024, 2, 1), ms(2024, 4, 9))
    assert b == [(wk[0], [wk[0]]), (wk[1], [wk[1], wk[2]]), (wk[3], [wk[3]])]               # 每月第一週重訓；期末沒結束的週不算
    t2 = np.array([1, 1, 1, 2, 2])
    s = shuffle_within_week(t2, np.array([0.1, 0.2, 0.3, 0.4, 0.5]), 0)
    assert sorted(s[:3].tolist()) == [0.1, 0.2, 0.3] and sorted(s[3:].tolist()) == [0.4, 0.5]  # 只在同一週內打亂
    p = ridge(np.array([[1.0], [-1.0]]), np.array([1.0, -1.0]), np.array([[1.0]]))
    assert abs(p[0] - 2 / 102) < 1e-12                                                     # 手算：2 ÷ (2 + 100)
    rng = np.random.default_rng(0)
    xs, ys = rng.uniform(-0.5, 0.5, (3000, 16)), rng.uniform(-0.5, 0.5, 3000)
    a, c = gbm(xs, ys, xs[:50]), gbm(xs, ys, xs[:50])
    assert (a == c).all()                                                                  # 同 seed 結果完全一樣
    print('selfcheck ok', lgb.__version__, np.__version__)


def main() -> None:
    sys.stdout.reconfigure(encoding='utf-8')
    cmd = sys.argv[1] if len(sys.argv) > 1 else ''
    if cmd == 'selfcheck':
        selfcheck()
    elif cmd == 'dev':
        if PREDS.exists():
            sys.exit('研究期已經跑過，不重跑')
        rows = walk('mon', DEV, ['ridge', 'gbm', 'ridge_shuf', 'gbm_shuf']) + walk('thu', DEV, ['ridge', 'gbm'])
        append(rows)
    elif cmd == 'exam' and len(sys.argv) == 3:
        name = sys.argv[2]
        pick = json.loads(DEV_JSON.read_text(encoding='utf-8')).get('pick') if DEV_JSON.exists() else None
        if name != pick:
            sys.exit(f'研究期通過的是 {pick}，不准考 {name}')
        append(walk('mon', EXAM, [name]))
    else:
        sys.exit(__doc__)


if __name__ == '__main__':
    main()
