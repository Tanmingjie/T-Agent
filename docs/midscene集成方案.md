# Midscene 集成方案

日期：2026-07-10

## 结论

第一阶段采用“手动选择 Midscene 视觉执行”方案：现有 T-Agent ReAct / playwright-mcp 链路保持默认不变；用户在执行前显式选择 Midscene 后，该用例走视觉执行后端。

这条路线优先解决 HMI / SVG / canvas / 颜色态 / 弱语义控件这些当前最痛的执行场景，同时保住 T-Agent 已有的平台能力：TestSpec、Run 编排、SSE、权限、结果落库、报告和代码生成。

当前已确认：

```text
执行粒度：第一版接受按 phase 合并后调用 Midscene aiAct
模型条件：内网已有视觉模型
部署方式：模型内网部署，数据安全可保证
集成入口：第一版手动选择 Midscene，不做自动兜底
```

```text
T-Agent 负责：Excel / TestSpec / 项目知识 / Run 编排 / 权限 / 结果 / 报告 / codegen
Midscene 负责：视觉定位 / 视觉动作 / 视觉断言 / 页面状态抽取
```

## 集成原则

1. 不全量替换现有 ReAct。Midscene 先作为可选执行后端接入。
2. 不让 Midscene 变成新黑盒。必须落 report、dump、截图、query 原始结果。
3. 不破坏 TestSpec v2。继续使用 `phase.steps` 驱动执行，`phase.expected` 只在阶段边界验证。
4. 不依赖真实视觉模型做单测。第一阶段用 fake runner 测 Python / Node 边界和结果归一。
5. 真实内网截图外发必须显式确认模型端点在允许范围内。

## 总体架构

```text
前端执行弹窗
  -> RunOptions.executor_backend = react | midscene
  -> api/routers/execution.py
  -> api/run_executor.py
  -> Orchestrator
  -> AgentFactory
       react    -> TestCaseAgent
       midscene -> MidsceneCaseAgent
                    -> harness/visual_executor.py
                    -> node scripts/midscene_runner.ts
                    -> @midscene/web PlaywrightAgent
  -> ExecutionRecord
  -> run_event / results / artifacts
```

默认仍是：

```text
executor_backend = "react"
```

只有用户显式选择时才走：

```text
executor_backend = "midscene"
```

## 后端接口

### RunOptions

`api/routers/execution.py::RunOptions` 增加字段：

```python
class RunOptions(BaseModel):
    skill_names: list[str] = []
    executor_backend: str = "react"  # react | midscene
```

校验规则：

```text
react    -> 默认现有链路
midscene -> 视觉执行链路
其它值   -> 400
```

### execute_run

`api/run_executor.py::execute_run(...)` 增加参数：

```python
executor_backend: str = "react"
```

职责：

1. 从 API / queue 透传执行后端选择。
2. 构造 agent factory 时按后端创建 agent。
3. `midscene` 未启用或配置缺失时，生成清晰 FAIL 记录，不启动真实视觉执行。

### queue 模式

`run_queue` 需要增加：

```text
executor_backend: str = "react"
```

API 入队时写入，worker 领取后透传给 `execute_run`。

需要新增 alembic migration，并保留 SQLite fallback 轻量迁移。

## MidsceneCaseAgent

新增 `harness/midscene_agent.py`。

对外契约与 `TestCaseAgent.run(...)` 保持一致：

```python
async def run(
    self,
    case: TestCase,
    spec: TestSpec | None = None,
    ctx: ExecutionContext | None = None,
    step_callback=None,
    run_id: str | None = None,
    should_abort=None,
) -> ExecutionRecord:
    ...
```

职责：

1. 复用现有 spec 生成逻辑，生成阶段化 TestSpec。
2. 发 `phase/spec_ready/step_change` SSE，前端不需要先大改。
3. 调用 `VisualExecutor.run_case(...)`。
4. 把 Midscene 输出归一为：
   - `ExecutionRecord.steps`
   - `ExecutionRecord.case_assertions`
   - `ExecutionRecord.metrics`
   - `ExecutionRecord.final_result`
5. 失败时 fail-closed，不默认 PASS。

## VisualExecutor

新增 `harness/visual_executor.py`。

职责：

