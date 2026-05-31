"""基于 playwright-mcp A11y 快照的 PageProbe 实现(配合 T-08 断言引擎,T-10)。

playwright-mcp 的 ``browser_snapshot`` 返回形如::

    ### Page
    - Page URL: https://intranet/order/list
    - Page Title: 订单管理
    ### Snapshot
    ```yaml
    - generic [ref=e1]:
      - heading "订单管理" [level=1] [ref=e2]
      - button "提交" [ref=e3]
      - generic [ref=e4]:
        - text: 用户名
        - textbox "用户名" [ref=e5]: admin
      - text: 待审批
    ```

本模块把它解析成 (url, nodes) 并提供语义查询,支撑确定性断言。解析为纯函数,可单测;
``MCPPageProbe`` 是基于 MCPClient 的运行时实现(查询前抓一次快照并缓存)。

匹配策略(阶段一,无词汇表):对语义 target 去掉常见后缀(按钮/输入框/框/链接…)后,
与节点的可及名(accessible name)及文本做双向包含匹配。元素找不到 → 断言引擎标 healable。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from harness.assertion import ElementQuery

# 节点行:  <role> ("name")? [attr]* [ref=..]?  (: value)?
_NODE_RE = re.compile(
    r"^(?P<role>[A-Za-z][\w-]*)"  # 角色
    r'(?:\s+"(?P<name>[^"]*)")?'  # 可选 "name"
    r"(?P<attrs>(?:\s*\[[^\]]*\])*)"  # 任意 [..] 属性
    r"(?:\s*:\s*(?P<value>.*))?$"  # 可选 : value
)

# 语义 target 常见后缀,匹配前剥离以提高命中率
_TARGET_SUFFIXES = (
    "按钮",
    "输入框",
    "输入栏",
    "文本框",
    "下拉框",
    "下拉",
    "链接",
    "图标",
    "框",
    "区域",
    "栏",
    "项",
    "列",
    "标签",
    "选项",
)


@dataclass
class A11yNode:
    role: str
    name: str = ""
    value: str | None = None

    @property
    def text_content(self) -> str:
        """该节点承载的文本:文本节点/表单值取 value,否则取可及名。"""
        if self.value is not None and self.value != "":
            return self.value
        return self.name


@dataclass
class Snapshot:
    url: str = ""
    title: str = ""
    nodes: list[A11yNode] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.nodes is None:
            self.nodes = []


def _parse_node_line(stripped: str) -> A11yNode | None:
    """解析去掉 '- ' 前缀后的单行节点。无法识别返回 None。"""
    m = _NODE_RE.match(stripped)
    if not m:
        return None
    role = m.group("role")
    name = m.group("name") or ""
    value = m.group("value")
    if value is not None:
        value = value.strip()
    # role 为 text 且无 name 时,value 即文本内容(形如 `text: 待审批`)
    return A11yNode(role=role, name=name, value=value)


def parse_snapshot(text: str) -> Snapshot:
    """解析 browser_snapshot 的结果文本为 Snapshot(纯函数)。"""
    snap = Snapshot()
    if not text:
        return snap

    in_yaml = False
    for raw in text.splitlines():
        stripped = raw.strip()

        # YAML 围栏:任一 ``` 行切换状态(```yaml 开始 / ``` 结束)
        if stripped.startswith("```"):
            in_yaml = not in_yaml
            continue

        if not in_yaml:
            # 头部元信息
            mu = re.match(r"-?\s*Page URL:\s*(.+)$", stripped)
            if mu:
                snap.url = mu.group(1).strip()
                continue
            mt = re.match(r"-?\s*Page Title:\s*(.+)$", stripped)
            if mt:
                snap.title = mt.group(1).strip()
            continue

        # YAML 内:解析节点行(去掉 '- ' 前缀)
        if stripped.startswith("- "):
            node = _parse_node_line(stripped[2:].strip())
            if node is not None:
                snap.nodes.append(node)
    return snap


def _normalize_target(target: str) -> str:
    t = target.strip()
    for suf in _TARGET_SUFFIXES:
        if t.endswith(suf) and len(t) > len(suf):
            t = t[: -len(suf)]
            break
    return t.strip()


def _matches(node: A11yNode, norm_target: str, raw_target: str) -> bool:
    name = node.name.strip()
    text = node.text_content.strip()
    for hay in (name, text):
        if not hay:
            continue
        if norm_target and (norm_target in hay or hay in norm_target):
            return True
        if raw_target in hay:
            return True
    return False


def query_nodes(nodes: list[A11yNode], target: str) -> ElementQuery:
    """在已解析节点中按语义 target 查询(纯函数)。"""
    norm = _normalize_target(target)
    matched = [n for n in nodes if _matches(n, norm, target.strip())]
    if not matched:
        return ElementQuery(found=False)
    # 取文本:优先表单控件的 value(如 textbox 的输入值),其次首个有文本的节点。
    # 这样"用户名输入框"取到控件值 admin,而非同名的"用户名"标签文本。
    value_nodes = [n for n in matched if n.role != "text" and n.value not in (None, "")]
    if value_nodes:
        text = value_nodes[0].value or ""
    else:
        text = next((n.text_content for n in matched if n.text_content), matched[0].text_content)
    return ElementQuery(found=True, visible=True, count=len(matched), text=text)


class MCPPageProbe:
    """基于 MCPClient 的 PageProbe 运行时实现。"""

    def __init__(self, mcp, snapshot_tool: str = "browser_snapshot") -> None:
        self._mcp = mcp
        self._snapshot_tool = snapshot_tool
        self._cache: Snapshot | None = None
        self._raw_text: str = ""

    async def refresh(self) -> Snapshot:
        """抓取一次当前页面快照并缓存。"""
        result = await self._mcp.call_tool(self._snapshot_tool, {})
        text = self._mcp.result_to_text(result)
        self._raw_text = text
        self._cache = parse_snapshot(text)
        return self._cache

    def raw_snapshot(self) -> str:
        """最近一次快照的原始文本(供自愈子代理重定位用)。"""
        return self._raw_text

    async def _ensure(self) -> Snapshot:
        if self._cache is None:
            await self.refresh()
        assert self._cache is not None
        return self._cache

    async def current_url(self) -> str:
        return (await self._ensure()).url

    async def query(self, target: str, selector: str | None = None) -> ElementQuery:
        snap = await self._ensure()
        return query_nodes(snap.nodes, target)
