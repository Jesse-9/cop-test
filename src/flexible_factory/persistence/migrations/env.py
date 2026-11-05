"""Alembic environment for the flexible-factory catalog database.

Database URL resolution order:
  1. ``-x db_url=...`` command line;
  2. ``sqlalchemy.url`` set programmatically (``persistence.db``);
  3. ``FLEXIBLE_FACTORY_CATALOG_URL`` environment variable.

The application does not rely on ``alembic.ini`` being present; the file is
provided for developer convenience only.
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, pool

# Allow ``alembic -c alembic.ini`` from the repository root without an
# editable install.
_SRC = Path(__file__).resolve().parents[3]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from flexible_factory.persistence.models import Base  # noqa: E402

config = context.config
target_metadata = Base.metadata

if (config.config_file_name is not None
        and config.attributes.get("configure_logger", True)):
    fileConfig(config.config_file_name, disable_existing_loggers=False)


def _database_url() -> str:
    x_args = context.get_x_argument(as_dictionary=True)
    url = (x_args.get("db_url")
           or config.get_main_option("sqlalchemy.url")
           or os.environ.get("FLEXIBLE_FACTORY_CATALOG_URL"))
    if not url:
        raise RuntimeError(
            "未指定数据库 URL: 使用 -x db_url=... 或设置 "
            "FLEXIBLE_FACTORY_CATALOG_URL")
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_database_url(), poolclass=pool.NullPool,
                           future=True)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
