## Context

当前执行入口在 `api/run_executor.py::execute_run` 中完成项目知识、执行 Skill、成功经验复用、LLM client 和 `MidsceneCaseAgent` 组装。成功经验复用已经能在执行前命中同一用例的 TestSpec 和经验上下文，但它不能处理大量旧用例“业务意图很抽象、操作路径缺失、预期不可观察”的问题。

质量闸门应放在 TestSpec 翻译和 Midscene 执行之前，因为低质量用例一旦进入视觉执行，失败成本高、耗时长，而且失败原因经常不是模型或页面问题，而是输入描述本身不满足可执行性。

## Goals / Non-Goals

**Goals:**

- 在执行前对每条目标用例给出稳定、可解释、可展示的质量评分。
- 对低分用例默认拦截，并给用户明确的缺失信息和改写建议。
- 允许试点/调试场景显式 override，并完整记录 override 的责任和上下文。
- 复用项目用例规范、执行 Skill 和成功经验，避免业务术语被误判为天然不可执行。
- 保持评估失败安全：评估服务异常不能悄悄放行低质量用例。

**Non-Goals:**

- 不自动修改 Excel 原始用例或批量覆盖用户数据。
- 不把改写建议直接当成已批准 TestSpec 执行。
- 不引入坐标回放、动作录制回放或半确定性执行。
- 不要求第一期评分完全准确；需要通过事件和结果数据持续校准。

## Decisions

### Add a first-class assessment model

新增 `CaseExecutabilityAssessment` 领域模型，核心字段包括:

- `score`: 0-100 总分。
- `risk_level`: `pass` / `warn` / `blocked` / `error`。
- `gate_decision`: `allow` / `warn` / `block` / `overridden`。
- `dimensions`: 操作明确性、断言可观察性、业务术语接地、歧义程度、等待条件清晰度、历史复用信心等维度。
- `issues`: 可执行性问题，区分 blocking 和 warning。
- `rewrite_suggestions`: 针对步骤、预期、Skill/SOP 的改写建议。
- `draft_steps` / `draft_expected`: 非破坏性的改写草稿。
- `context_sources`: 使用了哪些项目规范、Skill、成功经验。
- `override_reason`: 强制执行时填写。

Rationale: 评估结果需要贯穿 API、run_event、ExecutionRecord metrics 和前端展示，结构化模型比字符串提示更容易测试和校准。

Alternative considered: 只在前端展示一段 LLM 解释。这个方案实现快，但无法做阈值判断、可观测统计或后续校准。

### Use deterministic heuristics plus LLM assessment

评分器分两层:

1. 确定性预检: 检查空步骤、空预期、短句、模糊词、不可观察预期、无等待条件的长等待、缺少操作动词等。
2. LLM 评估: 结合用例、项目规范、已选 Skill、成功经验，输出结构化 JSON 分数、原因和建议。

最终结果采用保守合并:

- 确定性 blocking 问题不能被 LLM 完全抹掉，只能被“Skill/项目规范已解释”降级。
- LLM 输出解析失败或超时返回 `risk_level=error`，默认拦截。
- 有成功经验命中时可以提高历史复用维度，但不能让明显空预期、空步骤直接通过。

Rationale: 纯 LLM 容易波动，纯规则又不懂业务术语。两层合并能保持可解释性和业务弹性。

Alternative considered: 只用规则评分。它会把“LLL 腔室”“Stick 流气”这类内部术语全部误判为低分，推动用户重复写业务背景。

### Put the gate before TestSpec reuse and translation

执行流调整为:

```text
trigger_run
  -> resolve target cases / selected Skills
  -> assess target cases
  -> emit case_quality events
  -> block, warn, or continue
  -> load successful memory / reuse TestSpec
  -> translate missing TestSpec
  -> Midscene execute
```

Rationale: 即使命中成功经验，也应该先知道当前输入质量和风险；但成功经验可作为评估上下文参与评分。质量闸门位于翻译前，可以避免低质量用例进入昂贵的翻译和视觉执行。

Alternative considered: 在 TestSpec 翻译失败后再评估。这样无法减少翻译漂移，也无法避免低质量用例消耗执行资源。

### Default threshold with run-level override

默认阈值:

- `score >= 80`: allow。
- `60 <= score < 80`: warn，允许执行，但 UI 显示风险。
- `score < 60`: block，默认拒绝执行。
- `error`: block，除非用户强制执行。

API run options 增加:

- `quality_gate_enabled`: 默认 `true`。
- `force_low_quality_cases`: 默认 `false`。
- `quality_override_reason`: 强制执行低分或评估失败用例时必填。

