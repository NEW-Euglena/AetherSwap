"""货币守卫回归测试 – 工厂重置后非CNY账号安全性"""
# 覆盖三个关键场景：
# 1. 汇率缺失（重置后 exchange_rate.json 已删除）→ _build_listing_plan 必须返回 []
# 2. 汇率正常 → 价格正确换算后上架
# 3. 无账号（重置后账号列表为空）→ _run_sell_phase_impl 提前返回不调用 _build_listing_plan

from unittest.mock import MagicMock, patch
import pytest


# ------------------------------------------------------------------ helpers --

def _make_sellable():
    return [{
        "name": "AK-47 | Redline",
        "market_hash_name": "AK-47 | Redline",
        "assetid": "12345678",
        "can_sell": True,
        "appid": 730,
        "contextid": "2",
    }]


def _build_ctx(logged_errors):
    """返回一个会把 log 调用追加到 logged_errors 列表的 MagicMock ctx。"""
    ctx = MagicMock()
    ctx.is_stop_requested.return_value = False
    ctx.debug.return_value = None

    def _log(msg, level="info", **kw):
        logged_errors.append((level, msg))

    ctx.log.side_effect = _log
    return ctx


# ------------------------------------------------------------------ test 1 ---

def test_missing_rate_aborts_listing_plan():
    """账号币种=INR，rate_map 为空（汇率文件被工厂重置删除）→ 返回 [] 且记录 error"""
    from app.sell_pipeline import _build_listing_plan

    logged = []
    ctx = _build_ctx(logged)

    orders = {"sell_orders": [{"price": 30000, "quantity": 3}]}

    with patch("app.sell_pipeline.get_sell_orders_cny", return_value=orders), \
         patch("app.sell_pipeline.compute_smart_list_price", return_value=(300.0, "wall")):

        result = _build_listing_plan(
            ctx=ctx,
            cfg={"pipeline": {"profit_ratio_multiplier": 1.03}},
            session=MagicMock(),
            sellable=_make_sellable(),
            sell_strategy=1,
            pipeline_cfg={"profit_ratio_multiplier": 1.03},
            purchases_snapshot=[{"assetid": "12345678", "market_hash_name": "AK-47 | Redline"}],
            ok_listings=False,
            active_listing_ids=set(),
            listing_assetid_to_name={},
            assetid_to_name_map={},
            account_currency="INR",
            rate_map={},        # 关键：汇率文件已删除
        )

    assert result == [], "汇率缺失时必须终止整批上架，返回空列表"
    errors = [msg for lvl, msg in logged if lvl == "error"]
    assert errors, f"应当记录 error 级别日志，实际 logged={logged}"
    assert any("INR" in e for e in errors), f"error 日志应提及币种 INR，实际: {errors}"


# ------------------------------------------------------------------ test 2 ---

def test_valid_rate_converts_price():
    """rate_map 含 INR 时，price_cents 应按汇率换算而非直接用 CNY 分值"""
    from app.sell_pipeline import _build_listing_plan
    from utils.money import list_price_display_to_cents

    inr_rate = 11.5          # 1 CNY ≈ 11.5 INR
    cny_price = 300.0
    expected_inr = round(cny_price / inr_rate, 2)

    logged = []
    ctx = _build_ctx(logged)

    orders = {"sell_orders": [{"price": 30000, "quantity": 3}]}

    with patch("app.sell_pipeline.get_sell_orders_cny", return_value=orders), \
         patch("app.sell_pipeline.compute_smart_list_price", return_value=(cny_price, "wall")):

        result = _build_listing_plan(
            ctx=ctx,
            cfg={"pipeline": {"profit_ratio_multiplier": 1.03}},
            session=MagicMock(),
            sellable=_make_sellable(),
            sell_strategy=1,
            pipeline_cfg={"profit_ratio_multiplier": 1.03},
            purchases_snapshot=[{"assetid": "12345678", "market_hash_name": "AK-47 | Redline"}],
            ok_listings=False,
            active_listing_ids=set(),
            listing_assetid_to_name={},
            assetid_to_name_map={},
            account_currency="INR",
            rate_map={"INR": inr_rate},
        )

    assert len(result) == 1, "汇率正常时应生成 1 条上架计划"
    expected_cents = list_price_display_to_cents(expected_inr)
    actual_cents = result[0]["price_cents"]
    assert actual_cents == expected_cents, (
        f"价格换算错误: 期望 {expected_cents} 分 (≈₹{expected_inr}), 实际 {actual_cents} 分"
    )


