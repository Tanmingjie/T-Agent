# 阶段四（工程化界面）设计文档

> 日期: 2026-06-02
> 对标参考: TestSprite Web Test（双栏详情 + Per-Step 截图）
> 基于: 实现规格说明书 §5/§6 + 阶段一~三已完成代码

---

## 1. 总体架构

### 1.1 部署形态

当前（单用户）FastAPI 同进程 serve React 构建产物，`uvicorn` 一个进程搞定。设计上前后端已分离：前端全部通过 REST + SSE 与后端通信，不含 SSR 或模板渲染。未来拆开只需 React 部署到 Nginx/CDN，FastAPI 独立部署，加 CORS 即可。

```
frontend/ (React + Vite + shadcn/ui)       api/ (FastAPI + uvicorn)
        │                                          │
        ├── fetch() ──── REST ────────────────────→│ api/routers/
        ├── EventSource ── SSE ────────────────────→│   ├─ suites.py
        │                                          │   ├─ execution.py
        │   部署：开发 Vite dev server              │   ├─ results.py
        │   生产：同 uvicorn serve 静态产物         │   └─ permission.py
        │                                          │       │
        │                                          │   api/repository.py (抽象)
        │                                          │       │
        │                                          │   storage/db.py (SQLModel)
        │                                          │
        │                                          │   harness/ (Agent 同进程)
```

### 1.2 前端路由

```
/suites                       → Suite 列表 + 创建（Slice 1）
/suites/:id                   → Suite 详情：用例列表 + 上传 + 预置条件确认（Slice 1）
/suites/:id/run               → 实时执行控制台（Slice 2）
/suites/:id/runs/:runId       → 某次执行总览（Slice 3）
/suites/:id/runs/:runId/case/:caseId → 用例结果详情 + 截图（Slice 3）
/suites/:id/runs/:runId/case/:caseId/code → 代码查看器（Slice 3）
/vocabulary                   → Page Intelligence 词汇表维护（Slice 4）
```

### 1.3 关键技术选型

| 层 | 技术 | 备注 |
|---|---|---|
| 后端框架 | FastAPI | async + SSE，同进程调用 Agent |
| 前端框架 | React + Vite | 规格书已定 |
| 组件库 | shadcn/ui（Radix + Tailwind） | 拷贝式组件，完全可控 |
| 代码编辑器 | @monaco-editor/react | 阶段一只做只读，编辑留后扩展 |
| 实时通信 | SSE（EventSource） | 单向推送，混合模式（状态推 + 详情拉） |
| Repository 层 | 抽象基类 + SQLModel 实现 | 路由依赖接口，不依赖具体存储 |
| Agent 调用 | 同进程 import harness | 不搞微服务 |

---

## 2. 数据模型（storage/db.py 新增/扩展）

### 2.1 现有模型（阶段三已建，不改）

Suite, TestCase, ExecutionRecord, SessionProfile, PageVocabulary — 来自 T-21，本次不改结构，只读/写。

### 2.2 新增模型

```python
class RunRecord(SQLModel, table=True):
    """每次执行 Suite 的 run 记录"""
    __tablename__ = "run_records"
    id: str          # UUID
    suite_id: str    # FK → suites.id
    status: str      # running | completed | aborted | failed
    total_cases: int
    passed_cases: int = 0
    failed_cases: int = 0
    started_at: datetime
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime

class SuiteSettings(SQLModel, table=True):
    """Suite 级执行配置"""
    __tablename__ = "suite_settings"
    suite_id: str          # FK → suites.id, unique
    permission_mode: str   # "trust" | "approve"  默认 "trust"
    # 后续扩展: llm_model, timeout_seconds, ...
    created_at: datetime
    updated_at: datetime
```

### 2.3 截图存储

文件系统：`storage/screenshots/<run_id>/<case_id>/<step_index>.png`

不存 DB，因为截图已按 `run_id` 隔离，每次执行独立存储，历史执行可完整回溯。

---

## 3. Repository 抽象层（新增 `api/repository.py`）

路由不直接依赖 `storage/db.py`，通过抽象接口调用：

