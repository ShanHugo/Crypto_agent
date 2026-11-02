"""幣安資金費率做多的前瞻紀錄（DESIGN.md 6.7，2026-11-02 使用者同意）。**只記錄、不下單**；規則與標準看過結果後不改。

規則＝第十四輪 `fund_streak-hold8` 的做多那一半：ETHUSDT 永續合約最近 3 期資金費率都低於再往前 90 期的第 25 百分位，
在那一期結算時的價格做多、抱 8 小時，一次只抱一筆；成本來回 0.10%＋持倉期間實際結算的資金費率。
資料全是公開的，所以每週一補算上次之後完成的交易即可；紀錄只新增、不修改。
"""
from __future__ import annotations

import json
import statistics as st
from datetime import datetime, timezone
from pathlib import Path

from crypto_robot.lab.hourly import H, Series
from crypto_robot.lab.futures import Funding, Variant, points, signal, trade_net

START_MS = int(datetime(2026, 11, 2, tzinfo=timezone.utc).timestamp() * 1000)   # commit 之後的第一期（台北 11/02 08:00）
RULE = Variant('fund_streak', 8)
CHECK_N, FINAL_N, FINAL_T = 50, 150, 2.0


def simulate(eth: Series, fund: Funding, start: int = START_MS) -> tuple[list[dict], list[float]]:
    """回傳（已完成的做多交易, 同期間每一期都做多的淨報酬＝隨機基準）。"""
    trades, pool, free = [], [], 0
    for s, k in points(RULE, fund, start, 2 ** 62):
        if eth.span(s - 25 * H, s + (RULE.hours - 1) * H) is None:
            continue
        net = trade_net(eth, fund, s, RULE.hours, 1)
        pool.append(net)
        if s >= free and signal(RULE, eth, fund, s, k) == 1:
            i = eth.idx[s - H]
            trades.append({'ts': s, 'entry': eth.c[i], 'exit': eth.c[i + RULE.hours],
                           'funding': fund.between(s, s + RULE.hours * H), 'net': net})
            free = s + RULE.hours * H
    return trades, pool


def read_log(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding='utf-8').splitlines() if x.strip()]


def update(eth: Series, fund: Funding, path: Path) -> tuple[list[dict], list[float], int]:
    """把還沒記過的已完成交易加到紀錄檔；回傳（全部紀錄, 隨機基準母體, 這次新增筆數）。"""
    logged = read_log(path)
    seen = {r['ts'] for r in logged}
    trades, pool = simulate(eth, fund)
    new = [t for t in trades if t['ts'] not in seen and (not logged or t['ts'] > logged[-1]['ts'])]
    if new:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a', encoding='utf-8') as f:
            for t in new:
                f.write(json.dumps({**t, 'recorded_at': datetime.now(timezone.utc).isoformat()}) + '\n')
    return logged + new, pool, len(new)


def status(rows: list[dict], pool: list[float]) -> dict:
    nets = [r['net'] for r in rows]
    n = len(nets)
    out = {'n': n, 'base': st.fmean(pool) if pool else 0.0}
    if not n:
        return out
    out['mean'] = st.fmean(nets)
    out['total'] = sum(nets)
    out['t'] = st.fmean(nets) / (st.stdev(nets) / n ** 0.5) if n > 1 and st.stdev(nets) > 0 else 0.0
    if n >= CHECK_N:
        first = nets[:CHECK_N]
        out['stopped'] = st.fmean(first) <= 0 or st.fmean(first) <= out['base']
    if n >= FINAL_N:
        last = nets[:FINAL_N]
        half = FINAL_N // 2
        out['final_pass'] = (st.fmean(last) > 0 and st.fmean(last) > out['base']
                             and st.fmean(last) / (st.stdev(last) / FINAL_N ** 0.5) >= FINAL_T
                             and st.fmean(last[:half]) > 0 and st.fmean(last[half:]) > 0)
    return out


def text(s: dict, added: int) -> str:
    head = f'幣安資金費率做多（前瞻紀錄，只記錄不下單）：{s["n"]} 筆（本週新增 {added}）'
    if not s['n']:
        return head + f'；中途檢查 {CHECK_N} 筆、最後判定 {FINAL_N} 筆'
    lines = [head, f'每筆平均 {s["mean"]:+.3%}、累計 {s["total"]:+.2%}、t {s["t"]:+.2f}；同期間每期都做多平均 {s["base"]:+.3%}']
    if 'final_pass' in s:
        lines.append(f'最後判定（前 {FINAL_N} 筆）：{"通過，可以討論" if s["final_pass"] else "沒通過，停止"}')
    elif 'stopped' in s:
        lines.append(f'中途檢查（前 {CHECK_N} 筆）：{"停止條件成立，停止紀錄" if s["stopped"] else "沒觸發停止，繼續記錄"}')
    else:
        lines.append(f'距中途檢查還差 {CHECK_N - s["n"]} 筆')
    return '\n'.join(lines)
