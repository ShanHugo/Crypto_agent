"""「我的合約」24 小時多空分析（DESIGN 6.30，2026-11-17 營運者決定）。**沒有驗證過、只記錄，不用金鑰、不下單。**

營運者輸入一個 USDT 永續，Kronos 由大到小依序看 週線 → 日線 → 4 小時 → 1 小時 → 15 分 → 5 分：
- 每個週期各用最近已收盤的 K 線（最多 512 根、至少 30 根）預測下一段：週線 1 根（下一週，當背景）、日線 1 根、
  4 小時 6 根、1 小時 24 根、15 分 96 根、5 分 288 根（後五個都是接下來 24 小時）。每個週期 30 個樣本，記「幾個收漲」與平均漲跌。
- 結論＝日線～5 分這 5 個週期「收漲比例」的平均：≥ 0.6 偏多、≤ 0.4 偏空、中間不明；週線反方向（≤ 0.4／≥ 0.6）標「逆週線」。
- 每次分析都記下來；滿 24 小時用 5 分 K 開盤價對答案（說多漲了、說空跌了算對；不明不算）。
規則面（不進結論，只列出）：週線 20 點均線＋日線 50 日均線方向、2×ATR 停損距離、3～5 倍可用的最高倍數（同每日候選 6.29）、資金費率。
"""
from __future__ import annotations

import statistics as st

M, H, D = 60_000, 3_600_000, 86_400_000
FRAMES = (('1w', 7 * D, 1), ('1d', D, 1), ('4h', 4 * H, 6), ('1h', H, 24), ('15m', 15 * M, 96), ('5m', 5 * M, 288))
LABEL = {'1w': '週線', '1d': '日線', '4h': '4 小時', '1h': '1 小時', '15m': '15 分', '5m': '5 分'}
LOOKBACK, MIN_BARS, SAMPLES = 512, 30, 30
SAMPLING = {'T': 1.0, 'top_k': 0, 'top_p': 0.9}
BULL, BEAR = 0.6, 0.4


def closed(rows: list[list], now: int) -> list[list]:
    """幣安 K 線只留已收盤的（收盤時間 < now），最多最後 512 根。"""
    return [r for r in rows if int(r[6]) < now][-LOOKBACK:]


def model_req(rows: list[list], step: int, steps: int, seed: int) -> dict | None:
    if len(rows) < MIN_BARS:
        return None
    t = [int(r[0]) for r in rows]
    col = lambda i: [float(r[i]) for r in rows]   # noqa: E731
    return {'open': col(1), 'high': col(2), 'low': col(3), 'close': col(4), 'volume': col(5), 'x_ms': t,
            'y_ms': [t[-1] + step * (k + 1) for k in range(steps)], 'seed': seed, 'samples': SAMPLES, 'sampling': SAMPLING}


def frame_row(rets: list[float], last: float) -> dict:
    return {'up': sum(r > 0 for r in rets), 'n': len(rets), 'mean': st.fmean(rets), 'last': last}


def combine(frames: dict[str, dict | None]) -> dict:
    """frames：週期 → frame_row 或 None（資料不夠）。回傳 {'score', 'call'(1/-1/0), 'against_week'}。"""
    ratios = [f['up'] / f['n'] for k, f in frames.items() if k != '1w' and f]
    if not ratios:
        return {'score': None, 'call': 0, 'against_week': False}
    score = st.fmean(ratios)
    call = 1 if score >= BULL else -1 if score <= BEAR else 0
    w = frames.get('1w')
    wr = w['up'] / w['n'] if w else None
    against = wr is not None and ((call > 0 and wr <= BEAR) or (call < 0 and wr >= BULL))
    return {'score': score, 'call': call, 'against_week': against}


def settle(row: dict, px_after: float) -> dict:
    """滿 24 小時的對答案：漲跌幅、說多／說空有沒有對（不明 → hit None）。"""
    ret = px_after / row['price'] - 1
    hit = None if row['call'] == 0 else (row['call'] * ret > 0)
    return {'t': row['t'], 'sym': row['sym'], 'call': row['call'], 'price': row['price'], 'after': px_after, 'ret': ret, 'hit': hit}


def summary(results: list[dict]) -> dict:
    called = [r for r in results if r['hit'] is not None]
    out = {'n': len(results), 'called': len(called), 'hit': sum(r['hit'] for r in called) / len(called) if called else None}
    for c, k in ((1, 'long'), (-1, 'short')):
        xs = [r for r in called if r['call'] == c]
        out[k] = {'n': len(xs), 'hit': sum(r['hit'] for r in xs) / len(xs) if xs else None}
    return out
