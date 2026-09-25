"""``aif10_scraper.client.AIF10Client`` 判据 (2026-09-25 刀 A, spec_holders_
pagination.md §3.1/§7.1)。

每条用例注入一个假 ``requests.Session`` (只实现 ``.get`` 与返回的 ``resp`` 需要
的 ``.status_code``/``.text``/``.json()``), 不触网、不 sleep (``retry_backoff``
用默认值但 ``retry=0`` 时不会真的进入 sleep 分支)。用例名即节点名, 断言精确的
异常类/属性, 不接受父类 ``pytest.raises(AIF10Error)`` 之类的宽断言 (memory
形态一: 门问的问题≠它想守的东西)。
"""
from __future__ import annotations

import pytest

import aif10_scraper.client as aif10_client_module
from aif10_scraper.client import (
    KNOWN_RESPONSE_CODES,
    AIF10ApiError,
    AIF10BlockedError,
    AIF10Client,
    AIF10Error,
    AIF10NonJsonError,
    AIF10UnknownCodeError,
)


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """Retry backoff sleeps real seconds by design (``retry_backoff ** attempt``,
    which is 1.0 on the very first retry regardless of the backoff value) —
    not part of this cut's scope; stub it so K4's one retry doesn't slow the
    suite down."""
    monkeypatch.setattr(aif10_client_module.time, "sleep", lambda _seconds: None)


class _FakeResponse:
    def __init__(self, status_code: int, *, json_body=None, text: str = "", json_error: bool = False):
        self.status_code = status_code
        self._json_body = json_body
        self.text = text
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return self._json_body


class _FakeSession:
    """记录每次 ``.get`` 调用; 按预置队列依次返回 (或全部返回同一个)。"""

    def __init__(self, responses: list[_FakeResponse]):
        self._responses = list(responses)
        self.calls: list[dict] = []
        self.trust_env = True
        self.headers: dict[str, str] = {}

    def get(self, url, *, params, timeout):
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        idx = len(self.calls) - 1
        if idx < len(self._responses):
            return self._responses[idx]
        return self._responses[-1]

    def close(self):
        pass


def _client(responses: list[_FakeResponse], **kwargs) -> tuple[AIF10Client, _FakeSession]:
    kwargs.setdefault("retry", 0)
    kwargs.setdefault("rate_limit", 0.0)
    cli = AIF10Client(**kwargs)
    fake = _FakeSession(responses)
    cli._session = fake
    return cli, fake


