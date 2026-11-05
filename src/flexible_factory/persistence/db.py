"""persistence.db — SQLite Engine/Session、外键 PRAGMA 与 Alembic 入口.

数据库路径由调用方显式注入 (CLI ``--db`` 或测试临时目录), 不写入受保护的
场景工件目录. 每次连接都执行并核验 ``PRAGMA foreign_keys = ON``; SQLite
默认不启用外键, 不能只依赖 ORM relationship.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from .errors import CatalogPersistenceError, MigrationError

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.execute("PRAGMA foreign_keys")
        row = cursor.fetchone()
        if row is None or int(row[0]) != 1:
            raise MigrationError(
                "SQLite 连接未能启用 foreign_keys; 拒绝在无外键约束下运行")
    finally:
        cursor.close()


@dataclass(frozen=True)
class CatalogDatabase:
    """一个本地 SQLite 目录数据库; 路径必须指向文件, 不支持内存库."""

    path: Path

    def __post_init__(self) -> None:
        path = Path(self.path).expanduser().resolve()
        if str(path) == ":memory:":
            raise MigrationError(
                "目录数据库必须使用文件路径: 迁移与多连接需要共享同一库")
        object.__setattr__(self, "path", path)

    @property
    def url(self) -> str:
        return f"sqlite:///{self.path.as_posix()}"

    def create_engine(self) -> Engine:
        engine = create_engine(self.url, future=True)
        event.listen(engine, "connect", _enable_foreign_keys)
        return engine

    @contextmanager
    def session(self) -> Iterator[Session]:
        """显式会话边界; 调用方负责 ``with session.begin()`` 提交."""
        engine = self.create_engine()
        factory = sessionmaker(bind=engine, expire_on_commit=False,
                               future=True)
        session = factory()
        try:
            yield session
        except SQLAlchemyError as exc:
            session.rollback()
            raise CatalogPersistenceError(
                f"SQLite 访问失败 ({self.path}): "
                f"{type(exc).__name__}: {exc}") from exc
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
            engine.dispose()

    def connect(self) -> Connection:
        return self.create_engine().connect()


def alembic_config(database: CatalogDatabase) -> Config:
    """程序化 Alembic 配置; 不依赖当前工作目录下的 alembic.ini."""
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", database.url)
    config.attributes["configure_logger"] = False
    return config


def initialize_database(database: CatalogDatabase) -> dict:
    """创建父目录并从空库升级到 head; 重复调用无副作用."""
    database.path.parent.mkdir(parents=True, exist_ok=True)
    try:
        command.upgrade(alembic_config(database), "head")
    except Exception as exc:      # Alembic 的异常类型不作为本包公共契约
        raise MigrationError(
            f"目录数据库迁移失败 ({database.path}): "
            f"{type(exc).__name__}: {exc}") from exc
    revision = current_revision(database)
    if not revision:
        raise MigrationError(f"迁移后未找到 alembic_version: {database.path}")
    return {"ok": True, "database": str(database.path),
            "revision": revision}


def current_revision(database: CatalogDatabase) -> str:
    """读取 Alembic 当前 revision; 未初始化时抛出 MigrationError."""
    engine = database.create_engine()
    try:
        with engine.connect() as connection:
            inspector_tables = connection.execute(text(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name = 'alembic_version'")).fetchone()
            if inspector_tables is None:
                raise MigrationError(
                    f"{database.path} 尚未初始化; 先执行 catalog init")
            row = connection.execute(
                text("SELECT version_num FROM alembic_version")).fetchone()
            if row is None:
                raise MigrationError("alembic_version 为空; 先执行迁移")
            return str(row[0])
    except SQLAlchemyError as exc:
        raise MigrationError(
            f"读取数据库迁移版本失败: {type(exc).__name__}: {exc}") from exc
    finally:
        engine.dispose()


def table_names(database: CatalogDatabase) -> list[str]:
    with database.connect() as connection:
        rows = connection.execute(text(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name")).fetchall()
    return [str(row[0]) for row in rows]
