# Midscene 整体集成方案

日期：2026-07-13

## 结论

T-Agent 的执行主链路调整为：

```text
Excel 用例
  -> TestCase
  -> TestSpec(intent + phases[{steps, expected}])
  -> Midscene 视觉执行
  -> ExecutionRecord / run_event / artifacts
  -> 前端执行过程与结果展示
```

Midscene 不再作为可选后端，而是唯一执行内核。原 ReAct / playwright-mcp 执行链路不再作为产品能力保留。

## 保留的能力

- 用例管理：Suite / Case / Excel 上传 / Run 历史。
- 翻译链路：`intelligence/pre_analysis.py` 继续把业务用例翻译为阶段化 TestSpec。
- 项目知识：项目级“用例规范”和执行前勾选的 Skill 会作为 Midscene 执行上下文。
- 执行编排：`Orchestrator`、run 状态、SSE、run_event 持久化仍保留。
- 结果模型：`ExecutionRecord`、阶段断言、截图、Midscene report、runner 日志继续落库和展示。

## 移除或降级的能力

- 移除 ReAct / Midscene 执行内核选择。
- 移除执行期 playwright-mcp 依赖，不再启动 MCP 浏览器服务。
- 权限审批、Custom Tool、词汇表、DOM 定位、自愈 healing 不进入 Midscene 主链路。
- 旧 codegen 暂不作为 Midscene 主链路产物；后续若需要，应基于 Midscene action/report 重新设计。
- `run_queue.executor_backend` 字段移除，queue 只负责排队，不再携带执行内核选择。

## 后端主链路

```text
api/routers/execution.py
  -> RunOptions(skill_names)
  -> api/run_executor.py::execute_run
  -> MidsceneCaseAgent
  -> VisualExecutor
  -> scripts/midscene_runner.js
  -> @midscene/web/playwright PlaywrightAgent
```

`execute_run` 固定构造 `MidsceneCaseAgent`。`force_skill_names` 不再走 ReAct prompt preload，而是把命中的项目 Skill 内容追加到 Midscene 的翻译/执行上下文。

## Runner 契约

Python 侧通过 stdin 向 Node runner 传入：

```json
{
  "run_id": "...",
  "case_id": "...",
  "base_url": "...",
  "spec": {
    "intent": "...",
    "phases": [
      { "steps": ["..."], "expected": "..." }
    ]
  },
  "artifact_dir": "storage/midscene/<run>/<case>",
  "model_config": {
    "modelName": "...",
    "apiKey": "...",
    "baseURL": "...",
    "family": "..."
  }
}
```

Node runner stdout 只输出最终 JSON；日志写 stderr 并被 Python 保存为 `runner-stderr.log`。

## 前端入口

点击执行时仍弹确认框，但不再选择执行内核。弹框只展示：

- 当前执行方式：Midscene 视觉执行。
- 本次强制加载的 Skill。

请求体只发送：

```json
{
  "skill_names": ["..."]
}
```

## 配置

Midscene 是唯一执行内核，但仍保留显式开关防误跑：

```env
MIDSCENE_ENABLED=1
MIDSCENE_NODE_CMD=node
MIDSCENE_RUNNER=scripts/midscene_runner.js
MIDSCENE_RUNNER_TIMEOUT_SECONDS=300
MIDSCENE_MODEL_NAME=
MIDSCENE_MODEL_API_KEY=
MIDSCENE_MODEL_BASE_URL=
MIDSCENE_MODEL_FAMILY=
```

依赖已在根目录 `package.json` 工程化：

```text
@midscene/web
playwright
```

## 当前边界

- 已接通主链路，但真实内网 live 仍需用视觉模型验证。
- 执行结果展示优先使用 Midscene 原生 report。平台负责保存、索引、打开 report，并补充展示截图、
  runner stdout/stderr、阶段裁决摘要。
- 执行中过程可视化降级为低优先级。当前只展示平台侧状态和执行中提示，不再强行复刻旧 ReAct
  的逐 token/逐工具时间线；如后续需要，再基于 Midscene 原生 progress/report 事件桥接，而不是自造
  第二套过程模型。
- 停止执行当前只在启动前检查，runner 执行中协作式中止待补。

## 结果展示重设计

原则:

1. Midscene 已经产出详细报告，报告是执行过程细节的主视图。
2. T-Agent 只做“入口 + 索引 + 归档 + 阶段裁决摘要”，避免用旧 ReAct 时间线误导用户。
3. 截图、日志、report HTML 都作为 artifacts 暴露，便于内网失败排查。

后端:

- `GET /suites/{suite}/runs/{run}/cases/{case}/result` 返回 `midscene_artifacts`:
  - `report_url`: Midscene report 直达入口。
  - `files[]`: artifact 清单，含 `path/name/kind/size/url`。
- `GET /suites/{suite}/runs/{run}/cases/{case}/artifact?path=...` 读取单个 artifact。
- artifact 读取限制在本次用例的 `storage/midscene/<run>/<case>/` 目录内。

前端:

- 测试结果页优先展示“打开 Midscene 报告”。
- 同页展示阶段截图缩略图、runner 日志、其他 artifact。
- 阶段裁决仍展示在平台内，用于快速判断 PASS/FAIL 与失败阶段。

## 清理状态

已完成:

1. 主链路全切 Midscene。
2. Midscene report / artifacts 作为结果主视图接入。
3. 物理删除旧 ReAct 执行链路模块：`agent.py`、`react_loop.py`、`prompt.py`、`step_plan.py`、`page_probe.py`、`healing.py`、`mcp_client`。
4. 移除 ReAct 专属测试，保留翻译、编排、结果、Midscene fake runner 测试。

待后续另起任务:

1. 清理历史文档中的旧设计描述。
2. 评估 `permission.py` / `tools.py` 这类通用能力是否仍作为平台能力保留，或随旧 codegen/断言路径继续收缩。
