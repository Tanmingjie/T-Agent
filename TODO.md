# TODO — 设计差距与待办

> 来源:对照 `产品设计文档`(v2.1)+`实现规格说明书.md` 与实际代码的全量回顾(2026-06-09)。
> 分三类:**真正缺口**(设计有描述、代码缺失或与描述不符)/ **暂不做**(设计已声明)/ **弱项**(已实现但不完整)。
> 勾选规则:完成后打 `[x]` 并注明 commit/日期。

## 一、真正的缺口(应处理)

- [ ] **#1 视觉自愈 / 截图双通道**(§7.3、§9.1#1)
  - 现状:`harness/healing.py` 不传任何截图给 LLM,`P5_visual` 只是优先级标签,自愈实为**纯文本**。
  - 期望:抓快照 + 截图双通道,P1→P5 真正含视觉兜底(候选仍须落在快照里,防臆造)。
  - 价值:弱模型环境下纯文本自愈能力有限,截图是 browser-use 验证过的有效手段。

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
- [x] `llm_judge` 执行 — **有意恒 skipped**(贯彻铁律2,非缺陷)
- [x] `/vocabulary/scan` — **有意 no-op**(真扫描在执行期增量做)

## 三、已实现但不完整 / 弱项

- [ ] **断言目标定位器对齐**:action 定位器已对齐执行捕获;断言目标(如购物车角标)走 probe 不产出可复用选择器,无 vocab 时仍文本兜底 → 需在 vocab/selector 层对齐。
- [ ] **定时扫描触发**(§6.4 "手动/stale/定时"):只有手动 + stale + 执行期增量,"定时"无实现。
- [ ] **Session 过期自动重登**(§7.1):`valid_until` 有效期检查已做;自动重登需接真实 `login_aw`(未接时 optional 放行让 Agent 自登)。
- [ ] **prompt 优化 C/D**:每步约 3 次 LLM 往返(snapshot→action→mark_done)、system 每轮重列全部工具,可省 token;有正确性风险,待 live A/B。
- [ ] **真实内网用例 live 验证**(主线,环境阻塞):saucedemo 全链路已 live 绿(基础/结算/会话复用/custom_tool/codegen 回放),真实内网业务系统待跑。

## 建议优先级

1. 文档口径修正 #3 / #5 / #6 — 低成本,先做免误导。
2. #4 分类落库 + 前端确认 — 设计核心交互,断了。
3. #1 视觉自愈 — 弱模型价值高。
4. #2 `on_heal` 接通 — 几行代码。
