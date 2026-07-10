# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

> 本文件是**蓝图 + 铁律 + 索引**，不是规格副本。所有细节以 `实现规格说明书.md` 为唯一真相源；
> 这里只给整体认知、不可违反的约束、当前进度和指路。动手前按下方「工作约定」重读对应规格小节。

## 产品一句话

内网 Web 业务测试自动化平台。核心链路：

```
业务用例(Excel) → TestSpec(结构化执行规格+断言) → AI Agent 驱动浏览器执行(playwright-mcp/ReAct)
→ 结构化断言验证(规则引擎,非 LLM 眼判) → 产出 pytest-bdd Playwright 代码
```

## 当前状态:内网真实环境加固(2026-06-24 起)

**阶段化重设计 + 执行线 7 阶段走查已收官**(2026-06-22→24):翻译/执行/裁决全部推翻重做成
阶段化(翻译只产意图不接地 + ReAct 盯目标 + 逐阶段 Validator),旧「铁律」已全部清除、不作约束。
现转入**内网真实环境加固**主线——拿真实/替身站点跑,逐个挖暴露的根因(快照可观测性、HMI 指纹、
翻译质量等)。最新进展见下方「重大 redesign 实施记录」最近批次。

**约束以「产品设计文档最新版 + 当前对话拍板 + 本文件最近批次」为准**,不要拿旧铁律(含本文件
早期 git 历史里的版本)反推现状。当前没有重新沉淀一套静态「铁律」——约束随主线演进,写在最近批次里。

## 架构大图(需要读多文件才能拼出的部分)

单条用例的执行由 `harness/agent.py::TestCaseAgent.run()` 总装,串起以下模块：

