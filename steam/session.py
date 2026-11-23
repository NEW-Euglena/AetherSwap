from threading import Lock
from typing import Optional

import requests
import urllib3
from curl_cffi import requests as curl_requests

from steam.request_policy import SelectionMarketLimited, selection_market_cooldown
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
STEAM_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
MARKET_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.5845.97 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "X-Requested-With": "XMLHttpRequest",
}
_selection_market_request_lock = Lock()


class SelectionMarketSession(curl_requests.Session):
    """选品只读会话，复用 Requests 的环境代理解析并统一处理限流。"""

    def __init__(self, *, verify: bool = False):
        super().__init__(trust_env=False, verify=verify)

    def get(self, url, *, proxies=None, **kwargs):
        effective_proxies = dict(proxies or {})
        for scheme, proxy in requests.utils.get_environ_proxies(
            url, no_proxy=effective_proxies.get("no_proxy"),
        ).items():
            effective_proxies.setdefault(scheme, proxy)
        with _selection_market_request_lock:
            remaining = selection_market_cooldown.remaining()
            if remaining > 0:
                raise SelectionMarketLimited(remaining)
            response = super().get(url, proxies=effective_proxies, **kwargs)
            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                remaining = selection_market_cooldown.defer(retry_after)
                raise SelectionMarketLimited(remaining, observed=True, retry_after=retry_after)
            return response

    @staticmethod
    def record_success() -> None:
        selection_market_cooldown.record_success()


def parse_cookies(cookie_str: str) -> dict:
    out = {}
    for item in cookie_str.split(";"):
        s = item.strip()
        if "=" in s:
            k, _, v = s.partition("=")
            out[k.strip()] = v.strip()
    return out
def create_market_session(
    cookies_raw: str,
    steam_id: str,
    *,
    headers: Optional[dict] = None,
    verify: bool = False,
    selection_reads: bool = False,
) -> requests.Session | SelectionMarketSession:
    session = SelectionMarketSession(verify=verify) if selection_reads else requests.Session()
    session.verify = verify
    session.cookies.update(parse_cookies(cookies_raw))
    h = {**MARKET_HEADERS, "Referer": f"https://steamcommunity.com/profiles/{steam_id}/inventory/"}
    if headers:
        h.update(headers)
    session.headers.update(h)
    return session
