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

## 铁律(违反即错,必须常驻)

1. **浏览器层只用 playwright-mcp 的 stdio 模式,绝不用 CDP HTTP**(内网代理会拦截 → 504)。
2. **LLM 眼判分两种角色,边界要守清(2026-06-10 重订)**:
   - **(a) 驱动执行 = 鼓励用**。执行中靠 LLM「看快照判断这步成没成、要不要重试、冒出弹窗怎么绕」来随机应变——这是 Agent 的核心价值,治偶发噪声(弹窗/加载慢/改版/多一步确认),**让执行健壮**。这类眼判**不进最终裁决**,只负责「想办法走到终态」。中途若要加确定性校验,也必须是**软的、可恢复的**(不过 → 自愈/重试/绕行),**不能是一票否决的硬闸门**(那会把健壮性弄丢)。
   - **(b) 裁定 PASS/FAIL = 以规则引擎确定性验证为主**。判断在"翻译时"一次性做(预期→结构化 Assertion),执行后只做确定性比较。**`llm_judge` 作为降级链最末档显式兜底**(无法结构化/查真值时),接 LLM 真判 PASS/FAIL 并**计入裁决**(方案A,已选择信任),但标 `ai_judged`(低置信、报告与结构化绿区分,使 false green 可见可回溯);裁判 prompt 偏向 FAIL(宁可误报失败不可误报通过)。**能用 DOM/文本/URL/custom_tool 的预期一律不准落到 llm_judge**。
   - **守住的底线**:不让 LLM **不可见地/默认地**决定主裁决(结构化能判的不准交 LLM、AI 判绿必须标记);也不取 LLM 自报的 `TEST_RESULT`。〔方案A 由用户拍板,推翻原"绝不让 LLM 眼判 PASS/FAIL"的绝对表述——禁的是「LLM 不可见地刷绿」,不是「LLM 驱动执行」或「显式可审计的兜底裁决」。〕
3. **本地 LLM 的 tool_call 必须容错**(宽松 JSON / 从 content 提取 / 重试),偶发格式错误不得搞崩 ReAct 循环。
4. 最终 PASS/FAIL **以断言裁决为准**(含 `llm_judge` 兜底结果),**不取 LLM 自报的 TEST_RESULT**。
5. 实现原则(规格 §0):前后端分离、数据层抽象(SQLModel,不直接写 SQL)、输入/输出抽象(都产出 `TestCase`/落 `ExecutionRecord`)、核心表预留 `updated_at`/`owner`/`external_id`、分阶段不跳跃。

## 架构大图(需要读多文件才能拼出的部分)

单条用例的执行由 `harness/agent.py::TestCaseAgent.run()` 总装,串起以下模块：

