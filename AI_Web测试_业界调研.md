# AI Web 自动化测试 · 业界调研

> **调研背景:** 摸清"业界做得最好的能到什么程度、现在是否具备引入产品的条件",为后续判断提供事实依据。**本文为纯调研:只呈现事实、数据与可行性结论,不给投入建议。**
> **数据截至:2026-06**(活榜单 / 模型分数);一手论文锚点:2025-04。
> 编写原则:**① 真实性**——每个结论贴证据(来源 + 日期);**② 时效性**——本领域数字半衰期以月计,用时间线呈现进展,过时数据不单独采信;**③ 客观**——口径不一的数字分级标注,不横向硬比。

---

## 〇、术语与口径说明(先读这个)

### 0.1 证据分级标签(全文每个数字都带其一)

| 标签 | 含义 | 可信度 |
|---|---|---|
| `[一手]` | **一手证据**——原始论文 / 可公开复核的评测榜单(经同行评审或可直接验证) | 高 |
| `[三方]` | **第三方证据**——G2 / Capterra 等独立评价站的真实用户评分 | 中(有幸存者 / 营销偏差) |
| `[综述]` | **行业调查 / 媒体综述**——二手汇总,口径不一 | 中-低(取方向性,非精确值) |
| `[厂商]` | **厂商自报**——各自定义口径 | 低(不可横向比较,需交叉印证) |

### 0.2 "成功率"这个指标到底统计什么

- **成功率(Success Rate)** = 在某评测基准的全部任务中,Agent **完整、正确做成**的任务所占比例。
- 例:Online-Mind2Web 的 **35.8%** = 被测的 5 个 Agent,在 300 个真实任务上、由**人工逐条判定"是否真的完成"**后的**平均**成功率;**Operator 61.3%** 是其中最强的单个 Agent。
- 它衡量"**能不能把任务做成**",**不是**精确率,也**不是**"判断 PASS/FAIL 对错"的能力。
- ⚠️ **易混点:** 论文里另有一个 **~85%**,那是"自动评委 WebJudge 与人类判断的一致率"(评委多准),**不是 Agent 成功率**——二手榜单常把它误当"AI 测试准确率"。

### 0.3 什么是 Benchmark / Online-Mind2Web 等是什么、权威性如何

- **Benchmark(评测基准)** = 一套**固定的任务集 + 评分方法**,用来横向比较不同 Agent / 模型。**它是"考卷",不是产品。** 同一个 Agent 换一张考卷,分数可以差几十个百分点(见 §3.1)。
- **Online-Mind2Web** —— **学术评测基准**(俄亥俄州立大学 NLP 组 OSU-NLP 出品)。300 个任务 / 136 个**真实**网站、**人工评分**;论文《An Illusion of Progress?》发表于 **COLM 2025(同行评审会议)**;配套自动评委 WebJudge(与人评一致率 ~85%),有公开活榜单(2026-05 起人工评审外包以加快提交复核)。**权威性:高**——学术、同行评审、被广泛引用,是 Mind2Web 的"真实在线版"。是目前**最被认可的"戳破实验室虚高分"的真实基准之一**。`[一手]`
- 其余常见基准(§3.1 会用到):
  - **WebVoyager** —— 早期 web 基准,**网站少、任务常有捷径、用 LLM 自动评分且偏宽松** → 分数偏高(易出"幻觉")。
  - **WebArena** —— **自建可复现的模拟网站**环境(非真实互联网),重在可控复现。
  - **OSWorld / OSWorld-Verified** —— **操作系统级(桌面)**任务基准,真实桌面环境,比纯 web 更难;Verified 是清洗校验过的版本。
  - **SeeAct** —— 2024 年的 web agent,常作"基线"参照。
  - **Computer Use(CU)** —— 靠"看截图 + 操作鼠标键盘"操作电脑的模型 / Agent(如 Operator、Claude CU、GPT-5.4 CU)。
  - **人类基线(human baseline)** —— 人在同一套任务上的表现,作"天花板"参照。
  - **bu-max / Browser Use Cloud** —— 商用**调优系统**(非单一基座模型)。

---

## 一、摘要(Bottom Line)

