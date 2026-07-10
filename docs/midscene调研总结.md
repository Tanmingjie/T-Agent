# Midscene 深度调研总结

日期：2026-07-10

## 结论

Midscene 不是简单的“AI 点按钮 SDK”，而是一个已经成型的视觉 UI 自动化执行引擎。它最适合放在 T-Agent 下面，承担 HMI / SVG / canvas / 颜色态 / 弱语义控件的执行与视觉断言，不适合替代 T-Agent 的平台层。

推荐定位：

```text
T-Agent 负责：测试用例、项目知识、执行编排、权限、结果、报告平台
Midscene 负责：视觉 GUI 执行、视觉定位、视觉断言、页面状态抽取
```

## 一句话定位

Midscene = vision-driven UI automation engine。

T-Agent = 测试用例、项目知识、执行编排、权限、结果、报告平台。

二者最合理的关系：

```text
T-Agent 管测试平台和流程治理
Midscene 管视觉 GUI 执行、视觉定位、视觉断言、页面状态抽取
```

## 源码结构观察

本次阅读的是本地安装的 `@midscene/web@1.10.2` / `@midscene/core@1.10.2`。

关键文件：

- `.midscene-poc/node_modules/@midscene/core/dist/types/agent/agent.d.ts`
- `.midscene-poc/node_modules/@midscene/core/dist/types/agent/tasks.d.ts`
- `.midscene-poc/node_modules/@midscene/core/dist/types/device/index.d.ts`
- `.midscene-poc/node_modules/@midscene/web/dist/types/playwright/index.d.ts`
- `.midscene-poc/node_modules/@midscene/web/dist/types/puppeteer/base-page.d.ts`
- `.midscene-poc/node_modules/@midscene/shared/dist/types/env/types.d.ts`

核心抽象：

```text
Agent
  -> Service: locate / extract / assert / describe
  -> TaskExecutor: planning / action / query / waitFor
  -> AbstractInterface: screenshot / actionSpace / DOM tree / input primitives
  -> ReportGenerator: HTML report / execution dump
```

这说明 Midscene 自己已经有类似 T-Agent ReAct loop 的执行内核：规划、执行、重规划、定位、断言、报告、cache、progress event 都有。

## 原生能力

Midscene 主要 API 分两类。

Auto planning：

```ts
await agent.aiAct('打开进料阀，然后确认页面进入进料状态');
await agent.ai('完成登录流程');
```

Instant action：

```ts
await agent.aiTap('泵P1');
await agent.aiInput('用户名输入框', { value: 'admin' });
await agent.aiScroll('告警列表', { scrollType: 'untilBottom' });
await agent.aiAssert('进料阀是红色');
const state = await agent.aiQuery('{ level: string, valve: string } 当前状态');
```

官方也明确区分：`aiAct` / `ai` 自动规划更聪明但慢、依赖模型；`aiTap` / `aiInput` / `aiAssert` / `aiQuery` 更可控，更适合测试脚本。

## 视觉路线与 DOM

Midscene 1.x 的动作定位主路线是 pure vision。官方说 1.0 后移除了 DOM-extraction compatibility mode，UI actions 和 element localization 只走纯视觉；原因是 DOM 路线在 canvas、CSS background-image、跨域 iframe、弱 a11y 控件上不稳定。

但它并不是完全不用 DOM。源码里 WebPage 仍有：

```text
getElementsInfo()
getElementsNodeTree()
cacheFeatureForPoint()
rectMatchesCacheFeature()
```

并且 `aiQuery` / `aiBoolean` / `aiString` / `aiAssert` 支持 `domIncluded: true | 'visible-only'`，用于抽不可见属性、链接、DOM 辅助信息。

准确判断：

```text
动作定位：视觉为主
页面理解/数据抽取：可选视觉 + DOM
```

## 模型策略

源码和文档都支持多模型分工：

```text
default model  -> locate / 普通任务
planning model -> aiAct 规划
insight model  -> aiQuery / aiAssert / aiAsk
```

环境变量前缀：

