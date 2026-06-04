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

import json
import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from harness.assertion import ElementQuery


def _extract_json(text: str) -> dict | None:
    """从 browser_evaluate 的结果文本里抽出 JSON 对象。

    playwright-mcp 的真实返回形如::

        ### Result
        "{\\"found\\":true,...}"        # 求值结果:被引号包裹的 JSON 字符串字面量
        ### Ran Playwright code
        ```js
        await page.evaluate('() => { ... }');   # 回显的 JS 代码,含花括号
        ```

    因此**不能**对全文做 ``{.*}`` 匹配——会从 Result 的 ``{`` 一路吞到回显代码的
    最后一个 ``}``,得到非法 JSON。先窄化到 ``### Result`` 段(隔离回显代码),
    再解开引号包裹的字符串字面量,最后取对象。
    """
    if not text:
        return None
    m = re.search(r"###\s*Result(.*?)(?:\n###|\Z)", text, re.DOTALL)
    seg = m.group(1).strip() if m else text
    # 段内容可能是被引号包裹的 JSON 字符串字面量("{\"found\":..}") → 先解一层
    if seg.startswith('"'):
        try:
            inner = json.loads(seg)
            if isinstance(inner, str):
                seg = inner
        except (ValueError, TypeError):
            pass
    m2 = re.search(r"\{.*\}", seg, re.DOTALL)
    if not m2:
        return None
    try:
        data = json.loads(m2.group(0))
        return data if isinstance(data, dict) else None
    except (ValueError, TypeError):
        return None


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
    "数量",
    "个数",
    "总数",
    "计数",
    "数",
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
    """循环剥离常见后缀:'购物车图标数量' → '购物车图标' → '购物车',提高同语言命中。"""
    t = target.strip()
    changed = True
    while changed:
        changed = False
        for suf in _TARGET_SUFFIXES:
            if t.endswith(suf) and len(t) > len(suf):
                t = t[: -len(suf)].strip()
                changed = True
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


def _matches_exact(node: A11yNode, target: str) -> bool:
    t = target.strip()
    return node.name.strip() == t or node.text_content.strip() == t


def query_nodes(
    nodes: list[A11yNode], target: str, role: str | None = None, exact: bool = False
) -> ElementQuery:
    """在已解析节点中按语义 target 查询(纯函数)。

    role 非空时只在该角色的节点里匹配。``exact=True`` 时按 name/text **完全相等**匹配
    (词汇表解析出精确真实名后用):像 '1' 这类弱目标若走子串会命中商品描述里的
    "...1 AAA battery..." 等无关文本(真实跑 TC101 暴露),精确匹配才安全。role 限定下
    若无命中,放宽 role 再试一次(角标常是 text/generic 子节点而非 link 本身)。
    """
    if exact:
        matched = [
            n for n in nodes if (role is None or n.role == role) and _matches_exact(n, target)
        ]
        if not matched and role is not None:
            matched = [n for n in nodes if _matches_exact(n, target)]
    else:
        pool = [n for n in nodes if role is None or n.role == role]
        # 优先精确相等:避免短目标('1')子串命中长文本("...1 AAA battery...");
        # 无精确命中再退回双向子串(同语言模糊词仍可匹配)。
        exact_hits = [n for n in pool if _matches_exact(n, target)]
        if exact_hits:
            matched = exact_hits
        else:
            norm = _normalize_target(target)
            matched = [n for n in pool if _matches(n, norm, target.strip())]
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


@runtime_checkable
class VocabResolver(Protocol):
    """业务词 → 页面真实元素({role, name, ...})的解析器(词汇表第一优先,§5.5)。

    命中返回含 ``name``(必要)/可选 ``role`` 的 dict;未命中返回 None。
    实现可基于内存映射(``DictVocabResolver``)或 DB 词汇表(见 intelligence 适配器)。
    """

    async def resolve(self, target: str, *, url: str = "", title: str = "") -> dict | None: ...


class DictVocabResolver:
    """内存词汇表解析器:``{业务词: {"role":.., "name":..}}``,与页面无关。

    供手动维护 / 演示 / 测试用(saucedemo 无 DB 词汇表时直接喂映射)。匹配先精确,
    再子串(优先长 key),与 VocabularyManager 的 ``_term_lookup`` 行为一致。
    """

    def __init__(self, mapping: dict[str, dict]) -> None:
        self._m = dict(mapping)

    async def resolve(self, target: str, *, url: str = "", title: str = "") -> dict | None:
        t = (target or "").strip()
        if t in self._m:
            return self._m[t]
        for key in sorted(self._m, key=len, reverse=True):
            if t and (t in key or key in t):
                return self._m[key]
        return None


class MCPPageProbe:
    """基于 MCPClient 的 PageProbe 运行时实现。"""

    def __init__(
        self,
        mcp,
        snapshot_tool: str = "browser_snapshot",
        resolver: "VocabResolver | None" = None,
    ) -> None:
        self._mcp = mcp
        self._snapshot_tool = snapshot_tool
        self._cache: Snapshot | None = None
        self._raw_text: str = ""
        self._resolver = resolver

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

    async def resolve_entry(self, target: str) -> dict | None:
        """用注入的词汇表解析器把业务词解析为页面真实元素({role,name})。无解析器返回 None。"""
        if self._resolver is None:
            return None
        snap = await self._ensure()
        entry = await self._resolver.resolve(target, url=snap.url, title=snap.title)
        # 防御:词条值必须是 dict(下游 entry.get(...) 才安全);畸形词汇表不应炸断言
        return entry if isinstance(entry, dict) else None

    async def query(self, target: str, selector: str | None = None) -> ElementQuery:
        snap = await self._ensure()
        entry = await self.resolve_entry(target)
        # 解析优先级(§5.3 target→selector):
        #   显式 Assertion.selector > 词汇表 selector → CSS DOM 求值(最确定,治计数角标)
        #   > 词汇表 role+name → a11y 精确匹配 > 原始语义 a11y 匹配
        css = selector or (entry.get("selector") if entry else None)
        if css:
            return await self._query_by_selector(css)
        if entry:
            real_name = str(entry.get("name") or "").strip() or target
            role = entry.get("role") or None
            # 词汇表给的是精确真实名 → 精确匹配(子串会误命中含该串的长文本)
            return query_nodes(snap.nodes, real_name, role=role, exact=True)
        return query_nodes(snap.nodes, target)

    async def _query_by_selector(self, css: str) -> ElementQuery:
        """用 browser_evaluate 按 CSS selector 确定性求值(a11y 快照不支持 CSS)。

        计数角标等"身份即文本值"的元素靠 selector 才稳健(2 件→text='2',
        不像 name 型词汇表把值写死)。求值在浏览器里一次拿到 found/visible/count/text。
        """
        sel_js = json.dumps(css)
        func = (
            "() => {"
            f"const els = document.querySelectorAll({sel_js});"
            "if (!els.length) return JSON.stringify({found:false});"
            "const el = els[0];"
            "const r = el.getBoundingClientRect();"
            "const visible = !!(el.offsetParent !== null || r.width || r.height);"
            "return JSON.stringify({found:true, visible, count:els.length, text:(el.textContent||'').trim()});"
            "}"
        )
        result = await self._mcp.call_tool("browser_evaluate", {"function": func})
        text = self._mcp.result_to_text(result)
        data = _extract_json(text)
        if not data or not data.get("found"):
            return ElementQuery(found=False)
        return ElementQuery(
            found=True,
            visible=bool(data.get("visible", True)),
            count=int(data.get("count", 1)),
            text=data.get("text"),
        )
