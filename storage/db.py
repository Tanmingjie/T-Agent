"""SQLModel 持久化(规格 §4 注记 / §0 原则 2、4 / §7,T-21)。

数据层抽象:业务代码只与领域模型(``input.models`` 的 pydantic 结构)打交道,
**不直接写 SQL**;``Store`` 内部在领域模型 ↔ SQLModel 表行之间转换。SQLite +
aiosqlite;换 PostgreSQL 只改连接串。

核心表预留同步字段 ``updated_at`` / ``owner`` / ``external_id``(实现原则 4)。
嵌套/列表字段(steps、assertions、vocabulary、hooks…)以 JSON 列存储,保持单表简洁、
不引入 ORM 关系,契合"换库只改连接串"。

表行类用 ``*Row`` 命名以与领域模型区分;``PageVocabulary`` 无自然主键,用自增 id +
(url_pattern, page_title, login_role) 作为缓存键(规格 §5.5)。
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from typing import Type, TypeVar

from sqlalchemy import JSON, Column, event, func, inspect, text
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import Field, SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

logger = logging.getLogger(__name__)

import json

from input.models import (
    AuditLog,
    ExecutionRecord,
    PageVocabulary,
    Project,
    ProjectHttpTool,
    ProjectLLMConfig,
    ProjectMember,
    ProjectSkill,
    SessionProfile,
    Suite,
    TestCase,
    User,
    Version,
)
from storage import crypto

# ── 表行定义(table=True) ──────────────────────────────────────


class SuiteRow(SQLModel, table=True):
    __tablename__ = "suite"
    id: str = Field(primary_key=True)
    name: str = ""
    base_url: str = ""
    session_profile: str | None = None
    page_intelligence_id: str | None = None
    code_generator: str = "BDDGenerator"
    custom_prompt: str = ""
    hooks: dict = Field(default_factory=dict, sa_column=Column(JSON))
    project_id: str = Field(default="", index=True)  # 多租户(T-P04b)
    version_id: str = Field(default="", index=True)
    external_id: str | None = None
    owner: str | None = None
    updated_at: float = 0.0


class TestCaseRow(SQLModel, table=True):
    __tablename__ = "test_case"
    id: str = Field(primary_key=True)
    name: str = ""
    preconditions: list = Field(default_factory=list, sa_column=Column(JSON))
    precondition_confirmed: list = Field(default_factory=list, sa_column=Column(JSON))
    precondition_items: list = Field(default_factory=list, sa_column=Column(JSON))
    steps: list = Field(default_factory=list, sa_column=Column(JSON))
    expected: list = Field(default_factory=list, sa_column=Column(JSON))
    base_url: str = ""
    suite_id: str | None = Field(default=None, index=True)
    external_id: str | None = None
    owner: str | None = None
    updated_at: float = 0.0


class SessionProfileRow(SQLModel, table=True):
    __tablename__ = "session_profile"
    name: str = Field(primary_key=True)
    login_aw: str = ""
    cookie_store: str = ""
    valid_until: float | None = None
    base_url: str = ""
    project_id: str = Field(default="", index=True)  # 多租户(M2)
    cookies_encrypted: str = ""  # Cookie JSON 密文(M2;凭据,加密落库)
    owner: str | None = None
    updated_at: float = 0.0


class ProjectSkillRow(SQLModel, table=True):
    __tablename__ = "project_skill"
    project_id: str = Field(primary_key=True)
    name: str = Field(primary_key=True)
    description: str = ""  # 简述:常驻 prompt 清单,供 LLM 判断是否 load_skill 展开
    content: str = ""
    updated_at: float = 0.0


class ExecutionRecordRow(SQLModel, table=True):
    __tablename__ = "execution_record"
    exec_id: str = Field(primary_key=True)
    case_id: str = Field(default="", index=True)
    suite_id: str | None = None
    run_id: str | None = Field(default=None, index=True)  # 关联 RunRecord(Phase 4)
    steps: list = Field(default_factory=list, sa_column=Column(JSON))
    passed: bool = False
    case_assertions: list = Field(default_factory=list, sa_column=Column(JSON))
    spec: dict | None = Field(default=None, sa_column=Column(JSON))
    final_result: str = ""
    generated_code: str = ""
    token_usage: int = 0
    heal_count: int = 0
    metrics: dict = Field(default_factory=dict, sa_column=Column(JSON))  # 分阶段成本/质量指标(#6)
    start_time: float = 0.0
    end_time: float | None = None
    external_id: str | None = None
    owner: str | None = None
    updated_at: float = 0.0


class PageVocabularyRow(SQLModel, table=True):
    __tablename__ = "page_vocabulary"
    id: int | None = Field(default=None, primary_key=True)
    project_id: str = Field(default="", index=True)  # 多租户作用域(T-P04b),见 PageVocabulary
    base_url: str = Field(default="", index=True)  # 作用域键(跨系统隔离),见 PageVocabulary
    url_pattern: str = Field(default="", index=True)
    page_title: str = ""
    login_role: str = ""
    vocabulary: dict = Field(default_factory=dict, sa_column=Column(JSON))
    action_map: list = Field(default_factory=list, sa_column=Column(JSON))  # TODO: Phase 5
    stale: bool = False
    scanned_at: float = 0.0
    updated_at: float = 0.0


class RunRecordRow(SQLModel, table=True):
    """每次 Suite 执行产生的 run 记录(规格 §6 T-23)。"""

    __tablename__ = "run_record"
    id: str = Field(primary_key=True)  # UUID
    suite_id: str = Field(default="", index=True)
    project_id: str = Field(default="", index=True)  # 多租户(T-P04b)
    version_id: str = Field(default="", index=True)
    status: str = "running"  # running | completed | aborted | failed
    total_cases: int = 0
    passed_cases: int = 0
    failed_cases: int = 0
    started_at: float = 0.0
    finished_at: float | None = None
    updated_at: float = 0.0


class SuiteSettingsRow(SQLModel, table=True):
    """Suite 级执行配置(phase 4)。"""

    __tablename__ = "suite_settings"
    suite_id: str = Field(primary_key=True)
    permission_mode: str = "trust"  # trust | approve
    parallelism: int = 1  # 并发执行用例数(1=串行)
    updated_at: float = 0.0


class RunQueueRow(SQLModel, table=True):
    """执行任务队列(平台化 T-P08)。API 只入队;worker 进程 SKIP LOCKED 领取。

    心跳 claimed_at 供超时回收(worker 崩溃→任务回到 queued 重试)。
    """

    __tablename__ = "run_queue"
    run_id: str = Field(primary_key=True)
    suite_id: str = Field(default="", index=True)
    project_id: str = Field(default="", index=True)
    case_id: str | None = None
    status: str = Field(default="queued", index=True)  # queued | claimed | done | failed
    claimed_by: str = ""
    claimed_at: float = 0.0  # 心跳时间(超时回收依据);0=未领取
    attempts: int = 0
    created_at: float = 0.0


class RunEventRow(SQLModel, table=True):
    """跨进程进度事件(平台化 T-P09)。worker 追加,API /stream 尾随转 SSE。

    durable(可重放给晚到订阅者)+ 方言可移植(SQLite/PG 通用)。raw LISTEN/NOTIFY
    是延迟优化,留 M3。
    """

    __tablename__ = "run_event"
    seq: int | None = Field(default=None, primary_key=True)  # 自增,严格递增供尾随
    run_id: str = Field(default="", index=True)
    event_type: str = ""
    data: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: float = 0.0


class PermissionRequestRow(SQLModel, table=True):
    """跨进程权限审批工单(平台化 T-P09)。worker 写 pending 后轮询;API 端审批解决。"""

    __tablename__ = "permission_request"
    id: str = Field(primary_key=True)
    run_id: str = Field(default="", index=True)
    status: str = Field(default="pending", index=True)  # pending | approved | denied
    tool_name: str = ""
    reason: str = ""
    created_at: float = 0.0
    resolved_at: float | None = None


# ── 多租户表(平台化 T-P04)──────────────────────────────────────


class ProjectRow(SQLModel, table=True):
    __tablename__ = "project"
    id: str = Field(primary_key=True)
    name: str = ""
    description: str = ""
    owner: str | None = None
    max_concurrency: int = 0  # 项目级并发 run 配额(0=不限,M2)
    created_at: float = 0.0
    updated_at: float = 0.0


class AuditLogRow(SQLModel, table=True):
    __tablename__ = "audit_log"
    id: str = Field(primary_key=True)
    actor: str = Field(default="", index=True)
    action: str = ""
    project_id: str = Field(default="", index=True)
    target: str = ""
    detail: str = ""
    created_at: float = Field(default=0.0, index=True)


class VersionRow(SQLModel, table=True):
    __tablename__ = "version"
    id: str = Field(primary_key=True)
    project_id: str = Field(default="", index=True)
    name: str = ""
    status: str = "active"  # active | archived
    created_at: float = 0.0
    updated_at: float = 0.0


class UserRow(SQLModel, table=True):
    __tablename__ = "app_user"  # 避开部分 DB 的保留字 "user"
    id: str = Field(primary_key=True)
    display_name: str = ""
    is_platform_admin: bool = False
    updated_at: float = 0.0


class ProjectMemberRow(SQLModel, table=True):
    __tablename__ = "project_member"
    # 复合主键 (project_id, user_id):一个用户在一个项目里只有一种角色
    project_id: str = Field(primary_key=True)
    user_id: str = Field(primary_key=True)
    role: str = "tester"  # admin | tester
    updated_at: float = 0.0


class ProjectLLMConfigRow(SQLModel, table=True):
    __tablename__ = "project_llm_config"
    project_id: str = Field(primary_key=True)  # 一项目一配置
    model: str = ""
    api_base: str = ""
    api_key_encrypted: str = ""  # 密文落库(见 storage.crypto);领域模型回明文
    temperature: float = 0.0
    updated_at: float = 0.0


class ProjectHttpToolRow(SQLModel, table=True):
    __tablename__ = "project_http_tool"
    # 复合主键 (project_id, name)
    project_id: str = Field(primary_key=True)
    name: str = Field(primary_key=True)
    description: str = ""
    method: str = "GET"
    url: str = ""
    headers_encrypted: str = ""  # headers JSON 密文(可含凭据)
    body: str = ""
    parameters: dict = Field(default_factory=dict, sa_column=Column(JSON))
    when_to_use: str = ""
    timeout_seconds: int = 30
    updated_at: float = 0.0


_T = TypeVar("_T", bound=SQLModel)


class Store:
    """异步仓储。CRUD 方法收发领域模型,内部转换为表行。"""

    def __init__(self, url: str | None = None) -> None:
        # 连接串走 env(DATABASE_URL),缺省 SQLite 文件库(单机/CLI 向后兼容)。
        # 平台版传 postgresql+asyncpg://...;方言差异在此分支,业务代码不感知(规格 §0 原则2)。
        url = url or os.getenv("DATABASE_URL", "sqlite+aiosqlite:///storage/ai_test.db")
        self.url = url
        self.is_sqlite = url.startswith("sqlite")

        if self.is_sqlite:
            # SQLite 并发要点:后台执行任务写库的同时,API 还要读 /result。默认 rollback-journal
            # 下读写互斥 → 读请求被锁阻塞 → 前端"加载中"挂死、服务像崩了。WAL 让写不阻塞读。
            # 关键:PRAGMA journal_mode=WAL 在事务里会被 SQLite **静默忽略**,必须在每条新连接上、
            # 事务外执行才生效 → 用 connect 监听器逐连接设置(busy_timeout 兜底:撞锁等待而非抛)。
            # timeout 是 aiosqlite 的 connect 参数;asyncpg 不认,故只在 SQLite 下传。
            # 不指定 poolclass:SQLAlchemy 对 :memory: 自动用 StaticPool(测试)、文件库用 QueuePool。
            self.engine = create_async_engine(url, future=True, connect_args={"timeout": 30})

            @event.listens_for(self.engine.sync_engine, "connect")
            def _set_sqlite_pragma(dbapi_conn, _rec):  # noqa: ANN001
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA busy_timeout=30000")
                cur.close()

        else:
            # Postgres(asyncpg)等:MVCC 读写不互斥,无需 WAL pragma;pool_pre_ping 治
            # 长连接被服务端断开(开发期连接闲置后失效)。pool 参数用 SQLAlchemy 默认。
            self.engine = create_async_engine(url, future=True, pool_pre_ping=True)

        self._sf = sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)

    async def init(self) -> None:
        # schema 策略(T-P03):**平台部署用 Alembic**(`alembic upgrade head`)管 schema。
        # 这里的 create_all + 轻量迁移保留作**单机/CLI/测试的便利 fallback**(免每次跑 alembic)。
        # 因 Alembic 基线就是同一份 metadata 的 create_all,两条路径建出的 schema 一致、不漂移。
        async with self.engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
            # 轻量迁移:create_all 只建不存在的表、**不会给已存在的表加列**。新增模型字段
            # (如 test_case.precondition_items)在旧库里会缺列 → 查询 500。这里对比模型与
            # 实际表结构,对缺失列做 ALTER ADD COLUMN(SQLite 支持的可空/带默认列)。
            await conn.run_sync(self._migrate_add_missing_columns)

    @staticmethod
    def _migrate_add_missing_columns(sync_conn) -> None:  # noqa: ANN001
        insp = inspect(sync_conn)
        existing_tables = set(insp.get_table_names())
        # 表名 → SQLModel 类,用于查字段的「模型默认值」(SQLModel 列默认 nullable=True,
        # 无法靠 col.nullable 区分 ``str=""``(应回填空串)与 ``str|None=None``(应留 NULL))。
        name_to_cls = {m.local_table.name: m.class_ for m in SQLModel._sa_registry.mappers}
        for table in SQLModel.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            cls = name_to_cls.get(table.name)
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                coltype = col.type.compile(dialect=sync_conn.dialect)
                sync_conn.execute(
                    text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {coltype}')
                )
                # ALTER ADD COLUMN 把已有行的新列置 NULL。**仅非空集合列**(模型带
                # default_factory,如 steps=list / metrics=dict / vocabulary=dict)回填空 JSON,
                # 否则领域模型(期望 list/dict)校验失败;**按 factory 返回类型选 '{}' 或 '[]'**
                # (dict 列回填 '[]' 读回是 list 会触发 pydantic 序列化警告)。可空 JSON 列
                # (如 spec: dict|None,default=None)**保持 NULL**——回填 '{}'/'[]' 反而会让
                # `X | None` 模型拿空集合去建对象而校验失败。
                if isinstance(col.type, JSON):
                    field = cls.model_fields.get(col.name) if cls else None
                    factory = getattr(field, "default_factory", None)
                    if factory is not None:
                        try:
                            empty = "[]" if isinstance(factory(), list) else "{}"
                        except Exception:  # noqa: BLE001 — 取默认失败按 dict 兜底
                            empty = "{}"
                        sync_conn.execute(
                            text(
                                f'UPDATE "{table.name}" SET "{col.name}" = \'{empty}\' '
                                f'WHERE "{col.name}" IS NULL'
                            )
                        )
                else:
                    # 非可选字符串列(如 project_id/version_id,模型 ``str = ""``)旧行 NULL 会让
                    # 领域模型(期望 str)校验失败 → 按模型默认值回填。可选列(``str|None=None``)
                    # 默认 None,跳过,保持 NULL 语义正确。
                    field = cls.model_fields.get(col.name) if cls else None
                    default = getattr(field, "default", None)
                    if isinstance(default, str):
                        sync_conn.execute(
                            text(
                                f'UPDATE "{table.name}" SET "{col.name}" = :d '
                                f'WHERE "{col.name}" IS NULL'
                            ),
                            {"d": default},
                        )
                logger.info("DB 迁移:%s 补列 %s %s", table.name, col.name, coltype)

    async def close(self) -> None:
        await self.engine.dispose()

    # —— 通用 upsert(按主键 merge)——

    async def _upsert(self, row_cls: Type[_T], domain) -> None:
        data = domain.model_dump()
        data["updated_at"] = time.time()
        async with self._sf() as s:
            await s.merge(row_cls(**data))
            await s.commit()

    # —— TestCase ——

    async def save_case(self, tc: TestCase) -> None:
        await self._upsert(TestCaseRow, tc)

    async def get_case(self, case_id: str) -> TestCase | None:
        async with self._sf() as s:
            row = await s.get(TestCaseRow, case_id)
            return TestCase(**row.model_dump()) if row else None

    async def list_cases(self, suite_id: str | None = None) -> list[TestCase]:
        stmt = select(TestCaseRow)
        if suite_id is not None:
            stmt = stmt.where(TestCaseRow.suite_id == suite_id)
        async with self._sf() as s:
            rows = (await s.exec(stmt)).all()
            return [TestCase(**r.model_dump()) for r in rows]

    # —— ExecutionRecord ——

    async def save_record(self, rec: ExecutionRecord) -> None:
        await self._upsert(ExecutionRecordRow, rec)

    async def get_record(self, exec_id: str) -> ExecutionRecord | None:
        async with self._sf() as s:
            row = await s.get(ExecutionRecordRow, exec_id)
            return ExecutionRecord(**row.model_dump()) if row else None

    async def list_records(
        self, case_id: str | None = None, suite_id: str | None = None
    ) -> list[ExecutionRecord]:
        stmt = select(ExecutionRecordRow)
        if case_id is not None:
            stmt = stmt.where(ExecutionRecordRow.case_id == case_id)
        if suite_id is not None:
            stmt = stmt.where(ExecutionRecordRow.suite_id == suite_id)
        async with self._sf() as s:
            rows = (await s.exec(stmt)).all()
            return [ExecutionRecord(**r.model_dump()) for r in rows]

    # —— Suite ——

    async def save_suite(self, suite: Suite) -> None:
        await self._upsert(SuiteRow, suite)

    async def get_suite(self, suite_id: str) -> Suite | None:
        async with self._sf() as s:
            row = await s.get(SuiteRow, suite_id)
            return Suite(**row.model_dump()) if row else None

    async def list_suites(
        self, project_id: str | None = None, version_id: str | None = None
    ) -> list[Suite]:
        # 租户过滤:None=不过滤(向后兼容,单机/CLI 不传);传值则按项目/版本作用域隔离。
        stmt = select(SuiteRow)
        if project_id is not None:
            stmt = stmt.where(SuiteRow.project_id == project_id)
        if version_id is not None:
            stmt = stmt.where(SuiteRow.version_id == version_id)
        async with self._sf() as s:
            rows = (await s.exec(stmt)).all()
            return [Suite(**r.model_dump()) for r in rows]

    async def list_suite_status(
        self, project_id: str | None = None, version_id: str | None = None
    ) -> list[dict]:
        """套件列表 + 每套件用例数 + 最近一次执行摘要(版本工作区套件表用)。

        两条分组查询替代逐套件 N+1:用例数 group by、最近 run 按 started_at desc 取首条。
        """
        suites = await self.list_suites(project_id=project_id, version_id=version_id)
        ids = [s.id for s in suites]
        if not ids:
            return []
        async with self._sf() as s:
            # 用例数
            cc_rows = (
                await s.exec(
                    select(TestCaseRow.suite_id, func.count(TestCaseRow.id))
                    .where(TestCaseRow.suite_id.in_(ids))
                    .group_by(TestCaseRow.suite_id)
                )
            ).all()
            case_counts = {sid: n for sid, n in cc_rows}
            # 最近 run(取每套件 started_at 最新一条)
            run_rows = (
                await s.exec(
                    select(RunRecordRow)
                    .where(RunRecordRow.suite_id.in_(ids))
                    .order_by(RunRecordRow.started_at.desc())
                )
            ).all()
        last_run: dict[str, dict] = {}
        for r in run_rows:
            if r.suite_id not in last_run:  # 已按 desc 排序,首见即最新
                last_run[r.suite_id] = {
                    "id": r.id,
                    "status": r.status,
                    "total_cases": r.total_cases,
                    "passed_cases": r.passed_cases,
                    "failed_cases": r.failed_cases,
                    "finished_at": r.finished_at,
                    "started_at": r.started_at,
                }
        return [
            {
                **suite.model_dump(),
                "case_count": case_counts.get(suite.id, 0),
                "last_run": last_run.get(suite.id),
            }
            for suite in suites
        ]

    # —— SessionProfile(cookies 加密落库,M2)——

    async def save_session_profile(self, p: SessionProfile) -> None:
        row = SessionProfileRow(
            name=p.name,
            login_aw=p.login_aw,
            cookie_store=p.cookie_store,
            valid_until=p.valid_until,
            base_url=p.base_url,
            project_id=p.project_id,
            cookies_encrypted=(
                crypto.encrypt(json.dumps(p.cookies, ensure_ascii=False)) if p.cookies else ""
            ),
            owner=p.owner,
            updated_at=time.time(),
        )
        async with self._sf() as s:
            await s.merge(row)
            await s.commit()

    def _session_from_row(self, row: SessionProfileRow) -> SessionProfile:
        cookies: list = []
        if row.cookies_encrypted:
            raw = crypto.decrypt(row.cookies_encrypted)
            try:
                cookies = json.loads(raw) if raw else []
            except (json.JSONDecodeError, ValueError):
                cookies = []
        return SessionProfile(
            name=row.name,
            login_aw=row.login_aw,
            cookie_store=row.cookie_store,
            valid_until=row.valid_until,
            base_url=row.base_url,
            project_id=row.project_id,
            cookies=cookies,
            owner=row.owner,
            updated_at=row.updated_at,
        )

    async def get_session_profile(self, name: str) -> SessionProfile | None:
        async with self._sf() as s:
            row = await s.get(SessionProfileRow, name)
            return self._session_from_row(row) if row else None

    async def list_session_profiles(self, project_id: str | None = None) -> list[SessionProfile]:
        stmt = select(SessionProfileRow)
        if project_id is not None:
            stmt = stmt.where(SessionProfileRow.project_id == project_id)
        async with self._sf() as s:
            return [self._session_from_row(r) for r in (await s.exec(stmt)).all()]

    async def delete_session_profile(self, name: str) -> bool:
        async with self._sf() as s:
            row = await s.get(SessionProfileRow, name)
            if row is None:
                return False
            await s.delete(row)
            await s.commit()
            return True

    # —— ProjectSkill(项目级业务常识,M2)——

    async def save_skill(self, skill: ProjectSkill) -> None:
        await self._upsert(ProjectSkillRow, skill)

    async def list_skills(self, project_id: str) -> list[ProjectSkill]:
        async with self._sf() as s:
            stmt = select(ProjectSkillRow).where(ProjectSkillRow.project_id == project_id)
            return [ProjectSkill(**r.model_dump()) for r in (await s.exec(stmt)).all()]

    async def delete_skill(self, project_id: str, name: str) -> bool:
        async with self._sf() as s:
            row = await s.get(ProjectSkillRow, (project_id, name))
            if row is None:
                return False
            await s.delete(row)
            await s.commit()
            return True

    # —— PageVocabulary(按缓存键 upsert)——

    async def save_vocabulary(self, v: PageVocabulary) -> None:
        data = v.model_dump()
        data["updated_at"] = time.time()
        async with self._sf() as s:
            stmt = select(PageVocabularyRow).where(
                PageVocabularyRow.project_id == v.project_id,
                PageVocabularyRow.base_url == v.base_url,
                PageVocabularyRow.url_pattern == v.url_pattern,
                PageVocabularyRow.page_title == v.page_title,
                PageVocabularyRow.login_role == v.login_role,
            )
            existing = (await s.exec(stmt)).first()
            if existing is not None:
                for k, val in data.items():
                    setattr(existing, k, val)
                s.add(existing)
            else:
                s.add(PageVocabularyRow(**data))
            await s.commit()

    async def get_vocabulary(
        self,
        url_pattern: str,
        page_title: str,
        login_role: str,
        base_url: str = "",
        project_id: str = "",
    ) -> PageVocabulary | None:
        async with self._sf() as s:
            stmt = select(PageVocabularyRow).where(
                PageVocabularyRow.project_id == project_id,
                PageVocabularyRow.base_url == base_url,
                PageVocabularyRow.url_pattern == url_pattern,
                PageVocabularyRow.page_title == page_title,
                PageVocabularyRow.login_role == login_role,
            )
            row = (await s.exec(stmt)).first()
            if row is None:
                return None
            data = row.model_dump()
            data.pop("id", None)  # 领域模型无 id 字段
            return PageVocabulary(**data)

    async def list_vocabularies(self, project_id: str | None = None) -> list[PageVocabulary]:
        # 租户过滤:None=不过滤(向后兼容,单机/CLI);传值则按项目隔离。
        stmt = select(PageVocabularyRow)
        if project_id is not None:
            stmt = stmt.where(PageVocabularyRow.project_id == project_id)
        async with self._sf() as s:
            rows = (await s.exec(stmt)).all()
            out = []
            for r in rows:
                data = r.model_dump()
                data.pop("id", None)
                out.append(PageVocabulary(**data))
            return out

    # —— Project(多租户 T-P04)——

    async def save_project(self, p: Project) -> None:
        await self._upsert(ProjectRow, p)

    async def get_project(self, project_id: str) -> Project | None:
        async with self._sf() as s:
            row = await s.get(ProjectRow, project_id)
            return Project(**row.model_dump()) if row else None

    async def list_projects(self) -> list[Project]:
        async with self._sf() as s:
            rows = (await s.exec(select(ProjectRow))).all()
            return [Project(**r.model_dump()) for r in rows]

    async def delete_project(self, project_id: str) -> bool:
        async with self._sf() as s:
            row = await s.get(ProjectRow, project_id)
            if row is None:
                return False
            await s.delete(row)
            await s.commit()
            return True

    # —— Version ——

    async def save_version(self, v: Version) -> None:
        await self._upsert(VersionRow, v)

    async def get_version(self, version_id: str) -> Version | None:
        async with self._sf() as s:
            row = await s.get(VersionRow, version_id)
            return Version(**row.model_dump()) if row else None

    async def list_versions(self, project_id: str | None = None) -> list[Version]:
        stmt = select(VersionRow)
        if project_id is not None:
            stmt = stmt.where(VersionRow.project_id == project_id)
        async with self._sf() as s:
            rows = (await s.exec(stmt)).all()
            return [Version(**r.model_dump()) for r in rows]

    async def clone_version_suites(self, from_version_id: str, to_version_id: str) -> int:
        """把 from 版本下的所有 Suite(含用例、执行设置)拷到 to 版本(版本继承,已拍板)。

        显式动作:新版本从上一版本拷一份 Suite 后独立演进。生成新 Suite/用例 id;
        **不拷执行历史**(run/record 属各版本自有)。两版本须同项目(防跨租户拷贝)。
        返回拷贝的 Suite 数。
        """
        from_v = await self.get_version(from_version_id)
        to_v = await self.get_version(to_version_id)
        if from_v is None or to_v is None:
            raise ValueError("源/目标版本不存在")
        if from_v.project_id != to_v.project_id:
            raise ValueError("不能跨项目拷贝版本 Suite")

        suites = await self.list_suites(version_id=from_version_id)
        count = 0
        for suite in suites:
            new_suite_id = uuid.uuid4().hex
            cases = await self.list_cases(suite_id=suite.id)
            async with self._sf() as s:
                suite_data = suite.model_dump()
                suite_data.update(id=new_suite_id, version_id=to_version_id)
                s.add(SuiteRow(**suite_data))
                # 用例随 Suite 拷贝(新 id,挂新 Suite)
                for tc in cases:
                    tc_data = tc.model_dump()
                    tc_data.update(id=uuid.uuid4().hex, suite_id=new_suite_id)
                    s.add(TestCaseRow(**tc_data))
                # 执行设置随 Suite 拷贝(并发/权限模式)
                old_settings = await s.get(SuiteSettingsRow, suite.id)
                if old_settings is not None:
                    s.add(
                        SuiteSettingsRow(
                            suite_id=new_suite_id,
                            permission_mode=old_settings.permission_mode,
                            parallelism=old_settings.parallelism,
                            updated_at=time.time(),
                        )
                    )
                await s.commit()
            count += 1
        return count

    # —— User ——

    async def save_user(self, u: User) -> None:
        await self._upsert(UserRow, u)

    async def get_user(self, user_id: str) -> User | None:
        async with self._sf() as s:
            row = await s.get(UserRow, user_id)
            return User(**row.model_dump()) if row else None

    async def list_users(self) -> list[User]:
        async with self._sf() as s:
            rows = (await s.exec(select(UserRow))).all()
            return [User(**r.model_dump()) for r in rows]

    # —— ProjectMember(复合主键 project_id+user_id)——

    async def save_member(self, m: ProjectMember) -> None:
        await self._upsert(ProjectMemberRow, m)

    async def get_member(self, project_id: str, user_id: str) -> ProjectMember | None:
        async with self._sf() as s:
            row = await s.get(ProjectMemberRow, (project_id, user_id))
            return ProjectMember(**row.model_dump()) if row else None

    async def list_members(self, project_id: str) -> list[ProjectMember]:
        async with self._sf() as s:
            stmt = select(ProjectMemberRow).where(ProjectMemberRow.project_id == project_id)
            rows = (await s.exec(stmt)).all()
            return [ProjectMember(**r.model_dump()) for r in rows]

    async def list_memberships(self, user_id: str) -> list[ProjectMember]:
        """某用户加入的所有项目(用于「我的项目」列表 / 鉴权)。"""
        async with self._sf() as s:
            stmt = select(ProjectMemberRow).where(ProjectMemberRow.user_id == user_id)
            rows = (await s.exec(stmt)).all()
            return [ProjectMember(**r.model_dump()) for r in rows]

    async def delete_member(self, project_id: str, user_id: str) -> bool:
        async with self._sf() as s:
            row = await s.get(ProjectMemberRow, (project_id, user_id))
            if row is None:
                return False
            await s.delete(row)
            await s.commit()
            return True

    # —— ProjectLLMConfig(api_key 加密落库)——

    async def save_llm_config(self, cfg: ProjectLLMConfig) -> None:
        row = ProjectLLMConfigRow(
            project_id=cfg.project_id,
            model=cfg.model,
            api_base=cfg.api_base,
            api_key_encrypted=crypto.encrypt(cfg.api_key),  # 明文 → 密文落库
            temperature=cfg.temperature,
            updated_at=time.time(),
        )
        async with self._sf() as s:
            await s.merge(row)
            await s.commit()

    async def get_llm_config(self, project_id: str) -> ProjectLLMConfig | None:
        async with self._sf() as s:
            row = await s.get(ProjectLLMConfigRow, project_id)
            if row is None:
                return None
            return ProjectLLMConfig(
                project_id=row.project_id,
                model=row.model,
                api_base=row.api_base,
                api_key=crypto.decrypt(row.api_key_encrypted),  # 密文 → 明文回领域模型
                temperature=row.temperature,
                updated_at=row.updated_at,
            )

    async def delete_llm_config(self, project_id: str) -> bool:
        async with self._sf() as s:
            row = await s.get(ProjectLLMConfigRow, project_id)
            if row is None:
                return False
            await s.delete(row)
            await s.commit()
            return True

    # —— ProjectHttpTool(headers 加密落库)——

    async def save_http_tool(self, tool: ProjectHttpTool) -> None:
        row = ProjectHttpToolRow(
            project_id=tool.project_id,
            name=tool.name,
            description=tool.description,
            method=tool.method,
            url=tool.url,
            headers_encrypted=crypto.encrypt(json.dumps(tool.headers, ensure_ascii=False)),
            body=tool.body,
            parameters=tool.parameters,
            when_to_use=tool.when_to_use,
            timeout_seconds=tool.timeout_seconds,
            updated_at=time.time(),
        )
        async with self._sf() as s:
            await s.merge(row)
            await s.commit()

    def _http_tool_from_row(self, row: ProjectHttpToolRow) -> ProjectHttpTool:
        raw = crypto.decrypt(row.headers_encrypted)
        try:
            headers = json.loads(raw) if raw else {}
        except (json.JSONDecodeError, ValueError):
            headers = {}
        return ProjectHttpTool(
            project_id=row.project_id,
            name=row.name,
            description=row.description,
            method=row.method,
            url=row.url,
            headers=headers,
            body=row.body,
            parameters=row.parameters,
            when_to_use=row.when_to_use,
            timeout_seconds=row.timeout_seconds,
            updated_at=row.updated_at,
        )

    async def list_http_tools(self, project_id: str) -> list[ProjectHttpTool]:
        async with self._sf() as s:
            stmt = select(ProjectHttpToolRow).where(ProjectHttpToolRow.project_id == project_id)
            return [self._http_tool_from_row(r) for r in (await s.exec(stmt)).all()]

    async def delete_http_tool(self, project_id: str, name: str) -> bool:
        async with self._sf() as s:
            row = await s.get(ProjectHttpToolRow, (project_id, name))
            if row is None:
                return False
            await s.delete(row)
            await s.commit()
            return True

    # —— 执行任务队列(T-P08:API 入队,worker 领取)——

    async def enqueue_run(
        self, run_id: str, suite_id: str, project_id: str = "", case_id: str | None = None
    ) -> None:
        async with self._sf() as s:
            s.add(
                RunQueueRow(
                    run_id=run_id,
                    suite_id=suite_id,
                    project_id=project_id,
                    case_id=case_id,
                    status="queued",
                    created_at=time.time(),
                )
            )
            await s.commit()

    async def claim_next_run(
        self,
        worker_id: str,
        *,
        stale_seconds: float = 120.0,
        max_project_concurrency: int = 0,
    ) -> RunQueueRow | None:
        """领取下一条待执行任务(FIFO)。PG 用 FOR UPDATE SKIP LOCKED 防多 worker 抢同一条;
        SQLite 单写无并发抢占问题。返回领到的行(已置 claimed),无则 None。

        - 先回收**心跳超时**的 claimed 任务(worker 崩溃)→ queued 重试。
        - ``max_project_concurrency>0`` 时,跳过已达该项目并发上限的项目任务(配额)。
        """
        now = time.time()
        async with self._sf() as s:
            # 1) 回收超时
            await s.exec(
                sa_update(RunQueueRow)
                .where(
                    RunQueueRow.status == "claimed",
                    RunQueueRow.claimed_at < now - stale_seconds,
                )
                .values(status="queued", claimed_by="", claimed_at=0.0)
            )
            await s.commit()

            # 2) 取候选 queued(FIFO),PG 上 SKIP LOCKED 防抢
            stmt = (
                select(RunQueueRow)
                .where(RunQueueRow.status == "queued")
                .order_by(RunQueueRow.created_at)
            )
            if not self.is_sqlite:
                stmt = stmt.with_for_update(skip_locked=True)
            candidates = (await s.exec(stmt)).all()
            if not candidates:
                return None

            # 配额判定:全局 max_project_concurrency>0 时统一用它;否则查各项目 Project.max_concurrency
            # (0=不限)。两者皆无限制则直接取队首。
            counts = dict(
                (
                    await s.exec(
                        select(RunQueueRow.project_id, func.count())
                        .where(RunQueueRow.status == "claimed")
                        .group_by(RunQueueRow.project_id)
                    )
                ).all()
            )

            async def _limit_for(pid: str) -> int:
                if max_project_concurrency > 0:
                    return max_project_concurrency
                if not pid:
                    return 0
                prj = await s.get(ProjectRow, pid)
                return prj.max_concurrency if prj else 0

            chosen = None
            for row in candidates:
                limit = await _limit_for(row.project_id)
                if limit <= 0 or counts.get(row.project_id, 0) < limit:
                    chosen = row
                    break

            if chosen is None:
                return None
            chosen.status = "claimed"
            chosen.claimed_by = worker_id
            chosen.claimed_at = now
            chosen.attempts += 1
            s.add(chosen)
            await s.commit()
            await s.refresh(chosen)
            return chosen

    async def heartbeat_run(self, run_id: str) -> None:
        async with self._sf() as s:
            await s.exec(
                sa_update(RunQueueRow)
                .where(RunQueueRow.run_id == run_id)
                .values(claimed_at=time.time())
            )
            await s.commit()

    async def complete_queued_run(self, run_id: str, status: str = "done") -> None:
        async with self._sf() as s:
            await s.exec(
                sa_update(RunQueueRow).where(RunQueueRow.run_id == run_id).values(status=status)
            )
            await s.commit()

    async def get_queued_run(self, run_id: str) -> RunQueueRow | None:
        async with self._sf() as s:
            return await s.get(RunQueueRow, run_id)

    # —— 跨进程进度事件(T-P09)——

    async def append_run_event(self, run_id: str, event_type: str, data: dict) -> None:
        async with self._sf() as s:
            s.add(
                RunEventRow(run_id=run_id, event_type=event_type, data=data, created_at=time.time())
            )
            await s.commit()

    async def list_run_events(self, run_id: str, after_seq: int = 0) -> list[RunEventRow]:
        async with self._sf() as s:
            stmt = (
                select(RunEventRow)
                .where(RunEventRow.run_id == run_id, RunEventRow.seq > after_seq)
                .order_by(RunEventRow.seq)
            )
            return list((await s.exec(stmt)).all())

    # —— 跨进程权限审批工单(T-P09)——

    async def create_permission_request(
        self, req_id: str, run_id: str, tool_name: str, reason: str
    ) -> None:
        async with self._sf() as s:
            s.add(
                PermissionRequestRow(
                    id=req_id,
                    run_id=run_id,
                    status="pending",
                    tool_name=tool_name,
                    reason=reason,
                    created_at=time.time(),
                )
            )
            await s.commit()

    async def get_permission_request(self, req_id: str) -> PermissionRequestRow | None:
        async with self._sf() as s:
            return await s.get(PermissionRequestRow, req_id)

    async def resolve_permission_request(self, req_id: str, approved: bool) -> bool:
        async with self._sf() as s:
            row = await s.get(PermissionRequestRow, req_id)
            if row is None or row.status != "pending":
                return False
            row.status = "approved" if approved else "denied"
            row.resolved_at = time.time()
            s.add(row)
            await s.commit()
            return True

    async def list_pending_permission_requests(self, run_id: str) -> list[PermissionRequestRow]:
        async with self._sf() as s:
            stmt = select(PermissionRequestRow).where(
                PermissionRequestRow.run_id == run_id,
                PermissionRequestRow.status == "pending",
            )
            return list((await s.exec(stmt)).all())

    # —— 版本维度报告:按项目/版本列 run（M2)——

    async def list_runs(self, project_id: str, version_id: str | None = None) -> list[dict]:
        stmt = select(RunRecordRow).where(RunRecordRow.project_id == project_id)
        if version_id:
            stmt = stmt.where(RunRecordRow.version_id == version_id)
        stmt = stmt.order_by(RunRecordRow.started_at.desc())
        async with self._sf() as s:
            rows = (await s.exec(stmt)).all()
            return [
                {
                    "id": r.id,
                    "suite_id": r.suite_id,
                    "version_id": r.version_id,
                    "status": r.status,
                    "total_cases": r.total_cases,
                    "passed_cases": r.passed_cases,
                    "failed_cases": r.failed_cases,
                    "started_at": r.started_at,
                    "finished_at": r.finished_at,
                }
                for r in rows
            ]

    # —— 审计日志(M2)——

    async def append_audit(
        self, actor: str, action: str, *, project_id: str = "", target: str = "", detail: str = ""
    ) -> None:
        async with self._sf() as s:
            s.add(
                AuditLogRow(
                    id=uuid.uuid4().hex,
                    actor=actor,
                    action=action,
                    project_id=project_id,
                    target=target,
                    detail=detail,
                    created_at=time.time(),
                )
            )
            await s.commit()

    async def list_audit(self, project_id: str | None = None, limit: int = 100) -> list[AuditLog]:
        stmt = select(AuditLogRow)
        if project_id is not None:
            stmt = stmt.where(AuditLogRow.project_id == project_id)
        stmt = stmt.order_by(AuditLogRow.created_at.desc()).limit(limit)
        async with self._sf() as s:
            return [AuditLog(**r.model_dump()) for r in (await s.exec(stmt)).all()]
