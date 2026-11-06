"""第十九輪：逐月重訓 ridge、gbm（DESIGN.md 6.10）。**看過結果後不改**。用獨立環境執行：

    ..\\_vendor\\ml_venv\\Scripts\\python.exe -I scripts/own_model_train.py selfcheck
    ..\\_vendor\\ml_venv\\Scripts\\python.exe -I scripts/own_model_train.py dev            # 研究期，只能跑一次
    ..\\_vendor\\ml_venv\\Scripts\\python.exe -I scripts/own_model_train.py exam <模型>    # 研究期通過才准

只讀 data/own_model/features.csv，只寫 state/research/own_model_preds.jsonl；不碰金鑰、網路、資料庫。
LightGBM 用 CPU：實測這個資料量 CPU 每次約 0.02 秒、GPU 0.25～1.1 秒，兩者結果也有差（2026-11-06，隨機資料），CPU 才能固定重現。
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
CSV = ROOT / 'data' / 'own_model' / 'features.csv'
PREDS = ROOT / 'state' / 'research' / 'own_model_preds.jsonl'
DEV_JSON = ROOT / 'state' / 'research' / 'own_model_dev.json'
D = 86_400_000
RIDGE_LAMBDA = 100.0
CLIP = 5.0
SEED = 100
GBM = {'objective': 'regression', 'device_type': 'cpu', 'num_leaves': 8, 'max_depth': 3, 'learning_rate': 0.03,
       'min_data_in_leaf': 50, 'bagging_fraction': 0.7, 'bagging_freq': 1, 'feature_fraction': 0.7, 'seed': SEED,
       'deterministic': True, 'force_col_wise': True, 'num_threads': 1, 'verbose': -1}
TREES = 100
START = (2020, 1)
DEV = ((2022, 1), (2025, 9))
EXAM = ((2025, 9), (2026, 11))


def month_ms(y: int, m: int) -> int:
    return int(datetime(y, m, 1, tzinfo=timezone.utc).timestamp() * 1000)


def months(a: tuple[int, int], b: tuple[int, int]) -> list[tuple[int, int]]:
    """[a, b) 每個月的（開始毫秒, 結束毫秒）。"""
    out = []
    y, m = a
    while (y, m) < b:
        n = (y + m // 12, m % 12 + 1)
        out.append((month_ms(y, m), month_ms(*n)))
        y, m = n
    return out


def load() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    with CSV.open(encoding='utf-8') as f:
        rows = list(csv.reader(f))[1:]
    num = lambda s: float(s) if s else np.nan       # noqa: E731
    return (np.array([int(r[0]) for r in rows]), np.array([num(r[1]) for r in rows]),
            np.array([[num(x) for x in r[2:]] for r in rows]))


def train_mask(ts: np.ndarray, y: np.ndarray, x: np.ndarray, m0: int) -> np.ndarray:
    """只留 24 小時報酬在 m0 以前已結束、資料齊全的日子（purging）。"""
    return (ts >= month_ms(*START)) & (ts + D <= m0) & ~np.isnan(y) & ~np.isnan(x).any(axis=1)


def scale(train: np.ndarray, test: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """用訓練資料的平均與標準差標準化，超過 ±5 截掉。"""
    mu, sd = train.mean(axis=0), train.std(axis=0)
    sd[sd == 0] = 1.0
    return np.clip((train - mu) / sd, -CLIP, CLIP), np.clip((test - mu) / sd, -CLIP, CLIP)


def ridge(x: np.ndarray, y: np.ndarray, test: np.ndarray) -> np.ndarray:
    beta = np.linalg.solve(x.T @ x + RIDGE_LAMBDA * np.eye(x.shape[1]), x.T @ y)
    return test @ beta


def gbm(x: np.ndarray, y: np.ndarray, test: np.ndarray) -> np.ndarray:
    return lgb.train(GBM, lgb.Dataset(x, y), TREES).predict(test)


FIT = {'ridge': ridge, 'gbm': gbm}


def walk(period: tuple, names: list[str]) -> list[dict]:
    """逐月重訓：xxx_shuf 是對照組（訓練答案打亂、每月 seed 固定）。"""
    ts, y, x = load()
    out = []
    for n, (m0, m1) in enumerate(months(*period)):
        mask = train_mask(ts, y, x, m0)
        test = (ts >= m0) & (ts < m1) & ~np.isnan(x).any(axis=1)
        if not test.any():
            continue
        xtr, xte = scale(x[mask], x[test])
        ytr = y[mask] - y[mask].mean()              # 只學擇時，不學「過去一直漲」
        for name in names:
            base, shuf = name.removesuffix('_shuf'), name.endswith('_shuf')
            target = np.random.default_rng(SEED + n).permutation(ytr) if shuf else ytr
            out += [{'model': name, 'ts': int(t), 'pred': float(p)} for t, p in zip(ts[test], FIT[base](xtr, target, xte))]
        print(f'{datetime.fromtimestamp(m0 / 1000, timezone.utc):%Y-%m}：訓練 {int(mask.sum())} 天、預測 {int(test.sum())} 天', flush=True)
    return out


def append(rows: list[dict]) -> None:
    PREDS.parent.mkdir(parents=True, exist_ok=True)
    with PREDS.open('a', encoding='utf-8') as f:
        f.writelines(json.dumps(r) + '\n' for r in rows)


def selfcheck() -> None:
    assert months((2023, 11), (2024, 2)) == [(month_ms(2023, 11), month_ms(2023, 12)), (month_ms(2023, 12), month_ms(2024, 1)),
                                             (month_ms(2024, 1), month_ms(2024, 2))]
    m0 = month_ms(2024, 3)
    ts = np.array([month_ms(2019, 12), m0 - 2 * D, m0 - D, m0 - D + 1, m0 - 3 * D, m0 - 4 * D])
    y = np.array([0.1, 0.1, 0.1, 0.1, np.nan, 0.1])
    x = np.array([[1.0], [1.0], [1.0], [1.0], [1.0], [np.nan]])
    assert train_mask(ts, y, x, m0).tolist() == [False, True, True, False, False, False]   # 太早、可、剛好結束、還沒結束、沒答案、缺特徵
    tr, te = scale(np.array([[0.0], [2.0]]), np.array([[100.0], [3.0]]))
    assert tr.ravel().tolist() == [-1.0, 1.0] and te.ravel().tolist() == [CLIP, 2.0]       # 平均、標準差只看訓練資料
    p = ridge(np.array([[1.0], [-1.0]]), np.array([1.0, -1.0]), np.array([[1.0]]))
    assert abs(p[0] - 2 / 102) < 1e-12                                                     # 手算：2 ÷ (2 + 100)
    rng = np.random.default_rng(0)
    xs, ys = rng.standard_normal((1500, 16)), rng.standard_normal(1500)
    a, b = gbm(xs, ys, xs[:50]), gbm(xs, ys, xs[:50])
    assert (a == b).all()                                                                  # 同 seed 結果完全一樣
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
        if name != pick:
            sys.exit(f'研究期通過的是 {pick}，不准考 {name}')
        append(walk(EXAM, [name]))
    else:
        sys.exit(__doc__)


if __name__ == '__main__':
    main()
