"""第十六輪：Kronos 預測隔天（DESIGN.md 6.3，2026-11-02 使用者同意草案）。**看過結果後不改**。

每天台北 00:00（UTC 16:00）預測接下來 24 小時；預測由 scripts/kronos_predict_daily.py 在另一個 venv 算好（每次 30 個樣本）。
方向 1：猜隔天漲跌（MAX＋Binance）；方向 2：用預測波動調整三資產再平衡；方向 3：用預測漲跌延後再平衡（只 MAX）。
研究期 2024-07-01～2025-08-31（Kronos 訓練資料到 2024-06）；考卷 2025-09-01～2026-10-31（第十五輪看過 3 小時預測，打折）。
研究統計用 float；再平衡回測沿用 multi_backtest（Decimal）。
"""
from __future__ import annotations

import json
import random
import statistics as st
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from crypto_robot.agents.data.resample import DAY_MS, day_start
from crypto_robot.agents.strategy.rebalance_multi import MultiParams, needs_rebalance, target_deltas
from crypto_robot.core.models import Bar
from crypto_robot.lab.common import COSTS
from crypto_robot.lab.direction3h import FEE, H, STRESS, Series, _ms, evaluate
from crypto_robot.lab.metrics import segment_return
from crypto_robot.lab.multi_backtest import run_multi
from crypto_robot.lab.multi_variants import by_name

TPE = 8 * H
PERIODS = {'dev': (_ms(2024, 7, 1) - TPE, _ms(2025, 9, 1) - TPE), 'exam': (_ms(2025, 9, 1) - TPE, _ms(2026, 11, 1) - TPE)}
MIDS = {'dev': _ms(2025, 1, 16) - TPE, 'exam': _ms(2026, 3, 17) - TPE}     # 前後半切點（約在中間）
DATASETS = {'max': 'ethtwd', 'binance': 'binance_ethusdt'}
MODEL, TOKENIZER = 'NeoQuasar/Kronos-small', 'NeoQuasar/Kronos-Tokenizer-base'
LOOKBACK, SAMPLES = 512, 30
INPUTS = {'h1': 24, 'd1': 1}                     # 輸入 512 根 1h 預測 24 根／512 根日 K 預測 1 根
SAMPLING = {'T': 1.0, 'top_k': 0, 'top_p': 0.9}
BATCH_DAYS = 8                                   # 每批 8 天 × 30 樣本，seed＝該批第一個時點的小時數
AGREE = 24                                       # 30 個樣本中至少 24 個（80%）同方向

DIR1 = ['h1_mean', 'h1_agree', 'd1_mean', 'd1_agree']
DIR23 = ['vol_band10', 'vol_skip', 'filter0.32', 'filter1']
VARIANTS = DIR1 + DIR23
BASE = by_name('rebal3-eth50-cashusdt75-band5').params
WIDE = MultiParams(BASE.targets, Decimal('0.10'))
VOL_WINDOW = 60                                  # 預測波動和過去 60 天的預測中位數比；不滿 60 天不切換
FILTER_X = {'filter0.32': 0.0032, 'filter1': 0.01}
RANDOM_RUNS = 1000

# 方向 1：研究期（兩個資料源都要全過）
MIN_N, MIN_N_AGREE, MIN_HIT, MIN_HALF_HIT, MIN_T = 100, 60, 0.55, 0.52, 2.5
# 方向 1：考卷
HO_N, HO_HIT, HO_T = 50, 0.53, 2.0
MIN_DELAYS = 5                                   # 方向 3 延後少於 5 次＝分不出來，算沒過


def day_slots(start: int, end: int) -> list[int]:
    """台北每天 00:00 的時點（UTC 毫秒）。"""
    s = day_start(start)
    s = s if s >= start else s + DAY_MS
    return list(range(s, end, DAY_MS))


def d1_span(days: list[Bar], idx: dict[int, int], s: int) -> int | None:
    """時點 s 前 512 個台北日都齊全就回傳第一天的位置。"""
    i = idx.get(s - LOOKBACK * DAY_MS)
    j = idx.get(s - DAY_MS)
    return i if i is not None and j is not None and j - i == LOOKBACK - 1 else None


