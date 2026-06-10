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
from typing import Type, TypeVar

from sqlalchemy import JSON, Column, event, inspect, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import Field, SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

logger = logging.getLogger(__name__)

from input.models import (
    ExecutionRecord,
    PageVocabulary,
    Project,
    ProjectMember,
    SessionProfile,
    Suite,
    TestCase,
    User,
    Version,
)

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
    owner: str | None = None
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
    start_time: float = 0.0
    end_time: float | None = None
    external_id: str | None = None
    owner: str | None = None
    updated_at: float = 0.0


class PageVocabularyRow(SQLModel, table=True):
    __tablename__ = "page_vocabulary"
    id: int | None = Field(default=None, primary_key=True)
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


# ── 多租户表(平台化 T-P04)──────────────────────────────────────


class ProjectRow(SQLModel, table=True):
    __tablename__ = "project"
    id: str = Field(primary_key=True)
    name: str = ""
    description: str = ""
    owner: str | None = None
    created_at: float = 0.0
    updated_at: float = 0.0


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
        for table in SQLModel.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                coltype = col.type.compile(dialect=sync_conn.dialect)
                sync_conn.execute(
                    text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {coltype}')
                )
                # ALTER ADD COLUMN 把已有行的新列置 NULL;JSON 列(列表/字典)读回时会让
                # 领域模型(期望 list/dict)校验失败 → 回填空 JSON '[]',与新建行默认一致。
                if isinstance(col.type, JSON):
                    sync_conn.execute(
                        text(
                            f'UPDATE "{table.name}" SET "{col.name}" = \'[]\' '
                            f'WHERE "{col.name}" IS NULL'
                        )
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

    async def list_suites(self) -> list[Suite]:
        async with self._sf() as s:
            rows = (await s.exec(select(SuiteRow))).all()
            return [Suite(**r.model_dump()) for r in rows]

    # —— SessionProfile ——

    async def save_session_profile(self, p: SessionProfile) -> None:
        await self._upsert(SessionProfileRow, p)

    async def get_session_profile(self, name: str) -> SessionProfile | None:
        async with self._sf() as s:
            row = await s.get(SessionProfileRow, name)
            return SessionProfile(**row.model_dump()) if row else None

    # —— PageVocabulary(按缓存键 upsert)——

    async def save_vocabulary(self, v: PageVocabulary) -> None:
        data = v.model_dump()
        data["updated_at"] = time.time()
        async with self._sf() as s:
            stmt = select(PageVocabularyRow).where(
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
        self, url_pattern: str, page_title: str, login_role: str, base_url: str = ""
    ) -> PageVocabulary | None:
        async with self._sf() as s:
            stmt = select(PageVocabularyRow).where(
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

    async def list_vocabularies(self) -> list[PageVocabulary]:
        async with self._sf() as s:
            rows = (await s.exec(select(PageVocabularyRow))).all()
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