def test_same_name_purchase_does_not_allow_auto_sell():
    """同名但 assetid 不同的购买记录不能让库存物品进入自动出售计划。"""
    from app.sell_pipeline import _build_listing_plan

    logged = []
    ctx = _build_ctx(logged)

    with patch("app.sell_pipeline.get_sell_orders_cny") as mock_orders:
        result = _build_listing_plan(
            ctx=ctx,
            cfg={"pipeline": {"profit_ratio_multiplier": 1.03}},
            session=MagicMock(),
            sellable=[{
                "name": "AK-47 | Redline",
                "market_hash_name": "AK-47 | Redline",
                "assetid": "personal-asset",
                "can_sell": True,
                "appid": 730,
                "contextid": "2",
            }],
            sell_strategy=1,
            pipeline_cfg={"profit_ratio_multiplier": 1.03},
            purchases_snapshot=[{
                "assetid": "bought-asset",
                "market_hash_name": "AK-47 | Redline",
                "price": 100,
            }],
            ok_listings=False,
            active_listing_ids=set(),
            listing_assetid_to_name={},
            assetid_to_name_map={},
            account_currency="CNY",
            rate_map={},
        )

    assert result == []
    mock_orders.assert_not_called()
    assert any("未精确命中本地购买记录" in msg for _, msg in logged)


def test_strategy3_uses_strict_ratio_multiplier():
    """策略3只允许出售比例比买入时比例恶化 3%。"""
    from app.sell_pipeline import _build_listing_plan

    logged = []
    ctx = _build_ctx(logged)

    with patch("app.sell_pipeline.get_sell_orders_cny", return_value={"sell_orders": [{"price": 1225, "quantity": 3}]}), \
         patch("app.sell_pipeline.compute_smart_list_price", return_value=(12.25, "wall")), \
         patch("app.sell_pipeline._steam_latest_price_and_trend", return_value=(12.25, 0, [12.25])):

        result = _build_listing_plan(
            ctx=ctx,
            cfg={"pipeline": {"profit_ratio_multiplier": 1.03}},
            session=MagicMock(),
            sellable=[{
                "name": "Dreams & Nightmares Case",
                "market_hash_name": "Dreams & Nightmares Case",
                "assetid": "case-asset",
                "can_sell": True,
                "appid": 730,
                "contextid": "2",
            }],
            sell_strategy=3,
            pipeline_cfg={"profit_ratio_multiplier": 1.03},
            purchases_snapshot=[{
                "assetid": "case-asset",
                "market_hash_name": "Dreams & Nightmares Case",
                "price": 8.2,
                "market_price": 12.73,
            }],
            ok_listings=False,
            active_listing_ids=set(),
            listing_assetid_to_name={},
            assetid_to_name_map={},
            account_currency="CNY",
            rate_map={},
        )

    assert result == []
    assert any("1.03倍" in msg for _, msg in logged)


