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
2. **断言由规则引擎确定性验证,不让 LLM 眼判 PASS/FAIL**。判断在"翻译时"一次性做(预期→结构化 Assertion),执行时只做确定性比较。
3. **本地 LLM 的 tool_call 必须容错**(宽松 JSON / 从 content 提取 / 重试),偶发格式错误不得搞崩 ReAct 循环。
4. 最终 PASS/FAIL **以断言裁决为准,不取 LLM 自报的 TEST_RESULT**。
5. 实现原则(规格 §0):前后端分离、数据层抽象(SQLModel,不直接写 SQL)、输入/输出抽象(都产出 `TestCase`/落 `ExecutionRecord`)、核心表预留 `updated_at`/`owner`/`external_id`、分阶段不跳跃。

## 架构大图(需要读多文件才能拼出的部分)

单条用例的执行由 `harness/agent.py::TestCaseAgent.run()` 总装,串起以下模块：

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
- `harness/orchestrator.py` — Suite 调度:`parallelism` **可配并发**(`asyncio.Semaphore` + `gather`,默认 1=串行;>1 需 `agent_factory` 让每用例自带独立 MCP/浏览器)、用例间隔离(异常→FAIL 不拖垮他人)、suite 级 hooks、结果汇总。
- `harness/tools.py` — Custom Tool 注册:`@tool` 装饰器 + YAML `command`;LLM 按需调用;Agent 路由(控制→StepPlan / 自定义→Registry / 其余→MCP)。

### 工程化界面(阶段四)

- `api/server.py` — FastAPI 应用入口,挂载 5 个路由子模块 + SSE 推送。
- `api/repository.py` — **Repository 抽象层**。`SuiteRepo` / `RunRepo` / `VocabRepo` 三个抽象基类 + SQLModel 实现(`SQLModelSuiteRepo` 等),业务代码面向抽象、存储可替换。
- `api/routers/suites.py` — Suite CRUD(创建/列表/详情/删除)。
- `api/routers/execution.py` — 执行控制:**SSE 实时推送**执行进度;执行**搬离 API 事件循环**(见 `api/execution_worker.py`:每 run 一守护线程 + 独立 loop + 独立 Store),按 Suite 设置的 `parallelism` 跑 Orchestrator。
- `api/execution_worker.py` — **执行线程隔离工具**:`spawn_run`(每 run 一线程跑自己的 loop)、`make_sse_bridge`(worker→API 经 `call_soon_threadsafe` 桥接 SSE)、权限走 `threading.Event`(跨线程审批)。根治「执行期所有 HTTP 接口 pending」(单事件循环被执行的同步活儿占住)。
- `api/routers/permission.py` — 权限审批(approve/deny)。
- `api/routers/results.py` — 执行结果查询(用例列表/断言详情/代码查看)。
- `api/routers/vocabulary.py` — 词汇表 CRUD + scan 触发。
- `frontend/` — React + Vite + Tailwind 前端控制台(Suite 管理、执行控制台、结果详情含 Monaco 编辑器、词汇表)。
  - **Design Tokens** (`tailwind.config.js`): `brand` (cyan 系, 50–950)、`surface` (slate 系, 50–950)、`shadow-card` / `shadow-elevated`。
  - **UI Skills 已安装**: `frontend-design`(anthropics)、`ui-ux-pro-max` + 6 CKM skills(nextlevelbuilder)。通过 `npx skills add` 安装，各环境自行拉取。

数据结构全部在 `input/models.py`(pydantic;落库 SQLModel 留到 T-21)。

## 关键决策(已定,勿反复纠结)

- Python **3.11** + `uv`(规格用 `str | None` 等 3.10+ 语法;本机默认 3.9 不可用)。
- 本地包名 **`mcp_client`** 而非 `mcp`,避让官方 `mcp` SDK 顶层包名冲突。
- ReAct 用**文本式观察回灌**,不依赖严格 tool_call_id 配对(本地 Qwen 支持不稳)。
- `ExecutionRecord.case_assertions` / `spec` 是**有意新增**字段(规格模型没列):前者承载可信 PASS/FAIL 依据,后者存档 LLM 翻译产物供前端可视化 + 发现翻译偏差。
- 断言**聚合**用例级 `assertions` + 各步 `expect`(`agent.collect_assertions`),因 LLM 放断言位置不稳定。
- **定位三层**:`Locator` 模型(框架无关)/ 解析层(语义 target→Locator,放 generator 外)/ 渲染层(各 CodeGenerator 自实现);稳健度 `ROLE>TEST_ID>LABEL>PLACEHOLDER>TEXT>CSS`。BDD 只是渲染实现之一。
- 截图/代码生成在 `agent.run` 内**端到端接通**:浏览器动作后落 `step_NNN.png`(真实 run_id 目录),断言通过后生成 BDD 代码写 `record.generated_code`+落盘。

