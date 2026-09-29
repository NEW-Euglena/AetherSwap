import threading
import time

import pytest


def test_single_order_wait_pay_does_not_use_page_pay_as_paid(monkeypatch):
    from buff.buyer import API_HISTORY, API_PAGE_PAY, BuffBuyer

    buyer = BuffBuyer("csrf_token=abc")
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append(url)
        if url == API_HISTORY:
            return {
                "code": "OK",
                "data": {"items": [{"id": "order-1", "state": "wait_pay", "goods_id": 123}]},
            }
        if url == API_PAGE_PAY:
            return {"code": "FAIL"}
        return {"code": "OK", "data": {}}

    monkeypatch.setattr(buyer, "_make_request", fake_request)

    assert buyer.check_payment_status("order-1", 123, time.time(), False) is None
    assert API_PAGE_PAY not in calls


def test_single_order_not_found_does_not_use_page_pay_as_paid(monkeypatch):
    from buff.buyer import API_HISTORY, API_PAGE_PAY, BuffBuyer

    buyer = BuffBuyer("csrf_token=abc")
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append(url)
        if url == API_HISTORY:
            return {"code": "OK", "data": {"items": []}}
        if url == API_PAGE_PAY:
            return {"code": "FAIL"}
        return {"code": "OK", "data": {}}

    monkeypatch.setattr(buyer, "_make_request", fake_request)

    assert buyer.check_payment_status("order-1", 123, time.time(), False) == "NOT_FOUND"
    assert API_PAGE_PAY not in calls


def test_single_order_not_found_records_match_debug(monkeypatch):
    from buff.buyer import API_HISTORY, BuffBuyer

    buyer = BuffBuyer("csrf_token=abc")
    start = time.time()

    def fake_request(method, url, **kwargs):
        if url == API_HISTORY:
            return {
                "code": "OK",
                "data": {
                    "items": [{
                        "id": "other-order",
                        "state": "wait_pay",
                        "goods_id": 999,
                        "created_at": int(start * 1000),
                    }]
                },
            }
        return {"code": "OK", "data": {}}

    monkeypatch.setattr(buyer, "_make_request", fake_request)

    assert buyer.check_payment_status("order-1", 123, start, False) == "NOT_FOUND"
    assert "not_found" in buyer.last_payment_status_debug
    assert "target_order_id=order-1" in buyer.last_payment_status_debug
    assert "order_ids=[other-order]" in buyer.last_payment_status_debug
    assert "goods_ids=[999]" in buyer.last_payment_status_debug


def test_single_order_found_in_specific_history_state(monkeypatch):
    from buff.buyer import API_HISTORY, BuffBuyer

    buyer = BuffBuyer("csrf_token=abc")
    queried_states = []

    def fake_request(method, url, **kwargs):
        if url == API_HISTORY:
            state = (kwargs.get("params") or {}).get("state", "")
            queried_states.append(state)
            if state == "wait_send":
                return {
                    "code": "OK",
                    "data": {"items": [{"id": "order-1", "state": "wait_send", "goods_id": 123}]},
                }
            return {"code": "OK", "data": {"items": []}}
        return {"code": "OK", "data": {}}

    monkeypatch.setattr(buyer, "_make_request", fake_request)

    assert buyer.check_payment_status("order-1", 123, time.time(), False) is True
    assert "wait_send" in queried_states


def test_payment_status_uses_limited_scan_between_full_scans(monkeypatch):
    from buff.buyer import API_HISTORY, BuffBuyer

    buyer = BuffBuyer("csrf_token=abc")
    queried_states = []

    def fake_request(method, url, **kwargs):
        if url == API_HISTORY:
            queried_states.append((kwargs.get("params") or {}).get("state", ""))
            return {"code": "OK", "data": {"items": []}}
        return {"code": "OK", "data": {}}

    monkeypatch.setattr(buyer, "_make_request", fake_request)

    buyer.check_payment_status("order-1", 123, time.time(), False)
    buyer.check_payment_status("order-1", 123, time.time(), False)

    assert queried_states[:6] == ["", "wait_pay", "wait_send", "wait_receive", "success", "cancel"]
    assert queried_states[6:] == ["", "wait_pay", "wait_send"]