```python
class SuiteRepository(ABC):
    async def create(suite: SuiteCreate) -> Suite
    async def get(suite_id: str) -> Suite | None
    async def list() -> list[Suite]
    async def delete(suite_id: str) -> bool

class TestCaseRepository(ABC):
    async def bulk_insert(suite_id: str, cases: list[TestCaseCreate]) -> int
    async def list_by_suite(suite_id: str) -> list[TestCase]
    async def get(case_id: str) -> TestCase | None
    async def update_precondition(case_id: str, update: PreconditionUpdate) -> bool

class ExecutionRepository(ABC):
    async def create_run(run: RunRecordCreate) -> RunRecord
    async def update_run(run_id: str, update: RunRecordUpdate) -> RunRecord
    async def get_run(run_id: str) -> RunRecord | None
    async def list_runs_by_suite(suite_id: str) -> list[RunRecord]
    async def get_result(run_id: str, case_id: str) -> ExecutionRecord | None
    async def list_results(run_id: str) -> list[ExecutionRecord]
    async def get_code(case_id: str) -> dict[str, str]  # {filename: content}

class VocabularyRepository(ABC):
    async def list(page: int, query: str | None) -> list[PageVocabulary]
    async def get(vocab_id: str) -> PageVocabulary | None
    async def create(entry: VocabularyCreate) -> PageVocabulary
    async def update(vocab_id: str, entry: VocabularyUpdate) -> PageVocabulary
    async def delete(vocab_id: str) -> bool
    async def bulk_upsert(entries: list[VocabularyCreate]) -> int
```

默认实现 `SQLModelRepository` 实现全部接口，测试用 fake 实现。

---

## 4. API 路由详细设计

### 4.1 Suite 管理（`api/routers/suites.py`）

| Method | Path | 请求体 | 响应 | 说明 |
|---|---|---|---|---|
| GET | /api/suites | — | `list[Suite]` | Suite 列表 |
| POST | /api/suites | `SuiteCreate` | `Suite` | 创建 Suite |
| GET | /api/suites/:id | — | `SuiteDetail` | Suite 详情（含 cases + runs） |
| DELETE | /api/suites/:id | — | 204 | 删除 Suite |
| POST | /api/suites/:id/upload | `FormData(file)` | `UploadResult` | Excel 解析 |
| GET | /api/suites/:id/cases/:caseId | — | `TestCaseDetail` | 用例详情+预置条件 |
| PUT | /api/suites/:id/cases/:caseId/precondition | `PreconditionUpdate` | 200 | 确认/修改预置条件 |

**Excel 上传流程：**
```
POST FormData(file) → 后端接收 → 调用 input/excel_parser.py 解析
→ 校验必填字段 → TestCaseRepository.bulk_insert()
→ 返回：{ total: N, inserted: N, warnings: [...] }
```

### 4.2 执行（`api/routers/execution.py`）

| Method | Path | 说明 |
|---|---|---|
| POST | /api/suites/:id/run | 触发执行，返回 {run_id} |
| GET | /api/suites/:id/stream?run_id=xxx | SSE 事件流 |
| GET | /api/suites/:id/settings | 获取执行配置 |
| PUT | /api/suites/:id/settings | 更新执行配置 |

**POST /run 流程：**
1. 校验 Suite 状态（已在执行中 → 409）
2. 创建 RunRecord
3. 启动 `asyncio.Task`: `_execute_suite(run_id, suite_id, sse_queue)`
4. 立即返回 `202 {"run_id": "..."}`

**_execute_suite 内部：**
```
for case in cases:
  → push SSE: case_start
  → orchestrator.run_case(case, sse_queue=queue)
      内部每一步 push SSE:
        step_change / step_done / assertion / healing / permission
  → push SSE: case_result
→ push SSE: suite_done
→ 更新 RunRecord
```

**SSE 事件类型：**

```json
{"event": "suite_start",   "data": {"run_id": "r1", "total_cases": 12}}
{"event": "case_start",    "data": {"case_id": "TC101", "title": "登录流程"}}
{"event": "step_change",   "data": {"case_id": "TC101", "step_index": 3, "status": "active", "description": "点击登录按钮"}}
{"event": "step_done",     "data": {"case_id": "TC101", "step_index": 3, "status": "done"}}
{"event": "assertion",     "data": {"case_id": "TC101", "assertion_id": 2, "verdict": "pass", "detail": "URL 匹配"}}
{"event": "healing",       "data": {"case_id": "TC101", "step_index": 4, "original": "..", "healed": ".."}}
{"event": "permission",    "data": {"case_id": "TC101", "event_id": "p1", "action": "click_delete", "reason": "高危操作"}}
{"event": "case_result",   "data": {"case_id": "TC101", "verdict": "PASS"}}
{"event": "suite_done",    "data": {"run_id": "r1", "passed": 11, "failed": 1}}
{"event": "error",         "data": {"case_id": null, "message": "连接超时"}}
```

**设计原则**：混合模式 — SSE 只推"必须立刻知道的事"（步骤状态、断言结果、permission），LLM 推理原文和工具调用细节通过 REST 按需拉取。

