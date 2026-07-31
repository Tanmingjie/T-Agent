## Purpose

允许用户将 Suite 中任意一组用例作为一个 Run 执行，同时保持全量和单条执行入口，并确保规格确认、登录准备、调度和结果统计都严格作用于本次实际执行集合。

## ADDED Requirements

### Requirement: User can select a subset of suite cases
系统 SHALL 在 Suite 用例列表中允许用户选择一条或多条用例，并 SHALL 将所选用例作为一个 Run 执行。

#### Scenario: Run multiple selected cases
- **WHEN** 用户在 Suite 用例列表中选择多条用例并点击执行
- **THEN** 系统创建一个包含全部所选用例的 Run，而不是为每条用例分别创建 Run

#### Scenario: Run all cases when none are selected
- **WHEN** 用户没有选择任何用例并点击“执行全部”
- **THEN** 系统执行 Suite 中的全部用例

#### Scenario: Preserve single-case execution
- **WHEN** 用户从用例详情抽屉点击单条执行
- **THEN** 系统仅执行该目标用例，并保持现有单条执行入口可用

#### Scenario: Select currently filtered cases
- **WHEN** 用户通过搜索过滤用例后操作表头选择框
- **THEN** 系统只切换当前筛选结果的选择状态，并保留未出现在当前筛选结果中的已有选择

### Requirement: Requested cases are validated before run creation
系统 MUST 在创建 Run 或生成规格预览前验证明确提供的 `case_ids`，并 MUST 拒绝空数组、重复标识以及不属于当前 Suite 的用例标识。

#### Scenario: Reject an explicitly empty selection
- **WHEN** 请求明确提供空的 `case_ids` 数组
- **THEN** 系统返回客户端错误且不创建 Run，也不得将空数组解释为全量执行

#### Scenario: Reject duplicate case identifiers
- **WHEN** 请求的 `case_ids` 包含重复用例标识
- **THEN** 系统返回客户端错误且不创建 Run

#### Scenario: Reject a case outside the suite
- **WHEN** 请求的 `case_ids` 包含不存在或不属于当前 Suite 的用例
- **THEN** 系统返回客户端错误且不创建 Run

#### Scenario: Preserve legacy single-case requests
- **WHEN** 现有调用方使用单条 `case_id` 参数且未同时提供 `case_ids`
- **THEN** 系统继续将该请求解释为仅执行该用例

### Requirement: Selected cases execute in suite order
系统 SHALL 按用例在 Suite 中的原始顺序执行所选集合，不得按用户勾选先后改变执行顺序，并 SHALL 继续遵守 Suite 的并发设置。

#### Scenario: Selection order differs from suite order
- **WHEN** 用户先选择 Suite 中靠后的用例，再选择靠前的用例
- **THEN** 本次 Run 仍按 Suite 原始顺序调度这些用例

#### Scenario: Run selected cases with configured parallelism
- **WHEN** Suite 的并发数大于 1 且用户执行部分用例
- **THEN** 系统只在所选业务用例范围内应用该并发上限

### Requirement: Partial runs support the existing execution options
部分执行 SHALL 与全量和单条执行使用相同的 Skill、人工 TestSpec 确认、停止执行、阶段结果和 Run 汇总能力。

#### Scenario: Preview only effective selected cases
- **WHEN** 用户选择多条用例并启用人工确认执行规格
- **THEN** 规格审核界面只展示本次实际执行的所选用例以及自动加入的登录准备用例

#### Scenario: Execute approved selected specs
- **WHEN** 用户确认部分执行的规格并启动 Run
- **THEN** 系统直接使用这些确认后的规格，且不在执行期重新翻译对应用例

#### Scenario: Apply selected skills
- **WHEN** 用户在部分执行前选择项目 Skill
- **THEN** 系统将这些 Skill 应用于本次 Run 中的全部实际执行用例

### Requirement: Login setup is included once for partial runs
当 Suite 配置了登录准备用例时，系统 SHALL 在所选业务用例之前自动加入登录准备用例，并 MUST 保证同一 Run 内最多执行一次。

#### Scenario: Run selected business cases with login setup
- **WHEN** 用户选择多条业务用例且 Suite 配置了登录准备用例
- **THEN** 系统先执行一次登录准备用例，成功后再调度所选业务用例

#### Scenario: Selection already contains login setup
- **WHEN** 用户选择的集合已经包含登录准备用例
- **THEN** 系统不得重复加入或重复执行该登录准备用例

#### Scenario: Select only the login setup case
- **WHEN** 用户只选择被配置为登录准备的用例
- **THEN** 系统只执行该用例一次

### Requirement: Selected case scope survives execution handoff
系统 MUST 在 embedded 和 queue 两种运行模式中传递并保持相同的所选用例集合，queue worker 重启或延迟领取不得把部分执行扩大为全量执行。

#### Scenario: Execute a subset in embedded mode
- **WHEN** embedded 模式收到合法的多选执行请求
- **THEN** 后台执行核只接收并执行本次有效用例集合

#### Scenario: Execute a subset in queue mode
- **WHEN** queue 模式收到合法的多选执行请求并由 worker 领取
- **THEN** worker 从持久化队列中恢复同一个用例集合并只执行该集合

### Requirement: Progress reflects the effective run scope
系统 SHALL 使用本次 Run 的实际用例总数计算进度和结果汇总；自动加入的登录准备用例 SHALL 计入实际总数，未选择的 Suite 用例不得计入。

#### Scenario: Show progress for a partial run
- **WHEN** Suite 有八条用例而本次只执行三条且没有登录准备
- **THEN** 执行进度和 Run 总数以三条为分母

#### Scenario: Include automatic login setup in total
- **WHEN** 用户选择三条业务用例且系统自动加入一条登录准备用例
- **THEN** 本次 Run 的实际总数和完成进度以四条为分母

#### Scenario: Complete a partial run
- **WHEN** 本次部分执行全部结束
- **THEN** Run 结果只汇总本次实际执行用例的通过、失败和总数