**业界现状一句话:能力一年多大涨,但"可信地无人值守"仍不可行;当前真正落地的形态是"AI 提效 + 人 / 规则把关 + 选对场景"。**

1. **能力一年多大涨,但"可靠性鸿沟"没关闭。** 通用真实 Web 任务上,纯 AI 从 2025-04 的均值 **35.8%**(最强 Operator 61.3%)`[一手]`,到 2026 上半年活榜单头部已达 **90%+**(opus-4.6 在 Online-Mind2Web 拿到 **90.53%**,2026-03)`[一手]`,OSWorld 从 ~12% 升到 **66%**、前沿模型逼近人类基线 `[一手]`。**但同一榜单"多数 Agent 仍只能完成约 30%"** `[一手]`,头部高分多来自**调优系统 + LLM 自评**口径。

2. **"纯 AI 无人值守 + 可信回归门禁" = 当前不可行。** 没有任何团队这么用——这是行业事实,不是观点(落地率见 §3.2)。

3. **当前真正落地、且满意度最高的是"AI + 人在环 + 托管基建"。** 口碑最高的 QA Wolf(G2 **4.8**)`[三方]` 本质是**人机协同托管服务**,不是纯 AI;它卖的是"绿就是真绿"。

4. **最难、最值钱的一环是"测试判断"(断言 / 业务裁决),不是"生成测试"。** 行业共识:*"AI can generate test code. It cannot generate test judgment."* `[综述]` 所有"假阳性"问题的根都在这里。

5. **警惕"自评注水":** 当评分用 LLM 自动评委、且由被测方自建时,分数会虚高(false green)。2026 榜首近百分的成绩即此类口径。**引用任何准确率都要带【评测集 + 评分口径 + 日期】三要素。**

---

## 二、业界现状:最佳产品与能做到的程度

### 2.1 赛道四层(各取代表)

| 层 | 代表 | 性质 |
|---|---|---|
| 自然语言 / 自愈测试平台 | **testRigor**、mabl、Functionize、Momentic、Checksum | 商用,面向 QA 团队 |
| 托管式 AI QA 服务(人 + AI) | **QA Wolf**、Octomind | 卖"可信交付" |
| 开源浏览器 Agent 引擎 | **browser-use**、Skyvern、Stagehand | 执行层框架 |
| AI-native 验证 / 通用 Computer-Use | **TestSprite**、OpenAI Operator、Claude Computer Use | 给 AI 写的代码做验证 / 通用代理 |

### 2.2 关于"最佳产品"——先说清一个前提

**商用产品没有统一的准确率 benchmark。** §〇/§三 那些 30%、90% 的数字,都是针对**开源 Agent / 基座模型**在**通用公开任务**上跑的考卷;**商用 QA 产品不进这些榜**,各家形态、数据、客户场景都不同。所以商用"最佳"只能用三类口径衡量,**且都不可横向硬比**:

(a) **第三方满意度**(G2 / Capterra 用户评分)`[三方]`;
(b) **落地 / 回本周期** `[三方]/[综述]`;
(c) **厂商自报指标**(覆盖率 / flake 率 / 通过率)`[厂商]`。

#### A. 满意度口碑最高 —— QA Wolf(G2 **4.8** `[三方]`)

- **模式:** 测试即服务——人类 QA 工程师 + AI 帮你建 / 维护 / 跑 E2E,承诺高覆盖、近零 flake。
- **可引用统计(注意分级):**

| 指标 | 数值 | 口径 |
|---|---|---|
| G2 用户评分 | **4.8 / 5** | `[三方]` 较硬 |
| 平均落地 / 回本 | **2 个月落地 / 8 个月回本** | `[三方]` |
| 自动化覆盖 | **4 个月做到 80% 用户流程** | `[厂商]`(AWS Marketplace) |
| flake 率 | 承诺 **zero flake**、实际 **<1%** | `[厂商]` |
| 客户结果 | **92%** 发布更快、**90%** 消除发布后热修 | `[厂商]` |

- ⚠️ 除 G2 4.8 外,上面多为**厂商自报**,缺独立核验;但与第三方高分方向一致。
- **为何被视为"最佳":** 用**人在环**补上"AI 不可靠",托管基建吸收 flake,卖"绿就是真绿"的可信度——客户敢拿它做发布门禁。代价是贵、SaaS 云、数据出门。

