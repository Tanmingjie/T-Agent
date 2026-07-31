## Why

Suite 当前只能执行全部用例或从详情抽屉执行单条用例，用户无法把一组相关用例作为同一个 Run 执行。这迫使用户在不需要全量回归时重复触发多个单条 Run，也无法统一使用人工规格确认、Skill、登录准备、并发和结果汇总。

## What Changes

- 在 Suite 用例列表中支持勾选多条用例，并将所选用例作为一个 Run 执行。
- 未选择用例时保持全量执行；详情抽屉继续支持单条执行。
- 执行请求和规格预览请求支持 `case_ids` 列表，并校验空列表、重复值和非本 Suite 用例。
- 部分执行按 Suite 中的原始顺序调度，并继续应用人工 TestSpec 确认、Skill、并发和停止执行能力。
- Suite 配置登录准备用例时，部分执行自动将登录准备放在所选业务用例之前，同一 Run 内只执行一次。
- embedded 和 queue 两种运行模式都持久化并透传所选用例集合。
- 执行进度、Run 总数和结果汇总按本次实际执行的用例集合统计，而不是按 Suite 全部用例统计。

## Capabilities

### New Capabilities

- `suite-case-selection`: 定义 Suite 全量、单条和多选部分执行的选择、校验、调度、登录准备及进度统计行为。

### Modified Capabilities

无。

## Impact

- Suite 用例列表的复选框、批量执行工具栏和执行确认文案。
- `useSuiteRun` 的启动参数、SSE 总数状态和进度计算。
- 执行与规格预览 API 的请求模型及目标用例解析逻辑。
- `execute_run`、Orchestrator 和 queue worker 的用例集合透传。
- `run_queue` 的持久化结构及数据库迁移。
- API、repository、run executor、orchestrator 和前端交互相关测试。
