"""Kronos＋人工判斷的多空前瞻紀錄（DESIGN.md 6.7，2026-11-02 使用者同意）。**只記錄、不下單**；規則與標準看過結果後不改。

每天台北 02:00 用 Binance ETHUSDT 永續合約過去 512 根 1h K 線，讓 Kronos 預測接下來 24 根（30 個樣本）；
平均漲跌超過來回手續費 0.10% 算看多／看空。營運者看完 08:30 推送後用 scripts/judge.py 記自己的判斷（做多／做空／不做）。
隔天 02:00 結算三種成績：只照 Kronos、只照營運者、兩者同方向才做。成本＝來回 0.10%＋持倉期間實際資金費率（多付空收）。
營運者的判斷從記錄當下的價格起算（看到的漲跌不算進成績）。紀錄只新增、不修改。研究統計用 float。
"""
from __future__ import annotations

import json
import random
import statistics as st
from datetime import datetime, timezone
from pathlib import Path

from crypto_robot.agents.data.resample import DAY_MS, day_start
from crypto_robot.lab.futures import FEE, Funding
from crypto_robot.lab.hourly import H, Series

START_MS = int(datetime(2026, 11, 2, 18, tzinfo=timezone.utc).timestamp() * 1000)   # 台北 2026-11-03 02:00 起算
LOOKBACK, STEPS, SAMPLES = 512, 24, 30
SAMPLING = {'T': 1.0, 'top_k': 0, 'top_p': 0.9}
CHECK_N, FINAL_N, FINAL_T = 60, 150, 2.0
RANDOM_RUNS = 1000
TRACKS = {'kronos': '只照 Kronos', 'user': '只照營運者', 'agree': '兩者同方向'}
CALLS = {'long': 1, 'short': -1, 'flat': 0}


def forecast_ts(now_ms: int) -> int:
    """now 所在台北日的 02:00（UTC 毫秒）。"""
    return day_start(now_ms) + 2 * H


def model_input(eth: Series, ts: int) -> dict | None:
    """ts 之前 512 根已收盤 1h K 線；有缺就 None。"""
    i = eth.span(ts - LOOKBACK * H, ts - H)
    if i is None:
        return None
    j = i + LOOKBACK
    return {'open': eth.o[i:j], 'high': eth.h[i:j], 'low': eth.l[i:j], 'close': eth.c[i:j], 'volume': eth.v[i:j],
            'x_ms': [ts - (LOOKBACK - k) * H for k in range(LOOKBACK)], 'y_ms': [ts + k * H for k in range(STEPS)],
            'seed': ts // H, 'samples': SAMPLES, 'sampling': SAMPLING}


def direction(rets: list[float]) -> int:
    m = st.fmean(rets)
    return 1 if m > FEE else -1 if m < -FEE else 0


def forecast_row(ts: int, entry: float, rets: list[float]) -> dict:
    return {'ts': ts, 'entry': entry, 'mean': st.fmean(rets), 'up': sum(r > 0 for r in rets), 'samples': len(rets),
            'dir': direction(rets), 'recorded_at': datetime.now(timezone.utc).isoformat()}


def read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding='utf-8').splitlines() if x.strip()]


