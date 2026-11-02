"""第十五輪：用 Kronos 預測考試期每個 3 小時時點（DESIGN.md 6.3）。只算預測，成績在 scripts/research.py --family kronos。

要用另一個裝了 torch 的 venv 執行（不汙染真單用的 .venv），Kronos 原始碼放在 KRONOS_DIR（預設 ../_vendor/Kronos）：

    ..\\_vendor\\kronos_venv\\Scripts\\python.exe scripts/kronos_predict.py

結果存在 data/kronos/<market>.json（不進 git）；中斷後重跑會從沒做完的那批接著做。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
KRONOS_DIR = Path(os.getenv('KRONOS_DIR', ROOT.parent / '_vendor' / 'Kronos'))
sys.path.insert(0, str(KRONOS_DIR))

import pandas as pd  # noqa: E402
import torch  # noqa: E402
from model import Kronos, KronosPredictor, KronosTokenizer  # noqa: E402

from crypto_robot.lab import kronos3h as kr  # noqa: E402
from crypto_robot.lab.direction3h import H, Series  # noqa: E402
from crypto_robot.store.bars import BarStore  # noqa: E402


def main() -> None:
    sys.stdout.reconfigure(encoding='utf-8')
    predictor = KronosPredictor(Kronos.from_pretrained(kr.MODEL), KronosTokenizer.from_pretrained(kr.TOKENIZER),
                                max_context=kr.LOOKBACK)
    rev = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=KRONOS_DIR, capture_output=True, text=True).stdout.strip()
    store = BarStore(ROOT / 'data' / 'bars.db')
    try:
        bars = {ds: store.load(m, 60) for ds, m in kr.DATASETS.items()}
    finally:
        store.close()
    for ds, market in kr.DATASETS.items():
        eth = Series.of(bars[ds])
        path = ROOT / 'data' / 'kronos' / f'{market}.json'
        out = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {
            'meta': {'model': kr.MODEL, 'tokenizer': kr.TOKENIZER, 'kronos_commit': rev, 'device': predictor.device,
                     'lookback': kr.LOOKBACK, 'pred': kr.PRED, **kr.SAMPLING}, 'pred': {}}
        todo = [b for b in kr.batches(kr.exam_slots(eth)) if str(b[-1]) not in out['pred']]
        print(f'{ds}：還有 {len(todo)} 批')
        for n, batch in enumerate(todo, 1):
            dfs, xts, yts = [], [], []
            for s in batch:
                i = eth.idx[s - kr.LOOKBACK * H]
                j = i + kr.LOOKBACK
                dfs.append(pd.DataFrame({'open': eth.o[i:j], 'high': eth.h[i:j], 'low': eth.l[i:j],
                                         'close': eth.c[i:j], 'volume': eth.v[i:j]}))
                xts.append(pd.Series(pd.to_datetime([s - (kr.LOOKBACK - k) * H for k in range(kr.LOOKBACK)], unit='ms')))
                yts.append(pd.Series(pd.to_datetime([s + k * H for k in range(kr.PRED)], unit='ms')))
            torch.manual_seed(batch[0] // H)
            res = predictor.predict_batch(dfs, xts, yts, pred_len=kr.PRED, verbose=False, **kr.SAMPLING)
            for s, df, p in zip(batch, dfs, res):
                out['pred'][str(s)] = float(p['close'].iloc[-1]) / float(df['close'].iloc[-1]) - 1
            path.parent.mkdir(exist_ok=True)
            path.write_text(json.dumps(out), encoding='utf-8')
            if n % 10 == 0 or n == len(todo):
                print(f'  {n}/{len(todo)}')


if __name__ == '__main__':
    main()
