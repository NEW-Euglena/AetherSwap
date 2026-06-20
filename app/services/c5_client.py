import time
import uuid
from typing import Any, Dict, List, Optional

import requests


C5_BASE_URL = "https://openapi.c5game.com"
C5_TIMEOUT = 15


class C5ApiError(Exception):
    pass


class C5Client:
    def __init__(
        self,
        app_key: str,
        app_secret: str = "",
        trade_url: str = "",
        timeout_sec: int = C5_TIMEOUT,
        base_url: str = C5_BASE_URL,
    ) -> None:
        self.app_key = (app_key or "").strip()
        self.app_secret = (app_secret or "").strip()
        self.trade_url = (trade_url or "").strip()
        self.timeout = timeout_sec
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Accept-Encoding": "gzip, br, zstd, deflate",
            "User-Agent": "AetherSwap/0.1",
        }

    def _request(self, method: str, path: str, *, params: Optional[dict] = None, json_body: Optional[dict] = None) -> dict:
        if not self.app_key:
            raise C5ApiError("未配置 C5 app_key")
        q = dict(params or {})
        q["app-key"] = self.app_key
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            from utils.proxy_manager import get_proxy_manager

            proxies = get_proxy_manager().get_proxies_for_request()
        except Exception:
            proxies = None
        try:
            resp = requests.request(
                method,
                url,
                params=q,
                json=json_body,
                headers=self.headers,
                timeout=self.timeout,
                proxies=proxies,
            )
            data = resp.json() if resp.text else {}
        except ValueError as e:
            raise C5ApiError(f"C5 返回非 JSON 响应: HTTP {getattr(resp, 'status_code', 'N/A')}") from e
        except Exception as e:
            raise C5ApiError(f"C5 请求失败: {e}") from e
        if not data.get("success"):
            msg = data.get("errorMsg") or data.get("errorCodeStr") or "C5 接口返回失败"
            raise C5ApiError(str(msg))
        return data.get("data") or {}

    @staticmethod
    def _items_from_page(data: dict) -> List[dict]:
        if isinstance(data.get("list"), list):
            return data.get("list") or []
        if isinstance(data.get("records"), list):
            return data.get("records") or []
        if isinstance(data.get("items"), list):
            return data.get("items") or []
        page = data.get("page") or data.get("result") or {}
        if isinstance(page, dict):
            for key in ("list", "records", "items"):
                if isinstance(page.get(key), list):
                    return page.get(key) or []
        return []

    def get_balance(self) -> Optional[float]:
        data = self._request("GET", "/merchant/account/v2/balance")
        raw = data.get("moneyAmount", data.get("balance"))
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    def get_sell_orders(
        self,
        market_hash_name: str,
        app_id: int = 730,
        delivery: int = 1,
        page_size: int = 20,
    ) -> Optional[List[dict]]:
        name = (market_hash_name or "").strip()
        if not name:
            return []
        body = {
            "marketHashName": name,
            "appId": int(app_id),
            "delivery": int(delivery),
            "pageSize": max(1, min(int(page_size or 20), 50)),
        }
        data = self._request("POST", "/merchant/market/v2/products/search", json_body=body)
        raw_items = self._items_from_page(data)
        orders: List[dict] = []
        for item in raw_items:
            product_id = item.get("productId")
            try:
                price = float(item.get("price", 0))
            except (TypeError, ValueError):
                continue
            if product_id is None or price <= 0:
                continue
            orders.append({
                "id": str(product_id),
                "product_id": str(product_id),
                "price": price,
                "delivery": item.get("delivery"),
                "raw": item,
            })
        orders.sort(key=lambda x: float(x.get("price", 0)))
        return orders

    def buy_product(self, product_id: str, buy_price: float, out_trade_no: Optional[str] = None) -> Dict[str, Any]:
        body = {
            "outTradeNo": out_trade_no or self.make_out_trade_no(),
            "tradeUrl": self.trade_url,
            "productId": product_id,
            "buyPrice": round(float(buy_price), 2),
        }
        data = self._request("POST", "/merchant/trade/v2/normal-buy", json_body=body)
        return {
            "success": True,
            "out_trade_no": body["outTradeNo"],
            "product_id": str(product_id),
            "actual_pay": self._float_or_default(data.get("actualPay"), buy_price),
            "delivery": data.get("delivery"),
            "order_asset_id": str(data.get("orderAssetId")) if data.get("orderAssetId") is not None else "",
            "order_id": str(data.get("orderId")) if data.get("orderId") is not None else "",
            "pay_status": data.get("payStatus"),
            "order_status": data.get("orderStatus"),
            "raw": data,
        }

    def get_order_detail(self, order_id: str, out_trade_no: str = "") -> Dict[str, Any]:
        params = {"orderId": str(order_id)}
        if out_trade_no:
            params["outTradeNo"] = str(out_trade_no)
        return self._request("GET", "/merchant/order/v2/buy/detail", params=params)

    def get_buyer_status(self, order_ids: List[str]) -> List[dict]:
        ids = [str(x) for x in order_ids if x]
        if not ids:
            return []
        body = {"pageNum": 1, "pageSize": max(1, len(ids)), "status": None, "orderIds": ids}
        data = self._request("POST", "/merchant/order/v2/buyer/status", json_body=body)
        return data.get("list") or []

    @staticmethod
    def make_out_trade_no() -> str:
        return f"AS{int(time.time() * 1000)}{uuid.uuid4().hex[:8]}"

    @staticmethod
    def _float_or_default(value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return float(default)


def create_c5_client_from_config(config: dict) -> C5Client:
    c5_cfg = config.get("c5") or {}
    return C5Client(
        c5_cfg.get("app_key", ""),
        app_secret=c5_cfg.get("app_secret", ""),
        trade_url=c5_cfg.get("trade_url", ""),
        timeout_sec=C5_TIMEOUT,
    )
