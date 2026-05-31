"""阶段二真实验收驱动(saucedemo 端到端)。

用法:
    python examples/acceptance_stage2.py suite      # 场景①:Suite 调度 + 用例间隔离
    python examples/acceptance_stage2.py cookie      # 场景②:Cookie 复用跳过登录
    python examples/acceptance_stage2.py heal         # 场景③:断言自愈生效
    python examples/acceptance_stage2.py context      # 场景④:Context Compact token 对比

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


# ── 场景②:Cookie 复用跳过登录(直接驱动 LoginHook,0 LLM)──────

import json  # noqa: E402
import tempfile  # noqa: E402

from harness.hooks import ExecutionContext  # noqa: E402
from harness.session import (  # noqa: E402
    LoginHook,
    SessionManager,
    make_mcp_cookie_injector,
)
from input.models import SessionProfile  # noqa: E402


def _extract_result_payload(text: str):
    """从 browser_run_code_unsafe 的结果文本里取 ### Result 后的 JSON。"""
    marker = "### Result"
    if marker in text:
        seg = text.split(marker, 1)[1]
        seg = seg.split("### Ran", 1)[0].strip()
        try:
            return json.loads(seg)
        except (json.JSONDecodeError, ValueError):
            return None
    return None


async def scenario_cookie() -> None:
    print("\n############ 场景②:Cookie 复用跳过登录 ############")
    async with _mcp() as mcp:

        async def saucedemo_login(profile, ctx):
            """login_aw:真实浏览器登录 saucedemo,返回 Cookie 列表。"""
            code = (
                "async (page) => {"
                "  await page.goto('https://www.saucedemo.com');"
                "  await page.locator('[data-test=\"username\"]').fill('standard_user');"
                "  await page.locator('[data-test=\"password\"]').fill('secret_sauce');"
                "  await page.locator('[data-test=\"login-button\"]').click();"
                "  await page.waitForURL('**/inventory.html');"
                "  return await page.context().cookies();"
                "}"
            )
            res = await mcp.call_tool("browser_run_code_unsafe", {"code": code})
            cookies = _extract_result_payload(mcp.result_to_text(res)) or []
            print(
                f"  [login_aw] 真实登录完成,取到 {len(cookies)} 个 Cookie:"
                f"{[c.get('name') for c in cookies]}"
            )
            return cookies

        async def current_url() -> str:
            res = await mcp.call_tool("browser_snapshot", {})
            from harness.page_probe import parse_snapshot

            return parse_snapshot(mcp.result_to_text(res)).url

        with tempfile.TemporaryDirectory() as tmp:
            profile = SessionProfile(
                name="saucedemo",
                login_aw="saucedemo_login",
                cookie_store=f"{tmp}/saucedemo.cookies.json",
                base_url=BASE_URL,
            )
            manager = SessionManager()
            injector = make_mcp_cookie_injector(mcp, BASE_URL + "/inventory.html")
            hook = LoginHook(
                profile,
                manager,
                login_runner=saucedemo_login,
                cookie_injector=injector,
                ttl_seconds=600,
            )

            print("\n— 第 1 次 before_case(Cookie 不存在)—")
            ctx1 = ExecutionContext()
            await hook(ctx1)
            print(f"  login_via = {ctx1.get('login_via')}  (应为 login_aw:跑了登录)")
            print(f"  Cookie 是否有效 = {manager.is_valid(profile)}")

            print("\n— 第 2 次 before_case(Cookie 已存在且有效)—")
            ctx2 = ExecutionContext()
            await hook(ctx2)
            print(f"  login_via = {ctx2.get('login_via')}  (应为 cookie:跳过登录)")

            # 验证注入 Cookie 后能直达 inventory 而不被踢回登录页
            url = await current_url()
            print(f"\n  注入 Cookie 后当前 URL = {url}")
            print(f"  登录态有效(停在 inventory,未被踢回登录页)= {'inventory' in url}")


# ── 场景③:断言自愈生效 ────────────────────────────────────

from input.models import Assertion, SpecStep, TestCase, TestSpec  # noqa: E402


async def scenario_heal() -> None:
    print("\n############ 场景③:断言自愈生效 ############")
    # 显式 TestSpec(跳过生成,省 token):登录 → 断言"加入购物车按钮"可见
    # 该中文 target 不直接匹配英文页,触发 healable;自愈应把它映射到"Add to cart"
    spec = TestSpec(
        case_id="HEAL1",
        name="自愈演示",
        base_url=BASE_URL,
        steps=[
            SpecStep(action="fill", target="用户名输入框 Username", data="standard_user"),
            SpecStep(action="fill", target="密码输入框 Password", data="secret_sauce"),
            SpecStep(action="click", target="Login 登录按钮"),
        ],
        assertions=[Assertion(type="element_visible", target="加入购物车按钮")],
    )
    case = TestCase(id="HEAL1", name="自愈演示", base_url=BASE_URL)
    async with _mcp() as mcp:
        agent = TestCaseAgent(_llm(), mcp, max_steps=10)
        rec = await agent.run(case, spec=spec)

    print(f"\n最终判定 = {'PASS' if rec.passed else 'FAIL'}  自愈次数 heal_count={rec.heal_count}")
    for a in rec.case_assertions:
        print(
            f"  [{a['status']}] {a['type']} target={a['target']!r} "
            f"healed={a.get('healed')} heal_note={a.get('heal_note')!r}"
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
    "cookie": scenario_cookie,
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