def append(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as f:
        f.write(json.dumps(row, ensure_ascii=False) + '\n')


def open_forecast(forecasts: list[dict], judgments: list[dict], now_ms: int) -> tuple[dict | None, str]:
    """現在可以記判斷的那一筆預測（預測後、隔天 02:00 前、還沒記過）；不行就回傳原因。"""
    live = [f for f in forecasts if f['ts'] <= now_ms < f['ts'] + DAY_MS]
    if not live:
        return None, '今天 02:00 還沒有 Kronos 預測（或已過隔天 02:00）'
    f = live[-1]
    if any(j['ts'] == f['ts'] for j in judgments):
        return None, '這一筆已經記過判斷（紀錄只新增、不修改）'
    return f, ''


def judgment_row(f: dict, call: str, price: float, now_ms: int, reason: str) -> dict:
    return {'ts': f['ts'], 'at': now_ms, 'price': price, 'call': CALLS[call], 'kronos': f['dir'], 'reason': reason,
            'recorded_at': datetime.now(timezone.utc).isoformat()}


def _exit(eth: Series, ts: int) -> float | None:
    i = eth.idx.get(ts + (STEPS - 1) * H)
    return None if i is None else eth.c[i]


def _net(d: int, entry: float, exit_: float, fund: Funding, a: int, b: int) -> float:
    return d * (exit_ / entry - 1) - d * fund.between(a, b) - FEE


def settle(forecasts: list[dict], judgments: list[dict], eth: Series, fund: Funding, start: int = START_MS):
    """回傳（各軌道交易 [{ts, d, net}], 母體 {方向: [每天 02:00 進場的淨報酬]}）；只算已到隔天 02:00 的。"""
    trades = {k: [] for k in TRACKS}
    pool = {1: [], -1: []}
    by_ts = {j['ts']: j for j in judgments}
    for f in forecasts:
        ts = f['ts']
        if ts < start or (x := _exit(eth, ts)) is None:
            continue
        end = ts + STEPS * H
        for d in (1, -1):
            pool[d].append(_net(d, f['entry'], x, fund, ts, end))
        if f['dir']:
            trades['kronos'].append({'ts': ts, 'd': f['dir'], 'net': _net(f['dir'], f['entry'], x, fund, ts, end)})
        j = by_ts.get(ts)
        if j and j['call']:
            t = {'ts': ts, 'd': j['call'], 'net': _net(j['call'], j['price'], x, fund, j['at'], end)}
            trades['user'].append(t)
            if j['call'] == f['dir']:
                trades['agree'].append(t)
    return trades, pool


def status(trades: list[dict], pool: dict[int, list[float]], seed: int = 0) -> dict:
    nets = [t['net'] for t in trades]
    n = len(nets)
    out = {'n': n}
    if not n or not pool[1]:
        return out
    dirs = [t['d'] for t in trades]
    base = {d: st.fmean(p) for d, p in pool.items()}
    out['mean'], out['total'] = st.fmean(nets), sum(nets)
    out['base'] = st.fmean(base[d] for d in dirs)                 # 同方向組成、每天都做的平均＝隨機的期望值
    out['t'] = out['mean'] / (st.stdev(nets) / n ** 0.5) if n > 1 and st.stdev(nets) > 0 else 0.0
    if n >= CHECK_N:
        first = nets[:CHECK_N]
        out['stopped'] = st.fmean(first) <= 0 or st.fmean(first) <= st.fmean(base[d] for d in dirs[:CHECK_N])
    if n >= FINAL_N:
        last, half = nets[:FINAL_N], FINAL_N // 2
        rng, m = random.Random(seed), len(pool[1])
        means = sorted(st.fmean(pool[d][rng.randrange(m)] for d in dirs[:FINAL_N]) for _ in range(RANDOM_RUNS))
        t = st.fmean(last) / (st.stdev(last) / FINAL_N ** 0.5) if st.stdev(last) > 0 else 0.0
        out['final_pass'] = (st.fmean(last) > 0 and st.fmean(last) > means[int(RANDOM_RUNS * 0.95)] and t >= FINAL_T
                             and st.fmean(last[:half]) > 0 and st.fmean(last[half:]) > 0)
    return out


def track_text(name: str, s: dict) -> str:
    head = f'{TRACKS[name]}：{s["n"]} 筆'
    if not s['n'] or 'mean' not in s:
        return head
    line = f'{head}、每筆 {s["mean"]:+.3%}、累計 {s["total"]:+.2%}、t {s["t"]:+.2f}（隨機期望 {s["base"]:+.3%}）'
    if 'final_pass' in s:
        return line + f'；最後判定：{"通過，可以討論" if s["final_pass"] else "沒通過，停止"}'
    if 'stopped' in s:
        return line + f'；中途檢查：{"停止" if s["stopped"] else "繼續"}'
    return line + f'；距中途檢查還差 {CHECK_N - s["n"]} 筆'


def weekly_text(forecasts: list[dict], judgments: list[dict], eth: Series, fund: Funding) -> str:
    trades, pool = settle(forecasts, judgments, eth, fund)
    since = [f for f in forecasts if f['ts'] >= START_MS]
    head = (f'Kronos＋人工判斷多空（前瞻紀錄，只記錄不下單）：預測 {len(since)} 天、你記了 '
            f'{sum(j["ts"] >= START_MS for j in judgments)} 天；中途檢查 {CHECK_N} 筆、最後判定 {FINAL_N} 筆')
    return '\n'.join([head] + [f'　{track_text(k, status(trades[k], pool))}' for k in TRACKS])


def morning_line(forecasts: list[dict], judgments: list[dict], now_ms: int) -> str:
    ts = forecast_ts(now_ms)
    f = next((x for x in reversed(forecasts) if x['ts'] == ts), None)
    if f is None:
        return '🟡 Kronos：今天 02:00 沒有預測（看 logs/kronos_forecast.log）'
    word = {1: '看多', -1: '看空', 0: '不判斷'}[f['dir']]
    done = any(j['ts'] == ts for j in judgments)
    return (f'🔮 Kronos（02:00，只記錄）：{word}，30 個樣本 {f["up"]} 個看漲、平均 {f["mean"]:+.2%}；'
            + ('今天的判斷已記錄' if done else '記你的判斷：scripts/judge.py long|short|flat'))
