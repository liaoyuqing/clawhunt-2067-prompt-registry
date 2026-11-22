"""Alembic environment.

The URL comes from DATABASE_URL only. Migrations are written for PostgreSQL 16
(the deployment target); SQLite can apply them too because every column type
used here has a SQLite counterpart, which keeps local development honest.
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Make the project importable when alembic is run from the repository root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.dbcheck import build_engine, redact_url  # noqa: E402
from app.models import Base  # noqa: E402,F401  (declarative base and tables)

config = context.config

if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set; alembic will not guess a database. "
            "Export it before running alembic upgrade head."
        )
    return url


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting (used for migration review)."""
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = _url()
    print(f"alembic: migrating {redact_url(url)}", file=sys.stderr)
    engine = build_engine(url)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
