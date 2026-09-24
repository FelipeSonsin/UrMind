"""Close provider event-trigger RPC access and index canonical foreign keys.

Revision ID: 0011_security_indexes
Revises: 0010_detection_model_version

The RLS event trigger remains installed; only direct client EXECUTE is revoked.
Indexes cover FK delete checks and the expected joins without changing data.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0011_security_indexes"
down_revision: str | None = "0010_detection_model_version"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("revoke all on function public.rls_auto_enable() from public, anon, authenticated")
    indexes = {
        "captures_device_idx": ("captures", "device_id"),
        "detections_model_version_idx": ("detections", "model_version_id"),
        "events_capture_idx": ("events", "capture_id"),
        "events_mission_idx": ("events", "mission_id"),
        "events_model_version_idx": ("events", "model_version_id"),
        "missions_device_idx": ("missions", "device_id"),
        "model_versions_dataset_idx": ("model_versions", "dataset_version_id"),
        "predictions_model_version_idx": ("predictions", "model_version_id"),
        "predictions_road_segment_idx": ("predictions", "road_segment_id"),
        "risk_action_idx": ("risk_assessments", "action_id"),
        "risk_model_version_idx": ("risk_assessments", "model_version_id"),
        "risk_responsibility_idx": ("risk_assessments", "responsibility_rule_id"),
        "sensor_assets_capture_idx": ("sensor_assets", "capture_id"),
    }
    for name, (table, column) in indexes.items():
        op.execute(f"create index if not exists {name} on public.{table} ({column})")


def downgrade() -> None:
    raise RuntimeError(
        "0011 is forward-only: restore privileges or indexes via a reviewed migration"
    )
