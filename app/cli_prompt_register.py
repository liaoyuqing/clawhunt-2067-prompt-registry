"""prompt-register: register prompt files, or fail loudly.

Exit codes are the contract with CI:

* 0 - success: files registered, or all of them already registered;
* 1 - content problem: malformed front matter, a version edited in place, a
      rollback, a duplicated version, an empty body: a human must change the
      prompt files;
* 2 - operational problem: no database, unreachable database, unmigrated
      database, missing configuration. Fixing it never means touching prompts.

The batch is all-or-nothing: the whole plan is validated first, then every row
is written inside a single transaction, then that transaction is committed once.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app import config
from app.db import get_session_factory, init_engine, reset_engine
from app.dbcheck import DatabaseUnavailable, assert_migrated, engine_from_env
from app.registry import (
    RegistryError,
    apply_registration,
    format_plan,
    plan_registration,
)

EXIT_OK = 0
EXIT_CONTENT = 1
EXIT_OPERATIONAL = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="prompt-register",
        description="Register prompt files into the prompt version registry.",
    )
    parser.add_argument(
        "--prompt-dir",
        default="prompts",
        help="directory that holds the prompt files (default: prompts)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the plan but never write to the database",
    )
    parser.add_argument(
        "--allow-missing-db",
        action="store_true",
        help="when no database is configured, validate the files and exit 0",
    )
    parser.add_argument(
        "--created-by",
        default=None,
        help="audit identity; defaults to the PROMPT_CREATED_BY environment variable",
    )
    return parser


def _print_plan(plan, prompt_dir: Path) -> None:
    print(format_plan(plan, prompt_dir=prompt_dir))


def _fail(message: str, *, exit_code: int) -> int:
    print(f"[error] {message}", file=sys.stderr)
    print(f"exit-code {exit_code}", file=sys.stderr)
    return exit_code


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    prompt_dir = Path(arguments.prompt_dir)

    try:
        created_by = arguments.created_by or config.prompt_created_by(required=True)
    except config.ConfigError as exc:
        return _fail(exc.message, exit_code=EXIT_OPERATIONAL)
    try:
        org_id = config.org_id()
        site_id = config.site_id()
    except config.ConfigError as exc:
        return _fail(exc.message, exit_code=EXIT_OPERATIONAL)
    assert created_by is not None

    print(
        f"prompt-register: org_id={org_id} ({config.org_id_source()}) "
        f"created_by={created_by} dry_run={arguments.dry_run}"
    )

    try:
        database_url = config.database_url(required=not arguments.allow_missing_db)
    except config.ConfigError as exc:
        return _fail(exc.message, exit_code=EXIT_OPERATIONAL)

    engine = None
    if database_url is not None:
        try:
            engine = engine_from_env(required=True)
            assert engine is not None
            assert_migrated(engine)
        except DatabaseUnavailable as exc:
            return _fail(exc.message, exit_code=EXIT_OPERATIONAL)

    if engine is None:
        print(
            "prompt-register: no database configured (--allow-missing-db): "
            "validating files only, nothing will be written"
        )
        plan = plan_registration(prompt_dir, org_id=org_id, session=None)
        _print_plan(plan, prompt_dir)
        if not plan.ok:
            for error in plan.errors:
                print(f"[fail] {error.render()}", file=sys.stderr)
            return _fail(
                f"{len(plan.errors)} content error(s); the batch was not registered",
                exit_code=EXIT_CONTENT,
            )
        print(
            f"prompt-register: {len(plan.actions)} file(s) valid "
            "(file-level validation only, no database)"
        )
        print("exit-code 0")
        return EXIT_OK

    assert database_url is not None
    init_engine(database_url)
    session = get_session_factory()()
    try:
        plan = plan_registration(prompt_dir, org_id=org_id, session=session)
        _print_plan(plan, prompt_dir)
        if not plan.ok:
            for error in plan.errors:
                print(f"[fail] {error.render()}", file=sys.stderr)
            session.rollback()
            return _fail(
                f"{len(plan.errors)} content error(s); nothing was written",
                exit_code=EXIT_CONTENT,
            )
        if arguments.dry_run:
            session.rollback()
            print(
                f"prompt-register: dry run, would insert {plan.inserted_count} "
                f"and skip {plan.skipped_count}; nothing was written"
            )
            print("exit-code 0")
            return EXIT_OK
        inserted = apply_registration(
            session, plan, org_id=org_id, site_id=site_id, created_by=created_by
        )
        # The single commit of the batch: either every row lands or none does.
        session.commit()
        print(
            f"prompt-register: inserted {inserted}, skipped {plan.skipped_count}, "
            f"total {len(plan.actions)}"
        )
        print("exit-code 0")
        return EXIT_OK
    except DatabaseUnavailable as exc:
        return _fail(exc.message, exit_code=EXIT_OPERATIONAL)
    except RegistryError as exc:
        print(f"[fail] {exc.render()}", file=sys.stderr)
        return _fail(exc.message, exit_code=EXIT_CONTENT)
    except Exception as exc:  # pragma: no cover - operational safety net
        return _fail(
            f"unexpected failure while writing the registry: {type(exc).__name__}: {exc}",
            exit_code=EXIT_OPERATIONAL,
        )
    finally:
        session.close()
        if engine is not None:
            engine.dispose()
        reset_engine()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
