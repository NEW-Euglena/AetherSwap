import pytest


def test_buff_page_expired_is_verification_required():
    from buff.buyer import _is_verification_required

    assert _is_verification_required({"code": "FAIL", "msg": "页面已过期，请刷新当前页面"})


def test_write_request_verification_has_unknown_result(monkeypatch):
    from buff.buyer import BuffBuyer, BuffRequestPolicy, BuffWriteResultUnknown

    class FakeResponse:
        status_code = 200
        text = '{"code":"FAIL","msg":"页面已过期，请刷新当前页面"}'

        def json(self):
            return {"code": "FAIL", "msg": "页面已过期，请刷新当前页面"}

    monkeypatch.setattr(
        "requests.Session.request", lambda self, *args, **kwargs: FakeResponse()
    )
    buyer = BuffBuyer(
        "csrf_token=abc",
        request_policy=BuffRequestPolicy(
            min_interval=0,
            state_path=None,
            persist=False,
        ),
    )

    with pytest.raises(BuffWriteResultUnknown):
        buyer._make_request("POST", "https://buff.163.com/api/fake")
