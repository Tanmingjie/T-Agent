# browser-use 对比评估(是否替换我们的执行层)

> Time-boxed 只读分析。**不改任何代码**。目的:为"是否引入 browser-use 二次开发、替换
> `harness/react_loop.py` + 快照/DOM 处理"提供决策依据。
>
> 待验证的预判(用户):*"browser-use 解决的是'驱动执行'(我们已跑通),不解决我们真正的
> 瓶颈'可信裁决',默认倾向不替换。"*
>
> **本文结论:预判方向正确,但需两处修正** —— ①「驱动执行我们已跑通」只在**干净站点**成立,
> browser-use 的 DOM 提取在**烂 HTML 的内网站点**上确实更强,值得**定向借鉴**(非整体替换);
> ②原以为 browser-use 会撞铁律1(CDP/代理 504)的担心**多半不成立**(它走 localhost CDP,
> 通常绕过企业代理)——所以"代理拦截"不能作为反对替换的理由。
>
> 日期:2026-06-22 · 方法:对照读我方源码(react_loop.py / page_probe.py) + 联网查
> browser-use 架构(GitHub / DeepWiki) + Skyvern 对比。browser-use 内部细节凡未亲验的,
> 文中标注「需 spike 验证」。

---

## 0. 结论先行(TL;DR)

| 维度 | 判断 |
|---|---|
| browser-use 解决的核心问题 | **任务驱动执行**(让 agent 在网页上把事做成),不是**测试裁决** |
| 我们的真正瓶颈 | **可信裁决**(偏-FAIL 证据接地 + fail-closed + 防假绿)—— browser-use **完全不碰** |
| 整体替换执行层 | **不建议**。我方约 80% 价值(裁决/测试语义/StepPlan/codegen/词汇表/平台)与执行层正交,替换=高成本重接、且替换掉的恰是已跑通的部分 |
| 值得借鉴的**一点** | browser-use 的 **DOM 提取 + 元素索引(set-of-mark + 视觉高亮)** 在**无障碍树稀疏的烂内网页面**上比我们的 a11y-only 强 —— 建议**定向 spike**,只借这块,不吞整个框架 |
| 弱本地模型适配 | 我们的循环为 Qwen/DeepSeek 硬化;browser-use 面向前沿模型(GPT-4o/Claude),整体替换有**本地模型回归风险** |

**一句话**:browser-use 是把"驱动"做到极致的 SOTA agent;但**它没有"测试"这个概念**(无 PASS/FAIL 裁决、无证据接地、无防假绿)。我们的护城河正在它的盲区里。→ **不替换,定向借鉴 DOM 提取**。

---

## 1. 任务一:差异清单(对照阅读)

### 1.1 browser-use 有、我们没有(尤其 DOM 提取 / 元素索引 / 动作鲁棒性)

| 能力 | browser-use | 我们 | 影响 |
|---|---|---|---|
| **DOM 提取深度** | `buildDomTree.js` 注入页面,**遍历真实 DOM**,启发式识别可交互元素(clickable / 事件监听 / `cursor:pointer` / ARIA role),算可见性(视口/遮挡/透明度) | 只解析 playwright-mcp 的 **a11y 快照(无障碍树)**,YAML 行正则解析(`page_probe.parse_snapshot`) | **关键差异**。a11y 树在**规范站点**够用;但内网常见 div-soup / 无 ARIA / 自定义组件 → a11y 树稀疏 → 我们**可能压根看不见某些可交互元素**。browser-use 的 DOM 走查能看到。 |
| **元素寻址** | **数字高亮索引(set-of-mark)**:`[12]<button>Login</button>`,LLM 输出 `click(12)`,编排层映射回真实元素 | playwright-mcp 的 `ref=e11`(每次快照重分配),还得用 `_ref_alias` 从 `target` 等别名**回收** ref(react_loop:47) | browser-use 的索引更稳、更省 token;我们有"模型把 ref 放错参数"的脏活要兜 |
| **视觉接地** | 一等公民:截图 + **元素编号 bounding box** 一起喂多模态 LLM | 截图仅作**自愈侧通道**(`get_screenshot` → P5 视觉自愈),主循环不喂图 | 烂页面/图标按钮上 browser-use 接地更强 |
| **坐标兜底** | DOM 失败可**按坐标点击** | 无,纯靠 ref/选择器 | 极端页面 browser-use 多一条命 |
| **iframe / shadow DOM** | buildDomTree 显式穿透 shadow root / iframe | 依赖 playwright-mcp 快照覆盖(被动) | 复杂内嵌结构 browser-use 更可控(需 spike 验证我们覆盖到哪) |
| **动作注册表成熟度** | click/type/navigate/extract/scroll/dropdown/upload/tabs… Pydantic 结构化动作 + 校验,海量站点实战硬化 | 复用 playwright-mcp 全套工具(够用),但动作层鲁棒性靠 MCP + 我们的自愈 | browser-use 动作层身经百战 |
| **元素指纹/变更检测** | 跨 DOM 变更用哈希追踪元素,识别"页面变了" | 无显式指纹;靠每轮重抓快照 | browser-use 对动态页更稳 |
| **社区/实战规模** | 大量站点、持续硬化、活跃社区 | 自研,验证集中在 saucedemo/AE | 长尾站点 browser-use 成熟 |

