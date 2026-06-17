"""裁判 false-green / false-fail 评测台(执行剥离版)。

把两个裁判从执行里剥出来,直接用真实页面快照压测:
- 断言通道 ``AssertionEngine._check_llm_judge``(_JUDGE_SYSTEM,**偏 FAIL**);
- 步骤门控 ``_gate_step_done``(_GATE_SYSTEM,**偏 PASS/放行**)——AE03/FG01 实际裁决走的就是它。

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

from harness.agent import _gate_step_done  # noqa: E402
from harness.assertion import AssertionEngine, AssertionStatus  # noqa: E402
from harness.llm import LiteLLMClient  # noqa: E402
from input.models import Assertion  # noqa: E402


class FakeProbe:
    """只提供裁判需要的 raw_snapshot();其余接口给桩。"""

    def __init__(self, snap: str) -> None:
        self._snap = snap

    def raw_snapshot(self) -> str:
        return self._snap

    async def current_url(self) -> str:
        return ""


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

    # 计数:[gate, assertion] 各自的混淆
    fg = {"gate": 0, "assert": 0}  # false-green
    ff = {"gate": 0, "assert": 0}  # false-fail
    n_false = sum(1 for _, _, t in EVAL if not t)
    n_true = sum(1 for _, _, t in EVAL if t)

    print(f"{'真值':<4}{'gate':<6}{'assert':<8}预期")
    print("-" * 78)
    for page, exp, truth in EVAL:
        probe = FakeProbe(snaps[page])
        # 门控(偏 PASS)
        met, _ = await _gate_step_done(llm, probe, exp)
        gate_pass = met
        # 断言裁判(偏 FAIL)
        engine = AssertionEngine(probe, llm=llm)
        r = await engine._check_llm_judge(Assertion(type="llm_judge", target=exp, expected=exp))
        assert_pass = r.status == AssertionStatus.PASS

        # 标记错误
        def mark(passed: bool) -> str:
            if truth and not passed:
                return "FF✗"  # false-fail
            if (not truth) and passed:
                return "FG⚠"  # false-green
            return "ok"

        g, a = mark(gate_pass), mark(assert_pass)
        if g == "FG⚠":
            fg["gate"] += 1
        if g == "FF✗":
            ff["gate"] += 1
        if a == "FG⚠":
            fg["assert"] += 1
        if a == "FF✗":
            ff["assert"] += 1
        tval = "真" if truth else "假"
        print(f"{tval:<5}{g:<7}{a:<9}[{page}] {exp[:40]}")

    print("-" * 78)
    print(f"\n样本:假预期 {n_false} 条 / 真预期 {n_true} 条\n")
    print(f"{'裁判':<10}{'false-green(刷绿)':<22}{'false-fail(误报失败)'}")
    print(
        f"{'门控(偏PASS)':<12}{fg['gate']}/{n_false} = {fg['gate']/n_false:.0%}"
        f"{'':<10}{ff['gate']}/{n_true} = {ff['gate']/n_true:.0%}"
    )
    print(
        f"{'断言(偏FAIL)':<12}{fg['assert']}/{n_false} = {fg['assert']/n_false:.0%}"
        f"{'':<10}{ff['assert']}/{n_true} = {ff['assert']/n_true:.0%}"
    )


if __name__ == "__main__":
    asyncio.run(main())
