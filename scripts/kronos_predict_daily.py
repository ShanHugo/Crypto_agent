"""第十六輪：用 Kronos 預測每天台北 00:00 之後 24 小時（DESIGN.md 6.3）。只算預測，成績在 scripts/research.py --family kronos_daily。

要用另一個裝了 torch 的 venv 執行（不汙染真單用的 .venv），Kronos 原始碼放在 KRONOS_DIR（預設 ../_vendor/Kronos）：

    ..\\_vendor\\kronos_venv\\Scripts\\python.exe scripts/kronos_predict_daily.py --stage dev
    ..\\_vendor\\kronos_venv\\Scripts\\python.exe scripts/kronos_predict_daily.py --stage exam   # 研究期有組合通過才跑

結果存在 data/kronos_daily/<market>_<h1|d1>.json（不進 git）：每個時點 30 個樣本的報酬與高低價差；中斷後重跑會接著做。
"""
from __future__ import annotations

import argparse
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

from crypto_robot.agents.data.resample import DAY_MS, to_daily  # noqa: E402
from crypto_robot.lab import kronos_daily as kd  # noqa: E402
from crypto_robot.lab.direction3h import H, Series  # noqa: E402
from crypto_robot.store.bars import BarStore  # noqa: E402


def inputs(inp: str, eth: Series, days, didx, s: int):
    """時點 s 的輸入 DataFrame、輸入時間、預測時間、現在價格。"""
    if inp == 'h1':
        i = eth.idx[s - kd.LOOKBACK * H]
        j = i + kd.LOOKBACK
        df = pd.DataFrame({'open': eth.o[i:j], 'high': eth.h[i:j], 'low': eth.l[i:j], 'close': eth.c[i:j], 'volume': eth.v[i:j]})
        xt = [s - (kd.LOOKBACK - k) * H for k in range(kd.LOOKBACK)]
        yt = [s + k * H for k in range(kd.INPUTS['h1'])]
    else:
        i = kd.d1_span(days, didx, s)
        bs = days[i:i + kd.LOOKBACK]
        df = pd.DataFrame({'open': [float(b.open) for b in bs], 'high': [float(b.high) for b in bs], 'low': [float(b.low) for b in bs],
                           'close': [float(b.close) for b in bs], 'volume': [float(b.volume) for b in bs]})
        xt = [b.ts for b in bs]
        yt = [s]
    return df, pd.Series(pd.to_datetime(xt, unit='ms')), pd.Series(pd.to_datetime(yt, unit='ms')), float(df['close'].iloc[-1])


def main() -> None:
    sys.stdout.reconfigure(encoding='utf-8')
    ap = argparse.ArgumentParser()
    ap.add_argument('--stage', choices=['dev', 'exam'], required=True)
    args = ap.parse_args()
    predictor = KronosPredictor(Kronos.from_pretrained(kd.MODEL), KronosTokenizer.from_pretrained(kd.TOKENIZER),
                                max_context=kd.LOOKBACK)
    rev = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=KRONOS_DIR, capture_output=True, text=True).stdout.strip()
    store = BarStore(ROOT / 'data' / 'bars.db')
    try:
        bars = {ds: store.load(m, 60) for ds, m in kd.DATASETS.items()}
    finally:
        store.close()
    for ds, market in kd.DATASETS.items():
        eth, days = Series.of(bars[ds]), to_daily(bars[ds])
        didx = {b.ts: i for i, b in enumerate(days)}
        slots = kd.slots_for(eth, days, args.stage)
        for inp, steps in kd.INPUTS.items():
            path = ROOT / 'data' / 'kronos_daily' / f'{market}_{inp}.json'
            out = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {
                'meta': {'model': kd.MODEL, 'tokenizer': kd.TOKENIZER, 'kronos_commit': rev, 'device': predictor.device,
                         'lookback': kd.LOOKBACK, 'steps': steps, 'samples': kd.SAMPLES, **kd.SAMPLING}, 'pred': {}}
            todo = [b for b in kd.batches(slots) if str(b[-1]) not in out['pred']]
            print(f'{ds} {inp}（{args.stage}）：還有 {len(todo)} 批')
            for n, batch in enumerate(todo, 1):
                ins = [inputs(inp, eth, days, didx, s) for s in batch]
                torch.manual_seed(batch[0] // H)
                res = predictor.predict_batch([x[0] for x in ins for _ in range(kd.SAMPLES)],
                                              [x[1] for x in ins for _ in range(kd.SAMPLES)],
                                              [x[2] for x in ins for _ in range(kd.SAMPLES)],
                                              pred_len=steps, sample_count=1, verbose=False, **kd.SAMPLING)
                for k, s in enumerate(batch):
                    now = ins[k][3]
                    ps = res[k * kd.SAMPLES:(k + 1) * kd.SAMPLES]
                    out['pred'][str(s)] = {'ret': [float(p['close'].iloc[-1]) / now - 1 for p in ps],
                                           'range': [(float(p['high'].max()) - float(p['low'].min())) / now for p in ps]}
                path.parent.mkdir(exist_ok=True)
                path.write_text(json.dumps(out), encoding='utf-8')
                if n % 10 == 0 or n == len(todo):
                    print(f'  {n}/{len(todo)}')


if __name__ == '__main__':
    main()
