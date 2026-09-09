"""núcleo canônico do UrMind — captura, detecção, evento e geoespacial

O DDL vive aqui por inteiro e é imutável: uma revisão aplicada é registro
histórico, nunca é editada. Correções entram como a próxima revisão
(MASTER_PLAN §18.2).

Este esquema não é fruto de autogenerate. Ele cria extensões, funções PL/pgSQL,
geografias, índices GiST, RLS e grants — coisas que o autogenerate do Alembic
não descreve corretamente, e por isso foi escrito e revisado à mão [R43].

Revision ID: 0001_core_geospatial
Revises:
Create Date: 2026-09-05
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001_core_geospatial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


CORE_SCHEMA = r"""
-- URMIND 0001 — núcleo canônico (MASTER_PLAN §5, §11 e §29)
-- Esquema canônico do pipeline: missão → dispositivo → captura → detecção →
-- evento georreferenciado → contexto / risco / ação / previsão / revisão.
-- Toda saída guarda model_version e dataset_version para rastreabilidade.

create extension if not exists postgis;
create extension if not exists pgcrypto;

-- ---------------------------------------------------------------- utilitários

create or replace function public.set_updated_at()
returns trigger language plpgsql as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

-- ---------------------------------------------------------------- auditoria

-- AuditLog (§5): histórico de alterações relevantes em qualquer entidade do
-- núcleo. Não é log de debug; cada linha guarda antes/depois e um hash do
-- conteúdo para detectar adulteração silenciosa.
create table if not exists public.audit_log (
    id uuid primary key default gen_random_uuid(),
    operation text not null,
    entity_type text not null,
    entity_id uuid not null,
    actor text,
    before_data jsonb,
    after_data jsonb,
    event_hash char(64) not null,
    created_at timestamptz not null default now()
);

create index if not exists audit_log_entity_idx
    on public.audit_log (entity_type, entity_id, created_at desc);

-- ---------------------------------------------------------------- catálogos

create table if not exists public.dataset_versions (
    id uuid primary key default gen_random_uuid(),
    name text not null,
    version text not null,
    source text not null,
    license text,
    classes text[] not null default '{}',
    split jsonb not null default '{}'::jsonb,
    dvc_revision text,
    created_at timestamptz not null default now(),
    unique (name, version)
);

create table if not exists public.model_versions (
    id uuid primary key default gen_random_uuid(),
    name text not null,
    kind text not null check (kind in ('vision','imu','audio','fusion','risk','prediction')),
    version text not null,
    checksum text,
    dataset_version_id uuid references public.dataset_versions(id) on delete set null,
    metrics jsonb not null default '{}'::jsonb,
    promoted_at timestamptz,
    created_at timestamptz not null default now(),
    unique (name, version)
);

create table if not exists public.actions_catalog (
    id uuid primary key default gen_random_uuid(),
    code text not null unique,
    label text not null,
    description text,
    version text not null default 'v1',
    active boolean not null default true,
    created_at timestamptz not null default now()
);

create table if not exists public.responsibility_rules (
    id uuid primary key default gen_random_uuid(),
    jurisdiction text not null,
    asset_type text not null,
    urmind_class text not null,
    responsible text not null,
    source text not null,
    version text not null default 'v1',
    active boolean not null default true,
    created_at timestamptz not null default now(),
    unique (jurisdiction, asset_type, urmind_class, version)
);

-- ---------------------------------------------------------------- coleta

