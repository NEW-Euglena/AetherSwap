import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.pipeline_steps as steps
from app.pipeline_steps import _compute_sell_pressure_from_orders, _check_max_discount_precheck, c5_buy_and_record, filter_iflow_rows
from steamdt.parser import parse_response


def _行(name="Test Item", min_price="10.00", platform="https://buff.163.com/goods/12345", **kw):
    """构造一条最小化 iflow 行，省得每次都写全"""
    row = SimpleNamespace(
        name=name,
        min_price=min_price,
        sell_ratio="0.85",
        buy_ratio="0.80",
        safe_buy_ratio="0.78",
        recent_ratio="0.82",
        platform=platform,
        steam_link="",
        volume="500",
    )
    for k, v in kw.items():
        setattr(row, k, v)
    return row


_基础配置 = {
    "pipeline": {"exclude_keywords": ["印花", "胶囊"], "iflow_top_n": 0},
    "iflow": {"sort_by": "sell"},
}

# ── 卖压计算测试 ──────────────────────────────────────────────────────────

def test_卖压_空订单返回None():
    assert _compute_sell_pressure_from_orders([], 100) is None


def test_卖压_日销量为零返回None():
    orders = [(5.0, 10), (5.1, 5)]
    assert _compute_sell_pressure_from_orders(orders, 0) is None


def test_卖压_大量挂单卖压高():
    # 200件挂单，日销10，压力比 = 20，稳妥超过阈值
    orders = [(5.0, 200)]
    pressure = _compute_sell_pressure_from_orders(orders, 10, n_orders=1)
    assert pressure is not None
    assert pressure > 1.0


def test_卖压_少量挂单卖压低():
    orders = [(5.0, 1), (5.1, 1)]
    pressure = _compute_sell_pressure_from_orders(orders, 1000, n_orders=5)
    assert pressure is not None
    assert pressure < 1.0


def test_卖压_价格断层识别():
    # 3件挂5元，然后跳到10元才有大单——属于薄壁情况，应该降权
    orders_断层 = [(5.0, 3), (10.0, 50)]
    orders_正常 = [(5.0, 25), (5.1, 25)]
    p1 = _compute_sell_pressure_from_orders(orders_断层, 100, n_orders=5)
    p2 = _compute_sell_pressure_from_orders(orders_正常, 100, n_orders=5)
    assert p1 is not None and p2 is not None
    # 薄壁的卖压应该比正常的低（被打折了）
    assert p1 < p2


# ── iflow行过滤测试 ────────────────────────────────────────────────────────

def test_过滤_正常行通过():
    rows = [_行(name="AWP | Dragon Lore")]
    result = filter_iflow_rows(rows, _基础配置)
    assert len(result) == 1


def test_过滤_关键词命中被去掉():
    rows = [
        _行(name="印花 | 某队伍"),
        _行(name="胶囊 | 某队伍"),
        _行(name="AWP | 正常枪"),
    ]
    result = filter_iflow_rows(rows, _基础配置)
    assert len(result) == 1
    assert result[0]["name"] == "AWP | 正常枪"


def test_过滤_价格非正被去掉():
    rows = [
        _行(name="负价格", min_price="-1"),
        _行(name="零价格", min_price="0"),
        _行(name="正常价格", min_price="10.0"),
    ]
    result = filter_iflow_rows(rows, _基础配置)
    assert len(result) == 1
    assert result[0]["name"] == "正常价格"


def test_过滤_非buff链接被去掉():
    rows = [
        _行(name="c5game的", platform="https://c5game.com/item/999"),
        _行(name="buff的"),
    ]
    result = filter_iflow_rows(rows, _基础配置)
    assert len(result) == 1
    assert result[0]["name"] == "buff的"


def test_过滤_c5模式按平台名通过():
    rows = [
        _行(name="buff的", platform_name="BUFF"),
        _行(name="c5的", platform="https://www.c5game.com/item/999", platform_name="C5"),
    ]
    cfg = {
        **_基础配置,
        "market": {"platform": "c5"},
    }
    result = filter_iflow_rows(rows, cfg)
    assert len(result) == 1
    assert result[0]["name"] == "c5的"
    assert result[0]["platform_key"] == "c5"


