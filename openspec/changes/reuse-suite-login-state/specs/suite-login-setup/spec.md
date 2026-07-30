## Purpose

允许一个 Suite 在单次 Run 内先执行一次登录准备，并将登录成功后的浏览器认证状态复用于后续独立用例，从而避免每条业务用例重复登录。

## ADDED Requirements

### Requirement: Suite can select a login setup case
系统 SHALL 允许用户从当前 Suite 的现有用例中选择一条登录准备用例，也 SHALL 允许不配置登录准备用例。

#### Scenario: Configure an existing case
- **WHEN** 用户在 Suite 设置中选择当前 Suite 的一条现有用例作为登录准备用例
- **THEN** 系统保存该配置，并在后续执行中将该用例识别为登录准备

#### Scenario: Disable login setup
- **WHEN** 用户清空 Suite 的登录准备用例配置
- **THEN** 后续执行保持当前的逐用例独立启动行为，不额外运行登录准备

#### Scenario: Reject a case from another suite
- **WHEN** 用户尝试将不属于当前 Suite 的用例配置为登录准备用例
- **THEN** 系统拒绝保存该配置

### Requirement: Login setup runs once before requested business cases
当 Suite 配置了登录准备用例时，系统 SHALL 在本次 Run 的业务用例开始前先执行该登录准备用例，且同一 Run 内最多执行一次。

#### Scenario: Run the full suite
- **WHEN** 用户执行配置了登录准备用例的整个 Suite
- **THEN** 系统先执行一次登录准备用例，并仅在其成功后启动其余业务用例

#### Scenario: Run one business case
- **WHEN** 用户单独执行配置了登录准备用例的某条业务用例
- **THEN** 系统先执行一次登录准备用例，并仅在其成功后执行目标业务用例

#### Scenario: Run the login setup case itself
- **WHEN** 用户单独执行被配置为登录准备的用例
- **THEN** 系统只执行该用例一次，不重复前置执行自身

#### Scenario: Preserve setup result
- **WHEN** 登录准备用例执行完成
- **THEN** 系统将其执行结果与普通用例一样保存在本次 Run 中

### Requirement: Successful setup provides isolated authenticated contexts
登录准备用例成功后，系统 SHALL 捕获本次 Run 的浏览器认证状态，并 SHALL 在后续每条业务用例各自独立的浏览器上下文中加载该状态。

#### Scenario: Reuse authenticated state
- **WHEN** 登录准备用例成功且后续业务用例开始执行
- **THEN** 每条业务用例启动时已加载登录准备产生的认证状态，无需依赖前一条业务用例的浏览器上下文

#### Scenario: Start concurrent cases after setup
- **WHEN** Suite 配置的并发执行数大于 1
- **THEN** 系统等待登录准备成功并完成状态捕获后，才按配置的并发数启动其余业务用例

### Requirement: Setup failure stops dependent cases
如果登录准备用例失败或未产生可用认证状态，系统 MUST 停止本次 Run 的业务用例执行，不得在未登录状态下继续运行这些用例。

#### Scenario: Login setup fails
- **WHEN** 登录准备用例执行失败
- **THEN** 系统保留该失败结果，并将尚未执行的业务用例标记为未执行

#### Scenario: Authentication state capture fails
- **WHEN** 登录准备用例通过但认证状态无法捕获
- **THEN** 系统将登录准备视为失败，并且不启动业务用例

### Requirement: Authentication state is run-scoped
系统 MUST 将捕获的认证状态限制在当前 Run 内使用，不得自动复用于后续 Run，并 MUST 在 Run 结束、失败、中止或异常退出时清理临时认证状态。

#### Scenario: Normal completion cleanup
- **WHEN** 本次 Run 正常完成
- **THEN** 系统删除本次 Run 产生的临时认证状态

#### Scenario: Abnormal completion cleanup
- **WHEN** 本次 Run 失败、中止或异常退出
- **THEN** 系统仍尝试删除本次 Run 产生的临时认证状态

#### Scenario: Start a later run
- **WHEN** 用户再次执行同一个 Suite
- **THEN** 系统重新运行登录准备用例，而不是加载上一次 Run 的认证状态
