"""第十二輪：ETH 未來 3 小時漲跌方向（DESIGN.md 6.3，2026-11-02 使用者同意標準）。**看過結果後不改**。

台北時間 00、03…21 點（UTC 小時 % 3 == 1）各判斷一次，猜接下來 3 小時收盤比現在高或低；區塊不重疊。
同一組 16 個訊號在 MAX（ethtwd）與 Binance（ETHUSDT）各算一次，兩邊都過才算通過（Binance 只加嚴，不放寬）。
研究統計用 float（不碰交易路徑）。
"""
from __future__ import annotations

import json
import random
import statistics as st
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from crypto_robot.core.models import Bar


def _ms(y: int, m: int, d: int) -> int:
    return int(datetime(y, m, d, tzinfo=timezone.utc).timestamp() * 1000)


H = 3_600_000
DEV_START, DEV_MID, DEV_END = _ms(2021, 1, 1), _ms(2022, 7, 1), _ms(2024, 1, 1)
SLOT_MID = _ms(2023, 4, 1)          # 訊號 15 只在研究期後半評估，再對半切
HOLDOUT_END = DEV_START              # 考卷：資料開頭（MAX 2018-02、Binance 2017-08）～ 2020-12-31，只考一次
DATASETS = {
    'max': {'eth': 'ethtwd', 'btc': 'btctwd', 'usdt': 'usdttwd'},
    'binance': {'eth': 'binance_ethusdt', 'btc': 'binance_btcusdt', 'usdt': None},
}
FEE = 0.0032                          # 來回 taker 0.16% × 2
STRESS = 0.001                        # 每次再加 0.1% 滑價
RANDOM_RUNS = 1000
SIGNALS = ['mom3', 'rev3', 'mom24', 'ema20', 'ema9_21', 'rsi14', 'boll', 'break24', 'fake24',
           'big_mom', 'big_rev', 'vol_spike', 'btc3', 'usdt3', 'slot', 'mom24_ema20']

# 研究期通過標準（每個資料源都要全過）
MIN_N, MIN_HIT, MIN_EDGE, MIN_T, MIN_HALF_HIT, MIN_LONG = 300, 0.55, 0.03, 3.0, 0.53, 30
# 考卷標準（只考研究期通過組中 t 較小者最高的一組，兩個資料源都要過）
HO_N, HO_HIT, HO_T = 100, 0.53, 2.0


def _sign(x: float) -> int:
    return (x > 0) - (x < 0)


def _ema(xs: list[float], n: int) -> list[float]:
    k, out = 2 / (n + 1), []
    for x in xs:
        out.append(x if not out else out[-1] + k * (x - out[-1]))
    return out


def _rsi(xs: list[float], n: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(xs)
    gain = loss = 0.0
    for i in range(1, len(xs)):
        d = xs[i] - xs[i - 1]
        g, l_ = max(d, 0.0), max(-d, 0.0)
        if i <= n:
            gain += g / n
            loss += l_ / n
            if i < n:
                continue
        else:
            gain = (gain * (n - 1) + g) / n
            loss = (loss * (n - 1) + l_) / n
        out[i] = 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)
    return out


@dataclass
class Series:
    """一個市場的 1h K 線，加上事先算好的指標。"""
    idx: dict[int, int]
    o: list[float]
    h: list[float]
    l: list[float]
    c: list[float]
    v: list[float]
    ema9: list[float]
    ema20: list[float]
    ema21: list[float]
    rsi: list[float | None]

    @classmethod
    def of(cls, bars: list[Bar]) -> 'Series':
        c = [float(b.close) for b in bars]
        return cls({b.ts: i for i, b in enumerate(bars)}, [float(b.open) for b in bars], [float(b.high) for b in bars],
                   [float(b.low) for b in bars], c, [float(b.volume) for b in bars],
                   _ema(c, 9), _ema(c, 20), _ema(c, 21), _rsi(c))

    def span(self, first_ts: int, last_ts: int) -> int | None:
        """first_ts～last_ts 每一根都在就回傳 first_ts 的位置，有缺就 None。"""
        i, j = self.idx.get(first_ts), self.idx.get(last_ts)
        return i if i is not None and j is not None and j - i == (last_ts - first_ts) // H else None

    def ret3(self, s: int) -> float | None:
        """s 之前 3 小時的報酬（s 是判斷時點，最後一根已收盤的 K 線從 s−1h 開始）。"""
        i = self.span(s - 4 * H, s - H)
        return None if i is None else self.c[i + 3] / self.c[i] - 1


