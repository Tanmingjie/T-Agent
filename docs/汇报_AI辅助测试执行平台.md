---
marp: true
title: AI 辅助测试执行平台
paginate: true
size: 16:9
theme: default
style: |
  :root {
    --brand: #16a34a;
    --cyan: #06b6d4;
    --ink: #24292f;
    --muted: #6b7280;
    --line: #d8dee4;
    --band: #f3f4f6;
  }
  section {
    font-family: "Microsoft YaHei", "PingFang SC", sans-serif;
    color: var(--ink);
    padding: 48px 56px;
  }
  h1 { color: var(--ink); font-size: 38px; }
  h2 { color: var(--ink); font-size: 30px; border-left: 6px solid var(--brand); padding-left: 14px; }
  h3 { color: var(--muted); font-size: 20px; font-weight: 600; }
  strong { color: var(--brand); }
  table { font-size: 20px; }
  th { background: var(--band); }
  small { color: var(--muted); }
  /* 架构图组件 */
  .band {
    background: var(--band); border: 1px solid #e5e7eb; border-radius: 8px;
    padding: 8px 14px 12px; margin: 6px 0;
  }
  .band > .label { font-size: 15px; color: var(--muted); margin-bottom: 6px; }
  .row { display: flex; gap: 10px; justify-content: center; flex-wrap: wrap; }
  .box {
    background: #fff; border: 1px solid var(--line); border-radius: 6px;
    padding: 8px 12px; font-size: 16px; color: var(--ink); white-space: nowrap;
  }
  .box.core { background: #ecfeff; border-color: var(--cyan); color: #155e75; font-weight: 600; }
  .arrow { text-align: center; color: var(--cyan); font-size: 20px; line-height: 1; margin: 2px 0; }
  .archslide .box { font-size: 14px; padding: 6px 10px; }
  .archslide .band { padding: 6px 12px 8px; margin: 4px 0; }
  .note { color: var(--muted); font-size: 16px; }
  .pill { display:inline-block; background:#dcfce7; color:#166534; border-radius:6px; padding:2px 10px; font-size:16px; }
  .pill.todo { background:#fef9c3; color:#854d0e; }
  ul { font-size: 21px; }
---

<!-- _paginate: false -->

# AI 辅助测试执行平台

### 让测试人员只写业务用例，AI 跑执行、出结果、出代码

<br>

<small>汇报人：〔填〕 ｜ 部门：〔填〕 ｜ 日期：〔填〕</small>

<!--
开场：各位领导，我汇报的是我们在做的一个 AI 辅助测试执行平台。先讲为什么做、要做什么、现在到哪了。
-->

---

## 一、背景与痛点：两条老路都有天花板

<small>内网 Web 系统迭代频繁，每版本、每需求都要回归；功能越堆越多，人力固定 → 成瓶颈。</small>

| | 手工测试 | 自动化脚本 |
|---|---|---|
| 怎么做 | 逐条**手工点击** | 测试人员**手写脚本** |
| 门槛 | 低，但纯人力 | 高（要会编码） |
| 复用性 | **不可复用**，回归全重来 | 可复用 |
| 抗改版 | —— | **脆，改版即批量报红** |
| 主成本 | 执行人力〔填 N 人天/轮〕 | **维护成本滚雪球，常写完即弃** |

**手工灵活但不可复用，自动化可复用但又贵又脆 —— 这道缝，正是 AI 的切入点。**

<!--
第一条手工测试：最大问题是不可复用，每个版本重新人肉点一遍，回归就是重复劳动。〔填一轮回归 N 人天〕。
第二条写脚本：门槛高、维护更贵，页面一改版选择器失效、批量报红，很多团队写一批就弃用。
所以：手工灵活但不能复用，自动化能复用但又贵又脆。中间这道缝，正是 AI 切入点。
（这页停久一点，塞 1-2 个真实数字。）
-->

---

## 二、我们要做什么

<br>

<div class="row">
  <div class="box">业务用例<br>(Excel)</div>
  <div class="arrow">▶</div>
  <div class="box">AI 翻译<br>执行规格</div>
  <div class="arrow">▶</div>
  <div class="box core">AI 驱动浏览器<br>执行（自愈）</div>
  <div class="arrow">▶</div>
  <div class="box">规则引擎<br>裁决 PASS/FAIL</div>
  <div class="arrow">▶</div>
  <div class="box">产出可回放<br>自动化代码</div>
</div>

<br>

- 测试人员**只写本来就在写的业务用例**，不写代码
- AI 接管**执行 / 写脚本 / 修脚本**这三段重复又昂贵的活
- **判通过与否不靠 AI 眼判** → 由规则引擎确定性裁决，杜绝假绿

<!--
一句话：测试人员只写业务用例，剩下的执行、写脚本、修脚本全交给 AI。
特别强调：判通过还是失败不是 AI 说了算，是规则引擎确定性地查页面真值判定，AI 没权力偷偷把测试刷绿。这条线我们守得很死，就为了避免假绿。
-->

---

<!-- _class: archslide -->

## 三、怎么做：五层架构总览

<div class="band"><div class="label">L1 接入展示层 · 人看人操作</div>
<div class="row"><div class="box">Web 控制台</div><div class="box">执行过程时间线</div><div class="box">SSE 实时流式</div><div class="box">REST API</div></div></div>
<div class="arrow">▼</div>
<div class="band"><div class="label">L2 编排调度层 · 谁来跑·并发·隔离·闸门</div>
<div class="row"><div class="box">Orchestrator 调度</div><div class="box">执行 Worker（隔离）</div><div class="box">Run 生命周期·Hooks</div><div class="box">权限闸门</div></div></div>
<div class="arrow">▼</div>
<div class="band"><div class="label">L3 智能翻译层 · 用例→可执行规格</div>
<div class="row"><div class="box">用例解析 Excel</div><div class="box">预置条件分类</div><div class="box">TestSpec 翻译</div><div class="box">词汇表 业务词↔元素</div></div></div>
<div class="arrow">▼</div>
<div class="band"><div class="label">L4 执行推理层 · Agent 驱动浏览器走到终态</div>
<div class="row"><div class="box core">ReAct 循环</div><div class="box">StepPlan 状态机</div><div class="box">自愈 Healing</div><div class="box">浏览器驱动 mcp</div></div></div>
<div class="arrow">▼</div>
<div class="band"><div class="label">L5 裁决产出层 · 确定性判 + 出代码</div>
<div class="row"><div class="box">断言规则引擎·裁决</div><div class="box">PageProbe 探针</div><div class="box">代码生成 pytest+Playwright</div><div class="box">产物持久化</div></div></div>

<small>请求向下流（L1 触发 → L5 裁决）；结果向上流式回灌（执行过程实时推回 L1 时间线）。</small>

<!--
五层从上到下：
L1 控制台，能实时看 AI 执行全过程；
L2 管谁来跑、并发、隔离、危险操作拦截；
L3 把用例翻译成规格，还维护业务词↔元素词汇表；
L4 核心，AI 像测试员看一眼想一下点一下，页面变了自己绕（自愈）；
L5 规则引擎确定性判，通过后生成可回放代码。
讲的时候主点 L4（下一页放大），其余一句话带过。
-->

---

<!-- _class: archslide -->

## 四、L4 放大：会随机应变的执行

<div class="band"><div class="label">ReAct 核心循环（想 → 做 → 看，直到 StepPlan 全部完成）</div>
<div class="row">
  <div class="box core">① Reason 想下一步</div><div class="arrow">▶</div>
  <div class="box core">② Act 调工具</div><div class="arrow">▶</div>
  <div class="box core">③ Observe 看快照</div><div class="arrow">↺ 未走完则继续</div>
</div></div>
<div class="arrow">▼</div>
<div class="band"><div class="label">支撑模块（每轮为循环服务）</div>
<div class="row"><div class="box">Prompt 分层构建</div><div class="box">LLM 封装·流式/容错</div><div class="box">StepPlan 状态机</div><div class="box">Context Compact</div></div>
<div class="row" style="margin-top:8px"><div class="box">自愈 Healing</div><div class="box">Permission 拦截</div><div class="box">浏览器驱动 playwright-mcp</div><div class="box">PageProbe 快照解析</div></div></div>

<br>

**传统脚本是把步骤写死，页面一变就崩；L4 是会随机应变的** —— 弹窗、加载慢、多一步确认、改版，AI 都能自己处理。这是它比死脚本耐用的根本。

<!--
多说一句 L4：传统脚本把步骤写死，页面一变就崩；我们这一层会随机应变。弹窗、加载慢、多一步确认，AI 自己处理。这就是为什么同一套用例改版后还能继续跑。
-->

---

## 五、现在的进展

<span class="pill">✅ 已验证</span>　真实大模型 + 真实浏览器，公开站点端到端

- 翻译 → 执行 → 裁决 → **生成代码真能回放跑通**
- 复杂多步流程（完整下单结算 11 步）**端到端绿**
- 免登录复用 / 外部数据断言 / 执行中自愈 **均实证有效**
- Web 控制台 + 执行过程**实时观测**已上线；多租户平台已落地

<span class="pill todo">🟡 待办</span>　**内网真实业务系统 live 验证**（差这一步）

<small>当前被〔填：内网环境 / 模型算力 / 网关超时〕挡住。</small>

<!--
进展：核心链路已打通，而且在公开站点端到端验证通过，用真实大模型加真实浏览器，不是 PPT 概念。包括生成的代码真能回放跑通、11 步结算端到端绿、免登录复用/数据校验/自愈都实测有效。控制台和实时观测已上线。
现在差最后一步：内网真实系统跑一轮，目前被〔填〕挡住。
-->

---

## 六、下一步与需要的支持

- **当前卡点**：内网 live 验证被〔填：环境 / 算力 / 网关〕挡住
- **需要支持**：〔填：内网测试环境账号 / 模型算力 / 1 条试点业务线〕
- **路径**：解阻塞 → **试点 1 条业务线**真实跑一轮回归 → 用**真实人天数据**测算提效 → 再定推广

<br>

### 核心能力已在公开站点跑绿，就差内网验一轮。

<!--
收尾给明确 ask：核心能力已在公开站点跑绿，不是概念，就差内网验一轮。希望领导协调〔填〕。
计划：解阻塞后先选一条业务线真实跑一轮回归，用真实人天数据测算提效，再决定推广范围。
谢谢各位领导。
-->