def test_payment_status_light_scan_keeps_default_state_for_paying_order(monkeypatch):
    from buff.buyer import API_HISTORY, API_PAGE_PAY, BuffBuyer

    buyer = BuffBuyer("csrf_token=abc")
    queried_states = []
    start = time.time()

    def fake_request(method, url, **kwargs):
        if url == API_HISTORY:
            state = (kwargs.get("params") or {}).get("state", "")
            queried_states.append(state)
            if state == "":
                return {
                    "code": "OK",
                    "data": {
                        "items": [{
                            "id": "order-1",
                            "state": "PAYING",
                            "goods_id": 123,
                            "created_at": int(start * 1000),
                        }]
                    },
                }
            return {"code": "OK", "data": {"items": []}}
        if url == API_PAGE_PAY:
            return {"code": "OK", "data": {"pay_expire_timeout": 120}}
        return {"code": "OK", "data": {}}

    monkeypatch.setattr(buyer, "_make_request", fake_request)

    assert buyer.check_payment_status("order-1", 123, start, False) is None
    assert buyer.check_payment_status("order-1", 123, start, False) is None
    assert queried_states[6:9] == ["", "wait_pay", "wait_send"]


def test_single_order_paying_left_pay_page_confirms_by_seller_command(monkeypatch):
    from buff.buyer import API_ASK_SELLER_SEND, API_HISTORY, API_PAGE_PAY, BuffBuyer

    buyer = BuffBuyer("csrf_token=abc")
    start = time.time()
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append(url)
        if url == API_HISTORY:
            state = (kwargs.get("params") or {}).get("state", "")
            if state == "":
                return {
                    "code": "OK",
                    "data": {
                        "items": [{
                            "id": "order-1",
                            "state": "PAYING",
                            "goods_id": 123,
                            "created_at": int(start * 1000),
                        }]
                    },
                }
            return {"code": "OK", "data": {"items": []}}
        if url == API_PAGE_PAY:
            return {"code": "FAIL"}
        if url == API_ASK_SELLER_SEND:
            return {"code": "OK", "data": {"order-1": "OK"}}
        return {"code": "OK", "data": {}}

    monkeypatch.setattr(buyer, "_make_request", fake_request)

    assert buyer.check_payment_status("order-1", 123, start, False) is True
    assert API_PAGE_PAY in calls
    assert API_ASK_SELLER_SEND in calls


def test_single_order_paying_left_pay_page_rejected_by_seller_command(monkeypatch):
    from buff.buyer import API_ASK_SELLER_SEND, API_HISTORY, API_PAGE_PAY, BuffBuyer

    buyer = BuffBuyer("csrf_token=abc")
    start = time.time()

    def fake_request(method, url, **kwargs):
        if url == API_HISTORY:
            state = (kwargs.get("params") or {}).get("state", "")
            if state == "":
                return {
                    "code": "OK",
                    "data": {
                        "items": [{
                            "id": "order-1",
                            "state": "PAYING",
                            "goods_id": 123,
                            "created_at": int(start * 1000),
                        }]
                    },
                }
            return {"code": "OK", "data": {"items": []}}
        if url == API_PAGE_PAY:
            return {"code": "FAIL"}
        if url == API_ASK_SELLER_SEND:
            return {"code": "FAIL", "msg": "订单已取消"}
        return {"code": "OK", "data": {}}

    monkeypatch.setattr(buyer, "_make_request", fake_request)

    assert buyer.check_payment_status("order-1", 123, start, False) is False


def test_single_order_paying_left_pay_page_unknown_seller_command_is_not_retried(monkeypatch):
    from buff import BuffWriteResultUnknown
    from buff.buyer import API_ASK_SELLER_SEND, API_HISTORY, API_PAGE_PAY, BuffBuyer

    buyer = BuffBuyer("csrf_token=abc")
    start = time.time()
    seller_send_calls = 0

    def fake_request(method, url, **kwargs):
        nonlocal seller_send_calls
        if url == API_HISTORY:
            state = (kwargs.get("params") or {}).get("state", "")
            if state == "":
                return {
                    "code": "OK",
                    "data": {
                        "items": [{
                            "id": "order-1",
                            "state": "PAYING",
                            "goods_id": 123,
                            "created_at": int(start * 1000),
                        }]
                    },
                }
            return {"code": "OK", "data": {"items": []}}
        if url == API_PAGE_PAY:
            return {"code": "FAIL"}
        if url == API_ASK_SELLER_SEND:
            seller_send_calls += 1
            raise RuntimeError("temporary network error")
        return {"code": "OK", "data": {}}

    monkeypatch.setattr(buyer, "_make_request", fake_request)

    with pytest.raises(BuffWriteResultUnknown):
        buyer.check_payment_status("order-1", 123, start, False)
    with pytest.raises(BuffWriteResultUnknown):
        buyer.check_payment_status("order-1", 123, start, False)

    assert seller_send_calls == 1