def slots(start: int, end: int) -> list[int]:
    s = start + ((1 - start // H) % 3) * H
    return list(range(s, end, 3 * H))


def features(eth: Series, btc: Series | None, usdt: Series | None, s: int) -> tuple[float, dict[str, int]] | None:
    """時點 s 的未來 3 小時報酬與 15 個訊號方向（slot 另外算）；資料不完整回傳 None。"""
    i0 = eth.span(s - 25 * H, s + 2 * H)
    if i0 is None:
        return None
    i, j = i0 + 24, i0 + 27                       # i：s−1h 那根（現在），j：s+2h 那根（3 小時後收盤）
    c, now = eth.c, eth.c[i]
    fut = c[j] / now - 1
    r3, r24 = now / c[i - 3] - 1, now / c[i - 24] - 1
    win = c[i - 19:i + 1]
    mid, sd = st.fmean(win), st.pstdev(win)
    hi24, lo24 = max(eth.h[i - 24:i]), min(eth.l[i - 24:i])
    brk = 1 if now > hi24 else -1 if now < lo24 else 0
    rsi = eth.rsi[i]
    vavg = st.fmean(eth.v[i - 24:i])
    f = {
        'mom3': _sign(r3), 'rev3': -_sign(r3), 'mom24': _sign(r24),
        'ema20': _sign(now - eth.ema20[i]), 'ema9_21': _sign(eth.ema9[i] - eth.ema21[i]),
        'rsi14': 0 if rsi is None else 1 if rsi < 30 else -1 if rsi > 70 else 0,
        'boll': -1 if now > mid + 2 * sd else 1 if now < mid - 2 * sd else 0,
        'break24': brk, 'fake24': -brk,
        'big_mom': _sign(r3) if abs(r3) > 0.02 else 0, 'big_rev': -_sign(r3) if abs(r3) > 0.02 else 0,
        'vol_spike': _sign(now - eth.o[i]) if vavg > 0 and eth.v[i] > 2 * vavg else 0,
    }
    f['mom24_ema20'] = f['mom24'] if f['mom24'] == f['ema20'] else 0
    b = btc.ret3(s) if btc else None
    u = usdt.ret3(s) if usdt else None
    f['btc3'] = 0 if b is None else _sign(b)
    f['usdt3'] = 0 if u is None else _sign(u)
    return fut, f


def blocks(eth: Series, btc: Series | None, usdt: Series | None, start: int, end: int) -> list[tuple[int, float, dict]]:
    out = []
    for s in slots(start, end):
        r = features(eth, btc, usdt, s)
        if r:
            out.append((s, *r))
    return out


def fit_slots(rows: list[tuple[int, float, dict]]) -> dict[int, int]:
    """每個 UTC 小時（8 個時點）上漲比例 > 一半猜漲、< 一半猜跌。"""
    up: dict[int, list[int]] = {}
    for s, fut, _ in rows:
        up.setdefault(s // H % 24, []).append(fut > 0)
    return {h: _sign(sum(x) / len(x) - 0.5) for h, x in up.items()}


def evaluate(rows: list[tuple[int, float, dict]], name: str, mid: int, seed: int = 0) -> dict:
    """一個訊號在一段資料上的成績；rows 是評估期內全部區塊（隨機進場從這裡抽）。"""
    picks = [(s, d, fut) for s, fut, f in rows if (d := f[name])]
    n = len(picks)
    out = {'n': n}
    if n < 2:
        return out
    signed = [d * fut for _, d, fut in picks]
    sd = st.stdev(signed)
    out['hit'] = sum(x > 0 for x in signed) / n
    out['up_base'] = sum(fut > 0 for *_, fut in picks) / n
    out['t'] = 0.0 if sd == 0 else st.fmean(signed) / (sd / n ** 0.5)
    halves = [[x for (s, *_), x in zip(picks, signed) if (s < mid) == first] for first in (True, False)]
    out['half_hit'] = [sum(x > 0 for x in h) / len(h) if h else 0.0 for h in halves]
    longs = [fut for _, d, fut in picks if d > 0]
    out['n_long'] = len(longs)
    if longs:
        out['long_net'] = st.fmean(longs) - FEE
        pool = [fut for _, fut, _ in rows]
        rng = random.Random(seed)
        means = sorted(st.fmean(rng.sample(pool, len(longs))) - FEE for _ in range(RANDOM_RUNS))
        out['rand95'] = means[int(RANDOM_RUNS * 0.95)]
    return out


def passed(m: dict) -> bool:
    return (m['n'] >= MIN_N and m.get('hit', 0) >= MIN_HIT and m['hit'] >= m['up_base'] + MIN_EDGE
            and m['t'] >= MIN_T and min(m['half_hit']) >= MIN_HALF_HIT and m['n_long'] >= MIN_LONG
            and m['long_net'] > 0 and m['long_net'] > m['rand95'] and m['long_net'] - STRESS > 0)


def ho_passed(m: dict) -> bool:
    return m['n'] >= HO_N and m['hit'] >= HO_HIT and m['t'] >= HO_T and m.get('long_net', -1) > 0


def run_dev(series: dict[str, dict[str, Series | None]]) -> dict:
    res: dict = {}
    for ds, sr in series.items():
        rows = blocks(sr['eth'], sr['btc'], sr['usdt'], DEV_START, DEV_END)
        slot = fit_slots([r for r in rows if r[0] < DEV_MID])
        late = [(s, fut, {**f, 'slot': slot.get(s // H % 24, 0)}) for s, fut, f in rows if s >= DEV_MID]
        res[ds] = {'blocks': len(rows), 'metrics': {
            n: evaluate(late, n, SLOT_MID) if n == 'slot' else evaluate(rows, n, DEV_MID) for n in SIGNALS}}
    for n in SIGNALS:
        need = ['max'] if n == 'usdt3' else list(series)   # Binance 沒有台幣，usdt3 只看 MAX
        res.setdefault('passed', {})[n] = all(passed(res[ds]['metrics'][n]) for ds in need)
    return res


def pick(res: dict) -> str | None:
    ok = [n for n, p in res['passed'].items() if p]
    return max(ok, key=lambda n: min(res[ds]['metrics'][n]['t'] for ds in DATASETS if ds in res
                                     and not (n == 'usdt3' and ds != 'max')), default=None)


def run_holdout(series: dict[str, dict[str, Series | None]], name: str, path: Path, commit: str) -> dict:
    if path.exists():
        raise RuntimeError('考卷已經考過')
    record = {'signal': name, 'commit': commit, 'at': datetime.now(timezone.utc).isoformat(), 'datasets': {}}
    for ds, sr in series.items():
        if name == 'usdt3' and ds != 'max':
            continue
        dev = blocks(sr['eth'], sr['btc'], sr['usdt'], DEV_START, DEV_END)
        rows = blocks(sr['eth'], sr['btc'], sr['usdt'], 0, HOLDOUT_END)
        if name == 'slot':
            slot = fit_slots(dev)
            rows = [(s, fut, {**f, 'slot': slot.get(s // H % 24, 0)}) for s, fut, f in rows]
        mid = rows[len(rows) // 2][0] if rows else 0
        m = evaluate(rows, name, mid)
        record['datasets'][ds] = {**m, 'blocks': len(rows), 'passed': ho_passed(m)}
    record['passed'] = all(d['passed'] for d in record['datasets'].values())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    return record


def dev_report(res: dict, when: str) -> str:
    lines = [f'# 第十二輪：ETH 3 小時方向（研究期 2021-01-01～2023-12-31）', '', f'產生時間 {when}。成本：來回 {FEE:.2%}，壓力再加 {STRESS:.1%}。', '',
             f'通過（每個資料源都要）：次數 ≥ {MIN_N}、命中率 ≥ {MIN_HIT:.0%} 且比同區塊猜漲高 {MIN_EDGE:.0%}、t ≥ {MIN_T:g}、'
             f'前後半命中率 ≥ {MIN_HALF_HIT:.0%}、只做多 ≥ {MIN_LONG} 次且扣成本平均 > 0、贏隨機第 95 百分位、加滑價仍 > 0。', '']
    for ds in DATASETS:
        if ds not in res:
            continue
        lines += [f'## {ds}（{res[ds]["blocks"]} 個區塊）', '',
                  '| 訊號 | 次數 | 命中率 | 猜漲 | t | 前半／後半 | 做多次數 | 做多淨報酬 | 隨機95 | 過 |', '|---|---|---|---|---|---|---|---|---|---|']
        for n in SIGNALS:
            m = res[ds]['metrics'][n]
            if m['n'] < 2:
                lines.append(f'| {n} | {m["n"]} | – | – | – | – | – | – | – | ✗ |')
                continue
            ln = f'{m["long_net"]:+.3%}' if 'long_net' in m else '–'
            r95 = f'{m["rand95"]:+.3%}' if 'rand95' in m else '–'
            lines.append(f'| {n} | {m["n"]} | {m["hit"]:.1%} | {m["up_base"]:.1%} | {m["t"]:+.2f} | '
                         f'{m["half_hit"][0]:.1%}／{m["half_hit"][1]:.1%} | {m["n_long"]} | {ln} | {r95} | {"✓" if passed(m) else "✗"} |')
        lines.append('')
    ok = [n for n, p in res['passed'].items() if p]
    lines += ['## 結論', '', f'兩邊都通過：{"、".join(ok) if ok else "沒有"}。' + (f'考卷要考：{pick(res)}。' if ok else '不進考卷。'), '',
              'slot 只在研究期後半評估（前半用來找時段），前後半是 2022-07～2023-03／2023-04～12。usdt3 只看 MAX。']
    return '\n'.join(lines) + '\n'
