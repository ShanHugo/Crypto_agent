"""第十八輪：Kronos 加外部資訊再訓練（DESIGN.md 6.9）。在 Kronos 的 venv 執行（含 torch），不改 _vendor/Kronos 原始碼。

    ..\\_vendor\\kronos_venv\\Scripts\\python.exe scripts/kronos_cov.py selfcheck          # 新加的層初始為 0：輸出要和原版一樣
    ..\\_vendor\\kronos_venv\\Scripts\\python.exe scripts/kronos_cov.py train K1            # 再訓練、不加資訊
    ..\\_vendor\\kronos_venv\\Scripts\\python.exe scripts/kronos_cov.py train K2            # 再訓練＋7 項外部資訊
    ..\\_vendor\\kronos_venv\\Scripts\\python.exe scripts/kronos_cov.py predict K0 K1 K2    # 研究期每天 00:00 各 30 個樣本
    ..\\_vendor\\kronos_venv\\Scripts\\python.exe scripts/kronos_cov.py predict K2 --exam   # 考卷（研究期 K2 通過才可以）

資料：data/kronos_cov/eth_1h.csv（先在主 venv 執行 scripts/research.py --family kronos_cov --stage data）。
模型存在 ../_vendor/kronos_models/（不進 git）；預測存在 state/research/kronos_cov_preds.jsonl（只新增、可接續）。
外部資訊從「時間向量」同一個入口加進去：每一步的時間欄位後面接 7 項外部資訊，新的線性層初始為 0；預測未來 24 小時時外部資訊固定用當下最後已知的值。
"""
from __future__ import annotations

import json
import math
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(os.getenv('KRONOS_DIR', ROOT.parent / '_vendor' / 'Kronos'))))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import torch  # noqa: E402
from torch import nn  # noqa: E402
from model import Kronos, KronosTokenizer  # noqa: E402
from model.kronos import auto_regressive_inference  # noqa: E402

MODEL, TOKENIZER = 'NeoQuasar/Kronos-small', 'NeoQuasar/Kronos-Tokenizer-base'
CSV = ROOT / 'data' / 'kronos_cov' / 'eth_1h.csv'
MODELS_DIR = ROOT.parent / '_vendor' / 'kronos_models'
PREDS = ROOT / 'state' / 'research' / 'kronos_cov_preds.jsonl'
DEV_RESULT = ROOT / 'state' / 'research' / 'kronos_cov_dev.json'
COVS = ['fund_last', 'fund_7d', 'premium', 'stable_4w', 'netliq_4w', 'dxy_4w', 'real_4w']
FEATS = ['open', 'high', 'low', 'close', 'volume', 'amount']
H, D = 3_600_000, 86_400_000
CTX, PRED, CLIP = 512, 24, 5.0
WIN = CTX + 1                       # 訓練窗：513 根，前 489 根算標準化，最後 24 根是要學的未來
PAST = WIN - PRED
TRAIN = ('2020-01-01', '2024-01-01')
VAL = ('2024-01-01', '2024-07-01')
DEV = ('2024-07-01', '2025-09-01')
EXAM = ('2025-09-01', '2026-11-01')
CFG = {'batch': 32, 'lr': 4e-5, 'epochs': 10, 'patience': 3, 'train_samples': 20_000, 'val_samples': 4_000, 'seed': 100}
SAMPLES, SAMPLING = 30, {'T': 1.0, 'top_k': 0, 'top_p': 0.9}


def ms(day: str) -> int:
    return int(datetime.strptime(day, '%Y-%m-%d').replace(tzinfo=timezone.utc).timestamp() * 1000)


class CovTime(nn.Module):
    """原本的時間向量＋外部資訊向量。stamp 的前 5 欄是時間（分、時、星期、日、月），後面是外部資訊。"""

    def __init__(self, te: nn.Module, n_cov: int, d_model: int):
        super().__init__()
        self.te, self.proj = te, nn.Linear(n_cov, d_model)
        nn.init.zeros_(self.proj.weight)
        nn.init.zeros_(self.proj.bias)

    def forward(self, stamp):
        return self.te(stamp[..., :5]) + self.proj(stamp[..., 5:])


def load(name: str, device: str, trained: bool = True):
    tok = KronosTokenizer.from_pretrained(TOKENIZER).to(device).eval()
    model = Kronos.from_pretrained(MODEL)
    if name == 'K2':
        model.time_emb = CovTime(model.time_emb, len(COVS), model.d_model)
    if name != 'K0' and trained:
        model.load_state_dict(torch.load(MODELS_DIR / f'{name}.pt', map_location='cpu'))
    return tok, model.to(device)


def table() -> pd.DataFrame:
    df = pd.read_csv(CSV)
    df['amount'] = df['volume'] * df[['open', 'high', 'low', 'close']].mean(axis=1)
    t = pd.to_datetime(df['ts'], unit='ms')
    for c, v in (('minute', t.dt.minute), ('hour', t.dt.hour), ('weekday', t.dt.weekday), ('day', t.dt.day), ('month', t.dt.month)):
        df[c] = v
    return df


