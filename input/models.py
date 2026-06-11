"""核心数据结构(规格 §4)。

约定:
- 纯结构 / 中间产物 → pydantic ``BaseModel``。
- 落库表(table=True)留到阶段三 T-21 用 SQLModel 定义;此处先以 BaseModel
  承载,字段语义与表一致,届时平滑迁移。
- 实现原则 4:会成为「核心表」的结构预留同步字段 ``updated_at`` / ``owner`` /
  ``external_id``,为未来对接用例管理平台预留。这些字段对阶段一逻辑无影响。

输入/输出抽象(实现原则 3):所有用例来源都产出 ``TestCase``,所有执行结果都落
``ExecutionRecord``;下游只认这两个结构,不关心来源/去向。
"""

from __future__ import annotations

import time

from pydantic import BaseModel, Field

# ── 输入层 ──────────────────────────────────────────────────────────


class TestCase(BaseModel):
    """业务测试用例(来源无关:Excel 解析 / 未来平台拉取都产出它)。"""

    __test__ = False  # 名字以 Test 开头,告知 pytest 这不是测试类

    id: str
    name: str
    preconditions: list[str] = []  # 已拆分的预置条件
    precondition_confirmed: list[bool] = []  # 每个前置条件是否已确认(旧:仅布尔)
    # 预置条件三分类结果(规格 §3.2/§5.1):首次执行分类后落库,用户标黄确认/改类后持久化,
    # 下次执行据此跳过 LLM 重分类(confirmed_by_user 优先)。空表示尚未分类。
    precondition_items: list["PreconditionItem"] = []
    steps: list[str] = []  # 已拆分的测试步骤(数据写死在文本里)
    expected: list[str] = []  # 预期结果
    base_url: str = ""
    suite_id: str | None = None

    # —— 预留同步字段(实现原则 4) ——
    external_id: str | None = None  # 对应用例管理平台的用例 ID
    owner: str | None = None
    updated_at: float = Field(default_factory=time.time)


# ── 断言 / TestSpec ────────────────────────────────────────────────


class Assertion(BaseModel):
    """结构化断言。执行时由规则引擎确定性验证(非 LLM 眼判,规格 §5.3)。"""

    type: str  # element_visible | text_equals | url_contains | element_count | custom_tool | llm_judge
    target: str  # 目标语义描述(执行时解析为 selector)
    selector: str | None = None  # 可选,词汇表解析后填入
    expected: str | None = None
    confidence: str = "high"  # high | low(llm_judge 为 low)


class SpecStep(BaseModel):
    """软计划的一步:动作 + 目标语义 + 数据 + 即时断言,不锁 selector。"""

    action: str  # navigate | fill | click | select | hover | wait | ...
    target: str  # 目标语义描述,不锁 selector
    data: str | None = None  # 步骤里写死的数据
    expect: list[Assertion] = []  # 该步骤的即时断言(可选)


class TestSpec(BaseModel):
    """结构化执行规格(本产品关键中间产物,规格 §5.2)。软计划而非硬脚本。"""

    __test__ = False  # 名字以 Test 开头,告知 pytest 这不是测试类

    case_id: str
    name: str
    base_url: str
    given: list[SpecStep] = []  # 来自预置条件②类(操作步骤)
    steps: list[SpecStep] = []  # 来自测试步骤
    assertions: list[Assertion] = []  # 用例级最终断言(来自预期结果)


class PreconditionItem(BaseModel):
    """预置条件三分类结果(规格 §5.1)。"""

    text: str
    type: str  # state_hook | action_step | ambiguous
    hook_ref: str | None = None  # 状态声明映射到的 Hook
    confidence: float = 0.0
    confirmed_by_user: bool = False


# ── 执行录制 ────────────────────────────────────────────────────────