1. 构造 runner 输入 JSON。
2. 启动 Node runner。
3. 通过 stdin/stdout 交换 JSON。
4. 处理超时、非 JSON 输出、runner 退出码非 0。
5. 返回结构化 `VisualExecutionResult`。

建议核心数据结构：

```python
class VisualPhaseResult(BaseModel):
    phase_index: int
    status: str  # pass | fail
    expected: str = ""
    reason: str = ""
    evidence: str = ""
    query: dict = {}


class VisualExecutionResult(BaseModel):
    passed: bool = False
    stop_reason: str = ""
    phase_results: list[VisualPhaseResult] = []
    actions: list[dict] = []
    artifacts: dict = {}
    error: str = ""
```

runner 超时：

```text
passed=false
stop_reason=runner_timeout
error=...
```

runner 输出不是合法 JSON：

```text
passed=false
stop_reason=runner_bad_output
error=...
```

## Node Runner

新增 `scripts/midscene_runner.ts`。

初期锁版本：

```text
@midscene/web@1.10.2
```

原因：本地调研发现 `1.10.3` 存在 npm 包 `types.mjs` 缺失问题，`1.10.2` 能正常越过模块加载。

runner 输入：

```json
{
  "run_id": "xxx",
  "case_id": "TC001",
  "base_url": "https://...",
  "spec": {
    "intent": "...",
    "preconditions": [],
    "phases": [
      {
        "steps": ["点击进料阀", "点击启动按钮"],
        "expected": "进料阀变红，页面显示进料中"
      }
    ]
  },
  "artifact_dir": "storage/midscene/<run_id>/<case_id>",
  "model_config": {
    "modelName": "...",
    "apiKey": "...",
    "baseURL": "..."
  }
}
```

runner 输出：

```json
{
  "passed": false,
  "stop_reason": "phase_failed",
  "phase_results": [
    {
      "phase_index": 0,
      "status": "pass",
      "expected": "...",
      "reason": "...",
      "evidence": "...",
      "query": {}
    }
  ],
  "actions": [],
  "artifacts": {
    "report": "midscene-report.html",
    "dump": "execution-dump.json",
    "screenshots": ["step_001.png"]
  },
  "error": ""
}
```

## TestSpec 映射

T-Agent 继续使用 TestSpec v2：

```text
phase.steps    -> Midscene 执行
phase.expected -> 阶段边界验证
```

第一版执行策略：

```text
把同一 phase 下的 steps 合并成一个自然语言任务，交给 Midscene aiAct。
```

示例：

```text
完成以下操作：
1. 点击进料阀
2. 点击启动按钮
3. 设置液位为 80%
```

原因：

1. 最小化 T-Agent 侧动作解析。
2. 避免重新发明 “自然语言步骤 -> aiTap / aiInput / aiScroll” 的动作拆解器。
3. 优先验证 Midscene 对 HMI/SVG 的视觉接地能力。

阶段验证策略：

```text
优先 aiQuery 抽结构化状态，再由 T-Agent 归一成阶段裁决；
无法结构化时降级 aiAssert。
```

注意：`phase.expected` 不能进入执行侧 prompt，只能用于阶段验证，继续遵守 FG01。

## 前端入口

执行弹窗增加执行引擎选择：

```text
默认 ReAct 执行
Midscene 视觉执行
```

默认选中：

```text
默认 ReAct 执行
```

选择 Midscene 时展示轻提示：

```text
Midscene 会使用视觉模型理解页面截图，适合 SVG/HMI/canvas/颜色态场景。
```

前端请求：

```json
{
  "skill_names": ["工控HMI操作"],
  "executor_backend": "midscene"
}
```

## 环境变量

新增环境变量时必须同步 `.env.example`。

建议：

```text
MIDSCENE_ENABLED=0
MIDSCENE_NODE_CMD=node
MIDSCENE_RUNNER=scripts/midscene_runner.ts
MIDSCENE_RUNNER_TIMEOUT_SECONDS=300
MIDSCENE_MODEL_NAME=
MIDSCENE_MODEL_API_KEY=
MIDSCENE_MODEL_BASE_URL=
MIDSCENE_REPORT_MODE=single-html
```

行为：

```text
MIDSCENE_ENABLED != 1       -> 拒绝 Midscene 执行，返回清晰失败
MIDSCENE_MODEL_NAME 为空    -> fail-fast
MIDSCENE_MODEL_API_KEY 为空 -> fail-fast
```