def stamp_cols(name: str) -> list[str]:
    return ['minute', 'hour', 'weekday', 'day', 'month'] + (COVS if name == 'K2' else [])


def windows(df: pd.DataFrame, name: str, start: str, end: str) -> list[int]:
    """[start, end) 內、513 根連續且（K2）外部資訊都有的訓練窗起點。"""
    ts = df['ts'].to_numpy()
    ok = np.ones(len(df), bool) if name != 'K2' else df[COVS].notna().all(axis=1).to_numpy()
    lo, hi = ms(start), ms(end)
    out = []
    for i in range(len(df) - WIN + 1):
        j = i + WIN - 1
        if ts[i] >= lo and ts[j] < hi and ts[j] - ts[i] == (WIN - 1) * H and ok[i:j + 1].all():
            out.append(i)
    return out


def batch(df: pd.DataFrame, name: str, starts: list[int], device: str):
    x = df[FEATS].to_numpy(np.float32)
    s = df[stamp_cols(name)].to_numpy(np.float32)
    xs, ss = [], []
    for i in starts:
        w = x[i:i + WIN]
        mu, sd = w[:PAST].mean(0), w[:PAST].std(0)
        xs.append(np.clip((w - mu) / (sd + 1e-5), -CLIP, CLIP))
        ss.append(s[i:i + WIN])
    return torch.from_numpy(np.stack(xs)).to(device), torch.from_numpy(np.stack(ss)).to(device)


def step_loss(tok, model, bx, bs):
    with torch.no_grad():
        t0, t1 = tok.encode(bx, half=True)
    logits = model(t0[:, :-1], t1[:, :-1], bs[:, :-1, :])
    return model.head.compute_loss(logits[0], logits[1], t0[:, 1:], t1[:, 1:])[0]


def train(name: str) -> None:
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    torch.manual_seed(CFG['seed'])
    tok, model = load(name, device, trained=False)
    df = table()
    tr, va = windows(df, name, *TRAIN), windows(df, name, *VAL)
    rng = random.Random(CFG['seed'])
    val_idx = [rng.choice(va) for _ in range(CFG['val_samples'])]
    steps = CFG['train_samples'] // CFG['batch']
    opt = torch.optim.AdamW(model.parameters(), lr=CFG['lr'], betas=(0.9, 0.95), weight_decay=0.1)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=CFG['lr'], steps_per_epoch=steps, epochs=CFG['epochs'],
                                                pct_start=0.03, div_factor=10)
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    best, bad, log = math.inf, 0, []
    print(f'{name}: 訓練窗 {len(tr)}、驗證窗 {len(va)}、每輪 {steps} 步、batch {CFG["batch"]}、{device}', flush=True)
    for ep in range(CFG['epochs']):
        model.train()
        t = time.time()
        for k in range(steps):
            bx, bs = batch(df, name, [rng.choice(tr) for _ in range(CFG['batch'])], device)
            loss = step_loss(tok, model, bx, bs)
            opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=3.0)
            opt.step()
            sched.step()
            if k % 100 == 0:
                print(f'  第 {ep + 1} 輪 {k}/{steps} loss {loss.item():.4f}', flush=True)
        model.eval()
        with torch.no_grad():
            vl = [step_loss(tok, model, *batch(df, name, val_idx[i:i + CFG['batch']], device)).item()
                  for i in range(0, len(val_idx), CFG['batch'])]
        v = float(np.mean(vl))
        log.append({'epoch': ep + 1, 'val_loss': v, 'sec': round(time.time() - t)})
        print(f'第 {ep + 1} 輪：驗證 loss {v:.4f}（{time.time() - t:.0f} 秒）', flush=True)
        if v < best:
            best, bad = v, 0
            torch.save(model.state_dict(), MODELS_DIR / f'{name}.pt')
        else:
            bad += 1
            if bad >= CFG['patience']:
                break
    (MODELS_DIR / f'{name}.json').write_text(json.dumps({'cfg': CFG, 'log': log, 'best_val_loss': best}, indent=2), encoding='utf-8')
    print(f'{name} 完成：最好的驗證 loss {best:.4f}', flush=True)


