"""阶段 Validator 裁判 false-green / false-fail 评测台(执行剥离版)。

把**阶段 Validator 的裁判**从执行里剥出来,直接用真实页面快照压测。阶段化重设计
(2026-06-22)后**只剩一个裁判**——``AssertionEngine._check_llm_judge``(_JUDGE_SYSTEM,
**偏 FAIL** + 证据接地核验),逐阶段在该子页面核验 ``phase.expected``。〔旧的偏-PASS
步骤门控 ``_gate_step_done`` 已随步骤门控一并删除,本评测台不再测它。〕

eval 集:每条 (page, 预期, 真值)。真值=该预期在这页是否真成立(按裁判可见的前 6000 字定)。
统计:
- false-green = 真值 False 但裁判判 PASS(刷绿,危险);
- false-fail  = 真值 True  但裁判判 FAIL(误报失败)。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.getcwd())


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv(Path(".env"))

from harness.assertion import AssertionEngine, AssertionStatus  # noqa: E402
from harness.llm import LiteLLMClient  # noqa: E402
from input.models import Assertion  # noqa: E402


class FakeProbe:
    """只提供裁判需要的 raw_snapshot() + current_url();其余接口给桩。

    current_url 喂**真实页面 URL**(而非空串):裁判把实时 URL 当免费锚点,且证据核验
    (Fix 3 收尾)允许裁判逐字引证 URL 片段——URL 留空会让 URL 型预期无从引证,误伤评测失真。
    """

    def __init__(self, snap: str, url: str = "") -> None:
        self._snap = snap
        self._url = url

    def raw_snapshot(self) -> str:
        return self._snap

    async def current_url(self) -> str:
        return self._url


# page key → 真实 URL(与 capture.py 的 PAGES 对齐),供 FakeProbe.current_url。
PAGE_URLS = {
    "home": "https://automationexercise.com/",
    "products": "https://automationexercise.com/products",
    "search_dress": "https://automationexercise.com/products?search=dress",
    "login": "https://automationexercise.com/login",
    "cart_empty": "https://automationexercise.com/view_cart",
}

# 证据核验推翻的标记串(与 harness/assertion.py::_check_llm_judge 一致):裁判判 PASS 但
# 引证证据无一落在当前页 → fail-closed 推翻为 FAIL。据此从断言 FAIL 中**分离出"证据核验误伤"**。
_EVIDENCE_OVERRIDE_MARK = "疑似脑补"


# (page, 预期文本[中文业务语言], 真值是否成立)。真值按裁判可见的前6000字人工核定。
EVAL = [
    # —— login 页(完整可见) ——
    ("login", "页面提供 Login to your account 登录表单，含邮箱和密码输入框", True),
    ("login", "页面有 New User Signup! 新用户注册区，可填姓名和邮箱", True),
    ("login", "页面底部有 Subscription 订阅邮箱输入框", True),
    ("login", "页面顶部显示 Logged in as 某用户名，表示当前已登录", False),
    ("login", "页面出现 You have been logged in! 登录成功提示", False),
    ("login", "页面显示 Your account has been created! 账户创建成功", False),
    ("login", "顶部导航栏出现 Logout 退出登录链接", False),
    # —— cart_empty 页(完整可见) ——
    ("cart_empty", "购物车为空，提示 Cart is empty! 并有链接去购买商品", True),
    ("cart_empty", "面包屑显示 Shopping Cart", True),
    ("cart_empty", "购物车中已有 1 件商品 Sleeveless Dress", False),
    ("cart_empty", "购物车里商品数量为 1", False),
    ("cart_empty", "页面显示 Proceed To Checkout 结算按钮", False),
    ("cart_empty", "购物车内有 3 件商品，总价已显示", False),
    # —— search_dress 页 ——
    ("search_dress", "页面显示 Searched Products 标题", True),
    ("search_dress", "搜索结果包含商品 Sleeveless Dress", True),
    ("search_dress", "搜索框中内容为 dress", True),
    ("search_dress", "每个搜索结果商品都有 Add to cart 加入购物车按钮", True),
    ("search_dress", "页面提示 No products found 未找到任何商品", False),
    ("search_dress", "搜索结果显示共 0 个商品", False),
    ("search_dress", "页面顶部显示 Logged in as 已登录用户名", False),
    ("search_dress", "商品已成功加入购物车，出现 Added! 弹窗", False),
    # —— home / products(共用导航在前6000字内可见) ——
    ("home", "顶部导航包含 Products、Cart、Test Cases、API Testing 等入口", True),
    ("home", "页面顶部显示 Logged in as 用户名表示已登录", False),
    ("home", "页面顶部出现 Logout 链接", False),
    ("products", "顶部导航含 Products 和 Cart 入口", True),
    ("products", "购物车角标显示已有 5 件商品", False),
]


async def main() -> None:
    snaps = json.load(open("eval_fg/snapshots.json", encoding="utf-8"))
    llm = LiteLLMClient()
    print(f"模型={llm.model}\n")

    # 计数:阶段 Validator(``_check_llm_judge``)的混淆
    fg = 0  # false-green:真值 False 却判 PASS(刷绿)
    ff = 0  # false-fail:真值 True 却判 FAIL(误报失败)
    # 证据核验(Fix 3 收尾)专项:把"原始模型判定"与"核验后判定"分开统计,量化新加的核验
    # 对弱模型的**误伤**(真预期被核验推翻)与**额外拦截**(假预期被核验拦下)。
    override_total = 0  # 证据核验推翻 PASS→FAIL 的总次数
    override_ff = 0  # 其中误伤:真预期被推翻(本应 PASS)
    override_caught = 0  # 其中有益:假预期被推翻(原始模型想刷绿,被核验拦下)
    n_false = sum(1 for _, _, t in EVAL if not t)
    n_true = sum(1 for _, _, t in EVAL if t)

    print(f"{'真值':<4}{'Validator':<11}{'核验':<6}预期")
    print("-" * 80)
    for page, exp, truth in EVAL:
        probe = FakeProbe(snaps[page], PAGE_URLS.get(page, ""))
        # 阶段 Validator 裁判(偏 FAIL + 证据确定性核验)——逐阶段裁决走的就是它
        engine = AssertionEngine(probe, llm=llm)
        r = await engine._check_llm_judge(Assertion(type="llm_judge", target=exp, expected=exp))
        verdict_pass = r.status == AssertionStatus.PASS
        # 证据核验是否推翻了模型的 PASS(reason 含标记串 = 模型原判 PASS 但证据不在页 → 被推翻)
        overridden = (r.status == AssertionStatus.FAIL) and (_EVIDENCE_OVERRIDE_MARK in r.reason)
        if overridden:
            override_total += 1
            if truth:
                override_ff += 1  # 真预期被推翻 = 误伤
            else:
                override_caught += 1  # 假预期被推翻 = 有益拦截

        # 标记错误
        def mark(passed: bool) -> str:
            if truth and not passed:
                return "FF✗"  # false-fail
            if (not truth) and passed:
                return "FG⚠"  # false-green
            return "ok"

        v = mark(verdict_pass)
        if v == "FG⚠":
            fg += 1
        elif v == "FF✗":
            ff += 1
        tval = "真" if truth else "假"
        ov = "推翻" if overridden else ""
        print(f"{tval:<5}{v:<12}{ov:<7}[{page}] {exp[:38]}")

    print("-" * 80)
    print(f"\n样本:假预期 {n_false} 条 / 真预期 {n_true} 条\n")
    print(f"{'阶段 Validator(偏FAIL)':<22}{'false-green(刷绿)':<22}{'false-fail(误报失败)'}")
    print(f"{'':<22}{fg}/{n_false} = {fg/n_false:.0%}" f"{'':<10}{ff}/{n_true} = {ff/n_true:.0%}")
    # 证据核验专项报告(A-2 核心:量化对弱模型的误伤率)
    print(f"\n证据确定性核验(Fix 3 收尾)专项:")
    print(f"  推翻 PASS→FAIL 共 {override_total} 次")
    print(
        f"  · 误伤(真预期被推翻)= {override_ff}/{n_true} = {override_ff/n_true:.0%}"
        "  ← A-2 关注:核验把好用例判挂的比例"
    )
    print(
        f"  · 有益拦截(假预期被推翻,阻止刷绿)= {override_caught}/{n_false} = {override_caught/n_false:.0%}"
    )


if __name__ == "__main__":
    asyncio.run(main())
