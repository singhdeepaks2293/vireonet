from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context
from vireonet import models  # noqa: F401  (import registers tables)
from vireonet.config import get_settings
from vireonet.db import Base
from vireonet.drift import models as drift_models  # noqa: F401  (registers drift tables)

config = context.config
# alembic.ini in this repo has no [loggers]/[formatters] sections
# (logging is configured by structlog in the app), so only apply
# fileConfig when the ini actually contains a logging config.
if config.config_file_name is not None:
    from configparser import ConfigParser

    _parser = ConfigParser()
    _parser.read(config.config_file_name)
    if _parser.has_section("formatters"):
        fileConfig(config.config_file_name)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=str(get_settings().database_url), target_metadata=target_metadata, literal_binds=True
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):  # type: ignore[no-untyped-def]
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(str(get_settings().database_url))
    async with engine.begin() as conn:
        await conn.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
