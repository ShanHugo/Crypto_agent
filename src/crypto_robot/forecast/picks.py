"""每日 5 檔合約候選（DESIGN 6.29，2026-11-16 營運者決定）。**沒有驗證過、只記錄，不用金鑰、不下單**；營運者自己決定要不要手動下單。

來源＝`trend`（6.28）的日線規則，每天 UTC 0 點（台北 08:00）用前一天收完的日 K：
- 名單：`trend` 本月名單（成交金額前 30 名、上市滿 90 天）。
- 方向：週線（往回每 7 天一個收盤、20 點平均）和日線 50 日均線同向 → 做多／做空；不同向不列。
- 排序：先放當天創 20 日新高／新低的（`trend` 原本的進場訊號），不夠 5 檔再放只有方向一致的；各自照 30 天標準化報酬絕對值由大到小。
- 參考進場價：當天日 K 開盤價。停損＝進場 ∓ 2 × ATR(14)；止盈＝進場 ± 4 × ATR（停損距離的 2 倍）。
- 槓桿只在 3～5 倍：取「停損距離 + 2% 緩衝 < 強平距離（1/槓桿 − 1% 維持保證金，逐倉估算）」的最高倍數；3 倍也不行就不列，換下一檔。
記分：從進場那根 15 分 K 起，先碰止盈或停損就出場（同一根都碰算停損；開盤已跳過就用開盤價）；滿 10 天用開盤價出場。
扣吃單 0.05% × 2，不含資金費率。報酬換成保證金報酬＝槓桿 × 價格淨報酬。
"""
from __future__ import annotations

import math
import statistics as st

from crypto_robot.forecast import trend

N_PICKS = 5
TP_R = 2.0
LEVS = (5, 4, 3)
MMR, BUFFER = 0.01, 0.02


def lean(d: trend.Daily, T: int) -> tuple[int, float, float, bool] | None:
    """(方向, 30 天標準化報酬絕對值, ATR, 是否創 20 日新高／新低)；週線和日線不同向回 None。"""
    k = d.pos.get(T - trend.D)
    if k is None or k < 133 or d.t[k] - d.t[k - trend.ATR_N] != trend.ATR_N * trend.D:
        return None
    c = d.c
    w_ma = c[k] / (sum(c[k - 7 * j] for j in range(20)) / 20) - 1
    d50 = c[k] / (sum(c[k - 49:k + 1]) / 50) - 1
    side = 1 if w_ma > 0 and d50 > 0 else -1 if w_ma < 0 and d50 < 0 else 0
    if not side:
        return None
    tr = [max(d.h[j] - d.l[j], abs(d.h[j] - c[j - 1]), abs(d.l[j] - c[j - 1])) for j in range(k - trend.ATR_N + 1, k + 1)]
    r = [c[j] / c[j - 1] - 1 for j in range(k - 19, k + 1)]
    sd = st.stdev(r)
    z30 = (c[k] / c[k - 30] - 1) / (sd * math.sqrt(30)) if sd else 0.0
    last20 = c[k - 19:k + 1]
    breakout = c[k] >= max(last20) if side > 0 else c[k] <= min(last20)
    return side, abs(z30), sum(tr) / trend.ATR_N, breakout


def leverage(stop_pct: float) -> int | None:
    """3～5 倍裡，停損一定比強平先到的最高倍數；都不行回 None。"""
    for lev in LEVS:
        if stop_pct + BUFFER < 1 / lev - MMR:
            return lev
    return None


def choose(daily: dict[str, trend.Daily], names: list[str], T: int) -> list[dict]:
    cands = []
    for sym in names:
        d = daily.get(sym)
        i = d.pos.get(T) if d else None
        sig = lean(d, T) if i is not None else None
        if not sig:
            continue
        side, strength, atr, breakout = sig
        e = d.o[i]
        stop_pct = trend.STOP_K * atr / e
        lev = leverage(stop_pct)
        if lev is None:
            continue
        cands.append((not breakout, -strength, sym, {
            't': T, 'sym': sym, 'side': side, 'breakout': breakout, 'entry': e,
            'stop': e - side * trend.STOP_K * atr, 'tp': e + side * TP_R * trend.STOP_K * atr,
            'stop_pct': stop_pct, 'lev': lev,
            'loss_margin': -lev * (stop_pct + 2 * trend.COST), 'gain_margin': lev * (TP_R * stop_pct - 2 * trend.COST)}))
    return [x[3] for x in sorted(cands)[:N_PICKS]]


def score(p: dict, b: trend.K15) -> dict | None:
    """b＝從 p['t'] 起的 15 分 K（只含收完的）。結束了回結果，還沒結束回 None。"""
    side, e, sl, tp = p['side'], p['entry'], p['stop'], p['tp']
    end = p['t'] + trend.HOLD * trend.D

    def out(px: float, t: int, how: str) -> dict:
        net = side * (px / e - 1) - 2 * trend.COST
        return {'t': p['t'], 'sym': p['sym'], 'side': side, 'lev': p['lev'], 'entry': e, 'exit_px': px, 'exit_t': t,
                'how': how, 'net': net, 'r': net / p['stop_pct'], 'margin': p['lev'] * net}

    for j in range(len(b.t)):
        if b.t[j] >= end:
            return out(b.o[j], b.t[j], 'expire')
        o, h, lo = b.o[j], b.h[j], b.l[j]
        if j > 0 and side * (o - sl) <= 0:
            return out(o, b.t[j], 'stop')
        if j > 0 and side * (o - tp) >= 0:
            return out(o, b.t[j], 'tp')
        if (lo <= sl) if side > 0 else (h >= sl):
            return out(sl, b.t[j], 'stop')
        if (h >= tp) if side > 0 else (lo <= tp):
            return out(tp, b.t[j], 'tp')
    return None


def summary(results: list[dict]) -> dict:
    rs = sorted(results, key=lambda r: (r['exit_t'], r['sym']))
    n = len(rs)
    streak = worst = 0
    for r in rs:
        streak = streak + 1 if r['net'] <= 0 else 0
        worst = max(worst, streak)
    return {'n': n, 'win': sum(r['net'] > 0 for r in rs) / n if n else None,
            'tp': sum(r['how'] == 'tp' for r in rs), 'stop': sum(r['how'] == 'stop' for r in rs),
            'expire': sum(r['how'] == 'expire' for r in rs),
            'avg_r': st.fmean(r['r'] for r in rs) if n else None,
            'avg_margin': st.fmean(r['margin'] for r in rs) if n else None, 'worst_streak': worst}