## 产物与可观测性

Midscene 产物目录：

```text
storage/midscene/<run_id>/<case_id>/
```

必须保存：

```text
midscene-report.html
execution-dump.json
screenshots/*
query-results.json
runner-stdout.log
runner-stderr.log
```

`ExecutionRecord.metrics` 增加：

```json
{
  "executor_backend": "midscene",
  "midscene": {
    "stop_reason": "phase_failed",
    "report": "storage/midscene/xxx/TC001/midscene-report.html",
    "dump": "storage/midscene/xxx/TC001/execution-dump.json",
    "phase_count": 3
  }
}
```

`case_assertions` 保持现有结构，按 phase 输出：

```json
{
  "phase_index": 0,
  "status": "pass",
  "expected": "...",
  "reason": "...",
  "evidence": "...",
  "ai_judged": true
}
```

## 安全策略

第一阶段不做 action 级权限拦截，采用显式开关策略：

1. 用户必须手动选择 Midscene。
2. `MIDSCENE_ENABLED=1` 才允许执行。
3. 无视觉模型配置时不启动 runner。
4. 外部模型端点会接收页面截图，内网试用前必须确认模型服务在允许范围内。
5. 高危生产环境不建议启用 Midscene，后续再补 action 级权限网关。

## 测试计划

单测不连真实 LLM / 浏览器。

### API / queue

1. `RunOptions` 默认 `executor_backend="react"`。
2. 显式传 `midscene` 能透传到 `execute_run`。
3. 非法值返回 400。
4. queue 模式下 `executor_backend` 落 `run_queue`，worker 领取后透传。

### VisualExecutor

使用 fake runner：

1. runner 返回 pass JSON -> `VisualExecutionResult.passed=True`。
2. runner 返回 phase failed JSON -> 阶段失败被保留。
3. runner 超时 -> `runner_timeout`。
4. runner 非 JSON 输出 -> `runner_bad_output`。
5. runner exit code 非 0 -> `runner_failed`。

### MidsceneCaseAgent

1. spec 正常生成并进入 `ExecutionRecord.spec`。
2. phase_results 归一为 `case_assertions`。
3. 缺席 phase 补 fail 占位。
4. artifact 路径进入 `metrics.midscene`。
5. SSE `phase/spec_ready/step_change/case_result` 不破坏现有前端。

### 不做的测试

第一阶段不要求 saucedemo live 通过 Midscene。真实视觉冒烟等待可用视觉模型后再跑。

## 落地阶段

### P1：后端骨架

1. 增加 `executor_backend` 请求字段和透传。
2. 增加 queue 字段与迁移。
3. 增加 `VisualExecutor`。
4. 增加 `MidsceneCaseAgent`。
5. fake runner 单测打通。

### P2：前端入口

1. 执行弹窗增加执行引擎选择。
2. 默认保持 ReAct。
3. 请求体带 `executor_backend`。

### P3：真实 runner

1. 新增 `scripts/midscene_runner.ts`。
2. 锁 `@midscene/web@1.10.2`。
3. 支持 PlaywrightAgent。
4. 支持 report / dump / screenshots 输出。

### P4：结果页可观测

1. 结果详情展示 Midscene artifact 链接。
2. 展示 phase query 原始结果。
3. 展示 runner stderr 摘要。

### P5：自动兜底

稳定后再做：

```text
ReAct 定位失败 / SVG 不可观测 / 颜色态不可验证 -> 自动切 Midscene
```

第一阶段不做自动兜底。

## 风险

1. `aiAct` 把规划、定位、执行混在一起，排障不如现有 ReAct 细；第一版接受该成本，换取快速接入。
2. 纯视觉成本和延迟高于 a11y / DOM。
3. Midscene 版本需要锁定，避免 npm 包打包问题影响集成。
4. 视觉模型虽已确认内网部署，但仍需要用真实 HMI 用例验证 SVG 点击、颜色态、数值读取的稳定性。

## 当前推荐

先做 P1 + P2，完成“可选执行后端”的工程接入和 fake 测试。

随后直接进入 P3：用内网视觉模型跑真实 runner，并选 1-3 条 HMI 用例验证 SVG 点击、颜色态和数值读取。