create table if not exists public.devices (
    id uuid primary key default gen_random_uuid(),
    code text not null unique,
    kind text not null check (kind in ('scout','esp32_cam','esp32_devkit','phone','gateway')),
    firmware_version text,
    calibration jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create table if not exists public.missions (
    id uuid primary key default gen_random_uuid(),
    code text not null unique,
    device_id uuid references public.devices(id) on delete set null,
    operator text,
    status text not null default 'planned'
        check (status in ('planned','running','finished','aborted')),
    started_at timestamptz,
    finished_at timestamptz,
    route geography(LineString, 4326),
    created_at timestamptz not null default now()
);

-- Rede viária importada do OSM (apenas a área piloto).
create table if not exists public.road_segments (
    id uuid primary key default gen_random_uuid(),
    osm_id bigint unique,
    name text,
    highway text,
    jurisdiction text,
    geom geometry(LineString, 4326) not null,
    geog geography(LineString, 4326) generated always as (geom::geography) stored,
    attributes jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create table if not exists public.captures (
    id uuid primary key default gen_random_uuid(),
    capture_key text not null unique,
    mission_id uuid references public.missions(id) on delete set null,
    device_id uuid references public.devices(id) on delete set null,
    source text not null check (source in ('scout','pwa_photo','exif_upload','import')),
    source_location text not null
        check (source_location in ('gps_scout','gps_device','exif','manual','imported','unknown')),
    captured_at timestamptz not null,
    storage_path text,
    -- coordenada original preservada; o snap nunca a sobrescreve.
    point geography(Point, 4326),
    accuracy_m double precision check (accuracy_m >= 0),
    heading_deg double precision check (heading_deg >= 0 and heading_deg <= 360),
    speed_mps double precision check (speed_mps >= 0),
    quality jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    check (point is null or source_location <> 'unknown')
);

create table if not exists public.sensor_assets (
    id uuid primary key default gen_random_uuid(),
    mission_id uuid references public.missions(id) on delete set null,
    capture_id uuid references public.captures(id) on delete cascade,
    modality text not null check (modality in ('imu','audio','gps','video')),
    storage_path text not null,
    format text not null,
    window_start timestamptz not null,
    window_end timestamptz not null,
    sync_quality double precision check (sync_quality >= 0 and sync_quality <= 1),
    created_at timestamptz not null default now(),
    check (window_end >= window_start)
);

create table if not exists public.detections (
    id uuid primary key default gen_random_uuid(),
    capture_id uuid not null references public.captures(id) on delete cascade,
    urmind_class text not null,
    confidence double precision not null check (confidence >= 0 and confidence <= 1),
    bbox jsonb not null,
    model_version_id uuid references public.model_versions(id) on delete set null,
    created_at timestamptz not null default now()
);

-- ---------------------------------------------------------------- evento

create table if not exists public.events (
    id uuid primary key default gen_random_uuid(),
    event_key text not null unique,
    capture_id uuid references public.captures(id) on delete set null,
    mission_id uuid references public.missions(id) on delete set null,
    urmind_class text not null,
    evidence_mode text not null
        check (evidence_mode in ('scout_full','photo','exif_photo','image_only','sensor_only')),
    visual_confidence double precision check (visual_confidence >= 0 and visual_confidence <= 1),
    fused_confidence double precision check (fused_confidence >= 0 and fused_confidence <= 1),
    status text not null default 'detected'
        check (status in ('detected','review','confirmed','rejected','triage_required')),
    occurred_at timestamptz not null,
    point geography(Point, 4326),
    snapped_point geography(Point, 4326),
    road_segment_id uuid references public.road_segments(id) on delete set null,
    distance_to_road_m double precision check (distance_to_road_m >= 0),
    location_accuracy_m double precision check (location_accuracy_m >= 0),
    factors jsonb not null default '{}'::jsonb,
    model_version_id uuid references public.model_versions(id) on delete set null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists public.event_context (
    id uuid primary key default gen_random_uuid(),
    event_id uuid not null references public.events(id) on delete cascade,
    source text not null,
    payload jsonb not null default '{}'::jsonb,
    fetched_at timestamptz not null default now(),
    unique (event_id, source)
);

create table if not exists public.risk_assessments (
    id uuid primary key default gen_random_uuid(),
    event_id uuid not null references public.events(id) on delete cascade,
    severity text not null check (severity in ('low','medium','high','critical','unknown')),
    priority_score double precision check (priority_score >= 0 and priority_score <= 1),
    uncertainty double precision check (uncertainty >= 0 and uncertainty <= 1),
    factors jsonb not null default '{}'::jsonb,
    responsibility_rule_id uuid references public.responsibility_rules(id) on delete set null,
    action_id uuid references public.actions_catalog(id) on delete set null,
    model_version_id uuid references public.model_versions(id) on delete set null,
    created_at timestamptz not null default now()
);

create table if not exists public.predictions (
    id uuid primary key default gen_random_uuid(),
    event_id uuid references public.events(id) on delete cascade,
    road_segment_id uuid references public.road_segments(id) on delete cascade,
    task text not null check (task in ('recurrence','segment_risk','hotspot','evolution','scenario')),
    horizon_days integer check (horizon_days > 0),
    value double precision not null,
    uncertainty double precision check (uncertainty >= 0 and uncertainty <= 1),
    model_version_id uuid references public.model_versions(id) on delete set null,
    created_at timestamptz not null default now(),
    check (event_id is not null or road_segment_id is not null)
);

create table if not exists public.reviews (
    id uuid primary key default gen_random_uuid(),
    event_id uuid not null references public.events(id) on delete cascade,
    reviewer text not null,
    decision text not null check (decision in ('confirm','correct','reject')),
    corrected_class text,
    corrected_point geography(Point, 4326),
    notes text,
    created_at timestamptz not null default now()
);

-- ---------------------------------------------------------------- índices

create index if not exists road_segments_geom_gix on public.road_segments using gist (geom);
create index if not exists road_segments_geog_gix on public.road_segments using gist (geog);
create index if not exists captures_point_gix on public.captures using gist (point);
create index if not exists captures_captured_at_idx on public.captures (captured_at desc);
create index if not exists captures_mission_idx on public.captures (mission_id, captured_at desc);
create index if not exists sensor_assets_window_idx on public.sensor_assets (mission_id, window_start);
create index if not exists detections_capture_idx on public.detections (capture_id);
create index if not exists events_point_gix on public.events using gist (point);
create index if not exists events_snapped_gix on public.events using gist (snapped_point);
create index if not exists events_status_idx on public.events (status, occurred_at desc);
create index if not exists events_class_idx on public.events (urmind_class, occurred_at desc);
create index if not exists events_road_segment_idx on public.events (road_segment_id);
create index if not exists risk_event_idx on public.risk_assessments (event_id, created_at desc);
create index if not exists predictions_event_idx on public.predictions (event_id, created_at desc);
create index if not exists reviews_event_idx on public.reviews (event_id, created_at desc);
create index if not exists missions_route_gix on public.missions using gist (route);

drop trigger if exists events_set_updated_at on public.events;
create trigger events_set_updated_at
before update on public.events
for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------- geoespacial

-- Snap do ponto ao trecho viário mais próximo (§11.2): devolve o segmento,
-- a distância em metros e a posição equivalente sobre a LineString.
create or replace function public.snap_to_road(
    p_point geography,
    p_max_distance_m double precision default 50
) returns table (
    road_segment_id uuid,
    distance_m double precision,
    snapped_point geography
)
language sql stable set search_path = public as $fn$
    select rs.id,
           st_distance(p_point, rs.geog),
           st_closestpoint(rs.geom, p_point::geometry)::geography
    from public.road_segments rs
    where st_dwithin(p_point, rs.geog, p_max_distance_m)
    order by p_point::geometry <-> rs.geom
    limit 1;
$fn$;

-- Eventos dentro de um raio, do mais próximo ao mais distante.
create or replace function public.events_near(
    p_lat double precision,
    p_lon double precision,
    p_radius_m double precision default 500,
    p_limit integer default 100
) returns setof public.events
language sql stable set search_path = public as $fn$
    select e.*
    from public.events e
    where e.point is not null
      and st_dwithin(e.point, st_point(p_lon, p_lat, 4326)::geography, p_radius_m)
    order by st_distance(e.point, st_point(p_lon, p_lat, 4326)::geography)
    limit p_limit;
$fn$;

-- ---------------------------------------------------------------- RLS

alter table public.audit_log enable row level security;
alter table public.dataset_versions enable row level security;
alter table public.model_versions enable row level security;
alter table public.actions_catalog enable row level security;
alter table public.responsibility_rules enable row level security;
alter table public.devices enable row level security;
alter table public.missions enable row level security;
alter table public.road_segments enable row level security;
alter table public.captures enable row level security;
alter table public.sensor_assets enable row level security;
alter table public.detections enable row level security;
alter table public.events enable row level security;
alter table public.event_context enable row level security;
alter table public.risk_assessments enable row level security;
alter table public.predictions enable row level security;
alter table public.reviews enable row level security;

revoke all on public.audit_log, public.dataset_versions, public.model_versions, public.actions_catalog,
    public.responsibility_rules, public.devices, public.missions, public.road_segments,
    public.captures, public.sensor_assets, public.detections, public.events,
    public.event_context, public.risk_assessments, public.predictions, public.reviews
    from anon, authenticated;

grant select, insert, update, delete on public.audit_log, public.dataset_versions, public.model_versions,
    public.actions_catalog, public.responsibility_rules, public.devices, public.missions,
    public.road_segments, public.captures, public.sensor_assets, public.detections,
    public.events, public.event_context, public.risk_assessments, public.predictions,
    public.reviews to service_role;

revoke all on function public.snap_to_road(geography, double precision)
    from public, anon, authenticated;
revoke all on function public.events_near(double precision, double precision, double precision, integer)
    from public, anon, authenticated;
grant execute on function public.snap_to_road(geography, double precision) to service_role;
grant execute on function public.events_near(double precision, double precision, double precision, integer)
    to service_role;
"""

# Ordem inversa da criação: dependentes primeiro, catálogos por último.
DROP_ORDER = (
    "reviews",
    "predictions",
    "risk_assessments",
    "event_context",
    "events",
    "detections",
    "sensor_assets",
    "captures",
    "road_segments",
    "missions",
    "devices",
    "responsibility_rules",
    "actions_catalog",
    "model_versions",
    "dataset_versions",
    "audit_log",
)


def _run(sql: str) -> None:
    """Emite SQL bruto, online ou em modo --sql.

    Não passa por `op.execute()`/`text()` de propósito: o DDL tem casts `::` e
    corpos de função entre `$$`, que o SQLAlchemy leria como bind parameters.
    """
    context = op.get_context()
    if context.as_sql:
        context.impl.static_output(sql)
    else:
        op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run(CORE_SCHEMA)


def downgrade() -> None:
    _run(
        "drop function if exists public.events_near("
        "double precision, double precision, double precision, integer);"
    )
    _run("drop function if exists public.snap_to_road(geography, double precision);")
    for table in DROP_ORDER:
        _run(f"drop table if exists public.{table} cascade;")
    _run("drop function if exists public.set_updated_at();")
    # postgis e pgcrypto não são removidos: outros objetos do projeto dependem
    # deles e derrubar uma extensão inteira num downgrade é destrutivo demais.