#### B. 代表性 AI-native 产品(对比纳入)—— TestSprite

**明确:TestSprite 不是公认"最佳",口碑分化。** 纳入是因为它代表"**为 AI 写的代码做验证**"这一新方向、且单独被问及,作对照看,不作标杆。

- **背景:** 西雅图初创,**670 万美元种子轮**,定位"AI 生成代码的测试支柱" `[综述]`。
- **流程:** 给 URL + 凭据 → AI 爬应用 → 自动生成用例 → 云端沙箱拟人操作 → bug 报告 + 修复建议回灌编码 Agent;MCP 接 IDE / CI `[厂商]`。
- **自报成绩:** 把 GPT / Claude / DeepSeek 生成代码通过率 **42%→93%(一次迭代)** `[厂商]`(注:"验证-修复闭环"口径,**非测试准确率**)。
- **独立实测(2026):** 对**复杂业务逻辑 / 条件 UI / 多步流程产生大量假阳性**,"严重降低对结果的信心";自愈只在**简单 selector 变更**上有效,遇大改版即失灵;按 credit 计费贵、仅云端 → **简单公网应用可试,复杂 / 可靠场景尚未生产可用** `[三方]`。

> **关键观察(对内网 / 私有部署尤其重要):** 商用最佳产品**几乎全是 SaaS 云、数据出门**(QA Wolf、TestSprite、mabl…),强监管 / 内网 / 政企场景基本被排除在外。这是它们共同的形态局限。

### 2.3 能做到什么程度(配 §3.1 时间线看)

- **能稳定做到:** 在**对的场景**(已知固定流程、有明确预期、可预录元素)生成 + 自愈测试、降维护、根因辅助、跨改版的 selector 自愈。
- **做不稳定:** 开放任务的端到端(通用 benchmark 多数 Agent 仍只完成 ~30%)、复杂业务断言、跨系统数据一致性、大改版后的自愈。
- **做不到:** 纯 AI 无人值守、可信地替代人做最终 PASS/FAIL 裁决。

**从业者公认的"能用 / 不能用"清单 `[综述]`:**

| ✅ AI 确有实效 | ❌ AI 一直翻车 |
|---|---|
| 脚手架 / 样板代码生成 | **有意义的业务逻辑断言** |
| 测试数据(含边界) | 端到端测试设计 |
| **自愈 selector**(应对 UI 变动) | 复杂集成测试 |
| 根因分析辅助 | 安全 / 性能场景 |

> 核心矛盾一句话:**"AI 能生成测试代码,但生成不了测试判断。"** 断言需要理解业务逻辑与上下文,这正是当前 AI 的短板,也是所有"假阳性"问题的根。

---

## 三、代表性数据 + 可行性判断

### 3.1 时间线:能力进展(体现时效性;名词见 §0.3)

> 同一个 benchmark,一年多内数字可从 ~30% 变到 90%+——**这就是为什么过时数据无意义、必须看时间线。**

| 时间 | 数据 | 口径(考卷 + 评分方式) | 来源 |
|---|---|---|---|
| 2024 初 | OSWorld ~**12%**;SeeAct 成为学术 baseline | OS 任务 | `[一手]` Stanford HAI 2026 AI Index |
| 2024 | WebVoyager:browser-use **89.1%** / Skyvern **85.85%** | 少站点·**宽松自动评分**(偏高) | `[一手/厂商]` |
| 2025-01 | OpenAI Operator:WebArena **58.1%**、OSWorld **38.1%** | 模拟站点 / OS | `[一手]` |
| **2025-04** | **Online-Mind2Web 人工评分:均值 35.8%、Operator 61.3% 最强**;browser-use 30.0%、Claude CU 29.0%、SeeAct 30.7%、Agent-E 28.0% | **真实站点·人工评分** | `[一手]` arXiv 2504.01382(COLM 2025) |
| 2025 | 任务难度每升一档掉分:easy→medium **−29.6pt**,medium→hard **−15.1pt** | 同上 | `[一手]` 同上 |
| 2026-03 | **Online-Mind2Web 新高 90.53%**(opus-4.6 + agent-browser-protocol),超此前 78.7%;**但"多数 Agent 仍只完成约 30%"** | 真实站点·人工评(2026-05 起 v2 外包人评) | `[一手]` OSU-NLP / abp 榜单 |
| 2026-04 | 活榜单:bu-max **97%**(系统 + **自建 LLM 评委自评**)、GPT-5.4 原生 CU **93%**(基座)、UI-TARS-2 **88.2%**、Gemini 2.5 CU **69%** | 真实站点·**评分口径各异、不可横比** | `[一手]` Steel.dev(2026-04-16) |
| 2026 | OSWorld 整体 **66%**;Claude Sonnet 4.6 OSWorld-Verified **~73%**、GPT-5.4 **75%**,逼近人类基线 **72–84%**;OpenAI Operator 仍 **38.1%** | OS 任务 | `[一手]` Stanford HAI 2026 AI Index |
| 2026 | WebArena 前沿模型 **64–69%**(Claude / GPT-5 系) | 模拟站点 | `[一手]` |

