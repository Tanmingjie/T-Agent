# AI 自动化测试平台

内网 Web 业务测试自动化执行平台。当前主链路已切换为 Midscene 视觉执行:

```text
业务测试用例(Excel)
  -> 生成 TestSpec(阶段化执行规格 + 阶段预期)
  -> Midscene 视觉执行(aiAct / aiAssert)
  -> 结构化执行记录 + Midscene report / 截图 / runner 日志
  -> 前端执行过程与结果可视化
```

旧 ReAct / playwright-mcp 执行链路仍有部分代码和测试遗留,但不再作为产品主路径。

## 环境要求

- Python 3.11+
- Node.js 18+
- 翻译模型: 通过 `LLM_*` 配置,可用 DeepSeek / Qwen / Ollama / OpenAI-compatible 网关
- 视觉模型: 通过 `MIDSCENE_MODEL_*` 配置,必须是 Midscene 支持的多模态视觉模型

## 安装

### Windows PowerShell

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt

# 根目录 Node 依赖:Midscene runner
npm install --ignore-scripts --cache .npm-cache

# 前端依赖
cd frontend
npm install
cd ..
```

### Windows cmd

```bat
py -3.11 -m venv .venv
.venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt

npm install --ignore-scripts --cache .npm-cache
cd frontend && npm install && cd ..
```

### macOS / Linux

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt

npm install --ignore-scripts --cache .npm-cache
cd frontend && npm install && cd ..
```

## 配置

在项目根创建 `.env`。

### 翻译模型

```dotenv
LLM_MODEL=openai/qwen3
LLM_API_BASE=http://127.0.0.1:11434/v1
LLM_API_KEY=sk-xxx

# 内网直连 LLM、需绕过代理时:
# NO_PROXY=localhost,127.0.0.1,your-internal-llm-host
```

### Midscene 视觉模型

Midscene 执行阶段必须配置视觉模型。不要默认复用 `LLM_*`,避免把 DeepSeek 等文本模型误用于视觉定位。

```dotenv
MIDSCENE_MODEL_NAME=your-vision-model
MIDSCENE_MODEL_BASE_URL=https://your-internal-vision-gateway/v1
MIDSCENE_MODEL_API_KEY=sk-xxx
MIDSCENE_MODEL_FAMILY=qwen3.5
```

`MIDSCENE_MODEL_FAMILY` 是 Midscene 用来解析视觉定位坐标的模型族,必须填写。常见值:

```text
qwen3.5
qwen3
qwen3-vl
qwen2.5-vl
doubao-vision
gemini
glm-v
kimi
```

如果 `LLM_*` 本身就是视觉模型,可以显式复用:

```dotenv
MIDSCENE_REUSE_LLM_CONFIG=1
```

## 启动

```bash
# API 服务(:8000)
python scripts/serve.py

# 前端开发服务器(:5173)
cd frontend && npm run dev
```

打开 `http://localhost:5173`,进入测试任务后点击「执行」。确认弹框会显示 Midscene 视觉执行,可选择本次加载的项目 Skill。

## 执行前检查

在项目根执行:

```powershell
node -e "console.log(require.resolve('@midscene/web/playwright')); console.log(require.resolve('@playwright/test'))"
npm run midscene:check
python cli/run_case.py --check-llm
```

期望:

- 第一条命令能打印 `@midscene/web/playwright` 和 `@playwright/test` 的本地路径
- `npm run midscene:check` 通过
- `python cli/run_case.py --check-llm` 能连通翻译模型

## 运行测试

```powershell
python -m pytest tests/test_visual_executor.py tests/test_midscene_agent.py tests/test_midscene_runner.py tests/test_api_execution.py tests/test_run_executor.py -q
```

前端构建:

```powershell
cd frontend
npm run build
```

## 产物位置

Midscene artifacts 默认落在:

```text
storage/midscene/<run_id>/<case_id>/
```

常见文件:

- `initial.png`
- `phase-1.png` / `phase-1-failed.png`
- `runner-stdout.log`
- `runner-stderr.log`
- `midscene_run/report/midscene-report.html`
- `midscene_run/log/*.log`

这些产物已被 `.gitignore` 忽略。

## 常见问题

### Cannot find module '@playwright/test'

在项目根目录执行:

```powershell
npm install --ignore-scripts --cache .npm-cache
```

不要在 `frontend/` 目录执行这条命令。`frontend` 只安装前端依赖,根目录才安装 Midscene runner 依赖。

### Missing Midscene model config

说明 `.env` 缺少视觉模型配置。至少需要:

```dotenv
MIDSCENE_MODEL_NAME=
MIDSCENE_MODEL_BASE_URL=
MIDSCENE_MODEL_API_KEY=
MIDSCENE_MODEL_FAMILY=
```

### Default model family is required

说明 `MIDSCENE_MODEL_FAMILY` 未配置,或配置值不是 Midscene 支持的模型族。内网 qwen3.5 视觉模型可先试:

```dotenv
MIDSCENE_MODEL_FAMILY=qwen3.5
```

### reportFileName must not contain path separators

已在 runner 中修复。若仍出现,请确认后端已重启并运行最新代码。

### curl 能连模型,项目里报代理异常

Windows 下 Python/httpx 会读取系统代理。把内网模型主机写进 `.env`:

```dotenv
NO_PROXY=localhost,127.0.0.1,your-internal-llm-host
```

全部走内网可用:

```dotenv
NO_PROXY=*
```

诊断脚本:

```powershell
python scripts/diag_proxy.py
```

## 目录结构

```text
T-Agent/
├── api/                         FastAPI 后端
├── frontend/                    React + Vite 前端
├── harness/
│   ├── midscene_agent.py        Midscene 执行适配器
│   ├── visual_executor.py       Python <-> Node runner 边界
│   ├── llm.py                   LiteLLM 封装
│   └── orchestrator.py          Suite/Case 调度
├── scripts/
│   ├── serve.py                 API dev 启动器
│   └── midscene_runner.js       Midscene Node runner
├── intelligence/pre_analysis.py TestSpec 翻译
├── input/models.py              核心数据模型
├── storage/                     DB / artifacts
├── tests/                       单元测试
└── docs/midscene集成方案.md      Midscene 整体集成方案
```

## 核心模块

| 模块 | 入口 | 职责 |
|------|------|------|
| 执行总装 | `api/run_executor.py` | 构造 MidsceneCaseAgent,跑 Orchestrator,落 run_event / ExecutionRecord |
| Midscene Agent | `harness/midscene_agent.py` | 复用 TestSpec/ExecutionRecord 契约,归一 Midscene 阶段结果 |
| Visual Executor | `harness/visual_executor.py` | 调用 Node runner,保存 stdout/stderr |
| Node Runner | `scripts/midscene_runner.js` | PlaywrightAgent + aiAct/aiAssert + report/screenshot artifacts |
| TestSpec 翻译 | `intelligence/pre_analysis.py` | Excel 用例 -> 阶段化 TestSpec |
| 前端执行视图 | `frontend/src/pages/SuiteCasesPage.tsx` | 执行入口、过程和结果展示 |

## 当前边界

- Midscene 是唯一执行主链路。
- 真实内网 live 需要可用视觉模型配置。
- 执行中 progress 目前主要在 runner 返回后归一展示;后续可接 Midscene 原生 progress/report 实时事件。
- 旧 ReAct / playwright-mcp 相关模块后续会分批物理清理。
