# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> 本文件是**蓝图 + 铁律 + 索引**,不是规格副本。所有细节以 `实现规格说明书.md` 为唯一真相源;
> 这里只给整体认知、不可违反的约束、当前进度和指路。动手前请按下方「工作约定」重读对应规格小节。

## 产品一句话

内网 Web 业务测试自动化平台。核心链路:

```
业务用例(Excel) → TestSpec(结构化执行规格+断言) → AI Agent 驱动浏览器执行(playwright-mcp/ReAct)
→ 结构化断言验证(规则引擎,非 LLM 眼判) → 产出 pytest-bdd Playwright 代码
```

## 铁律(违反即错,必须常驻)

1. **浏览器层只用 playwright-mcp 的 stdio 模式,绝不用 CDP HTTP**(内网代理会拦截 → 504)。
2. **断言由规则引擎确定性验证,不让 LLM 眼判 PASS/FAIL**。判断在"翻译时"一次性做(预期→结构化 Assertion),执行时只做确定性比较。
3. **本地 LLM 的 tool_call 必须容错**(宽松 JSON / 从 content 提取 / 重试),偶发格式错误不得搞崩 ReAct 循环。
4. 最终 PASS/FAIL **以断言裁决为准,不取 LLM 自报的 TEST_RESULT**。
5. 实现原则(规格 §0):前后端分离、数据层抽象(SQLModel,不直接写 SQL)、输入/输出抽象(都产出 `TestCase`/落 `ExecutionRecord`)、核心表预留 `updated_at`/`owner`/`external_id`、分阶段不跳跃。

## 架构大图(需要读多文件才能拼出的部分)

单条用例的执行由 `harness/agent.py::TestCaseAgent.run()` 总装,串起以下模块:

- `intelligence/pre_analysis.py` — TestCase → **TestSpec**(纯 LLM 翻译,阶段一无词汇表)。坏输出降级为朴素映射。
- `harness/step_plan.py` — TestSpec.steps → **StepPlan** 状态机(pending/active/done/...),暴露 `mark_step_done` 工具给 LLM。
- `harness/prompt.py` — System Prompt **分层**(Base+Context+Task+Tools),`PromptBuilder.build(step_plan)` 每轮重算反映进度。
- `harness/react_loop.py` — **ReAct 主循环**。Reason→Act→Observe;护栏:循环检测(连续 3 次同调用)、max_steps、哑火续推(`max_idle_nudges`)、tool_call 容错终止。**观察以 user 消息文本回灌**(不依赖 tool_call_id 配对,本地模型更稳)。
- `harness/llm.py` — LiteLLM 封装 + tool_call 容错 + token 统计。配置走 env(`LLM_MODEL`/`LLM_API_BASE`/`LLM_API_KEY`)。
- `mcp_client/client.py` — MCP 官方 SDK(stdio)连 playwright-mcp;工具格式 MCP↔LiteLLM 转换。
- `harness/page_probe.py` — 解析 playwright-mcp 的 `browser_snapshot`(YAML A11y 树)为节点,按语义 target 双向包含匹配(`MCPPageProbe` 实现断言引擎的 `PageProbe` 协议)。
- `harness/assertion.py` ★ — **断言规则引擎**。阶段一支持 DOM/文本/URL;元素找不到标 `healable`;接 healer 做目标重定位复验;`verdict()` 裁决(任一 FAIL 即不通过,全 skipped 不算可信通过)。
- `harness/healing.py` — **Healing Subagent**(独立 context)。断言侧:重定位断言目标;操作侧:工具报错时重定位并把建议回灌 ReAct。P1 角色→P5 视觉,防臆造(候选必须落在快照里)。
- `harness/context.py` — **Context Compact**。发 LLM 前压缩:旧观察折叠成一行(L1)、近期快照按关键词相关度截断(L2),治 token 膨胀。
- `harness/recorder.py` — 汇总 `ExecutionRecord`;`to_history()` 把 model_output / action_result 分离序列化。
- `harness/hooks.py` — 生命周期 Hook(before_case 失败→用例 FAIL 不进 Agent)+ 共享 `ExecutionContext`。
- `harness/session.py` — `SessionManager`(Cookie 存盘+有效期,跨用例共享)+ `LoginHook`(有效复用、过期跑 login_aw 重登)。
- `harness/precondition.py` — 预置条件 LLM 三分类(state_hook/action_step/ambiguous),低置信/无映射降级 ambiguous。
- `harness/skills.py` — Skill 体系:DomainSkill(常注入)/ PageSkill(按 URL 动态加载卸载)/ ToolSkill(关键词相关度);Agent 按当前 URL+步骤关键词动态注入。
- `harness/permission.py` — 高危词 + prod 环境锁;Reason 后 Act 前拦截;trust_mode / 可注入 approver;无 approver 默认拒绝。
- `harness/orchestrator.py` — Suite 调度:**串行**执行用例、用例间隔离(异常→FAIL 不拖垮他人)、suite 级 hooks、结果汇总。
- `harness/tools.py` — Custom Tool 注册:`@tool` 装饰器 + YAML `command`;LLM 按需调用;Agent 路由(控制→StepPlan / 自定义→Registry / 其余→MCP)。

