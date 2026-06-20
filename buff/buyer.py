import json
import random
import time
import logging
from typing import Optional

logger = logging.getLogger(__name__)
import requests
import urllib3
from utils.delay import jittered_sleep
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3.1 Safari/605.1.15",
]
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
class BuffAuthExpired(Exception):
    pass

class BuffVerificationRequired(Exception):
    pass

def _is_auth_error(status_code: int, data: dict) -> bool:
    if status_code == 401:
        return True
    code = str(data.get("code", "")).lower()
    err_val = data.get("error") or data.get("msg") or ""
    msg = str(err_val).lower()
    if "login" in code or "login" in msg or "未登录" in msg or "登录" in msg:
        return True
    return False

def _is_verification_required(data: dict) -> bool:
    text = str(data.get("error") or data.get("msg") or data.get("message") or "").lower()
    code = str(data.get("code", "")).lower()
    haystack = f"{code} {text}"
    markers = (
        "页面已过期",
        "刷新当前页面",
        "人机验证",
        "captcha",
        "risk",
        "安全验证",
    )
    return any(marker in haystack for marker in markers)
PAY_METHOD_ALIPAY = 51
PAY_METHOD_WECHAT = 6
API_HISTORY = "https://buff.163.com/api/market/buy_order/history"
API_SELL_ORDER = "https://buff.163.com/api/market/goods/sell_order"
API_GOODS = "https://buff.163.com/api/market/goods"
API_BUY = "https://buff.163.com/api/market/goods/buy"
API_PAGE_PAY = "https://buff.163.com/api/market/bill_order/page_pay"
API_WX_PAY_QRCODE = "https://buff.163.com/api/market/bill_order/wx_pay_qrcode"
API_BATCH_BUY_CREATE = "https://buff.163.com/api/market/goods/batch_buy/create"
API_BATCH_WX_PAY_QRCODE = "https://buff.163.com/api/market/goods/batch_buy/wx_pay_qrcode"
API_ASK_SELLER_SEND = "https://buff.163.com/api/market/bill_order/ask_seller_to_send_offer"
def _parse_cookies(cookie_str: str) -> dict:
    out = {}
    for item in cookie_str.split(";"):
        s = item.strip()
        if "=" in s:
            k, _, v = s.partition("=")
            out[k.strip()] = v.strip()
    return out
def _csrf(cookies_dict: dict) -> str:
    return cookies_dict.get("csrf_token", "").strip('"')
def _normalize_timestamp(value) -> Optional[float]:
    try:
        ts = float(value)
    except (TypeError, ValueError):
        return None
    if ts > 10_000_000_000:
        ts = ts / 1000.0
    return ts
def _nested_dicts(item: dict) -> list:
    out = [item]
    for key in ("goods_info", "goods", "item", "asset", "sell_order"):
        val = item.get(key)
        if isinstance(val, dict):
            out.append(val)
    for key in ("items", "goods_list", "items_to_trade"):
        val = item.get(key)
        if isinstance(val, list):
            out.extend(x for x in val if isinstance(x, dict))
    return out
def _extract_order_ids(item: dict) -> set:
    keys = (
        "id", "order_id", "bill_order_id", "bill_orderid", "billOrderId",
        "orderId", "batch_buy_id", "batch_id",
    )
    ids = set()
    for d in _nested_dicts(item):
        for key in keys:
            raw = d.get(key)
            if raw is not None and str(raw).strip():
                ids.add(str(raw).strip())
    return ids
def _extract_goods_ids(item: dict) -> set:
    keys = ("goods_id", "goodsId", "goodsid")
    ids = set()
    for i, d in enumerate(_nested_dicts(item)):
        candidate_keys = keys if i == 0 else keys + ("id",)
        for key in candidate_keys:
            raw = d.get(key)
            if raw is None:
                continue
            try:
                ids.add(str(int(raw)))
            except (TypeError, ValueError):
                s = str(raw).strip()
                if s:
                    ids.add(s)
    return ids
