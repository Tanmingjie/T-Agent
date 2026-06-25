# TODO — 待办与设计差距

> **重写于 2026-06-24**(执行线 7 阶段走查收官后)。旧版(2026-06-09)早于阶段化重设计
> (FP0-3)+ 会话复用退役 + 7 阶段走查,大面积过时(引用已删的步骤级 `expect_text`/门控、
> Session/Cookie 复用、llm_judge 作"最末档兜底"等),已整体替换。
>
> 进度跟踪以本文件 + `CLAUDE.md`「实施进度」为准。完成后打 `[x]` 并注明 commit/日期。
> 当前真相源:`产品设计文档_v2.0.md` + `CLAUDE.md`(蓝图/铁律待定/索引)。

---

## 现状一句话

执行线 7 阶段走查(①→⑦)已收官:翻译只产意图不接地(②)→ 像 Claude 一样盯目标诊断
换法(③)→ 逐阶段 LLM judge 主裁决、缺失不默认绿(⑤)。驱动层(软/可恢复)与裁决层
(硬/fail-closed)两层分离落定。**后续转入功能补全 / 真实环境验证主线。**

---

## 一、功能补全(执行链产物 / 健壮性的实质缺口)

- [ ] **轨迹驱动 codegen**(⑦ T1+T2,**产物核心缺口**)— 产物从"可读骨架"→"可回放"。
  - T1:When 步骤体当前只渲染定位器表达式 + "请人工补 .click()/.fill()"注释,**不含真实
    动作动词**。执行轨迹完整有 `tool_name`(browser_click/type)+ value,可精确渲染成
    `.fill("standard_user")` / `.click()`。
  - T2:多动作 phase 步骤(如"输入用户名+密码+点登录")当前只捕首个定位器
    (`locators_from_steps` 同 target 取首个 + BDD 按 phase 步骤去重)→ 应按 **action 序列**展开。
  - 影响面:`codegen/bdd.py`(改 `_step_defs` 按 record.steps 的 action 序列渲染)+ 可能调
    `locators_from_steps` 数据结构(从 `{target: Locator}` 改为按步序列)。

- [ ] **阶段失败的 replan**(③/⑤)— 当前"阶段失败即失败"(PHASE_FAILED 直接终止)。
  业界(Skyvern Planner-Actor-Validator)在子目标失败时 replan 重试,WebVoyager 45%→85%。
  本项目刻意先做最简"失败即失败",replan 留作健壮性提升。

- [ ] **纯叙述型哑火残余**(③,弱模型 function-calling)— 2026-06-24 已修"模型把调用写成
  `函数名({...})` 文本"那类(funcname salvage,`da9a58b`)。**残余**:模型反复**纯叙述**意图
  (「第2步已达成,立即标记完成」却不发任何调用),无可解析调用,salvage 救不了;TC201 偶现
  卡死即此。候选:① 调大 `max_idle_nudges`(加 env)让聒噪模型啰嗦着跑完;② 在 mark/确认类
  步骤注入更强「现在就调用、不要只说」约束;③ 换更强模型。先靠 `idle_outputs` 持续观察占比。
  〔判定:弱模型 function-calling 不稳,非平台 bug。〕

- [ ] **运行时锚点自动捕获(URL/数据)** — 翻译期只产意图不接地(FG01),运行时可自动捕获
  稳定锚点(到达页 URL、关键数据)回填裁决/codegen,减少对 LLM judge 自然语言核验的依赖。
  - **动机案例(2026-06-24)**:expected「登录成功，进入系统主页面」被裁判判 FAIL,理由
    「当前 URL 为 /about 而非典型主页路径，且页面无‘登录成功’或‘主页’明确文案」。登录其实
    成功了——`/about` 是该内网系统的真实落地页,裁判拿"典型主页路径"公网先验对赌、又找不到
    字面"登录成功"(成功登录的稳态页本就不显示该 toast)→ 偏-FAIL 误伤。根治信号不是文案,
    是**确定性事实**:提交登录后 **URL 从登录页跳走 + 登录表单(密码框)消失**。运行时捕获
    "登录前 URL → 登录后 URL 变化"作免费锚点喂裁判,把"登录成功"从模糊语义命题降维成
    确定性事实,不再跟裁判常识先验对赌。〔已先做翻译侧缓解,见二.脏站翻译质量对齐。〕

