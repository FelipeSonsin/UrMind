"""Allow authenticated users to reach the narrowly published Realtime tables.

Revision ID: 0009_authenticated_schema_usage
Revises: 0008_sidewalk_not_municipal
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009_authenticated_schema_usage"
down_revision: str | None = "0008_sidewalk_not_municipal"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("revoke usage on schema public from anon")
    op.execute("grant usage on schema public to authenticated")


def downgrade() -> None:
    raise RuntimeError("0009 is forward-only: revoking USAGE would break authenticated Realtime")
