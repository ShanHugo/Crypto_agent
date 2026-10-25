"""測試用假 aiohttp session：依路徑回傳預先準備的回應，不連網。

回應內容取自 2026-10-26 實測的 MAX v3 真實回應（截短）。
"""
from __future__ import annotations

import json

MARKETS = json.dumps([
    {'id': 'ethtwd', 'status': 'active', 'base_unit': 'eth', 'base_unit_precision': 6, 'min_base_amount': 0.003,
     'quote_unit': 'twd', 'quote_unit_precision': 1, 'min_quote_amount': 250.0, 'm_wallet_supported': True},
])
DEPTH = ('{"timestamp":1792946587,"last_update_version":1792814120236,"last_update_id":1129815,'
         '"asks":[["76932.6","0.076458"],["76928.4","0.354498"],["76920.5","0.495672"]],'
         '"bids":[["76890.2","0.522645"],["76872.3","0.852842"],["76700.0","0.020366"]]}')
TICKERS = ('[{"market":"ethtwd","at":1792946587,"buy":"76890.2","buy_vol":"0.522645","sell":"76920.5",'
           '"sell_vol":"0.495672","open":"75512.5","low":"75512.5","high":"77713.6","last":"76827.3","vol":"260.4078"}]')


class FakeResponse:
    def __init__(self, status: int, text: str):
        self.status = status
        self._text = text

    async def text(self):
        return self._text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    """routes：路徑 → 回應文字、(status, 文字)、或 callable(params) → 上述兩者；也可給 list 依序回傳。"""

    def __init__(self, routes: dict):
        self.routes = routes
        self.calls: list[tuple[str, object]] = []

    def get(self, url, params=None, timeout=None):
        path = url.split('max-api.maicoin.com', 1)[-1]
        self.calls.append((path, params))
        route = self.routes[path]
        if isinstance(route, list):
            route = route.pop(0)
        if callable(route):
            route = route(params)
        if isinstance(route, Exception):
            raise route
        status, text = route if isinstance(route, tuple) else (200, route)
        return FakeResponse(status, text)


def k_rows(start_s: int, n: int, *, step_s: int = 3600, price: str = '1000.0', vol: str = '1.5') -> list:
    return [[start_s + i * step_s, price, price, price, price, vol] for i in range(n)]
