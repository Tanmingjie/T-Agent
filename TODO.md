# TODO — 设计差距与待办

> 来源:对照 `产品设计文档`(v2.1)+`实现规格说明书.md` 与实际代码的全量回顾(2026-06-09)。
> 分三类:**真正缺口**(设计有描述、代码缺失或与描述不符)/ **暂不做**(设计已声明)/ **弱项**(已实现但不完整)。
> 勾选规则:完成后打 `[x]` 并注明 commit/日期。

## 一、真正的缺口(应处理)

- [x] **#1 视觉自愈 / 截图双通道**(§7.3、§9.1#1)— 2026-06-10
  - `HealingSubagent.relocate(screenshot=)`:有截图时发**多模态**消息(文本 A11y 清单 + 图),
    模型不支持图像/`HEAL_VISUAL=0` 时自动退回纯文本通道(向后兼容,不传图时行为不变)。
  - 防臆造升级:A11y 清单带 `[ref=eXX]`,视觉候选用 **ref 锚定**真实可操作节点;ref 命中但
    target 对不上时用该 ref 节点真实可及名复写 target(供按名复验)。治"元素在 a11y 里但可及名
    缺失/与业务词不一致"(图标按钮 / 角标)的高频误判。
  - 两侧接通:断言侧(`MCPPageProbe.raw_screenshot` → `AssertionEngine._try_heal`)+ 操作侧
    (`ReActLoop.get_screenshot` → `_heal_action`)。单测 +5(ref 校验 / 带图 / 退回 / env 关 / 纯文本兼容)。
  - **遗留**:需多模态模型才真正生效;纯 a11y 缺失(canvas)仍无可操作 ref(设计内边界)。
    未真机 live 验证(待你内网多模态模型)。

- [x] **#4 预置条件分类结果落库 + 前端确认闭环**(§3.2)— 2026-06-10
  - 后端:`TestCase.precondition_items`(+`TestCaseRow` JSON 列)落库分类;`agent._classify_preconditions`
    从用例已确认项灌 classifier memory(命中跳过 LLM、用户选择优先)并回写分类到 case;
    执行链 `_save_record` 用例跑完即落库;`PreconditionClassifier.IGNORE` + `USER_SETTABLE_TYPES`;
    Repository/路由 `PUT .../cases/{id}/precondition-item`(type=Hook/Given/忽略,标 confirmed)。
  - 前端:`CaseDrawerBody` 新增 `PreconditionBlock`,模糊项标黄、下拉选 Hook/Given/忽略即时落库。
  - 测试:repo/agent/classifier/API 四处单测(共 +4);前端 tsc 通过。
  - 遗留:执行前「先分类后审查」仍需先跑一次(分类在执行链内);纯 classify-only 端点未做(非必需)。

- [x] **#2 `on_heal` Hook 接通**(§7.7)— 2026-06-10
  - `agent.run` 收尾聚合断言侧(`a_results` 中 `healed`)+ 操作侧(`action_steps[].heal_attempts`)
    两路自愈,任一发生即触发 `ON_HEAL`,详情(heal_count / healed_assertions / action_heals)入
    `ctx` 供 hook 消费;无自愈不触发(避免噪声)。单测 `test_hooks.py` 两例覆盖。

- [x] **#3 文档口径修正:操作图谱标"预留"**(§6.2)— 2026-06-10
  - 产品设计文档 §6.2 表格「操作图谱」标 ⚠️预留(未实现)+ 补一段说明,与 §1.3 口径一致。

- [x] **#5 文档口径修正:生成器只有 BDD**(§8.1)— 2026-06-10
  - §8.1 已写「BDDGenerator(默认)/ PlainGenerator / PageObjectGenerator(预留)」,口径正确,无需改。

- [x] **#6 文档口径修正:输入仅 Excel**(顶部表/§3/§10)— 2026-06-10
  - 顶部能力表、产品一句话、架构流程图三处把「测试管理平台 API」标注为「阶段五预留,当前仅 Excel」。

## 二、设计已声明"暂不做"(确认即可,非遗漏)

- [ ] 报告导出 — 阶段五
- [ ] 失败断点续跑 — 阶段五
- [ ] Page Object 模式 — 阶段五
- [ ] 测试管理平台 API 对接(含 `external_id` 同步)— 阶段五
- [x] `llm_judge` 执行 — **方案A(2026-06-10)**:作降级链最末档兜底,接 LLM 真判 PASS/FAIL 计入裁决,标 `ai_judged` 低置信、报告区分、偏向 FAIL(原"恒 skipped"已由用户拍板推翻)
- [x] `/vocabulary/scan` — **有意 no-op**(真扫描在执行期增量做)

