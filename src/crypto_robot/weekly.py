"""每週事實檢討（DESIGN.md 6.6 第 2 關的成績單）：只列數字、不給下單建議。

每週一 08:30 報告之後產生（scripts/weekly_review.py），存成 reports/weekly_YYYYMMDD.md 並推送 Discord。
函式都是純函式：資料由呼叫端讀好傳入；數字一律由這裡計算（本地 AI 只能寫摘要，不能加數字或建議）。

比較的起點是「真單第一天交易後」（權益與比例都用 01:35 當下的中間價估值）：
- 真單：每天執行紀錄（state/live/live.db 的 runs）的權益。
- 主模擬帳戶：同一天的模擬權益（同樣的規則，用盤口模擬成交）。
- 一直持有 ETH：ETH 價格變化。
- 同比例不再平衡：第一天交易後的比例放著不動。
入金或出金會讓真單權益跳動；目前沒有自動扣除，報告會提醒。
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal

ONE = Decimal(1)
GATE1_DAYS = 14          # 第 1 關：系統可靠，現貨真單跑滿 2 週
GATE2_DAYS = 90          # 第 2 關：真單紀錄約 3 個月
OK_RUN = ('done', 'hold')


def _d(v) -> dict[str, Decimal]:
    if not v:
        return {}
    return {k: Decimal(x) for k, x in (json.loads(v) if isinstance(v, str) else v).items()}


def _equity(run: dict) -> Decimal | None:
    v = run.get('equity_after') or run.get('equity_before')
    return None if v is None else Decimal(v)


def pct(x: Decimal | None) -> str:
    return '—' if x is None else f'{x:+.2%}'


def max_drawdown(values: list[Decimal]) -> Decimal:
    """最大回撤（負數或 0）。"""
    peak, worst = None, Decimal(0)
    for v in values:
        peak = v if peak is None or v > peak else peak
        worst = min(worst, v / peak - 1)
    return worst


def slippage_bp(side: str, avg: Decimal, mid: Decimal) -> Decimal:
    """成交均價和中間價的差（基點），正數代表對我們不利。"""
    return ((avg / mid - 1) if side == 'buy' else (1 - avg / mid)) * 10000


def build(today: date, live_runs: list[dict], live_orders: list[dict], paper_hist: list[dict], paper_trades: list[dict]) -> dict:
    """today：檢討日（週一）。期間是 today 前 7 天到 today（不含 today-7）。"""
    runs = sorted(live_runs, key=lambda r: r['date'])
    first = next((r for r in runs if r['status'] in OK_RUN), None)
    out: dict = {'date': today, 'start': today - timedelta(days=6), 'live_start': None}
    if first is None:
        return out
    d0 = first['date']
    out['live_start'] = date.fromisoformat(d0)
    week_from = (today - timedelta(days=6)).isoformat()
    in_week = lambda d: week_from <= d <= today.isoformat()          # noqa: E731
    upto = [r for r in runs if d0 <= r['date'] <= today.isoformat() and _equity(r) is not None]
    last = upto[-1]

    # 真單：從入金（第一天交易前）起算，含第一天的手續費與價差
    deposit = Decimal(first['equity_before'])
    end = _equity(last)
    out['live'] = {'deposit': deposit, 'end': end, 'since_deposit': end / deposit - 1, 'end_date': last['date']}

    # 同一起點（第一天交易後）的比較
    base_live = _equity(first)
    p0, p1 = _d(first['prices']), _d(last['prices'])
    w0 = _d(first.get('weights_after') or first.get('weights_before'))
    paper = {h['date']: Decimal(h['equity']) for h in paper_hist}
    paper_end = paper.get(last['date'])
    cmp = {
        '真單': end / base_live - 1,
        '主模擬帳戶': (paper_end / paper[d0] - 1) if d0 in paper and paper_end is not None else None,
        '一直持有 ETH': p1['eth'] / p0['eth'] - 1 if 'eth' in p0 and 'eth' in p1 else None,
        '同比例不再平衡': sum((w * p1[a] / p0[a] for a, w in w0.items() if a != 'twd' and a in p1 and a in p0), Decimal(0))
                         + w0.get('twd', Decimal(0)) - 1 if w0 else None,
    }
    out['compare'] = cmp
    out['gap'] = None if cmp['主模擬帳戶'] is None else cmp['真單'] - cmp['主模擬帳戶']
    out['drawdown'] = max_drawdown([_equity(r) for r in upto])

    # 本週
    wk = [r for r in upto if in_week(r['date'])]
    before = [r for r in upto if r['date'] < week_from]
    wk_base = _equity(before[-1]) if before else base_live
    out['week'] = {'live': end / wk_base - 1 if wk else None}
    pw = [d for d in paper if in_week(d)]
    pb = [d for d in paper if d0 <= d < week_from]
    if pw and (pb or d0 in paper):
        out['week']['paper'] = paper[max(pw)] / paper[max(pb) if pb else d0] - 1

    # 營運：本週每一天（真單開始之後）的執行狀況
    by_date = {r['date']: r for r in runs}
    days = [(today - timedelta(days=i)).isoformat() for i in range(6, -1, -1)]
    days = [d for d in days if d >= d0]
    status = {d: (by_date[d]['status'] if d in by_date else 'missing') for d in days}
    bad_orders = [o for o in live_orders if in_week(o['date']) and o['status'] in ('rejected', 'unknown')]
    out['ops'] = {'days': len(days), 'ok': sum(s in OK_RUN for s in status.values()),
                  'trade_days': sum(s == 'done' for s in status.values()),
                  'problems': [f'{d} {s}' for d, s in status.items() if s not in OK_RUN],
                  'bad_orders': [f'{o["date"]} {o["side"]} {o["asset"].upper()} {o["status"]}' for o in bad_orders]}

    # 成交品質：本週真單成交 vs 01:35 中間價、vs 同一天模擬帳戶的成交均價
    fills = []
    for o in live_orders:
        if not in_week(o['date']) or not o.get('executed') or Decimal(o['executed']) == 0 or not o.get('avg_price'):
            continue
        mid = _d(by_date.get(o['date'], {}).get('prices')).get(o['asset'])
        avg = Decimal(o['avg_price'])
        twin = [t for t in paper_trades if t['date'] == o['date'] and t['asset'] == o['asset'] and t['side'] == o['side']]
        fills.append({'date': o['date'], 'side': o['side'], 'asset': o['asset'], 'qty': Decimal(o['executed']),
                      'volume': Decimal(o['volume']), 'avg': avg,
                      'slip_bp': slippage_bp(o['side'], avg, mid) if mid else None,
                      'vs_paper_bp': slippage_bp(o['side'], avg, Decimal(twin[0]['avg_price'])) if twin else None})
    out['fills'] = fills

    # 關卡進度（DESIGN.md 6.6）
    live_days = len({r['date'] for r in runs if r['date'] >= d0 and r['date'] <= today.isoformat()})
    problems_all = sum(r['status'] not in OK_RUN for r in runs if r['date'] >= d0)
    out['gates'] = {'days': live_days, 'problems': problems_all,
                    'bad_orders': sum(o['status'] in ('rejected', 'unknown') for o in live_orders)}
    return out


def text(r: dict) -> str:
    head = f'📋 每週事實檢討 {r["start"]:%m/%d}～{r["date"]:%m/%d}（只列數字，不給建議）'
    if r['live_start'] is None:
        return head + '\n真單還沒有成功執行的紀錄。'
    lv, c = r['live'], r['compare']
    lines = [head,
             f'💰 真單：權益 {lv["end"]:,.0f} 元，入金 {lv["deposit"]:,.0f} 元起算 {pct(lv["since_deposit"])}'
             f'（{lv["end_date"]} 01:35 估值；有轉帳的話這個數字不準）',
             f'本週：真單 {pct(r["week"]["live"])}、主模擬帳戶 {pct(r["week"].get("paper"))}',
             f'從 {r["live_start"]:%m/%d} 交易後起算：' + '、'.join(f'{k} {pct(v)}' for k, v in c.items()),
             f'真單和模擬的差距 {pct(r["gap"])}；真單最大回撤 {pct(r["drawdown"])}']
    ops = r['ops']
    lines.append(f'🛠️ 執行：{ops["days"]} 天中 {ops["ok"]} 天正常（{ops["trade_days"]} 天有下單）'
                 + (f'；異常 {"、".join(ops["problems"])}' if ops['problems'] else '')
                 + (f'；🔴 訂單 {"、".join(ops["bad_orders"])}' if ops['bad_orders'] else ''))
    for f in r['fills']:
        lines.append(f'　{f["date"][5:]} {"買" if f["side"] == "buy" else "賣"} {f["asset"].upper()} {f["qty"].normalize():f}／{f["volume"].normalize():f}'
                     f'，均價 {f["avg"]:,.3f}'
                     + (f'，比中間價差 {f["slip_bp"]:+.1f} bp' if f['slip_bp'] is not None else '')
                     + (f'，比模擬成交差 {f["vs_paper_bp"]:+.1f} bp' if f['vs_paper_bp'] is not None else ''))
    g = r['gates']
    lines.append(f'🚦 關卡：真單第 {g["days"]} 天；第 1 關（{GATE1_DAYS} 天）{"已滿" if g["days"] >= GATE1_DAYS else "未滿"}、'
                 f'第 2 關（約 {GATE2_DAYS} 天）{min(g["days"] / GATE2_DAYS, 1):.0%}；'
                 f'累計異常天數 {g["problems"]}、問題訂單 {g["bad_orders"]}')
    lines.append('（bp＝萬分之一，正數代表對我們不利；要不要進下一關由營運者決定）')
    return '\n'.join(lines)
