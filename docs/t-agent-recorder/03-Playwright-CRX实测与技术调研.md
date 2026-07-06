# Playwright CRX 实测与技术调研

> 调研与实测日期：2026-07-06  
> 调研对象：`ruifigueira/playwright-crx` v0.15.0  
> 结论：适合作为 T-Agent Recorder 的 fork 基线

## 1. 调研结论

在候选方案中，Playwright CRX 与需求最契合：

- 以 Chrome 扩展形式运行，非编码用户不需要启动命令行；
- 复用 Playwright 官方 Recorder 和 locator 生成能力；
- 能够在当前用户 Chrome 标签页中工作；
- 支持录制、断言、实时代码生成和本地回放；
- 能生成 Python pytest 等多种语言；
- 源码开放，许可证允许内部 fork；
- 本地录制天然避免平台浏览器并发和用户会话隔离问题。

它不是完整的低代码步骤编辑器。其主要界面是 Playwright Recorder 风格的工具栏、代码区和回放调用日志。T-Agent Recorder 首版应尊重这一交互模型，而不是先重做一套结构化步骤设计器。

上游仓库：[ruifigueira/playwright-crx](https://github.com/ruifigueira/playwright-crx)

## 2. 本次实际试用方式

本次不是只阅读网页或源码，而是在本地完成了以下操作：

1. 克隆上游仓库；
2. 安装 npm 依赖；
3. 构建 `playwright-crx` 核心包；
4. 构建官方 `examples/recorder-crx` 扩展示例；
5. 构建测试扩展与本地测试页面；
6. 下载项目锁定的 Chromium；
7. 以可见浏览器模式加载扩展；
8. 执行官方录制、断言、回放和语言相关端到端用例。

本地构建成功。核心构建产物包括扩展 Service Worker、侧边栏页面、代码编辑器和首选项页面。

依赖安装时 npm 报告了 30 个已知漏洞，其中包括高危和严重等级。该结果不等同于扩展可被直接利用，但正式 fork 前必须执行依赖审计、确认生产依赖影响并制定升级方案。

## 3. 实际交互认识

实际交互流程为：

1. 用户打开目标页面；
2. 点击扩展图标；
3. 扩展挂接当前标签页并展示侧边栏；
4. 用户在目标页面操作；
5. 生成代码在侧边栏实时更新；
6. 用户通过工具栏进入元素检查或断言模式；
7. 停止录制后，通过播放/继续操作执行回放；
8. 回放调用日志展示每一步的状态、耗时和失败位置；
9. 用户选择目标语言并保存代码。

需要特别记录的修正：

- CRX 不是以中文业务步骤列表为核心；
- 原生版本没有表单式步骤编辑、拖拽排序等能力；
- 回放使用内部结构化 Action，不是运行生成后的 Python 文件；
- Python 是生成结果视图，不能像 Playwright Test JavaScript 那样被完整反向解析为 Action；
- “失败步骤”来自回放调用日志；
- 插件天然绑定本地标签页，无需平台启动或管理浏览器。

## 4. 实测结果

### 4.1 用户前期手工试用结果

此前在真实目标页面上的手工试用确认：

- Chrome `debugger` 权限在当前环境允许使用；
- 常见操作录制正常；
- 能够添加断言；
- 回放稳定，并能指出失败步骤；
- Python 输出完整；
- 支持多种语言输出；
- 密码输入会以明文出现在结果中；
- 回放速度较快，异步页面可能因为尚未加载完成而失败；
- 自定义下拉框存在录制或回放问题；
- 新标签页场景未测试。

自定义下拉框由 `<div>` 和 `<span>` 构成，不是原生 `<select>`。因此它通常会被录制为“点击下拉容器 + 点击选项”，问题更可能来自 locator 唯一性、弹层 Portal、重复文本或动画时序，而不是 Playwright 的 `selectOption`。

### 4.2 本次官方端到端试用结果

在官方可见浏览器端到端用例中：

- “全部支持的操作与断言录制”通过；
- “断言录制后停止并回放”通过；
- 回放日志能够显示各断言成功状态；
- 扩展、侧边栏和 Recorder 注入链路能够正常工作。

部分依赖本地 `127.0.0.1:3000` 测试页面的用例出现 `ERR_CONNECTION_REFUSED`，包括部分语言切换和 smoke 用例。这些失败发生在测试页面导航阶段，不是 Recorder 断言或生成逻辑本身失败；因此不能把这些用例记为功能验证通过，也不能据此判定对应功能有缺陷。后续 fork 的基线测试需要先稳定测试 Web Server，再重跑完整套件。

## 5. 源码结构与关键链路

### 5.1 Chrome 调试传输

`src/server/transport/crxTransport.ts` 使用 `chrome.debugger` 把 Chrome DevTools Protocol 消息接入 Playwright。它负责：

- 标签页 attach/detach；
- CDP 消息转发；
- OOPIF 处理；
- Cookie 相关兼容；
- 新建标签页的自动挂接。

这是 CRX 能在普通用户 Chrome 中复用 Playwright 能力的基础，也是内部安全审批需要重点关注的权限。

### 5.2 CRX 应用层

`src/server/crx.ts` 创建 Playwright 的 Chromium Browser/Context，并负责标签页挂接、Recorder 展示和应用生命周期。

示例扩展的 `examples/recorder-crx/src/background.ts` 在后台懒加载单例应用，绑定活动标签页并打开侧边栏。

### 5.3 Recorder 与代码生成

Playwright Recorder 的注入脚本捕获用户操作，通过绑定发送结构化 Action。`contextRecorder.ts` 负责：

- 收集操作；
- 处理 frame 路径；
- 处理 page alias；
- 记录 popup、download、dialog 和 navigation 等信号；
- 调用不同语言的代码生成器。

侧边栏通过 Chrome Port 接收 `Source[]`，其中 `source.text` 是实时生成的代码。Python pytest 的默认文件名为 `test_example.py`。

### 5.4 本地回放

`src/server/recorder/crxPlayer.ts` 回放结构化 Action，支持导航、点击、按键、输入、勾选、取消勾选、原生选择以及常见断言。

当前观察到的限制：

- 单步默认超时硬编码为 5 秒；
- `setInputFiles` 在 CRX Player 中明确不支持；
- 没有通用固定等待 Action；
- 没有观察元素、文本、URL 或状态的长等待 Action；
- 没有 `waitForLoadState`、`waitForURL` 等专门步骤。

因此等待能力不能只在 Python 文本中插入一行代码。若要保证“本地回放”和“下载 Python”语义一致，需要同时改造 Action 类型、Recorder UI、Player、解析/序列化以及 Python 生成器。

## 6. 已确认能力与限制

| 项目 | 结论 |
|---|---|
| Chrome 扩展形态 | 支持，Manifest V3 |
| 当前标签页录制 | 支持 |
| 点击、输入、按键 | 支持 |
| 原生 `<select>` | 支持并有测试 |
| 自定义 `<div>/<span>` 下拉框 | 依赖点击和 locator，需要专项优化 |
| 常见断言 | 支持 |
| Python pytest | 支持 |
| 多语言输出 | 支持 |
| 本地回放 | 支持 |
| 失败步骤日志 | 支持 |
| 新标签页/Popup | 源码与测试支持，但本项目首版不纳入 |
| 文件上传录制 | 可生成相关 Action/代码 |
| 文件上传本地回放 | CRX Player 不支持 |
| 固定等待 | 当前不支持 |
| 长时间观察等待 | 当前不支持 |
| `slowMo` | 公共启动配置支持，示例未暴露设置 |
| 密码保护 | 生成结果可能包含明文 |
| Python 代码反向编辑 | 不支持完整反向解析 |

## 7. 等待能力设计影响

需要支持两类明确语义：

### 固定等待

“必须完整等待 3 分钟后再检查。”

```python
page.wait_for_timeout(180_000)
```

### 观察等待

“最长等待 3 分钟，状态一旦出现就继续。”

```python
expect(page.get_by_text("处理完成")).to_be_visible(timeout=180_000)
```

观察等待还可扩展为：

- 元素出现或消失；
- 文本出现；
- URL 匹配；
- 元素值或状态变化。

`slowMo` 适合解决动画、点击节奏和短加载问题，不能替代最长 3 分钟的业务状态等待。两者需要同时存在。

## 8. 敏感信息问题

Recorder 在输入事件发生时能够知道目标输入框类型，但现有 fill Action 主要保存 selector 和 text，没有把可靠的 `inputType` 元数据一路传到侧边栏。

因此，仅在最终 Python 文本中使用变量名或 selector 进行正则判断无法可靠识别全部密码字段。首版可以接受风险并增加提示；后续若要可靠处理，需要修改 Recorder Action 元数据，或在录制阶段额外采集敏感字段标记。

## 9. 许可证与维护

Playwright CRX 使用 Apache-2.0 许可证。内部 fork 时应：

- 保留许可证文件；
- 保留上游版权声明；
- 对修改内容做清晰说明；
- 检查其 vendored Playwright 源码及其他依赖的许可证；
- 在内部发布物中附带第三方软件声明。

当前调研版本为 Playwright CRX 0.15.0，内置 Playwright 版本为 1.53.0。该项目包含 vendored Playwright 源码，仓库和构建产物较大；升级时需要同时评估 CRX 适配和 Playwright 上游变化。

## 10. 其他候选方案

### Playwright 官方 Codegen

官方 Codegen 能录制点击、输入和断言，自动生成较稳定的 role、text、test id 等 locator，并支持 Python 等目标语言。它通常由命令行启动，同时打开浏览器和 Inspector，更适合开发人员，不如 Chrome 插件适合非编码测试经理。

资料：

- [Playwright Test Generator](https://playwright.dev/docs/codegen)
- [Playwright CLI](https://playwright.dev/docs/test-cli)

### Chrome DevTools Recorder

Chrome 自带 Recorder 支持录制、回放、编辑步骤、导入导出 JSON，并可导出 Puppeteer 格式，也支持通过扩展增加自定义导出格式。其优势是 Chrome 原生和结构化步骤编辑；不足是默认围绕 Puppeteer/Replay，而本项目目标是 Playwright Python，二次集成链路更长。

资料：

- [Chrome Recorder 概览](https://developer.chrome.com/docs/devtools/recorder/overview)
- [Chrome Recorder 功能参考](https://developer.chrome.com/docs/devtools/recorder/reference)

### Selenium IDE

Selenium IDE 是成熟的 Chrome/Firefox 录制回放扩展，支持命令编辑和插件扩展。它更偏 Selenium IDE 自身的命令与项目模型，若最终目标是标准 Playwright Python，需要额外转换和维护，技术一致性不如 Playwright CRX。

资料：

- [Selenium IDE 官方文档](https://www.selenium.dev/documentation/ide/)
- [Selenium IDE Getting Started](https://www.selenium.dev/selenium-ide/docs/en/introduction/getting-started)

## 11. 最终选择依据

| 维度 | Playwright CRX | Playwright Codegen | Chrome Recorder | Selenium IDE |
|---|---|---|---|---|
| 非编码用户入口 | Chrome 插件 | 需要命令行或 IDE | DevTools | Chrome 插件 |
| Playwright 原生链路 | 是 | 是 | 否 | 否 |
| Python Playwright 输出 | 支持 | 支持 | 默认不支持 | 需转换 |
| 本地回放 | 支持 | 支持 | 支持 | 支持 |
| 可 fork 定制 | 是 | Playwright 上游改造较重 | 扩展机制可用 | 是 |
| 与目标契合度 | 最高 | 较高 | 中等 | 中等 |

综合目标用户、单人维护成本、Python Playwright 输出和本地插件体验，选择 Playwright CRX 作为第一阶段基线。

## 12. 待验证事项

1. 固定等待的 Action、Player 和 Python 生成全链路；
2. 观察等待及 3 分钟以上超时；
3. `slowMo=500/1000` 对短加载失败的改善程度；
4. 真实业务自定义下拉框的生成 locator 和失败原因；
5. 完整官方端到端测试在稳定 Web Server 下的结果；
6. 公司内部扩展发布、签名、自动更新流程；
7. `chrome.debugger` 权限的内部安全审批；
8. 依赖漏洞是否进入生产扩展以及升级可行性；
9. 最终下载的 Python pytest 在标准独立环境中的执行一致性。

