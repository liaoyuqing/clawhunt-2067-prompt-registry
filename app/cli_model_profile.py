"""model-profile: register a model sampling profile by alias.

Only the alias is stored. The real endpoint is resolved from the environment at
call time by `app.config.resolve_model_endpoint`, which is why no address can
appear in this database or in this repository.
"""

from __future__ import annotations

import argparse
import json
import sys

from sqlalchemy import select

from app import config
from app.db import get_session_factory, init_engine, reset_engine
from app.dbcheck import DatabaseUnavailable, assert_migrated, engine_from_env
from app.models import AiModelProfile

EXIT_OK = 0
EXIT_CONTENT = 1
EXIT_OPERATIONAL = 2

#: Sampling parameters this command accepts; the JSON column stores whatever is
#: registered, and the API returns it verbatim so the caller sees the same body.
PARAM_NAMES: tuple[str, ...] = ("temperature", "max_tokens", "top_p")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="model-profile",
        description="Register or update a model sampling profile (alias only).",
    )
    parser.add_argument("--name", required=True, help="profile name, unique per org")
    parser.add_argument(
        "--endpoint-alias",
        required=True,
        help="alias such as model.main; its address lives in MODEL_ALIAS_MODEL_MAIN",
    )
    parser.add_argument("--temperature", type=float, default=None)
    parser.add_argument("--max-tokens", type=int, default=None)
    parser.add_argument("--top-p", type=float, default=None)
    parser.add_argument(
        "--dry-run", action="store_true", help="validate and print, never write"
    )
    return parser


def _fail(message: str, *, exit_code: int) -> int:
    print(f"[error] {message}", file=sys.stderr)
    print(f"exit-code {exit_code}", file=sys.stderr)
    return exit_code


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)

    params: dict[str, float | int] = {}
    if arguments.temperature is not None:
        if not 0.0 <= arguments.temperature <= 2.0:
            return _fail("temperature must be within 0.0 and 2.0", exit_code=EXIT_CONTENT)
        params["temperature"] = arguments.temperature
    if arguments.max_tokens is not None:
        if arguments.max_tokens <= 0:
            return _fail("max_tokens must be a positive integer", exit_code=EXIT_CONTENT)
        params["max_tokens"] = arguments.max_tokens
    if arguments.top_p is not None:
        if not 0.0 < arguments.top_p <= 1.0:
            return _fail("top_p must be within 0.0 and 1.0", exit_code=EXIT_CONTENT)
        params["top_p"] = arguments.top_p

    alias = arguments.endpoint_alias.strip()
    if not alias or "/" in alias or ":" in alias:
        return _fail(
            "endpoint_alias must be a plain alias such as model.main, "
            "not an address",
            exit_code=EXIT_CONTENT,
        )
    variable = config.alias_env_var(alias)

    try:
        org_id = config.org_id()
        site_id = config.site_id()
        created_by = config.prompt_created_by(required=True)
        database_url = config.database_url(required=True)
    except config.ConfigError as exc:
        return _fail(exc.message, exit_code=EXIT_OPERATIONAL)
    assert created_by is not None and database_url is not None

    try:
        engine = engine_from_env(required=True)
        assert engine is not None
        assert_migrated(engine)
    except DatabaseUnavailable as exc:
        return _fail(exc.message, exit_code=EXIT_OPERATIONAL)

    init_engine(database_url)
    session = get_session_factory()()
    try:
        existing = session.execute(
            select(AiModelProfile).where(
                AiModelProfile.org_id == org_id,
                AiModelProfile.name == arguments.name,
                AiModelProfile.deleted_at.is_(None),
            )
        ).scalar_one_or_none()
        action = "update" if existing is not None else "insert"
        if existing is None:
            existing = AiModelProfile(
                org_id=org_id,
                site_id=site_id,
                created_by=created_by,
                name=arguments.name,
                endpoint_alias=alias,
                params=params,
            )
            session.add(existing)
        else:
            existing.endpoint_alias = alias
            existing.params = params
            existing.created_by = created_by
        if arguments.dry_run:
            session.rollback()
            print(
                f"model-profile: dry run, would {action} name={arguments.name} "
                f"alias={alias}; nothing was written"
            )
            print("exit-code 0")
            return EXIT_OK
        session.commit()
        print(
            f"model-profile: {action} name={arguments.name} alias={alias} "
            f"params={json.dumps(params, sort_keys=True)} "
            f"address_from_env={variable}"
        )
        print("exit-code 0")
        return EXIT_OK
    except Exception as exc:  # pragma: no cover - operational safety net
        session.rollback()
        return _fail(
            f"could not write the model profile: {type(exc).__name__}: {exc}",
            exit_code=EXIT_OPERATIONAL,
        )
    finally:
        session.close()
        engine.dispose()
        reset_engine()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
