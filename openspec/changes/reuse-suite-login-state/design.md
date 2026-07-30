## Context

当前每条用例由 `VisualExecutor` 启动独立 Node sidecar；runner 内部执行 `chromium.launch()`、`browser.newPage()`，用例结束后关闭浏览器。`Orchestrator` 可以并发运行用例，因此用例之间既不共享进程，也不共享浏览器上下文。

Suite 用例来自 Excel，当前设置只包含权限模式和并发数。登录准备用例应继续使用现有 TestCase/TestSpec/Midscene 执行链，而不是新增一套登录脚本格式。行为契约见 `specs/suite-login-setup/spec.md`。

## Goals / Non-Goals

**Goals:**

- 在不牺牲业务用例浏览器隔离的前提下，在单次 Run 内只登录一次。
- 复用完整浏览器认证状态，而不是重新实现 Cookie 抓取和注入。
- 同时覆盖完整 Suite 和单条业务用例执行，并保持 embedded/queue 两种运行形态一致。
- 未配置登录准备用例时不改变现有执行链。

**Non-Goals:**

- 不跨 Run 保存认证状态。
- 不支持多账号、多角色或按用例选择不同登录状态。
- 不增加凭据中心、人工登录流程或新的单条用例编辑入口。
- 不自动删除业务用例中已有的登录步骤；用例数据由用户通过 Excel 调整。
- 不让业务用例共享同一个页面或 BrowserContext。

## Decisions

### 1. SuiteSettings stores a nullable case reference

在 `SuiteSettingsRow` 增加可空的 `login_setup_case_id`。空值表示关闭；非空值必须引用当前 Suite 的现有用例。设置 API 保存前校验归属，执行时再次校验，避免用例数据变化后静默使用无效配置。

登录准备是 Suite 对某条用例的用途配置，因此不在 `TestCase` 上增加全局 `is_setup` 标记。这样同一条导入用例不会在其他 Suite 或未来同步来源中携带错误语义。

替代方案是约定第一条用例固定用于登录。该方案依赖导入顺序且无法显式关闭，故不采用。

### 2. Orchestration has a serial setup phase followed by the existing business phase

`run_executor` 在按 `case_id` 筛选目标业务用例前解析登录准备用例，并把“登录准备 + 目标业务用例”作为本次实际执行集合。若目标就是登录准备用例，则去重后只执行一次。

`Orchestrator` 增加可选的登录准备参数：存在时先通过现有 agent 执行该用例；只有执行记录通过且状态文件已经生成，才使用现有 Semaphore/gather 逻辑执行其余用例。登录失败时为未启动的业务用例产生“未执行”记录，并保留登录用例的正常执行记录。

手工确认 TestSpec 的 preview/approval 路径使用同一套实际执行集合，保证单条业务用例执行时登录准备用例也能先被翻译和确认。

替代方案是在每个业务用例前由 runner 自行检查并登录。这仍会重复登录，也无法在并发开始前建立统一前置状态，故不采用。

### 3. Authentication state is exchanged through a run-scoped temporary file

`run_executor` 为启用了登录准备的 Run 创建系统临时目录，并将绝对状态文件路径传给 Orchestrator、Agent 和 `VisualExecutor`。登录准备 payload 标记“成功后捕获”，业务用例 payload 标记“启动时加载”。临时目录由 `run_executor` 在 `finally` 中统一清理。

状态文件不放进 artifact 目录、不写入 runner stdout/stderr、不通过结果 API 返回。它只在本机父进程和 Node 子进程间传递，满足 embedded 与 queue worker 的共同文件系统边界。

替代方案是把状态 JSON 放进 runner stdout。当前 stdout 会完整落盘为日志，会泄露可用于冒用账号的认证信息，故不采用。

### 4. Runner uses an explicit BrowserContext

Node runner 将 `browser.newPage()` 改为显式的 `browser.newContext()` + `context.newPage()`：

- 登录准备不加载旧状态；所有阶段通过后调用 `context.storageState({ path, indexedDB: true })`。
- 业务用例创建 context 时传入 `storageState` 文件路径。
- viewport 等现有 page 选项迁移到 context 选项。
- finally 中按 `agent.destroy()`、`context.close()`、`browser.close()` 的顺序收尾。

捕获必须发生在登录用例全部阶段通过之后；捕获失败会使登录准备用例失败，避免后续业务用例在未登录状态下运行。

替代方案是让整个 Suite 共享一个 BrowserContext。该方案会把页面、localStorage 修改、弹窗和其他用例副作用传播给后续用例，并与现有并发隔离模型冲突，故不采用。

### 5. Configuration is a single dropdown in the existing Suite settings page

Suite 设置页在“执行设置”中增加“登录准备用例”下拉框，选项来自当前 Suite 已导入用例，并提供“不使用”空选项。不增加独立开关：字段是否为空已经完整表达启用状态。

完整 Suite 和单条业务用例都遵循该持久配置；本次变更不提供 Run 级临时覆盖，以保持最小范围。

## Risks / Trade-offs

- **[业务用例仍包含登录步骤]** → 文档明确要求用户从 Excel 中移除重复登录步骤，并将前置条件改为“用户已登录”；系统不猜测或重写业务步骤。
- **[认证使用 sessionStorage]** → Playwright `storageState` 不包含 sessionStorage；首版接受该限制，真实内网冒烟若失败再单独设计补充机制。
- **[同一账号并发修改服务端状态]** → 本变更只隔离浏览器上下文，不解决服务端数据竞争；继续由 Suite 的 `parallelism` 配置控制。
- **[进程被强制终止时临时文件残留]** → 使用系统临时目录并在所有可控终态中 `finally` 清理；不为不可恢复的进程强杀引入后台清理服务。
- **[配置的登录用例被移除]** → 保存时和执行前双重校验，执行前发现无效引用时快速失败并给出明确错误。

## Migration Plan

1. 数据库迁移为 `suite_settings` 增加可空列，现有 Suite 默认值为空，行为不变。
2. 部署后由用户在 Excel 中增加登录用例、移除业务用例的重复登录步骤，并在 Suite 设置中选择该用例。
3. 回滚时忽略或删除该可空列即可；未配置 Suite 与旧版本完全兼容。