**读这张表的四个要点:**
1. **进步是真的**——基座模型从 61%(2025-04)到 90%+(2026),OSWorld 逼近人类基线。"纯 AI 永远不行"是错的。
2. **WebVoyager ~90% 是幻觉**——换真实站点 + 人工评分,2025-04 坍缩到 35.8%。
3. **头部 90%+ 要打折看**——bu-max 97% 是"系统调优 + 自建 LLM 评委自评",榜单官方明示**"各家口径不可横比"**;基座模型口径(GPT-5.4 CU 93%、opus-4.6 90.53% 经外包人评)更可信;OSWorld 的 82% vendor 自报伴随"benchmark 被钻空子(exploited)"争议,不采信。
4. **"高分"与"多数仍 30%"并存**——头部模型大涨,但**榜单中位 / 多数 Agent 仍只完成约 30%**;能现成用到的不是榜首那套调优系统,而是接近"多数"水平的能力。

### 3.2 另一类高可信数据:落地与信任(理想 vs 现实)

| 数据 | 含义 | 来源 |
|---|---|---|
| **70%+ 在试,仅 11% 真在生产跑、25% 还在 pilot** | 实验多、真上生产极少 | `[综述]` 2026 |
| 开发者对 AI 输出信任 **69%(2024)→54%(2025)**;不信任 **31%→46%** | 用得越多越不信 | `[综述]` |
| 仅 **33%** 真正信任 AI 输出,**3%** 高度信任 | 信任稀缺 | `[综述]` |
| **61%** 开发者认同"AI 常产出看着对、实则不可靠的代码" | 可靠性是核心痛点 | `[综述]` 2026 |
| **67%** 测试人员只接受"带强制人工复核"的 AI 测试 | 人在环是刚需 | `[综述]` |
| 仅 **25%** AI 项目兑现预期 ROI、**16%** 规模化成功 | 收益真实但难规模化 | `[综述]` |
| 自动化测试(宽口径)ROI **300–500%**、成本降 **78–93%** | 选对场景收益大 | `[综述]` |

> **解读:** 能力曲线在涨(§3.1),但**落地曲线几乎没动**(生产部署仅 11%)。瓶颈不是"AI 够不够聪明",而是"够不够可信"。

### 3.3 可行性结论:当前是可行,还是不可行?

| 用法 | 结论 | 依据 |
|---|---|---|
| 纯 AI 无人值守 + 可信回归门禁 | ❌ **不可行** | 多数 Agent 真实任务仍 ~30%;生产落地仅 11%;无团队这么用 |
| AI 自动产出"可信 PASS/FAIL 裁决" | ❌ **不可行** | *"can't generate test judgment"*;自评注水 / 假阳性 |
| AI 生成测试 + **人审断言** | ✅ 可行 | 67% 只接受带复核;QA Wolf 模式即此 |
| AI 驱动执行(随机应变、自愈)+ **规则引擎确定性裁决** | ✅ 可行 | 把"判断"从 LLM 收回确定性层,绕开假阳性根因 |
| AI 自愈 selector / 测试数据 / 根因辅助 | ✅ 可行且实效 | 从业者公认 `[综述]` |
| 选对场景(固定流程 + 明确预期)+ 人在环 | ✅ 有真实 ROI | 自动化测试 300–500% ROI(选对场景) |