def slots_for(eth: Series, days: list[Bar], stage: str) -> list[int]:
    idx = {b.ts: i for i, b in enumerate(days)}
    return [s for s in day_slots(*PERIODS[stage])
            if eth.span(s - LOOKBACK * H, s + 23 * H) is not None and d1_span(days, idx, s) is not None]


def batches(xs: list[int]) -> list[list[int]]:
    return [xs[i:i + BATCH_DAYS] for i in range(0, len(xs), BATCH_DAYS)]


def signal(name: str, rets: list[float]) -> int:
    m = st.fmean(rets)
    d = 1 if m > FEE else -1 if m < -FEE else 0
    if d and name.endswith('agree') and sum((r > 0) == (d > 0) for r in rets if r) < AGREE:
        return 0
    return d


def dir1_rows(eth: Series, preds: dict[int, dict], name: str, slots: list[int]) -> list[tuple[int, float, dict]]:
    out = []
    for s in slots:
        i = eth.idx[s - H]
        out.append((s, eth.c[i + 24] / eth.c[i] - 1, {name: signal(name, preds[s]['ret'])}))
    return out


def dir1_passed(m: dict, name: str) -> bool:
    return (m['n'] >= (MIN_N_AGREE if name.endswith('agree') else MIN_N) and m['hit'] >= MIN_HIT
            and min(m['half_hit']) >= MIN_HALF_HIT and m['t'] >= MIN_T and m.get('long_net', -1) > 0
            and m['long_net'] > m['rand95'] and m['long_net'] - STRESS > 0)


def dir1_ho_passed(m: dict) -> bool:
    return (m['n'] >= HO_N and m['hit'] >= HO_HIT and m['t'] >= HO_T
            and m.get('long_net', -1) > 0 and m['long_net'] > m['rand95'])


