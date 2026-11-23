from types import SimpleNamespace

import pytest

from app import pipeline, pipeline_steps as steps
from app.services import steam_client as service
from steam import client, market_orders, session
from steam.request_policy import MarketCooldown, SelectionMarketLimited


@pytest.fixture
def cooldown(monkeypatch):
    clock = [0.0]
    limiter = MarketCooldown(lambda: clock[0], exponential=True)
    monkeypatch.setattr(session, "selection_market_cooldown", limiter)
    monkeypatch.setattr(steps, "selection_market_cooldown", limiter)
    monkeypatch.setattr(steps.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    return limiter, clock


def test_cooldown_preserves_explicit_wait_and_extends_missing_header(cooldown):
    limiter, clock = cooldown
    for expected in (60, 120, 240, 480, 900, 900):
        assert limiter.defer() == expected
        clock[0] += expected
    assert limiter.defer("75") == 75
    limiter.record_success()
    assert limiter.remaining() == 75
    clock[0] += 75
    assert limiter.defer() == 60


def test_history_and_orderbook_share_cooldown_and_allow_history_cache(monkeypatch, cooldown):
    limiter, clock = cooldown
    prices = [["Nov 22 2026 06: +0", 2.47, "484"]]
    calls = []

    def send(_session, url, **kwargs):
        calls.append((url, kwargs))
        if "/market/pricehistory/" in url:
            return SimpleNamespace(status_code=200, text="", json=lambda: {
                "success": True, "prices": prices, "price_prefix": "CNY ",
            })
        return SimpleNamespace(status_code=429, headers={"Retry-After": "75"})

    monkeypatch.setattr(session.curl_requests.Session, "get", send)
    monkeypatch.setattr(session.requests.utils, "get_environ_proxies", lambda *args, **kwargs: {
        "https": "http://env-proxy.test:80",
        "http": "http://other-env-proxy.test:80",
    })
    monkeypatch.setattr(service, "_history_cache", {})
    monkeypatch.setattr(service, "get_steam_credentials", lambda: {"cookies": "synthetic=test", "steam_id": "42"})
    monkeypatch.setattr(service, "load_app_config_validated", lambda: {"steam": {"proxy": "http://explicit-proxy.test:80"}})
    proxy = SimpleNamespace(get_proxies_for_request=lambda **kwargs: None, is_proxy_enabled=lambda: False)
    monkeypatch.setattr("utils.proxy_manager.get_proxy_manager", lambda: proxy)
    monkeypatch.setattr(market_orders, "get_proxy_manager", lambda: proxy)
    steam = service.SteamClient(selection_reads=True)
    expected = {"history": prices, "currency": "CNY"}
    assert steam.fetch_history("Test Item", return_currency=True) == expected
    assert calls[0][1]["headers"] == client.DEFAULT_HEADERS
    assert calls[0][1]["proxies"]["https"] == "http://explicit-proxy.test:80"
    assert calls[0][1]["cookies"] == {"synthetic": "test"}
    with session.create_market_session("synthetic=test", "42", selection_reads=True) as market:
        with pytest.raises(SelectionMarketLimited) as caught:
            market_orders.get_sell_orders_cny(market, "Test Item", use_cache=False)
    assert caught.value.observed
    assert caught.value.retry_after == "75"
    assert limiter.remaining() == 75
    assert len(calls) == 2
    assert "/market/listings/" in calls[1][0]
    assert calls[1][1]["proxies"]["https"] == "http://env-proxy.test:80"
    assert steam.fetch_history("Test Item", return_currency=True) == expected
    with pytest.raises(SelectionMarketLimited) as blocked:
        steam.fetch_history("Another Item", return_currency=True)
    assert not blocked.value.observed
    assert len(calls) == 2
    clock[0] += 75
    assert steam.fetch_history("Another Item", return_currency=True) == expected
    assert len(calls) == 3


@pytest.mark.parametrize("limited_stage", ["orderbook", "daily_high", "history"])
def test_selecting_a_candidate_retries_only_the_limited_read(monkeypatch, cooldown, limited_stage):
    limiter, _ = cooldown
    calls = []
    events = []

    def reply(stage, value):
        if stage == limited_stage and stage not in calls:
            calls.append(stage)
            raise SelectionMarketLimited(limiter.defer("2"), observed=True, retry_after="2")
        calls.append(stage)
        return value

    monkeypatch.setattr(steps, "set_status", lambda status, **kwargs: events.append(kwargs))
    monkeypatch.setattr(steps, "_fetch_steam_sell_data", lambda *args, **kwargs: reply(
        "orderbook", ({"sell_orders": [{"price": 10, "quantity": 20}], "smart_price": 10}, None),
    ))
    monkeypatch.setattr(steps, "_adjust_ref_price_for_daily_high", lambda *args, **kwargs: reply("daily_high", 10))
    monkeypatch.setattr(steps, "_passes_custom_buy_modules", lambda *args, **kwargs: True)
    monkeypatch.setattr(steps, "_passes_final_buff_precheck", lambda *args, **kwargs: True)
    steam = SimpleNamespace(fetch_history=lambda *args, **kwargs: reply(
        "history", {"history": [["Nov 22 2026 06: +0", 10, "484"]], "currency": "CNY"},
    ))
    analyzer = SimpleNamespace(analyze=lambda *args, **kwargs: {"valid": True, "is_stable": True})
    item = {"name": "Test Item", "goods_id": 42, "min_price": 3}
    config = {
        "pipeline": {"max_discount": 0.8},
        "_strategy_runtime": {"buy": {"enabled_modules": [
            "buy.steam_sell_depth", "guard.max_discount", "guard.history_data_window",
        ]}},
    }
    selected, rejected = steps.pick_stable_item([item], config, steam, analyzer, lambda: False)
    expected = ["orderbook", "daily_high", "history"]
    expected.insert(expected.index(limited_stage), limited_stage)
    assert calls == expected
    assert selected is item
    assert rejected == set()
    state = next(event for event in events if event["step"] == "STEAM_COOLDOWN")
    assert "2 秒" in state["progress_item"]
    assert events[-1]["step"] == "STABILITY_CHECK"


@pytest.mark.parametrize("should_stop", [True, False])
def test_buy_round_leaves_cooldown_on_stop_or_closed_window(monkeypatch, cooldown, should_stop):
    limiter, clock = cooldown
    statuses = []
    sends = []

    def limited(*args, **kwargs):
        sends.append(1)
        raise SelectionMarketLimited(limiter.defer("10"), observed=True, retry_after="10")

    monkeypatch.setattr(steps, "set_status", lambda *args, **kwargs: None)
    monkeypatch.setattr(steps, "_fetch_steam_sell_data", limited)
    ctx = SimpleNamespace(
        is_stop_requested=lambda: should_stop and clock[0] > 0,
        set_status=lambda *args, **kwargs: statuses.append(args),
        log=lambda *args, **kwargs: None,
    )
    cfg = {"_strategy_runtime": {"buy": {"enabled_modules": []}}}
    result = pipeline._process_deals_for_target(
        ctx, [{"name": "Test Item", "goods_id": 42, "min_price": 3}], cfg,
        100, 0, 0, object(), object(), object(), set(), set(), set(),
        is_time_allowed=lambda: should_stop or clock[0] == 0,
    )
    assert sends == [1]
    assert result == (0, 0, True if should_stop else steps.TIME_WINDOW_CLOSED)
    if should_stop:
        assert statuses[-1][0] == "stopped"


def test_third_real_429_stops_pipeline_without_checkout(monkeypatch, cooldown):
    limiter, _ = cooldown
    sends = []
    statuses = []
    failed = set()

    def limited(*args, **kwargs):
        sends.append(1)
        raise SelectionMarketLimited(limiter.defer("1"), observed=True, retry_after="1")

    monkeypatch.setattr(steps, "set_status", lambda *args, **kwargs: None)
    monkeypatch.setattr(steps, "_fetch_steam_sell_data", limited)
    ctx = SimpleNamespace(
        is_stop_requested=lambda: False,
        set_status=lambda *args, **kwargs: statuses.append(args),
        log=lambda *args, **kwargs: None,
    )
    cfg = {"_strategy_runtime": {"buy": {"enabled_modules": []}}}
    result = pipeline._process_deals_for_target(
        ctx, [{"name": "Test Item", "goods_id": 42, "min_price": 3}], cfg,
        100, 0, 0, object(), object(), object(), set(), set(), failed,
    )
    assert result == (0, 0, True)
    assert sends == [1, 1, 1]
    assert failed == set()
    assert statuses[-1] == ("error", "STEAM_RATE_LIMITED")