> **可行性边界一句话(事实结论,非建议):** 当前可行的是 **"AI 提效 + 人 / 规则把关";不可行的是 "AI 替代判断"。** 没有任何产品越过这条线——商用最佳的 QA Wolf 也是靠人越不过去。

---

## Sources(按主题)

**一手论文 / 评测榜 `[一手]`**
- [Online-Mind2Web —《An Illusion of Progress?》(arXiv 2504.01382, COLM 2025)](https://arxiv.org/abs/2504.01382) · [GitHub(OSU-NLP)](https://github.com/OSU-NLP-Group/Online-Mind2Web) · [HuggingFace 活榜单](https://huggingface.co/spaces/osunlp/Online_Mind2Web_Leaderboard)
- [Online-Mind2Web 活榜单(Steel.dev,2026-04-16)](https://leaderboard.steel.dev/leaderboards/online-mind2web/) · [abp 评测结果(2026-03,opus-4.6 90.53%)](https://github.com/theredsix/abp-online-mind2web-results) · [Web Agent Benchmarks(Awesome Agents, 2026-04)](https://awesomeagents.ai/leaderboards/web-agent-benchmarks-leaderboard/)
- [WebArena Benchmark 2026(BenchLM)](https://benchlm.ai/benchmarks/webArena) · [OpenAI Operator(Wikipedia)](https://en.wikipedia.org/wiki/OpenAI_Operator)

**商用产品(第三方 / 厂商)`[三方]`/`[厂商]`**
- [QA Wolf(G2 4.8)](https://www.g2.com/products/qa-wolf/reviews) · [QA Wolf — 80% 覆盖 4 个月(AWS Marketplace)](https://aws.amazon.com/marketplace/pp/prodview-zx663ireraacm) · [QA Wolf service](https://www.qawolf.com/service)
- [testRigor(G2 4.7)](https://www.g2.com/products/testrigor/reviews) · [mabl(Capterra)](https://capterra.com/p/175029/mabl/reviews/)
- [TestSprite 官网](https://www.testsprite.com/) · [TestSprite 670 万美元种子轮(GeekWire)](https://www.geekwire.com/2025/seattle-startup-testsprite-raises-6-7m-to-become-testing-backbone-for-ai-generated-code/) · [TestSprite 实测《Promise vs Reality》(DEV)](https://dev.to/govinda_s/testsprite-review-ai-powered-testing-tool-promise-vs-reality-58k8) · [TestSprite AI 2026 知识库(Bug0)](https://bug0.com/knowledge-base/testsprite-ai)

**行业调查 / 综述 `[综述]`**
- [AI Testing Adoption Gap: Hype vs Reality 2025–2026(Medium, 2026-03)](https://medium.com/@accounts_89844/ai-testing-adoption-gap-hype-vs-reality-in-qa-2025-2026-qa-engineers-b57f84cb67b3) · [QA Trends Report 2026(ThinkSys)](https://thinksys.com/qa-testing/qa-trends-report-2026/)
- [State of Code Developer Survey 2026(SonarSource)](https://www.sonarsource.com/state-of-code-developer-survey-report.pdf) · [AI Testing Strategy 2026(Applitools)](https://applitools.com/blog/ai-testing-strategy-in-2026/)
- [自动化测试 ROI(Virtuoso)](https://www.virtuosoqa.com/post/automated-testing-strategy-roi-enterprises) · [Agentic AI ROI 案例(AI Monk)](https://aimonk.com/agentic-ai-examples-enterprise-roi-case-studies/)

> **口径与时效免责:** 厂商自报指标(覆盖率 / flake 率 / 通过率)为各自定义,不可横向比较;benchmark 数字随评测集、评分口径、时间显著变化,引用须带三要素【评测集 + 评分口径 + 日期】。OSWorld 82% 等 vendor 自报伴随"被钻空子"争议,本文不采信为可比成绩。本文为公开信息综述,呈现事实与可行性结论,不构成投入或采购建议。数据截至 2026-06。