def test_payment_status_periodically_runs_full_scan(monkeypatch):
    from buff.buyer import API_HISTORY, BuffBuyer

    buyer = BuffBuyer("csrf_token=abc")
    queried_states = []

    def fake_request(method, url, **kwargs):
        if url == API_HISTORY:
            queried_states.append((kwargs.get("params") or {}).get("state", ""))
            return {"code": "OK", "data": {"items": []}}
        return {"code": "OK", "data": {}}

    monkeypatch.setattr(buyer, "_make_request", fake_request)

    for _ in range(4):
        buyer.check_payment_status("order-1", 123, time.time(), False)

    assert queried_states[-6:] == ["", "wait_pay", "wait_send", "wait_receive", "success", "cancel"]


def test_single_order_matches_created_at_milliseconds(monkeypatch):
    from buff.buyer import API_HISTORY, BuffBuyer

    buyer = BuffBuyer("csrf_token=abc")
    start = time.time()

    def fake_request(method, url, **kwargs):
        if url == API_HISTORY:
            state = (kwargs.get("params") or {}).get("state", "")
            if state == "wait_send":
                return {
                    "code": "OK",
                    "data": {
                        "items": [{
                            "id": "other-order-id",
                            "state": "wait_send",
                            "goods_id": 123,
                            "created_at": int(start * 1000),
                        }]
                    },
                }
            return {"code": "OK", "data": {"items": []}}
        return {"code": "OK", "data": {}}

    monkeypatch.setattr(buyer, "_make_request", fake_request)

    assert buyer.check_payment_status("order-1", 123, start, False) is True


@pytest.mark.parametrize(
    "response, expected",
    [
        ({"code": "OK", "data": {"state": 2}}, True),
        ({"code": "OK", "data": {"state": "2"}}, True),
        ({"code": "OK", "data": {"state": 1}}, None),
        ({"code": "FAIL", "data": {}}, None),
    ],
)
def test_batch_order_only_confirms_server_paid_state(monkeypatch, response, expected):
    from buff.buyer import API_BATCH_CHECK_STATE, BuffBuyer

    buyer = BuffBuyer("csrf_token=abc")
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs.get("params")))
        return response

    monkeypatch.setattr(buyer, "_make_request", fake_request)

    assert buyer.check_payment_status("batch-1", 123, time.time(), True) is expected
    assert calls == [("GET", API_BATCH_CHECK_STATE, {"batch_buy_id": "batch-1"})]


def test_auto_detect_not_found_does_not_confirm_failure(monkeypatch):
    import app.pipeline_steps as steps

    class FakeBuffClient:
        def __init__(self):
            self.calls = 0

        def check_payment_status(self, **kwargs):
            self.calls += 1
            return "NOT_FOUND"

    client = FakeBuffClient()
    confirm_calls = []
    original_sleep = time.sleep
    original_time = time.time
    fake_now = [1000.0]

    def fake_time():
        fake_now[0] += 1.0
        return fake_now[0]

    def fake_sleep(_seconds):
        original_sleep(0)

    def wait_payment_confirm(timeout_seconds=None):
        deadline = original_time() + 1.0
        while client.calls < 6 and original_time() < deadline:
            original_sleep(0.001)
        return False

    monkeypatch.setattr(steps.time, "time", fake_time)
    monkeypatch.setattr(steps.time, "sleep", fake_sleep)

    ok = steps._do_payment_notify_and_wait(
        item={"name": "Test Item"},
        config={"notify": {"auto_detect_payment": True, "email_timeout_seconds": 1}},
        unit_price=1.0,
        num=1,
        pay_url="https://pay.example",
        pay_type="wechat",
        order_id="order-1",
        acc=0.0,
        set_pending_payment=lambda _data: None,
        wait_payment_confirm=wait_payment_confirm,
        confirm_payment=lambda value: confirm_calls.append(value),
        is_stop_requested=lambda: False,
        log_fn=None,
        buff_client=client,
        goods_id=123,
        start_time=1000.0,
        is_batch=False,
    )

    assert ok is False
    assert client.calls >= 5
    assert confirm_calls == []