def _rank(xs: list[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    for k, i in enumerate(order):
        r[i] = float(k)
    return r


def spearman(a: list[float], b: list[float]) -> float:
    ra, rb = _rank(a), _rank(b)
    return st.correlation(ra, rb) if len(a) > 2 else 0.0


def vol_check(days: list[Bar], preds: dict[int, dict], slots: list[int]) -> dict:
    """預測波動（30 樣本平均的高低價差）跟「昨天的實際價差」比，誰和隔天實際價差的排名相關較高。"""
    by = {b.ts: b for b in days}
    pred, naive, real = [], [], []
    for s in slots:
        today, yday = by.get(s), by.get(s - DAY_MS)
        if today is None or yday is None:
            continue
        pred.append(st.fmean(preds[s]['range']))
        naive.append(float((yday.high - yday.low) / yday.close))
        real.append(float((today.high - today.low) / yday.close))
    k, n = spearman(pred, real), spearman(naive, real)
    return {'n': len(real), 'kronos': k, 'naive': n, 'passed': k > n}


def high_vol_days(preds: dict[int, dict], slots: list[int]) -> set[int]:
    """預測波動 > 過去 60 個時點預測波動中位數的日子（前 60 個時點不判斷）。日 K ts＝時點前一天。"""
    vals = [st.fmean(preds[s]['range']) for s in slots]
    return {slots[i] - DAY_MS for i in range(VOL_WINDOW, len(slots)) if vals[i] > st.median(vals[i - VOL_WINDOW:i])}


def rebalance_rule(name: str, preds: dict[int, dict], slots: list[int], high: set[int] | None = None):
    """回傳給 run_multi 的 decide(ts, qty, cash, closes)：ts 是日 K（台北日）開始，收盤＝隔天 00:00 的時點。"""
    high = high_vol_days(preds, slots) if high is None else high
    delays = []

    def decide(ts, qty, cash, closes):
        if name == 'vol_band10':
            return needs_rebalance(qty, cash, closes, WIDE if ts in high else BASE)
        if name == 'vol_skip':
            return ts not in high and needs_rebalance(qty, cash, closes, BASE)
        if not needs_rebalance(qty, cash, closes, BASE):
            return False
        p = preds.get(ts + DAY_MS)
        if p is None:
            return True
        x, r, d = FILTER_X[name], st.fmean(p['ret']), target_deltas(qty, cash, closes, BASE)['eth']
        if (d > 0 and r < -x) or (d < 0 and r > x):
            delays.append(ts)
            return False
        return True
    decide.delays = delays
    return decide


def random_rule(rng: random.Random, kind: str, k: int, eligible: list[int], prob: float):
    """隨機基準：方向 2 隨機挑同樣天數當「波動高」；方向 3 每次觸發以同樣機率延後。"""
    if kind in ('vol_band10', 'vol_skip'):
        high = set(rng.sample(eligible, min(k, len(eligible))))

        def decide(ts, qty, cash, closes):
            if kind == 'vol_band10':
                return needs_rebalance(qty, cash, closes, WIDE if ts in high else BASE)
            return ts not in high and needs_rebalance(qty, cash, closes, BASE)
        return decide

    def decide(ts, qty, cash, closes):
        return needs_rebalance(qty, cash, closes, BASE) and rng.random() >= prob
    return decide


def dir23_eval(days: dict[str, list[Bar]], preds: dict[int, dict], slots: list[int], stage: str,
               name: str, seed: int = 0) -> dict:
    start, end = PERIODS[stage]
    mid = MIDS[stage]
    base = run_multi(days, start, end, BASE, COSTS)
    rule = rebalance_rule(name, preds, slots)
    res = run_multi(days, start, end, BASE, COSTS, decide=rule)
    ret = float(res.equity[-1][1] / res.initial) - 1
    bret = float(base.equity[-1][1] / base.initial) - 1
    halves = [segment_return(res, start, mid) - segment_return(base, start, mid),
              segment_return(res, mid, end) - segment_return(base, mid, end)]
    high = high_vol_days(preds, slots)
    eligible = [s - DAY_MS for s in slots[VOL_WINDOW:]]
    triggers = len({x.entry_ts for x in base.trades if x.reason.startswith('rebal')})
    n_delay = len(rule.delays)
    prob = n_delay / max(n_delay + len({x.entry_ts for x in res.trades if x.reason.startswith('rebal')}), 1)
    rng = random.Random(seed)
    rand = sorted(float(run_multi(days, start, end, BASE, COSTS,
                                  decide=random_rule(rng, name, len(high), eligible, prob)).equity[-1][1] / res.initial) - 1
                  for _ in range(RANDOM_RUNS))
    out = {'ret': ret, 'base_ret': bret, 'halves': halves, 'rand95': rand[int(RANDOM_RUNS * 0.95)],
           'high_days': len(high), 'delays': n_delay, 'base_triggers': triggers,
           'rebal_days': len({x.entry_ts for x in res.trades if x.reason.startswith('rebal')})}
    out['passed'] = (ret > bret and min(halves) > 0 and ret > out['rand95']
                     and (name in ('vol_band10', 'vol_skip') or n_delay >= MIN_DELAYS))
    return out


def run(stage: str, eth: dict[str, Series], days: dict[str, list[Bar]], usdt_days: list[Bar],
        preds: dict[str, dict[str, dict[int, dict]]], slots: dict[str, list[int]], only: str | None = None) -> dict:
    """preds[資料源][h1|d1][時點] = {'ret': [30], 'range': [30]}。only：考卷只考一組。"""
    res: dict = {'dir1': {}, 'dir23': {}}
    for name in DIR1:
        if only and name != only:
            continue
        inp = name.split('_')[0]
        ms = {}
        for ds in DATASETS:
            rows = dir1_rows(eth[ds], preds[ds][inp], name, slots[ds])
            ms[ds] = evaluate(rows, name, MIDS[stage])
        ok = dir1_ho_passed if stage == 'exam' else (lambda m, n=name: dir1_passed(m, n))
        res['dir1'][name] = {'metrics': ms, 'passed': all(m['n'] >= 2 and ok(m) for m in ms.values())}
    mx = {'eth': days['max'], 'usdt': usdt_days}
    h1 = preds['max']['h1']
    vc = vol_check(days['max'], h1, slots['max'])
    res['vol_check'] = vc
    for name in DIR23:
        if only and name != only:
            continue
        r = dir23_eval(mx, h1, slots['max'], stage, name)
        if name in ('vol_band10', 'vol_skip'):
            r['passed'] = r['passed'] and vc['passed']
        res['dir23'][name] = r
    return res


def pick(res: dict) -> str | None:
    """事先訂的挑法：方向 1 有通過就挑兩個資料源 t 較小者最高的；沒有才從方向 2、3 挑超額報酬最高的。"""
    ok1 = [n for n, r in res['dir1'].items() if r['passed']]
    if ok1:
        return max(ok1, key=lambda n: min(m['t'] for m in res['dir1'][n]['metrics'].values()))
    ok2 = [n for n, r in res['dir23'].items() if r['passed']]
    return max(ok2, key=lambda n: res['dir23'][n]['ret'] - res['dir23'][n]['base_ret'], default=None)


def load_preds(path: Path) -> dict[int, dict]:
    return {int(k): v for k, v in json.loads(path.read_text(encoding='utf-8'))['pred'].items()}


def save_exam(res: dict, name: str, path: Path, commit: str) -> dict:
    if path.exists():
        raise RuntimeError('考卷已經考過')
    r = res['dir1'].get(name) or res['dir23'][name]
    record = {'variant': name, 'commit': commit, 'at': datetime.now(timezone.utc).isoformat(), 'result': r,
              'vol_check': res['vol_check'], 'passed': r['passed']}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    return record


def report(res: dict, stage: str, when: str) -> str:
    title = '研究期 2024-07-01～2025-08-31' if stage == 'dev' else '考卷 2025-09-01～2026-10-31'
    lines = [f'# 第十六輪：Kronos 預測隔天（{title}）', '', f'產生時間 {when}。模型 {MODEL}，每天台北 00:00 預測，30 個樣本。', '',
             '## 方向 1：猜隔天漲跌（MAX、Binance 都要過）', '',
             '| 組合 | 資料 | 次數 | 命中率 | 猜漲 | t | 前半／後半 | 做多次數 | 做多淨報酬 | 隨機95 |', '|---|---|---|---|---|---|---|---|---|---|']
    for name, r in res['dir1'].items():
        for ds, m in r['metrics'].items():
            if m['n'] < 2:
                lines.append(f'| {name} | {ds} | {m["n"]} | – | – | – | – | – | – | – |')
                continue
            ln = f'{m["long_net"]:+.3%}' if 'long_net' in m else '–'
            r95 = f'{m["rand95"]:+.3%}' if 'rand95' in m else '–'
            lines.append(f'| {name} | {ds} | {m["n"]} | {m["hit"]:.1%} | {m["up_base"]:.1%} | {m["t"]:+.2f} | '
                         f'{m["half_hit"][0]:.1%}／{m["half_hit"][1]:.1%} | {m["n_long"]} | {ln} | {r95} |')
        lines.append(f'| **{name}** | | | | | | | | | {"✓ 通過" if r["passed"] else "✗"} |')
    vc = res['vol_check']
    lines += ['', '## 方向 2、3：三資產再平衡（只 MAX，比較對象＝現在的 band5）', '',
              f'波動預測檢查（排名相關，{vc["n"]} 天）：Kronos {vc["kronos"]:+.3f}、昨天的實際價差 {vc["naive"]:+.3f} → '
              f'{"Kronos 較好" if vc["passed"] else "Kronos 沒贏，方向 2 不算通過"}', '',
              '| 組合 | 報酬 | band5 | 前半差距 | 後半差距 | 隨機95 | 波動高天數 | 延後次數 | 再平衡天數 | 過 |', '|---|---|---|---|---|---|---|---|---|---|']
    for name, r in res['dir23'].items():
        lines.append(f'| {name} | {r["ret"]:+.2%} | {r["base_ret"]:+.2%} | {r["halves"][0]:+.2%} | {r["halves"][1]:+.2%} | '
                     f'{r["rand95"]:+.2%} | {r["high_days"] if name.startswith("vol") else "–"} | {r["delays"] if name.startswith("filter") else "–"} | {r["rebal_days"]} | {"✓" if r["passed"] else "✗"} |')
    if stage == 'dev':
        p = pick(res)
        lines += ['', '## 結論', '', f'考卷要考：{p}。' if p else '沒有一組通過，不進考卷。']
    return '\n'.join(lines) + '\n'