- [ ] **每阶段 system prompt 优化**(FP0-3 候选,源 `CLAUDE.md` 阶段化重设计条目)— 当前
  `PromptBuilder.build(step_plan)` 每轮重算反映进度,Task 层渲染 intent/preconditions/阶段化
  步骤清单(不渲染 expected,FG01)。可进一步**按当前所处 phase 定制驱动 prompt**:高亮本阶段
  子目标、注入阶段相关上下文/skill 提示,让模型更聚焦当前子目标而非整条用例。属驱动层(role a)
  质量打磨,与 ③ 执行健壮化同源。

- [ ] **T7 词汇表来源定位器接进 codegen**(⑦)— `resolve_locators`/`locator_from_vocab` 在
  codegen 路径从未被调用(只接了执行捕获一级);`locators.py` 注释自称三级优先"执行捕获>
  词汇表>文本兜底",实际只接第一级。**要么接上词汇表层兜底**(执行没捕获到的 target,如纯
  断言目标),**要么把注释改诚实**(执行捕获单源)。

- [ ] **T3 Then 对强锚点 expected 生成真断言**(⑦)— NL expected 一律 TODO;但 E5
  `_expected_anchors` 已能抽 URL-like/引号强锚点 → 这类可生成 `expect(page).to_have_url(...)`
  真断言而非 TODO。部分缓解 Then 空洞。

- [ ] **断言目标定位器对齐**(长期弱项)— action 定位器已对齐执行捕获;断言目标(如购物车
  角标)走 probe 不产出可复用 selector,无 vocab 时文本兜底 → 需在 vocab/selector 层对齐。
  〔阶段化后断言主走 llm_judge,此项紧迫度下降,但 codegen 的断言渲染仍受影响。〕

## 二、裁判 / 翻译质量

- [ ] **T5 `ai_judged` 置信分级**(裁判侧专题)— 当前所有阶段裁决一刀切贴"AI 判定·低置信"。
  〔**2026-06-24 更新**:原方案依赖的 E5 锚点佐证 + 证据接地层**已撤销**(eval 实测净 ≤0,
  裁决权交回模型),故"按 evidence_grounded/expected_grounded 分层"的旧设计作废。〕新思路:
  让裁判**自报 confidence**(`_check_llm_judge` 在 prompt 里要模型给 high/medium/low),或据
  锚点类型(URL/数据真值 = 高;纯 UI 态 = 低)分级;`AssertionResult` 加 confidence 字段,前端
  徽标分级。E6(多模态)开启时另算一档。

- [x] **脏站翻译质量对齐 — 登录/成功类 expected 引导**(②翻译线,2026-06-24)— 翻译 prompt
  加两条专项引导:① expected 写**稳态可观测特征**(导航/用户名/业务模块出现、登录表单消失、
  URL 跳走),**别写瞬态成功文案**("登录成功"是一闪而过的 toast,稳态页不显示);② **不假设
  "典型"路径/文案**(不同系统落地页 /home /portal /about 各异,不猜不钉死)。动机=「登录成功，
  进入系统主页面」被裁判按 /about≠典型主页 + 无"登录成功"文案误判 FAIL(见一.运行时锚点案例)。
- [ ] **脏站翻译质量对齐(余)**(②翻译线)— AE03(automationexercise)实测:翻译产出的严格
  expected("按钮变 Remove / 角标=1")与真实站点行为(只弹"Added!"模态)不符 → 偏-FAIL 裁判
  正确地判 FAIL,但用例 flaky。需翻译引导 expected 严格度/锚点与真实站点行为对齐(加购/提交
  类的稳态特征对齐,同登录类思路但场景不同)。