- `intelligence/pre_analysis.py` — TestCase → **阶段化 TestSpec**(纯 LLM 翻译,**只产意图不接地**,2026-06-22 重设计)。产出 `{intent, preconditions:[str], phases:[{steps:[str], expected}]}`:每阶段 = 一组自然语言步骤(驱动)+ 一条组级预期(验证依据)。不写 selector/不锁动作/不猜元素,接地全交运行时。坏输出降级为单阶段无损映射。契约见 `docs/test_spec_v2.md`。**业务知识接入(2026-06-27)**:`build_spec_messages(case, knowledge=)` 把项目级**「用例规范」**(`project.translation_knowledge`,前端侧栏「用例规范」页维护)作"理解用"背景注入翻译——助补全隐含步骤/对齐术语/写对 expected;两条护栏:①仍不写 selector/不锁动作(知识≠接地);②expected 仍只写页面真实可观察、本阶段直接产生的状态,不把指南理想态当"页面必现"(防脑补→误判)。来源链:CLI `--knowledge <file>` / `run_executor` 读项目字段。看 prompt:CLI `--dump-spec-prompt` 或前端用例抽屉「查看翻译 prompt」(`GET /suites/{id}/cases/{cid}/spec-prompt`)。见 [[translation-knowledge]]。
- `harness/step_plan.py` — 阶段化 TestSpec 的 `phases.steps` **摊平**成扁平步骤状态机(pending/active/done/...)+ 记每步所属阶段;暴露 `mark_step_done` 工具 + **阶段边界**查询(`is_phase_last_step`/`phase_last_step_no`)。
- `harness/prompt.py` — System Prompt **分层**(Base+Context+Task+Tools),`PromptBuilder.build(step_plan)` 每轮重算反映进度。Task 层渲染 `intent`/`preconditions`/阶段化步骤清单,**不渲染 expected**(FG01:验证依据绝不进驱动)。
- `harness/react_loop.py` — **ReAct 主循环**。Reason→Act→Observe;护栏:循环检测、max_steps、哑火续推(`max_idle_nudges`)、tool_call 容错、**过早 mark_done 软护栏 两分支**(E2:没操作 / 操作无效果——后者用页面指纹 URL+ref 集判,无 LLM)、**单步定位失败预算**(`STEP_FAIL_BUDGET` 默认 10 → `STEP_FAILED` 快速失败,E2 由 3→5;2026-06-29 5→10 治内网脏 live SPA nav-click 超时恢复被掐断)、**步级卡住主动提醒**(E2:同步连续 N 轮 fp 未变 → 注入诊断引导 + E3 浮现命中 skill 名催 `load_skill` 甲 / 持续仍卡 → 平台 `auto_load` top1 注入兜底 乙)、**跨 phase 重置**(E2:进新 phase 清零 idle/loop)。**观察以 user 消息文本回灌**(不依赖 tool_call_id 配对)。**阶段边界 Validator**(2026-06-22 取代步骤门控):`on_phase_end(phase_index) -> str|None` 在某阶段**最后一步** mark_step_done 落定时触发,核验该阶段 expected——返回非空原因 = 未达成 → `PHASE_FAILED` **阶段失败即失败**(不 replan/重试)。**哑火可观测(2026-06-24)**:`ReActResult.idle_outputs` 记每个哑火轮 `{iteration,step_no,kind,rechecked,text}`——`kind` 三态 narration_only(纯叙述放弃)/ malformed_tool_call(调了但格式坏)/ premature_result;透到 `metrics.execution.idle_outputs` 落库 + CLI 打印,供"卡死类"失败事后定性(模型放弃 vs 流式丢采)。**参数归一(2026-06-24)**:`_normalize_ref_target` 在 dispatch 前给 browser_* 工具把 `ref`/`element_ref`/`ref_id` 补进 `target`(本版 playwright-mcp 用 `target` 装 ref,模型先验是 `ref`、会抖动),消白烧步骤。
- `harness/llm.py` — LiteLLM 封装 + tool_call 容错 + token 统计。配置走 env(`LLM_MODEL`/`LLM_API_BASE`/`LLM_API_KEY`)。**tool_call 容错链**:①标准 `tool_calls` 字段 → ②宽松 JSON 修复 → ③从 content 提取(`<tool_call>` 标签 / ```围栏 / 裸 JSON / **`函数名({...})` 文本** ← 2026-06-24 治 deepseek-v4-flash 偶发把调用吐进 content 文本而非 tool_calls 通道,实测 TC201 哑火卡死根因)→ ④纠偏重试 1 次 → 仍失败抛 `LLMToolCallError`。`extract_verdict` 从坏 JSON 正则捞 PASS/FAIL(裁决解析卫生)。**未设 `max_tokens`**(judge 偶发因 provider 默认输出上限截断 → 层1 兜底捞回;已加 prompt 简短约束减少触发)。
- `mcp_client/client.py` — MCP 官方 SDK(stdio)连 playwright-mcp;工具格式 MCP↔LiteLLM 转换。
- `harness/page_probe.py` — 解析 playwright-mcp 的 `browser_snapshot`(YAML A11y 树)为节点,按语义 target 双向包含匹配(`MCPPageProbe` 实现断言引擎的 `PageProbe` 协议)。
- `harness/assertion.py` ★ — **断言引擎 + 阶段 Validator**。`_check_llm_judge`(**偏-FAIL** + 要求模型逐字引证页面 evidence,evidence 仅作**可审计依据**写入 reason)是阶段 Validator 的核心;另留 DOM/文本/URL/custom_tool 确定性检查 + healer 重定位 + `verdict()`(化石,阶段化下不再被 agent.run 调用)。裁决保留两道**与模型独立**的底线:**层(1)解析卫生**(`extract_verdict` 正则捞 verdict / 无 verdict→FAIL,fail-closed)+ **G1 主裁决缺失三态→FAIL**(未接 LLM / 调用失败 / 解析不出 verdict)。**〔2026-06-24 撤销「平台确定性证据接地推翻」(用户拍板①)〕**:eval_fg A/B 扩样(n=63,3 站点,6 轮)实测接地层有益拦截恒为 0、仅偶发误伤 → 净 ≤0,偏-FAIL prompt 自身已扛住全部 false-green;**裁决权交回模型,evidence 不再作推翻闸门**(删 `_norm_evidence`/`_evidence_*`/`_expected_*` + 锚点正则 + E5)。回归基准见 `eval_fg/`。**2026-06-22 阶段化重设计**:`agent.run::on_phase_end` 在某阶段最后一步落定时,于**当时所处页面**对该阶段 `expected` 跑一次 `_check_llm_judge`(实时 URL 作免费锚点喂模型),逐阶段裁决;**取代**旧的「步骤门控 + 终态 verify_all」三处验证。结果按 `phase_index`+`expected` 落库,前端按阶段展示。〔删旧 `_gate_step_done` 偏-PASS 门控、`step.expect` 结构化锚点、终态用例级 assertions。〕
- `harness/healing.py` — **Healing Subagent**(独立 context)。断言侧:重定位断言目标;操作侧:工具报错时重定位并把建议回灌 ReAct。P1 角色→P5 视觉,防臆造(候选必须落在快照里)。
- `harness/context.py` — **Context Compact**。发 LLM 前压缩:旧观察折叠成一行(L1)、近期快照按关键词相关度截断(L2),治 token 膨胀。
- `harness/recorder.py` — 汇总 `ExecutionRecord`;`to_history()` 把 model_output / action_result 分离序列化。
- `harness/hooks.py` — 生命周期 Hook(before_case 失败→用例 FAIL 不进 Agent)+ 共享 `ExecutionContext`。
- 〔`harness/session.py` **2026-06-24 删除**:其唯一残留 `make_mcp_credential_login` 仅供已退役的主动扫描登录用,随扫描子系统收缩一并删。早先(2026-06-18)已删 `SessionManager`/`LoginHook`/`CaptureSessionHook`/`make_mcp_cookie_*` + `SessionProfile`(Cookie 复用对 SPA/Token 型登录不对症、TTL 与真实会话寿命脱节);登录态跨用例复用交后续「环境管理」主线。`harness/hook_builder.py` 同期删除,`Suite.session_profile` 及表经 alembic `0002` 降除。〕
- 〔`harness/precondition.py` 预置条件三分类器 **2026-06-22 删除**:阶段化重设计后预置条件退化为纯背景 `list[str]`(不执行、不 guard、不分类),原 state_hook/action_step/ambiguous 三分类 + 标黄确认端点一并退役。〕
- `harness/skills.py` — Skill 体系(**2026-06-15 对齐 Anthropic/Codex 标准 Skill**;**E3 2026-06-23 三层加载**):单一 `Skill`(name+description+content),**渐进披露**——System Prompt 常驻 `name—description` 清单,LLM 判断相关时**主动调 `load_skill(name)` 工具**展开正文(主路,由 E1 的 BASE_PROMPT 引导)。`DEFAULT_SKILLS` 内置基线 `preload=True`(表单操作/结果定位 + E3 加的「重新快照拿新 ref」「找不到元素的常见原因」机械套路);项目 Skill `preload=False` 走真渐进加载(E3 停 `api/run_executor.py` 的 `preload=True` force-preload TODO)。E3 加 `SkillManager.relevant(step_text, top_k)` 按 token 重叠挑相关 skill(确定性、无 LLM)+ `auto_load(step_text)` top1 直接加载;ReActLoop 在卡住时按 `stuck_round_budget` 触发**甲层**(浮现命中 skill 名催加载)、`*2` 触发**乙层**(平台 auto_load 注入)。〔删旧 DomainSkill/PageSkill/ToolSkill 三类与 URL/关键词平台侧匹配——加载与否改由 LLM 决策 + 卡住时平台兜底。〕
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
落地模块见上「架构大图 › 平台化(M1)」小节(Postgres / API·worker 双进程 / 项目→版本→Suite→Run /
三角色 RBAC / HTTP Custom Tool / 项目级 LLM 配置)。约束:**单机 CLI 路径保留不回归**;K8s 延后到 M3。

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

> 详细逐批次记录已归档到 `docs/实施记录.md`(每次会话不再全量载入)。下面只留**最近批次全文**
> + 历史**一行索引**(倒序,★=重大架构决策);要看某批次全文去归档文件查。

**最近批次(全文)**

- **SVG/HMI 工控加固 + 执行选 skill + prompt review 批次(2026-06-27 周末收尾)** ★ — 用户内网
  「工艺模拟器(主要 SVG 画)」用例一条都没完整跑通、各类失败。约束:**内网数据不可外泄**。改用
  **公开/本地替身复现失败类**(见 [[public-analogue-debugging]] + 工程经验条),在替身上挖出并修
  两个真根因 + 配套特性/清理。**关键认知:用户内网模型是 qwen3.5-397B(强模型),失败大概率是
  页面可观测性而非模型能力。**
  - **SVG 可点元素纳入快照截断保留(`025309e`)** — 内网 SVG 工艺图的泵/阀,playwright-mcp 表达成
    `generic [ref] [cursor=pointer]`(role 非 button),旧 `context._is_interactive` 按 role 判 →
    长页面截断时当噪声丢 → 模型拿不到 ref → "找不到元素"。改:带 `[cursor=pointer]` 的行一律保留。
    本地 SVG HMI live 复现验证。见 [[snapshot-truncation]]。
  - **页面指纹纳入文本内容摘要(`4f2e3ab`)** — 工艺/HMI 核心变化常是 `<text>` 原地改值(液位
    72%→80%),DOM 结构/ref 集都不变 → 旧「URL+ref 集」指纹无感 → 误报「操作没生效」软护栏 →
    模型反复 snapshot 空转。`react_loop._fingerprint` 加一维:剥 ref/cursor 等易变标记后的可见
    文本哈希。本地 HMI live 实测该用例 **27 步→13 步**。
  - **执行时多选指定 skill 强制加载(`5f3954e`+`5c7602a`)** — 点「执行」弹框选项目 skill,勾选的
    本次**强制常驻 prompt**(preload=True,不等弱模型自己 load_skill),未勾仍渐进披露;一次性随
    本次 run。后端 `execute_run(force_skill_names=)` + `trigger_run` 收 `RunOptions.skill_names`,
    embedded 透传 / queue 落 `run_queue.skill_names`(迁移 `0003`)+ worker 透传;前端弹框
    (z-[60] 盖抽屉)。无项目 skill 则直跑不弹。
  - **执行 prompt review(`081647b`)** — BASE_PROMPT 加正例锚点(INTENT+真发调用的正确形状)+
    收紧三反例(没真发调用的三种长相)+ 去重(已满足别多疑/诊断清单/[观察]回灌各 2→1);docstring
    清 load_skill 化石(早移到 SkillManager.render)。react_loop/assertion 清「证据接地」化石注释
    (该层 2026-06-24 已撤)。saucedemo TC101 live PASS。
  - **翻译 expected 去歧义(`cd2ce18`)** — 示例「Products 标题」歧义(裁判误当文档 `<title>`=Swag
    Labs 判 FAIL)→ 改「页面出现文案 Products」+ 明确禁用"标题"二字;正例收到 2 锚点(合规铁律②)。
  - **复现工具入库(`1185a8d`)** — `scripts/diag_svg_snapshot.py`(并排 dump a11y 快照 vs DOM
    遍历,看"元素在 DOM 却没进快照"gap)+ `storage/diag_svg_hmi.html/.xlsx`(最小 SVG 工艺图样例
    + live 用例);`storage/*.log` 加忽略。
  - **下周继续(接力点)**:① **用户内网拉最新代码验证** SVG 工艺用例(上两个根因应缓解"找不到/
    点不动");按非敏感信号(停因+死在第几步+那步目标)反馈,继续挖第三个根因。② **纯颜色状态灯**
    (无文字)a11y 永远表达不了 → 裁决层需 `JUDGE_VISUAL=1` 多模态(qwen3.5 若支持图像)或用例规范
    里别拿颜色当 expected——这是已定位未做的下一个点。③ 可选:对 `demo.thingsboard.io` 写真用例继续
    挖(登录+表单+仪表盘 widget)。④ Codex 工作约定新增「编码行为准则」(Karpathy 4 原则)已落地,
    后续编码遵循。

**历史批次索引(全文见 `docs/实施记录.md`,倒序)**

- ThingsBoard 工业 SPA 替身战役(2026-06-28→30,一串落地) ★
- 内网验证批次(2026-06-27,一串落地)
- SSE 进度统一到 run_event 表 + 前端重连(2026-06-26,已落地) ★
- 健壮化批次(2026-06-25,一串落地)
- 词汇表扫描子系统收缩(2026-06-24,用户拍板 B,已落地)
- 阶段⑦ codegen → scanning 走查(走查结论=设计健全、不 redesign,2026-06-24)
- 阶段⑥ 收尾 Hooks 走查 + 清理(2026-06-24,已落地 H1+H2+H3)
- 阶段⑤ 合并裁决 + 执行完整性闸门 redesign(2026-06-23,已落地 G1+G2) ★
- 阶段④ asserting 撤销(走查结论=取消,2026-06-23,已落地 F1+F2)
- 撤销「证据接地推翻」+ eval 扩样回归基准(2026-06-24,用户拍板①,已落地) ★
- TC201 结算流哑火卡死调查 → 三连修(2026-06-24,已落地)
- 执行健壮化(stage③ ReAct 主循环 redesign,2026-06-23,已落地 E1-7) ★
- 翻译/执行/裁决阶段化重设计(2026-06-22,已落地 FP0-3) ★
- UI Redesign (TestSprite 风格,已落地一轮)
- 真实环境验证加固(进行中)
- 抽屉可观测性 + 产物落地 + UI 收口(已做)
- 执行中实时反馈(已做,修执行期抽屉空白)
- 词汇表全链接通 + 可观测(已做)
- 执行态可观测收口(2026-06-05,已做)
- 空壳模块接通(2026-06-06,已做)
- Custom Tool + 数据断言接通(2026-06-06,已做)
- ReAct 卡死修复 + 首轮真实 live 验证(2026-06-07,已做)
- prompt 优化(2026-06-07,已做)
- 公开站点验证战役 + 真 bug 修复(2026-06-07,已做)
- 定位器对齐(2026-06-07,已做)
- codegen 闭环验证 + 导航修复(2026-06-07,已做)
- llm_judge / 词汇表 base_url / 主动扫描(2026-06-10,已做,用户拍板三连)
- 翻译阶段提速 + 铁律2 重订 + 断言去冗余(2026-06-10,从内网「翻译总超时」问题展开)
- 裁决哲学转向:LLM 裁判为主 + 确定性锚点(2026-06-17,据真实公网评测重订铁律 2/4)
- Fix 3 = 实装新裁决架构(2026-06-17,已做)
- Fix 3 收尾加固(2026-06-17,已做)
- A-2 裁判可靠性评测 + 证据核验校准(2026-06-18,已做)
- 会话/Cookie 复用退役 + Hook 回归通用扩展点(2026-06-18,已做,用户拍板)

- **下一步候选(2026-06-07 收口时的待办):**
  - **Fix 3 = 实装新裁决架构** — ✅ **已做**(含收尾加固 + A-2 评测校准,见上「实施进度」Fix 3 / A-2 条目)。**剩余可选**:⑤ 继续用 `eval_fg/` 扩样(换 Cisco/ThingsBoard + **第二模型**,需另配 LLM 凭据)把 false-green/false-fail 率做到更紧的可信区间(A-2 已在 deepseek×26 条上测得 0/0,但样本偏小、单模型)。
  - **真实内网用例 live 验证**(主线,当前被环境阻塞)— saucedemo 全链路已 live 绿(基础/结算/会话复用/custom_tool/codegen 回放),内网真实业务系统待跑。解阻塞后 CLI/API 两条路径都就绪。
  - **prompt 优化 C/D(省 token/提速,未做)** — C:每业务步约 3 次 LLM 往返(snapshot→action→mark_done),~100k token/用例,可探索「动作结果已带快照则免单独 snapshot」「mark_done 合并」(有正确性风险,实测 click 结果不总带 ref,需 live A/B);D:system 每轮重列全部 ~25 工具文本(已另经 `tools=` 传,冗余),可用 `PromptBuilder.max_tools` 按相关度截断(风险:漏掉所需工具)。已做的 prompt 优化见「prompt 优化(2026-06-07)」:BASE 强制每轮工具调用 + idle 指令修正,live 0 卡死。
  - **断言目标定位器对齐(遗留,部分缓解)** — codegen 的 action 定位器已对齐(执行捕获),但**断言目标**(如购物车角标)不经"执行定位器"产出,无 vocab/selector 时仍文本兜底。需在 vocab/selector 层对齐(断言走 probe,naming 随 LLM 漂移、子串匹配也常错过)。**2026-06-15 缓解**:`text_equals/text_contains` 在**元素定位失败 + 自愈也没救回**时,加一档**全页文本兜底**(`AssertionEngine._text_page_fallback`)——整页快照里确定性搜 `expected` 子串,命中判 PASS 但 reason 标「全页文本兜底」可审计区分。**护栏**(防短串误绿,贴铁律2「宁可误报失败」):① 有显式 `selector` 不兜底(selector 失败是真信号,如空购物车);② `expected` 须够独特(含空白短语或长度≥5,排除 "1"/"2"/状态短词);③ 放在**自愈之后**,优先元素级/自愈精确绿。治「业务词名(中文「成功提示区域」)对不上英文页面元素 → 流程跑完仍 false-fail」(saucedemo 结算「Thank you for your order!」实证)。
  - 阶段五(用例管理平台集成,规格"现在不做")。
- 单测数量以 `python -m pytest -q` 实跑为准(2026-06-17 Fix 3 + 收尾加固后约 526 passed;**2 个预存在失败**:`test_recorder` 截图目录 + `test_tools` 命令替换(均 Windows 平台,与业务逻辑无关)。`test_healing` 视觉自愈 3 个此前在全量顺序下因 vision 缓存跨用例泄漏失败,现工作树已修)。

T-xx ↔ 规格小节对照见 `实现规格说明书.md` §5(各模块详细规格)与 §6(实施计划)。

## 工作约定

### 本机执行环境约束

- **不要使用沙箱执行命令**:本机环境不支持沙箱模式,涉及 git 写操作、依赖安装、测试、浏览器/服务启动、文件生成等动作时,应直接走非沙箱执行/审批;若平台默认沙箱导致失败,不要绕路折腾,立刻说明原因并改为申请非沙箱权限。

### 行为准则(给"写本项目代码的 AI",据 Karpathy coding 观察沉淀)

> 来源:`multica-ai/andrej-karpathy-skills`「give success criteria rather than imperatives」。
> 其运行时哲学(给成功标准而非逐步命令)我们产品架构已天然对齐(翻译只产意图/阶段 expected +
> ReAct 盯目标收敛,见 2026-06-22 阶段化重设计);下面 4 条是约束**编码本身**的,正对本项目反复
> 踩的坑("有模块≠接通"、化石堆积):

- **动手前先想、有歧义先暴露**:显式说出假设;有多种解读就摆出来、不闷头猜一种实现下去;
  发现更简单的做法要讲;看不懂的地方停下来点名,别绕过。设计点不确定先问用户(已有约定的强化)。
- **简单优先**:不写没要求的功能/参数/抽象;别为想象中的未来留扩展点(本项目「不过度设计未来阶段」
  的同义);不给不可能发生的场景加防御;200 行能 50 行就重写。判据:资深工程师会不会嫌它过度设计。
- **外科手术式改动**:改既有代码只动该动的——不顺手"优化"邻近代码/注释/格式、不重构没坏的东西、
  跟随既有风格;只清你这次改动**新产生**的孤儿 import/变量;预先存在的死代码除非任务要求否则不碰
  (成体系的化石清理另起独立任务,如扫描子系统收缩,不夹带)。
- **模糊任务先转成可验证目标**:把"加个校验"这类指令先落成可测的成功标准 + 多步计划的验证检查点,
  再动手——标准够硬才能自主收敛、少来回澄清(这也正是我们对**运行时 agent** 的要求,编码同理)。

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
  改动较深时再加跑完整结算 `examples/saucedemo_checkout.xlsx` TC201(`--max-steps 60`,11 业务步,验长流程);只改翻译/分类时可用 `--spec-only` 快速验 spec 质量不退化(不跑浏览器、更快)。env(LLM)走项目根 `.env`。**这条 live 冒烟是 `AGENTS.md` 约定的标准验证步骤,大改动不可只凭单测交付。**
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
- **内网数据不可外泄时,用同技术栈「公开/本地替身」复现失败类**(2026-06-27,见 [[public-analogue-debugging]]):用户内网用例总失败但**任何内网信息都不能给**→"帮你 debug 具体 run"走不通。打法:问**非敏感的技术栈**(前端框架/SVG 还是 canvas/失败的抽象信号:停因+死在第几步+那步目标)→选或造同栈替身(工控类:`demo.thingsboard.io` 账号 tenant/tenant 有登录+表单+仪表盘;或自造本地 SVG HMI 起 http.server 喂 playwright-mcp)→`scripts/diag_svg_snapshot.py` 并排 dump「a11y 快照 vs browser_evaluate 遍历 DOM」一眼看出"元素在 DOM 却没进快照"的 gap→在替身上修+单测+live→交还内网验。**认知校正:用户内网模型是 qwen3.5-397B(强模型),"找不到元素/哑火"大概率是页面可观测性而非模型能力。SVG 元素在 DOM、够得着(playwright-mcp 把带文字+cursor 的 g 表达成 generic[cursor=pointer] 给 ref);canvas 才是真盲区(死位图,只能视觉通道)。** 此法当场挖出两个真根因:SVG `generic[cursor=pointer]` 被截断丢(`025309e`)、指纹漏判数值读数原地刷新(`4f2e3ab`,本地 HMI 27→13 步)。
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
- **子串匹配失败标志会误伤**(2026-06-25 血泪):`_is_tool_failure` 用裸子串 `"Timeout"` 抓超时报错,结果误中 `browser_wait_for` 成功结果里的 `setTimeout(...)` → 每次按时长等待都被判工具失败 → STEP_FAILED,"等待 N 分钟"类步骤 100% 失败。教训:**失败标志别用会嵌进正常文本的裸子串**,用词边界/负向后瞻(`(?<!set)timeout`)或更具体措辞。排查时一度误判成 120s 超时,实测 playwright-mcp 单次 wait 内部 ~30s 就返回(等不满)——所以"按时长等待"要在执行器**分段累积**才能真等满(`agent._chunked_wait`)。**〔2026-06-29 同类更广根因 + 结构性根治〕**:`setTimeout` 只是冰山一角——`_is_tool_failure` 是拿**整段观察(含 playwright-mcp 随成功结果返回的整页 a11y 快照)**去匹配 `not found`/`no element`/`error`/`timeout`,所以**只要页面内容里出现这些词**(内网工艺模拟器报警/状态文本"Timeout"/"Error"/"Not Found"高发),每帧观察都被误判工具失败 → 跑 healer 把"重定位建议"回灌 → **模型以为 `browser_wait_for` 一直失败、反复检查参数重试**(死循环)+ 累加 STEP_FAILED。根治两层:**①主信号改用 MCP 结构化 `CallToolResult.isError`**(权威、不受页面内容影响),透过 `ToolOutcome.is_error` 到 `_outcome_failed`;**②字符串 marker 降为兜底,且先剥 ```yaml 围栏(页面快照)只扫错误信封**(`_strip_snapshot`)。教训升级:**失败判定要信工具的结构化失败标志,别拿"会同时出现在页面内容里的词"扫整段观察**——干净站点(saucedemo)永远不暴露,脏的真实工艺页必爆。
- **单行 megablob 绕过行截断 → 上下文撑爆硬崩**(2026-06-29 血泪,thingsboard.cloud 替身实测):Context Compact 的 `truncate_snapshot` **只按行数截断**(`len(lines)<=max_lines` 原样返回)。真实系统(尤其工业/IoT)的网络响应体常是**压缩 JS / 巨型 JSON,整坨一行没换行**——模型登录失败陷入调试螺旋、`browser_network_request` 拉回一个 MB 级单行 blob → 行数=1 ≤ 40 → 截断**空操作** → 单条观察就是几十万 token → 冲破 1M 上下文 → `ContextWindowExceededError` **整条 run 不可恢复崩溃**(实测 1.25M tokens)。根治:`truncate_snapshot` 加 `max_chars` **硬字符上限**——①先把超长**单行**就地砍短(`_cap_long_lines`,治 megablob)②拼好结果再兜一道总字符上限(`_cap_total`,末位安全阀);`ContextCompactor.hard_char_cap`(env `OBS_HARD_CHAR_CAP` 默认 12000)在 L2 传入。教训:**任何"截断/压缩"逻辑都要有一道不依赖输入结构(行数/字段数)的硬字节上限兜底**——按行截就会被无换行单行绕过,按字段截就会被巨型单字段绕过。又一条"干净站点(saucedemo 短页)永不暴露、脏真实系统(压缩 bundle 响应)必爆"。
- **embedded 模式 run 命绑 API 进程,中断=僵尸+丢记录**(2026-06-25):embedded 执行跑在 API 进程的守护线程里,**进程/线程一中断**(终端/SSH 断、OOM、手动重启、setup 阶段异常逃出 try)就留下 DB `running` 僵尸 + `/result` 全空(记录是用例跑完才落库)。诊断指纹:**"卡死 running 的 run 一触发新执行就变 failed"** = 僵尸收尾(它不在内存 `_sse_queues` 里 → 说明进程已中断过)。`execute_run` 已加最外层兜底(任何异常标 failed + 占位记录 + 必发 suite_done);但**根治是 queue 模式**(`RUN_MODE=queue` + `scripts/worker.py`,run 不在 API 进程里、抗重启、worker 崩溃 stale 回收重跑)。**关浏览器/退页面不影响执行**(执行在服务端;唯一杀 run 的是 API 进程停)。**审批模式**例外:关页面没人批 → run 卡审批处。

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
#   --no-skills         不注入内置基线 Skill(DEFAULT_SKILLS,默认注入)
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

# ThingsBoard 工业 SPA 替身(脏 dirty-SPA 失败类挖掘;凭据走 env、xlsx 不入库)
#   先注册一个 thingsboard.cloud 租户账号(公网服务,用一次性密码),再生成 xlsx:
$env:TB_EMAIL="<你的邮箱>"; $env:TB_PASSWORD="<你的密码>"; python examples/make_thingsboard_xlsx.py
python cli/run_case.py --excel examples/thingsboard_cases.xlsx \
    --case-id TB04 --base-url https://thingsboard.cloud --isolated --headless --max-steps 100
#   TB04=综合复杂流程(登录→设备表→HVAC 行钻取详情+遥测→仪表盘列表→等待 3 分钟→存活,13 步);
#   TB05=硬交互探针(点 mat-table 行)。canvas/SVG widget 内部值是 a11y 盲区,expected 只写可观察证据。

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
#   〔VOCAB_SCAN / SCAN_CRAWL_DEPTH / SCAN_MAX_PAGES 已删除(2026-06-24 扫描子系统收缩)〕
#   MCP_SETTLE=0                                   → 关「交互类动作后等页面稳定」(默认开;导航类已不 settle,Playwright 自动等 load)
#   MCP_SETTLE_TIMEOUT_MS=8000 / MCP_SETTLE_INTERVAL_MS=400 → settle 超时/轮询间隔
#   PHASE_SETTLE_TIMEOUT_MS=2000                   → 阶段末尾 Validator 判前 settle 短超时(默认 2s,防动态页白烧 8s)
#   MCP_VIEWPORT=1920,1080                         → 浏览器视口(默认放大 1920×1080,治窄视口把按钮收进汉堡菜单;设 0/空 回 playwright-mcp 默认 1280×720)
#   OBS_MAX_CHARS=4000 / SNAPSHOT_MAX_LINES=150    → 驱动侧(喂模型)A11y 快照截断旋钮(超字符触发/保留行数);截断优先保留可交互元素行,内网密集 SPA 藏元素时调更大(默认 150 治企业级密集页设备详情/表格被截致硬交互看不到元素)
#   OBS_HARD_CHAR_CAP=16000                        → 单条观察硬字符上限(末位安全阀,封压缩 JS/巨型 JSON 单行 megablob 撑爆上下文)
#   JUDGE_SNAPSHOT_LIMIT=9000                      → 喂【裁判】的 A11y 快照字符上限(按期望锚点窗口截断,与驱动侧两条独立路径)
#   WAIT_MAX_SECONDS=300 / WAIT_CHUNK_SECONDS=20   → browser_wait_for 按时长等待:上限(默认 5min)/ 分段时长(<内部 ~30s 上限)
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
│   ├── hooks.py        #   生命周期 Hook(通用扩展点;参考 Codex hooks,平台只提供机制)
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