数据结构全部在 `input/models.py`(pydantic;落库 SQLModel 留到 T-21)。

## 关键决策(已定,勿反复纠结)

- Python **3.11** + `uv`(规格用 `str | None` 等 3.10+ 语法;本机默认 3.9 不可用)。
- 本地包名 **`mcp_client`** 而非 `mcp`,避让官方 `mcp` SDK 顶层包名冲突。
- ReAct 用**文本式观察回灌**,不依赖严格 tool_call_id 配对(本地 Qwen 支持不稳)。
- `ExecutionRecord.case_assertions` 是**有意新增**字段(规格模型没列),承载可信 PASS/FAIL 依据。
- 断言**聚合**用例级 `assertions` + 各步 `expect`(`agent.collect_assertions`),因 LLM 放断言位置不稳定。

## 实施进度

- 阶段一 ✅ T-01~T-10(主干跑通,断言驱动 PASS/FAIL;saucedemo 端到端验证过)
- 阶段二 ✅ T-11~T-19(自愈 / Context Compact / Hooks / Session+LoginHook / 预置条件分类器 / Skill / Permission / Orchestrator / Custom Tool)。四条验收标准 saucedemo 真实演示通过(见 `examples/acceptance_stage2.py`)。
- 阶段三 ✅ T-20~T-22(`codegen/`BDDGenerator / `storage/db.py` SQLModel 持久化 / `intelligence/`词汇表+Scanner)。
- **下一步:阶段四**(工程化界面 T-23~T-27:FastAPI 路由+SSE / React 控制台)。阶段五(用例管理平台集成)规格明确"现在不做",只预留 `external_id`。
- 全量单测 **274 passed / 1 skipped**。

### 待确认的自主决策(Sprint 2-C,趁我休息时按合理默认实现)
- Permission:**无 approver 时高危操作默认拒绝**;控制工具(mark_step_done)豁免权限检查。
- Orchestrator:用例**串行**(遵 §0/§7/§8),before_suite 失败则整 Suite 中止。
- Custom Tool:command 工具用 `{arg}` 占位 + shell 执行,异常/非零退出转文本返回。
- **未做**:`custom_tool` 类**断言**仍标 skipped(只实现了"LLM 按需调用自定义工具",数据断言走断言引擎的路径留待阶段三数据断言一起做)。

T-xx ↔ 规格小节对照见 `实现规格说明书.md` §5(各模块详细规格)与 §6(实施计划)。

## 工作约定

- **每个任务动手前,重读 `实现规格说明书.md` 对应小节**(以原文为准,别凭记忆);并核对已实现部分有无偏离。
- 每个任务配单元测试;不连真实 LLM/浏览器,用 fake/mock 驱动(参考 `tests/` 现有写法)。
- 改完跑 `pytest`,并 `isort`+`black` 格式化后再交。
- 分阶段推进:一个阶段验收通过再进下一阶段,不跳阶段、不过度设计未来阶段。
- 不确定的设计点(尤其用例管理平台集成)不要自行假设,先问用户。

## 常用命令

```bash
# 环境(首次)
uv venv --python 3.11 && source .venv/bin/activate && uv pip install -r requirements.txt

# 测试
source .venv/bin/activate
python -m pytest -q                          # 全量
python -m pytest tests/test_assertion.py -q  # 单文件
python -m pytest tests/test_react_loop.py::test_happy_path_completes  # 单用例

# 格式化(提交前)
isort harness mcp_client input intelligence cli tests && black harness mcp_client input intelligence cli tests

# 运行一条用例(阶段一验收入口)
python cli/run_case.py --excel examples/saucedemo_cases.xlsx --case-id TC101 --base-url https://www.saucedemo.com
python cli/run_case.py --excel <用例.xlsx> --case-id <ID> --spec-only   # 只生成并打印 TestSpec
python cli/run_case.py --check-llm                                       # LLM 连通性自检

# LLM 配置:.env(项目根,自动加载) 或 env 或 CLI flag
#   LLM_MODEL / LLM_API_BASE / LLM_API_KEY；模型名需带 provider 前缀(如 openai/xxx、ollama/xxx)

# 浏览器层:npx @playwright/mcp(stdio);saucedemo 等会触发 Chrome 密码泄露弹框,可加 --isolated --headless 规避
```

测试配置:`pyproject.toml` 的 `[tool.pytest.ini_options]` 已设 `asyncio_mode = "auto"`(async 测试无需标记)。
领域模型 `TestCase`/`TestSpec` 及 `TestCaseAgent` 名字以 `Test` 开头,已用 `__test__ = False` 避免 pytest 误收集。