class ActionStep(BaseModel):
    """ReAct 单步录制。存「操作意图」而非只存选择器(自愈/代码生成依据)。"""

    step_no: int
    tool_name: str
    tool_input: dict = {}
    reasoning: str = ""  # ReAct 思考链
    intent: str = ""  # 操作意图(自愈重定位的依据)
    prompt: str = ""  # 本轮发给 LLM 的请求(System Prompt + 最近输入),供「查看 prompt」调试
    tool_result: str = ""
    screenshot: str | None = None  # 文件路径
    url: str = ""
    # 执行期捕获的被操作元素真实 a11y 身份(从操作的 ref 回查快照得到)。
    # 让未录入词汇表的目标也能在 codegen 拿到稳健 get_by_role 定位(覆盖面 > 仅词汇表)。
    element_role: str = ""
    element_name: str = ""
    # 实际执行的 Playwright 定位表达式(从 tool_result「Ran Playwright code」抓取,ground truth)。
    # 比快照重建的 role+name 更可靠(它真跑通过、必然唯一可用),供 codegen 对齐定位器。
    element_selector: str = ""
    step_target: str = ""  # 该操作所属业务步骤的语义 target(供 codegen 按 target 回填定位)
    heal_attempts: list[dict] = []
    assertion_results: list[dict] = []
    is_custom_tool: bool = False
    is_hook_action: bool = False
    duration_ms: int = 0


class ExecutionRecord(BaseModel):
    """执行结果(所有执行的统一落点,实现原则 3)。"""

    exec_id: str
    case_id: str
    suite_id: str | None = None
    run_id: str | None = None  # 关联 RunRecord(Phase 4)
    steps: list[ActionStep] = []
    passed: bool = False
    # 用例级最终断言裁决结果(AssertionResult.to_dict),可信 PASS/FAIL 的依据
    case_assertions: list[dict] = []
    # 本次执行使用的 TestSpec(LLM 翻译产物)。存档以便前端可视化 + 发现翻译偏差。
    spec: TestSpec | None = None
    final_result: str = ""
    generated_code: str = ""  # 断言通过后生成的 pytest-bdd 代码(随 run 持久化)
    token_usage: int = 0
    heal_count: int = 0
    start_time: float = Field(default_factory=time.time)
    end_time: float | None = None

    # —— 预留同步字段(实现原则 4) ——
    external_id: str | None = None
    owner: str | None = None
    updated_at: float = Field(default_factory=time.time)


# ── 会话 / 套件 / 词汇表 / 工具 ────────────────────────────────────


class SessionProfile(BaseModel):
    """账号 + 登录 AW + Cookie 缓存(规格 §5.4 Session Profile)。"""

    name: str
    login_aw: str  # 登录 AW 引用(用户已有 pytest 脚本)
    cookie_store: str  # Cookie 持久化路径(文件型,向后兼容)
    valid_until: float | None = None
    base_url: str
    project_id: str = ""  # 多租户作用域(平台化 M2);单机留空
    cookies: list = []  # Cookie 列表(平台化 M2:落库时加密;空则用 cookie_store 文件)

    # —— 预留同步字段(实现原则 4) ——
    owner: str | None = None
    updated_at: float = Field(default_factory=time.time)


class ProjectSkill(BaseModel):
    """项目级 Skill(平台化 M2:项目业务常识,作为常注入 DomainSkill 接入执行链)。

    主键 (project_id, name)。content 是注入 System Prompt 的业务提示文本。
    """

    project_id: str
    name: str
    content: str = ""
    updated_at: float = Field(default_factory=time.time)


class Suite(BaseModel):
    """用例套件(规格 §4)。"""

    id: str
    name: str
    base_url: str
    session_profile: str | None = None
    page_intelligence_id: str | None = None
    code_generator: str = "BDDGenerator"
    custom_prompt: str = ""
    hooks: dict = {}  # {"before_case": [...], "after_case": [...]}

    # —— 多租户(平台化 T-P04b;单机/CLI 留空,空=默认租户,向后兼容)——
    project_id: str = ""
    version_id: str = ""  # Suite 绑版本(已拍板)

    # —— 预留同步字段(实现原则 4) ——
    external_id: str | None = None
    owner: str | None = None
    updated_at: float = Field(default_factory=time.time)


