"""Verify AC4 end-to-end: the database stores a model alias, never an address.

Runs four independent checks against a SQLite database produced by
`python -m app.dbcheck init` + `python -m app.cli_model_profile ...`:

  1. the ai_model_profile column list contains no url/address column;
  2. every string column of every table is scanned for http(s) literals;
  3. the alias resolves only through the environment (MODEL_ALIAS_*);
  4. the same values are visible from the ORM model definition.

Usage:
    python scripts/verify_alias_only.py [path/to/run.sqlite3]
    python scripts/verify_alias_only.py --plant-address path/to/copy.sqlite3

Exit code 0 when all checks pass, 1 otherwise. The model code is imported
from the repository itself, so run this from the repository root.

`--plant-address` copies the default database to the given path and writes an
http address into `ai_model_profile.endpoint_alias` of the copy. Running the
checks against that copy must fail, which is how the scan proves it is really
reading the rows instead of always printing zero.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys

# Import the application from the repository this script ships in, not from the
# directory the script happens to live in.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

FORBIDDEN_COLUMNS = ("url", "base_url", "endpoint_url", "address", "endpoint")
SCHEMES = ("http://", "https://")
DEFAULT_DB = os.path.join("build", "demo", "run.sqlite3")


def _fail(message):
    print("[fail] %s" % message)
    return 1


def check_columns(conn):
    """Return True when ai_model_profile has no column that could hold an address."""
    rows = conn.execute("PRAGMA table_info(ai_model_profile)").fetchall()
    columns = [row[1] for row in rows]
    print("[1] ai_model_profile columns: %s" % ", ".join(columns))
    offenders = [name for name in columns if name.lower() in FORBIDDEN_COLUMNS]
    if offenders:
        return "columns that could hold an address: %s" % ", ".join(offenders)
    print("    no url/base_url/endpoint_url/address column; only endpoint_alias")
    return None


def check_literals(conn):
    """Return True when no table holds an http(s) literal in any string column."""
    tables = [row[0] for row in conn.execute(
        "select name from sqlite_master where type='table' order by name")]
    hits = []
    scanned = 0
    for table in tables:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(%s)" % table)]
        for column in columns:
            for (value,) in conn.execute("select %s from %s" % (column, table)):
                scanned += 1
                if isinstance(value, str) and any(s in value for s in SCHEMES):
                    hits.append("%s.%s=%s" % (table, column, value))
    print("[2] scanned %d value(s) across %d table(s): %s"
          % (scanned, len(tables), ", ".join(tables)))
    print("    http(s) literal hits: %d" % len(hits))
    if hits:
        return "http(s) literals found: %s" % "; ".join(hits[:5])
    return None


def check_environment_only():
    """Return True when the alias maps to an env var and has no built-in default."""
    from app import config

    alias = "model.main"
    env_var = config.alias_env_var(alias)
    print("[3] alias %r -> environment variable %s" % (alias, env_var))
    if env_var.upper().find("HTTP") >= 0 or "://" in env_var:
        return "alias_env_var must return a variable name, got %r" % env_var

    previous = os.environ.pop(env_var, None)
    try:
        try:
            resolved = config.resolve_model_endpoint(alias)
        except config.ConfigError as exc:
            print("    unresolved without %s: ConfigError: %s" % (env_var, exc))
        else:
            return "resolved to %r without %s; there must be no default" % (resolved, env_var)
    finally:
        if previous is not None:
            os.environ[env_var] = previous

    os.environ[env_var] = "https://gateway.example.invalid/v1"
    try:
        resolved = config.resolve_model_endpoint(alias)
    except config.ConfigError as exc:
        return "still unresolvable with %s set: %s" % (env_var, exc)
    finally:
        if previous is not None:
            os.environ[env_var] = previous
        else:
            os.environ.pop(env_var, None)
    print("    resolved from %s -> %s (never stored)" % (env_var, resolved))
    return None


def check_model_definition():
    """Return True when the ORM model has no field that could store an address."""
    from app.models import AiModelProfile

    names = sorted(column.name for column in AiModelProfile.__table__.columns)
    print("[4] ORM columns: %s" % ", ".join(names))
    offenders = [name for name in names if name.lower() in FORBIDDEN_COLUMNS]
    if offenders:
        return "ORM fields that could hold an address: %s" % ", ".join(offenders)
    return None


def plant_address(destination, source=None):
    """Copy the database and write one http address into the copy."""
    source = source or DEFAULT_DB
    if not os.path.exists(source):
        return _fail("database %s does not exist; run app.dbcheck init first" % source)
    parent = os.path.dirname(os.path.abspath(destination))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    shutil.copyfile(source, destination)
    conn = sqlite3.connect(destination)
    try:
        conn.execute("update ai_model_profile set endpoint_alias = ?",
                     ("https://planted.example.invalid/v1",))
        conn.commit()
    finally:
        conn.close()
    print("[plant] copied %s -> %s and wrote an address into ai_model_profile.endpoint_alias"
          % (source, destination))
    print("[plant] running the checks against this copy must fail")
    return 0


def main(argv):
    if len(argv) > 1 and argv[1] == "--plant-address":
        if len(argv) < 3:
            return _fail("--plant-address needs a destination path")
        return plant_address(argv[2])

    path = argv[1] if len(argv) > 1 else DEFAULT_DB
    if not os.path.exists(path):
        return _fail("database %s does not exist; run app.dbcheck init first" % path)

    conn = sqlite3.connect(path)
    try:
        failures = [check_columns(conn), check_literals(conn)]
    finally:
        conn.close()
    failures.append(check_environment_only())
    failures.append(check_model_definition())

    failures = [failure for failure in failures if failure]
    if failures:
        for failure in failures:
            print("[fail] %s" % failure)
        return 1
    print("all AC4 checks passed: only an alias is stored, the address stays in the environment")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