## 实施进度

- 阶段一 ✅ T-01~T-10(主干跑通,断言驱动 PASS/FAIL;saucedemo 端到端验证过)
- 阶段二 ✅ T-11~T-19(自愈 / Context Compact / Hooks / Session+LoginHook / 预置条件分类器 / Skill / Permission / Orchestrator / Custom Tool)。四条验收标准 saucedemo 真实演示通过(见 `examples/acceptance_stage2.py`)。
- 阶段三 ✅ T-20~T-22(`codegen/`BDDGenerator / `storage/db.py` SQLModel 持久化 / `intelligence/`词汇表+Scanner)。
- 阶段四 ✅ T-23~T-27(FastAPI 后端 5 路由+SSE / Repository 抽象层 / React 前端控制台(Suite 管理、执行控制台、结果详情、词汇表)/ BDD `step_N` 标记)。
- **UI Redesign (TestSprite 风格,已落地一轮)** — 参照 TestSprite 控制台重构前端,设计语言:**森林绿**强调色(`brand` 令牌已由 cyan 改 green)、**浅色分组侧栏**、**表格**列表、**双栏抽屉**。关键结构(以 git 历史/当前代码为准,不逐项追踪):
  - 布局:`RootLayout`(全局浅色侧栏:测试套件/词汇表)+ `SuiteLayout`(进入套件后切换为**套件内导航**:用例/执行历史/设置 + 面包屑)。
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
  - **Scanner 策略C 接通(执行期增量扫描)** — `agent.run` 结束后 `_incremental_scan` 复用
    ReAct 期间已捕获的 a11y 快照(`action_steps.tool_result` 含 `[ref=`),按 URL 去重、
    独立 context 调 `Scanner.scan_and_save` 提炼业务词→元素映射并库(手动条目优先,AI 标
    `source=ai`)。**扫前先 `find_page` 查重**:非 stale 词汇表(含手动)就跳过,免同界面
    多用例重复提炼;页面变更经自愈 `mark_stale` 触发下次重扫。env `VOCAB_SCAN=0` 关。
    (此前 `/vocabulary/scan` 是空壳桩、Scanner 从没被执行链调用——又一例「有模块≠接通」。)
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
  - **P3 基础 DomainSkill + custom_prompt 接通** — `harness/skills.py::build_skill_manager`:注入内置 `DEFAULT_DOMAIN_SKILLS`(表单操作 / 结果定位等业务常识)+ 把此前孤儿字段 `Suite.custom_prompt` 作为 DomainSkill 接通;经 `make_agent`(API,带 custom_prompt)与 CLI(`--no-skills` 可关)注入 agent。
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
- **公开站点验证战役 + 真 bug 修复(2026-06-07,已做)** — 内网用例阻塞,改用公开站点把**未 live 验过的路径**逐条跑通(都用 saucedemo + 真 DeepSeek + 真 Chrome):
  - **API+SSE 端到端**(此前只有单测)— 起 `uvicorn` → API 建 suite/传 Excel/触发 run → 收 SSE → 拉结果。两用例经 worker 线程路径 PASS 2/2,SSE 事件全到(`suite_start/case_start/phase/spec_ready/step_change/case_result/suite_done`),落库 status=completed。**整条阶段四生产路径(前端走的那条)首次真跑通**。
  - **复杂多步流程** — saucedemo 完整结算(登录→加购→购物车→Checkout→填 First/Last/Zip→Continue→Finish)11 业务步 PASS,终态 `text_equals "Thank you for your order!"` + `url_contains checkout-complete.html` 双绿,过程 2 次自愈。素材 `examples/saucedemo_checkout.xlsx`。
  - **custom_tool 数据断言** — 接 `examples/custom_tools.yaml` 真跑外部命令:`http_health` curl saucedemo→`200` PASS、期望 `500`→FAIL、未注册工具→skipped,确定性比较全对。
  - **P2 跨用例 Cookie 复用 + 抓出真 bug** ★ — 走 API 路径(hooks 只在 API 接通)2 用例 suite。**首跑两用例都"绿"但 cookie 文件根本没生成 → 功能其实没接通**(典型「看着绿实则空转」,幸亏走真实端到端到产物)。根因:`browser_run_code_unsafe` 把返回值**双重 JSON 编码**(cookies 是带引号的 JSON 字符串字面量 `"[{\"name\":...}]"`),`_parse_cookies_result` 只 loads 一次 → 0 条 → `CaptureSessionHook` 从没存过。修:`_parse_cookies_result` 兼容双重编码(先 loads 外层字符串再 loads 内层数组)。修后:cookie 文件生成、TCB 步数 7→3(省了登录);**铁证**(无 agent 干扰的直接注入回放):捕获 cookie→全新浏览器注入→直达 `inventory.html` 免登录可达(6 个商品)= True。教训复刻:**saucedemo 账号太有名,agent 可能自己重登 → 用例"绿"会掩盖复用失效;验证复用要看产物(cookie 文件)+ 隔离注入回放,别只看用例 PASS**。