```text
MIDSCENE_MODEL_*
MIDSCENE_PLANNING_MODEL_*
MIDSCENE_INSIGHT_MODEL_*
```

这和 T-Agent 未来拆“执行定位模型 / 规划模型 / 裁决模型”高度一致。

当前限制：只有 DeepSeek 文本模型时，无法验证 Midscene 的核心价值。Midscene 的核心场景需要视觉模型，例如 Qwen3-VL、Qwen3.5/3.6、Doubao Seed、GLM-V、Gemini、UI-TARS 等。

## 报告与可观测性

Midscene 的报告能力比较成熟：

```text
generateReport
persistExecutionDump
single-html / html-and-external-assets
onDumpUpdate
addProgressListener
recordToReport
recordErrorToReport
```

对 T-Agent 很关键。引入 Midscene 后不能变成另一个黑盒，必须把 Midscene HTML report、execution dump、截图、`aiQuery` JSON 原样落到 T-Agent artifacts 里。

## 集成方式

Midscene 支持多种入口：

```text
Chrome 插件：人工试用
Bridge Mode：复用当前 Chrome 登录态
PlaywrightAgent：正式平台集成首选
PuppeteerAgent：备选
YAML：可把 TestSpec 转成 Midscene 脚本
Gherkin：和 BDD 方向有潜在契合
Custom Interface：未来桌面/HMI 客户端可能有用
```

对 T-Agent 推荐顺序：

```text
内网验证：Chrome 插件 / Bridge Mode
正式接入：PlaywrightAgent Node sidecar
中期探索：TestSpec -> Midscene YAML
```

## 对 T-Agent 的建议架构

不要直接重写主执行链。先做可插拔 sidecar：

```text
T-Agent Python
  -> visual_executor.py
  -> node scripts/midscene_runner.ts
  -> PlaywrightAgent / Bridge Agent
  -> JSON result + report path + screenshots
```

第一版 runner 只支持三类命令：

```json
{"action": "act", "text": "点击进料阀"}
{"action": "assert", "text": "进料阀是红色"}
{"action": "query", "text": "{level:string, valve:string} 当前状态"}
```

T-Agent 的 phase 映射：

```text
phase.steps    -> aiTap / aiInput / aiAct
phase.expected -> aiAssert 或 aiQuery + T-Agent 裁决
```

更推荐：

```text
Midscene 负责执行和状态抽取
T-Agent 保留最终裁决权
```

也就是优先用 `aiQuery` 抽结构化状态，再由 T-Agent assertion engine 判定 PASS/FAIL。

## 风险

1. 必须有视觉模型。DeepSeek 文本模型不够。
2. 纯视觉成本和延迟高于 DOM / selector。
3. `aiAct` 把规划、定位、执行混在一起，失败排障不如 `aiTap` / `aiAssert` / `aiQuery` 清晰。
4. 本地试 `@midscene/web@1.10.3` 遇到 npm 包缺 `types.mjs` 的打包问题，`1.10.2` 正常越过模块加载。接入初期建议锁 `1.10.2`。
5. 安全上要接 T-Agent 的高危操作权限拦截，不能让视觉 Agent 自由提交、删除、发布、生产操作。

## 推荐落地路线

```text
P0：Chrome 插件验证 HMI Tap / Assert / Query
P1：Node runner，JSON stdin/stdout
P2：T-Agent 手动选择 Midscene 执行某个 phase
P3：Hybrid 路由，只有视觉场景或现链路失败时切 Midscene
```

这条路能把最难维护的“页面视觉接地”交给 Midscene，同时保住 T-Agent 已经沉淀的平台能力。

## 参考资料

- Midscene GitHub: https://github.com/web-infra-dev/midscene
- Midscene API Reference: https://midscenejs.com/api
- Midscene Model Strategy: https://midscenejs.com/model-strategy
- Midscene YAML Automation: https://midscenejs.com/automate-with-scripts-in-yaml
- Midscene Playwright Integration: https://midscenejs.com/integrate-with-playwright