class PageVocabulary(BaseModel):
    """页面词汇表:业务词 → UI 元素映射(规格 §5.5)。"""

    project_id: str = ""  # 多租户作用域(T-P04b):词汇表项目级,跨版本共享;空=默认租户(单机/CLI)
    base_url: str = ""  # 被测系统根地址(作用域键):resolve 时只匹配 base_url 为当前 url 前缀者,
    #                     跨系统不再撞键(系统甲/乙的 /login 互不污染);同 base_url 多 suite 共享
    url_pattern: str  # /order/{id}
    page_title: str
    login_role: str
    vocabulary: dict = {}  # {业务词: {role, name, confidence}}
    action_map: list = []
    stale: bool = False
    scanned_at: float = Field(default_factory=time.time)

    # —— 预留同步字段(实现原则 4) ——
    updated_at: float = Field(default_factory=time.time)


class ToolDef(BaseModel):
    """Custom Tool 定义(规格 §5.4 Custom Tools)。"""

    name: str
    description: str
    parameters: dict = {}  # JSON Schema
    returns: str = ""
    when_to_use: str = ""
    timeout_seconds: int = 30


# ── 多租户(平台化 T-P04;单机版不使用,project_id 留空即可)──────────────


class Project(BaseModel):
    """租户边界:一个产品线/团队的项目。LLM/Tools/Skills/词汇表/Session 都挂项目级。"""

    id: str  # UUID 或人类可读 slug
    name: str
    description: str = ""
    owner: str | None = None  # 创建人(user id),自动成为项目管理员
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class Version(BaseModel):
    """项目下的测试版本。测试人员按「项目→版本」测试;Suite/Run 绑版本(已拍板)。"""

    id: str  # UUID
    project_id: str
    name: str  # 如 v1.2.0 / 2026Q2
    status: str = "active"  # active | archived
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class User(BaseModel):
    """平台用户。一期本地账号(或网关 header 透传);二期接 IDaaS 只换来源,结构不变。"""

    id: str  # 用户名 / IDaaS subject
    display_name: str = ""
    is_platform_admin: bool = False
    updated_at: float = Field(default_factory=time.time)


class ProjectMember(BaseModel):
    """项目成员与角色(项目内角色平台自管,不绑 IDaaS 组织架构)。"""

    project_id: str
    user_id: str
    role: str = "tester"  # admin(项目管理员)| tester(测试人员)
    updated_at: float = Field(default_factory=time.time)


class ProjectHttpTool(BaseModel):
    """项目级 HTTP 型 Custom Tool(平台化 M2:替代 shell,受控 HTTP 调用 + SSRF 防护)。

    headers 可能含凭据(Authorization),领域模型持明文,存储层加密落库。
    url/body 支持 {arg} 占位。主键 (project_id, name)。
    """

    project_id: str
    name: str
    description: str = ""
    method: str = "GET"
    url: str = ""
    headers: dict = {}  # 明文(落库加密);可含 Authorization 等凭据
    body: str = ""
    parameters: dict = {}  # JSON Schema(LLM 调用参数)
    when_to_use: str = ""
    timeout_seconds: int = 30
    updated_at: float = Field(default_factory=time.time)


class ProjectLLMConfig(BaseModel):
    """项目级 LLM 配置(T-P06)。每项目一份;执行时按项目构造 LLMClient。

    领域模型持**明文** api_key;加密是存储层职责(Store 存密文、读回解密),业务码不感知。
    """

    project_id: str  # 一项目一配置(主键)
    model: str = ""  # 带 provider 前缀,如 openai/xxx、ollama/xxx
    api_base: str = ""
    api_key: str = ""  # 明文(落库时加密)
    temperature: float = 0.0
    updated_at: float = Field(default_factory=time.time)
