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

**测试: 334 passed / 1 skipped**(另 2 个 Windows 平台预存在失败:截图目录 / 命令替换)

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

- Python **3.11+**(下限,无上限;已含 3.14。`uv` 可选,亦支持标准 `venv` + `pip`)
  - 3.14 上若个别依赖(如 pydantic / uvicorn[standard])无预编译 wheel,**上调该依赖版本**即可,无需降 Python
- Node.js **18+**(前端 + `npx @playwright/mcp` 浏览器层,stdio)
- LLM: 本地 Qwen3 / DeepSeek 等,经 Ollama / LiteLLM 接入(可用 `.env` 配置)

> 下面分 **Windows(PowerShell)** 与 **macOS/Linux(bash)** 两套命令;每套又分
> **uv** 与 **标准 venv + pip(非 uv)** 两种安装方式,按需任选一条路径。

### 安装

#### Windows(PowerShell)

```powershell
# ── 方式 A:标准 venv + pip(非 uv,推荐内网/无 uv 环境)──
py -3.11 -m venv .venv            # 或 python -m venv .venv(需确保是 3.11)
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt

# ── 方式 B:uv ──
uv venv --python 3.11
.venv\Scripts\Activate.ps1
uv pip install -r requirements.txt

# 前端(两种方式相同)
cd frontend; npm install; cd ..
```

> PowerShell 若禁止运行脚本,先放开当前用户策略:
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

#### macOS / Linux(bash)

```bash
# ── 方式 A:标准 venv + pip(非 uv)──
python3.11 -m venv .venv && source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt

# ── 方式 B:uv ──
uv venv --python 3.11 && source .venv/bin/activate
uv pip install -r requirements.txt

# 前端(两种方式相同)
cd frontend && npm install && cd ..
```

### LLM 配置

在项目根创建 `.env`(自动加载),或用环境变量 / CLI flag:

```dotenv
LLM_MODEL=openai/qwen3          # 模型名需带 provider 前缀(openai/xxx、ollama/xxx)
LLM_API_BASE=http://127.0.0.1:11434/v1
LLM_API_KEY=sk-xxx
```

### 运行测试

```powershell
# Windows(PowerShell)
.venv\Scripts\Activate.ps1
python -m pytest -q
```

```bash
# macOS / Linux
source .venv/bin/activate
python -m pytest -q
```

> Windows 上有 2 个平台预存在失败(截图目录 / 命令替换),不影响主干。

### 启动服务

激活虚拟环境后(Windows: `.venv\Scripts\Activate.ps1`;*nix: `source .venv/bin/activate`),
以下命令两平台通用:

```bash
# API 服务(:8000,纯 API)
# 用 dev 启动器(--reload 只监视源码;直接 uvicorn --reload 会因 codegen 写
# storage/generated/*.py 触发重启、打断正在跑的 run)
python scripts/serve.py

# 前端开发服务器(:5173)
cd frontend && npm run dev

# CLI 运行单条用例
python cli/run_case.py --excel examples/saucedemo_cases.xlsx --case-id TC101 --base-url https://www.saucedemo.com
python cli/run_case.py --excel <用例.xlsx> --case-id <ID> --spec-only   # 只生成并打印 TestSpec
python cli/run_case.py --check-llm                                       # LLM 连通性自检

# 更复杂的开源验证用例(Automation Exercise:注册/下单/搜索,含多字段表单与结算流程)
python examples/make_automation_exercise_xlsx.py                          # 生成 xlsx(首次)
python cli/run_case.py --excel examples/automation_exercise_cases.xlsx \
    --case-id AE01 --base-url https://automationexercise.com --isolated --headless

# saucedemo 完整结算流程(多页表单 + 终态断言,已 live 绿)
python cli/run_case.py --excel examples/saucedemo_checkout.xlsx --case-id TC201 \
    --base-url https://www.saucedemo.com --isolated --headless

# 接入 Custom Tool(LLM 按需调用 + custom_tool 数据断言)
python cli/run_case.py --excel <用例.xlsx> --case-id <ID> --tools examples/custom_tools.yaml \
    --base-url <url> --isolated --headless
# (API 路径用环境变量:CUSTOM_TOOLS_YAML=examples/custom_tools.yaml)
```

> Windows 提示:多行命令的续行符 `\` 是 bash 写法;PowerShell 请改用反引号 `` ` ``,
> 或直接把参数写在一行。

## 目录结构

```
T-agent/
├── harness/          # Agent 核心(ReAct/断言/自愈/Prompt/LLM/录制…)
├── mcp_client/       # MCP 官方 SDK 封装(stdio 连 playwright-mcp)
├── intelligence/     # Page Intelligence(词汇表 / 用例预解析 / TestSpec 生成)
├── input/            # 输入层(models 结构体 + Excel 解析)
├── codegen/          # 输出层(代码生成)
│   ├── base.py       #   CodeGenerator 抽象 + GeneratedCode 落盘
│   ├── locators.py   #   框架无关的稳健定位器解析层(语义 target→Locator)
│   └── bdd.py        #   BDDGenerator(渲染 Locator→pytest-bdd Playwright)
├── api/              # FastAPI 后端(纯 API,:8000;不挂前端静态构建)
│   ├── routers/      #   suites/execution/permission/results/vocabulary
│   └── repository.py #   抽象层 + SQLModel 实现
├── storage/          # SQLModel 模型 + SQLite 持久化(screenshots/ + generated/)
├── frontend/         # React + Vite + Tailwind 控制台(:5173)
│   └── src/
│       ├── pages/    #   SuiteList/SuiteCases/SuiteHistory/SuiteRunDetail/SuiteSettings/Vocabulary
│       ├── components/ # RootLayout/SuiteLayout/IconRail/Drawer/CaseDrawerBody/Sidebar/...
│       └── api/      #   client.ts(API 封装)
├── cli/              # 命令行入口
├── tests/            # 单元测试(334 passed)
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
| FastAPI 入口 | `api/server.py` | 纯 API(5 路由子模块 + SSE 推送),不挂前端 |
| Repository 层 | `api/repository.py` | 抽象基类 + SQLModel 实现 |
| 定位器解析层 | `codegen/locators.py` | 框架无关:语义 target→稳健 Locator(词汇表来源) |
| BDD 代码生成 | `codegen/bdd.py` | 渲染 Locator → pytest-bdd Playwright 代码 |
| Page Intelligence | `intelligence/` | 词汇表 + Scanner + 用例预解析 |

## 格式化

```bash
isort harness mcp_client input intelligence cli api storage tests && black harness mcp_client input intelligence cli api storage tests
```