第一期先用代码默认值，不新增 env。后续如果试点需要，可把阈值提升为项目设置或 Suite 设置。

Rationale: 默认开启能立刻保护执行资源；override 保留试点灵活性。

Alternative considered: 只做提示不阻断。这个路径更温和，但不能解决用户提出的“低分拒绝执行”目标。

### Persist latest assessment and attach run context

持久化策略:

- 新增 `case_executability_assessment` 表保存最近评估和历史评估。
- 每条评估保存 `assessment_version`、用例 `case_hash` 和 `context_hash`，用于判断评估是否仍可复用。
- `run_event` 写入 `case_quality` 事件，展示执行前评估进度和 gate 决策。
- `ExecutionRecord.metrics["case_quality"]` 保存本次执行实际使用的评分和 gate 决策。
- 被 block 且未执行的用例需要保存一条可查询的 blocked 结果或 run_event，以便前端解释“为什么没有执行”。

Rationale: 用户需要在用例抽屉和运行结果里复盘低分原因；试点团队也需要统计哪些维度最常阻塞。

Alternative considered: 只保存在 run_event。run_event 适合时序展示，但不方便“打开用例查看最近一次评估”。

### Reuse unchanged assessment results

评估缓存命中条件:

- 同一 `project_id` / `version_id` / `suite_id` / `case_id` / `base_url`。
- 同一用例内容指纹 `case_hash`。
- 同一评估算法版本 `assessment_version`。
- 同一评估上下文指纹 `context_hash`，其输入包括项目用例规范、已选 Skill 名称和命中的成功经验元数据。

命中后不再调用评估 LLM，而是复制一条本次 run 专属的评估记录:

- 新记录拥有新的 `id` / `run_id` / 时间戳。
- `cache_hit=true`，`source_assessment_id` 指向原始评估。
- 根据本次 run 的 gate 选项重新计算 override / disabled 结果。
- 前端展示“复用历史评估缓存”，避免用户误以为本次重新评分。

失效策略:

- 用例文本、项目规范、已选 Skill 或成功经验上下文变化都会改变指纹，从而触发重新评估。
- 评估异常和人工 override 的结果不作为缓存源，避免把临时绕过或服务失败固化。
- 缓存命中记录自身不作为下一次缓存源，避免来源链无限延长。

Rationale: 质量评分放到翻译前后会更依赖原始文本，缓存能减少 LLM 波动和执行前延迟，同时保留每次 run 的可审计记录。

Alternative considered: 直接复用最新评估，不保存本次 run 记录。这个方案省库表行数，但运行结果无法回答“本次到底用了哪个评分”，也会导致抽屉历史串味。

### Keep rewrite suggestions non-destructive

改写建议只输出草稿:

- 建议步骤列表。
- 建议预期结果。
- 需要补充的业务 SOP/Skill。
- 仍不确定的问题。

前端第一期只展示和复制，不自动写回用例。若后续要“一键应用建议”，应另起 change，明确编辑权限、审计和 Excel 源数据同步策略。

Rationale: 自动改写旧用例有数据治理风险，尤其试点阶段评分尚未校准。

Alternative considered: 低分时自动替换为改写草稿继续执行。风险过高，可能让系统执行了用户未确认的语义。

## Risks / Trade-offs

- LLM 评分波动 → 用确定性规则兜底、结构化输出校验、事件记录和后续校准集降低风险。
- 低分误拦截真实可执行用例 → 支持 force override，并在 UI 展示具体原因供用户修正或反馈。
- Skill 未选择导致术语误判 → 执行确认弹框在评估前允许选择 Skill，并在建议中提示需要补充/选择哪个 Skill。
- 评估增加执行前延迟 → 多用例并发评估、缓存最近评估结果、命中未变化指纹时复用评分。
- 缓存误命中旧上下文 → 将项目规范、Skill、成功经验和评估版本纳入 `context_hash`，并禁止复用异常/override/缓存派生记录。
- 过早固化阈值 → 第一期用默认阈值，保留后续项目级阈值配置空间。

## Migration Plan

- 新增 Alembic 迁移创建评估表；已有数据无需迁移。
- 若环境已提前创建评估表，迁移会幂等补齐缓存字段和索引。
- 部署后默认开启质量闸门；若试点担心阻断过多，可先在 API 层通过 run option 显式关闭或强制执行。
- 回滚时评估表可保留为历史数据，不影响既有 Midscene 执行链。

## Open Questions

无。