### 1.2 我们有、browser-use 没有或很弱

| 能力 | 我们 | browser-use | 说明 |
|---|---|---|---|
| **可信裁决架构 ★护城河** | 偏-FAIL **证据接地** LLM 裁判 + 确定性锚点 + **fail-closed** + 门控不计入裁决 + 防假绿(A-2 实测 deepseek 0 假绿/0 误伤) | **无**。browser-use 是**任务完成** agent,无 PASS/FAIL 裁决、无证据核验、无 false-green 概念 | **这正是用户瓶颈所在,browser-use 0 贡献** |
| **测试语义:StepPlan + 逐步门控** | TestSpec→StepPlan 状态机、`mark_step_done`、逐步完成门控、单步失败预算、过早 mark 护栏 | 开放式目标追逐(planner/memory),无"用例步骤逐步核验"概念 | 我们贴"测试用例",它贴"把任务做完" |
| **断言引擎 + 断言侧自愈** | 规则引擎(DOM/文本/URL/custom_tool)+ 目标重定位自愈 + 词汇表 + 全页文本兜底 | 无断言概念 | 测试专有 |
| **codegen(pytest-bdd)** | 执行轨迹 → 可复跑测试代码 | 无(纯运行时 agent) | 测试平台专有 |
| **领域集成** | 预置条件分类、跨语言词汇表(中文业务词↔英文元素)、custom_tool 数据断言、多租户平台、SSE 实时、录制 | 无 | 全是测试平台关切 |
| **弱本地模型硬化** | 文本式观察回灌(不靠 tool_call_id 配对)、哑火喂新快照续推、循环检测、tool_call 容错、流式丢调用复核 | 面向前沿模型(GPT-4o/Claude),弱模型适配未知 | **整体替换有本地模型回归风险** |

### 1.3 循环设计关键差异(Reason→Act→Observe)

| 维度 | 我们(react_loop.py) | browser-use |
|---|---|---|
| **谁控制循环** | LLM 驱动 ReAct,**叠加 StepPlan 检查清单**(每步 `mark_step_done`)→ 有界、测试导向 | LLM 驱动 ReAct + planner/memory,**开放式目标追逐** |
| **LLM 调用/步** | 1 次(+ 自愈/门控/复核可能额外) | 1 次/步(+ 重试) |
| **观察回灌** | **文本 a11y 快照作 user 消息回灌**(不靠 tool_call_id 配对,弱模型稳);`[观察]` 前缀 + Context Compact 压缩 | **结构化 DOM 状态(索引元素)+ 截图**,经 MessageManager,假设强工具调用模型 |
| **元素寻址** | `ref=e11`(a11y,每快照重分配)+ 别名回收 hack | 数字高亮索引(buildDomTree 分配)+ set-of-mark |
| **DOM 源** | playwright-mcp a11y YAML(**仅无障碍树**) | buildDomTree 全 DOM 走查 + 可交互启发式 + 可见性 + 可选视觉 |
| **容错** | 循环检测 + 哑火喂新快照续推 + tool_call 容错 + **单步失败预算** + **自愈子代理(词汇表优先 + 视觉双通道)** | 循环检测 + behavioral nudges + 动作层重试/元素重解析(细节需 spike) |
| **完成判定** | StepPlan 全 DONE + 执行完整性闸门;**不取 LLM 自报 TEST_RESULT** | LLM 自报 done 动作(细节需 spike) |
| **裁决** | 循环外独立裁决(偏-FAIL 证据接地) | **无裁决**——任务做完即止 |

---

## 2. 对用户预判的"事实核验"

> 预判:*browser-use 解决驱动(我们已跑通),不解决裁决(我们的瓶颈)→ 不替换。*

- ✅ **核心成立**:裁决是瓶颈,browser-use **结构上没有裁决**(它是任务-完成 agent,不是测试-判定系统)。换它**一行裁决能力都拿不到**,而裁决正是 Fix3/A-2 一路在啃的硬骨头。
- ⚠️ **修正一:「驱动我们已跑通」有边界**。我们在 saucedemo/AE 这类**规范站点**跑通了;但我们的提取是 **a11y-only**,内网烂 HTML(div-soup、无 ARIA、自定义控件)上 a11y 树会稀疏 → 我们**可能看不见元素**。browser-use 的 buildDomTree + 视觉接地在这种页面**确实更强**。所以"驱动已解决"在内网真实站点上**尚未被证明**,这是 browser-use 唯一真正诱人的点。
- ⚠️ **修正二:别拿"代理拦截(铁律1)"当反对理由**。铁律1 是 **playwright-mcp 的 CDP HTTP 模式**被企业代理拦 →504。browser-use 连的是**本地浏览器的 localhost CDP**(websocket),通常走 `NO_PROXY` 绕过代理 → **多半不受影响**。所以"它会撞代理"这个直觉**不成立**,不能用它反对替换(需 spike 验证,但别预设)。

