"""只跑 Kronos 模型：從 stdin 讀一個 JSON（forecast/kronos_ls.model_input 的格式），stdout 印 {"rets": [每個樣本的 24 小時報酬]}。
JSON 有 "frames"（{名稱: 同樣格式}，forecast/mtf24.py 用）時，模型只載入一次、逐個預測，印 {"frames": {名稱: rets}}。

由 scripts/kronos_forecast.py 用另一個 venv（含 torch）呼叫，真單用的 .venv 不裝 torch：
Kronos 原始碼在 KRONOS_DIR（預設 ../_vendor/Kronos，commit 67b630e），venv 在 ../_vendor/kronos_venv。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(os.getenv('KRONOS_DIR', ROOT.parent / '_vendor' / 'Kronos'))))

import pandas as pd  # noqa: E402
import torch  # noqa: E402
from model import Kronos, KronosPredictor, KronosTokenizer  # noqa: E402

MODEL, TOKENIZER = 'NeoQuasar/Kronos-small', 'NeoQuasar/Kronos-Tokenizer-base'


def run(predictor: KronosPredictor, req: dict) -> list[float]:
    df = pd.DataFrame({k: req[k] for k in ('open', 'high', 'low', 'close', 'volume')})
    xt = pd.Series(pd.to_datetime(req['x_ms'], unit='ms'))
    yt = pd.Series(pd.to_datetime(req['y_ms'], unit='ms'))
    n = req['samples']
    torch.manual_seed(req['seed'])
    res = predictor.predict_batch([df] * n, [xt] * n, [yt] * n, pred_len=len(req['y_ms']), sample_count=1,
                                  verbose=False, **req['sampling'])
    now = float(df['close'].iloc[-1])
    return [float(p['close'].iloc[-1]) / now - 1 for p in res]


def main() -> None:
    req = json.loads(sys.stdin.read())
    frames = req.get('frames')
    ctx = max(len(r['close']) for r in frames.values()) if frames else len(req['close'])
    predictor = KronosPredictor(Kronos.from_pretrained(MODEL), KronosTokenizer.from_pretrained(TOKENIZER), max_context=ctx)
    if frames:
        print(json.dumps({'frames': {k: run(predictor, r) for k, r in frames.items()}, 'device': predictor.device}))
    else:
        print(json.dumps({'rets': run(predictor, req), 'device': predictor.device}))

if __name__ == '__main__':
    main()
