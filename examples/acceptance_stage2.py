"""阶段二真实验收驱动(saucedemo 端到端)。

用法:
    python examples/acceptance_stage2.py suite      # 场景①:Suite 调度 + 用例间隔离
    python examples/acceptance_stage2.py heal         # 场景③:自愈生效(操作侧重定位)
    python examples/acceptance_stage2.py context      # 场景④:Context Compact token 对比

注:原「场景②:Cookie 复用跳过登录」随会话/Cookie 复用退役(2026-06-18)已移除。

真调 LLM(读 .env)+ playwright-mcp(--isolated --headless 规避密码弹框)。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from cli.run_case import _load_dotenv  # noqa: E402

_load_dotenv(_ROOT / ".env")

from harness.agent import TestCaseAgent  # noqa: E402
from harness.llm import LiteLLMClient  # noqa: E402
from harness.orchestrator import Orchestrator  # noqa: E402
from input.excel_parser import parse_excel  # noqa: E402
from mcp_client.client import MCPClient  # noqa: E402

BASE_URL = "https://www.saucedemo.com"
EXCEL = str(_ROOT / "examples" / "saucedemo_cases.xlsx")
MCP_ARGS = ["@playwright/mcp@latest", "--isolated", "--headless"]


def _mcp() -> MCPClient:
    return MCPClient(args=MCP_ARGS)


def _llm() -> LiteLLMClient:
    return LiteLLMClient()


# ── 场景①:Suite 调度 + 用例间隔离 ──────────────────────────


async def scenario_suite() -> None:
    print("\n############ 场景①:Suite 调度 + 用例间隔离 ############")
    cases = parse_excel(EXCEL, base_url=BASE_URL)  # TC101(应过) + TC102(应失败)
    print(f"载入 {len(cases)} 条用例:{[c.id for c in cases]}")

    async with _mcp() as mcp:
        agent = TestCaseAgent(_llm(), mcp, max_steps=12)
        orch = Orchestrator(agent)
        result = await orch.run_suite(cases)

    print("\n———— Suite 结果 ————")
    print(
        f"总数={result.total}  通过={result.passed_count}  失败={result.failed_count}  中止={result.aborted}"
    )
    for r in result.records:
        verdict = "PASS" if r.passed else "FAIL"
        print(f"\n[{r.case_id}] {verdict}  步数={len(r.steps)} token={r.token_usage}")
        print("  " + r.final_result.replace("\n", "\n  "))
    # 隔离证据:两条用例都产出了独立记录,各自有独立 exec_id
    ids = {r.case_id: r.exec_id for r in result.records}
    print(f"\n隔离证据:各用例独立 exec_id = {ids}")
    print("（即使某条 FAIL,另一条仍独立执行并裁决,互不污染）")


# ── 场景③:自愈生效(操作侧重定位) ──────────────────────────

from input.models import Phase, TestCase, TestSpec  # noqa: E402


async def scenario_heal() -> None:
    print("\n############ 场景③:自愈生效(操作侧重定位)############")
    # 显式阶段化 TestSpec(跳过翻译,省 token):步骤里的中文 target(用户名输入框 Username 等)
    # 不直接匹配英文页 → 触发**操作侧自愈**把它映射到真实英文元素;阶段 Validator 用偏-FAIL
    # 证据接地裁判核验该阶段 expected「可见 Add to cart 加入购物车按钮」。
    spec = TestSpec(
        case_id="HEAL1",
        name="自愈演示",
        base_url=BASE_URL,
        intent="验证标准用户能登录 saucedemo 并进入商品列表页",
        phases=[
            Phase(
                steps=[
                    "在用户名输入框 Username 输入 standard_user",
                    "在密码输入框 Password 输入 secret_sauce",
                    "点击 Login 登录按钮",
                ],
                expected="登录成功,进入商品列表页,页面出现 Add to cart 加入购物车按钮",
            )
        ],
    )
    case = TestCase(id="HEAL1", name="自愈演示", base_url=BASE_URL)
    async with _mcp() as mcp:
        agent = TestCaseAgent(_llm(), mcp, max_steps=10)
        rec = await agent.run(case, spec=spec)

    print(f"\n最终判定 = {'PASS' if rec.passed else 'FAIL'}  自愈次数 heal_count={rec.heal_count}")
    for a in rec.case_assertions:
        print(
            f"  [{a['status']}] 阶段{a.get('phase_index', '?')} "
            f"expected={a.get('expected', '')!r} "
            f"healed={a.get('healed')} reason={a.get('reason', '')!r}"
        )


# ── 场景④:Context Compact token/字符不溢出 ────────────────

from harness.context import ContextCompactor  # noqa: E402


def _big_snapshot(step: int) -> str:
    """造一段真实风格的大 A11y 快照观察(几十行)。"""
    rows = [
        f"### Page",
        f"- Page URL: https://www.saucedemo.com/inventory.html?step={step}",
        "### Snapshot",
        "```yaml",
    ]
    for i in range(40):
        rows.append(f'  - button "Add to cart item {i}" [ref=e{step}_{i}]')
        rows.append(f"  - text: 商品{i} 价格 ¥{i*7}")
    rows.append("```")
    return "\n".join(rows)


async def scenario_context() -> None:
    print("\n############ 场景④:Context Compact(15 步大快照不溢出)############")
    # 模拟 15 步执行后的消息历史:每步一条大快照观察
    messages: list[dict] = [
        {"role": "system", "content": "SYSTEM PROMPT ..."},
        {"role": "user", "content": "开始执行测试。"},
    ]
    for step in range(1, 16):
        messages.append({"role": "assistant", "content": f"第{step}步:点击加入购物车"})
        messages.append({"role": "user", "content": f"[观察] {_big_snapshot(step)}"})

    before_chars = sum(len(m["content"]) for m in messages)
    before_obs = sum(1 for m in messages if m["content"].startswith("[观察]"))

    compactor = ContextCompactor(keep_recent_observations=2, snapshot_max_lines=15)
    saved = compactor.compact_inplace(messages, keywords=["加入购物车", "Add to cart"])

    after_chars = sum(len(m["content"]) for m in messages)
    archived = sum(
        1 for m in messages if m["content"].startswith("[已归档]") or "已归档" in m["content"][:8]
    )
    kept_obs = sum(1 for m in messages if m["content"].startswith("[观察]"))

    print(f"\n  压缩前:{len(messages)} 条消息,{before_chars} 字符,{before_obs} 条完整观察")
    print(
        f"  压缩后:{after_chars} 字符(省下 {saved});近期保留观察 {kept_obs} 条,其余折叠归档 {archived} 条"
    )
    print(f"  体积降至原来的 {after_chars*100//before_chars}%")
    print("  → 旧观察折叠成一行(L1)、近期大快照按关键词截断(L2),15 步也不会撑爆上下文。")


SCENARIOS = {
    "suite": scenario_suite,
    "heal": scenario_heal,
    "context": scenario_context,
}


def main(argv: list[str]) -> int:
    name = argv[0] if argv else "suite"
    fn = SCENARIOS.get(name)
    if fn is None:
        print(f"未知场景 {name!r};可用:{', '.join(SCENARIOS)}")
        return 2
    asyncio.run(fn())
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
