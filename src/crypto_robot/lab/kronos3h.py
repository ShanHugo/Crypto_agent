"""第十五輪：Kronos 預測 ETH 未來 3 小時（DESIGN.md 6.3，2026-11-02 使用者同意草案）。**看過結果後不改**。

Kronos（github.com/shiyu-coder/Kronos，MIT）是 K 線預測模型，論文寫訓練資料到 2024-06。
只用 2025-09 之後的資料考一次（避開訓練資料與作者 2024-07 起的測試期），不調參數、不分研究期。
預測在另一個 venv 由 scripts/kronos_predict.py 算好存成 JSON；這裡只讀預測、算成績（不用 torch）。
研究統計用 float（不碰交易路徑）。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from crypto_robot.lab.direction3h import FEE, H, Series, _ms, evaluate, slots

START, END = _ms(2025, 9, 1), _ms(2026, 11, 1)   # 台北 3 小時時點，2025-09-01～2026-10-31
DATASETS = {'max': 'ethtwd', 'binance': 'binance_ethusdt'}
MODEL, TOKENIZER = 'NeoQuasar/Kronos-small', 'NeoQuasar/Kronos-Tokenizer-base'
LOOKBACK, PRED = 512, 3                          # 輸入過去 512 根 1h K 線，預測下 3 根
SAMPLING = {'T': 1.0, 'top_k': 0, 'top_p': 0.9, 'sample_count': 10}
BATCH = 32                                       # 每批固定 32 個時點，seed＝該批第一個時點的小時數
NAME = 'kronos'
# 標準（兩個資料源都要過）：次數、命中率、t、只做多扣成本 > 0、贏隨機第 95 百分位
MIN_N, MIN_HIT, MIN_T = 100, 0.53, 2.0


def exam_slots(eth: Series) -> list[int]:
    """考試期內，前 512 根與後 3 根 K 線都齊全的時點。"""
    return [s for s in slots(START, END) if eth.span(s - LOOKBACK * H, s + 2 * H) is not None]


def batches(xs: list[int]) -> list[list[int]]:
    return [xs[i:i + BATCH] for i in range(0, len(xs), BATCH)]


def direction(pred_ret: float) -> int:
    """預測漲幅超過來回手續費才算看多／看空。"""
    return 1 if pred_ret > FEE else -1 if pred_ret < -FEE else 0


def rows(eth: Series, preds: dict[int, float]) -> list[tuple[int, float, dict]]:
    out = []
    for s in exam_slots(eth):
        if s not in preds:
            continue
        i = eth.idx[s - H]
        out.append((s, eth.c[i + 3] / eth.c[i] - 1, {NAME: direction(preds[s])}))
    return out


def passed(m: dict) -> bool:
    return (m['n'] >= MIN_N and m['hit'] >= MIN_HIT and m['t'] >= MIN_T
            and m.get('long_net', -1) > 0 and m['long_net'] > m['rand95'])


def load_preds(path: Path) -> dict[int, float]:
    return {int(k): v for k, v in json.loads(path.read_text(encoding='utf-8'))['pred'].items()}


def run_exam(eth: dict[str, Series], preds: dict[str, dict[int, float]], path: Path, commit: str) -> dict:
    if path.exists():
        raise RuntimeError('考卷已經考過')
    record = {'model': MODEL, 'commit': commit, 'at': datetime.now(timezone.utc).isoformat(), 'datasets': {}}
    for ds, e in eth.items():
        need = exam_slots(e)
        missing = [s for s in need if s not in preds[ds]]
        if missing:
            raise RuntimeError(f'{ds} 缺 {len(missing)} 個時點的預測，先跑完 scripts/kronos_predict.py')
        rs = rows(e, preds[ds])
        m = evaluate(rs, NAME, rs[len(rs) // 2][0])
        record['datasets'][ds] = {**m, 'blocks': len(rs), 'passed': m['n'] >= 2 and passed(m)}
    record['passed'] = all(d['passed'] for d in record['datasets'].values())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    return record