def test_auto_detect_logs_payment_match_debug(monkeypatch):
    import app.pipeline_steps as steps

    class FakeBuffClient:
        def __init__(self):
            self.calls = 0

        def check_payment_status(self, **kwargs):
            self.calls += 1
            return "NOT_FOUND"

        def get_last_payment_status_debug(self):
            return "not_found history_counts={'wait_pay': 1}"

    client = FakeBuffClient()
    logs = []
    original_sleep = time.sleep
    original_time = time.time
    fake_now = [1000.0]

    def fake_time():
        fake_now[0] += 1.0
        return fake_now[0]

    def fake_sleep(_seconds):
        original_sleep(0)

    def wait_payment_confirm(timeout_seconds=None):
        deadline = original_time() + 1.0
        while client.calls < 1 and original_time() < deadline:
            original_sleep(0.001)
        return False

    monkeypatch.setattr(steps.time, "time", fake_time)
    monkeypatch.setattr(steps.time, "sleep", fake_sleep)

    steps._do_payment_notify_and_wait(
        item={"name": "Test Item"},
        config={"notify": {"auto_detect_payment": True, "email_timeout_seconds": 1}},
        unit_price=1.0,
        num=1,
        pay_url="https://pay.example",
        pay_type="wechat",
        order_id="order-1",
        acc=0.0,
        set_pending_payment=lambda _data: None,
        wait_payment_confirm=wait_payment_confirm,
        confirm_payment=lambda _value: None,
        is_stop_requested=lambda: False,
        log_fn=lambda message, level="info": logs.append((level, message)),
        buff_client=client,
        goods_id=123,
        start_time=1000.0,
        is_batch=False,
    )

    assert any("自动检测匹配诊断: not_found" in message for _level, message in logs)


def test_auto_detect_propagates_unknown_write_result_to_waiting_thread(monkeypatch):
    import app.pipeline_steps as steps
    from buff import BuffWriteResultUnknown

    unknown = BuffWriteResultUnknown(
        "提醒卖家发货结果未知",
        method="POST",
        url="https://buff.invalid/ask",
    )
    confirm_calls = []
    original_sleep = time.sleep
    original_time = time.time
    fake_now = [1000.0]

    class FakeBuffClient:
        def check_payment_status(self, **_kwargs):
            raise unknown

    def fake_time():
        fake_now[0] += 1.0
        return fake_now[0]

    def fake_sleep(_seconds):
        original_sleep(0)

    def confirm_payment(value):
        confirm_calls.append(value)

    def wait_payment_confirm(timeout_seconds=None):
        deadline = original_time() + 1.0
        while not confirm_calls and original_time() < deadline:
            original_sleep(0.001)
        return False

    monkeypatch.setattr(steps.time, "time", fake_time)
    monkeypatch.setattr(steps.time, "sleep", fake_sleep)

    with pytest.raises(BuffWriteResultUnknown) as exc_info:
        steps._do_payment_notify_and_wait(
            item={"name": "Test Item"},
            config={
                "notify": {
                    "auto_detect_payment": True,
                    "email_timeout_seconds": 1,
                }
            },
            unit_price=1.0,
            num=1,
            pay_url="https://pay.example",
            pay_type="alipay",
            order_id="order-1",
            acc=0.0,
            set_pending_payment=lambda _data: None,
            wait_payment_confirm=wait_payment_confirm,
            confirm_payment=confirm_payment,
            is_stop_requested=lambda: False,
            log_fn=None,
            buff_client=FakeBuffClient(),
            goods_id=123,
            start_time=1000.0,
            is_batch=False,
        )

    assert exc_info.value is unknown
    assert confirm_calls == [False]
