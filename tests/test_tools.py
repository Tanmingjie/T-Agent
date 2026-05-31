"""T-19 单元测试:Custom Tool 注册(@tool 装饰器 + YAML command)。

TDD:先定义注册/调用/格式转换行为,再实现 harness/tools.py。
"""

from __future__ import annotations

import pytest

from harness.tools import ToolRegistry

# ── @tool 装饰器:函数工具 ───────────────────────────────────


async def test_decorator_registers_and_calls_async():
    reg = ToolRegistry()

    @reg.tool(name="query_order", description="查订单状态", when_to_use="数据断言")
    async def query_order(order_id: str) -> str:
        return f"状态:待审批({order_id})"

    assert "query_order" in reg.names
    out = await reg.call("query_order", {"order_id": "TC1"})
    assert "待审批" in out


async def test_decorator_supports_sync_function():
    reg = ToolRegistry()

    @reg.tool(name="add", description="加")
    def add(a: int, b: int) -> int:
        return a + b

    assert await reg.call("add", {"a": 2, "b": 3}) == "5"


def test_to_litellm_tools_schema():
    reg = ToolRegistry()

    @reg.tool(
        name="q",
        description="查询",
        parameters={"type": "object", "properties": {"id": {"type": "string"}}},
    )
    async def q(id: str) -> str:
        return id

    tools = reg.to_litellm_tools()
    assert tools[0]["type"] == "function"
    assert tools[0]["function"]["name"] == "q"
    assert tools[0]["function"]["parameters"]["properties"]["id"]["type"] == "string"


async def test_call_unknown_tool_raises():
    with pytest.raises(KeyError):
        await ToolRegistry().call("nope", {})


async def test_function_tool_exception_returned_as_text():
    reg = ToolRegistry()

    @reg.tool(name="boom", description="x")
    async def boom():
        raise RuntimeError("查库失败")

    out = await reg.call("boom", {})
    assert "查库失败" in out  # 异常不冒泡,作为结果文本返回给 LLM


# ── YAML / command 工具 ─────────────────────────────────────


async def test_register_command_tool_runs_shell():
    reg = ToolRegistry()
    reg.register_command(name="echo_status", description="回显", command="echo 待审批")
    out = await reg.call("echo_status", {})
    assert out.strip() == "待审批"


async def test_command_tool_arg_substitution():
    reg = ToolRegistry()
    reg.register_command(
        name="echo_arg",
        description="回显参数",
        command="echo {status}",
        parameters={"type": "object", "properties": {"status": {"type": "string"}}},
    )
    out = await reg.call("echo_arg", {"status": "已审批"})
    assert out.strip() == "已审批"


def test_register_from_yaml_config():
    reg = ToolRegistry()
    reg.register_yaml(
        {
            "name": "check_db",
            "description": "查库",
            "command": "echo ok",
            "when_to_use": "数据断言",
        }
    )
    assert "check_db" in reg.names
    assert reg.to_litellm_tools()[0]["function"]["description"] == "查库"


async def test_has_and_names():
    reg = ToolRegistry()

    @reg.tool(name="a", description="x")
    async def a():
        return "x"

    assert reg.has("a") and not reg.has("b")
    assert reg.names == ["a"]


# ── 与 Agent 集成:LLM 按需调用自定义工具 ───────────────────


async def test_agent_routes_custom_tool_call():
    from harness.agent import TestCaseAgent
    from tests.test_agent import SNAPSHOT_OK, _FakeMCP, _case, _resp, _spec, _ScriptedLLM

    reg = ToolRegistry()

    @reg.tool(name="query_status", description="查订单状态")
    async def query_status() -> str:
        return "数据库状态=待审批"

    mcp = _FakeMCP(SNAPSHOT_OK)
    llm = _ScriptedLLM(
        [
            _resp(content="查状态", calls=[("query_status", {})]),
            _resp(content="完成", calls=[("mark_step_done", {"step_no": 1})]),
            _resp(content="TEST_RESULT: PASS"),
        ]
    )
    agent = TestCaseAgent(llm, mcp, tools_registry=reg)
    record = await agent.run(_case(), spec=_spec())

    step0 = record.steps[0]
    assert step0.tool_name == "query_status"
    assert "待审批" in step0.tool_result
    assert step0.is_custom_tool is True
    # 自定义工具不应走 MCP
    assert ("query_status", {}) not in mcp.tool_calls
