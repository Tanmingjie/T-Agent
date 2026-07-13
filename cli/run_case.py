"""命令行入口。

    python cli/run_case.py --excel cases.xlsx --case-id TC001 --base-url http://intranet

流程:解析 Excel → 选用例 → 生成并打印可读 TestSpec → Midscene 视觉执行 → 打印结果。

LLM 部署配置走环境变量(LLM_MODEL / LLM_API_BASE / LLM_API_KEY),不在此硬编码;
Midscene 视觉模型走 MIDSCENE_MODEL_*。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
# 允许 `python cli/run_case.py` 直接运行(把项目根加入 sys.path)
sys.path.insert(0, str(_ROOT))


def _load_dotenv(path: Path) -> None:
    """轻量加载 .env(无第三方依赖);不覆盖已存在的环境变量。"""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv(_ROOT / ".env")

from harness.llm import LiteLLMClient  # noqa: E402
from harness.midscene_agent import MidsceneCaseAgent  # noqa: E402
from input.excel_parser import parse_excel  # noqa: E402
from input.models import TestCase, TestSpec  # noqa: E402


def _print_spec(spec: TestSpec) -> None:
    print("\n" + "═" * 60)
    print(f"TestSpec:{spec.name}(case_id={spec.case_id})")
    print(f"  base_url: {spec.base_url}")
    if spec.intent:
        print(f"  intent(测试意图): {spec.intent}")
    if spec.preconditions:
        print("  preconditions(前置背景):")
        for p in spec.preconditions:
            print(f"    - {p}")
    print(f"  phases(共 {len(spec.phases)} 个阶段):")
    no = 0
    for pi, ph in enumerate(spec.phases, 1):
        print(f"    ── 阶段 {pi} ──")
        for s in ph.steps:
            no += 1
            print(f"      {no}. {s}")
        print(f"      预期⟶ {ph.expected or '(无)'}")
    print("═" * 60 + "\n")


def _print_record(record) -> None:
    print("\n" + "─" * 60)
    print("执行过程:")
    for s in record.steps:
        intent = s.intent or (s.reasoning[:40] + "…" if len(s.reasoning) > 40 else s.reasoning)
        print(f"  [{s.step_no:>2}] {s.tool_name}  intent={intent}")
        if s.url:
            print(f"       URL: {s.url}")
        result_snip = s.tool_result.replace("\n", " ")[:120]
        print(f"       结果: {result_snip}")
    print("─" * 60)
    verdict = "✅ PASS" if record.passed else "❌ FAIL"
    print(f"\n最终判定(断言驱动): {verdict}")
    print(record.final_result)
    print(f"\n步数={len(record.steps)}")
    metrics = getattr(record, "metrics", None) or {}
    midscene = (metrics.get("midscene") or {}) if isinstance(metrics, dict) else {}
    artifacts = midscene.get("artifacts") or {}
    if artifacts.get("report"):
        print(f"Midscene report: {artifacts['report']}")
    if artifacts.get("artifact_dir"):
        print(f"Midscene artifacts: {artifacts['artifact_dir']}")
    print("─" * 60 + "\n")


def _select_case(cases: list[TestCase], case_id: str | None) -> TestCase:
    if not cases:
        raise SystemExit("Excel 中未解析到任何用例。")
    if case_id is None:
        return cases[0]
    for c in cases:
        if c.id == case_id:
            return c
    ids = ", ".join(c.id for c in cases)
    raise SystemExit(f"未找到 case-id={case_id}。可用:{ids}")


async def _check_llm(args: argparse.Namespace) -> int:
    """连通性自检:不跑用例,只验证 LLM 是否可达、能否正常返回。"""
    llm = LiteLLMClient(model=args.model, api_base=args.api_base, api_key=args.api_key)
    print("LLM 配置:")
    print(f"  model    = {llm.model}")
    print(f"  api_base = {llm.api_base or '(未设置)'}")
    print(f"  api_key  = {'已设置' if llm.api_key else '(未设置)'}")
    if "/" not in llm.model:
        print(
            f"\n⚠️  模型名 {llm.model!r} 没有 provider 前缀,LiteLLM 多半会报"
            f"「LLM Provider NOT provided」。\n"
            f"   内网 OpenAI 兼容网关请用  openai/{llm.model}  并设置 LLM_API_BASE。"
        )
    print("\n正在发送一条测试消息…")
    try:
        r = await llm.chat([{"role": "user", "content": "只回复两个字:正常"}])
    except Exception as e:  # noqa: BLE001
        print(f"\n❌ 调用失败:{type(e).__name__}: {e}")
        print(
            "   常见原因:模型名缺 provider 前缀 / api_base 路径(是否要 /v1) / api_key 错误 / 网关不可达。"
        )
        return 1
    print(f"\n✅ 连通正常。")
    print(f"   回复: {r.content!r}")
    print(
        f"   tokens: prompt={r.usage.prompt_tokens} completion={r.usage.completion_tokens} total={r.usage.total_tokens}"
    )
    return 0


async def _run(args: argparse.Namespace) -> int:
    if args.check_llm:
        return await _check_llm(args)
    if not args.excel:
        raise SystemExit("缺少 --excel(执行用例时必需);仅自检 LLM 请用 --check-llm。")
    cases = parse_excel(args.excel, base_url=args.base_url or "")
    case = _select_case(cases, args.case_id)
    print(f"已选用例:{case.id} - {case.name}")
    if not args.base_url:
        print("⚠️  未提供 --base-url,TestCase.base_url 为空(Agent 可能无法导航)。")

    llm = LiteLLMClient(model=args.model, api_base=args.api_base, api_key=args.api_key)
    # 翻译知识/操作指南(--knowledge <file>):注入翻译 prompt 助补全流程/对齐术语/写对 expected
    translation_knowledge = ""
    if args.knowledge:
        translation_knowledge = Path(args.knowledge).read_text(encoding="utf-8")
        print(f"已加载翻译知识:{args.knowledge}({len(translation_knowledge)} 字符)")
    if args.context:
        translation_knowledge = f"{translation_knowledge}\n\n{args.context}".strip()
    agent = MidsceneCaseAgent(
        llm=llm,
        translation_knowledge=translation_knowledge,
    )

    # 查看翻译 prompt(调试):打印实际喂给翻译 LLM 的 system + user 消息(含用例规范注入),
    # 不调用 LLM。用于核对「用例规范是否进了翻译、长什么样」。
    if args.dump_spec_prompt:
        from intelligence.pre_analysis import build_spec_messages

        msgs = build_spec_messages(case, knowledge=translation_knowledge)
        print("\n" + "═" * 60)
        print("翻译 Prompt(喂给翻译 LLM 的消息,未调用 LLM):")
        for m in msgs:
            print("\n" + "─" * 60)
            print(f"# role = {m['role']}")
            print("─" * 60)
            print(m["content"])
        print("═" * 60 + "\n")
        return 0

    # 先生成并打印 TestSpec 供审查
    print("正在生成 TestSpec…")
    spec = await agent.generate_spec(case)
    _print_spec(spec)
    if args.spec_only:
        print("(--spec-only:仅生成 TestSpec,不执行)")
        return 0

    record = await agent.run(case, spec=spec, run_id="cli")
    _print_record(record)
    return 0 if record.passed else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="执行一条业务测试用例(阶段一验收)")
    p.add_argument("--excel", default=None, help="用例 Excel 路径(执行用例时必需)")
    p.add_argument("--case-id", default=None, help="用例 ID(默认取第一条)")
    p.add_argument("--base-url", default=None, help="被测系统地址(注入 TestCase.base_url)")
    p.add_argument("--context", default="", help="附加业务上下文(注入 Prompt)")
    p.add_argument("--model", default=None, help="LLM 模型名(默认读 env LLM_MODEL)")
    p.add_argument(
        "--api-base", default=None, help="LLM API base/base_url(默认读 env LLM_API_BASE)"
    )
    p.add_argument("--api-key", default=None, help="LLM API key(默认读 env LLM_API_KEY)")
    p.add_argument(
        "--knowledge",
        default=None,
        help="翻译知识/操作指南文件(自然语言文本):注入翻译 prompt 助补全流程/对齐术语/写对 expected",
    )
    p.add_argument("--spec-only", action="store_true", help="只生成并打印 TestSpec,不执行")
    p.add_argument(
        "--dump-spec-prompt",
        action="store_true",
        help="只打印翻译 prompt(喂翻译 LLM 的 system+user,含用例规范注入),不调用 LLM、不执行",
    )
    p.add_argument("--check-llm", action="store_true", help="只做 LLM 连通性自检,不跑用例")
    p.add_argument("-v", "--verbose", action="store_true", help="输出 DEBUG 日志")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
