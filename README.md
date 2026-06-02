# AI 自动化测试平台

内网 Web 业务测试自动化执行平台。核心链路：

```
业务测试用例(Excel)
  → 生成 TestSpec(结构化执行规格 + 断言)
  → AI Agent 驱动浏览器执行(playwright-mcp / ReAct)
  → 结构化断言验证(规则引擎,非 LLM 眼判)
  → 产出可维护的 pytest-bdd Playwright 代码
```

## 项目状态

| 阶段 | 范围 | 状态 |
|------|------|------|
| 一: 主干跑通 | T-01~T-10(TestSpec/ReAct/断言引擎/MCP) | ✅ 完成 |
| 二: Harness 能力 | T-11~T-19(自愈/Context Compact/Hooks/Session/Skill/Permission/Orchestrator/Custom Tool) | ✅ 完成 |
| 三: 输出层 | T-20~T-22(BDDGenerator/SQLModel 持久化/词汇表+Scanner) | ✅ 完成 |
| 四: 工程化界面 | T-23~T-27(FastAPI 后端/React 前端/SSE/Repository 抽象层) | ✅ 完成 |
| 五: 用例管理集成 | 预留 `external_id` | 🔜 待定 |

**测试: 293 passed / 1 skipped**

## 实现原则(务必遵守)

1. **前后端彻底分离** — 功能通过 HTTP API 暴露,前端只调 API。React ↔ FastAPI,即使单机运行也视为独立两端。
2. **数据层抽象** — 用 SQLModel,业务代码不直接写 SQL(SQLite → PostgreSQL 只改连接串)。
3. **输入/输出抽象** — 所有来源产出同一个 `TestCase`,所有执行结果落 `ExecutionRecord`。
4. **数据预留同步字段** — 核心表预留 `updated_at` / `owner` / `external_id`。
5. **分阶段、可验证** — 严格按实施计划推进,跑通一阶段再进下一阶段。

## 关键约束

- 浏览器层 **必须用 `playwright-mcp` 的 stdio 模式**,绝不用 CDP HTTP 连接(内网代理会拦截 → 504)。
- 断言 **必须由规则引擎确定性验证**,不让 LLM 眼判 PASS/FAIL。
- 本地 LLM(Qwen3 via Ollama/LiteLLM)的 `tool_call` 需做 **格式容错**,偶发格式错误不得搞崩执行循环。

## 环境

- Python **3.11** + `uv`
- Node: `npx @playwright/mcp`(浏览器层,stdio)
- LLM: 本地 Qwen3 397B,经 Ollama,由 LiteLLM 接入

### 安装

```bash
# 后端
uv venv --python 3.11 && source .venv/bin/activate
uv pip install -r requirements.txt

# 前端
cd frontend && npm install
```

### 运行测试

```bash
source .venv/bin/activate
python -m pytest -q
```

### 启动服务

```bash
# API 服务
uvicorn api.server:app --reload --port 8000

# 前端开发服务器
cd frontend && npm run dev

# CLI 运行单条用例
python cli/run_case.py --excel examples/saucedemo_cases.xlsx --case-id TC101 --base-url https://www.saucedemo.com
python cli/run_case.py --excel <用例.xlsx> --case-id <ID> --spec-only   # 只生成并打印 TestSpec
python cli/run_case.py --check-llm                                       # LLM 连通性自检
```

## 目录结构

```
T-agent/
├── harness/          # Agent 核心(ReAct/断言/自愈/Prompt/LLM/录制…)
├── mcp_client/       # MCP 官方 SDK 封装(stdio 连 playwright-mcp)
├── intelligence/     # Page Intelligence(词汇表 / 用例预解析 / TestSpec 生成)
├── input/            # 输入层(models 结构体 + Excel 解析)
├── codegen/          # 输出层(BDD 代码生成)
├── api/              # FastAPI 后端(server + 路由 + Repository)
│   ├── routers/      #   suites/execution/permission/results/vocabulary
│   └── repository.py #   抽象层 + SQLModel 实现
├── storage/          # SQLModel 模型 + SQLite 持久化
├── frontend/         # React + Vite + Tailwind 控制台
│   └── src/
│       ├── pages/    #   SuiteList/SuiteDetail/RunConsole/CaseResult/CodeViewer/Vocabulary
│       ├── components/ # PermissionDialog/ProgressBar/StepListPanel/FileTree
│       └── api/      #   client.ts(API 封装)
├── cli/              # 命令行入口
├── tests/            # 单元测试(293 passed)
├── examples/         # 验收入口 + saucedemo 用例
├── 实现规格说明书.md  # 唯一真相源:所有模块详细规格
└── 产品设计文档_v2.0.md # 产品设计原文
```

## 核心模块速查

| 模块 | 入口 | 职责 |
|------|------|------|
| Agent 总装 | `harness/agent.py` | TestCaseAgent.run() 串起整条执行链路 |
| ReAct 循环 | `harness/react_loop.py` | Reason→Act→Observe 主循环 + 护栏 |
| 断言引擎 | `harness/assertion.py` | 规则引擎确定性验证,裁决 PASS/FAIL |
| 自愈 | `harness/healing.py` | 断言侧目标重定位 + 操作侧回灌 |
| Prompt 构建 | `harness/prompt.py` | 分层 System Prompt(Base+Context+Task+Tools) |
| 上下文压缩 | `harness/context.py` | L1 旧观察折叠 + L2 快照截断 |
| LLM 封装 | `harness/llm.py` | LiteLLM + tool_call 容错 + token 统计 |
| MCP 客户端 | `mcp_client/client.py` | stdio 连 playwright-mcp, 工具格式转换 |
| 页面探测 | `harness/page_probe.py` | 解析 A11y 树, 语义匹配 |
| Suite 调度 | `harness/orchestrator.py` | 串行执行 + 用例间隔离 + 结果汇总 |
| 权限管控 | `harness/permission.py` | 高危词+prod 锁, API 侧审批 |
| FastAPI 入口 | `api/server.py` | 5 路由子模块 + SSE 推送 |
| Repository 层 | `api/repository.py` | 抽象基类 + SQLModel 实现 |
| BDD 代码生成 | `codegen/bdd.py` | TestSpec → pytest-bdd Playwright 代码 |
| Page Intelligence | `intelligence/` | 词汇表 + Scanner + 用例预解析 |

## 格式化

```bash
isort harness mcp_client input intelligence cli api storage tests && black harness mcp_client input intelligence cli api storage tests
```