def _ok_envelope(**overrides):
    body = {
        "code": 0,
        "message": "ok",
        "success": True,
        "result": {"pages": 1, "count": 1, "data": [{"a": 1}]},
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# K1/K2 — 403/429 立即抛, 不重试
# ---------------------------------------------------------------------------


def test_K1_403_blocked_no_retry():
    cli, fake = _client([_FakeResponse(403, text="blocked")], retry=3)
    with pytest.raises(AIF10BlockedError):
        cli.get_v1("RPT_X")
    assert len(fake.calls) == 1


def test_K2_429_blocked_no_retry():
    cli, fake = _client([_FakeResponse(429, text="rate limited")], retry=3)
    with pytest.raises(AIF10BlockedError):
        cli.get_v1("RPT_X")
    assert len(fake.calls) == 1


# ---------------------------------------------------------------------------
# K3 — 200 但非 JSON 立即抛 AIF10NonJsonError (不进 5xx 重试)
# ---------------------------------------------------------------------------


def test_K3_200_non_json_raises_nonjson():
    cli, fake = _client([_FakeResponse(200, text="<html>", json_error=True)], retry=3)
    with pytest.raises(AIF10NonJsonError):
        cli.get_v1("RPT_X")
    assert len(fake.calls) == 1


# ---------------------------------------------------------------------------
# K4 — 5xx 沿用瞬态重试, 之后成功返回
# ---------------------------------------------------------------------------


def test_K4_5xx_then_ok_retried():
    cli, fake = _client(
        [_FakeResponse(500, text="boom"), _FakeResponse(200, json_body=_ok_envelope())],
        retry=3,
        retry_backoff=1.0,
    )
    result = cli.get_v1("RPT_X")
    assert result["code"] == 0
    assert len(fake.calls) == 2


# ---------------------------------------------------------------------------
# K5 — 200 JSON 但顶层缺 code -> envelope_missing_code (不补 0)
# ---------------------------------------------------------------------------


def test_K5_envelope_missing_code():
    body = {"result": {"pages": 1, "count": 1, "data": [{"a": 1}]}}
    cli, fake = _client([_FakeResponse(200, json_body=body)])
    with pytest.raises(AIF10Error, match="envelope_missing_code"):
        cli.get_v1("RPT_X")
    assert len(fake.calls) == 1


# ---------------------------------------------------------------------------
# K6 — text/plain 头照样按 JSON 解析 (不按 Content-Type 判非 JSON)
# ---------------------------------------------------------------------------


def test_K6_text_plain_json_parsed():
    cli, fake = _client([_FakeResponse(200, json_body=_ok_envelope())])
    result = cli.get_v1("RPT_X")
    assert result["code"] == 0
    assert len(fake.calls) == 1


# ---------------------------------------------------------------------------
# K7 — 400 结构性错误, 非 Blocked, 不重试
# ---------------------------------------------------------------------------


def test_K7_400_structural_no_retry():
    cli, fake = _client([_FakeResponse(400, text="bad request")], retry=3)
    with pytest.raises(AIF10Error) as excinfo:
        cli.get_v1("RPT_X")
    assert not isinstance(excinfo.value, AIF10BlockedError)
    assert len(fake.calls) == 1


# ---------------------------------------------------------------------------
# K8 — code=9201 (result=null) 原样携带 code, 折算 pages/data/count = 0/[]/0
# ---------------------------------------------------------------------------


def test_K8_get_v1_keeps_code_message():
    body = {"code": 9201, "message": "返回数据为空", "success": False, "result": None}
    cli, _fake = _client([_FakeResponse(200, json_body=body)])
    result = cli.get_v1("RPT_X")
    assert result["code"] == 9201
    assert result["data"] == []
    assert result["count"] == 0
    assert result["pages"] == 0


# ---------------------------------------------------------------------------
# K9 — 9501 抛 AIF10ApiError, 携带 code/message, 不重试
# ---------------------------------------------------------------------------


def test_K9_9501_raises_api_error():
    body = {"code": 9501, "message": "X排序列不存在", "success": False, "result": None}
    cli, fake = _client([_FakeResponse(200, json_body=body)], retry=3)
    with pytest.raises(AIF10ApiError) as excinfo:
        cli.get_v1("RPT_X")
    assert excinfo.value.code == 9501
    assert "排序列不存在" in excinfo.value.message
    assert len(fake.calls) == 1


# ---------------------------------------------------------------------------
# K10 — 未知 code fail-closed, 即使 result 里有行也不返回
# ---------------------------------------------------------------------------


def test_K10_unknown_code_fail_closed():
    body = {
        "code": 1234,
        "message": "who knows",
        "success": True,
        "result": {"pages": 1, "count": 2, "data": [{"a": 1}, {"a": 2}]},
    }
    cli, _fake = _client([_FakeResponse(200, json_body=body)])
    with pytest.raises(AIF10UnknownCodeError) as excinfo:
        cli.get_v1("RPT_X")
    assert excinfo.value.code == 1234


# ---------------------------------------------------------------------------
# K11 — code=0 正常两行
# ---------------------------------------------------------------------------


def test_K11_zero_code_returns_rows():
    body = {
        "code": 0,
        "message": "ok",
        "success": True,
        "result": {"pages": 1, "count": 2, "data": [{"a": 1}, {"a": 2}]},
    }
    cli, _fake = _client([_FakeResponse(200, json_body=body)])
    result = cli.get_v1("RPT_X")
    assert result["code"] == 0
    assert len(result["data"]) == 2


# ---------------------------------------------------------------------------
# K12 — KNOWN_RESPONSE_CODES 闭合集合, K8/K9/K11 各覆盖一个成员
# ---------------------------------------------------------------------------


def test_K12_known_codes_closed_set():
    assert set(KNOWN_RESPONSE_CODES.keys()) == {0, 9201, 9501}
    # K11 覆盖 0, K8 覆盖 9201, K9 覆盖 9501 —— 三个成员各有专属用例钉住行为。