## 三、已实现但不完整 / 弱项

- [ ] **断言目标定位器对齐**:action 定位器已对齐执行捕获;断言目标(如购物车角标)走 probe 不产出可复用选择器,无 vocab 时仍文本兜底 → 需在 vocab/selector 层对齐。**(是下条「步骤级软校验/B-软」的前置:定位不稳时步骤级硬信号会误判空转,反噬健壮性。)**
- [ ] **步骤级软校验(B-软,未来能力)**:让 `SpecStep.expect` 在步骤边界做**软**校验——不过 → 触发自愈/重试/绕行(**不**判用例失败),而非终态统一硬验。价值:① 挡 LLM「过早 mark_done」给确定性 ground truth;② 失败定位前移;③ 自愈触发更准;④ 补「瞬态期望」(中途出现、终态消失,如加购角标)的覆盖缺口。**前置依赖「断言目标定位器对齐」**(否则误判空转)。字段已保留(2026-06-10 #2 清理只停止索取、未删 `expect`),门没焊死。〔与铁律2(a):必须是软、可恢复,不能硬闸门一票否决。〕
- [ ] **定时扫描触发**(§6.4 "手动/stale/定时"):**手动主动扫描已做**(2026-06-10,`/vocabulary/scan` 会导航的探索式扫描,清单为主+可选浅爬)+ stale + 执行期增量(默认关);唯"**定时**"调度仍无实现。
- [ ] **Session 过期自动重登**(§7.1):`valid_until` 有效期检查已做;自动重登需接真实 `login_aw`(未接时 optional 放行让 Agent 自登)。
- [ ] **prompt 优化 C/D**:每步约 3 次 LLM 往返(snapshot→action→mark_done)、system 每轮重列全部工具,可省 token;有正确性风险,待 live A/B。
- [ ] **真实内网用例 live 验证**(主线,环境阻塞):saucedemo 全链路已 live 绿(基础/结算/会话复用/custom_tool/codegen 回放),真实内网业务系统待跑。
- [ ] **Skill 渐进式加载调试**(2026-06-15):项目 Skill 现**暂用默认加载**(`run_executor` 构造时 `preload=True`,正文常驻 prompt,保证生效)。渐进披露链路(`preload=False` + LLM 调 `load_skill` 展开正文)**已实现且单测覆盖**,但实测弱模型(DeepSeek/Qwen)常**不主动 load** → skill 形同虚设,故先回退默认加载。**待做**:调试/打磨渐进式加载使其在弱模型下可靠触发——候选:① 启发式辅助加载(步骤业务词命中 skill description 关键词时确定性帮 load);② 在 BASE prompt 里强化「相关时必须先 load_skill」的指令;③ 区分 skill 大小(短的 preload、长的渐进)。目标:大词表/多 skill 场景下省 context 又不漏加载。改回只需 `run_executor` 去掉 `preload=True`。
- [ ] **指标 run/套件级聚合看板**(2026-06-15,#6 收口时记):单用例 metrics 已埋点 + 抽屉「执行指标」面板已做(commit 53ec851),`metrics` 已随 record 落库、run 概览接口每用例透出。**待做**:执行历史/报告页加 run 级汇总(总 token / 各阶段占比 / 哑火率 / 完整性闸门拦截率 / **llm_judge 兜底占比 = false-green 风险面**)+ 套件级趋势。数据已就位(`ExecutionRecord.metrics` + `results.py::get_run_overview` 的 `cases[].metrics`),纯前端聚合 + 一个汇总组件即可,无需后端改动。
- [ ] **执行后增量补充的逐步 url 归属精度**(2026-06-15,重写策略C 时 live 发现):`_incremental_scan` 按步 `s.url` 分组,部分步(如 `browser_type`)`outcome.url` 为空靠「继承最近非空 url」兜底——偶尔把跨页元素(如 inventory 页的「加入购物车」)归到前一页 url 组(saucedemo TC101 实证:Add to cart 落到登录页 `/` 组)。**不影响 selector 正确性**(词条仍带真实 `[data-test=...]`),但页面归属不精确会影响按 url 解析命中。根因在 `react_loop` 逐步 url 捕获精度(click 后 `outcome.url` 不总回填新页 url)。修法候选:react_loop 每步落定后补一次 url 探查,或 `_incremental_scan` 用步后快照 url 校正。

## 建议优先级

1. 文档口径修正 #3 / #5 / #6 — 低成本,先做免误导。
2. #4 分类落库 + 前端确认 — 设计核心交互,断了。
3. #1 视觉自愈 — 弱模型价值高。
4. #2 `on_heal` 接通 — 几行代码。
