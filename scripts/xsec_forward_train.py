"""blend 前瞻模擬的訓練與預測（DESIGN.md 6.18）：gbm 與 ridge。用獨立環境執行，模型參數同第二十一輪（scripts/xsec_train.py）：

    ..\\_vendor\\ml_venv\\Scripts\\python.exe -I scripts/xsec_forward_train.py <週開始毫秒>
    ..\\_vendor\\ml_venv\\Scripts\\python.exe -I scripts/xsec_forward_train.py selfcheck

每月第一個週一重訓一次，模型存 state/futures/models/；只讀 data/xsec/forward_panel.csv，只寫模型與預測。不碰金鑰、網路、資料庫。
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import xsec_train as base  # noqa: E402

PANEL = ROOT / 'data' / 'xsec' / 'forward_panel.csv'
STATE = ROOT / 'state' / 'futures'
W = base.W


def load() -> tuple[np.ndarray, list[str], np.ndarray, np.ndarray]:
    with PANEL.open(encoding='utf-8') as f:
        rows = list(csv.reader(f))[1:]
    return (np.array([int(r[0]) for r in rows]), [r[1] for r in rows], np.array([float(r[3]) if r[3] else np.nan for r in rows]),
            np.array([[float(x) for x in r[4:]] for r in rows]))


def first_of_month(t: int) -> int:
    """t 那個月的第一個週一。"""
    key = base.month_key(t)
    while base.month_key(t - W) == key:
        t -= W
    return t


def train_mask(ts: np.ndarray, y: np.ndarray, t0: int) -> np.ndarray:
    """只留持有在重訓日以前已結束、有答案的週。"""
    return (ts + W <= t0) & ~np.isnan(y)


def model_for(t: int, ts: np.ndarray, y: np.ndarray, x: np.ndarray) -> lgb.Booster:
    t0 = first_of_month(t)
    y_, m_ = base.month_key(t0)
    path = STATE / 'models' / f'gbm_{y_}-{m_:02d}.txt'
    if path.exists():
        return lgb.Booster(model_file=str(path))
    mask = train_mask(ts, y, t0)
    booster = lgb.train(base.GBM, lgb.Dataset(x[mask], y[mask]), base.TREES)
    path.parent.mkdir(parents=True, exist_ok=True)
    booster.save_model(str(path))
    print(f'重訓 {y_}-{m_:02d}：{int(mask.sum())} 列', flush=True)
    return booster


def selfcheck() -> None:
    t = base.ms(2026, 11, 23)
    assert first_of_month(t) == base.ms(2026, 11, 2) and first_of_month(base.ms(2026, 11, 2)) == base.ms(2026, 11, 2)
    t0 = base.ms(2026, 11, 2)
    ts = np.array([t0 - 2 * W, t0 - W, t0 - W + 1, t0 - 3 * W])
    y = np.array([0.1, 0.2, 0.3, np.nan])
    assert train_mask(ts, y, t0).tolist() == [True, True, False, False]
    print('selfcheck ok')


def main() -> None:
    sys.stdout.reconfigure(encoding='utf-8')
    if sys.argv[1:] == ['selfcheck']:
        selfcheck()
        return
    t = int(sys.argv[1])
    ts, syms, y, x = load()
    test = np.flatnonzero(ts == t)
    if not len(test):
        sys.exit(f'panel 沒有 {t} 這一週')
    pred = model_for(t, ts, y, x).predict(x[test])
    out = STATE / 'preds' / f'{t}.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({syms[i]: float(p) for i, p in zip(test, pred)}), encoding='utf-8')
    mask = train_mask(ts, y, first_of_month(t))
    ridge = base.ridge(x[mask], y[mask], x[test])          # 每月第一個週一的訓練範圍，和第二十一輪相同；很快，不存模型
    out = STATE / 'preds_ridge' / f'{t}.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({syms[i]: float(p) for i, p in zip(test, ridge)}), encoding='utf-8')


if __name__ == '__main__':
    main()