def _extract_created_at(item: dict) -> Optional[float]:
    for d in _nested_dicts(item):
        for key in ("created_at", "createdAt", "create_time", "createTime", "created_time"):
            ts = _normalize_timestamp(d.get(key))
            if ts is not None:
                return ts
    return None
def _normalize_order_state(raw: str) -> str:
    return str(raw or "").strip().upper().replace("-", "_")
def _short_debug_value(value, max_len: int = 80) -> str:
    s = str(value or "")
    if len(s) <= max_len:
        return s
    return s[:max_len - 3] + "..."
def _format_payment_candidate_debug(item: dict, query_state: str, order_id: Optional[str], goods_id: int, start_time: float) -> str:
    order_ids = sorted(_extract_order_ids(item))
    goods_ids = sorted(_extract_goods_ids(item))
    created_at = _extract_created_at(item)
    order_match = bool(order_id and str(order_id) in order_ids)
    goods_match = str(goods_id) in goods_ids
    time_match = created_at is not None and created_at >= (start_time - 30)
    raw_state = item.get("state") or item.get("status") or ""
    created_text = "" if created_at is None else f"{created_at:.0f}"
    order_ids_text = ",".join(_short_debug_value(x, 32) for x in order_ids[:3])
    goods_ids_text = ",".join(_short_debug_value(x, 24) for x in goods_ids[:3])
    return (
        f"query_state={query_state or '<empty>'} raw_state={_short_debug_value(raw_state, 32)} "
        f"order_ids=[{order_ids_text}] goods_ids=[{goods_ids_text}] created_at={created_text} "
        f"order_match={order_match} goods_match={goods_match} time_match={time_match}"
    )
