"""T-03 单元测试:LLM 封装 + tool_call 容错。

全部 mock litellm,不连真实 LLM。覆盖:
- 宽松 JSON 修复 / 从 content 提取工具调用(纯函数)
- 标准 tool_calls 解析、坏参数宽松修复
- 格式坏 → 重试 1 次 → 成功 / 仍失败抛错
- token 用量累计、env 配置
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import harness.llm as llm_mod
from harness.llm import (
    LiteLLMClient,
    LLMToolCallError,
    extract_tool_calls_from_content,
    extract_verdict,
    loads_lenient,
)

# ── 纯函数:宽松 JSON ──────────────────────────────────────────


def test_loads_lenient_strict():
    assert loads_lenient('{"url": "http://x"}') == {"url": "http://x"}


def test_loads_lenient_fence():
    assert loads_lenient('```json\n{"a": 1}\n```') == {"a": 1}


def test_loads_lenient_trailing_comma():
    assert loads_lenient('{"a": 1, "b": 2,}') == {"a": 1, "b": 2}


def test_loads_lenient_single_quotes():
    assert loads_lenient("{'a': 'b'}") == {"a": "b"}


def test_loads_lenient_embedded():
    assert loads_lenient('随便说点 {"x": 1} 后面还有') == {"x": 1}


def test_loads_lenient_fails():
    with pytest.raises(ValueError):
        loads_lenient("完全不是 json")
    with pytest.raises(ValueError):
        loads_lenient("")


# ── 裁判 verdict 稳健提取(治 reason 含未转义引号炸 JSON,2026-06-17)──


def test_extract_verdict_from_broken_json():
    # 实测假绿根因:模型判 FAIL,但 reason 里的裸引号把 JSON 弄坏
    s = '{"verdict":"FAIL","reason":"未出现"You have been logged in!"提示"}'
    assert extract_verdict(s) == "FAIL"


def test_extract_verdict_plain_forms():
    assert extract_verdict('{"verdict": "PASS"}') == "PASS"
    assert extract_verdict("verdict: pass") == "PASS"  # 大小写无关


def test_extract_verdict_none_when_absent():
    assert extract_verdict("一段没有裁决字样的文字") is None
    assert extract_verdict("") is None
    assert extract_verdict(None) is None


# ── 纯函数:从 content 提取工具调用 ───────────────────────────


def test_extract_tool_call_tag():
    content = '思考中\n<tool_call>\n{"name": "browser_navigate", "arguments": {"url": "http://x"}}\n</tool_call>'
    calls = extract_tool_calls_from_content(content)
    assert calls == [{"name": "browser_navigate", "arguments": {"url": "http://x"}}]


def test_extract_json_fence():
    content = '```json\n{"name": "click", "arguments": {"ref": "btn1"}}\n```'
    calls = extract_tool_calls_from_content(content)
    assert calls == [{"name": "click", "arguments": {"ref": "btn1"}}]


def test_extract_bare_json():
    content = '{"name": "wait", "arguments": {"seconds": 2}}'
    assert extract_tool_calls_from_content(content) == [
        {"name": "wait", "arguments": {"seconds": 2}}
    ]


def test_extract_nested_function_shape():
    content = '{"function": {"name": "fill", "arguments": {"text": "abc"}}}'
    assert extract_tool_calls_from_content(content) == [
        {"name": "fill", "arguments": {"text": "abc"}}
    ]


def test_extract_args_as_string():
    content = '<tool_call>{"name": "fill", "arguments": "{\\"text\\": \\"abc\\"}"}</tool_call>'
    assert extract_tool_calls_from_content(content) == [
        {"name": "fill", "arguments": {"text": "abc"}}
    ]


def test_extract_func_call_syntax_written_as_text():
    # deepseek-v4-flash 偶发把调用写成 `函数名({...})` 文本(实测 TC201 卡死根因)。
    content = 'browser_click({"ref": "e54", "element": "第一个商品 Add to cart 按钮"})'
    assert extract_tool_calls_from_content(content) == [
        {
            "name": "browser_click",
            "arguments": {"ref": "e54", "element": "第一个商品 Add to cart 按钮"},
        }
    ]


def test_extract_func_call_syntax_amid_narration():
    # 夹在叙述里也要捞出来
    content = '现在执行第4步,点击加购按钮。\nmark_step_done({"step_no": 4})'
    assert extract_tool_calls_from_content(content) == [
        {"name": "mark_step_done", "arguments": {"step_no": 4}}
    ]


def test_extract_func_call_not_triggered_when_standard_json_present():
    # 标准 JSON(带 name)已能捞 → 不走 funcname 兜底,不重复
    content = '{"name": "wait", "arguments": {"seconds": 2}}'
    assert extract_tool_calls_from_content(content) == [
        {"name": "wait", "arguments": {"seconds": 2}}
    ]


def test_extract_none_and_plain():
    assert extract_tool_calls_from_content(None) == []
    assert extract_tool_calls_from_content("纯文本没有调用") == []
    # 纯叙述、无 `名({...})` 形态 → 不误抽
    assert extract_tool_calls_from_content("让我重新获取快照确认一下输入是否生效了") == []


# ── 构造 litellm 假响应 ───────────────────────────────────────


def _msg(content=None, tool_calls=None):
    return SimpleNamespace(content=content, tool_calls=tool_calls)


def _tc(name, arguments, id="call_1"):
    return SimpleNamespace(id=id, function=SimpleNamespace(name=name, arguments=arguments))


def _resp(message, usage=None):
    usage = usage or {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=SimpleNamespace(**usage),
    )


def _patch_completion(monkeypatch, responses):
    """responses: list 依次返回;记录每次调用的 messages。"""
    calls = {"count": 0, "messages": []}

    # 现在 _complete 走 asyncio.to_thread(litellm.completion, ...)(同步调用挪线程,
    # 不占事件循环),故 patch 同步 completion。
    def fake_completion(**kwargs):
        calls["messages"].append(kwargs.get("messages"))
        idx = min(calls["count"], len(responses) - 1)
        calls["count"] += 1
        return responses[idx]

    monkeypatch.setattr(llm_mod.litellm, "completion", fake_completion)
    return calls


# ── chat_stream:流式路径 ──────────────────────────────────────


def _chunk(content=None, usage=None):
    ns = SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=content))],
        usage=SimpleNamespace(**usage) if usage else None,
    )
    return ns


def _patch_acompletion(monkeypatch, chunks):
    calls = {"kwargs": []}

    async def fake_acompletion(**kwargs):
        calls["kwargs"].append(kwargs)

        async def gen():
            for c in chunks:
                yield c

        return gen()

    monkeypatch.setattr(llm_mod.litellm, "acompletion", fake_acompletion)
    return calls


async def test_chat_stream_accumulates_and_callbacks(monkeypatch):
    chunks = [
        _chunk("翻译"),
        _chunk("中…"),
        _chunk(None, usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}),
    ]
    calls = _patch_acompletion(monkeypatch, chunks)

    client = LiteLLMClient(model="test/model")
    seen = []

    async def on_delta(t):
        seen.append(t)

    out = await client.chat_stream([{"role": "user", "content": "hi"}], on_delta=on_delta)
    assert out.content == "翻译中…"
    assert seen == ["翻译", "中…"]  # 空 content 的末 chunk 不回调
    assert out.usage.total_tokens == 15
    assert not out.has_tool_calls
    # 必须开 stream(网关保活的关键)
    assert calls["kwargs"][0]["stream"] is True


async def test_chat_stream_no_on_delta_still_accumulates(monkeypatch):
    _patch_acompletion(monkeypatch, [_chunk("a"), _chunk("b")])
    client = LiteLLMClient(model="test/model")
    out = await client.chat_stream([{"role": "user", "content": "hi"}])
    assert out.content == "ab"


async def test_chat_with_tools_streams_reasoning_via_on_delta(monkeypatch):
    """ReAct 路径:chat(tools=, on_delta=) 流式回调 reasoning,tool_call 经
    stream_chunk_builder 重建 + _parse 解析(语义同非流式)。"""
    _patch_acompletion(monkeypatch, [_chunk("思考"), _chunk("中"), _chunk(None)])
    # stream_chunk_builder 把 chunks 重建为带 tool_call 的标准响应
    built = _resp(_msg(content="思考中", tool_calls=[_tc("browser_click", '{"ref": "e1"}')]))
    monkeypatch.setattr(
        llm_mod.litellm, "stream_chunk_builder", lambda chunks, messages=None: built
    )
    client = LiteLLMClient(model="test/model")
    seen = []

    async def on_delta(t):
        seen.append(t)

    out = await client.chat(
        [{"role": "user", "content": "go"}], tools=[{"x": 1}], on_delta=on_delta
    )
    assert seen == ["思考", "中"]  # reasoning 逐 chunk 回调
    assert out.has_tool_calls
    assert out.tool_calls[0].name == "browser_click"
    assert out.tool_calls[0].arguments == {"ref": "e1"}


# ── chat:标准路径 ─────────────────────────────────────────────


async def test_chat_standard_tool_call(monkeypatch):
    resp = _resp(_msg(content="", tool_calls=[_tc("browser_navigate", '{"url": "http://x"}')]))
    _patch_completion(monkeypatch, [resp])

    client = LiteLLMClient(model="test/model")
    out = await client.chat([{"role": "user", "content": "去 x"}], tools=[{"x": 1}])
    assert out.has_tool_calls
    assert out.tool_calls[0].name == "browser_navigate"
    assert out.tool_calls[0].arguments == {"url": "http://x"}
    assert out.usage.total_tokens == 15


async def test_chat_plain_content_no_tools(monkeypatch):
    resp = _resp(_msg(content="TEST_RESULT: PASS"))
    _patch_completion(monkeypatch, [resp])
    client = LiteLLMClient(model="test/model")
    out = await client.chat([{"role": "user", "content": "hi"}])
    assert out.content == "TEST_RESULT: PASS"
    assert not out.has_tool_calls


async def test_chat_no_tools_json_content_not_misparsed(monkeypatch):
    """未传 tools 时,含 "name" 子串的正常 JSON 内容**不得**被误判成坏工具调用而抛错。

    回归:Scanner 要的是纯 JSON({"提交": {"role":"button","name":"保存并提交"}}),
    内容里的 "name" 曾触发 _looks_like_tool_call → had_error → LLMToolCallError。
    """
    content = '{"提交": {"role": "button", "name": "保存并提交"}}'
    resp = _resp(_msg(content=content))
    calls = _patch_completion(monkeypatch, [resp])
    client = LiteLLMClient(model="test/model")
    out = await client.chat([{"role": "user", "content": "提炼"}])  # 无 tools
    assert out.content == content
    assert not out.has_tool_calls
    assert calls["count"] == 1  # 不重试、不抛错


async def test_chat_lenient_repair_bad_args(monkeypatch):
    # tool_calls 字段在,但 arguments 是带尾逗号的坏 JSON → 宽松修复,不重试
    resp = _resp(_msg(content="", tool_calls=[_tc("click", "{'ref': 'btn1',}")]))
    calls = _patch_completion(monkeypatch, [resp])
    client = LiteLLMClient(model="test/model")
    out = await client.chat([{"role": "user", "content": "点"}], tools=[{"x": 1}])
    assert out.tool_calls[0].arguments == {"ref": "btn1"}
    assert calls["count"] == 1  # 未触发重试


async def test_chat_extract_from_content(monkeypatch):
    # 模型把工具调用写进 content(Qwen 风格),走兜底提取
    resp = _resp(_msg(content='<tool_call>{"name": "wait", "arguments": {"s": 1}}</tool_call>'))
    calls = _patch_completion(monkeypatch, [resp])
    client = LiteLLMClient(model="test/model")
    out = await client.chat([{"role": "user", "content": "等"}], tools=[{"x": 1}])
    assert out.tool_calls[0].name == "wait"
    assert calls["count"] == 1


async def test_chat_retry_then_success(monkeypatch):
    # 第一次:content 像工具调用但坏到提取不出 → 触发重试;第二次:标准成功
    bad = _resp(_msg(content="<tool_call> name: click 没有合法json </tool_call>"))
    good = _resp(_msg(content="", tool_calls=[_tc("click", '{"ref": "ok"}')]))
    calls = _patch_completion(monkeypatch, [bad, good])
    client = LiteLLMClient(model="test/model", max_tool_retries=1)
    out = await client.chat([{"role": "user", "content": "点"}], tools=[{"x": 1}])
    assert out.tool_calls[0].arguments == {"ref": "ok"}
    assert calls["count"] == 2  # 重试了一次
    # 重试时追加了纠偏提示
    assert any("严格" in (m[-1]["content"]) for m in calls["messages"] if m)


async def test_chat_retry_exhausted_raises(monkeypatch):
    bad = _resp(_msg(content="<tool_call> 全是坏的 name 没json </tool_call>"))
    calls = _patch_completion(monkeypatch, [bad, bad])
    client = LiteLLMClient(model="test/model", max_tool_retries=1)
    with pytest.raises(LLMToolCallError):
        await client.chat([{"role": "user", "content": "点"}], tools=[{"x": 1}])
    assert calls["count"] == 2  # 初次 + 重试 1 次


async def test_usage_accumulates(monkeypatch):
    resp = _resp(_msg(content="ok"))
    _patch_completion(monkeypatch, [resp])
    client = LiteLLMClient(model="test/model")
    await client.chat([{"role": "user", "content": "a"}])
    await client.chat([{"role": "user", "content": "b"}])
    assert client.usage_summary().total_tokens == 30
    client.reset_usage()
    assert client.usage_summary().total_tokens == 0


def test_env_config(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "ollama/qwen3:32b")
    monkeypatch.setenv("LLM_API_BASE", "http://localhost:11434")
    monkeypatch.setenv("LLM_API_KEY", "secret")
    client = LiteLLMClient()
    assert client.model == "ollama/qwen3:32b"
    assert client.api_base == "http://localhost:11434"
    assert client.api_key == "secret"


async def test_api_base_key_passed_through(monkeypatch):
    resp = _resp(_msg(content="ok"))
    captured = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        return resp

    monkeypatch.setattr(llm_mod.litellm, "completion", fake_completion)
    client = LiteLLMClient(model="m", api_base="http://h", api_key="k")
    await client.chat([{"role": "user", "content": "a"}], tools=[{"t": 1}])
    assert captured["model"] == "m"
    assert captured["api_base"] == "http://h"
    assert captured["api_key"] == "k"
    assert captured["tools"] == [{"t": 1}]
