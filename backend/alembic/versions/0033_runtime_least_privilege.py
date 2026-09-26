"""Least-privilege role for the API and the Worker.

The runtime authenticated as `postgres` (CREATEROLE, CREATEDB, BYPASSRLS). This
revision creates `urmind_runtime` without any of those attributes, grants only the
DML each table needs and adds an explicit RLS policy per table, following the
conditional pattern already used by 0025-0030 (which ran before the role existed).

On Supabase `postgres` is not a superuser: CREATE ROLE may state the NO* attributes,
but ALTER ROLE may not even mention SUPERUSER/REPLICATION/BYPASSRLS. So attributes are
set only at creation and a pre-existing role is verified instead of altered: if it
carries any elevated attribute the migration aborts rather than grant to it.

Migrations, model registration/promotion and maintenance keep the admin identity
(MIGRATION_DATABASE_URL, `Database(role="admin")`). The role is created without a
password; `python -m app.db.migrate runtime-password` sets a SCRAM verifier computed
locally, so no plaintext ever reaches SQL or server logs.
"""

from alembic import op

revision = "0033_runtime_least_privilege"
down_revision = "0032_demo_events_private"
branch_labels = None
depends_on = None

ROLE = "urmind_runtime"

# Privileges follow the writes the application really performs (repositories,
# services, worker, OSM importer). Tables absent here get nothing.
TABLE_PRIVILEGES: dict[str, str] = {
    "alembic_version": "select",
    "model_versions": "select",
    "dataset_versions": "select",
    "actions_catalog": "select",
    "responsibility_rules": "select",
    "operational_territory": "select",
    "missions": "select",
    "devices": "select",
    "sensor_assets": "select",
    # Append-only evidence: no update/delete from the runtime.
    "audit_log": "select, insert",
    "capture_privacy_consents": "select, insert",
    "captures": "select, insert, update",
    "detections": "select, insert, update",
    "events": "select, insert, update",
    "event_context": "select, insert, update",
    "risk_assessments": "select, insert, update",
    "reviews": "select, insert, update",
    "predictions": "select, insert, update",
    "road_segments": "select, insert, update",
    "operational_configuration": "select, insert, update",
    # Expiring leases, caches and quota ledger delete their own stale rows.
    "public_image_admissions": "select, insert, delete",
    "photo_admission_leases": "select, insert, update, delete",
    "geocoding_cache": "select, insert, update, delete",
    "geocoding_leases": "select, insert, update, delete",
}
QUEUE_TABLES = ("pgmq.q_inference_jobs", "pgmq.a_inference_jobs")
QUEUE_FUNCTIONS = ("send", "read", "archive", "metrics")
# Policies that 0025-0030 create for this role when it already exists.
CONDITIONAL_POLICIES = (
    ("operational_runtime_role", "operational_configuration"),
    ("photo_admission_runtime", "photo_admission_leases"),
    ("consent_runtime", "capture_privacy_consents"),
    ("geocoding_runtime", "geocoding_cache"),
    ("geocoding_runtime", "geocoding_leases"),
    ("territory_runtime", "operational_territory"),
)


def upgrade() -> None:
    op.execute(f"""do $$ begin
        if not exists (select 1 from pg_roles where rolname = '{ROLE}') then
            create role {ROLE} login noinherit nosuperuser nocreatedb nocreaterole
                noreplication nobypassrls;
        end if;
        if exists (
            select 1 from pg_roles where rolname = '{ROLE}' and (
                rolsuper or rolcreatedb or rolcreaterole or rolreplication or rolbypassrls
                or not rolcanlogin
            )
        ) then
            raise exception '{ROLE} exists with elevated or missing attributes; refusing to grant';
        end if;
        if exists (
            select 1 from pg_auth_members m join pg_roles r on r.oid = m.roleid
            where m.member = (select oid from pg_roles where rolname = '{ROLE}')
        ) then
            raise exception '{ROLE} is a member of another role; refusing to grant';
        end if;
    end $$""")
    # Session default only; changing it needs ADMIN on the role, which its creator holds.
    op.execute(f"""do $$ begin
        execute 'alter role {ROLE} set statement_timeout = ''60s''';
    exception when insufficient_privilege then
        raise notice 'statement_timeout default not set for {ROLE}';
    end $$""")
    op.execute(f"grant usage on schema public, extensions, pgmq to {ROLE}")
    for table, privileges in TABLE_PRIVILEGES.items():
        op.execute(f"grant {privileges} on public.{table} to {ROLE}")
        command = "select" if privileges == "select" else "all"
        check = "" if command == "select" else " with check (true)"
        op.execute(f"drop policy if exists urmind_runtime_access on public.{table}")
        op.execute(f"""create policy urmind_runtime_access on public.{table}
            for {command} to {ROLE} using (true){check}""")
    op.execute(f"grant usage, select on all sequences in schema public to {ROLE}")
    # Public functions have EXECUTE revoked from PUBLIC; the app calls only this one.
    # Trigger functions are not checked at fire time.
    op.execute(
        f"grant execute on function public.snap_to_road(geography, double precision) to {ROLE}"
    )
    for table in QUEUE_TABLES:
        op.execute(f"grant select, insert, update, delete on {table} to {ROLE}")
    op.execute(f"grant select on pgmq.meta to {ROLE}")
    op.execute(f"grant usage, select on all sequences in schema pgmq to {ROLE}")
    names = ", ".join(f"'{name}'" for name in QUEUE_FUNCTIONS)
    op.execute(f"""do $$ declare fn regprocedure; begin
        for fn in select p.oid::regprocedure from pg_proc p
            join pg_namespace n on n.oid = p.pronamespace
            where n.nspname = 'pgmq' and p.proname in ({names})
        loop
            execute format('grant execute on function %s to {ROLE}', fn);
        end loop;
    end $$""")


def downgrade() -> None:
    # Explicit, dependency-by-dependency removal: `drop owned by` needs the privileges
    # of the role, which the non-superuser `postgres` does not hold on Supabase.
    for table in TABLE_PRIVILEGES:
        op.execute(f"drop policy if exists urmind_runtime_access on public.{table}")
    for policy, table in CONDITIONAL_POLICIES:
        op.execute(f"drop policy if exists {policy} on public.{table}")
    names = ", ".join(f"'{name}'" for name in QUEUE_FUNCTIONS)
    op.execute(f"""do $$ declare fn regprocedure; begin
        if not exists (select 1 from pg_roles where rolname = '{ROLE}') then return; end if;
        for fn in select p.oid::regprocedure from pg_proc p
            join pg_namespace n on n.oid = p.pronamespace
            where n.nspname = 'pgmq' and p.proname in ({names})
        loop
            execute format('revoke execute on function %s from {ROLE}', fn);
        end loop;
        revoke execute on function public.snap_to_road(geography, double precision)
            from {ROLE};
        revoke all on all tables in schema public from {ROLE};
        revoke all on all sequences in schema public from {ROLE};
        revoke all on all tables in schema pgmq from {ROLE};
        revoke all on all sequences in schema pgmq from {ROLE};
        revoke usage on schema public, extensions, pgmq from {ROLE};
        drop role {ROLE};
    end $$""")