def test_listing_price_respects_lowest_sell_order_floor():
    """智能定价异常偏低时，不应低于 Steam 最低卖单减一分。"""
    from app.sell_pipeline import _build_listing_plan

    logged = []
    ctx = _build_ctx(logged)

    with patch("app.sell_pipeline.get_sell_orders_cny", return_value={
            "lowest_price": 12.62,
            "sell_orders": [(12.39, 2)],
         }), \
         patch("app.sell_pipeline.compute_smart_list_price", return_value=(12.38, "单档无断层")):

        result = _build_listing_plan(
            ctx=ctx,
            cfg={"pipeline": {}},
            session=MagicMock(),
            sellable=[{
                "name": "Dreams & Nightmares Case",
                "market_hash_name": "Dreams & Nightmares Case",
                "assetid": "case-asset",
                "can_sell": True,
                "appid": 730,
                "contextid": "2",
            }],
            sell_strategy=1,
            pipeline_cfg={},
            purchases_snapshot=[{
                "assetid": "case-asset",
                "market_hash_name": "Dreams & Nightmares Case",
                "price": 8.2,
                "market_price": 12.73,
            }],
            ok_listings=False,
            active_listing_ids=set(),
            listing_assetid_to_name={},
            assetid_to_name_map={},
            account_currency="CNY",
            rate_map={},
        )

    assert len(result) == 1
    assert result[0]["list_price"] == 12.61
    assert any("最低卖单保护价" in msg for _, msg in logged)


# ------------------------------------------------------------------ test 3 ---

def test_no_account_skips_sell_phase():
    """工厂重置后 get_current_account 返回 None → _run_sell_phase_impl 提前返回"""
    from app.sell_pipeline import _run_sell_phase_impl

    state = MagicMock()
    state.get_purchases.return_value = []

    with patch("app.sell_pipeline.get_steam_credentials",
               return_value={"steam_id": "x", "session_id": "y", "cookies": "z=v"}), \
         patch("app.sell_pipeline._resolve_steam_session",
               return_value=(MagicMock(), "sid")), \
         patch("app.sell_pipeline._get_inventory",
               return_value=[{"can_sell": True, "assetid": "1",
                              "name": "Knife", "appid": 730}]), \
         patch("app.sell_pipeline.fetch_my_listings",
               return_value=(True, set(), "", {})), \
         patch("app.sell_pipeline.get_current_account", return_value=None), \
         patch("app.sell_pipeline._build_listing_plan") as mock_plan:

        _run_sell_phase_impl({"pipeline": {}}, state, "test-flow")

    mock_plan.assert_not_called()

def test_missing_currency_code_aborts_sell_phase():
    from app.sell_pipeline import _run_sell_phase_impl
    state = MagicMock()
    state.get_purchases.return_value = []
    account = {"id": "1", "currency_code": ""}
    logged = []
    def _log(msg, level="info", **kw):
        logged.append((level, msg))
    with patch("app.sell_pipeline.get_steam_credentials", return_value={"steam_id": "x", "session_id": "y", "cookies": "z=v"}), \
         patch("app.sell_pipeline._resolve_steam_session", return_value=(MagicMock(), "sid")), \
         patch("app.sell_pipeline._get_inventory", return_value=[{"can_sell": True, "assetid": "1", "name": "Knife", "appid": 730}]), \
         patch("app.sell_pipeline.fetch_my_listings", return_value=(True, set(), "", {})), \
         patch("app.gift_engine.get_wallet_balance", side_effect=Exception("Mock no network")), \
         patch("app.sell_pipeline.get_current_account", return_value=account), \
         patch("app.sell_pipeline._build_listing_plan") as mock_plan, \
         patch("app.sell_pipeline.PipelineContext") as mock_ctx_cls:
        ctx_inst = MagicMock()
        ctx_inst.state = state
        ctx_inst.is_stop_requested.return_value = False
        ctx_inst.log.side_effect = _log
        mock_ctx_cls.return_value = ctx_inst
        _run_sell_phase_impl({"pipeline": {}}, state, "test-flow")
    mock_plan.assert_not_called()
    errors = [msg for lvl, msg in logged if lvl == "error"]
    assert errors
    assert any("currency_code" in e for e in errors)

