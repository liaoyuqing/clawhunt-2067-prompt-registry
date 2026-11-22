"""Operational database checks: reachable? migrated?

Kept separate from app.db (which only manages the engine and sessions) so that
both the CLI and the API share one definition of "the database is ready".
"""

from __future__ import annotations

import sys
from typing import Iterable

from sqlalchemy import create_engine, inspect as sqlalchemy_inspect
from sqlalchemy.engine import Engine

from app import config

#: Tables without which this application cannot serve a single request.
REQUIRED_TABLES: tuple[str, ...] = (
    "ai_prompt_version",
    "ai_model_profile",
    "eval_run",
    "eval_result",
)


class DatabaseUnavailable(RuntimeError):
    """The database is unreachable, unreadable, or missing tables.

    The CLI maps this exception to exit code 2 so that an operational problem is
    never confused with a prompt-content problem (exit code 1): in CI the two are
    investigated in completely different ways.
    """

    def __init__(self, message: str, *, reason: str = "unavailable") -> None:
        super().__init__(message)
        self.message = message
        self.reason = reason


def is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def build_engine(url: str, *, echo: bool = False) -> Engine:
    """Create an engine; the driver import happens only when it is used."""
    kwargs: dict = {"future": True}
    if is_sqlite(url):
        kwargs["connect_args"] = {"check_same_thread": False}
    try:
        return create_engine(url, **kwargs)
    except ModuleNotFoundError as exc:
        raise DatabaseUnavailable(
            f"database driver for {url.split(':', 1)[0]!r} is not installed: {exc}; "
            "install requirements-postgres.txt or point DATABASE_URL at sqlite",
            reason="driver-missing",
        ) from exc
    except Exception as exc:  # pragma: no cover - defensive
        raise DatabaseUnavailable(
            f"could not build a database engine from DATABASE_URL: {exc}",
            reason="engine",
        ) from exc


def redact_url(url: str) -> str:
    """Never echo credentials from a DSN into a log line."""
    if "@" in url and "://" in url:
        scheme, _, rest = url.partition("://")
        host = rest.split("@", 1)[1]
        return f"{scheme}://***@{host}"
    return url


def engine_from_env(*, required: bool = True) -> Engine | None:
    """Engine built from DATABASE_URL, or None when it is optional and unset."""
    url = config.database_url(required=required)
    if url is None:
        return None
    return build_engine(url)


def missing_tables(engine: Engine) -> list[str]:
    """Raise when unreachable; return the required tables that are absent."""
    try:
        with engine.connect() as connection:
            existing = set(sqlalchemy_inspect(connection).get_table_names())
    except DatabaseUnavailable:
        raise
    except Exception as exc:
        raise DatabaseUnavailable(
            "cannot connect to the database described by DATABASE_URL: "
            f"{type(exc).__name__}: {exc}",
            reason="connect",
        ) from exc
    return [name for name in REQUIRED_TABLES if name not in existing]


def assert_migrated(engine: Engine) -> None:
    """Raise DatabaseUnavailable when the schema has not been created."""
    missing = missing_tables(engine)
    if missing:
        raise DatabaseUnavailable(
            "database is reachable but not migrated (missing tables: "
            + ", ".join(missing)
            + "); run alembic upgrade head first",
            reason="not-migrated",
        )


def create_all(engine: Engine) -> None:
    """Build the schema from the models; used by `python -m app.dbcheck init`."""
    from app.models import Base  # noqa: F401  registers the tables on Base.metadata

    Base.metadata.create_all(engine)


def _main(argv: Iterable[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    command = arguments[0] if arguments else "check"
    try:
        url = config.database_url(required=True)
    except config.ConfigError as exc:
        print(f"[error] {exc.message}", file=sys.stderr)
        return 2
    assert url is not None
    engine = build_engine(url)
    if command == "init":
        if not is_sqlite(url):
            print(
                "[error] init only builds schemas for sqlite; "
                "use alembic upgrade head for PostgreSQL",
                file=sys.stderr,
            )
            return 2
        create_all(engine)
        print(f"schema ready in {redact_url(url)}")
        return 0
    if command == "check":
        try:
            assert_migrated(engine)
        except DatabaseUnavailable as exc:
            print(f"[error] code={exc.reason} {exc.message}", file=sys.stderr)
            print("exit-code 2", file=sys.stderr)
            return 2
        print(f"database ready at {redact_url(url)}")
        return 0
    print("usage: python -m app.dbcheck [check|init]", file=sys.stderr)
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