class BuffBuyer:
    def __init__(self, cookie_str: str, pay_method: int = PAY_METHOD_ALIPAY, use_ssl: bool = True):
        self.cookies_dict = _parse_cookies(cookie_str)
        self.csrf_token = _csrf(self.cookies_dict)
        self.pay_method = pay_method
        self.use_ssl = use_ssl
        self.last_payment_status_debug = ""
        self._payment_status_poll_counts = {}
        self._seller_send_confirmed_order_ids = set()
        self.headers = {
            "Host": "buff.163.com",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "X-Csrftoken": self.csrf_token,
            "User-Agent": random.choice(_USER_AGENTS),
            "Content-Type": "application/json",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
    def _make_request(self, method: str, url: str, **kwargs) -> dict:
        h = self.headers.copy()
        if method.upper() == "GET":
            h.pop("Content-Type", None)
        headers_override = kwargs.pop("headers", None)
        if headers_override:
            for k, v in headers_override.items():
                if v is None:
                    h.pop(k, None)
                else:
                    h[k] = v
        verify = kwargs.pop("verify", self.use_ssl)
        timeout = kwargs.pop("timeout", 10)
        r = requests.request(
            method,
            url,
            headers=h,
            cookies=self.cookies_dict,
            verify=verify,
            timeout=timeout,
            **kwargs
        )
        try:
            data = r.json() if r.text else {}
        except ValueError:
            data = {"code": "HTTP_" + str(r.status_code), "error": f"接口返回非预期的内容 (Status: {r.status_code})"}
        if _is_auth_error(r.status_code, data):
            raise BuffAuthExpired()
        if _is_verification_required(data):
            msg = data.get("error") or data.get("msg") or data.get("message") or "Buff 需要刷新页面或完成人机验证"
            raise BuffVerificationRequired(str(msg))
        return data
    def check_wait_pay_orders(self, game: str = "csgo") -> bool:
        params = {
            "game": game,
            "page_num": "1",
            "page_size": "10",
            "state": "wait_pay",
            "_": str(int(time.time() * 1000)),
        }
        try:
            res = self._make_request("GET", API_HISTORY, params=params)
            items = res.get("data", {}).get("items", [])
            if items:
                logger.info("检测到 %d 个待付款订单，正在获取支付链接...", len(items))
                for item in items:
                    if self.pay_method == PAY_METHOD_WECHAT:
                        self._fetch_wechat_url(game, item["id"])
                    else:
                        self._fetch_pay_url(game, item["id"])
                return True
            return False
        except Exception as e:
            logger.exception("检查订单失败: %s", e)
        return False

    def check_payment_status(self, order_id: Optional[str], goods_id: int, start_time: float, is_batch: bool, game: str = "csgo") -> Optional[bool]:
        """
        Check the status of a specific order or batch in the history list.
        Returns:
            True: if paid (wait_send, wait_receive, success)
            False: if cancelled (cancel)
            None: if unpaid (wait_pay/paying)
            "NOT_FOUND": if not found (for retry tolerance)

        检测策略：
        - 单件订单只信任 buy_order/history 中的明确状态，避免把支付页过期/取消误判成已付款。
        - 批量订单创建后通常没有明细订单，仍使用二维码接口消失作为付款完成信号，后续核销会再次验证。
        """
        self.last_payment_status_debug = ""
        poll_key = (str(order_id or ""), str(goods_id), bool(is_batch), game)
        poll_count = self._payment_status_poll_counts.get(poll_key, 0) + 1
        self._payment_status_poll_counts[poll_key] = poll_count
        full_scan = poll_count == 1 or poll_count % 4 == 0
        if full_scan:
            history_states = ("", "wait_pay", "wait_send", "wait_receive", "success", "cancel")
        else:
            history_states = ("", "wait_pay", "wait_send")
        history_items = []
        history_counts = {}
        seen = set()
        for query_state in history_states:
            params = {
                "game": game,
                "page_num": "1",
                "page_size": "50",
                "state": query_state,
                "_": str(int(time.time() * 1000)),
            }
            # Note: BuffVerificationRequired will be automatically raised by _make_request if triggered.
            res = self._make_request("GET", API_HISTORY, params=params)
            items = res.get("data", {}).get("items", [])
            if not isinstance(items, list):
                history_counts[query_state or "<empty>"] = f"non_list:{type(items).__name__}"
                continue
            history_counts[query_state or "<empty>"] = len(items)
            for item in items:
                if not isinstance(item, dict):
                    continue
                ids = tuple(sorted(_extract_order_ids(item)))
                dedupe_key = (query_state, ids, item.get("created_at"), item.get("goods_id"))
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                history_items.append((item, query_state))
        
        paid_states = {
            "WAIT_SEND", "WAIT_RECEIVE", "TO_DELIVER", "TO_SEND", "WAIT_DELIVER", "WAIT_DELIVERY",
            "SUCCESS", "SUCCESSFUL", "DONE", "FINISHED", "COMPLETE", "COMPLETED", "PAID",
        }
        unpaid_states = {"PAYING", "WAIT_PAY", "UNPAID", "INIT"}
        cancelled_states = {"FAIL", "CANCEL", "CANCELLED", "CLOSED"}
        found_any = False
        has_paid = False
        has_cancelled = False
        has_unpaid = False
        has_unknown = False
        has_paying_order_match = False
        matched_debug = []

        for item, query_state in history_items:
            order_ids = _extract_order_ids(item)
            order_id_match = bool(order_id and str(order_id) in order_ids)
            goods_id_match = str(goods_id) in _extract_goods_ids(item)
            item_created_at = _extract_created_at(item)
            time_match = item_created_at is not None and item_created_at >= (start_time - 30)
            match = order_id_match or (goods_id_match and time_match)

            if match:
                found_any = True
                matched_debug.append(_format_payment_candidate_debug(item, query_state, order_id, goods_id, start_time))
                state = _normalize_order_state(item.get("state") or item.get("status") or query_state)
                if state in cancelled_states:
                    has_cancelled = True
                elif state in paid_states:
                    has_paid = True
                elif state in unpaid_states:
                    has_unpaid = True
                    if state == "PAYING" and order_id_match:
                        has_paying_order_match = True
                else:
                    has_unknown = True

        if has_cancelled:
            self.last_payment_status_debug = "matched_cancelled: " + " | ".join(matched_debug[:5])
            return False

        if found_any:
            self.last_payment_status_debug = "matched: " + " | ".join(matched_debug[:5])
            if has_paid and not has_unpaid:
                return True
            if not is_batch and order_id and has_paying_order_match:
                try:
                    still_waiting = self._check_page_pay_status(str(order_id), game)
                    if still_waiting is False:
                        seller_send_ok = self._confirm_single_order_by_seller_send(str(order_id), game)
                        self.last_payment_status_debug += (
                            f" page_pay_still_waiting={still_waiting} "
                            f"seller_send_accepted={seller_send_ok}"
                        )
                        if seller_send_ok is True:
                            return True
                        if seller_send_ok is False:
                            return False
                    self.last_payment_status_debug += f" page_pay_still_waiting={still_waiting}"
                except (BuffAuthExpired, BuffVerificationRequired):
                    raise
                except Exception as e:
                    self.last_payment_status_debug += f" page_pay_error={type(e).__name__}: {e}"
            return None

        if not is_batch:
            sample_debug = [
                _format_payment_candidate_debug(item, query_state, order_id, goods_id, start_time)
                for item, query_state in history_items[:5]
            ]
            self.last_payment_status_debug = (
                f"not_found history_counts={history_counts} target_order_id={_short_debug_value(order_id, 40)} "
                f"target_goods_id={goods_id} start_time={start_time:.0f} samples="
                + (" | ".join(sample_debug) if sample_debug else "[]")
            )
            return "NOT_FOUND"

        # 批量订单在付款前只有 batch_id，history 可能查不到明细；二维码消失后再进入核销阶段验证。
        if order_id:
            try:
                still_waiting = self._check_batch_pay_status(order_id, game)
                self.last_payment_status_debug = (
                    f"batch_history_not_found history_counts={history_counts} "
                    f"target_batch_id={_short_debug_value(order_id, 40)} qrcode_still_waiting={still_waiting}"
                )
                if still_waiting is False:
                    return True
                return None
            except Exception as e:
                self.last_payment_status_debug = (
                    f"batch_history_not_found history_counts={history_counts} "
                    f"target_batch_id={_short_debug_value(order_id, 40)} qrcode_error={type(e).__name__}: {e}"
                )

        sample_debug = [
            _format_payment_candidate_debug(item, query_state, order_id, goods_id, start_time)
            for item, query_state in history_items[:5]
        ]
        self.last_payment_status_debug = (
            f"batch_not_found history_counts={history_counts} target_batch_id={_short_debug_value(order_id, 40)} "
            f"target_goods_id={goods_id} start_time={start_time:.0f} samples="
            + (" | ".join(sample_debug) if sample_debug else "[]")
        )
        return "NOT_FOUND"

    def _check_page_pay_status(self, order_id: str, game: str = "csgo") -> bool:
        """
        【单件订单副轨】通过 bill_order/page_pay 接口查询订单是否仍在等待付款。
        Returns:
            True  → 订单仍处于待付款状态（接口返回有效倒计时）
            False → 订单已离开待付款状态（接口返回非 OK 或无 pay_expire_timeout）
        """
        params = {
            "bill_order_id": str(order_id),
            "_": str(int(time.time() * 1000)),
        }
        h = {
            "Referer": f"https://buff.163.com/market/buy_order/history?game={game}",
        }
        res = self._make_request("GET", API_PAGE_PAY, params=params, headers=h)
        if res.get("code") != "OK":
            # 接口返回非 OK，说明订单已不在待付款状态
            return False
        data = res.get("data", {})
        # 没有 pay_expire_timeout 字段，视为订单已离开待付款状态
        if not data or not data.get("pay_expire_timeout"):
            return False
        return True  # 接口仍返回有效付款信息，订单还在等待

    def _check_batch_pay_status(self, batch_id: str, game: str = "csgo") -> bool:
        """
        【批量订单副轨】通过 batch_buy/wx_pay_qrcode 接口查询批量订单是否仍在等待付款。
        付款前该接口返回二维码 URL；付款后接口返回非 OK 或空 URL。
        Returns:
            True  → 批量订单仍处于待付款状态（接口返回有效二维码）
            False → 批量订单已离开待付款状态（接口返回非 OK 或无 URL）
        """
        params = {
            "batch_buy_id": str(batch_id),
            "_": str(int(time.time() * 1000)),
        }
        h = {"Referer": "https://buff.163.com/goods/0?from=market"}
        res = self._make_request("GET", API_BATCH_WX_PAY_QRCODE, params=params, headers=h)
        if res.get("code") != "OK":
            # 接口返回非 OK，说明批量订单已不在待付款状态
            return False
        data = res.get("data", {})
        url = data.get("url") or data.get("qrcode")
        if not url:
            # 接口返回 OK 但没有二维码 URL，说明订单已完成支付
            return False
        return True  # 接口返回有效二维码，订单还在等待付款

    def get_sell_orders(self, goods_id: int, game: str = "csgo") -> Optional[list]:
        params = {
            "game": str(game),
            "goods_id": str(goods_id),
            "page_num": "1",
            "sort_by": "default",
            "mode": "",
            "allow_tradable_cooldown": "1",
            "_": str(int(time.time() * 1000)),
        }
        h = {"Referer": f"https://buff.163.com/goods/{goods_id}"}
        try:
            data = self._make_request("GET", API_SELL_ORDER, params=params, headers=h)
            if data.get("code") != "OK":
                params.pop("mode", None)
                data = self._make_request("GET", API_SELL_ORDER, params=params, headers=h)
            return data.get("data", {}).get("items", []) or None
        except (BuffAuthExpired, BuffVerificationRequired):
            raise
        except Exception:
            return None
    def get_goods_steam_price_cny(self, search_name: str, game: str = "csgo") -> Optional[float]:
        params = {
            "game": game,
            "page_num": "1",
            "search": search_name.strip(),
            "tab": "selling",
            "_": str(int(time.time() * 1000)),
        }
        h = {"Referer": "https://buff.163.com/market/csgo"}
        try:
            data = self._make_request("GET", API_GOODS, params=params, headers=h)
            if data.get("code") != "OK":
                return None
            items = data.get("data", {}).get("items", [])
            if not items:
                return None
            goods_info = items[0].get("goods_info") or {}
            raw = goods_info.get("steam_price_cny")
            if raw is None:
                return None
            return float(raw)
        except (BuffAuthExpired, BuffVerificationRequired):
            raise
        except (ValueError, TypeError, KeyError):
            return None
        except Exception:
            return None
    def get_and_buy(
        self,
        goods_id: int,
        price_tolerance: float,
        game: str = "csgo",
    ) -> None:
        params = {
            "game": str(game),
            "goods_id": str(goods_id),
            "page_num": "1",
            "sort_by": "default",
            "mode": "",
            "allow_tradable_cooldown": "1",
            "_": str(int(time.time() * 1000)),
        }
        h = {"Referer": f"https://buff.163.com/goods/{goods_id}"}
        try:
            data = self._make_request("GET", API_SELL_ORDER, params=params, headers=h)
            if data.get("code") != "OK" and "Invalid Argument" in str(data):
                params.pop("mode", None)
                data = self._make_request("GET", API_SELL_ORDER, params=params, headers=h)
            items = data.get("data", {}).get("items", [])
            if not items:
                logger.info("当前无人上架 (ID: %s)", goods_id)
                return
            base_price = float(items[0]["price"])
            logger.info("基准价: %s | 容忍: +%s", base_price, price_tolerance)
            for item in items[:5]:
                current_price = float(item["price"])
                price_diff = current_price - base_price
                if price_diff > price_tolerance:
                    logger.warning("价格熔断：%s (差价 %.2f) > %s", current_price, price_diff, price_tolerance)
                    break
                logger.info("尝试购买 [%s] 价格: %s", item['user_id'], current_price)
                result = self._execute_post_buy(game, goods_id, item["id"], item["price"])
                if result == "SUCCESS":
                    logger.info("购买流程结束。")
                    return
                if result == "COOLING_DOWN":
                    logger.warning("触发频率限制/存在未付款订单。")
                    self.check_wait_pay_orders(game)
                    return
                jittered_sleep(0.5)
            logger.info("遍历结束，无合适商品。")
        except Exception as e:
            logger.exception("运行流程异常: %s", e)
    def _execute_post_buy(
        self,
        game: str,
        goods_id: int,
        order_id: str,
        price: str,
    ) -> str:
        payload = {
            "game": game,
            "goods_id": str(goods_id),
            "sell_order_id": order_id,
            "price": price,
            "pay_method": self.pay_method,
            "allow_tradable_cooldown": 0,
            "token": "",
            "cdkey_id": "",
            "hide_non_epay": True,
        }
        if self.pay_method == PAY_METHOD_ALIPAY:
            payload["steamid"] = None
        h = {"Referer": f"https://buff.163.com/goods/{goods_id}?from=market"}
        try:
            res = self._make_request("POST", API_BUY, headers=h, data=json.dumps(payload))
            if res.get("code") == "OK":
                new_order_id = res.get("data", {}).get("id")
                logger.info("锁单成功！订单号: %s", new_order_id)
                if self.pay_method == PAY_METHOD_WECHAT:
                    jittered_sleep(0.5)
                    self._fetch_wechat_url(game, new_order_id)
                else:
                    self._fetch_pay_url(game, new_order_id)
                return "SUCCESS"
            error_code = str(res.get("code", ""))
            err_msg = res.get("error") or res.get("msg") or f"接口返回异常 Code: {error_code}"
            msg = str(err_msg)
            if "Cooling Down" in error_code or "Cooling Down" in msg:
                return "COOLING_DOWN"
            if error_code == "Error":
                logger.warning("卖家不支持当前支付方式 (Code: Error)")
                return "FAIL"
            logger.warning("锁单失败: %s", err_msg)
            return "FAIL"
        except Exception as e:
            logger.exception("锁单异常: %s", e)
            return "FAIL"
    def _fetch_pay_url(self, game: str, order_id: str) -> Optional[str]:
        params = {
            "bill_order_id": str(order_id),
            "_": str(int(time.time() * 1000)),
        }
        h = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"https://buff.163.com/market/buy_order/history?game={game}",
        }
        try:
            logger.info("正在请求订单 %s 的支付链接 (GET)...", order_id)
            res = self._make_request("GET", API_PAGE_PAY, params=params, headers=h)
            if res.get("code") == "OK":
                data = res.get("data", {})
                elements_v2 = data.get("elements_v2", {})
                alipay_info = elements_v2.get("alipay", {})
                pay_url = (
                    alipay_info.get("url")
                    or data.get("elements", {}).get("url")
                    or data.get("url")
                )
                if pay_url:
                    logger.info("获取成功！支付链接如下：\n%s", pay_url)
                    logger.info("剩余支付时间: %ss", data.get('pay_expire_timeout', 'N/A'))
                    return pay_url
                logger.warning("接口返回 OK 但未找到 URL: %s", res)
            else:
                err = res.get("error") or res.get("msg") or f"未返回 error 字段 (Code: {res.get('code', 'N/A')})"
                logger.warning("支付接口返回错误: %s", err)
        except Exception as e:
            logger.exception("获取支付链接异常: %s", e)
        return None
    def lock_and_get_pay_url(
        self,
        game: str,
        goods_id: int,
        sell_order_id: str,
        price: str,
    ) -> dict:
        payload = {
            "game": game,
            "goods_id": str(goods_id),
            "sell_order_id": sell_order_id,
            "price": price,
            "pay_method": self.pay_method,
            "allow_tradable_cooldown": 0,
            "token": "",
            "cdkey_id": "",
            "hide_non_epay": True,
        }
        if self.pay_method == PAY_METHOD_ALIPAY:
            payload["steamid"] = None
        h = {"Referer": f"https://buff.163.com/goods/{goods_id}?from=market"}
        try:
            res = self._make_request("POST", API_BUY, headers=h, data=json.dumps(payload))
            if res.get("code") != "OK":
                err_msg = res.get("error") or res.get("msg") or f"接口代码非 OK (Code: {res.get('code', 'N/A')})"
                msg_str = str(err_msg)
                if "Cooling Down" in msg_str:
                    return {"success": False, "code": "COOLING_DOWN"}
                return {"success": False, "code": "FAIL", "msg": err_msg}
            new_order_id = res.get("data", {}).get("id")
            if self.pay_method == PAY_METHOD_WECHAT:
                jittered_sleep(0.5)
                pay_url = self._get_wechat_pay_url(game, new_order_id)
                return {"success": True, "pay_url": pay_url, "pay_type": "wechat", "order_id": new_order_id}
            pay_url = self._get_alipay_url(game, new_order_id)
            return {"success": True, "pay_url": pay_url, "pay_type": "alipay", "order_id": new_order_id}
        except (BuffAuthExpired, BuffVerificationRequired):
            raise
        except Exception as e:
            return {"success": False, "code": "FAIL", "msg": str(e)}
    def _get_alipay_url(self, game: str, order_id: str) -> Optional[str]:
        params = {"bill_order_id": str(order_id), "_": str(int(time.time() * 1000))}
        h = {
            "Accept": "application/json, text/javascript, */*; q=0.01", 
            "X-Requested-With": "XMLHttpRequest", 
            "Referer": f"https://buff.163.com/market/buy_order/history?game={game}"
        }
        try:
            res = self._make_request("GET", API_PAGE_PAY, params=params, headers=h)
            if res.get("code") == "OK":
                data = res.get("data", {})
                return data.get("elements_v2", {}).get("alipay", {}).get("url") or data.get("elements", {}).get("url") or data.get("url")
        except Exception:
            pass
        return None
    def _get_wechat_pay_url(self, game: str, order_id: str) -> Optional[str]:
        params = {"bill_order_id": str(order_id), "_": str(int(time.time() * 1000))}
        h = {"Referer": f"https://buff.163.com/market/buy_order/history?game={game}"}
        try:
            res = self._make_request("GET", API_WX_PAY_QRCODE, params=params, headers=h)
            if res.get("code") == "OK":
                data = res.get("data", {})
                return data.get("url") or data.get("elements_v2", {}).get("wechatpay", {}).get("url")
        except Exception:
            pass
        return None
    def _fetch_wechat_url(self, game: str, order_id: str) -> None:
        pay_url = self._get_wechat_pay_url(game, order_id)
        if pay_url:
            logger.info("链接获取成功，正在生成二维码...")
            self._generate_qr_code(pay_url)
        else:
            logger.warning("未找到微信支付 URL")
            
    def _generate_qr_code(self, url: str) -> None:
        try:
            import qrcode
            qr = qrcode.QRCode(version=1, box_size=10, border=4)
            qr.add_data(url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            img.show()
            logger.info("二维码已弹出，请用微信扫码！")
            logger.info("进入支付等待期 30 秒...")
            time.sleep(30)
        except ImportError:
            logger.warning("未安装 qrcode，请执行: pip install qrcode[pil]")
            logger.info("支付链接: %s", url)
            time.sleep(30)
        except Exception as e:
            logger.exception("生成二维码失败: %s", e)
    def batch_buy_create(
        self,
        goods_id: int,
        max_price: float,
        num: int,
        game: str = "csgo",
    ) -> Optional[str]:
        if self.pay_method != PAY_METHOD_WECHAT:
            return None
        import uuid
        trace_id = uuid.uuid4().hex
        payload = {
            "game": game,
            "goods_id": int(goods_id),
            "pay_method": PAY_METHOD_WECHAT,
            "frozen_amount": float(max_price) * num,
            "max_price": str(max_price),
            "num": str(num),
            "steamid": None,
        }
        h = {
            "Referer": f"https://buff.163.com/goods/{goods_id}",
            "Buff-Cashier-Trace-Id": trace_id,
        }
        try:
            res = self._make_request("POST", API_BATCH_BUY_CREATE, headers=h, data=json.dumps(payload))
            if res.get("code") != "OK":
                return None
            data = res.get("data", {})
            raw = data.get("id") or data.get("batch_buy_id")
            return str(raw) if raw is not None else None
        except (BuffAuthExpired, BuffVerificationRequired):
            raise
        except Exception:
            return None
    def batch_buy_wx_qrcode(self, batch_id: str, game: str = "csgo") -> Optional[str]:
        params = {
            "batch_buy_id": str(batch_id),
            "_": str(int(time.time() * 1000)),
        }
        h = {"Referer": "https://buff.163.com/goods/0?from=market"}
        try:
            res = self._make_request("GET", API_BATCH_WX_PAY_QRCODE, params=params, headers=h)
            if res.get("code") != "OK":
                return None
            data = res.get("data", {})
            return data.get("url") or data.get("qrcode") or None
        except BuffVerificationRequired:
            raise
        except Exception:
            return None
    def batch_buy_finalize(
        self,
        game: str,
        goods_id: int,
        sell_order_id: str,
        price: str,
        batch_buy_id: str,
    ) -> Optional[str]:
        if self.pay_method != PAY_METHOD_WECHAT:
            return None
        import uuid
        trace_id = uuid.uuid4().hex
        payload = {
            "game": game,
            "goods_id": int(goods_id),
            "sell_order_id": str(sell_order_id),
            "price": str(price),
            "pay_method": PAY_METHOD_WECHAT,
            "batch": 1,
            "batch_buy_id": str(batch_buy_id),
            "batch_id": "",
            "allow_tradable_cooldown": 0,
            "hide_non_epay": False,
            "steamid": None,
        }
        h = {
            "Referer": f"https://buff.163.com/goods/{goods_id}",
            "Buff-Cashier-Trace-Id": trace_id
        }
        try:
            res = self._make_request("POST", API_BUY, headers=h, data=json.dumps(payload))
            if res.get("code") != "OK":
                return None
            raw = res.get("data", {}).get("id")
            return str(raw) if raw is not None else None
        except (BuffAuthExpired, BuffVerificationRequired):
            raise
        except Exception:
            return None

    def _confirm_single_order_by_seller_send(self, order_id: str, game: str = "csgo") -> Optional[bool]:
        if order_id in self._seller_send_confirmed_order_ids:
            return True
        h = {"Referer": f"https://buff.163.com/market/buy_order/history?game={game}"}
        payload = {
            "bill_orders": [str(order_id)],
            "game": game,
            "steamid": None,
        }
        try:
            res = self._make_request("POST", API_ASK_SELLER_SEND, headers=h, data=json.dumps(payload))
        except (BuffAuthExpired, BuffVerificationRequired):
            raise
        except Exception:
            return None
        if res.get("code") == "OK":
            self._seller_send_confirmed_order_ids.add(str(order_id))
            return True
        return False

    def ask_seller_to_send(self, bill_order_id_or_ids, game: str = "csgo") -> bool:
        if isinstance(bill_order_id_or_ids, (list, tuple)):
            ids = [str(x) for x in bill_order_id_or_ids if x is not None]
        else:
            ids = [str(bill_order_id_or_ids)] if bill_order_id_or_ids is not None else []
        if not ids:
            return False
        h = {"Referer": f"https://buff.163.com/market/buy_order/history?game={game}"}
        any_success = False
        for i, order_id in enumerate(ids):
            if order_id in self._seller_send_confirmed_order_ids:
                any_success = True
                continue
            if i > 0:
                jittered_sleep(1.5)
            payload = {
                "bill_orders": [order_id],
                "game": game,
                "steamid": None,
            }
            try:
                res = self._make_request("POST", API_ASK_SELLER_SEND, headers=h, data=json.dumps(payload))
                if res.get("code") == "OK":
                    any_success = True
                    self._seller_send_confirmed_order_ids.add(order_id)
            except (BuffAuthExpired, BuffVerificationRequired):
                raise
            except Exception:
                pass
        return any_success