### 4.3 结果与代码（`api/routers/results.py`）

| Method | Path | 说明 |
|---|---|---|
| GET | /api/suites/:id/runs | 执行历史列表 |
| GET | /api/suites/:id/runs/:runId | 某次执行总览 |
| GET | /api/suites/:id/runs/:runId/cases/:caseId/result | 用例结果详情 |
| GET | /api/suites/:id/runs/:runId/cases/:caseId/code | BDD 代码 |
| GET | /api/suites/:id/runs/:runId/cases/:caseId/code/download | 下载 zip |
| GET | /api/screenshots/:run_id/:case_id/:step_index | 单张截图 |

**步骤双向关联**：codegen 输出中每个 step 加注释标记 `# step_<N>`，Monaco 点击步骤 → 正则匹配 → 滚动到对应函数；点击 Monaco 行 → 反向匹配 → 左侧步骤高亮。

### 4.4 Permission（`api/routers/permission.py`）

| Method | Path | 说明 |
|---|---|---|
| POST | /api/suites/:id/permission/:event_id | `{"choice": "approve" \| "reject"}` |

超时 30s 默认拒绝。当 Suite Settings 中 permission_mode="trust" 时，后端不推送 permission 事件。

### 4.5 词汇表（`api/routers/vocabulary.py`）

| Method | Path | 说明 |
|---|---|---|
| GET | /api/vocabulary?page=1&query=xxx | 分页+搜索 |
| POST | /api/vocabulary | 添加词汇 |
| PUT | /api/vocabulary/:id | 编辑词汇 |
| DELETE | /api/vocabulary/:id | 删除词汇 |
| POST | /api/vocabulary/scan | 触发扫描（调用 scanner.py） |

---

## 5. 前端设计

### 5.1 组件树

```
App
├─ SuiteListPage (/suites)
│   └─ SuiteCard × N
│       ├─ 名称、用例数、最后运行时间
│       └─ 删除确认 Dialog
│
├─ SuiteDetailPage (/suites/:id)
│   ├─ SuiteHeader（名称 + [上传] [执行]）
│   ├─ CaseTable
│   │   └─ CaseRow × N（展开 → 预置条件面板）
│   ├─ PreconditionPanel
│   │   ├─ StateHookBadge
│   │   ├─ ActionStepBadge
│   │   └─ AmbiguousBadge（可手动重分类）
│   └─ RunHistoryTable（执行历史）
│
├─ RunConsolePage (/suites/:id/run)
│   ├─ ProgressBar（整体进度）
│   ├─ CaseListPanel（左栏：用例列表 + 实时状态）
│   │   └─ CaseStatusIcon（▶ pulse / ✅ / ❌ / ⏳ / 🟡 healing）
│   ├─ DetailPanel（右栏：当前步骤详情）
│   │   ├─ StepProgressIndicator
│   │   ├─ ReasoningSummary（推理摘要）
│   │   └─ ScreenshotViewer（当前页面截图，可放大）
│   └─ PermissionDialog（弹窗：批准/拒绝 + 倒计时）
│
├─ CaseResultPage (/suites/:id/runs/:runId/case/:caseId)
│   ├─ StepListPanel（左栏：步骤 + 断言列表）
│   │   └─ StepRow（点击 → 切换右侧 Tab）
│   └─ DetailPanel（右栏：三 Tab）
│       ├─ TabSnapshot（截图）
│       ├─ TabCode（操作代码片段，只读）
│       └─ TabLog（LLM 推理 + 工具调用）
│
├─ CodeViewerPage (/suites/:id/runs/:runId/case/:caseId/code)
│   ├─ FileTree（左栏）
│   └─ MonacoEditor（右栏，只读 + 步骤关联）
│
└─ VocabularyPage (/vocabulary)
    ├─ SearchBar + ScanButton
    ├─ VocabularyTable（分页列表）
    └─ VocabularyEditPanel（展开编辑）
```

### 5.2 状态指示

| 图标 | 含义 | 样式 |
|---|---|---|
| ▶️ 青色 pulse | 正在执行 | `animate-pulse text-cyan-500` |
| ✅ 绿色 | 通过 | `text-green-500` |
| ❌ 红色 | 失败 | `text-red-500` |
| ⏳ 灰色 | 等待执行 | `text-gray-400` |
| 🟡 黄色 | 自愈中 | `text-yellow-500` |
| ⚠️ 红色脉冲 | 等待确认 | `animate-pulse text-red-500` |

### 5.3 错误状态处理