**真正该用来支撑"不替换"的理由**(都成立):
1. 裁决是瓶颈,browser-use 0 贡献;
2. 替换=丢掉**已跑通**的部分,还要把 browser-use 的循环重接进我们的 StepPlan/门控/裁决/SSE/录制/codegen —— **集成面巨大**,而约 80% 代码(裁决/测试语义/词汇表/codegen/平台)与执行层正交、原样保留;
3. browser-use 面向前沿模型,**本地弱模型(Qwen/DeepSeek)适配未知**,整体替换有回归风险。

---

## 3. 风险评估

| 选项 | 收益 | 风险/成本 |
|---|---|---|
| **A. 整体替换执行层为 browser-use** | 拿到更强 DOM 提取 / 视觉接地 / 成熟动作层 | 🔴 **高**:① 裁决/StepPlan/门控/SSE/录制/codegen 全要重接;② 弱本地模型回归;③ 引入大依赖 + 升级跟随成本;④ 丢掉已硬化的弱模型容错;⑤ 替换掉的是**已工作**的部分 |
| **B. 定向借鉴 DOM 提取(set-of-mark / buildDomTree 思路),不吞框架** | 补齐唯一真实短板(烂内网页提取),保留全部裁决/测试资产 | 🟡 **中**:需自实现/移植一段 DOM 走查(经 `browser_evaluate` 注入)+ 索引化喂 LLM;与现有 ref 机制并存的取舍 |
| **C. 不动(维持 a11y-only)** | 0 成本 | 🟡 内网烂页面提取不足的隐患仍在,等真实内网用例暴露 |

---

## 4. Spike 方案(time-boxed,先验证再决策)

目标:**用事实判定"我们的 a11y 提取到底够不够"**,而不是凭感觉替换。建议 ≤1.5 天:

1. **提取覆盖率对照(半天)** — 取 3~5 个**真实内网页面**(或公网烂 HTML 代表),分别用:
   - 我们的 `browser_snapshot` a11y 快照(`parse_snapshot` 数节点);
   - browser-use 的 `buildDomTree.js`(或等价:`browser_evaluate` 注入一段可交互元素走查)。
   - **量**:各自识别出多少可交互元素、关键业务元素(登录框/提交/菜单)有没有被 a11y 漏掉。
   - **判据**:若 a11y 覆盖 ≈ buildDomTree → 我们够用,**选 C**;若 a11y 明显漏 → **选 B**。
2. **browser-use 跑通性(半天)** — 在内网环境裸跑一个 browser-use minimal agent,验证:
   - 本地 CDP 是否真不受企业代理影响(证伪/证实"铁律1 不适用");
   - 用**我们的本地模型(DeepSeek)**而非 GPT-4o 时,驱动是否还稳(弱模型适配)。
3. **借鉴成本估算(半天)** — 若选 B:评估"注入 DOM 走查 + 数字索引喂 LLM"接进现有 react_loop 的改动面(与 `ref` 机制并存 or 替换),给工时。

**spike 不做**:整体替换 PoC(成本太高,且结论大概率是 B/C,不值得先做 A)。

---

## 5. 倾向性结论

**默认不替换(否决选项 A),与用户预判一致。** 理由按权重:

1. **裁决是瓶颈,browser-use 不解决** —— 换它拿不到任何裁决能力,而那是我们一路在啃的硬骨头。
2. **替换掉的是已跑通的部分,集成面却巨大** —— 80% 价值正交于执行层,替换=高风险重接 + 弱模型回归。
3. **唯一真实短板(烂内网页 DOM 提取)可定向借鉴解决** —— 不必为这一块吞下整个框架。

**建议下一步**:先做 **spike 步骤 1(提取覆盖率对照)**。

- 若 a11y 够用 → **选 C**(不动执行层),把精力**全押回"裁决 + 翻译重设计(阶段化 spec)"** 这条真正的主线。
- 若 a11y 明显漏元素 → **选 B**(定向借鉴 set-of-mark / DOM 走查),作为执行层的**增量增强**,而非替换。

无论 1 还是 2,**都不替换 react_loop 的整体控制权**——它承载的 StepPlan/门控/弱模型容错/裁决接缝是资产,不是负债。

---

## 附:信息来源

- [Skyvern 2.0: Planner–Actor–Validator(SOTA 对照)](https://www.skyvern.com/blog/skyvern-2-0-state-of-the-art-web-navigation-with-85-8-on-webvoyager-eval/)
- [browser-use DOM Processing(DeepWiki)](https://deepwiki.com/browser-use/browser-use)
- [Browser Tools for AI Agents: Framework Wars(browser-use / Stagehand / Skyvern)](https://dev.to/stevengonsalvez/browser-tools-for-ai-agents-part-2-the-framework-wars-browser-use-stagehand-skyvern-4gn)
- [browser-use GitHub](https://github.com/browser-use/browser-use)
- 我方源码:`harness/react_loop.py`、`harness/page_probe.py`(对照阅读,2026-06-22)
