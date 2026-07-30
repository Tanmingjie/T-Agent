## Why

同一个 Suite 中的每条用例当前都会从全新的浏览器上下文开始，因此每条用例都必须重复登录。对于全部使用同一账号的业务回归套件，这增加了执行时间，也增加了 Midscene 在重复登录步骤上失败的概率。

## What Changes

- 允许 Suite 从现有用例中选择一条“登录准备用例”；未选择时保持当前行为。
- 执行整个 Suite 或单条业务用例时，先且仅先执行一次已配置的登录准备用例。
- 登录准备成功后捕获临时 Playwright `storageState`，后续用例在各自独立的浏览器上下文中加载该状态。
- 登录准备失败时停止本次 Run，不执行依赖登录状态的业务用例。
- Run 结束或异常退出后删除临时登录状态，不跨 Run 持久化。
- 登录准备用例继续作为普通用例从现有 Excel 导入，并保留自己的执行结果。

## Capabilities

### New Capabilities

- `suite-login-setup`: 配置并执行 Suite 级登录准备用例，在单个 Run 内向后续独立用例上下文提供临时登录状态。

### Modified Capabilities

无。

## Impact

- Suite 执行设置的数据模型、存储和设置 API。
- Suite 设置页面中的登录准备用例选择控件。
- `api/run_executor.py` 与 `harness/orchestrator.py` 的用例调度顺序。
- Python `VisualExecutor` 与 Node Midscene runner 之间的 payload 契约。
- Playwright 浏览器上下文创建、`storageState` 捕获/加载和临时文件清理。
- Suite/单用例执行、失败隔离、并发和 runner 的相关测试。
