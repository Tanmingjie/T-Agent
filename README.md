# AI 自动化测试平台

内网 Web 业务测试自动化执行平台。核心链路:

```
业务测试用例(Excel)
  → 生成 TestSpec(结构化执行规格 + 断言)
  → AI Agent 驱动浏览器执行(playwright-mcp / ReAct)
  → 结构化断言验证(规则引擎,非 LLM 眼判)
  → 产出可维护的 pytest-bdd Playwright 代码
```

## 实现原则(务必遵守)

1. **前后端彻底分离** —— 功能通过 HTTP API 暴露,本地单机是「混合架构的单机退化版」。
2. **数据层抽象** —— 用 SQLModel,业务代码不直接写 SQL(SQLite → PostgreSQL 只改连接串)。
3. **输入/输出抽象** —— 所有来源产出同一个 `TestCase`,所有执行结果落 `ExecutionRecord`。
4. **数据预留同步字段** —— 核心表预留 `updated_at` / `owner` / `external_id`。
5. **分阶段、可验证** —— 严格按实施计划推进,跑通一阶段再进下一阶段。

## 关键约束

- 浏览器层 **必须用 `playwright-mcp` 的 stdio 模式**,绝不用 CDP HTTP 连接(内网代理会拦截 → 504)。
- 断言 **必须由规则引擎确定性验证**,不让 LLM 眼判 PASS/FAIL。
- 本地 LLM(Qwen3 via Ollama/LiteLLM)的 `tool_call` 需做 **格式容错**,偶发格式错误不得搞崩执行循环。

## 环境

- Python **3.11**(规格数据结构使用 `str | None` / `list[str]` 等 3.10+ 语法)
- Node:`npx @playwright/mcp`(浏览器层,stdio)
- LLM:本地 Qwen3 397B,经 Ollama,由 LiteLLM 接入

### 安装

```bash
uv venv --python 3.11
source .venv/bin/activate
uv pip install -r requirements.txt
```

### 运行测试

```bash
pytest
```

### 阶段一验收命令

```bash
python cli/run_case.py --excel cases.xlsx --case-id TC001
```

## 目录结构

```
T-agent/            # 项目根(= Claude Code 启动目录)
├── harness/        # Agent 核心(ReAct/断言/自愈/Prompt/LLM/录制…)
├── mcp_client/     # MCP 官方 SDK 封装(stdio 连 playwright-mcp;避让 mcp 包名)
├── intelligence/   # Page Intelligence(词汇表 / 用例预解析 / TestSpec 生成)
├── input/          # 输入层(models 结构体 + Excel 解析)
├── codegen/        # 输出层(BDD 代码生成)
├── api/            # FastAPI 后端(阶段四)
├── storage/        # SQLModel + 截图 + 生成代码
├── frontend/       # React + Vite(阶段四)
├── cli/            # 命令行入口(阶段一验收)
└── tests/          # 单元测试
```

## 实施进度

- [ ] **阶段一**:主干跑通(T-01 ~ T-10)—— 进行中
- [ ] 阶段二:Harness 能力补全(自愈 / Context Compact / Hooks / Session / 预置条件 / Skill / Permission / Orchestrator / Custom Tool)
- [ ] 阶段三:输出层(代码生成 / 持久化 / Page Intelligence)
- [ ] 阶段四:工程化界面(FastAPI + React)
- [ ] 阶段五:用例管理平台集成(留待最后,现仅预留 `external_id`)
