"""Remove client access from the demo-only table.

0031 was applied to Urmind DEV with DEMO_MODE=1, which granted SELECT to anon and
authenticated and created a public read policy over synthetic coordinates. The demo
panel reads through its own server-side connection, so no client role needs the
table. After this revision a DEV database and a clean upgrade reach the same state:
the table exists, RLS is on and only service_role has privileges.
"""

import os

from alembic import op

revision = "0032_demo_events_private"
down_revision = "0031_demo_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("drop policy if exists demo_events_public_read on public.demo_events")
    op.execute("revoke all on public.demo_events from public, anon, authenticated")


def downgrade() -> None:
    # Restores exactly what 0031 left: client read only when it ran with DEMO_MODE=1.
    if os.getenv("DEMO_MODE") != "1":
        return
    op.execute("grant select on public.demo_events to anon, authenticated")
    op.execute("""create policy demo_events_public_read on public.demo_events
        for select to anon, authenticated using (true)""")