def forecast(tok, model, name: str, df: pd.DataFrame, pos: dict[int, int], t: int, device: str) -> list[float] | None:
    """t（UTC 毫秒，台北 00:00）之前 512 根連續 K 線 → 30 個樣本的 24 小時報酬；缺資料就 None。"""
    j = pos.get(t - H)
    if j is None or j < CTX - 1 or df['ts'].iat[j - CTX + 1] != t - CTX * H:
        return None
    ctx = df.iloc[j - CTX + 1:j + 1]
    cols = stamp_cols(name)
    if name == 'K2' and ctx[COVS].isna().any().any():
        return None
    x = ctx[FEATS].to_numpy(np.float32)
    mu, sd = x.mean(0), x.std(0)
    xn = np.clip((x - mu) / (sd + 1e-5), -CLIP, CLIP)
    fut = pd.to_datetime(pd.Series([t + k * H for k in range(PRED)]), unit='ms')
    ys = pd.DataFrame({'minute': fut.dt.minute, 'hour': fut.dt.hour, 'weekday': fut.dt.weekday, 'day': fut.dt.day, 'month': fut.dt.month})
    for c in cols[5:]:
        ys[c] = ctx[c].iat[-1]                         # 未來的外部資訊不知道：固定用最後已知的值
    rep = lambda a: torch.from_numpy(np.repeat(a[None].astype(np.float32), SAMPLES, axis=0)).to(device)   # noqa: E731
    torch.manual_seed(t // H)
    preds = auto_regressive_inference(tok, model, rep(xn), rep(ctx[cols].to_numpy()), rep(ys[cols].to_numpy()), CTX, PRED,
                                      CLIP, SAMPLING['T'], SAMPLING['top_k'], SAMPLING['top_p'], 1)
    close = preds[:, -1, 3] * (sd[3] + 1e-5) + mu[3]
    return [float(c / x[-1, 3] - 1) for c in close]


def predict(names: list[str], exam: bool) -> None:
    if exam:
        res = json.loads(DEV_RESULT.read_text(encoding='utf-8')) if DEV_RESULT.exists() else {}
        if not res.get('passed', {}).get('K2') or names != ['K2']:
            sys.exit('考卷只考 K2，而且研究期 K2 要先通過（state/research/kronos_cov_dev.json）')
    start, end = EXAM if exam else DEV
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    df = table()
    pos = {int(t): i for i, t in enumerate(df['ts'])}
    done = set()
    if PREDS.exists():
        done = {(r['model'], r['ts']) for r in map(json.loads, PREDS.read_text(encoding='utf-8').splitlines())}
    days = [t for t in range(ms(start) + 16 * H, ms(end), D)]
    PREDS.parent.mkdir(parents=True, exist_ok=True)
    for name in names:
        tok, model = load(name, device)
        model.eval()
        t0, n = time.time(), 0
        for t in days:
            if (name, t) in done:
                continue
            rets = forecast(tok, model, name, df, pos, t, device)
            if rets is None:
                continue
            with PREDS.open('a', encoding='utf-8') as f:
                f.write(json.dumps({'model': name, 'ts': t, 'rets': rets}) + '\n')
            n += 1
            if n % 20 == 0:
                print(f'{name}：{n} 天，{(time.time() - t0) / n:.1f} 秒／天', flush=True)
        print(f'{name} 完成：新增 {n} 天', flush=True)


def selfcheck() -> None:
    """K2 包裝的新層初始為 0：同樣的輸入，輸出要和原版 Kronos 完全一樣；也量一下訓練一步要多久、多少記憶體。"""
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    _, k0 = load('K0', device)
    tok, k2 = load('K2', device, trained=False)
    k0.eval(), k2.eval()
    g = torch.Generator().manual_seed(0)
    bx = torch.randn(4, 64, 6, generator=g).to(device)
    s5 = torch.stack([torch.randint(0, n, (4, 64), generator=g) for n in (60, 24, 7, 32, 13)], -1).float().to(device)
    with torch.no_grad():
        t0, t1 = tok.encode(bx, half=True)
        a = k0(t0, t1, s5)[0]
        b = k2(t0, t1, torch.cat([s5, torch.randn(4, 64, len(COVS), generator=g).to(device)], -1))[0]
    print('原版與 K2（新層為 0）最大差異：', float((a - b).abs().max()))
    df = table()
    tr = windows(df, 'K2', *TRAIN)
    k2.train()
    opt = torch.optim.AdamW(k2.parameters(), lr=CFG['lr'])
    if device == 'cuda':
        torch.cuda.reset_peak_memory_stats()
    t = time.time()
    for _ in range(3):
        loss = step_loss(tok, k2, *batch(df, 'K2', tr[:CFG['batch']], device))
        opt.zero_grad()
        loss.backward()
        opt.step()
    print(f'訓練窗 {len(tr)}；每步 {(time.time() - t) / 3:.2f} 秒；loss {loss.item():.4f}'
          + (f'；GPU 記憶體峰值 {torch.cuda.max_memory_allocated() / 2**30:.1f} GB' if device == 'cuda' else ''))


def main() -> None:
    sys.stdout.reconfigure(encoding='utf-8')
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == 'selfcheck':
        selfcheck()
    elif cmd == 'train':
        train(args[0])
    elif cmd == 'predict':
        predict([a for a in args if not a.startswith('--')], '--exam' in args)
    else:
        sys.exit(__doc__)


if __name__ == '__main__':
    main()
