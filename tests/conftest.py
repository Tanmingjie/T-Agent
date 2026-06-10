"""全局测试夹具。"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _fast_settle(monkeypatch):
    """把页面稳定等待(settle)的轮询间隔/超时压到 ~0,使 settle 逻辑仍跑但不拖慢测试。

    settle 的真实行为由 tests/test_agent.py 的专项用例显式验证。
    """
    import harness.agent as agent

    monkeypatch.setattr(agent, "_SETTLE_INTERVAL", 0.0, raising=False)
    monkeypatch.setattr(agent, "_SETTLE_TIMEOUT", 0.05, raising=False)
