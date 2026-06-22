# TestSpec v2 契约(阶段化)

> 2026-06-22 翻译/执行/裁决重设计的**数据契约**。前后端、翻译、执行、裁决都以本文为准。
> 旧 TestSpec(`given`/`steps[expect_text,expect]`/`assertions`)及其盲接地翻译**已废弃**。

## 设计原则(为什么是这个形状)

1. **翻译只产意图,不接地**:翻译时不知道页面长什么样、元素叫什么、操作在哪个弹窗。
   所以 spec 只描述"要做什么 + 期望什么",**不写 selector / 不锁动作类型 / 不猜元素**。
   元素定位、动作选择全部交给运行时 agent 看真实页面决定。
2. **阶段化(phase)= 一组步骤 + 一条组级预期**:对齐手工 QA 的"分组步骤 + 组级预期结果",
   也对齐 SOTA agent(Skyvern Planner-Actor-Validator)的"逐子目标验证"。
3. **驱动与验证严格分离(FG01 血泪)**:`steps` 驱动 agent;`expected` **只给 Validator 看**,
   绝不喂进 agent 驱动循环——写错/不可达的预期若进驱动,会把 agent 带去追错目标。
4. **逐阶段验证,无终态裁决**:在**每个阶段边界**、该子页面还活着时验该阶段的 `expected`;
   最后一个阶段的验证即天然终态检查。不再有"攒到终态页统一验"的环节。
5. **前置纯背景**:`preconditions` 是陈述性状态声明,**不执行、不 guard**(多数无法被自动化
   确定性核验,如"系统运行正常");只作背景喂给 agent/Validator 理解假设。具体状态保证
   (会话/数据)是未来「环境管理」主线的事。

## JSON Schema

```jsonc
{
  "case_id":  "string",          // 用例 ID
  "name":     "string",          // 用例名称(短标签)
  "base_url": "string",          // 被测系统基址
  "intent":   "string",          // 整体测试意图(背景,一两句;助 agent/Validator 理解;不是判据)
  "preconditions": ["string"],   // 前置声明(背景上下文;不执行不 guard)
  "phases": [                     // 有序阶段(步骤分组)
    {
      "steps":    ["string"],    // 一组步骤(自然语言祈使句;数据内联;序号=数组位置)
      "expected": "string"       // 该阶段完成判据(自然语言;只给 Validator 偏-FAIL 证据核验)
    }
  ]
}
```

### 字段语义与归属

| 字段 | 消费者 | 说明 |
|---|---|---|
| `intent` | Actor(背景) + Validator(背景) | 整体目的。**不是 pass/fail 判据**,不喂硬门控 |
| `preconditions[]` | Actor(背景) | 假设的初始状态。不执行、不核验 |
| `phases[].steps[]` | **Actor(驱动)** | 这一步要达成什么。数据写在句子里("输入用户名 standard_user")。agent 看真实页面自己选工具/定位 |
| `phases[].expected` | **Validator(裁决)** | 阶段边界核验依据。**绝不进 Actor 驱动循环** |

- 步骤序号 = 数组位置(全局连续渲染 1,2,3…),不写进字符串。
- 预期序号 = 阶段序(阶段1 的 expected = 预期①)。
- 一个阶段恰好一条 `expected`;可含多个事实("登录成功 + 顶部显示用户名"),裁判逐条引证核验。

## 执行 / 裁决契约(Validator)

```
for phase in phases:                      # 按序
    Actor 执行 phase.steps(ReAct,在真实页面接地)
    到阶段边界 → Validator(phase.expected, 当前真实页面快照 + 实时 URL):
        偏-FAIL + 强制引证页面证据 + 证据确定性核验(锚点接地)
        PASS → 记为该阶段裁决证据,进入下一阶段
        FAIL → 用例直接 FAIL,停(本轮不做 replan/重试,阶段失败即失败)
用例 PASS  ⟺  所有阶段 Validator 通过 且 每个阶段都执行到了(执行完整)
```

- Validator 解析失败 / 拿不到证据 → **fail-closed**(FAIL),绝不默认绿。
- 不取 agent 自报的 TEST_RESULT。
- Validator 复用既有 `AssertionEngine._check_llm_judge` 的证据接地裁判(偏-FAIL),
  内部以 `Assertion(type="llm_judge", target=expected, expected=expected)` 承载该阶段预期。

## Mock 1:saucedemo TC101

```json
{
  "case_id": "TC101",
  "name": "登录并加购商品",
  "base_url": "https://www.saucedemo.com",
  "intent": "验证标准用户能登录 saucedemo 并将商品加入购物车，购物车计数正确",
  "preconditions": ["saucedemo 站点可正常访问"],
  "phases": [
    {
      "steps": [
        "打开 saucedemo 登录页 https://www.saucedemo.com",
        "在用户名框输入 standard_user",
        "在密码框输入 secret_sauce",
        "点击登录按钮"
      ],
      "expected": "登录成功，进入商品列表页（URL 含 inventory.html，出现商品列表）"
    },
    {
      "steps": ["点击 Sauce Labs Backpack 的 Add to cart 按钮"],
      "expected": "购物车角标显示 1，且该商品按钮由 Add to cart 变为 Remove"
    }
  ]
}
```

## Mock 2:内网订单 ORD-007

```json
{
  "case_id": "ORD-007",
  "name": "新建采购订单并提交审批",
  "base_url": "https://intranet.example.com",
  "intent": "验证采购员能新建一条采购订单并成功提交审批，订单进入审批中状态",
  "preconditions": ["采购员账号可用且具有新建订单权限", "存在可选的供应商基础数据"],
  "phases": [
    {
      "steps": ["在用户名框输入采购员账号 buyer01", "输入密码并登录"],
      "expected": "登录成功，进入系统首页，顶部显示当前用户为采购员"
    },
    {
      "steps": ["展开左侧采购管理菜单", "点击采购订单子菜单"],
      "expected": "进入采购订单列表页，看到订单列表表头与「新建」按钮"
    },
    {
      "steps": ["点击新建打开订单表单", "选择供应商 供应商A", "填写采购数量 100", "点击提交审批"],
      "expected": "订单提交成功，出现成功提示，订单状态变为「审批中」"
    }
  ]
}
```

## 落库(ExecutionRecord)对接

- `ExecutionRecord.spec` 存档这份 v2 TestSpec(供前端可视化 + 翻译偏差排查)。
- `ExecutionRecord.case_assertions` 改为**逐阶段裁决记录**:每项含 `phase_index` / `expected` /
  `status`(PASS/FAIL/skipped)/ `evidence` / `reason` / `ai_judged=true`。
- 不再有 `phase=step/gate/final` 三态;裁决只有"阶段 Validator"一种来源。
