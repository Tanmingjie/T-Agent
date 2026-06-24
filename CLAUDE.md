# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> 本文件是**蓝图 + 铁律 + 索引**，不是规格副本。所有细节以 `实现规格说明书.md` 为唯一真相源；
> 这里只给整体认知、不可违反的约束、当前进度和指路。动手前按下方「工作约定」重读对应规格小节。

## 产品一句话

内网 Web 业务测试自动化平台。核心链路：

```
业务用例(Excel) → TestSpec(结构化执行规格+断言) → AI Agent 驱动浏览器执行(playwright-mcp/ReAct)
→ 结构化断言验证(规则引擎,非 LLM 眼判) → 产出 pytest-bdd Playwright 代码
```

## 当前状态:架构重设计中(2026-06-22 起)

旧版「铁律」已**全部清除**。我们正在推翻翻译/执行/裁决的旧设计(详见 `产品设计文档_v2.0.md`
与进度条目),原铁律里的具体结论(终态裁判 + 确定性锚点为主、偏-PASS 门控、URL/data
锚点等)已不作为约束。

**新约束待定**——重设计稳定后,会在此处重新沉淀(或就不沉淀)。在此期间,以**产品设计文档
最新版 + 当前对话拍板**为准,不要拿旧铁律(包括本文件早期 git 历史里的版本)反推现状。

## 架构大图(需要读多文件才能拼出的部分)

单条用例的执行由 `harness/agent.py::TestCaseAgent.run()` 总装,串起以下模块：

