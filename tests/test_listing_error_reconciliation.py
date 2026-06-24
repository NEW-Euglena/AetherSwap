from unittest.mock import MagicMock, patch


def test_missing_active_listing_without_sold_history_keeps_listing_state():
    from app.services.workers import _reconcile_missing_active_listings

    updates_by_id = []
    logs = []

    sold_updates = _reconcile_missing_active_listings(
        not_in_active=[(0, {"_db_id": 10, "assetid": "asset-1", "listing": True})],
        sold_map={},
        history_ok=True,
        update_by_id=lambda db_id, data: updates_by_id.append((db_id, data)) or True,
        update_by_idx=MagicMock(),
        get_current_purchases=MagicMock(return_value=[]),
        log_fn=lambda msg, level="info", **kw: logs.append((level, msg)),
    )

    assert sold_updates == 0
    assert updates_by_id == []
    assert any("保留出售中状态" in msg for _, msg in logs)


def test_missing_active_listing_with_sold_history_marks_sold():
    from app.services.workers import _reconcile_missing_active_listings

    updates_by_id = []

    sold_updates = _reconcile_missing_active_listings(
        not_in_active=[(0, {"_db_id": 10, "assetid": "asset-1", "listing": True})],
        sold_map={"asset-1": 7.04},
        history_ok=True,
        update_by_id=lambda db_id, data: updates_by_id.append((db_id, data)) or True,
        update_by_idx=MagicMock(),
        get_current_purchases=MagicMock(return_value=[]),
        log_fn=MagicMock(),
    )

    assert sold_updates == 1
    assert updates_by_id[0][0] == 10
    assert updates_by_id[0][1]["sale_price"] == 7.04
    assert updates_by_id[0][1]["listing"] is False
    assert updates_by_id[0][1]["listing_status"] is None


def test_record_listing_success_clears_old_error_status():
    from app.sell_pipeline import _record_listing_success

    ctx = MagicMock()
    ctx.state.get_purchases.return_value = [{
        "_db_id": 20,
        "assetid": "asset-1",
        "listing": False,
        "listing_status": "error",
    }]

    with patch("app.sell_pipeline.append_sale"), \
         patch("app.sell_pipeline.jittered_sleep"):
        _record_listing_success(ctx, "asset-1", "AK-47 | Redline", 12.34, 1)

    ctx.state.update_purchase_by_id.assert_called_once_with(
        20,
        {"listing": True, "listing_status": None},
    )


def test_repair_listing_candidate_restores_listing_state():
    from app.repair_error_records import _apply_candidate

    purchase = {"assetid": "old-asset", "listing": False, "listing_status": "error"}

    _apply_candidate(
        purchase,
        {"assetid": "new-listing-asset", "source": "listing", "sale_price": None},
        sold_at=123.0,
    )

    assert purchase["assetid"] == "new-listing-asset"
    assert purchase["listing"] is True
    assert purchase["listing_status"] is None


def test_repair_candidate_pool_excludes_sold_history():
    from app.repair_error_records import _build_merged

    _, candidates = _build_merged(
        inv_items=[{"assetid": "inventory-1", "market_hash_name": "AK-47 | Redline"}],
        listing_assetids={"listing-1"},
        listing_name_by_assetid={"listing-1": "AK-47 | Redline"},
    )

    sources = [c["source"] for c in candidates["AK-47 | Redline"]]
    assert sources == ["listing", "inventory"]
    assert "sold" not in sources


def test_repair_skips_existing_sold_records():
    from app.repair_error_records import _clear_repair_targets, _refill_from_list

    purchases = [{
        "name": "AK-47 | Redline",
        "assetid": "sold-asset",
        "sale_price": 1.23,
        "sold_at": 100.0,
        "listing": False,
    }]

    cleared = _clear_repair_targets(purchases)
    filled = _refill_from_list(
        purchases,
        {"AK-47 | Redline": [{"assetid": "inventory-asset", "source": "inventory", "sale_price": None}]},
        sold_at=200.0,
    )

    assert cleared == 0
    assert filled == 0
    assert purchases[0]["assetid"] == "sold-asset"
    assert purchases[0]["sale_price"] == 1.23
    assert purchases[0]["sold_at"] == 100.0


def test_repair_skips_pending_receipt_records():
    from app.repair_error_records import _clear_repair_targets, _refill_from_list

    purchases = [{
        "name": "AK-47 | Redline",
        "pending_receipt": True,
        "assetid": None,
        "sale_price": None,
        "sold_at": None,
    }]

    cleared = _clear_repair_targets(purchases)
    filled = _refill_from_list(
        purchases,
        {"AK-47 | Redline": [{"assetid": "inventory-asset", "source": "inventory", "sale_price": None}]},
        sold_at=200.0,
    )

    assert cleared == 0
    assert filled == 0
    assert purchases[0]["assetid"] is None
    assert purchases[0]["pending_receipt"] is True


def test_restore_holding_api_clears_sale_fields(monkeypatch):
    from app.routes.transactions import RestoreHoldingBody, api_restore_purchases_holding

    updates = []

    monkeypatch.setattr(
        "app.routes.transactions.update_purchase",
        lambda idx, data: updates.append((idx, data.copy())) or True,
    )

    result = api_restore_purchases_holding(RestoreHoldingBody(indices=[3]))

    assert result["ok"] is True
    assert result["restored"] == 1
    assert updates == [(3, {
        "sale_price": None,
        "sold_at": None,
        "listing": False,
        "listing_status": None,
    })]


def test_batch_delist_api_updates_successful_listing(monkeypatch):
    from app.routes.transactions import PurchaseBatchBody, api_batch_delist_purchases

    purchases = [{"name": "AK-47 | Redline", "assetid": "old-asset", "listing": True}]
    updates = []

    monkeypatch.setattr("app.routes.transactions.get_purchases", lambda: purchases)
    monkeypatch.setattr(
        "app.steam_delist.delist_item",
        lambda assetid, name, log_fn=None: (True, "new-asset", None),
    )
    monkeypatch.setattr(
        "app.routes.transactions.update_purchase",
        lambda idx, data: updates.append((idx, data.copy())) or True,
    )

    result = api_batch_delist_purchases(PurchaseBatchBody(indices=[0]))

    assert result["ok"] is True
    assert result["delisted"] == 1
    assert result["failed"] == []
    assert updates == [(0, {"assetid": "new-asset", "listing": False, "listing_status": None})]


def test_batch_delist_api_reports_invalid_records(monkeypatch):
    from app.routes.transactions import PurchaseBatchBody, api_batch_delist_purchases

    purchases = [
        {"name": "A", "assetid": "asset-1", "listing": False},
        {"name": "B", "assetid": "", "listing": True},
    ]

    monkeypatch.setattr("app.routes.transactions.get_purchases", lambda: purchases)
    steam_delist = MagicMock()
    monkeypatch.setattr("app.steam_delist.delist_item", steam_delist)

    result = api_batch_delist_purchases(PurchaseBatchBody(indices=[0, 1, 9]))

    assert result["ok"] is False
    assert result["delisted"] == 0
    assert len(result["failed"]) == 3
    assert {f["error"] for f in result["failed"]} == {"该记录非出售中状态", "无 assetid", "索引无效"}
    steam_delist.assert_not_called()
