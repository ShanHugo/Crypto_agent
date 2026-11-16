"""每日 5 檔合約候選（DESIGN 6.29；未驗證、只記錄，不用金鑰、不下單）。
每天台北 08:05 排程（操作台也有按鈕）：抓幣安公開資料，產生今天的候選（一天只寫一次、寫了不改），
再替還沒結束的舊候選記分。紀錄：state/forecast/daily_picks.jsonl（每天一行）、daily_picks_result.jsonl（每檔一行）。
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import weekly_review as wr  # noqa: E402

from crypto_robot.forecast import picks, trend  # noqa: E402

PICKS = ROOT / 'state' / 'forecast' / 'daily_picks.jsonl'
RESULTS = ROOT / 'state' / 'forecast' / 'daily_picks_result.jsonl'
BAR = trend.BAR


def append(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')


def make_today(now: int, days: list[dict]) -> str:
    T = now // trend.D * trend.D
    day = datetime.fromtimestamp(T / 1000, timezone.utc).strftime('%Y-%m-%d')
    if any(d['t'] == T for d in days):
        return f'{day} 的候選已經有了，不重做'
    uni = wr.trend_universe(now)
    names = uni.get(trend.month_key(T), [])
    daily = {s: trend.Daily.from_klines(wr.fapi('/fapi/v1/klines', symbol=s, interval='1d', limit=200)) for s in names}
    chosen = [{**p, 'px_made': daily[p['sym']].c[-1]} for p in picks.choose(daily, names, T)]   # 產生當下的價格（晚產生時進場價可能已跑掉）
    append(PICKS, [{'date': day, 't': T, 'made_at': now, 'picks': chosen}])
    return f'{day} 候選 {len(chosen)} 檔：' + '、'.join(f"{p['sym']} {'多' if p['side'] > 0 else '空'} {p['lev']}x" for p in chosen)


def score_open(now: int, days: list[dict]) -> int:
    done = {(r['t'], r['sym']) for r in trend.read_log(RESULTS)}
    new = []
    for d in days:
        for p in d['picks']:
            if (p['t'], p['sym']) in done or p['t'] + BAR > now:
                continue
            b = trend.K15.from_klines(wr.fapi('/fapi/v1/klines', symbol=p['sym'], interval='15m', startTime=p['t'], limit=1000))
            k = sum(1 for t in b.t if t <= now - BAR)                     # 只用收完的 15 分 K
            r = picks.score(p, trend.K15(b.t[:k], b.o[:k], b.h[:k], b.l[:k], b.c[:k]))
            if r is None and now >= p['t'] + (trend.HOLD + 2) * trend.D and k:   # 下架或資料中斷：最後收盤出場
                net = p['side'] * (b.c[k - 1] / p['entry'] - 1) - 2 * trend.COST
                r = {'t': p['t'], 'sym': p['sym'], 'side': p['side'], 'lev': p['lev'], 'entry': p['entry'], 'exit_px': b.c[k - 1],
                     'exit_t': b.t[k - 1] + BAR, 'how': 'expire', 'net': net, 'r': net / p['stop_pct'], 'margin': p['lev'] * net}
            if r:
                new.append(r)
            time.sleep(0.05)
    append(RESULTS, sorted(new, key=lambda r: (r['exit_t'], r['sym'])))
    return len(new)


def main() -> None:
    sys.stdout.reconfigure(encoding='utf-8')
    now = int(time.time() * 1000)
    msg = make_today(now, trend.read_log(PICKS))
    added = score_open(now, trend.read_log(PICKS))
    s = picks.summary(trend.read_log(RESULTS))
    print(msg)
    print(f'本次記分 {added} 檔；累計結束 {s["n"]} 檔' + (f'，勝率 {s["win"]:.0%}、平均 {s["avg_r"]:+.2f}R' if s['n'] else ''))


if __name__ == '__main__':
    main()
