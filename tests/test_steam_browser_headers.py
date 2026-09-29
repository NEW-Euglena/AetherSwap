from steam.client import DEFAULT_HEADERS
from steam.session import MARKET_HEADERS, parse_cookies

from config import _filter_steam_cookies


def test_history_and_market_session_use_author_browser_identifiers():
    history_user_agent = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )

    assert DEFAULT_HEADERS["User-Agent"] == history_user_agent
    assert "Chrome/116.0.5845.97" in MARKET_HEADERS["User-Agent"]


def test_steam_cookie_filter_preserves_browser_identifier():
    filtered = _filter_steam_cookies(
        "sessionid=session; steamCountry=CN; "
        "steamLoginSecure=secure; browserid=123456789"
    )

    assert parse_cookies(filtered)["browserid"] == "123456789"
