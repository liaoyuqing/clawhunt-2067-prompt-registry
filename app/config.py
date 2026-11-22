"""Runtime configuration.

Every value is read from the process environment at the moment it is needed.
No credential, host name, connection string or endpoint appears in this
repository; the mapping from a model alias to a real endpoint exists only as
an environment variable (see README, section Environment variables).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from uuid import UUID

ENV_DATABASE_URL = "DATABASE_URL"
ENV_PROMPT_CREATED_BY = "PROMPT_CREATED_BY"
ENV_ORG_ID = "EVAL_ORG_ID"
ENV_SITE_ID = "EVAL_SITE_ID"
ENV_MODEL_ALIAS_PREFIX = "MODEL_ALIAS_"
ENV_DEFAULT_MODEL_PROFILE = "EVAL_MODEL_PROFILE"

# Deployments that serve a single tenant do not have to set an org id; the nil
# UUID is used and printed by every command that relies on it, so a wrong
# default is never silent.
DEFAULT_ORG_ID = UUID("00000000-0000-0000-0000-000000000000")
DEFAULT_SITE_ID = UUID("00000000-0000-0000-0000-000000000000")


class ConfigError(RuntimeError):
    """A required environment variable is missing or malformed."""

    def __init__(self, message: str, variable: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.variable = variable


def _read(name: str) -> str | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    value = raw.strip()
    return value or None


def database_url(*, required: bool = True) -> str | None:
    """Return DATABASE_URL, or raise ConfigError naming the missing variable."""
    value = _read(ENV_DATABASE_URL)
    if value is not None:
        return value
    if not required:
        return None
    raise ConfigError(
        f"environment variable {ENV_DATABASE_URL} is not set; "
        "point it at the application database: a PostgreSQL DSN in production, "
        "or a sqlite file DSN for local development",
        variable=ENV_DATABASE_URL,
    )


def prompt_created_by(*, required: bool = True) -> str | None:
    value = _read(ENV_PROMPT_CREATED_BY)
    if value is not None:
        return value
    if not required:
        return None
    raise ConfigError(
        f"environment variable {ENV_PROMPT_CREATED_BY} is not set; "
        "it is written to created_by on every registered row and keeps the audit trail",
        variable=ENV_PROMPT_CREATED_BY,
    )


def parse_uuid(raw: str, *, variable: str) -> UUID:
    try:
        return UUID(raw)
    except (ValueError, AttributeError, TypeError) as exc:
        raise ConfigError(
            f"{variable} must be a UUID, got {raw!r}",
            variable=variable,
        ) from exc


def org_id() -> UUID:
    raw = _read(ENV_ORG_ID)
    if raw is None:
        return DEFAULT_ORG_ID
    return parse_uuid(raw, variable=ENV_ORG_ID)


def site_id() -> UUID:
    raw = _read(ENV_SITE_ID)
    if raw is None:
        return DEFAULT_SITE_ID
    return parse_uuid(raw, variable=ENV_SITE_ID)


def org_id_source() -> str:
    """Provenance of the effective org id, printed by the CLI."""
    return ENV_ORG_ID if _read(ENV_ORG_ID) is not None else "default (EVAL_ORG_ID unset)"


def alias_env_var(endpoint_alias: str) -> str:
    """Map a model alias such as model.main to MODEL_ALIAS_MODEL_MAIN."""
    suffix = re.sub(r"[^0-9A-Za-z]+", "_", endpoint_alias or "").strip("_").upper()
    if not suffix:
        raise ConfigError(
            "model endpoint alias is empty; an alias such as model.main is required",
        )
    return ENV_MODEL_ALIAS_PREFIX + suffix


def resolve_model_endpoint(endpoint_alias: str) -> str:
    """Resolve an alias to its real endpoint through the environment only."""
    variable = alias_env_var(endpoint_alias)
    value = _read(variable)
    if value is None:
        raise ConfigError(
            f"model endpoint alias {endpoint_alias!r} has no target configured: "
            f"set {variable} to the real base address for this deployment",
            variable=variable,
        )
    return value


def default_model_profile() -> str:
    value = _read(ENV_DEFAULT_MODEL_PROFILE)
    if value is None:
        raise ConfigError(
            f"no model profile selected: pass one explicitly or set {ENV_DEFAULT_MODEL_PROFILE}",
            variable=ENV_DEFAULT_MODEL_PROFILE,
        )
    return value


@dataclass(frozen=True)
class Settings:
    """Snapshot of the configuration an application instance was built with."""

    database_url: str
    org_id: UUID
    site_id: UUID

    @classmethod
    def load(cls) -> "Settings":
        url = database_url(required=True)
        assert url is not None
        return cls(database_url=url, org_id=org_id(), site_id=site_id())
