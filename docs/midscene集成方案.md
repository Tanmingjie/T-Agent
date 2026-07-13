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
- 执行中过程事件目前主要在 runner 结束后归一回传；Midscene 原生 progress/report 的实时桥接是下一步。
- 停止执行当前只在启动前检查，runner 执行中协作式中止待补。
- Midscene report 已落 artifact，但前端可以进一步提供直接打开入口。

## 后续清理顺序

1. 主链路全切 Midscene 并保持测试通过。
2. 基于真实内网 live 结果补齐 Midscene progress、report 展示和失败归因。
3. 按模块物理删除 ReAct 执行链路：`agent.py`、`react_loop.py`、`prompt.py`、`step_plan.py`、`mcp_client` 相关调用。
4. 移除 ReAct 专属测试，保留翻译、编排、结果、Midscene fake runner 测试。