| 状态 | 处理 |
|---|---|
| Suite 不存在 | 404 页面 |
| Suite 正在执行，再次触发 | 409 → Toast "已有执行在进行" |
| SSE 连接断开 | EventSource onerror → "连接中断，重新连接中..." + 自动重连 |
| 截图文件不存在 | 占位图 + "截图未生成" |
| Excel 解析失败 | 返回错误行号 + 原因，前端红色高亮异常行 |
| 空状态（无 Suite） | 居中引导："创建你的第一个测试 Suite" |

---

## 6. 实施顺序（垂直切片）

### Slice 1: Suite 管理 + Excel 上传（T-23 子集 + T-25）
**后端：**
- `api/server.py` — FastAPI app + CORS + 挂载路由
- `api/repository.py` — Repository 抽象基类
- `storage/db.py` — SQLModel 实现 + 迁移
- `api/routers/suites.py` — Suite CRUD + Excel 上传路由

**前端：**
- Vite + React + shadcn/ui 脚手架
- `SuiteListPage`, `SuiteDetailPage`
- CaseTable + PreconditionPanel

**验收：** 浏览器创建 Suite → 上传 Excel → 看到解析后的用例列表 → 预置条件确认

### Slice 2: 执行控制台（T-23 子集 + T-24 + T-26）
**后端：**
- `api/routers/execution.py` — /run, /stream SSE
- `api/routers/permission.py` — Permission 确认路由
- `harness/orchestrator.py` 集成（接 SSE queue）

**前端：**
- `RunConsolePage` — SSE 消费 + 双栏布局
- `PermissionDialog`
- ProgressBar + StepProgressIndicator

**验收：** 点击执行 → 实时看到步骤变化 → Permission 弹窗 → 用例 PASS/FAIL

### Slice 3: 结果详情 + 代码查看器（T-23 子集 + T-27 主体）
**后端：**
- `api/routers/results.py` — 结果/截图/代码路由

**前端：**
- `CaseResultPage` — 步骤列表 + 三 Tab（截图/代码/日志）
- `CodeViewerPage` — Monaco 只读 + 步骤关联 + 下载

**验收：** 执行后查看历史结果 → 点击步骤看截图 → 查看代码 → 步骤跳转生效 → 下载 zip

### Slice 4: 权限配置 + 词汇表（T-24 补完 + T-27 尾巴）
**后端：**
- SuiteSettings CRUD（`api/routers/suites.py` 补）
- `api/routers/vocabulary.py`

**前端：**
- Settings 面板（Suite 详情页增加）
- `VocabularyPage`

**验收：** 切换 trust/approve 模式 → 词汇表 CRUD → 扫描触发

---

## 7. 与现有代码的集成点

| 模块 | 如何集成 | 改动 |
|---|---|---|
| `input/excel_parser.py` | 上传路由直接 import 调用 | 不改 |
| `intelligence/pre_analysis.py` | 执行路由调用（同 CLI 路径） | 不改 |
| `harness/orchestrator.py` | 接 SSE queue 推事件 | 加 `sse_queue` 参数 |
| `harness/agent.py` | 同进程调用，不加包装 | 不改 |
| `harness/permission.py` | 从同步回调改为 `asyncio.Event` 模式 | 加 `event_map` |
| `harness/recorder.py` | 落 ExecutionRecord，API 查询 | 不改（T-21 已通） |
| `codegen/bdd.py` | 加 `# step_<N>` 注释标记 | 小改 |
| `storage/db.py` | 已有模型不改，加新模型 | 加 RunRecord, SuiteSettings |
| `storage/screenshots/` | 路径改为 `<run_id>/<case_id>/<step>.png` | 路径规约调整 |

---

## 8. 测试策略

- `harness/agent.py` + `api/routers/execution.py` — 不连真实 LLM，用 fake agent 推 SSE 事件
- `api/routers/suites.py` + repository — 用 fake repository 驱动路由测试
- React 组件 — 单元测试不连后端，全部 mock API/fetch
- Repository 层 — 单元测试用 SQLite 内存数据库
- SSE 测试 — `httpx.AsyncClient` + `async for` 消费流

---

## 9. 最终验收标准

浏览器打开 `http://localhost:8000` → 创建 Suite → 上传 Excel → 配置执行 → 实时看步骤进度 → Permission 弹窗确认 → 查看历史结果 → 点击步骤看截图 → 打开 Monaco 查看代码 → 步骤跳转 → 下载代码 → pytest 回放通过。

此标准与 `CLAUDE.md` 和 `实现规格说明书.md` §6 阶段四验收标准一致。