def test_过滤_c5模式拒绝未命中c5平台的行():
    rows = [
        _行(name="无c5报价", platform="https://www.c5game.com/item/999", platform_name=""),
        _行(name="有c5报价", platform="https://www.c5game.com/item/888", platform_name="C5"),
    ]
    cfg = {
        **_基础配置,
        "market": {"platform": "c5"},
    }
    result = filter_iflow_rows(rows, cfg)
    assert len(result) == 1
    assert result[0]["name"] == "有c5报价"


def test_steamdt解析未命中目标平台时不伪造平台名():
    rows = parse_response(
        {
            "success": True,
            "data": [{
                "marketHashName": "Test Item",
                "platformPrice": 10,
                "platformList": [{"platformEnum": "BUFF", "price": 10, "linkUrl": "https://buff.example"}],
            }],
        },
        target_platform="C5",
    )

    assert rows[0].platform_name == ""
    assert rows[0].platform == ""


def test_steamdt_request_keeps_decimal_max_price(monkeypatch):
    from app.services.iflow_client import IflowClient

    captured = {}

    def fake_fetch(params, **kwargs):
        captured["payload"] = params.to_payload()
        return []

    monkeypatch.setattr("steamdt.fetcher.fetch_steamdt_data", fake_fetch)

    IflowClient().fetch({"min_price": 0.05, "max_price": 5.5})

    assert captured["payload"]["minSellPrice"] == "0.05"
    assert captured["payload"]["maxSellPrice"] == 5.5


def test_过滤_topN限制数量():
    rows = [_行(name=f"物品{i}") for i in range(20)]
    cfg = {**_基础配置, "pipeline": {**_基础配置["pipeline"], "iflow_top_n": 5}}
    result = filter_iflow_rows(rows, cfg)
    assert len(result) <= 5


def test_过滤_goods_id从url解析():
    rows = [_行(platform="https://buff.163.com/goods/98765")]
    result = filter_iflow_rows(rows, _基础配置)
    assert result[0]["goods_id"] == 98765


def test_c5最高折扣预检日志使用c5标签():
    logs = []
    ok = _check_max_discount_precheck(
        {},
        0,
        smart_price=1.0,
        est_ratio=0.9,
        ref_price_est=10.0,
        plan_price=8.0,
        max_discount=0.8,
        log_fn=lambda msg, level: logs.append(msg),
        platform_label="C5",
    )

    assert ok is False
    assert "C5最低价" in logs[0]
    assert "Buff最低价" not in logs[0]


class _C5Client:
    def __init__(self, details):
        self.details = list(details)
        self.detail_order_ids = []

    def get_balance(self):
        return 100

    def get_sell_orders(self, *args, **kwargs):
        return [{"product_id": "product-1", "price": 10.0}]

    def make_out_trade_no(self):
        return "trade-1"

    def buy_product(self, product_id, buy_price, out_trade_no=None):
        return {
            "order_id": "order-1",
            "order_asset_id": "order-asset-1",
            "actual_pay": buy_price,
            "pay_status": 1,
        }

    def get_order_detail(self, order_id, out_trade_no=""):
        self.detail_order_ids.append(order_id)
        if self.details:
            return self.details.pop(0)
        return {}


def test_c5_buy_处理中状态不写采购记录(monkeypatch):
    times = iter([0, 0, 2])
    monkeypatch.setattr(steps.time, "time", lambda: next(times, 2))
    monkeypatch.setattr(steps.time, "sleep", lambda _: None)
    records = []

    paid = c5_buy_and_record(
        _C5Client([{"status": 2, "statusName": "处理中"}]),
        {"name": "Test Item", "min_price": 10.0},
        {"c5": {"order_timeout_seconds": 1, "order_poll_interval_seconds": 1}},
        100,
        0,
        lambda: False,
        records.append,
        market_price=12.0,
    )

    assert paid is None
    assert records == []


def test_c5_buy_待收货状态写入待收货记录(monkeypatch):
    monkeypatch.setattr(steps.time, "time", lambda: 0)
    records = []
    client = _C5Client([{"status": 3, "statusName": "待收货"}])

    paid = c5_buy_and_record(
        client,
        {"name": "Test Item", "min_price": 10.0},
        {"c5": {"order_timeout_seconds": 1, "order_poll_interval_seconds": 1}},
        100,
        0,
        lambda: False,
        records.append,
        market_price=12.0,
    )

    assert paid == 10.0
    assert len(records) == 1
    assert records[0]["pending_receipt"] is True
    assert records[0]["platform"] == "c5"
    assert records[0]["order_id"] == "order-asset-1"
    assert client.detail_order_ids == ["order-asset-1"]