- **codegen 闭环验证 + 导航修复(2026-06-07,已做)** — 真把生成的 pytest-bdd 用 Playwright 跑了一遍。发现并修:用例把"打开页面"写在预置条件(被 P1 归 state_hook 不进 steps)→ 生成代码缺 `page.goto`、回放不开页面。修:`agent.ensure_navigation_step` codegen 前置注入隐式导航。闭环实证:含正确定位器的 spec→生成 BDD→pytest-playwright 真打 saucedemo 1 passed。遗留:spec target 与词汇表/捕获键不一致时定位器退化文本兜底(待对齐)。**附:此前怀疑的"step-def 命名 bug"是 `ls|head` 截断误判,不存在**(`test_<case>.py` 本就按用例命名)。
- **下一步候选:**
  - **执行期捕获真实 a11y role+name → ActionStep(已做)** — `page_probe` 解析快照里每个节点的 `ref`(`A11yNode.ref` + `build_ref_index(text)→{ref:node}`);`react_loop` 在操作元素时(`browser_click/type/...` 带 `ref`)从**上一份观察快照**的 ref 索引回查真实 `(role, name)`,连同当前业务步骤 `target` 记到 `ActionStep`(`element_role`/`element_name`/`step_target`)。`codegen/locators.py::locators_from_steps` 据此对**未录入词汇表**的目标也产出稳健 `get_by_role` 定位;`agent.run` 把它 overlay 到词汇表解析结果之上(**执行捕获优先**:`{**vocab, **captured}`)。仅 role+name 齐备才采纳(role 无 name 过宽,反不如词汇表)。
  - 阶段五(用例管理平台集成,规格"现在不做");**真实内网用例** live 验证(saucedemo 已 live 绿,内网真实业务系统待跑)。
- 单测数量以 `python -m pytest -q` 实跑为准(当前约 374;另有 2 个 Windows 平台预存在失败:`test_recorder` 截图目录、`test_tools` 命令替换)。

T-xx ↔ 规格小节对照见 `实现规格说明书.md` §5(各模块详细规格)与 §6(实施计划)。

## 工作约定

- **每个任务动手前,重读 `实现规格说明书.md` 对应小节**(以原文为准,别凭记忆);并核对已实现部分有无偏离。
- 每个任务配单元测试;不连真实 LLM/浏览器,用 fake/mock 驱动(参考 `tests/` 现有写法)。
- 改完跑 `pytest`,并 `isort`+`black` 格式化后再交。
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

# 启动 API 服务(dev 启动器:--reload 只监视源码目录)
python scripts/serve.py
# ⚠️ 别直接 `uvicorn api.server:app --reload`:codegen 执行通过后写 storage/generated/*.py,
#    默认 reload 监视项目目录会因此重启后端、打断正在跑的 run、令所有请求 pending。

# 启动前端开发服务器
cd frontend && npm install && npm run dev

# LLM 配置:.env(项目根,自动加载) 或 env 或 CLI flag
#   LLM_MODEL / LLM_API_BASE / LLM_API_KEY；模型名需带 provider 前缀(如 openai/xxx、ollama/xxx)

# 浏览器层:npx @playwright/mcp(stdio);saucedemo 等会触发 Chrome 密码泄露弹框,可加 --isolated --headless 规避
```

测试配置:`pyproject.toml` 的 `[tool.pytest.ini_options]` 已设 `asyncio_mode = "auto"`(async 测试无需标记)。
领域模型 `TestCase`/`TestSpec` 及 `TestCaseAgent` 名字以 `Test` 开头,已用 `__test__ = False` 避免 pytest 误收集。

## 目录结构速览

```
T-agent/
├── harness/          # Agent 核心(ReAct/断言/自愈/Prompt/LLM/录制…)
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
│       ├── pages/    #   SuiteList/SuiteCases/SuiteHistory/SuiteRunDetail/SuiteSettings/Vocabulary
│       ├── components/ # RootLayout/SuiteLayout/IconRail/Drawer/CaseDrawerBody/Sidebar/…
│       └── api/     #   client.ts(API 封装)
├── cli/              # 命令行入口(run_case.py)
├── tests/            # 单元测试(fake/mock 驱动,不连真实 LLM/浏览器)
├── examples/         # 验收入口 + saucedemo 用例
├── 实现规格说明书.md  # 唯一真相源:所有模块详细规格
└── 产品设计文档_v2.0.md # 产品设计原文
```