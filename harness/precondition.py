"""预置条件三分类器(规格 §5.1,T-15)。

用 **LLM 意图分类**(不用关键词匹配,成功率低)把每条预置条件分为三类:

- ``state_hook`` —— 状态声明(如"已登录系统""环境已装好")→ 映射到 Hook。
- ``action_step`` —— 操作步骤(如"设置环境变量 CONF=10")→ 转为 TestSpec.given。
- ``ambiguous`` —— 模糊/低置信 → 标记待用户确认。

规则:
- 置信度低于阈值 → 一律降级为 ambiguous(置信度阈值 + 标黄兜底)。
- state_hook 经**用户可维护的映射表**(关键词→Hook 名)解析 hook_ref;映射不到 →
  视为 ambiguous(需要用户补映射或确认)。
- 分类结果**记忆**(memory 缓存,按文本),下次跳过;落库到用例留到 T-21。

输出 ``list[PreconditionItem]``(含置信度),供 UI 标黄确认 / 生成 given / 注册 Hook。
"""

from __future__ import annotations

import json
import logging
import re

from harness.llm import LLMClient
from input.models import PreconditionItem, SpecStep

logger = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _loads_array_or_obj(text: str):
    """宽松解析:支持顶层 JSON 数组或对象(分类器返回的是数组)。"""
    if not text or not text.strip():
        raise ValueError("空内容")
    s = text.strip()
    candidates = [s]
    fence = _FENCE_RE.search(s)
    if fence:
        candidates.append(fence.group(1).strip())
    # 截取首个 [ 或 { 到对应的末个 ] 或 }
    for open_c, close_c in (("[", "]"), ("{", "}")):
        i, j = s.find(open_c), s.rfind(close_c)
        if i != -1 and j > i:
            candidates.append(s[i : j + 1])
    for cand in candidates:
        try:
            return json.loads(cand)
        except (json.JSONDecodeError, ValueError):
            continue
    raise ValueError(f"无法解析为 JSON: {s[:200]!r}")


STATE_HOOK = "state_hook"
ACTION_STEP = "action_step"
AMBIGUOUS = "ambiguous"
_VALID_TYPES = {STATE_HOOK, ACTION_STEP, AMBIGUOUS}

_SYSTEM = """\
你是测试预置条件的意图分类器。把每条预置条件分到三类之一:

- state_hook:状态声明,描述"开始测试前系统应处于的状态",通常由框架的 Hook 来保证。
  例:"已登录系统"、"环境已部署"、"用户具有管理员权限"。
- action_step:需要实际执行的操作步骤,有明确动作。
  例:"设置环境变量 CONF=10"、"新建一条草稿订单"、"导入测试数据 a.csv"。
- ambiguous:含义模糊、信息不足、或你不确定属于上面哪类。

只输出 JSON 数组,与输入条目一一对应、顺序一致:
[{"text":"原文","type":"state_hook|action_step|ambiguous","confidence":0.0~1.0,"reason":"简述"}]
"""


class PreconditionClassifier:
    """预置条件三分类器。"""

    def __init__(
        self,
        llm: LLMClient,
        *,
        hook_map: dict[str, str] | None = None,
        confidence_threshold: float = 0.6,
        memory: dict[str, PreconditionItem] | None = None,
    ) -> None:
        self.llm = llm
        # 用户可维护:关键词 → Hook 名(如 {"已登录": "LoginHook"})
        self.hook_map = hook_map or {}
        self.confidence_threshold = confidence_threshold
        self.memory = memory if memory is not None else {}

    async def classify(self, preconditions: list[str]) -> list[PreconditionItem]:
        """分类一批预置条件;命中 memory 的跳过 LLM。结果按输入顺序返回。"""
        preconditions = [p for p in preconditions if p and p.strip()]
        to_ask = [p for p in preconditions if p not in self.memory]
        if to_ask:
            raw = await self._llm_classify(to_ask)
            for text in to_ask:
                self.memory[text] = self._build_item(text, raw.get(text))
        return [self.memory[p] for p in preconditions]

    def _build_item(self, text: str, raw: dict | None) -> PreconditionItem:
        if not raw:
            return PreconditionItem(text=text, type=AMBIGUOUS, confidence=0.0)
        a_type = str(raw.get("type", "")).strip()
        if a_type not in _VALID_TYPES:
            a_type = AMBIGUOUS
        try:
            conf = float(raw.get("confidence", 0.0))
        except (TypeError, ValueError):
            conf = 0.0

        # 低置信 → 降级 ambiguous
        if conf < self.confidence_threshold and a_type != AMBIGUOUS:
            logger.info("预置条件低置信(%.2f)降级 ambiguous:%s", conf, text)
            a_type = AMBIGUOUS

        hook_ref = None
        if a_type == STATE_HOOK:
            hook_ref = self._resolve_hook(text)
            if hook_ref is None:
                # 状态声明但映射不到 Hook → 需用户补映射,先标 ambiguous
                logger.info("state_hook 未命中映射表,降级 ambiguous:%s", text)
                a_type = AMBIGUOUS

        return PreconditionItem(
            text=text,
            type=a_type,
            hook_ref=hook_ref,
            confidence=conf,
            confirmed_by_user=False,
        )

    def _resolve_hook(self, text: str) -> str | None:
        for keyword, hook_name in self.hook_map.items():
            if keyword in text:
                return hook_name
        return None

    async def _llm_classify(self, items: list[str]) -> dict[str, dict]:
        """调 LLM 分类,返回 {text: {type, confidence, reason}}。失败则全空(→ambiguous)。"""
        numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(items))
        messages = [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": f"待分类预置条件:\n{numbered}"},
        ]
        try:
            resp = await self.llm.chat(messages)
            data = _loads_array_or_obj(resp.content)
        except Exception as e:  # noqa: BLE001
            logger.warning("预置条件分类 LLM 调用/解析失败:%s", e)
            return {}

        arr = data if isinstance(data, list) else data.get("items") or data.get("results") or []
        out: dict[str, dict] = {}
        # 优先按 text 匹配;LLM 漏写 text 时按顺序兜底
        for idx, entry in enumerate(arr):
            if not isinstance(entry, dict):
                continue
            text = str(entry.get("text") or "").strip()
            if text not in items and idx < len(items):
                text = items[idx]
            if text in items:
                out[text] = entry
        return out


def to_given_steps(items: list[PreconditionItem]) -> list[SpecStep]:
    """把 action_step 类预置条件转成 TestSpec.given(规格 §5.2)。"""
    return [
        SpecStep(action="execute", target=item.text) for item in items if item.type == ACTION_STEP
    ]


def needs_confirmation(items: list[PreconditionItem]) -> list[PreconditionItem]:
    """返回需要用户确认的条目(ambiguous 且未确认)。"""
    return [i for i in items if i.type == AMBIGUOUS and not i.confirmed_by_user]
