"""Give the runtime role the same search_path as `postgres`.

On Supabase PostGIS lives in the `extensions` schema and `postgres` has a per-role
`search_path = "$user", public, extensions`. A new role inherits the server default
instead, so unqualified `geography`, `ST_*` and PostGIS casts used by the API and
the Worker failed for `urmind_runtime` (caught by the runtime privilege test before
any traffic used the role). Only the session default changes; no privilege does.
"""

from alembic import op

revision = "0034_runtime_search_path"
down_revision = "0033_runtime_least_privilege"
branch_labels = None
depends_on = None

ROLE = "urmind_runtime"


def upgrade() -> None:
    op.execute(f"""alter role {ROLE} set search_path = "$user", public, extensions""")


def downgrade() -> None:
    op.execute(f"alter role {ROLE} reset search_path")