- `intelligence/pre_analysis.py` — TestCase → **TestSpec**(纯 LLM 翻译,阶段一无词汇表)。坏输出降级为朴素映射。
- `harness/step_plan.py` — TestSpec.steps → **StepPlan** 状态机(pending/active/done/...),暴露 `mark_step_done` 工具给 LLM。
- `harness/prompt.py` — System Prompt **分层**(Base+Context+Task+Tools),`PromptBuilder.build(step_plan)` 每轮重算反映进度。
- `harness/react_loop.py` — **ReAct 主循环**。Reason→Act→Observe;护栏:循环检测(连续 3 次同调用)、max_steps、哑火续推(`max_idle_nudges`)、tool_call 容错终止、**单步定位失败预算**(同一业务步累计定位失败达 `step_fail_budget`/env `STEP_FAIL_BUDGET` 默认 3 → `STEP_FAILED` 快速失败,标明卡死步,治"点错前序元素致后续找不到目标却磨到 max_steps")。**观察以 user 消息文本回灌**(不依赖 tool_call_id 配对,本地模型更稳)。`on_step_done(step_no)` 回调在某业务步 mark_step_done 落定时触发(供步骤级即时验证)。
- `harness/llm.py` — LiteLLM 封装 + tool_call 容错 + token 统计。配置走 env(`LLM_MODEL`/`LLM_API_BASE`/`LLM_API_KEY`)。
- `mcp_client/client.py` — MCP 官方 SDK(stdio)连 playwright-mcp;工具格式 MCP↔LiteLLM 转换。
- `harness/page_probe.py` — 解析 playwright-mcp 的 `browser_snapshot`(YAML A11y 树)为节点,按语义 target 双向包含匹配(`MCPPageProbe` 实现断言引擎的 `PageProbe` 协议)。
- `harness/assertion.py` ★ — **断言规则引擎**。阶段一支持 DOM/文本/URL;元素找不到标 `healable`;接 healer 做目标重定位复验;`verdict()` 裁决(任一 FAIL 即不通过,全 skipped 不算可信通过)。**断言验证分两处(2026-06-15,治内网"按步预期"用例)**:**步骤级 `step.expect`** 在该步 mark_step_done 落定时于**当前子页面**即时验证(`agent.run` 的 `verify_step` 回调,经 `on_step_done` 接进 ReAct);**用例级 `assertions`** 在**终态**验证;两者合并裁决(`step_assert_pairs` + `terminal_results`)。根治"中间子页面的预期被攒到终态页验 → 元素已不在 → 假阴性"。结果按 `phase=step/final` + `step_no` 标注落库,前端断言列标「步骤N」。
- `harness/healing.py` — **Healing Subagent**(独立 context)。断言侧:重定位断言目标;操作侧:工具报错时重定位并把建议回灌 ReAct。P1 角色→P5 视觉,防臆造(候选必须落在快照里)。
- `harness/context.py` — **Context Compact**。发 LLM 前压缩:旧观察折叠成一行(L1)、近期快照按关键词相关度截断(L2),治 token 膨胀。
- `harness/recorder.py` — 汇总 `ExecutionRecord`;`to_history()` 把 model_output / action_result 分离序列化。
- `harness/hooks.py` — 生命周期 Hook(before_case 失败→用例 FAIL 不进 Agent)+ 共享 `ExecutionContext`。
- `harness/hook_builder.py` — **Hook 组装入口**。`build_session_hooks(profile, mcp)` 按 Suite 绑定的 `SessionProfile` 组装含 `LoginHook`(before_case,有效 Cookie 注入/放行)+ `CaptureSessionHook`(after_case,用例通过后抓 Cookie 落盘)的 HookManager;无 `login_aw` 时 `optional=True` 放行让 Agent 自行登录。API 路径(`execution.py`)调用此模块把 Hooks 接进每个用例 agent。
- `harness/session.py` — `SessionManager`(Cookie 存盘+有效期,跨用例共享)+ `LoginHook`(有效复用、过期跑 login_aw 重登)+ `CaptureSessionHook`(after_case 抓 Cookie)+ `make_mcp_cookie_injector/capturer`(基于 `browser_run_code_unsafe`)。
- `harness/precondition.py` — 预置条件 LLM 三分类(state_hook/action_step/ambiguous),低置信/无映射降级 ambiguous。
- `harness/skills.py` — Skill 体系(**2026-06-15 对齐 Anthropic/Claude Code 标准 Skill**):单一 `Skill`(name+description+content),**渐进披露**——System Prompt 常驻 `name—description` 清单,LLM 判断相关时**主动调 `load_skill(name)` 工具**展开正文(`SkillManager.load`/`render`,`load_skill` 经 agent 控制工具路由 + `SkillManager.tool_schema()` 暴露)。内置基线常识 `preload=True` 正文常驻;项目 Skill `preload=False` 走渐进加载。〔删旧 DomainSkill/PageSkill/ToolSkill 三类与 URL/关键词平台侧匹配——加载与否改由 LLM 决策。〕
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
- 断言归属**按页面/时机分两处(2026-06-15 改,治内网"按步预期"用例)**:**步骤级预期 → `SpecStep.expect`**(翻译 prompt 引导按步分发,预期 N ↔ 步 N),执行到该步时在**当前子页面**即时验证;**整体/最终预期 → 用例级 `assertions`**,**终态**验证;合并裁决。`agent.collect_assertions` 仍聚合两者(供 codegen 的 Then 段)。〔**推翻 2026-06-10 的「统一产在用例级、终态一次验」**:那是为消冗余 + 避"瞬态 expect 拍到终态"的 false-fail,但内网真实用例的预期本就是**按步写**的(每步一个预期,常指向不同子页面),攒到终态验 → 跨子页元素已不在 → 假阴性。改为「按步在所属页验」从根上解决,B-软的「在所属页面软验 + 失败先自愈」一并落地。〕
- **定位三层**:`Locator` 模型(框架无关)/ 解析层(语义 target→Locator,放 generator 外)/ 渲染层(各 CodeGenerator 自实现);稳健度 `ROLE>TEST_ID>LABEL>PLACEHOLDER>TEXT>CSS`。BDD 只是渲染实现之一。
- 截图/代码生成在 `agent.run` 内**端到端接通**:浏览器动作后落 `step_NNN.png`(真实 run_id 目录),断言通过后生成 BDD 代码写 `record.generated_code`+落盘。

