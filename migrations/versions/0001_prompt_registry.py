"""prompt version registry and evaluation tables

Creates the four tables this delivery is responsible for, in the PostgreSQL 16
flavour used in production:

* ai_prompt_version  - immutable prompt revisions, unique per (org, tool, key, version)
* ai_model_profile   - alias-only model sampling profiles
* eval_run           - one evaluation execution
* eval_result        - one case result, pinned to the prompt version that produced it

Revision ID: 0001_prompt_registry
Revises:
Create Date: 2026-11-22
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_prompt_registry"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_prompt_version",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("org_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("site_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tool", sa.String(length=32), nullable=False),
        sa.Column("prompt_key", sa.String(length=64), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("version_major", sa.Integer(), nullable=False),
        sa.Column("version_minor", sa.Integer(), nullable=False),
        sa.Column("version_patch", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("file_path", sa.String(length=512), nullable=False),
        sa.Column("changelog", sa.Text(), nullable=True),
        sa.UniqueConstraint(
            "org_id", "tool", "prompt_key", "version", name="uq_ai_prompt_version_scope"
        ),
    )
    op.create_index("ix_ai_prompt_version_org_id", "ai_prompt_version", ["org_id"])
    op.create_index(
        "ix_ai_prompt_version_lookup",
        "ai_prompt_version",
        ["org_id", "tool", "prompt_key"],
    )
    op.create_index(
        "ix_ai_prompt_version_sha",
        "ai_prompt_version",
        ["org_id", "content_sha256"],
    )

    op.create_table(
        "ai_model_profile",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("org_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("site_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("endpoint_alias", sa.String(length=64), nullable=False),
        sa.Column(
            "params",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.UniqueConstraint("org_id", "name", name="uq_ai_model_profile_name"),
    )
    op.create_index("ix_ai_model_profile_org_id", "ai_model_profile", ["org_id"])

    op.create_table(
        "eval_run",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("org_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("site_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tool", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_cases", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("passed_cases", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_eval_run_org_id", "eval_run", ["org_id"])
    op.create_index("ix_eval_run_tool", "eval_run", ["org_id", "tool", "status"])

    op.create_table(
        "eval_result",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("org_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("site_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("run_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("prompt_version_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("model_profile_name", sa.String(length=64), nullable=True),
        sa.Column("case_id", sa.String(length=64), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["eval_run.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["prompt_version_id"], ["ai_prompt_version.id"], ondelete="RESTRICT"
        ),
    )
    op.create_index("ix_eval_result_org_id", "eval_result", ["org_id"])
    op.create_index("ix_eval_result_run_id", "eval_result", ["run_id"])
    op.create_index("ix_eval_result_prompt_version_id", "eval_result", ["prompt_version_id"])
    op.create_index(
        "ix_eval_result_run_version", "eval_result", ["run_id", "prompt_version_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_eval_result_run_version", table_name="eval_result")
    op.drop_index("ix_eval_result_prompt_version_id", table_name="eval_result")
    op.drop_index("ix_eval_result_run_id", table_name="eval_result")
    op.drop_index("ix_eval_result_org_id", table_name="eval_result")
    op.drop_table("eval_result")

    op.drop_index("ix_eval_run_tool", table_name="eval_run")
    op.drop_index("ix_eval_run_org_id", table_name="eval_run")
    op.drop_table("eval_run")

    op.drop_index("ix_ai_model_profile_org_id", table_name="ai_model_profile")
    op.drop_table("ai_model_profile")

    op.drop_index("ix_ai_prompt_version_sha", table_name="ai_prompt_version")
    op.drop_index("ix_ai_prompt_version_lookup", table_name="ai_prompt_version")
    op.drop_index("ix_ai_prompt_version_org_id", table_name="ai_prompt_version")
    op.drop_table("ai_prompt_version")