- `intelligence/pre_analysis.py` — TestCase → **阶段化 TestSpec**(纯 LLM 翻译,**只产意图不接地**,2026-06-22 重设计)。产出 `{intent, preconditions:[str], phases:[{steps:[str], expected}]}`:每阶段 = 一组自然语言步骤(驱动)+ 一条组级预期(验证依据)。不写 selector/不锁动作/不猜元素,接地全交运行时。坏输出降级为单阶段无损映射。契约见 `docs/test_spec_v2.md`。
- `harness/step_plan.py` — 阶段化 TestSpec 的 `phases.steps` **摊平**成扁平步骤状态机(pending/active/done/...)+ 记每步所属阶段;暴露 `mark_step_done` 工具 + **阶段边界**查询(`is_phase_last_step`/`phase_last_step_no`)。
- `harness/prompt.py` — System Prompt **分层**(Base+Context+Task+Tools),`PromptBuilder.build(step_plan)` 每轮重算反映进度。Task 层渲染 `intent`/`preconditions`/阶段化步骤清单,**不渲染 expected**(FG01:验证依据绝不进驱动)。
- `harness/react_loop.py` — **ReAct 主循环**。Reason→Act→Observe;护栏:循环检测、max_steps、哑火续推(`max_idle_nudges`)、tool_call 容错、**过早 mark_done 软护栏 两分支**(E2:没操作 / 操作无效果——后者用页面指纹 URL+ref 集判,无 LLM)、**单步定位失败预算**(`STEP_FAIL_BUDGET` 默认 5 → `STEP_FAILED` 快速失败,E2 由 3→5)、**步级卡住主动提醒**(E2:同步连续 N 轮 fp 未变 → 注入诊断引导 + E3 浮现命中 skill 名催 `load_skill` 甲 / 持续仍卡 → 平台 `auto_load` top1 注入兜底 乙)、**跨 phase 重置**(E2:进新 phase 清零 idle/loop)。**观察以 user 消息文本回灌**(不依赖 tool_call_id 配对)。**阶段边界 Validator**(2026-06-22 取代步骤门控):`on_phase_end(phase_index) -> str|None` 在某阶段**最后一步** mark_step_done 落定时触发,核验该阶段 expected——返回非空原因 = 未达成 → `PHASE_FAILED` **阶段失败即失败**(不 replan/重试)。**哑火可观测(2026-06-24)**:`ReActResult.idle_outputs` 记每个哑火轮 `{iteration,step_no,kind,rechecked,text}`——`kind` 三态 narration_only(纯叙述放弃)/ malformed_tool_call(调了但格式坏)/ premature_result;透到 `metrics.execution.idle_outputs` 落库 + CLI 打印,供"卡死类"失败事后定性(模型放弃 vs 流式丢采)。**参数归一(2026-06-24)**:`_normalize_ref_target` 在 dispatch 前给 browser_* 工具把 `ref`/`element_ref`/`ref_id` 补进 `target`(本版 playwright-mcp 用 `target` 装 ref,模型先验是 `ref`、会抖动),消白烧步骤。
- `harness/llm.py` — LiteLLM 封装 + tool_call 容错 + token 统计。配置走 env(`LLM_MODEL`/`LLM_API_BASE`/`LLM_API_KEY`)。**tool_call 容错链**:①标准 `tool_calls` 字段 → ②宽松 JSON 修复 → ③从 content 提取(`<tool_call>` 标签 / ```围栏 / 裸 JSON / **`函数名({...})` 文本** ← 2026-06-24 治 deepseek-v4-flash 偶发把调用吐进 content 文本而非 tool_calls 通道,实测 TC201 哑火卡死根因)→ ④纠偏重试 1 次 → 仍失败抛 `LLMToolCallError`。`extract_verdict` 从坏 JSON 正则捞 PASS/FAIL(裁决解析卫生)。**未设 `max_tokens`**(judge 偶发因 provider 默认输出上限截断 → 层1 兜底捞回;已加 prompt 简短约束减少触发)。
- `mcp_client/client.py` — MCP 官方 SDK(stdio)连 playwright-mcp;工具格式 MCP↔LiteLLM 转换。
- `harness/page_probe.py` — 解析 playwright-mcp 的 `browser_snapshot`(YAML A11y 树)为节点,按语义 target 双向包含匹配(`MCPPageProbe` 实现断言引擎的 `PageProbe` 协议)。
- `harness/assertion.py` ★ — **断言引擎 + 阶段 Validator**。`_check_llm_judge`(**偏-FAIL** + 要求模型逐字引证页面 evidence,evidence 仅作**可审计依据**写入 reason)是阶段 Validator 的核心;另留 DOM/文本/URL/custom_tool 确定性检查 + healer 重定位 + `verdict()`(化石,阶段化下不再被 agent.run 调用)。裁决保留两道**与模型独立**的底线:**层(1)解析卫生**(`extract_verdict` 正则捞 verdict / 无 verdict→FAIL,fail-closed)+ **G1 主裁决缺失三态→FAIL**(未接 LLM / 调用失败 / 解析不出 verdict)。**〔2026-06-24 撤销「平台确定性证据接地推翻」(用户拍板①)〕**:eval_fg A/B 扩样(n=63,3 站点,6 轮)实测接地层有益拦截恒为 0、仅偶发误伤 → 净 ≤0,偏-FAIL prompt 自身已扛住全部 false-green;**裁决权交回模型,evidence 不再作推翻闸门**(删 `_norm_evidence`/`_evidence_*`/`_expected_*` + 锚点正则 + E5)。回归基准见 `eval_fg/`。**2026-06-22 阶段化重设计**:`agent.run::on_phase_end` 在某阶段最后一步落定时,于**当时所处页面**对该阶段 `expected` 跑一次 `_check_llm_judge`(实时 URL 作免费锚点喂模型),逐阶段裁决;**取代**旧的「步骤门控 + 终态 verify_all」三处验证。结果按 `phase_index`+`expected` 落库,前端按阶段展示。〔删旧 `_gate_step_done` 偏-PASS 门控、`step.expect` 结构化锚点、终态用例级 assertions。〕
- `harness/healing.py` — **Healing Subagent**(独立 context)。断言侧:重定位断言目标;操作侧:工具报错时重定位并把建议回灌 ReAct。P1 角色→P5 视觉,防臆造(候选必须落在快照里)。
- `harness/context.py` — **Context Compact**。发 LLM 前压缩:旧观察折叠成一行(L1)、近期快照按关键词相关度截断(L2),治 token 膨胀。
- `harness/recorder.py` — 汇总 `ExecutionRecord`;`to_history()` 把 model_output / action_result 分离序列化。
- `harness/hooks.py` — 生命周期 Hook(before_case 失败→用例 FAIL 不进 Agent)+ 共享 `ExecutionContext`。
- `harness/session.py` — 仅留 `make_mcp_credential_login`(账号+密码表单登录回调,供主动扫描登录用)。〔**2026-06-18 会话/Cookie 复用退役**:原 `SessionManager`/`LoginHook`/`CaptureSessionHook`/`make_mcp_cookie_*` + `SessionProfile` 整体删除——Cookie 复用对 SPA/Token 型登录不对症、TTL 与真实会话寿命脱节;登录态跨用例复用改由后续「环境管理」主线维护。`harness/hook_builder.py` 随之删除,`Suite.session_profile` 及 `session_profile` 表经 alembic `0002` 降除。〕
- 〔`harness/precondition.py` 预置条件三分类器 **2026-06-22 删除**:阶段化重设计后预置条件退化为纯背景 `list[str]`(不执行、不 guard、不分类),原 state_hook/action_step/ambiguous 三分类 + 标黄确认端点一并退役。〕
- `harness/skills.py` — Skill 体系(**2026-06-15 对齐 Anthropic/Claude Code 标准 Skill**;**E3 2026-06-23 三层加载**):单一 `Skill`(name+description+content),**渐进披露**——System Prompt 常驻 `name—description` 清单,LLM 判断相关时**主动调 `load_skill(name)` 工具**展开正文(主路,由 E1 的 BASE_PROMPT 引导)。`DEFAULT_SKILLS` 内置基线 `preload=True`(表单操作/结果定位 + E3 加的「重新快照拿新 ref」「找不到元素的常见原因」机械套路);项目 Skill `preload=False` 走真渐进加载(E3 停 `api/run_executor.py` 的 `preload=True` force-preload TODO)。E3 加 `SkillManager.relevant(step_text, top_k)` 按 token 重叠挑相关 skill(确定性、无 LLM)+ `auto_load(step_text)` top1 直接加载;ReActLoop 在卡住时按 `stuck_round_budget` 触发**甲层**(浮现命中 skill 名催加载)、`*2` 触发**乙层**(平台 auto_load 注入)。〔删旧 DomainSkill/PageSkill/ToolSkill 三类与 URL/关键词平台侧匹配——加载与否改由 LLM 决策 + 卡住时平台兜底。〕
- `harness/permission.py` — 高危词 + prod 环境锁;Reason 后 Act 前拦截;trust_mode / 可注入 approver;无 approver 默认拒绝。
- `harness/orchestrator.py` — Suite 调度:`parallelism` **可配并发**(`asyncio.Semaphore` + `gather`,默认 1=串行;>1 需 `agent_factory` 让每用例自带独立 MCP/浏览器)、用例间隔离(异常→FAIL 不拖垮他人)、suite 级 hooks、结果汇总。
- `harness/tools.py` — Custom Tool 注册:`@tool` 装饰器 + YAML `command`;LLM 按需调用;Agent 路由(控制→StepPlan / 自定义→Registry / 其余→MCP)。

### 工程化界面(阶段四)

- `api/server.py` — FastAPI 应用入口,挂载路由子模块(suites/execution/permission/results/vocabulary/projects)+ SSE 推送 + lifespan 注入 AuthProvider。
- `api/repository.py` — **Repository 抽象层**。`SuiteRepo` / `RunRepo` / `VocabRepo` 三个抽象基类 + SQLModel 实现(`SQLModelSuiteRepo` 等),业务代码面向抽象、存储可替换。
- `api/routers/suites.py` — Suite CRUD(创建/列表/详情/删除)。
- `api/routers/execution.py` — 执行控制:**SSE 实时推送**执行进度;执行**搬离 API 事件循环**(见 `api/execution_worker.py`:每 run 一守护线程 + 独立 loop + 独立 Store),按 Suite 设置的 `parallelism` 跑 Orchestrator。
- `api/execution_worker.py` — **执行线程隔离工具**:`spawn_run`(每 run 一线程跑自己的 loop)、`make_sse_bridge`(worker→API 经 `call_soon_threadsafe` 桥接 SSE)、权限走 `threading.Event`(跨线程审批)。根治「执行期所有 HTTP 接口 pending」(单事件循环被执行的同步活儿占住)。
- `api/routers/permission.py` — 权限审批(approve/deny)。
- `api/routers/results.py` — 执行结果查询(用例列表/断言详情/代码查看)。
- `api/routers/vocabulary.py` — 词汇表 CRUD + scan 触发。

### 平台化(M1,多租户,见 `平台化设计草案.md`/`平台化开发路径.md`)

- `storage/db.py` — 连接串走 `DATABASE_URL`(SQLite 缺省 / Postgres 平台);方言分支(WAL 仅 SQLite)。多租户表 Project/Version/User/ProjectMember/ProjectLLMConfig + run_queue/run_event/permission_request。`alembic`(`migrations/`)管 schema(`alembic upgrade head`);Store.init 的 create_all+轻量迁移作单机/测试 fallback。
- `storage/crypto.py` — Fernet 字段加密(`PLATFORM_SECRET_KEY`);LLM api_key 等密文落库。
- `storage/artifacts.py` — `ArtifactStore` 抽象(本地实现;M3 换对象存储),截图/代码按 run/case 分桶。
- `api/auth.py` — `AuthProvider`(header 透传,IDaaS 可换)+ 三角色 RBAC 依赖;**未配 provider=单机模式(隐式平台管理员,全开放)**,平台部署 `set_auth_provider` 走真实 RBAC。
- `api/routers/projects.py` — 项目/成员/版本 CRUD + 项目级 LLM 配置(掩码/自检)。
- `api/run_executor.py` — **与进程无关的共享执行核** `execute_run`(API 单机线程 + worker 进程都复用)。
- `scripts/worker.py` — **执行 worker 进程**(`RUN_MODE=queue` 时 API 入队,worker `claim_next_run` 领取执行;多开横向扩);进度落 run_event 表、审批走 permission_request 工单。
- 执行两形态:**embedded**(默认,进程内线程,SSE 实时,单机)/ **queue**(`RUN_MODE=queue`,双进程,SSE 尾随 run_event 表)。
- `frontend/` — React + Vite + Tailwind 前端控制台(测试任务管理、执行、结果详情、词汇表)。
  - 平台化:`lib/session.ts`(项目从 `?project=<id>` URL 锁定只读 + 版本在平台内选择,落 localStorage;身份 `authHeaders` 留壳给 M4 IDaaS,本期不传)+ `ProjectSettingsPage`(LLM 配置)。
  - **集成入口模型**:内网系统维护项目/版本,登录后选项目跳转本平台(带 `?project=`);本平台**只展示不建管**项目与版本。本地联调用 `scripts/seed_demo.py` 种 saucedemo 为 demo 项目/版本/任务,`?project=demo` 走真实后端端到端可点通(无前端 mock)。
  - **Design Tokens** (`tailwind.config.js`): `brand` (cyan 系, 50–950)、`surface` (slate 系, 50–950)、`shadow-card` / `shadow-elevated`。
  - **UI Skills 已安装**: `frontend-design`(anthropics)、`ui-ux-pro-max` + 6 CKM skills(nextlevelbuilder)。通过 `npx skills add` 安装，各环境自行拉取。

数据结构全部在 `input/models.py`(pydantic;落库 SQLModel 留到 T-21)。

## 关键决策(已定,勿反复纠结)

- Python **3.11** + `uv`(规格用 `str | None` 等 3.10+ 语法;本机默认 3.9 不可用)。
- 本地包名 **`mcp_client`** 而非 `mcp`,避让官方 `mcp` SDK 顶层包名冲突。
- ReAct 用**文本式观察回灌**,不依赖严格 tool_call_id 配对(本地 Qwen 支持不稳)。
- `ExecutionRecord.case_assertions` / `spec` 是**有意新增**字段(规格模型没列):前者承载可信 PASS/FAIL 依据,后者存档 LLM 翻译产物供前端可视化 + 发现翻译偏差。
- 断言归属**按页面/时机分三层 + 步骤级完成门控(2026-06-15 门控化重订,治内网"按步预期"用例)**:**每步完成判据 → `SpecStep.expect_text`**(自然语言,必填;翻译引导按步分发,预期 N ↔ 步 N),执行到该步落定时在**当前子页面**由 **LLM 看快照判达成**(驱动门控,不依赖定位器对齐;未达成退回重做,经 `llm_judge` 标 `ai_judged` 低置信计入裁决);**步骤级结构化预期 → `SpecStep.expect`**(可选,门控通过后确定性验,高置信);**整体/最终预期 → 用例级 `assertions`**,终态验。三者合并裁决。`agent.collect_assertions` 仍聚合 `step.expect`+用例级(供 codegen 的 Then 段;`expect_text` 是 LLM 判据不入 codegen)。〔**为何引入 LLM 门控**(用户 2026-06-15 拍板):每步完成判断"交给 LLM 看快照更可靠"——绕开"业务词名对不上英文页面元素→结构化探针 false-fail"的定位脆弱;但守住可见性(`ai_judged` 标记、偏 FAIL、可统计 false-green 占比),不让 LLM 不可见地刷绿(铁律2(a)驱动 + (b)可审计裁决的统一)。**推翻**早先"步骤级只用结构化 expect"——结构化作高置信补充而非唯一判据。〕
- **定位三层**:`Locator` 模型(框架无关)/ 解析层(语义 target→Locator,放 generator 外)/ 渲染层(各 CodeGenerator 自实现);稳健度 `ROLE>TEST_ID>LABEL>PLACEHOLDER>TEXT>CSS`。BDD 只是渲染实现之一。
- 截图/代码生成在 `agent.run` 内**端到端接通**:浏览器动作后落 `step_NNN.png`(真实 run_id 目录),断言通过后生成 BDD 代码写 `record.generated_code`+落盘。

## 平台化(进行中的新主线,2026-06-10 启动)

单机版 → 多租户 Web 平台(全公司多产品线)。方向与已拍板决策见 `平台化设计草案.md`;
**任务分解与进度跟踪见 `平台化开发路径.md`(用户两地开发、多 agent 接力:开工前必读、收尾必更新该文档并提交)**。
要点:Postgres、API/worker 双进程(本地优先,K8s 延后到 M3)、项目→版本→Suite→Run、
三角色 RBAC、HTTP 型 Custom Tool、项目级 LLM 配置(加密落库)。单机 CLI 路径保留不回归。

## 实施进度

### 执行线 7 阶段走查(主线,2026-06-22 起)

`agent.run()` 按 7 阶段拆解,逐阶段走查→暴露设计问题→必要时 redesign。状态:

| # | 阶段(`agent.run` 子段) | 状态 | 落地 |
|---|---|---|---|
| ① | before_case Hooks | ✅ 走查完(2026-06-18) | cookie/session 退役、Hook 回归通用扩展点 `3b49864` |
| ② | spec 翻译 → TestSpec | ✅ 走查 + redesign(2026-06-22) | 阶段化 FP0-3 `a9df22e`→`38483fb`(翻译只产意图、不接地) |
| ③ | executing — ReAct 主循环 | ✅ 走查 + redesign(2026-06-23) | 执行健壮化 E1-7 `a7c9ba3`→`1831508`(驱动/裁决两层分离) |
| ④ | ~~asserting — 阶段裁决汇总~~ | ✅ 走查结论=**取消**(2026-06-23) | F1+F2 `dccc95b`→`2e6c023`:阶段化后真正裁决全在③ on_phase_end,④ 只剩空 SSE 事件 + 隐式 schema → 撤独立阶段、职责并入 ③/⑤ |
| ⑤ | 合并裁决 + 执行完整性闸门 | ✅ 走查 + redesign(2026-06-23) | G1+G2 `f0bc53c`→`9f7e450`:消灭 SKIPPED 计入 FAIL(LLM 是主裁决,缺失不默认绿)+ 缺席阶段 FAIL 占位 + 翻译退化归因 |
| ⑥ | 收尾 Hooks(on_heal/on_failure/after_case) | ✅ 走查 + 清理(2026-06-24) | H1+H2+H3 `3d03099`:清 T9 healed_assertions 死字段 + 统一 heal_count 口径 + 诚实标注"休眠的通用扩展点"(生产 hooks=None,默认不触发) |
| ⑦ | codegen → scanning(产物) | ✅ 走查结论=**设计健全、不 redesign**(2026-06-24) | 框架无关 Locator 层 + 稳健度分档 + 执行捕获优先,设计无问题;产物当前=可读骨架 + 定位器提示,"轨迹驱动 codegen"(渲染真实动作动词)作为**已知完成度缺口**留独立功能任务(非设计修复) |

**✅ 执行线 7 阶段走查收官(2026-06-24)**:①②③⑤⑥ 已 redesign/清理落地,④ 走查结论=取消、
⑦ 走查结论=设计健全不动。走查使命(逐阶段暴露设计问题→必要时 redesign)已完成。后续转入
**功能补全 / 真实环境验证**主线(轨迹驱动 codegen、阶段失败 replan、内网 live 等,见各阶段"下一步候选")。

走查范式:**读真实代码 → 设计张力清单 → 用户拍设计方向 → 必要时 redesign(`F<n>` 命名,按
功能点拆分单独 commit/push,每点单测 + saucedemo live 冒烟)**。

### 重大 redesign 实施记录

- **阶段⑦ codegen → scanning 走查(走查结论=设计健全、不 redesign,2026-06-24)** — ⑦ =
  执行链末端产物(passed 时 BDDGenerator 生成 pytest-bdd 三件套 + 默认关的执行后增量扫描)。
  走查结论:**codegen 设计健全**(框架无关 `Locator` 层 + 稳健度分档 ROLE>TEST_ID>...>CSS +
  执行捕获优先于词汇表),**无需 redesign**;缺的是**完成度**——产物当前 = 可读骨架 + 定位器提示,
  离"可回放"差一层动作渲染。澄清:`step_target = cur_step.text`(StepPlan 摊平的 phase 步骤句)
  与 `_flatten_steps(spec)` 同源,BDD 的 `locators.get(s)` key **对得上**(非漏接)。
  - **暴露的张力(均记为独立功能任务,非设计修复,本轮不做)**:
    - **T1 轨迹驱动 codegen(核心缺口)**:When 步骤体只渲染定位器表达式 + "请人工补 .click()/
      .fill()"注释,**不含真实动作动词**。执行轨迹完整有 `tool_name`(browser_click/type)+ value,
      可精确渲染成 `.fill("standard_user")` / `.click()`。这是注释里"轨迹驱动 codegen 列后续"的核心。
    - **T2 多动作 phase 步骤只捕首个定位器**:`locators_from_steps` 同 target 取首个 + BDD 按
      phase 步骤去重渲染一个 When;"输入用户名+密码+点登录"合并步只留第一个动作。轨迹驱动应按
      **action 序列**展开,而非按 phase 步骤去重。
    - **T3 Then 全 TODO**:NL expected 无法确定性断言。〔**2026-06-24 更新**:此处原写"E5
      `_expected_anchors` 已能抽强锚点 → 部分缓解",但 E5/`_expected_anchors` **已随证据接地层
      撤销删除**;若要做这条 codegen 缓解(从 expected 抽 URL/引号强锚点 → 生成 `to_have_url`
      等真断言),需**自行重写一个轻量锚点抽取**,别引用已删的 `_expected_anchors`。〕
    - **T7 词汇表来源定位器是孤儿**:`resolve_locators`/`locator_from_vocab` 在 agent.run codegen
      路径**从未被调用**(只 `locators_from_steps` 执行捕获);`locators.py` 注释自称三级优先
      "执行捕获>词汇表>文本兜底",实际只接第一级。接上词汇表层或改诚实注释,待办。
  - **不动代码**(用户选"接受骨架,关闭走查"):走查使命=暴露设计问题,codegen 不是设计有问题、
    是没做完。T1+T2(轨迹驱动)是有实质价值的功能补全,留作独立任务专门做。

- **阶段⑥ 收尾 Hooks 走查 + 清理(2026-06-24,已落地 H1+H2+H3)** — ⑥ 段 = 用例收尾
  三事件(on_heal/on_failure/after_case)。走查发现**整块在生产路径休眠**:
  `run_executor` 传 `hooks=None`、CLI 亦不预填(2026-06-18 Cookie/Session 退役后 Hook
  回归纯通用扩展点),真实执行中本段从不触发,只有测试构造 HookManager 才跑。结论=**不
  投机加料(不丰富 ctx/不调时序——无消费方),只清死代码 + 诚实标注**。
  - **H1 清 T9 healed_assertions 死字段**(`3d03099`):阶段裁决走 `_check_llm_judge` 直连、
    **不过 `verify()` 的 healable 装饰** → `AssertionResult.healed` 恒 False → `healed_assertions`
    恒空。删 ⑤ 段恒 +0 的 `record.heal_count += sum(if r.healed)`、删 ⑥ `healed_assertions`
    聚合 + `ctx.set("healed_assertions")`、`_build_metrics` 去掉恒 0 的 `assertion_heals`
    (healing 只剩 `{action}`)、前端 metrics.healing 去 assertion 字段(展示改"自愈 N 次")。
    `on_heal` 明确=**操作侧自愈**触发;测试从 monkeypatch `_check_llm_judge`(造不可能发生的
    断言侧自愈)改为 wrap `ReActLoop.run` 注入真实操作侧 heal_attempt。
  - **H2 统一 heal_count 口径**:`record.heal_count` 单一来源(操作侧,`recorder.add_step`
    每步累加);⑥ ctx 不再重算,直接读 `recorder.record.heal_count`(此前三处口径:add_step
    累加 / ⑤ 恒 +0 / ⑥ 重算 len+len,有重复累加风险)。
  - **H3 文档诚实化**:⑥ 收尾段 + `hooks.py` docstring 标注"休眠的通用扩展点,默认无 hook,
    需调用方装配";去掉"规格 §7.7 自愈可观测"等像已接功能的措辞。
  - 走查报告里 T19(ctx 信息贫乏)/ T20(时序在 finalize/codegen 之前)= **暂缓**:在没有
    真实 hook 消费方之前丰富 ctx / 调时序是投机未来设计(违反"不过度设计未来阶段"),待真有
    "失败发通知 / 产物上传"类消费场景再动。
  - **验证**:pytest 491 passed(2 个预存在 Windows 失败不变);前端 build 绿。纯清理(不碰
    驱动/裁决路径,hooks 生产中休眠,verdict 路径与 G2 冒烟字节一致)→ 按 F1/F2 先例不单独冒烟。

- **阶段⑤ 合并裁决 + 执行完整性闸门 redesign(2026-06-23,已落地 G1+G2)** ★ — ⑤ 段把
  「逐阶段 LLM 裁决」与「执行完整性」合成最终 `passed`。走查暴露:**SKIPPED 是旧设计化石**
  ——服务于"LLM 不可信、需结构化兜底、skipped 等人工复核"的假设,但**阶段化下 LLM judge 是
  主裁决,自动化平台无人工复核环节**,SKIPPED 实际 = 被忽略 = **false-green 漏洞**(LLM 罢工 →
  整批集体绿,因为旧公式 `validated==n_phases and not phase_fail` 把全 SKIPPED 算可信通过)。
  - **G1 消灭 SKIPPED 计入 FAIL**(`f0bc53c`):`_check_llm_judge` 主裁决缺失三态(未接 LLM /
    调用失败 / 解析不出 verdict)一律 FAIL(保留 `ai_judged` 标记);`on_phase_end` 无 expected
    分支 SKIPPED→FAIL + 返回原因触发 PHASE_FAILED(暴露翻译退化:本该产组级预期却空);
    `AssertionEngine.verdict()` 加化石注释(阶段化下不再被 agent.run 调用);⑤ 闸门公式简化为
    `passed = execution_complete and n_phases>0 and not phase_fail`(去掉 `validated==n_phases`
    守门——执行完整即蕴含每阶段末步都触发过 on_phase_end,故全被裁决过)。**custom_tool/未知
    类型未接入时仍 skipped**(非阶段化路径,不在收 FAIL 范围)。
  - **G2 缺席阶段 FAIL 占位 + 翻译退化归因**(`9f7e450`):早停(STEP_FAILED/max_steps/卡死)时
    `phase_results` 短于 `n_phases` → 落库 `case_assertions` 断层(前端时间线"阶段 1 ✅/阶段 2 ✅/
    阶段 3-5 不见/整体 ❌"用户困惑)。给未触达阶段补 FAIL 占位(`reason="该阶段未触达,执行已
    早停"`,phase_index 保留,按升序);**占位不进 `validated_phases`**,`phase_fail` 只看真实裁决过
    的阶段(`pi in validated_phases`),否则占位会污染 phase_fail → 失败归因误报"阶段预期未达成";
    失败归因加 `n_phases==0` 档"翻译退化为空 phases"(替代荒谬的"0/0 未全过")。
  - **走查报告其他张力归位**:T5 ai_judged 置信分级 = 裁判侧(`_check_llm_judge` 透出 confidence),
    独立专题留后;T9 healed_assertions 死字段归 ⑥;T14 执行未完成伪断言(G3)暂缓到前端走查统一处理。
  - **验证**:pytest 491 passed(+2 = G2 新测,改 ~6 条 SKIPPED 测;2 个预存在 Windows 失败
    不变)。**saucedemo TC101 live ✅ PASS**(2 阶段证据接地,15 步 0 自愈,裁决=阶段1 inventory.html
    URL 锚点 + 阶段2 购物车角标=1)。**AE03 脏公网两次复跑 FAIL**——失败阶段/原因每次不同
    (阶段3/阶段4),均为 LLM 裁判对**翻译产出的严格 expected**("按钮变 Remove / 角标=1")的
    偏-FAIL(automationexercise 实际只弹 Added! 模态、不改按钮态/不显角标);裁判给**明确 FAIL
    verdict**(非 SKIPPED → G1 路径未触发;步骤跑完 → G2 路径未触发),与 G1/G2 正交,属 AE03
    既有脏站 flaky + 翻译质量问题(根因正交于 ⑤,记入下一步候选)。
  - **重要**:**无 LLM 的开发环境下阶段裁决整批 FAIL**——这是正确的(LLM 是主裁决,没 LLM 用
    什么判?);本地冒烟/单测用 fake/mock LLM,真实跑必须配 `.env`(LLM_MODEL/API_BASE/KEY)。
  - **下一步候选(未做)**:① T5 ai_judged 置信分级(裁判侧专题);② AE03 翻译质量——脏站点
    expected 严格度与真实站点行为对齐(翻译引导/锚点放宽,属 ② 翻译线,非 ⑤);③ G3 执行未完成
    伪断言进 case_assertions(前端走查统一)。

- **阶段④ asserting 撤销(走查结论=取消,2026-06-23,已落地 F1+F2)** — 阶段化重设计
  (FP0-3)后,真正的裁决全在 ③ ReAct 内 `on_phase_end` 即时完成,阶段④ 在 `agent.run`
  里只剩**汇总记账 + verdict 计算**——`phase_tokens["asserting"]` 恒 0、SSE 推一个
  空阶段让前端等。走查结论:**④ 不再作为独立阶段**,职责并入 ③(裁决)+ ⑤(汇总闸门)
  + ⑥(Hooks)。
  - **F1 撤 SSE 事件 + token mark**(`dccc95b`):删 `emit_phase("asserting")` +
    `_mark_phase("asserting")`;前端 `PHASE_LABEL` 删 asserting;tests 同步。Validator
    token 在 ③ 内自然计入 executing,真实反映消耗位置。
  - **F2 case_assertions schema 对齐**(`2e6c023`):`AssertionResult` 加 `phase_index:
    int = -1` 一等字段(默认 -1=非阶段裁决);`to_dict` 自然带出。agent.run 不再外塞
    dict / 不再覆盖 expected(消除"双 expected 同名覆盖"风险——以前 to_dict 的
    `Assertion.expected` 与外塞的 `spec.phases[pi].expected` 同名,改任一处就裂)。
    `ExecutionRecord.case_assertions` 注释从"用例级最终断言"→"阶段化裁决证据"。
  - 走查报告里其他张力(T3/T5/T7/T8 等)归位到 ⑤ 走查解决:T3 全 SKIPPED 算可信通过
    护栏、T5 ai_judged 分级、T7 缺席阶段占位、T8 `validated_phases` 含 FAIL 命名;
    T9 healed_assertions 死字段归 ⑥;T10 调内部方法风格归 ③。
  - **验证**:pytest 489 passed(+1 = F2 新测;2 个预存在 Windows 失败不变);
    前端 `npm run build` 绿。F1+F2 都是纯收尾清理(不动裁决路径、不动执行链路),
    live 冒烟随 ⑤ 走查动到的代码一起做(本两条不单独冒烟)。

- **撤销「证据接地推翻」+ eval 扩样回归基准(2026-06-24,用户拍板①,已落地)** ★ — 据 A/B
  扩样实测,把 `_check_llm_judge` 的**确定性证据接地推翻层**整层删除,**裁决权交回模型**
  (用户核心诉求:把权力交给模型)。结论由数据驱动,非拍脑袋:
  - **方法**:新增 `eval_fg/ab_grounding.py`——同一次 LLM 调用**无损还原**「开核验 vs 关核验」
    两配置(接地推翻是确定性的、只把 PASS→FAIL,故模型原判可由 reason 标记反推);
    `eval_fg/capture_more.py` 增量抓 the-internet + demoblaze 公开页**合并**进 snapshots.json
    (不动已标注的 automationexercise 快照),`judge_eval.EVAL` 从 26 扩到 **63 条 / 3 站点**。
  - **数据**(deepseek-v4-flash ≈ 内网模型,n=63,6 轮共 189 次裁决):**偏-FAIL 的
    `_JUDGE_SYSTEM` 自身 false-green=0/34**(跨 3 站点 0 漏绿);接地层**「有益拦截」恒为 0**
    (它要防的脑补刷绿一次都没发生、它一次都没拦);唯一可测作用是**偶发误伤**(把真 PASS
    推成 FAIL),且误伤**全落在 expected 无强锚点的「疑似脑补」分支**(无 ground truth、纯跟
    模型对赌)→ **净贡献 ≤0**。用户实证的 false-FAIL(登录已成功却因 evidence='' 被推翻)正是
    此分支。置信上界从 ~20%(n=15)收紧到 ~9%(n=34)。
  - **落地**:删 `_check_llm_judge` 的证据接地块 + 全部 helper(`_norm_evidence`/`_evidence_*`/
    `_expected_*` + 锚点正则 + `import re`);`_JUDGE_SYSTEM` 仍**要求模型逐字引证 evidence**
    (偏-FAIL 纪律 + reason 可审计),但**evidence 仅作依据写入 reason、不再作推翻闸门**;
    去掉 prompt 里「平台会确定性核验」的承诺。**保留层(1)解析卫生**(`extract_verdict` 正则
    捞 verdict / 无 verdict→FAIL,fail-closed)+ G1 主裁决缺失三态→FAIL。tests 删 9 条接地/E5
    锚点测试、改 broken-json 测试为「正则捞回 PASS 即 PASS」。
  - **验证**:pytest 486 passed(2 预存在 Windows 失败不变);**saucedemo TC101 live ✅ PASS**
    (两阶段裁决均来自模型 `AI 判定通过`,evidence 作 `依据:` 回写,无推翻)。`eval_fg/` 留作
    **回归基准**:`judge_eval.py` 测纯模型裁判 false-green/false-fail,`ab_grounding.py` 锁
    「若有人重新引入接地层,其净效果应 ≤0」。
  - **残余风险(诚实)**:n=63 单模型,「接地层无用」是**方向成立**(置信上界 ~9%)、非保证;
    有益拦截=0 的前提是该模型偏-FAIL 够好——换**更弱**模型(真会脑补 green)时接地层可能挣到
    价值。换言之这是拿 deepseek-v4-flash 这个水位**信模型**;部署更菜模型需重判。下一步候选:
    再扩样 + 第二模型(需另配凭据)进一步收紧。

- **TC201 结算流哑火卡死调查 → 三连修(2026-06-24,已落地)** — 前端跑「完整下单结算流程」
  (saucedemo TC201,11 步)**偶现** FAIL:停因 `llm_finished`、仅完成 2~4/11 步、哑火续推撞上限。
  排查链(**观测先行**):
  - **先补可观测(`367996b`)**:哑火轮的模型原文从不持久化(embedded SSE 走桥不落表、哑火轮
    不产生 step 记录)→「模型放弃 vs 平台丢调用」只能猜。`ReActResult.idle_outputs` 记每轮
    `{iteration,step_no,kind,rechecked,text}`,`kind` 三态分类,透 metrics 落库 + CLI 打印。
  - **据 idle_outputs 定性**:复现 run 26/26 哑火轮 = `narration_only` + `rechecked=True`(非流式
    复核也无 tool_call)→ **排除流式丢采**;根因 = **deepseek-v4-flash 偶发把工具调用吐进 content
    文本而非 tool_calls 通道**(实测 iter=27:`browser_click({"ref":"e54",...})` 写成了文字)。
  - **真修(`da9a58b`)**:`extract_tool_calls_from_content` 补 `_FUNC_CALL_RE`——标准/围栏/裸 JSON
    都没捞到时,按 `函数名({...})` 形态提取并合成 tool_call(救回"写成文字的真调用",治这类哑火)。
  - **顺带两修**:① `_normalize_ref_target`(`d27c94e`)——browser_* dispatch 前把 `ref`/
    `element_ref` 补进 `target`(本版 playwright-mcp 用 `target` 装 ref,模型先验 `ref` 会抖动,
    实测步14 白烧一步);② judge prompt 加简短输出约束(`2a45fac`)——judge 偶因 provider 默认
    输出上限截断成坏 JSON(我们**未设 max_tokens**),压短 evidence/reason 减少触发层1 兜底。
  - **未修(残余)**:`narration_only` 里还有**纯叙述**型(「第2步已达成,立即标记完成」反复说却不
    发任何调用,无可解析调用)——funcname salvage 救不了;若仍频发,候选 = 调大 `max_idle_nudges`
    让聒噪模型啰嗦着跑完 / 在 mark 类步骤加更强「现在就调用」约束 / 换更强模型。**结论:TC201
    偶现卡死是弱模型 function-calling 不稳,非平台 bug;observability 已能事后定性,持续观察。**

- **执行健壮化(stage③ ReAct 主循环 redesign,2026-06-23,已落地 E1-7)** ★ — 在阶段化
  重设计(FP0-3)之上,把③ executing 阶段从「按步打勾」升级成「像 Claude 一样盯目标、
  失败诊断换法」。两层分离更彻底:**驱动层(role a,鼓励·软·可恢复)** + **裁决层
  (role b,可审计·硬·fail-closed)**。
  - **E1 驱动 prompt 契约重构**(`a7c9ba3`):BASE_PROMPT 重写——步骤=要达成的目标(达成
    标志=页面出现你预期的变化)+ 先验后进(mark 前必看观察)+ **动手前主动 load_skill**;
    废 `TEST_RESULT` 输出教学(裁决全由 phase Validator);`step_plan.to_prompt` 加当前
    phase 高亮(N/M),仍只渲染步骤、不渲染 expected(FG01)。
  - **E2 页面指纹软护栏 + 跨 phase 重置 + 预算放宽**(`1fceb7d`):轻量页面指纹 `URL+ref 集`
    (无 LLM)→ 过早 mark 软护栏从「没操作」扩到「**操作无效果**」(`step_pre_op_fp` +
    `step_changed_fp`);步级卡住主动提醒(连续 N 轮 round-end fp 无变化,默认 2 轮);
    单步失败预算 3→5;进入新 phase 清零 idle/loop 计数。
  - **E4 裁决判前 settle + 恰好一次**(`cfd731d`):`on_phase_end` 在 `probe.refresh()`
    前调 `settle_page`(治 mark_step_done 不触发 settle 致 Validator 抓到过渡态);
    `validated_phases` 去重,同 phase 即便末步被重复 mark 只裁决一次。
  - **E3 Skill 三层加载 + 基线机械下沉**(`29c5f1e`,**最大风险点,解掉旧 force-preload TODO**)
    :主路靠 E1 prompt(模型主动 load_skill);**甲层** = ReAct 卡住时按
    `SkillManager.relevant(step_text)` token 重叠度浮现命中 skill 名催加载;**乙层** =
    甲层已发但仍卡住(stuck≥budget*2)→ `auto_load(top1)` 平台直接注入兜底。`DEFAULT_SKILLS`
    新增「重新快照拿新 ref」「找不到元素的常见原因」两条机械套路(preload=True);项目 Skill
    退回 `preload=False` 走真渐进披露(`api/run_executor.py` 停 force-preload)。
  - **E5 确定性锚点佐证**(`d658c70`):`_check_llm_judge` 在 evidence 接地核验通过后,再
    从 `expected` 抽**强锚点**(只取**引号字面值 + URL-like 片段**——`.html`/`.aspx` 等
    Web 后缀或 `/a/b` 路径段)——刻意保守不取一般 CJK/ASCII 词,避免文风误伤。强锚点全不在
    页面/URL → judge 与 expected 矛盾,fail-closed 推翻 FAIL。
  - **E6 多模态裁判通道**(`4e81ded`,**开关默认关**):env `JUDGE_VISUAL=1` 开启;开启后
    judge 抓 `probe.raw_screenshot` 作 user content 第二段(`image_url` 块,OpenAI/LiteLLM
    vision 格式)。失败 → 标记 `_vision_unsupported`,本 engine 后续不再尝试图像。治
    a11y 看不全的角标/图标/canvas;本地弱模型多模态不稳故默认关。
  - **验证**:pytest 488 passed(2 个预存在 Windows 失败不变)。每个 E 单独 commit + push,
    每点 saucedemo TC101 live 冒烟绿。**E7 全栈端到端**:
    `saucedemo TC101` ✅(12 步,2 阶段证据接地);`AE03(脏公网 automationexercise)`
    ✅(29 步,2 次自愈,3 阶段证据接地)。
  - `eval_fg(deepseek×26)` 验 E5:false-green 0/15、false-fail 1/11(与 A-2 历史一致);
    E5 在数据集上推翻 0 次(纯中文 expected 无引号无 URL → 严格设计精准跳过,**零误伤**)。
  - **下一步候选(未做,本轮不在范围内)**:阶段失败的 replan、运行时锚点自动捕获(URL/数据)、
    codegen 轨迹化、E6 在真实多模态模型下做 live A/B、E5 在更多真实公网用例上扩样验证。

- **翻译/执行/裁决阶段化重设计(2026-06-22,已落地 FP0-3)** ★ — 推翻旧「盲接地翻译 + 步骤门控 + 终态裁决」,改为**阶段化(phase)**。根因:旧翻译在**翻译期接地**(盲猜 selector/动作/元素),复杂用例翻译时根本不知道页面长什么样 → 五大病(盲断言、步↔预期对齐脆、expect_text 盲编、降级断崖、词表空转)。新设计:**翻译只产意图,接地全交运行时**。
  - **数据契约**(`docs/test_spec_v2.md`,无兼容):`TestSpec = {intent, preconditions:[str], phases:[{steps:[str], expected}]}`。阶段 = 一组自然语言步骤(**驱动**)+ 一条组级预期(**验证依据**)。`steps` 数据内联、不写 selector;`expected` **只在阶段边界给 Validator 核验,绝不进 agent 驱动**(FG01:错预期不会把 agent 带去追错目标)。前置纯背景(不执行/不 guard,具体状态保证交未来「环境管理」)。
  - **执行 + 裁决**(取代铁律 2(b) 的「终态裁判为主」结论):**逐阶段 Validator**——某阶段最后一步落定时,在**当时所处页面**用偏-FAIL 的 `_check_llm_judge` 核验该阶段 expected;PASS 进下一阶段,FAIL → **阶段失败即失败**(不 replan/重试)。**取消独立终态裁决**(最后阶段的核验即终态检查)。verdict = 全阶段通过 + 执行完整。〔**注:本条原文写「偏-FAIL + 证据接地」,其中"证据接地"层已于 2026-06-24 撤销(eval 实测净 ≤0,见上「撤销证据接地推翻」),现底线 = 偏-FAIL + 解析卫生 fail-closed + 不可见刷绿;evidence 仅作可审计依据。〕
  - **业界印证**(调研):Skyvern 2.0 Planner-Actor-**Validator**(逐子目标验证、replan,WebVoyager 45%→85.85%)+ 30 年手工 QA(分组步骤 + 组级预期结果)+ BDD,三者独立收敛到「验证粒度 = 子目标/阶段」。browser-use 对比评估见 `browser-use_对比评估.md`(结论:不替换执行层)。
  - **删除**(无兼容,净 −1900 行):`harness/precondition.py` 分类器、`SpecStep`、`_gate_step_done` 步骤门控、`step.expect`/`expect_text`、终态 `verify_all`、`enhance_targets`、`collect_assertions`/`ensure_navigation_step`、预置条件标黄确认端点。codegen 最小适配(phases→steps + 执行轨迹定位器)。
  - **验证**:pytest 462 passed(2 个预存在 Windows 失败不变);前端 `npm run build` 绿;**saucedemo TC101 + AE03(脏公网站点)live 双 PASS**——各阶段 Validator 在所属页面证据接地判通过(TC101:inventory URL/商品列表 + 购物车角标=1;AE03:All Products/Searched Products/购物车 qty=1)。提交 `04b412d`(后端)/ `f02cd0e`(前端)。
  - **下一步候选**:阶段失败的 replan(本轮"阶段失败即失败")、运行时锚点自动捕获(URL/数据)、codegen 轨迹化、每阶段 system prompt 优化(任务清单已记)。
- 阶段一 ✅ T-01~T-10(主干跑通,断言驱动 PASS/FAIL;saucedemo 端到端验证过)
- 阶段二 ✅ T-11~T-19(自愈 / Context Compact / Hooks / Session+LoginHook / 预置条件分类器 / Skill / Permission / Orchestrator / Custom Tool)。四条验收标准 saucedemo 真实演示通过(见 `examples/acceptance_stage2.py`)。
- 阶段三 ✅ T-20~T-22(`codegen/`BDDGenerator / `storage/db.py` SQLModel 持久化 / `intelligence/`词汇表+Scanner)。
- 阶段四 ✅ T-23~T-27(FastAPI 后端 5 路由+SSE / Repository 抽象层 / React 前端控制台(Suite 管理、执行控制台、结果详情、词汇表)/ BDD `step_N` 标记)。
- **UI Redesign (TestSprite 风格,已落地一轮)** — 参照 TestSprite 控制台重构前端,设计语言:**森林绿**强调色(`brand` 令牌已由 cyan 改 green)、**浅色分组侧栏**、**表格**列表、**双栏抽屉**。关键结构(以 git 历史/当前代码为准,不逐项追踪):
  - 布局:`RootLayout`(项目级:只读项目顶栏 + 浅色侧栏 概览/测试任务/词汇表/设置)+ `SuiteLayout`(进入测试任务后切换为**任务内导航**:用例/执行历史/报告/设置 + 面包屑)。
  - **信息架构(M2-UI,2026-06-11 定稿)**:项目 → 版本 → 测试任务 三级。**「测试套件」全平台改名「测试任务」**(用户可见文案改,代码标识符 `Suite`/`SuiteLayout`/路由 `/suites/:id` 不动)。「测试任务」页(`TasksPage`,`/tasks`)是 **版本→任务的树**:每版本一行可展开,列出其下任务(用例数/最近执行状态/更新时间);新建任务挂在各版本行(就近建)。**版本不再是独立页面/工作区**(已删 `VersionLayout`/`VersionListPage`/`VersionReportsPage`/`SuiteListPage`)。**报告归入任务**(`SuiteReportsPage`,任务工作区 tab,与执行历史并列;当前基础版,趋势/通过率维度后续补)。`ProjectOverviewPage`(`/`)是落地概览页。
  - 用例页(`SuiteCasesPage`):状态列 + 顶部进度条 + **原地执行**(`hooks/useSuiteRun` 封装 SSE,不再跳独立控制台页);点用例 → **双栏抽屉**(`CaseDrawerBody`):左=信息/测试结果卡/步骤卡,右=测试结果(Preview 最终态截图 / 代码 / 断言结果)或单步截图。
  - 执行历史 → run 详情(`SuiteRunDetailPage`,挂 SuiteLayout 下)点用例**复用同一抽屉**(绑定该次 runId),不再三页跳转。
  - 已删死代码:`RunConsolePage`/`RunOverviewPage`/`CaseResultPage`/`CodeViewerPage`/`ProgressBar`/`FileTree`/`StepListPanel`/旧 `SuiteDetailPage`(`@monaco-editor/react` 随之不再被引用,dep 留着无害)。
  - 后端配合:`api/routers/execution.py` 执行默认 `--isolated --headless`(规避 Chrome 密码泄露弹框,env `MCP_ISOLATED`/`MCP_HEADLESS` 可关)。
  - 数据依赖:抽屉的断言/代码/截图来自 `ExecutionRecord`(需该用例有 run 记录);截图依赖运行时真落图(Windows 上 `test_recorder` 截图目录预存失败,实际未落图则回退"无截图")。
  - **前后端分离**:`api/server.py` 是**纯 API**(`:8000`),不再挂 `frontend/dist` 静态构建(已移除,避免服务旧构建造成混乱)。前端一律走 Vite dev server `:5173`(`npm run dev`)。若日后要让后端托管前端,需**显式**重新引入静态挂载并自管构建新鲜度。
- **真实环境验证加固(进行中)** — 用 DeepSeek(代 Qwen3)+ 真实浏览器跑 saucedemo TC101,暴露并修复:① `collect_assertions` 断言去重(LLM 常把同一断言既放用例级又放 step.expect);② page_probe 后缀循环剥离 + **精确优先匹配**(短目标 '1' 子串会误中长描述);③ **词汇表接入断言侧(方案A)** — `MCPPageProbe(resolver=...)` 运行时按真实 role+name 解析跨语言/图标类目标,healing 同步接通 vocab,CLI 加 `--vocab` 手动词汇表入口(见 `examples/saucedemo_vocab.json`)。
  - ④ **selector 型词汇表(已做)** — 词条/`Assertion.selector` 给 CSS 时,`MCPPageProbe.query` 走 `browser_evaluate` DOM 求值(返回 `{found,visible,count,text}`),对计数角标稳健(2 件→text='2',不像 name 型写死)。解析优先级:显式 selector > 词汇表 selector > 词汇表 role+name a11y 精确 > 原始 a11y。saucedemo vocab 已改 selector 型。
  - ⑤ **词汇表落 DB + 前端维护(已做)** — `execution.py` 构造 `VocabularyResolver(VocabularyManager(store))` 注入 agent,闭合"维护词汇表→执行真正用上"的环;`find_page` 对空 `page_title`/`login_role` 宽松匹配(运行时 role 常未知也能命中手动词条);`VocabularyResolver` 支持 selector-only 词条;前端 `VocabularyPage` 从只读改为可维护(展开看词条、增改删、selector 字段、新建/删除页面词汇表)。
  - **未决发现(均非断言层,实证确认):** (b-1) **密码泄露弹框** — Chrome 原生 UI,**不在 a11y 快照里**,自愈(只读快照)无法识别/关闭;robust 解只能靠启动参数,故 CLI 加了 `--isolated`/`--headless`。(b-2) **ReAct 早停**(已修)— 根因是 `react_loop` 在模型自报 `TEST_RESULT` 时即终止(`all_resolved() or maybe_result`),DeepSeek 登录后提前吐一句就停在中途;已改为**有未完成步骤时不采信自报结果、改哑火续推**(贯彻铁律4 到执行层)。(b-3) ~~**saucedemo 加购不生效**~~ **【已证伪,2026-06-07】** — 此前判断是 mcp/浏览器问题,实为**误判**:真实 live 跑(DeepSeek+真 Chrome)TC101 加购**完全生效**,角标 `.shopping_cart_badge=1`、按钮变 Remove,终态断言拿到 live 绿。旧"加购不生效"其实是 ReAct **卡在点登录**(见下「ReAct 卡死修复」)根本没走到加购步骤的连带误判。**b-1 仍有效**(密码泄露弹框靠 `--isolated/--headless` 规避);b-2 见下已彻底修。
- **抽屉可观测性 + 产物落地 + UI 收口(已做)** — 围绕"点开用例能看到全过程"补齐数据链:
  - **TestSpec 存档可视化** — `ExecutionRecord.spec`(+DB 列)每次执行存档 LLM 翻译产物;抽屉左栏纯导航(用例信息/测试结果/步骤),长内容(预置/预期/TestSpec/断言)移右侧宽栏滚动;断言视图聚合步骤级 expect(与 `collect_assertions` 一致)。
  - **截图捕获管线** — 此前 `ToolOutcome.screenshot` 空有字段从未落盘。补:`MCPClient.result_to_image_bytes` 取 base64;`ReActLoop.capture_screenshot` 回调每个浏览器动作后落 `step_NNN.png`;`agent.run` 接 `run_id` → Recorder 用**真实 run_id** 建目录(原 `norun` 与前端取图路径不一致);orchestrator 透传 run_id;env `MCP_SCREENSHOT=0` 可关。
  - **接入 BDD 代码生成** — `BDDGenerator` 此前是孤立模块、执行链从未调用。`agent.run` 在**断言通过后**生成并写 `record.generated_code`(随 run 持久化)+ 落盘 `storage/generated/`;`/code` 端点优先返回 per-run 的 generated_code。
  - **框架无关定位器解析层(`codegen/locators.py`)** — `LocatorStrategy`(ROLE>TEST_ID>LABEL>PLACEHOLDER>TEXT>CSS,**按稳健度**)+ `Locator` 模型 + `resolve_locators`(词汇表来源,role+name>selector>name);解析放在 generator **之外**,各 CodeGenerator 只渲染自身语法(BDD 只是一种实现)。未命中词汇表回退文本启发式 + 前置 TODO 注释。
  - **前后端边界 + 皮肤** — `api/server.py` **移除 `frontend/dist` 静态挂载**,`:8000` 纯 API、前端一律 `:5173`(根除"改了前端但 :8000 服务旧构建"的反复混乱);brand 主色改 TestSprite 沙绿 `#478d54` + 新增 `canvas` 灰底白卡背景;代码区浅色主题+行号+限高滚动+复制。
- **执行中实时反馈(已做,修执行期抽屉空白)** — 根因:`agent.run` 原把 `step_callback` 放在 `loop.run()` **返回之后**一次性补发,执行期间(占满几乎全部耗时)SSE 只有 `case_start`、抽屉拿不到任何 live 数据。修:① `ReActLoop` 加 `on_step` 回调,每步落定**即时**推送 `step_change`(去掉事后补发);② `agent.run` 发**生命周期阶段**事件 `phase`(spec 翻译 / executing / asserting / codegen),orchestrator 的 `sse_callback` 透传;③ 前端 `useSuiteRun` 收 `phase` 入 `CaseRunState.phases`,`CaseDrawerBody` 加 `RunningView`(阶段清单:末项转圈、其余打勾 + 实时步骤),`case_start` 时右栏默认切到该视图;用例在本次会话内跑完(running→passed/failed)**重新拉结果**,免得抽屉停在"执行中"。参考 TestSprite 的运行态交互。
- **词汇表全链接通 + 可观测(已做)** — 此前词汇表只服务**判定/产物**(断言探针、codegen),
  执行驱动完全没用,且规格设想的两处是孤儿代码。本轮补齐:
  - **Scanner 策略C(执行后增量补充)** — `agent.run` 结束后 `_incremental_scan`。**2026-06-15
    按用户设计意图重写**(原实现跑偏:逐页跑一遍和主动扫描一样的全页提炼,既贵又丢真值、与
    主动扫描两写入源)。**新逻辑**:职责分工——主动扫描(`/vocabulary/scan`)铺全量页面词汇,
    本模块只用**执行轨迹里跑通的真实元素**(`ActionStep.step_target/element_name/element_selector`,
    ground truth)补**这条用例触达、词汇表还缺的**业务词。流程:① 从 `action_steps` 收
    「业务词→真实元素」候选(只取有元素证据的步,同词留证据最强:有 selector>仅 name);
    ② **确定性过滤**已被词汇表覆盖且一致的词(`_already_covered`);③ 仅在有待补充候选时叫
    **一次** LLM「总结挑词」(`Scanner.summarize_supplements`:过滤通用控件/同义重复、规范化
    业务词)——**无新词则 0 次 LLM 调用**;④ `merge_scanned` 并库(手动条目优先),复用既有页
    身份对齐键(`find_page` 命中则用其 base_url/url_pattern/title,免「宽松查/精确写」两套键
    产生重复行)。复用执行期**已登录**会话、不另开浏览器/不重走全流程(这正是它相对主动扫描的
    价值)。best-effort,`find_page`/`merge` 均包错不影响用例结果。env `VOCAB_SCAN=0` 默认关。
  - **操作侧自愈查词汇表(规格 §5.4「词汇表第一优先」)** — `react_loop._heal_action` 按
    业务词 `vocab_resolver.resolve` → 真实页面名,作 P1 候选传 `healer.relocate(vocabulary=)`;
    此前操作侧自愈根本没传 vocab(只断言侧传了)。
  - **翻译期 `enhance_targets` 增强(规格 §5.2)** — `agent._enhance_spec_with_vocab` 接通
    孤儿 `enhance_targets`:按 base_url 命中的词汇表把**精确**业务词 target 改写成页面真实
    文案("提交"→"保存并提交"),仅在本次自动生成 spec 时增强(显式传入的 spec 不动)。
  - **查看 prompt(调试)** — `ActionStep.prompt` 记每轮请求(System Prompt + 最近输入,
    不存完整历史避免 DB 膨胀);`react_loop` 每步落定时挂上,经 recorder/SSE 透出,抽屉
    步骤详情加「查看 prompt」按钮(落库 + 执行中实时都能看)。**注意**:结果接口
    `api/routers/results.py::_build_history` 是**独立于** `recorder.to_history` 的另一套
    序列化,改 history 字段要**两处都改**(否则执行中能看、完成后丢)。
- **执行态可观测收口(2026-06-05,已做)** — 围绕"执行中也能看清"补的一组小修:
  - **实时推送 TestSpec** — 翻译完成即发 `spec_ready` SSE,执行中点「用例信息」就能看执行
    规格(此前 spec 只随结果落库,执行期抽屉为空)。
  - **执行中状态区分色** — running 用蓝(`blue-*`)、通过才绿,不再全程一片绿(参考 TestSprite)。
  - **步骤截图按真实落图状态** — `step_change` 带真实 `screenshot` 文件名,前端 `hasShot=!!screenshot`;
    失败/重试步、快照步本就不落图,不再一律假设有图去取不存在的 `step_NNN.png` 报 404。
  - **抽屉步骤详情收口** — 移除「执行结果」原始 mcp 观察文本(用户不关心),保留 URL + 查看 prompt。
  - **`react_loop` 执行捕获修复** — `last_snapshot_text` 仅在观察**真带 `[ref=`** 时更新,
    否则「操作→mark_step_done→操作」序列会被非快照输出覆盖,令第二步捕获漏采。
  - **LiteLLM 封装两修** — ① 未传 tools 时 `_parse` 不再对 content 做 tool_call 兜底/报错
    (正常 JSON 含 `"name"` 子串曾被误判成坏调用而抛错,连累 Scanner/SpecGen);
    ② import 前 `LITELLM_LOCAL_MODEL_COST_MAP=True`,内网免去联网拉价目表的握手超时 + warning。
- **空壳模块接通(2026-06-06,已做)** — 排查出一批「有模块、单测绿,但执行链从不调用」的孤儿,本轮接通三处:
  - **P1 预置条件分类器接通** — `TestCaseAgent` 默认自带 `PreconditionClassifier`(`DEFAULT_HOOK_MAP`,传 `precondition_classifier=False` 关闭);`generate_spec` 先三分类 → 把结果按类分组下发给翻译器(`build_spec_messages`/`SpecGenerator.generate` 新增 `precondition_items`,引导 given 只收 action_step)→ 再**确定性**把 action_step 合入 `spec.given`(按 target 去重,兜底 LLM 漏放);state_hook 的 hook_ref 写入 `ctx.required_hooks`、ambiguous 记 warning。CLI/API 两条路径都经 `generate_spec`,均接通。
  - **P2 Hooks/Session 接通 API** — `Suite.session_profile`(名称引用)→ `Store.get_session_profile` → `harness/hook_builder.py::build_session_hooks` 组装 HookManager,经 `make_agent` 注入每个用例 agent(`hooks=`)。`LoginHook` 新增 `optional` 模式:无 login_aw 且 Cookie 失效时**不报错、放行**让 Agent 自行登录(避免「Cookie 缺失→全 FAIL」回归);新增 `CaptureSessionHook`(after_case,用例通过后抓浏览器 Cookie 落盘)+ `make_mcp_cookie_capturer`,实现**不接 login_aw 也能跨用例 Cookie 复用**(首例 Agent 登录→落盘,后续注入复用)。接了真实 login_aw 时传 `login_runner` 即恢复规格 §5.4「过期重登」。
  - **P3 基础 Skill + custom_prompt 接通** — `harness/skills.py::build_skill_manager`:注入内置 `DEFAULT_SKILLS`(表单操作 / 结果定位等基线常识,`preload=True`)+ 把 `Suite.custom_prompt` 作为 preload Skill 接通;经 `make_agent`(API,带 custom_prompt)与 CLI(`--no-skills` 可关)注入 agent。〔**2026-06-15 重构为标准 Skill 渐进披露**:项目级 Skill(`ProjectSkill` + `description` 列)经 `run_executor` 接入,LLM 调 `load_skill` 按需展开;前端项目设置页 Skill 表单加「简述」字段。**渐进披露链路完整保留**(`SkillManager` render/load/tool_schema),但**项目 Skill 暂用默认加载**(`run_executor` 构造时 `preload=True`,正文常驻)——弱模型常不主动 `load_skill` 致 skill 形同虚设,先默认加载保生效,渐进式加载的调试列 TODO(改回去掉 `preload=True` 即可)。〕
  - **P4 更复杂开源验证素材** — `examples/make_automation_exercise_xlsx.py` 生成 Automation Exercise(https://automationexercise.com,公开发布 26 条业务用例)的注册/下单/搜索三条用例(`automation_exercise_cases.xlsx` + `automation_exercise_vocab.json`);比 saucedemo 复杂(多字段表单、结算流程、混合 state_hook/状态声明的预置条件,压测翻译/断言/词汇表/P1 分类)。尚需真实 LLM+浏览器 live 跑一轮。
- **Custom Tool + 数据断言接通(2026-06-06,已做)** — 继续填空壳,接通 `ToolRegistry` 与 `custom_tool` 断言:
  - **ToolRegistry 从 YAML 接入执行链** — `harness/tools.py::load_tool_registry_from_yaml`(顶层 `tools:` 列表或直接数组,每条需 `name`+`command`);CLI 加 `--tools <yaml>`、API 读 env `CUSTOM_TOOLS_YAML`,经 `make_agent` 注入每个用例 agent(`tools_registry=`)。示例 `examples/custom_tools.yaml`。此前 `ToolRegistry` 完整但 API/CLI 从不实例化。
  - **`custom_tool` 数据断言执行(规格 §5.3#4/§5.4)** — `AssertionEngine(tool_registry=...)` 实现 `_check_custom_tool`:**约定** `target`=已注册工具名、`selector`=JSON 参数、`expected`=期望子串(空则结果非空且非错误即通过);工具失败→FAIL;未接 registry 或工具名未注册→skipped(不静默放过)。`agent.run` 把 `self.tools_registry` 传入引擎。
  - **`llm_judge` 仍保持 skipped(铁律2)** — 即便接了 registry 也**不执行 LLM 眼判**,标 skipped 待人工复核;裁决全 skipped 不算可信通过。
  - **action_map 不动(有意)** — `PageVocabulary.action_map` 规格里只有一行声明、无任何语义定义;「填」它=凭空发明 Phase 5 设计(违反「不过度设计未来阶段」),故保留为显式预留字段。`ExecutionRecord.generated_code` 的 `# TODO: Phase 5` 是**误导性 stale 注释**(其实早已接通)→ 已清理。`/vocabulary/scan` 是有意的 no-op(真扫描在执行期增量做)。
- **ReAct 卡死修复 + 首轮真实 live 验证(2026-06-07,已做)** — 用 DeepSeek(`deepseek-v4-flash`)+ 真 Chrome(playwright-mcp,`--isolated --headless`)live 跑 saucedemo,**端到端首次拿到 live 绿**。
  - **根因**:弱模型抓完快照后**只回文字、不发工具调用**(落入 `react_loop` 的 `not resp.tool_calls` 哑火分支),原因是 Context Compact 只留最近 2 条观察、且按**中文步骤关键词**截断快照(页面文案常是英文,如「登录按钮」命不中 `Login` → 目标行连同 ref 被丢)→ 模型手里没 ref 只能叙述 → 哑火超 `max_idle_nudges` 被终止 → 卡在「点登录」FAIL。
  - **修法**(`harness/react_loop.py`):哑火且仍有未完成步骤时,**主动抓一份最新完整快照**(`_safe_snapshot`)作为**普通 user 消息**(不加 `[观察]` 前缀 → 不被 Compact 折叠/截断)喂回,配强指令「立即只调用一个工具,用快照里的 ref 操作当前步骤」。给具体 ref + 逼出动作,治叙述退化。无 `get_snapshot` 时退回纯文字催促(向后兼容)。
  - **live 实证(5 轮)**:修复前 1 轮卡在点登录 FAIL;修复后 TC101 ×3 全 PASS(其中 2 轮**真实再现哑火并被快照续推拉回完成**,非靠运气)、TC102 PASS(且 live 跑出 1 次成功**自愈**)。终态断言 `url_contains inventory.html` + `text_equals 购物车角标==1` 均 live 绿。
- **prompt 优化(2026-06-07,已做)** — 基于真实 LLM 请求体(31 条消息)审视各轮 prompt,修两处高价值问题:① **BASE_PROMPT 强约束**「每轮回复必须以恰好一个工具调用结束(`mark_step_done` 也是工具调用),只输出文字=未推进会被打断;唯一例外是全部完成后输出 `TEST_RESULT`」——从源头治弱模型「叙述代替调用」的卡死;② **idle 续推指令修正**——改为「页面操作已完成就直接 `mark_step_done(step_no=N)`,否则才用快照 ref 操作」,不再在 mark_done 场景误导 + 白塞整页快照。live 复验 TC101 ×2 全 PASS、**0 次哑火卡死**(优化前 r3/r4 都触发过)。**未做(记入「下一步候选」C/D)**:每业务步 3 次 LLM 往返、system 每轮重列全部工具文本——省 token 类,有正确性/收益权衡,待独立 A/B 验证。
- **公开站点验证战役 + 真 bug 修复(2026-06-07,已做)** — 内网用例阻塞,改用公开站点把**未 live 验过的路径**逐条跑通(都用 saucedemo + 真 DeepSeek + 真 Chrome):
  - **API+SSE 端到端**(此前只有单测)— 起 `uvicorn` → API 建 suite/传 Excel/触发 run → 收 SSE → 拉结果。两用例经 worker 线程路径 PASS 2/2,SSE 事件全到(`suite_start/case_start/phase/spec_ready/step_change/case_result/suite_done`),落库 status=completed。**整条阶段四生产路径(前端走的那条)首次真跑通**。
  - **复杂多步流程** — saucedemo 完整结算(登录→加购→购物车→Checkout→填 First/Last/Zip→Continue→Finish)11 业务步 PASS,终态 `text_equals "Thank you for your order!"` + `url_contains checkout-complete.html` 双绿,过程 2 次自愈。素材 `examples/saucedemo_checkout.xlsx`。
  - **custom_tool 数据断言** — 接 `examples/custom_tools.yaml` 真跑外部命令:`http_health` curl saucedemo→`200` PASS、期望 `500`→FAIL、未注册工具→skipped,确定性比较全对。
  - **P2 跨用例 Cookie 复用 + 抓出真 bug** ★ — 走 API 路径(hooks 只在 API 接通)2 用例 suite。**首跑两用例都"绿"但 cookie 文件根本没生成 → 功能其实没接通**(典型「看着绿实则空转」,幸亏走真实端到端到产物)。根因:`browser_run_code_unsafe` 把返回值**双重 JSON 编码**(cookies 是带引号的 JSON 字符串字面量 `"[{\"name\":...}]"`),`_parse_cookies_result` 只 loads 一次 → 0 条 → `CaptureSessionHook` 从没存过。修:`_parse_cookies_result` 兼容双重编码(先 loads 外层字符串再 loads 内层数组)。修后:cookie 文件生成、TCB 步数 7→3(省了登录);**铁证**(无 agent 干扰的直接注入回放):捕获 cookie→全新浏览器注入→直达 `inventory.html` 免登录可达(6 个商品)= True。教训复刻:**saucedemo 账号太有名,agent 可能自己重登 → 用例"绿"会掩盖复用失效;验证复用要看产物(cookie 文件)+ 隔离注入回放,别只看用例 PASS**。
- **定位器对齐(2026-06-07,已做)** — 修 codegen 退化文本兜底。实证根因:① 模型把 ref 放进 **`target`** 参数(非 `ref`),`react_loop` 只读 `ref` → 执行捕获 role+name 永远落空;② 真正可靠的是 tool_result 里**实际执行的 Playwright 定位器**(`page.locator('[data-test="username"]')` 等,真跑通过、必然唯一可用)。修:`react_loop._ref_alias`(从 `target` 等别名认 ref,形如 `e\d+`)+ `extract_executed_locator`(抓实际执行的定位表达式存 `ActionStep.element_selector`);`codegen/locators.py::locator_from_executed` 解析成 Locator(getByRole→ROLE / getByTestId→TEST_ID / locator(css)→CSS …),`locators_from_steps` **优先用实际执行定位器**(ground truth,胜过快照重建的可能歧义 role+name,如 6 个"Add to cart")。**闭环实证**(无 `--vocab` 纯靠执行捕获):TC101 生成代码 action 步全部用真实选择器(`[data-test="username/password/login-button/add-to-cart-sauce-labs-backpack"]`)、无兜底注释;pytest-playwright 回放登录+加购全过。**遗留**:断言目标(如购物车角标)不经"执行定位器"捕获(断言走 probe 不产出可复用选择器),无 vocab 时仍文本兜底——需 vocab/selector 对齐(设计内边界)。
- **codegen 闭环验证 + 导航修复(2026-06-07,已做)** — 真把生成的 pytest-bdd 用 Playwright 跑了一遍。发现并修:用例把"打开页面"写在预置条件(被 P1 归 state_hook 不进 steps)→ 生成代码缺 `page.goto`、回放不开页面。修:`agent.ensure_navigation_step` codegen 前置注入隐式导航。闭环实证:含正确定位器的 spec→生成 BDD→pytest-playwright 真打 saucedemo 1 passed。遗留:spec target 与词汇表/捕获键不一致时定位器退化文本兜底(待对齐)。**附:此前怀疑的"step-def 命名 bug"是 `ls|head` 截断误判,不存在**(`test_<case>.py` 本就按用例命名)。
- **llm_judge / 词汇表 base_url / 主动扫描(2026-06-10,已做,用户拍板三连)** —
  - **#1 llm_judge 方案A(推翻原"恒 skipped")** — 接降级链最末档兜底:`AssertionEngine(llm=)` 真判 PASS/FAIL **计入裁决**,但结果标 `ai_judged`(低置信)、裁判 prompt 偏向 FAIL(宁可误报失败不可误报通过);前端断言视图加「AI判定·低置信」标记使 false green 可见可回溯。**铁律2/4 口径已改**(CLAUDE.md+产品设计文档+TODO);skipped 收窄为「custom_tool 未接 / llm_judge 未接 LLM」。**仍引导:能结构化/查真值的预期优先 DOM/文本/URL/custom_tool**。
  - **#2 词汇表绑 base_url** — `PageVocabulary`/`PageVocabularyRow` 加 `base_url` 维度,去重键含之;`find_page` 按「base_url 为当前 url 前缀」作用域隔离(空 base_url 通配,向后兼容)→ **跨系统不再撞键**(系统甲/乙的 `/login` 互不污染)、**同系统多 suite 共享一份**。前端词汇表页加「系统(base_url)」列 + 新建表单字段。`Suite.page_intelligence_id` 仍预留(base_url 作用域已实质完成 suite↔vocab 关联,更干净)。**存量数据:迁移自动加列(旧行 base_url="" 通配);要清版需手动清 `page_vocabulary` 表**。
  - **#3 主动扫描 + 执行期扫描默认关** — `intelligence/active_scan.py::ActiveScanner`:会导航的**只读**探索式扫描——起浏览器、可选登录(**最简方式:账号+密码表单登录** `make_mcp_credential_login`,2026-06-15 由 `session_profile` 改来——用户不理解 Session Profile 概念;`ScanRequest.username/password/login_url`,启发式选择器填用户名/密码框+点登录)、按入口清单逐页抓快照提炼词汇、**可选浅爬**(点击 link/tab/menuitem 进入点击触发的内页,跳过可及名含高危词的元素,深度/页数受限)。`/vocabulary/scan` 从 no-op 改真干活(后台线程+独立 loop/Store/MCP,同 execution_worker;返回 scan_id 轮询);前端加扫描表单(base_url/入口清单/Session/浅爬开关 + 状态轮询)。**`VOCAB_SCAN` 默认改 0**(执行期增量扫降为可选补充,主动扫描为主入口,消除交互延迟 + 两写入源打架)。单测 +12(active_scan 6 / base_url 作用域 / llm_judge 4 / scan 端点)。
- **翻译阶段提速 + 铁律2 重订 + 断言去冗余(2026-06-10,从内网「翻译总超时」问题展开)** —
  - **TestSpec 翻译合并:2 次 LLM 往返 → 1 次** — 原本串行做「预置条件分类」+「生成 spec」两次大调用,是与模型快慢无关的结构浪费。合并:模型在翻译时**顺带输出预置条件分类**(`preconditions` 数组),再由 `PreconditionClassifier.classify_from_raw` 确定性建项(置信阈值/Hook 映射/用户确认优先),**不再单独调 LLM**。`build_spec_messages(request_classification=)` + `parse_classification` + `SpecGenerator.generate_with_classification`;`agent.generate_spec` 有待分类→合并 1 次,无待分类(空/全命中 memory/分类器不支持合并)→ 退回旧两次路径(向后兼容)。**§3.2 确认闭环不变**。live(DeepSeek)复验单次调用同时分类+翻译、质量不退化。〔注:内网「翻译撞 300s 超时」根因是慢模型对大请求生成超时(模型资源,用户另行申请)。**2026-06-11 用户拍板:翻译阶段改流式**(`LLMClient.chat_stream` 用 `acompletion(stream=True)`,逐 token 经 SSE `spec_delta` 推前端)——根因是 300s 是**网关/代理空闲超时**,streaming 让网关见持续字节流不切连接,长生成得以完成;浏览器侧增量合批(~50 字符)削减事件/queue run_event 行数。**推翻原「不为慢模型定制」**:streaming 是对的(既保活又显进度)。流式仅 spec 翻译(无 tools)走,ReAct 全流式(思考/工具/断言/自愈)列为第二步。〕**第二步已落地(2026-06-11):ReAct reasoning 流式**——`LLMClient.chat(on_delta=)` 走流式(`_complete_stream` 用 `acompletion(stream=True)`,逐 token 回调 reasoning;tool_call 由 `litellm.stream_chunk_builder` 重建后交回 `_parse`,容错语义与非流式一致,重试走非流式);`ReActLoop(on_llm_delta=)` 经 SSE `think_delta` 推前端(合批 ~50 字符,step_change 落定前 flush 保证「思考→步骤」顺序)。**ReAct 期每次 LLM 调用也对网关保活**(与 spec 同理,治执行期长调用空闲超时切 SSE)。前端 `RunningView` 执行阶段显示「思考过程(流式)」,step_change 落定即清显示下一步。回放复用已存 `ActionStep.reasoning`(流式 token 仅 live,不持久化)。live(DeepSeek)TC101 复验:流式路径 tool_call 重建正确、终态双绿、停因=completed。**仍未做**:断言/自愈逐条 live 事件(可后续补,断言走批量裁决、自愈已有 heal_attempts/已自愈徽标事后可见)。〕**流式可观测性收口(2026-06-11)**:思考流不再用完即弃——`useSuiteRun` 在 step_change 落定时把累积 thinkStream **定格进该步 `reasoning`**(retain),抽屉步骤详情渲染「思考过程」(运行中步显示实时流+光标、已落定步显示定格文本;执行完后从 `history.reasoning` 回溯)。执行中**自动跟随当前步骤**(`followLive`,用户点任意条目即停跟随自由回看)。**消卡顿**:`steps` memo 改依赖稳定的 `liveState.steps` 引用(非整个 liveState)→ 思考流逐 token 推进不重算步骤列表;think 合批阈值 50→120 降 setState 频率。〕**过程时间线收口(2026-06-11,统一全过程一处可见)**:把右栏从「Preview/代码 tab + 断言」改为 **`TimelineView` 单一过程时间线**——顺序 **翻译规格(流式)→ 逐步(思考流/工具调用/自愈徽标/截图,可展开折叠,运行步自动展开)→ 结构化断言(含执行未完成告警)→ 最终结果(verdict + token/自愈/停因 + 生成代码折叠)**,执行中流式追加+自动滚动,执行后从 `history` 回溯,**全过程同一处**(此前误解为左栏点步看详情,用户要的是一条端到端时间线)。配套后端:`step_change` SSE 加 `reasoning/tool_result/url/heal_count`、`to_history`+`_build_history` 加 `heal_attempts`(两序列化同步,自愈可见)。删死代码 `RunningView`/`rightTab`/`finalShotNo`。live TC101 复验无回归。**抽屉收口(2026-06-11)**:左栏「步骤」列表(与时间线冗余)移除——纯展示的**测试步骤**并入「用例信息」(`InfoView` 加 `测试步骤` 块);「查看 prompt」能力下放到时间线**每个步骤**内(展开后按步切换,各步独立);左栏只剩「用例信息 / 测试结果」两项,选择模型收窄为 info|result(删 step 选择 + 自动跟随 + 单步详情视图死代码)。〕
  - **前端流式/抽屉渲染性能收口(2026-06-12)** — 流式逐 token 期间消除整页/整抽屉重渲染的一组架构修(纯前端,无行为/配置变更):① **流式文本搬出 React state** — `useSuiteRun` 内建外部 store(ref+per-case 监听器)承载 `spec`/`think` 流式增量,`spec_delta`/`think_delta` 只 notify 订阅者**不 setStatuses**;抽屉用 `SpecStream`/`ThinkStream` 叶子经 `useSyncExternalStore` 自订阅 → 仅这俩叶子随 token 重渲染(此前每 token 重渲染整个 `SuiteCasesPage`)。② **rAF 合批通知** — store 通知用 `requestAnimationFrame` 攒到帧末一次,流式叶子每帧至多重渲染一次(≤60fps),与 token 速率解耦;后端 `emit_think_delta` 合批回到 ~60 字符(渲染成本已被 rAF 兜住,小批量让文本顺滑流出,不再一跳一跳)。③ **`run` 收进 `RunProvider`(children-as-props)** — 执行状态从 `SuiteLayout` 移入只包 `<Outlet/>` 的 `RunProvider`,经 context 下发;高频 `statuses` 更新只重渲染 Provider,`<Outlet/>` 引用不变令 React 跳过整棵子树,**仅 `useSuiteRunCtx` 消费者(用例页)重渲染**,侧栏/面包屑/非消费页不参与(SSE 跨 tab 存活不变,按 `suiteId` 关流)。④ **`TimelineStep` 浅比 memo** — `step_change` 时 `steps` 数组整体重建(每步新对象),自定义 `areTimelineStepEqual` 按字段浅比 → 只内容真变的步重渲染。⑤ **`CaseRow` memo + 稳定 `onSelect`** — 用例表行 memo,`step_change` 只重渲染状态真变的行。⑥ **`StreamPre` 自动滚底 + 尾部窗口**(超长流只渲染尾 8000 字,界住单帧排版)。⑦ **抽屉滑入** — 去 `loading` 全屏遮罩(`InfoView` 由 `caseInfo` 同步可得,结果到了再切时间线,避免滑入期整面多次跳变)+ 面板 `transform-gpu will-change-transform` 提合成层。
  - **铁律2 重订** — 区分 LLM 眼判两种角色:**(a) 驱动执行=鼓励**(随机应变绕偶发噪声让执行健壮,不进裁决;中途校验须软、可恢复,不可硬闸门);**(b) 裁定 PASS/FAIL=规则引擎为主 + llm_judge 显式可审计兜底**(方案A 已选择信任)。底线:不让 LLM 不可见地刷绿、不取自报 TEST_RESULT。推翻原「绝不让 LLM 眼判」的绝对表述。
  - **断言去冗余(#2,A-保留字段版)** — 翻译 prompt 原要求步骤级 `expect` + 用例级 `assertions` 两处都放、再事后去重,且瞬态 expect 拍到终态验是 false-fail 雷。改为**只索取用例级 `assertions`**;`SpecStep.expect` **字段保留**(给未来 B-软留门)、`collect_assertions` 仍聚合+去重作防御网。live 复验 spec 无 expect、断言仅用例级、质量不退化。
  - **B-软(步骤级软校验)——第一步已落地(2026-06-11):过早 mark_done 护栏**。`react_loop` 加最小护栏(`_guard_premature_mark`):本轮**仅**调用 `mark_step_done`、且该 StepPlan 步骤**从未执行过操作类工具**(非 snapshot/非 mark、未报错)、且**未就此步提示过** → 软提示「先实操再标记」、不采信本次标记(铁律2(a):软、可恢复、**不判失败**),`continue` 续推。三重保守 + **每步至多拦一次**(已提示过再标即放行,覆盖「纯校验/状态已满足、确实无需操作」的合法步);代价至多一次多余往返。治「没点 Finish 就 mark done」类过早收尾。**未做(避开「定位不稳→步骤级硬信号误判空转」依赖)**:通用 `expect` 步骤级软校验、瞬态期望覆盖——**前置仍依赖「断言目标定位器对齐」**,待定位层对齐后再上。live(DeepSeek)TC101 复验护栏不干扰正常流(每次 mark 前都有实操,护栏不触发)、终态双绿。
  - **流式 ReAct 抗丢调用复核(2026-06-11)** — 流式(`on_delta`)下 `stream_chunk_builder` 重建 tool_call 有概率漏采 → 模型其实调了工具却被当「未推进」哑火,叠加几轮就触发「连续 N 次未推进→终止」(停因 `llm_finished`)。`react_loop` 在「无 tool_call 且仍有未完成步骤」时**非流式复核一次**(`chat` 不带 `on_delta`)把漏采的调用捞回来,再走哑火逻辑。仅在该分支触发(代价至多一次额外调用),治流式 ReAct 偶发早停。单测覆盖(流式丢→非流式捞回)。注:**真·模型放弃**(确实只回文字)复核也捞不回,仍按哑火兜底——长流程(结算)易撞 `max_steps`/模型变慢,需调大 `AGENT_MAX_STEPS`(80-100)+ 文本模型 `HEAL_VISUAL=0`(免视觉自愈 image_url 被网关拒的失败调用)。
  - **执行完整性闸门(2026-06-11,原则强化)** — `agent.run` 在断言裁决前加闸:**任一步骤非 DONE**(pending 未执行 / failed / skipped,即 `not plan.all_done()`)→ 用例**直接 FAIL**,`final_result` 标真因(「执行未完成:仅完成 N/M 步,停因=…」),**不靠半路断言裁决**。根因:早停(哑火上限 / max_steps / 卡死 / tool 错)会留 pending 步骤,旧代码却照跑终态断言 → 半路页面上断言**可能误绿(碰巧过)也可能误红(掩盖真因)**。这是铁律4「PASS 以断言裁决为准」的必要前提:**先确认流程真跑完,断言才有裁决资格**。前端告警条同步从「仅 max_steps」泛化到「执行未完成」。〔实测内网:10+ 步 spec 登录完(第4步)就跳断言 = 此 bug;现强制 FAIL 并标真因。〕
  - **可用 Hook 告知 LLM(2026-06-11)** — 合并翻译/分类时把**实际配置**的 Hook 名(`HookManager.hook_names()`,无则空)注入分类 prompt(`build_spec_messages(available_hooks=)`):有 Hook → 只对可用 Hook 归 state_hook+写 hook_ref;**无 Hook → 引导状态前提归 action_step(测试内执行)/ ambiguous,不许归 state_hook**。治「分类成 Hook 却没配 Hook → 该前提被静默忽略」(`session_profile=None` 时 `hooks=None`,state_hook 无人执行)。
- **裁决哲学转向:LLM 裁判为主 + 确定性锚点(2026-06-17,据真实公网评测重订铁律 2/4)** — 用真实公网用例(automationexercise)实证:① 结构化规则引擎在真实页面贡献≈0(翻译几乎不产结构化断言、业务词↔元素定位脆弱、false-fail 高到用例不可用;AE03 6 条裁决**全是 llm_judge**、用例级 `assertions` 空);② 把裁判从执行**剥离单独压测**(`eval_fg/`:抓真实快照 × 26 条真/假混合预期)——**偏-FAIL 裁判 false-green=0/15、正确率≈96%**,"本地模型脑补成功"的恐惧未兑现;唯一 1 例假绿根因是**门控 fail-open + reason 含未转义引号炸 JSON**(工程缺陷,非脑补)。另发现 FG01:把假预期挂成步骤完成判据 → 门控硬闸门把 agent 带去**追错目标**(注册账号、烧 42 万 token 撞 max_steps),实证铁律2(a)「不可硬闸门」。据此重写铁律 2/3/4 + 产品设计文档 §5。**已落地(安全部分,Fix 1/2)**:`harness/llm.py::extract_verdict`(JSON 炸时正则捞 PASS/FAIL)+ 接入 `_check_llm_judge`/`_gate_step_done`(**裁决路径 fail-closed**;门控仍 fail-open 但尊重明确 FAIL);单测 5 条,saucedemo TC101 live 双绿无回归。
- **Fix 3 = 实装新裁决架构(2026-06-17,已做)** — 据重订铁律 2(b)落地「LLM 裁判为主 + 确定性锚点」的执行结构,四块:① **解耦门控**(`agent.run::verify_step`)——`_gate_step_done` 结果不再塞进参与裁决的 `step_assert_pairs`,改记独立 `gate_observations`(落库标 `phase="gate"`,前端「驱动门控·不计入裁决」徽标 + CLI 摘要 `〔驱动门控·不计入裁决〕` 标注),门控**只驱动 reactivate**;步骤级 url/custom_tool 确定性锚点仍计入裁决。② **终态裁决路**——`engine.verify_all(spec.assertions)` 为主路,自然语言预期走**强化后的** `_check_llm_judge`(偏-FAIL + **强制引证页面证据**,引证不出判 FAIL;**实时 URL 作免费锚点**显式喂入裁判),逐条裁决,fail-closed 不变。③ **翻译配合**(`intelligence/pre_analysis.py::_SYSTEM_PROMPT`)——每条「最终态」预期都落成一条用例级 assertion(能 URL/数据确定性验的优先结构化锚点,否则用 `llm_judge` 承载自然语言原文,**已从"最末档"升为默认主裁决**);保留「中间页不进 assertions」护栏;澄清 `expect_text` 只是驱动信号、非最终裁决标准(贴铁律2(a)/FG01)。④ 单测:`test_agent` 解耦/终态裁判/「门控独不刷绿」、`test_assertion` URL 锚点喂入、`test_pre_analysis` prompt 路由。**live 实证**:saucedemo TC101 双绿(裁决来自终态 url 锚点 + 用例级 llm_judge);**AE03 重跑**裁决改由**终态 llm_judge**(「购物车页面」)给出、门控判定降为 `phase="gate"` 观测(印证「裁决来源从门控转到终态裁判+锚点」)。
- **Fix 3 收尾加固(2026-06-17,已做)** — 补齐「弱模型脑补证据 / 翻译产脆弱锚点」两条防线,使终态裁判真正可信:
  - **裁判证据确定性核验(治脑补刷绿)** — `_JUDGE_SYSTEM` 改为**判 PASS 必须在 `evidence` 字段逐字摘录页面/URL 实证**;`_check_llm_judge` 拿到 PASS 后**确定性核验**该 evidence(空白归一后子串匹配,`_norm_evidence`)真出现在当前页(快照/URL),**不在 → fail-closed 推翻为 FAIL**;实证回写 reason 可审计。仅当确有可核验来源时启用(真实探针总有快照;无快照单测不误伤)。直击「把中间页/别页预期在终态页脑补判过」。live 实证:TC101/AE03 终态裁判均给出可核验实证(`generic [ref=e124]: "1"` / `cell "1" [ref=e70]`)通过核验。
  - **url_equals 结尾斜杠容差 + 翻译偏好 url_contains** — `_check_url_equals` 归一结尾斜杠(`https://x` ≡ `https://x/`,RFC 根路径等价,浏览器常自动补 `/`);翻译 prompt 引导步骤级导航锚点**优先 url_contains 写稳定 URL 片段、不要 url_equals 精确匹配整 URL**。治 AE03「打开首页」步 url_equals 因一个尾斜杠 false-fail 拖垮整条用例(LLM 变异产出 url_equals 时surface)。
  - 单测:`test_assertion` 证据核验(脑补推翻 / 可核验通过 / 无快照不误伤)+ url_equals 斜杠容差;**AE03 加固后 live 复跑 ✅ PASS**(裁决=步骤级 url_contains 锚点 + 终态 llm_judge 实证核验)。
- **A-2 裁判可靠性评测 + 证据核验校准(2026-06-18,已做)** — 用 `eval_fg/`(裁判从执行剥离,26 条真/假混合预期 × automationexercise 真实快照)量化 Fix 3 收尾「证据核验」对弱模型的误伤。**首测暴露**:证据核验用"整串子串匹配"时,断言裁判 false-fail=3/11(27%)、其中**证据核验误伤=2/11(18%)**——根因是**复合预期**(如"导航含 Products、Cart、Test Cases")模型把 evidence 写成**概括句**而非单一逐字串,整串匹配命中不了。**修复**:核验改"锚点接地"(`_evidence_grounded`/`_evidence_anchors`:抽引号片段/≥4 英文数字串/≥3 中文串作锚点,**任一**逐字落页即算有据);仍拦整段脑补(无锚点落页,如 standard_user 在无该字段页)。**复测(deepseek)**:断言裁判 false-green=0/15、**false-fail=0/11、证据核验误伤=0/11**;门控(偏-PASS)false-green=0/15。单测 +1(复合概括证据不误伤)。`eval_fg/judge_eval.py` 加 FakeProbe 真实 URL + 原始/核验后判定分离统计(误伤 vs 有益拦截);saucedemo TC101 live 复跑 ✅ PASS 无回归。**遗留(A-2 可选)**:样本 n=26 偏小(0/15 的可信区间上界仍宽)、仅 deepseek 单模型——扩样 + 第二模型(需另配 LLM 凭据)留作后续。
- **会话/Cookie 复用退役 + Hook 回归通用扩展点(2026-06-18,已做,用户拍板)** — 实证讨论后定调:① **Cookie 复用不对症**——`context.cookies()` 抓不到 localStorage/sessionStorage,对 SPA/Token(JWT)型登录是不报错的空操作;固定 1h TTL 与真实会话寿命脱节、无存活探测;且它只是 Playwright `storageState` 的严格子集(重造了一半轮子)。② **跨用例登录复用选「每用例 hook 重登」(方案①)**而非搬状态:全新浏览器架构下要复用登录,逻辑上只能"搬状态/不重登共享上下文/每用例重登"三选一;放弃 Cookie 即选每用例确定性重登(保用例隔离、对 Cookie/SPA/SSO 一视同仁)。③ **Hook 回归纯通用扩展点**(参考 Claude Code hooks,平台只提供机制、用户自己实现 hook);**登录不绑死在 hook 里**,登录态复用交后续「**环境管理**」主线维护。**落地(甲:彻底删)**:删 `SessionManager`/`LoginHook`/`CaptureSessionHook`/`make_mcp_cookie_*`/`_parse_cookies_result` + `SessionProfile` 模型 + `Suite.session_profile` + `store.*_session_profile` + `SessionProfileRow` + `hook_builder.py` + 前端 Session 设置页 + projects 路由 `/session-profiles` 端点 + 相关单测;`harness/session.py` 仅留 `make_mcp_credential_login`(扫描登录还用);alembic `0002_drop_session_profile` 降表降列;`run_executor` 的 agent `hooks=None`(扩展点仍在 `agent.hooks`,默认不预填)。验证:`pytest -q` 504 passed(2 个预存在 Windows 失败不变)、前端 `npm run build` 绿、isort/black 过。〔**未做**:替代登录(credential hook / 环境管理对接)本次不接,纯做减法。〕
- **下一步候选(2026-06-07 收口时的待办):**
  - **Fix 3 = 实装新裁决架构** — ✅ **已做**(含收尾加固 + A-2 评测校准,见上「实施进度」Fix 3 / A-2 条目)。**剩余可选**:⑤ 继续用 `eval_fg/` 扩样(换 Cisco/ThingsBoard + **第二模型**,需另配 LLM 凭据)把 false-green/false-fail 率做到更紧的可信区间(A-2 已在 deepseek×26 条上测得 0/0,但样本偏小、单模型)。
  - **真实内网用例 live 验证**(主线,当前被环境阻塞)— saucedemo 全链路已 live 绿(基础/结算/会话复用/custom_tool/codegen 回放),内网真实业务系统待跑。解阻塞后 CLI/API 两条路径都就绪。
  - **prompt 优化 C/D(省 token/提速,未做)** — C:每业务步约 3 次 LLM 往返(snapshot→action→mark_done),~100k token/用例,可探索「动作结果已带快照则免单独 snapshot」「mark_done 合并」(有正确性风险,实测 click 结果不总带 ref,需 live A/B);D:system 每轮重列全部 ~25 工具文本(已另经 `tools=` 传,冗余),可用 `PromptBuilder.max_tools` 按相关度截断(风险:漏掉所需工具)。已做的 prompt 优化见「prompt 优化(2026-06-07)」:BASE 强制每轮工具调用 + idle 指令修正,live 0 卡死。
  - **断言目标定位器对齐(遗留,部分缓解)** — codegen 的 action 定位器已对齐(执行捕获),但**断言目标**(如购物车角标)不经"执行定位器"产出,无 vocab/selector 时仍文本兜底。需在 vocab/selector 层对齐(断言走 probe,naming 随 LLM 漂移、子串匹配也常错过)。**2026-06-15 缓解**:`text_equals/text_contains` 在**元素定位失败 + 自愈也没救回**时,加一档**全页文本兜底**(`AssertionEngine._text_page_fallback`)——整页快照里确定性搜 `expected` 子串,命中判 PASS 但 reason 标「全页文本兜底」可审计区分。**护栏**(防短串误绿,贴铁律2「宁可误报失败」):① 有显式 `selector` 不兜底(selector 失败是真信号,如空购物车);② `expected` 须够独特(含空白短语或长度≥5,排除 "1"/"2"/状态短词);③ 放在**自愈之后**,优先元素级/自愈精确绿。治「业务词名(中文「成功提示区域」)对不上英文页面元素 → 流程跑完仍 false-fail」(saucedemo 结算「Thank you for your order!」实证)。
  - 阶段五(用例管理平台集成,规格"现在不做")。
- 单测数量以 `python -m pytest -q` 实跑为准(2026-06-17 Fix 3 + 收尾加固后约 526 passed;**2 个预存在失败**:`test_recorder` 截图目录 + `test_tools` 命令替换(均 Windows 平台,与业务逻辑无关)。`test_healing` 视觉自愈 3 个此前在全量顺序下因 vision 缓存跨用例泄漏失败,现工作树已修)。

T-xx ↔ 规格小节对照见 `实现规格说明书.md` §5(各模块详细规格)与 §6(实施计划)。

## 工作约定

- **每个任务动手前,重读 `实现规格说明书.md` 对应小节**(以原文为准,别凭记忆);并核对已实现部分有无偏离。
- **改动任何环境变量配置(新增/改默认/改语义)时,必须同步更新 `.env.example`**——它是配置的唯一索引(用户约定)。新 env 变量在代码里加 `os.getenv(...)` 的同时就把它(含默认值与用途注释)补进 `.env.example`。
- 每个任务配单元测试;不连真实 LLM/浏览器,用 fake/mock 驱动(参考 `tests/` 现有写法)。
- 改完跑 `pytest`,并 `isort`+`black` 格式化后再交。
- **较大改动(碰执行链 / 断言 / 翻译 / 词汇表 / codegen / API 执行路径)交付前,除单测外必须跑一轮 saucedemo live 冒烟**(真 DeepSeek + 真 Chromium + playwright-mcp,验端到端不退化,弥补「单测绿≠接通」)。最小冒烟一条命令:
  ```powershell
  python cli/run_case.py --excel examples/saucedemo_cases.xlsx --case-id TC101 `
      --base-url https://www.saucedemo.com --isolated --headless
  ```
  期望:`✅ PASS`,终态断言 `url_contains inventory.html` + `text_equals 角标==1` 双绿。
  改动较深时再加跑完整结算 `examples/saucedemo_checkout.xlsx` TC201(`--max-steps 60`,11 业务步,验长流程);只改翻译/分类时可用 `--spec-only` 快速验 spec 质量不退化(不跑浏览器、更快)。env(LLM)走项目根 `.env`。**这条 live 冒烟是 `CLAUDE.md` 约定的标准验证步骤,大改动不可只凭单测交付。**
- 分阶段推进:一个阶段验收通过再进下一阶段,不跳阶段、不过度设计未来阶段。
- 不确定的设计点(尤其用例管理平台集成)不要自行假设,先问用户。

## 工程经验(避坑,血泪复用)

- **"有模块"≠"接通了"**:本项目多次出现"能力模块写好、单测绿,但执行链从没调用它"——
  截图(`ToolOutcome.screenshot` 从不落盘)、代码生成(`BDDGenerator` 从不被调)、
  `spec` 从不进 `ExecutionRecord`。**验收必须走真实端到端到产物**,别只看模块单测。
- **环境先于代码怀疑**:"改了没效果"先确认"在看/在跑的是不是同一份东西"——
  Tailwind 改 `tailwind.config.js` **Vite 不热更需重启**;`:8000` 旧 `dist`、`:5173` 旧编译、
  浏览器缓存,都比代码更会骗人。(已根除:`:8000` 不再挂前端,前端只走 `:5173`。)
- **Tailwind 产物是 `rgb()` 不是 hex**:在编译 CSS 里 grep 颜色要按 `rgb(...)` 找。
- **a11y 快照是 YAML**:纯数字/特殊文本会被加引号(`: "1"`),解析须剥字面引号,
  否则 `text_equals "1" != 1` 误判(已修;saucedemo 终态现已 live 绿)。
- **弱模型会"抓完快照只叙述不行动"**:DeepSeek 等拿到快照后可能只回文字、不发 tool_call
  (落入哑火分支),叠加 Context Compact 按中文关键词截断英文页面快照丢了 ref → 卡死终止。
  解法:哑火时**主动喂最新完整快照 + 强指令逼出单个工具调用**(见 `react_loop._safe_snapshot`)。
  教训:weak-model live 跑要给「具体 ref + 强制动作」,不能只靠文字催促。
- **单跑一次绿 ≠ 修复生效**:live 有 LLM 波动,bug 可能本轮没复现。验证护栏类修复要
  **多跑几轮**、并确认**真复现了问题且被恢复**(看日志里护栏路径是否触发),否则可能是运气。
- **稳健定位在语义层**:role+可及名/test-id 不随样式/布局/class 变;CSS/文本脆弱。
  定位逻辑要与具体测试框架解耦(模型+解析层 / 渲染层分离)。
- **per-run 数据别按 case_id 落平文件**:会被后续 run 覆盖、抽屉串味;优先存进 `ExecutionRecord`。
- **误判结论要随证据回收**:文档里写过的根因,拿到新证据(如引号 bug)要更新,别让旧判断误导后人。
- **同一数据有"两套序列化"要一起改**:`recorder.to_history`(执行内/落库)与
  `api/routers/results.py::_build_history`(结果接口)各自手写 step 序列化,加字段只改一处会
  出现"执行中能看、完成后丢"(本轮 `prompt` 字段即栽在此)。改 history 结构务必两处同步。
- **封装别越权兜底**:LLM 封装在**未传 tools** 时不应对 content 做 tool_call 提取/报错——
  正常 JSON/文本里只要含 `"name"` 子串就会被误判成坏工具调用而抛错,连累所有要纯内容的
  调用(Scanner/SpecGen/healing)。容错只在"调用方确实期望工具调用"时启用。
- **本工具 bash cwd 跨调用保持**:`cd frontend` 后下一条命令仍在 frontend,git 操作记得回根目录。
- **单事件循环会被同步活儿饿死**:uvicorn 默认单进程/单 loop/单线程,无锁、协作式(只在 `await` 让出)。长任务(用例执行)跑在 API 共用 loop 上时,链路里**任何不让出的同步代码**(快照解析、库的同步开销、CPU)都会让**所有 HTTP 请求 pending**——表象像"DB 锁/崩溃",实为 loop 被占。诊断要点:**连无 DB 的接口也 pending = loop 阻塞**(非 DB 锁)。根治不是逐行挪线程(打地鼠),而是把执行**整体搬出 API loop**(独立线程+独立 loop+独立 Store;SSE 经 `call_soon_threadsafe` 桥接)。SQLAlchemy async engine **绑定创建它的 loop**,跨 loop 必须各用各的 Store。
- **SQLite WAL 必须在事务外、连接级设**:`PRAGMA journal_mode=WAL` 在 `engine.begin()`(事务)里会被**静默忽略**;要用 connect 监听器逐连接设,否则默认 rollback-journal 下写阻塞读。
- **本机 Python 是 embeddable 版**:`~/python/python`,`sys.path` 锁定(有 `._pth`),跑临时脚本需 `sys.path.insert(0, os.getcwd())`;pytest 正常(自带 rootdir 注入)。
- **`--reload` 不能监视运行时产物目录**:codegen 执行通过后写 `storage/generated/*.py`,默认 `uvicorn --reload` 监视项目目录 → **检测到这些写入就重启整个后端**,后果是打断正在跑的 run、SSE 断开、重启窗口内**所有 HTTP 请求 pending**(曾被误判成事件循环/DB/GIL/代理问题,绕了一大圈)。用 `scripts/serve.py`(`reload_dirs` 只限源码)。诊断启发:**"一批请求 pending 后又集中恢复" + 日志里有 `Shutting down`/`Started server process` = 服务在重启**,不是 handler 慢。`--reload-exclude "storage/*"` 在 Windows 上 `Path.match` 不一定命中,用 `reload_dirs` 白名单更稳。
- **干净站点会骗你"裁决没问题"**(2026-06-17 裁决调查):saucedemo 英文、元素唯一,结构化断言看着稳;一上真实公网站点(automationexercise,中文业务词 ↔ 英文元素、多相似元素、广告浮层)结构化裁决 false-fail 立刻爆、且翻译几乎不产结构化断言(AE03 裁决 6 条全 llm_judge、用例级 assertions 空)。**验"裁决/定位"类设计必须用脏的真实站点,别拿 saucedemo 下结论。** "确定性裁决"的确定性只在比较层,定位层是启发式——干净站点恰好掩盖了它。
- **量裁判可靠性要把裁判从执行【剥离】单独压测**(`eval_fg/`):全 agent 跑动会被门控耦合污染(假预期把 agent 带去追错目标、撞 max_steps),**不是干净测量仪器**。正确做法:抓真实页面快照 → 喂裁判一组**真/假混合**预期 → 直接统计 false-green/false-fail。实测(deepseek 26 条):偏-FAIL 裁判 false-green=0/15,**"本地模型脑补成功"的恐惧未被数据支持**。
- **"假绿"多是工程缺陷,不是模型脑补**(2026-06-17 根因):唯一 1 例假绿 = 模型其实判了 FAIL,但 reason 含**未转义引号**炸了 JSON + 门控 **fail-open(解析失败放行)**。教训:**裁决路径必须 fail-closed**(解析失败 → FAIL/skipped,绝不默认绿),只有**驱动路径**可 fail-open;裁判只需 `verdict` 字段,**正则兜底捞 PASS/FAIL**(`extract_verdict`)比强解析整段 JSON 稳。
- **别把断言/预期挂成执行的硬闸门**(铁律2a,FG01 实证):把"业务预期"当步骤完成判据,**不可达/写错的预期会变无限重试 + 驱使 agent 追错目标**(假预期"已登录张三" → agent 跑去注册账号、烧 42 万 token)。**裁决标准不能泄漏进执行驱动**:预期只在终态裁决阶段验,执行门控只判"这步操作做没做"。
- **裁判判 PASS 时逐字引证 evidence(可审计),但平台「确定性证据接地推翻」已撤——别再加回**
  〔**2026-06-24 用户拍板①撤销,本条结论已更新**〕:Fix 3 收尾曾让 `_check_llm_judge` 对模型
  judge 的 PASS 做**确定性证据核验**(`_evidence_grounded` 锚点接地,不在→fail-closed 推翻),
  治弱模型脑补刷绿。但 **eval_fg A/B 扩样(deepseek-v4-flash,n=63,3 站点,6 轮共 189 次裁决)
  实测该层净 ≤0**:偏-FAIL 的 `_JUDGE_SYSTEM` **自身** false-green=0/34(0 漏绿)、接地层
  **有益拦截恒为 0**(要防的脑补一次没发生、一次没拦)、唯一可测作用是**偶发误伤**(全落在
  expected 无强锚点的「疑似脑补」分支,无 ground truth、纯跟模型对赌)。→ **整层删除,裁决权
  交回模型**(删 `_evidence_*`/`_expected_*`/`_norm_evidence` + E5 锚点)。**留下的耐久教训**:
  ① 仍**要求模型逐字引证 evidence**(偏-FAIL 纪律 + reason 可审计),只是 evidence 不再作
  推翻闸门;② 真正不可替代的是**与模型独立**的两道底线——层(1)解析卫生(`extract_verdict`
  正则捞 verdict / 无 verdict→fail-closed FAIL)+ G1 缺失三态→FAIL;③ **量化方法论**:用
  `eval_fg/ab_grounding.py`(同一次调用无损还原开/关核验)让"该不该信模型"由数据回答,而非
  拍脑袋。**残余风险(诚实)**:n=63 单模型,"接地层无用"是方向成立(置信上界 ~9%)非保证;
  有益拦截=0 的前提是模型偏-FAIL 够好,换**更弱**模型可能需重判——回归基准在 `eval_fg/` 常备。
- **url_equals 对整 URL 精确匹配很脆**:浏览器自动补结尾 `/`、查询参数/语言前缀差异都会让"打开首页"类 url_equals false-fail 拖垮整条用例(AE03 实测,LLM 变异产 url_equals 时才暴露)。解:`_check_url_equals` 归一结尾斜杠 + 翻译引导导航锚点优先 `url_contains` 写**稳定 URL 片段**而非精确整串。**导航断言默认用 url_contains,别用 url_equals。**
- **"用例级断言"列里混着不计入裁决的驱动门控观测**:Fix 3 后 `case_assertions` 同时含裁决证据(phase=step/final)和**仅观测**的步骤驱动门控(phase=gate)。读 CLI/前端断言列别把 gate 项当裁决依据——它标了「驱动门控·不计入裁决」;真正决定 PASS/FAIL 的只有 step(确定性锚点)+ final(终态裁判)。`verdict()` 只吃后两者。

## 常用命令

> 本机为 **Windows**(PowerShell)。激活虚拟环境用 `.venv\Scripts\Activate.ps1`,不是 Unix 的 `source .venv/bin/activate`。下面命令按 PowerShell 写。

```powershell
# 环境(首次)
uv venv --python 3.11; .venv\Scripts\Activate.ps1; uv pip install -r requirements.txt

# 测试
.venv\Scripts\Activate.ps1
python -m pytest -q                          # 全量
python -m pytest tests/test_assertion.py -q  # 单文件
python -m pytest tests/test_react_loop.py::test_happy_path_completes  # 单用例

# 格式化(提交前;目录须覆盖 api/storage/codegen)
isort harness mcp_client input intelligence codegen cli api storage tests; black harness mcp_client input intelligence codegen cli api storage tests

# 运行一条用例(CLI 入口)
python cli/run_case.py --excel examples/saucedemo_cases.xlsx --case-id TC101 --base-url https://www.saucedemo.com
python cli/run_case.py --excel <用例.xlsx> --case-id <ID> --spec-only   # 只生成并打印 TestSpec
python cli/run_case.py --check-llm                                       # LLM 连通性自检

# 常用 CLI flags
#   --isolated          playwright-mcp 无持久 profile(防 Chrome 密码泄露弹框,该弹框在 a11y 快照外无法自愈)
#   --headless          playwright-mcp 无头模式
#   --vocab <json>      手动词汇表文件(JSON,{业务词: {role,name,selector}})
#   --tools <yaml>      Custom Tool YAML(LLM 按需调用 + custom_tool 数据断言)
#   --no-skills         不注入内置基础 DomainSkill(默认注入)
#   --context <str>     附加业务上下文(注入 Prompt)

# 公开站点验证示例(已 live 绿)
python cli/run_case.py --excel examples/saucedemo_checkout.xlsx --case-id TC201 \
    --base-url https://www.saucedemo.com --isolated --headless
python cli/run_case.py --excel examples/saucedemo_cases.xlsx --case-id TC101 \
    --base-url https://www.saucedemo.com --vocab examples/saucedemo_vocab.json --isolated --headless
python cli/run_case.py --excel <用例.xlsx> --case-id <ID> --tools examples/custom_tools.yaml \
    --base-url <url> --isolated --headless                               # 接 Custom Tool

# Automation Exercise(更复杂开源用例,含多字段表单+结算流程,尚需 live 跑)
python examples/make_automation_exercise_xlsx.py                         # 生成 xlsx(首次)
python cli/run_case.py --excel examples/automation_exercise_cases.xlsx \
    --case-id AE01 --base-url https://automationexercise.com --isolated --headless

# 启动 API 服务(dev 启动器:--reload 只监视源码目录)
python scripts/serve.py
# ⚠️ 别直接 `uvicorn api.server:app --reload`:codegen 执行通过后写 storage/generated/*.py,
#    默认 reload 监视项目目录会因此重启后端、打断正在跑的 run、令所有请求 pending。

# 种联调数据(saucedemo → demo 项目/版本/任务;幂等,前端 ?project=demo 进入)
python scripts/seed_demo.py

# 启动前端开发服务器
cd frontend && npm install && npm run dev
# 前端构建 / 类型检查(无独立 ESLint;`build` 先跑 `tsc`,即类型检查门禁)
cd frontend && npm run build      # tsc + vite build;提交前用它当类型检查
cd frontend && npm run preview    # 本地预览生产构建

# LLM 配置:.env(项目根,自动加载) 或 env 或 CLI flag
#   LLM_MODEL / LLM_API_BASE / LLM_API_KEY；模型名需带 provider 前缀(如 openai/xxx、ollama/xxx)

# API 路径的等价环境变量
#   CUSTOM_TOOLS_YAML=examples/custom_tools.yaml  → Custom Tool
#   MCP_ISOLATED=1 / MCP_HEADLESS=1               → playwright-mcp 启动参数
#   MCP_SCREENSHOT=0                               → 关截图捕获
#   VOCAB_SCAN=1                                   → 开执行期增量词汇表扫描(**默认关**;主动扫描为主入口)
#   SCAN_CRAWL_DEPTH=1 / SCAN_MAX_PAGES=20         → 主动扫描浅爬深度 / 单次最多扫页数
#   MCP_SETTLE=0                                   → 关「导航类动作后等页面稳定」(默认开)
#   MCP_SETTLE_TIMEOUT_MS=8000 / MCP_SETTLE_INTERVAL_MS=400 → settle 超时/轮询间隔
#   HEAL_VISUAL=0                                  → 关视觉自愈截图双通道(默认开,需多模态模型)
#   AGENT_MAX_STEPS=40                             → API 执行的 ReAct 最大步数(长流程如结算需调大)
#   RUN_MODE=queue                                 → 双进程执行(API 入队,scripts/worker.py 领取);默认 embedded(进程内线程,SSE 实时)
#   PLATFORM_SECRET_KEY=<Fernet key>               → 字段加密密钥(LLM key/Cookie);不设用开发兜底+告警(生产必设)
#   WORKER_ID / WORKER_POLL_INTERVAL / WORKER_STALE_SECONDS / WORKER_MAX_PROJECT_CONC → worker 进程参数
#   HTTP_TOOL_ALLOW_PUBLIC=1 / HTTP_TOOL_ALLOW_HOSTS=a,b → HTTP 型 Custom Tool 放开公网 / host 白名单(默认仅内网防 SSRF)
#   ARTIFACT_ROOT=storage                          → 产物(截图/代码)根目录(M3 换对象存储)

# 平台化双进程(T-P08):API 入队 + 独立 worker 领取执行
python scripts/worker.py            # 起一个执行 worker(可多开横向扩并发);需 RUN_MODE=queue 让 API 入队
```

测试配置:`pyproject.toml` 的 `[tool.pytest.ini_options]` 已设 `asyncio_mode = "auto"`(async 测试无需标记)。
领域模型 `TestCase`/`TestSpec` 及 `TestCaseAgent` 名字以 `Test` 开头,已用 `__test__ = False` 避免 pytest 误收集。

## 目录结构速览

```
T-agent/
├── harness/          # Agent 核心(ReAct/断言/自愈/Prompt/LLM/录制…)
│   ├── hooks.py        #   生命周期 Hook(通用扩展点;参考 Claude Code hooks,平台只提供机制)
├── mcp_client/       # MCP 官方 SDK 封装(stdio 连 playwright-mcp)
├── intelligence/     # Page Intelligence(词汇表 / 用例预解析 / TestSpec 生成)
├── input/            # 输入层(models 结构体 + Excel 解析)
├── codegen/          # 输出层(代码生成)
│   ├── base.py       #   CodeGenerator 抽象 + GeneratedCode 落盘
│   ├── locators.py   #   框架无关稳健定位器解析层(语义 target→Locator)
│   └── bdd.py        #   BDDGenerator(渲染 Locator→pytest-bdd Playwright)
├── api/              # FastAPI 后端(纯 API,:8000;不挂前端静态构建)
│   ├── routers/      #   suites/execution/permission/results/vocabulary
│   └── repository.py #   抽象层 + SQLModel 实现
├── storage/          # SQLModel 模型 + SQLite 持久化(screenshots/ + generated/)
├── frontend/         # React + Vite + Tailwind 控制台(:5173)
│   └── src/
│       ├── pages/    #   ProjectOverview/Tasks(版本→任务树)/SuiteCases/SuiteHistory/SuiteReports/SuiteRunDetail/SuiteSettings/ProjectSettings/Vocabulary
│       ├── components/ # RootLayout/SuiteLayout/IconRail/Drawer/CaseDrawerBody/Sidebar/…
│       └── api/     #   client.ts(API 封装)
├── cli/              # 命令行入口(run_case.py)
├── tests/            # 单元测试(fake/mock 驱动,不连真实 LLM/浏览器)
├── examples/         # 验收入口 + saucedemo 用例
├── 实现规格说明书.md  # 唯一真相源:所有模块详细规格
└── 产品设计文档_v2.0.md # 产品设计原文
```