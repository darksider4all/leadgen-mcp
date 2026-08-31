"""Isolated tests for monetization.py — no server, no DB required."""
import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import monetization as m


class FakeHeaders(dict):
    pass


class FakeClient:
    host = "203.0.113.9"


class FakeRequest:
    def __init__(self, headers=None, client=None):
        self.headers = FakeHeaders(headers or {})
        self.client = client


def fake_ctx(method, params=None, request=None):
    return types.SimpleNamespace(method=method, params=params, request=request)


async def test_client_ip():
    # proxied header precedence
    r = FakeRequest(headers={"cf-connecting-ip": "1.2.3.4", "x-forwarded-for": "5.6.7.8"})
    assert m.client_ip(r) == "1.2.3.4"
    r = FakeRequest(headers={"x-forwarded-for": "5.6.7.8, 9.9.9.9"}, client=FakeClient())
    assert m.client_ip(r) == "5.6.7.8"
    # fallback to peer
    r = FakeRequest(headers={}, client=FakeClient())
    assert m.client_ip(r) == "203.0.113.9"
    # no request
    assert m.client_ip(None) == "unknown"
    print("test_client_ip OK")


async def test_valid_api_key():
    orig = m.API_KEYS
    m.API_KEYS = frozenset({"secret-123"})
    try:
        assert m.valid_api_key("Bearer secret-123")
        assert m.valid_api_key("secret-123")
        assert not m.valid_api_key("Bearer wrong")
        assert not m.valid_api_key(None)
        assert not m.valid_api_key("")
    finally:
        m.API_KEYS = orig
    print("test_valid_api_key OK")


async def test_rate_limiter():
    rl = m._RateLimiter(limit=3, window_seconds=60)
    assert rl.allow("ip1") and rl.allow("ip1") and rl.allow("ip1")
    assert not rl.allow("ip1")  # 4th blocked
    assert rl.allow("ip2")       # different key unaffected
    print("test_rate_limiter OK")


async def test_middleware_pass_through_and_gating():
    mw = m.MonetizationMiddleware()
    req = FakeRequest(headers={}, client=FakeClient())
    seen = []

    async def call_next(ctx):
        seen.append(ctx.method)
        return "ran"

    # non-tool-call methods pass straight through
    res = await mw(fake_ctx("initialize", request=req), call_next)
    assert res == "ran" and seen == ["initialize"], seen
    res = await mw(fake_ctx("tools/list", request=req), call_next)
    assert res == "ran" and seen == ["initialize", "tools/list"], seen

    # held mode (default): tools/call never blocks, even paid tool w/o key
    orig_enabled, orig_paid, orig_keys = m.MONETIZATION_ENABLED, m.PAID_TOOLS, m.API_KEYS
    try:
        m.MONETIZATION_ENABLED = False
        res = await mw(fake_ctx("tools/call", {"name": "lookup_financials"}, req), call_next)
        assert res == "ran", "held mode must not block"
    finally:
        m.MONETIZATION_ENABLED = orig_enabled

    # enabled mode: paid tool without key raises MCPError
    try:
        m.MONETIZATION_ENABLED = True
        m.PAID_TOOLS = frozenset({"lookup_financials"})
        m.API_KEYS = frozenset()
        raised = False
        try:
            await mw(fake_ctx("tools/call", {"name": "lookup_financials"}, req), call_next)
        except m.MCPError as e:
            raised = True
            assert e.code == -32001, e.code
        assert raised, "paid tool without key should raise"
        # free tool without key passes
        res = await mw(fake_ctx("tools/call", {"name": "lookup_business"}, req), call_next)
        assert res == "ran"
        # paid tool WITH key passes
        m.API_KEYS = frozenset({"secret-123"})
        req2 = FakeRequest(headers={"authorization": "Bearer secret-123"}, client=FakeClient())
        res = await mw(fake_ctx("tools/call", {"name": "lookup_financials"}, req2), call_next)
        assert res == "ran"
    finally:
        m.MONETIZATION_ENABLED = orig_enabled
        m.PAID_TOOLS = orig_paid
        m.API_KEYS = orig_keys
    print("test_middleware_pass_through_and_gating OK")


async def main():
    await test_client_ip()
    await test_valid_api_key()
    await test_rate_limiter()
    await test_middleware_pass_through_and_gating()
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
