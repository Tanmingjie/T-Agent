"""证据接地核验(层2)A/B 对比评测:开核验 vs 关核验。

回答的问题(用户 2026-06-24):阶段裁决里那层**确定性证据接地推翻**(把模型判的 PASS、在
证据无据时 fail-closed 推翻成 FAIL)到底值不值得留?它**拦下多少真脑补刷绿**(有益),又
**误伤多少真 PASS**(代价)?

两个配置,**同一次 LLM 调用**还原(接地推翻是确定性的、只把 PASS→FAIL,故模型原判可无损反推):
- **B 关核验(纯模型 + 解析卫生)**:取模型裁决;只保留层(1)解析兜底 fail-closed(JSON 炸/无
  verdict → FAIL),**不做**层(2)证据接地推翻。
- **A 开核验(现状)**:模型裁决 + 层(1) + 层(2)证据接地推翻(`_check_llm_judge` 现行为)。

层(2)推翻只在两处发生,各留唯一 reason 标记(见 harness/assertion.py::_check_llm_judge):
  · "疑似脑补"        —— evidence 无任一锚点落页 → 推翻
  · "与 expected 矛盾" —— E5:expected 强锚点一个都不落页 → 推翻
层(1)解析卫生 FAIL 标记是 "未给出明确裁决",**不带**上面两个 → 两配置都保留(B 也 fail-closed)。

统计:false-green(真值 False 判 PASS,危险)/ false-fail(真值 True 判 FAIL,误报)。
对比 A vs B 的两个净效果数:
  · 有益拦截 = B 误绿但 A 拦下(false-green 减少量)
  · 误伤     = B 正确但 A 推成 FAIL(false-fail 增加量)
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.getcwd())

from eval_fg.judge_eval import EVAL, PAGE_URLS, FakeProbe  # noqa: E402
from harness.assertion import AssertionEngine, AssertionStatus  # noqa: E402
from harness.llm import LiteLLMClient  # noqa: E402
from input.models import Assertion  # noqa: E402

# 层(2)证据接地推翻的 reason 标记(与 harness/assertion.py 一致)。两者都是 PASS→FAIL。
_OVERRIDE_MARKS = ("疑似脑补", "与 expected 矛盾")


def _was_overridden(reason: str) -> bool:
    """final=FAIL 时,是否由层(2)接地推翻造成(而非模型原判 FAIL / 层1解析卫生)。"""
    return any(m in (reason or "") for m in _OVERRIDE_MARKS)


def _mark(truth: bool, passed: bool) -> str:
    if truth and not passed:
        return "FF"  # false-fail
    if (not truth) and passed:
        return "FG"  # false-green
    return "ok"


async def main() -> None:
    import json

    snaps = json.load(open("eval_fg/snapshots.json", encoding="utf-8"))
    llm = LiteLLMClient()
    print(f"模型={llm.model}\n")

    n_false = sum(1 for _, _, t in EVAL if not t)
    n_true = sum(1 for _, _, t in EVAL if t)

    # 混淆计数:A=开核验(现状) / B=关核验(纯模型+解析卫生)
    fg_a = ff_a = fg_b = ff_b = 0
    benefit = 0  # B 误绿 → A 拦下(有益拦截)
    harm = 0  # B 正确 PASS → A 推成 FAIL(误伤)

    print(f"{'真值':<5}{'B关核验':<9}{'A开核验':<9}{'差异':<10}预期")
    print("-" * 88)
    for page, exp, truth in EVAL:
        probe = FakeProbe(snaps[page], PAGE_URLS.get(page, ""))
        engine = AssertionEngine(probe, llm=llm)
        r = await engine._check_llm_judge(Assertion(type="llm_judge", target=exp, expected=exp))
        a_pass = r.status == AssertionStatus.PASS
        overridden = (r.status == AssertionStatus.FAIL) and _was_overridden(r.reason)
        # B 关核验 = 还原模型原判:final PASS → PASS;final FAIL 但被层2推翻 → 模型原判 PASS;
        # 其余(模型原判 FAIL / 层1解析卫生 FAIL)→ FAIL。
        b_pass = a_pass or overridden

        ma, mb = _mark(truth, a_pass), _mark(truth, b_pass)
        if ma == "FG":
            fg_a += 1
        elif ma == "FF":
            ff_a += 1
        if mb == "FG":
            fg_b += 1
        elif mb == "FF":
            ff_b += 1

        diff = ""
        if b_pass and not a_pass:  # 层2 把 B 的 PASS 推成了 A 的 FAIL
            if truth:
                harm += 1
                diff = "误伤✗"
            else:
                benefit += 1
                diff = "拦截✓"
        tval = "真" if truth else "假"
        print(
            f"{tval:<6}{('PASS' if b_pass else 'FAIL'):<10}"
            f"{('PASS' if a_pass else 'FAIL'):<10}{diff:<12}[{page}] {exp[:34]}"
        )

    print("-" * 88)
    print(f"\n样本:假预期 {n_false} 条 / 真预期 {n_true} 条\n")
    print(f"{'配置':<22}{'false-green(刷绿)':<22}{'false-fail(误报)':<20}{'总错'}")
    print(
        f"{'B 关核验(纯模型)':<20}"
        f"{fg_b}/{n_false} = {fg_b / n_false:.0%}{'':<8}"
        f"{ff_b}/{n_true} = {ff_b / n_true:.0%}{'':<6}{fg_b + ff_b}"
    )
    print(
        f"{'A 开核验(+接地)':<20}"
        f"{fg_a}/{n_false} = {fg_a / n_false:.0%}{'':<8}"
        f"{ff_a}/{n_true} = {ff_a / n_true:.0%}{'':<6}{fg_a + ff_a}"
    )
    print(f"\n接地层(2)净效果:")
    print(f"  · 有益拦截(拦下真脑补刷绿,false-green 减少)= {benefit}")
    print(f"  · 误伤    (推翻真 PASS,false-fail 增加)    = {harm}")
    net = benefit - harm
    verdict = "净正(值得留)" if net > 0 else ("净负(建议撤)" if net < 0 else "净零(不干活)")
    print(f"  · 净贡献(有益 − 误伤)= {net:+d}  → {verdict}")


if __name__ == "__main__":
    asyncio.run(main())