- [ ] **`eval_fg/` 第二模型评测**(可选,可信区间收紧)— **2026-06-24 已扩样到 n=63 / 3 站点**
  (automationexercise + the-internet + demoblaze),deepseek-v4-flash 上 false-green=0/34、接地层
  净 ≤0(置信上界从 ~20% 收到 ~9%)。**剩第二模型未做**:换一个(尤其**更弱**的)模型重跑
  `ab_grounding.py`,验证"接地层无用 / 模型偏-FAIL 够好"在跨模型下是否仍成立(需另配 LLM 凭据)。

- [ ] **E6 多模态裁判真实模型 live A/B**(默认关 `JUDGE_VISUAL=0`)— 治 a11y 看不全的角标/
  图标/canvas;本地弱模型多模态不稳故默认关,有多模态模型时做 A/B 验证再决定是否默认开。

## 三、主线:真实环境验证(环境阻塞中)

- [ ] **真实内网用例 live 验证** — saucedemo 全链路已 live 绿(基础/结算/custom_tool/codegen
  回放);AE03 脏公网 flaky(翻译质量问题,见上)。真实内网业务系统待跑,环境解阻塞即可跑,
  CLI/API 两条路径都就绪。**⚠️ 注意 G1 后无可用 LLM 时阶段裁决整批 FAIL**(LLM 是主裁决),
  真实跑必须配好 `.env`(LLM_MODEL/API_BASE/KEY)。

## 四、弱项 / 工程整洁(低紧迫)

- [ ] **Skill 渐进式加载打磨**(E3 已部分解决)— E3(2026-06-23)已加**甲/乙兜底层**(卡住时
  按 token 重叠浮现命中 skill 名催加载 / 仍卡则平台 auto_load top1 注入),`run_executor` 已停
  force-preload、项目 skill 走真渐进。**剩余**:主路仍依赖弱模型主动 `load_skill`,可继续打磨
  BASE prompt 引导强度 / 区分 skill 大小(短的 preload、长的渐进)。

- [ ] **指标 run/套件级聚合看板** — 单用例 metrics 已埋点 + 抽屉「执行指标」面板已做,数据
  已随 record 落库、run 概览接口透出。**待做**:执行历史/报告页加 run 级汇总(总 token/各阶段
  占比/哑火率/完整性闸门拦截率/**llm_judge 占比=false-green 风险面**)+ 套件级趋势。纯前端
  聚合 + 汇总组件,无需后端改动。

- [ ] **`_incremental_scan` 逐步 url 归属精度** — 按步 `s.url` 分组,部分步(browser_type)
  `outcome.url` 空靠"继承最近非空 url"兜底,偶尔把跨页元素归到前一页 url 组。不影响 selector
  正确性,但页面归属不精确影响按 url 解析命中。根因在 react_loop 逐步 url 捕获精度。
  〔注:`VOCAB_SCAN` 默认关,影响面小。〕

- [ ] **定时扫描触发**(§6.4)— 手动主动扫描 + stale + 执行期增量(默认关)已做;唯"**定时**"
  调度无实现。

- [ ] **prompt 优化 C/D**(省 token,有正确性风险)— 每步约 3 次 LLM 往返
  (snapshot→action→mark_done)、system 每轮重列全部工具文本(已另经 `tools=` 传,冗余)。
  可探索合并往返 / 按相关度截断工具列表;有正确性/收益权衡,待 live A/B。

## 五、设计已声明"暂不做"(阶段五,确认即可)

- [ ] 报告导出
- [ ] 失败断点续跑
- [ ] Page Object 模式(`PageObjectGenerator` 预留)
- [ ] 测试管理平台 API 对接(含 `external_id` 同步)
- [ ] `storage/db.py` `action_map`(Phase 5 预留字段)

---

## 建议优先级

1. **真实内网 live 验证**(主线,解阻塞后第一优先 —— 验证整条链路在真实业务系统不退化)。
2. **轨迹驱动 codegen**(T1+T2 —— 产品价值链末端,产物从骨架→可回放,有实质交付价值)。
3. **T5 ai_judged 置信分级**(裁判可信度可见性,改善前端展示语义)。
4. **脏站翻译质量对齐**(②,提升真实公网用例稳定性)。
5. 其余弱项按需穿插(指标看板/Skill 打磨/定时扫描等纯增量,不阻塞主线)。