## 平台化(进行中的新主线,2026-06-10 启动)

单机版 → 多租户 Web 平台(全公司多产品线)。方向与已拍板决策见 `平台化设计草案.md`;
**任务分解与进度跟踪见 `平台化开发路径.md`(用户两地开发、多 agent 接力:开工前必读、收尾必更新该文档并提交)**。
要点:Postgres、API/worker 双进程(本地优先,K8s 延后到 M3)、项目→版本→Suite→Run、
三角色 RBAC、HTTP 型 Custom Tool、项目级 LLM 配置(加密落库)。单机 CLI 路径保留不回归。

## 实施进度

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
- **下一步候选(2026-06-07 收口时的待办):**
  - **真实内网用例 live 验证**(主线,当前被环境阻塞)— saucedemo 全链路已 live 绿(基础/结算/会话复用/custom_tool/codegen 回放),内网真实业务系统待跑。解阻塞后 CLI/API 两条路径都就绪。
  - **prompt 优化 C/D(省 token/提速,未做)** — C:每业务步约 3 次 LLM 往返(snapshot→action→mark_done),~100k token/用例,可探索「动作结果已带快照则免单独 snapshot」「mark_done 合并」(有正确性风险,实测 click 结果不总带 ref,需 live A/B);D:system 每轮重列全部 ~25 工具文本(已另经 `tools=` 传,冗余),可用 `PromptBuilder.max_tools` 按相关度截断(风险:漏掉所需工具)。已做的 prompt 优化见「prompt 优化(2026-06-07)」:BASE 强制每轮工具调用 + idle 指令修正,live 0 卡死。
  - **断言目标定位器对齐(遗留,部分缓解)** — codegen 的 action 定位器已对齐(执行捕获),但**断言目标**(如购物车角标)不经"执行定位器"产出,无 vocab/selector 时仍文本兜底。需在 vocab/selector 层对齐(断言走 probe,naming 随 LLM 漂移、子串匹配也常错过)。**2026-06-15 缓解**:`text_equals/text_contains` 在**元素定位失败 + 自愈也没救回**时,加一档**全页文本兜底**(`AssertionEngine._text_page_fallback`)——整页快照里确定性搜 `expected` 子串,命中判 PASS 但 reason 标「全页文本兜底」可审计区分。**护栏**(防短串误绿,贴铁律2「宁可误报失败」):① 有显式 `selector` 不兜底(selector 失败是真信号,如空购物车);② `expected` 须够独特(含空白短语或长度≥5,排除 "1"/"2"/状态短词);③ 放在**自愈之后**,优先元素级/自愈精确绿。治「业务词名(中文「成功提示区域」)对不上英文页面元素 → 流程跑完仍 false-fail」(saucedemo 结算「Thank you for your order!」实证)。
  - 阶段五(用例管理平台集成,规格"现在不做")。
- 单测数量以 `python -m pytest -q` 实跑为准(当前约 378;另有 2 个 Windows 平台预存在失败:`test_recorder` 截图目录、`test_tools` 命令替换)。

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
│   ├── hook_builder.py #   build_session_hooks → HookManager(LoginHook+CaptureSessionHook)
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