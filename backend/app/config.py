import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

BACKEND_DIR = Path(__file__).resolve().parents[1]
URMIND_DEV_SHADOW_REF = "impmeitwtusjtwjouggy"
PROJECT_DIR = BACKEND_DIR.parent
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"
ALEMBIC_VERSIONS_DIR = BACKEND_DIR / "alembic" / "versions"

# A suíte de testes nunca herda o .env de desenvolvimento: com ele carregado, os
# testes de integração deixariam de ser pulados e escreveriam no banco real. O
# conftest define ENVIRONMENT=test antes de qualquer import de `app`.
if os.environ.get("ENVIRONMENT") != "test":
    load_dotenv(BACKEND_DIR / ".env")

JWKS_PATH = "/auth/v1/.well-known/jwks.json"

# Dados brutos de dataset (§10.2). O padrão fica em datasets/ dentro do projeto,
# mas URMIND_DATASETS_DIR permite apontar para fora — disco externo ou pasta que
# não seja sincronizada em nuvem. Nenhum código abaixo assume que a pasta existe.
DEFAULT_DATASETS_DIR = PROJECT_DIR / "datasets"


def datasets_dir() -> Path:
    """Raiz de datasets, com override por ambiente. Não cria a pasta."""
    override = os.environ.get("URMIND_DATASETS_DIR")
    return Path(override).expanduser().resolve() if override else DEFAULT_DATASETS_DIR


def datasets_raw_dir() -> Path:
    """Arquivos originais, jamais alterados (datasets/README.md)."""
    return datasets_dir() / "raw"


def datasets_manifests_dir() -> Path:
    """Manifestos derivados. Ficam sempre no projeto, mesmo com raw/ fora dele.

    São arquivos pequenos e são a linhagem do que foi registrado: o §10.2 quer
    exatamente isso versionado no Git, enquanto o dado bruto fica fora dele.
    """
    return DEFAULT_DATASETS_DIR / "manifests"


class Settings(BaseModel):
    # hide_input_in_errors: um ValidationError nunca ecoa o valor recebido, então
    # connection string e chave não vazam em exceção nem em log.
    model_config = ConfigDict(frozen=True, hide_input_in_errors=True)

    app_name: str = "URMIND"
    app_env: str = "development"
    public_capture_markers_enabled: bool = Field(
        default=False, alias="PUBLIC_CAPTURE_MARKERS_ENABLED"
    )
    location_conflict_distance_m: float = Field(
        default=500, gt=0, alias="LOCATION_CONFLICT_DISTANCE_M"
    )
    # Phase 3 is BLOCKED_DATA. Vision execution is fail-closed until a specific
    # ModelVersion is explicitly authorized for shadow use.
    vision_execution_mode: Literal["disabled", "shadow", "production"] = Field(
        default="disabled", alias="VISION_EXECUTION_MODE"
    )
    shadow_model_version_id: UUID | None = Field(default=None, alias="SHADOW_MODEL_VERSION_ID")
    # Directory of a promoted review_confirmed XGBoost run (report/model/calibration/
    # promotion). Unset = DISABLED: rules decide alone and nothing is estimated.
    tabular_model_dir: Path | None = Field(default=None, alias="TABULAR_MODEL_DIR")
    # PostgreSQL/PostGIS do Supabase (SQLAlchemy 2 + psycopg 3) é o único acesso a
    # dados do backend (§4.3): nada de PostgREST com secret key. São conexões com
    # papéis separados e sem fallback entre runtime e migrations:
    #   DATABASE_POOLER_URL    — Session Pooler (Supavisor, 5432): runtime do FastAPI.
    #   MIGRATION_DATABASE_URL — Session Pooler (Supavisor, 5432): Alembic, com prioridade.
    #   DATABASE_URL           — OPCIONAL: conexão direta db.<ref>.supabase.co (exige
    #                            IPv6); só fallback legado do Alembic, nunca do runtime.
    database_url: str | None = Field(default=None, alias="DATABASE_URL", repr=False)
    migration_database_url: str | None = Field(
        default=None, alias="MIGRATION_DATABASE_URL", repr=False
    )
    database_pooler_url: str | None = Field(default=None, alias="DATABASE_POOLER_URL", repr=False)

    # Supabase API/Auth. Ainda não consumidas por nenhum código: Storage e Auth
    # chegam no passo 3 do §25. SUPABASE_URL é a base https://<ref>.supabase.co,
    # sem /rest/v1. A secret key é SERVER ONLY (§17); a publishable pode ir ao
    # cliente. A validação de JWT de usuário deve usar o JWKS, nunca a secret key.
    supabase_url: str | None = Field(default=None, alias="SUPABASE_URL")
    supabase_secret_key: str | None = Field(default=None, alias="SUPABASE_SECRET_KEY", repr=False)
    supabase_publishable_key: str | None = Field(
        default=None, alias="SUPABASE_PUBLISHABLE_KEY", repr=False
    )
    supabase_jwks_url: str | None = Field(default=None, alias="SUPABASE_JWKS_URL")
    # Origens do PWA autorizadas a chamar a API com token (produção, origem distinta).
    # Vazio = sem CORS: o PWA usa a mesma origem (reverse proxy) ou o proxy do Vite.
    cors_allowed_origins: str | None = Field(default=None, alias="CORS_ALLOWED_ORIGINS")
    # PWA compilado servido pela própria API (mesma origem): sem CORS e sem
    # mixed content. Vazio = só API; o PWA fica noutro host com CORS declarado.
    serve_frontend_dir: str | None = Field(default=None, alias="SERVE_FRONTEND_DIR")
    # Fonte real de câmera do Scout. Sem elas, o painel público declara a câmera
    # indisponível — nunca simula vídeo (§21, escopo público).
    scout_stream_url: str | None = Field(default=None, alias="SCOUT_STREAM_URL")
    scout_frame_url: str | None = Field(default=None, alias="SCOUT_FRAME_URL")
    public_stream_max_clients: int = Field(default=4, alias="PUBLIC_STREAM_MAX_CLIENTS")
    # A feira usa somente o projeto DEV conhecido. O limite e provisório e
    # configurável; produção permanece fechada para identidades anônimas.
    public_capture_limit_per_hour: int = Field(
        default=4, alias="PUBLIC_CAPTURE_LIMIT_PER_HOUR", ge=1, le=100
    )
    public_capture_global_limit_per_hour: int = Field(
        default=60, alias="PUBLIC_CAPTURE_GLOBAL_LIMIT_PER_HOUR", ge=1, le=1000
    )
    db_pool_size: int = Field(default=5, alias="DB_POOL_SIZE")
    db_max_overflow: int = Field(default=5, alias="DB_MAX_OVERFLOW")
    db_echo: bool = Field(default=False, alias="DB_ECHO")

    # Integrações externas. Todas têm defaults públicos ou são opcionais:
    # ausência de token nunca impede o backend de iniciar.
    geofabrik_sudeste_pbf_url: str = Field(
        default="https://download.geofabrik.de/south-america/brazil/sudeste-latest.osm.pbf",
        alias="GEOFABRIK_SUDESTE_PBF_URL",
    )
    geofabrik_sudeste_md5_url: str = Field(
        default="https://download.geofabrik.de/south-america/brazil/sudeste-latest.osm.pbf.md5",
        alias="GEOFABRIK_SUDESTE_MD5_URL",
    )
    overpass_api_url: str = Field(
        default="https://overpass-api.de/api/interpreter", alias="OVERPASS_API_URL"
    )
    open_meteo_forecast_url: str = Field(
        default="https://api.open-meteo.com/v1/forecast",
        alias="OPEN_METEO_FORECAST_URL",
    )
    open_meteo_archive_url: str = Field(
        default="https://archive-api.open-meteo.com/v1/archive",
        alias="OPEN_METEO_ARCHIVE_URL",
    )
    nominatim_base_url: str = Field(
        default="https://nominatim.openstreetmap.org",
        alias="NOMINATIM_BASE_URL",
    )
    geosampa_wfs_url: str = Field(
        default="https://wfs.geosampa.prefeitura.sp.gov.br/geoserver/geoportal/wfs",
        alias="GEOSAMPA_WFS_URL",
    )
    geosampa_wms_url: str = Field(
        default="https://wms.geosampa.prefeitura.sp.gov.br/geoserver/geoportal/wms",
        alias="GEOSAMPA_WMS_URL",
    )
    brasil_api_base_url: str = Field(
        default="https://brasilapi.com.br/api",
        alias="BRASIL_API_BASE_URL",
    )
    viacep_base_url: str = Field(
        default="https://viacep.com.br/ws",
        alias="VIACEP_BASE_URL",
    )
    ibge_sidra_base_url: str = Field(
        default="https://apisidra.ibge.gov.br", alias="IBGE_SIDRA_BASE_URL"
    )
    ibge_sidra_municipality_code: str | None = Field(
        default=None,
        alias="IBGE_SIDRA_MUNICIPALITY_CODE",
    )
    ibge_cnefe_2022_base_url: str = Field(
        default=(
            "https://ftp.ibge.gov.br/"
            "Cadastro_Nacional_de_Enderecos_para_Fins_Estatisticos/"
            "Censo_Demografico_2022/"
        ),
        alias="IBGE_CNEFE_2022_BASE_URL",
    )
    hf_token: str | None = Field(default=None, alias="HF_TOKEN", repr=False)
    kaggle_api_token: str | None = Field(default=None, alias="KAGGLE_API_TOKEN", repr=False)
    external_http_timeout_seconds: float = Field(
        default=15.0, alias="URMIND_EXTERNAL_HTTP_TIMEOUT_SECONDS", gt=0, le=300
    )
    external_http_user_agent: str = Field(
        default="UrMind/1.0", alias="URMIND_EXTERNAL_HTTP_USER_AGENT", min_length=1
    )

    @field_validator("ibge_sidra_municipality_code", mode="before")
    @classmethod
    def _sidra_municipality_code(cls, value: object) -> str | None:
        if value is None or not str(value).strip():
            return None
        code = str(value).strip()
        if not re.fullmatch(r"\d{7}", code):
            raise ValueError("IBGE_SIDRA_MUNICIPALITY_CODE deve ter 7 digitos")
        return code

    @field_validator("geosampa_wfs_url", "geosampa_wms_url", mode="before")
    @classmethod
    def _geosampa_https(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        parts = urlsplit(value)
        if parts.scheme == "http" and parts.hostname in {
            "wfs.geosampa.prefeitura.sp.gov.br",
            "wms.geosampa.prefeitura.sp.gov.br",
        }:
            return parts._replace(scheme="https").geturl()
        return value

    @field_validator("supabase_url")
    @classmethod
    def _supabase_base_url(cls, value: str | None) -> str | None:
        if not value:
            return None
        parts = urlsplit(value)
        if parts.scheme != "https" or not parts.hostname:
            raise ValueError("SUPABASE_URL deve ser https://<project-ref>.supabase.co")
        if parts.path not in ("", "/") or parts.query or parts.fragment:
            raise ValueError(
                "SUPABASE_URL deve ser só a base https://<project-ref>.supabase.co, "
                "sem caminho como /rest/v1 ou /auth/v1"
            )
        return f"https://{parts.netloc}"

    @field_validator("serve_frontend_dir")
    @classmethod
    def _frontend_dir(cls, value: str | None) -> str | None:
        if not value or not value.strip():
            return None
        path = Path(value).expanduser().resolve()
        if not (path / "index.html").is_file():
            raise ValueError("SERVE_FRONTEND_DIR deve apontar para o build do PWA (index.html)")
        return str(path)

    @field_validator("cors_allowed_origins")
    @classmethod
    def _cors_origins(cls, value: str | None) -> str | None:
        if not value or not value.strip():
            return None
        origins = [origin.strip() for origin in value.split(",") if origin.strip()]
        for origin in origins:
            parts = urlsplit(origin)
            if origin == "*" or parts.scheme not in ("https", "http") or not parts.hostname:
                raise ValueError("CORS_ALLOWED_ORIGINS exige origens explícitas; '*' é recusado")
            if parts.scheme == "http" and parts.hostname not in ("localhost", "127.0.0.1"):
                raise ValueError("origem http só é aceita para localhost; produção usa https")
            if parts.path not in ("", "/"):
                raise ValueError("origem CORS não leva caminho")
        return ",".join(origin.rstrip("/") for origin in origins)

    @property
    def cors_origins(self) -> list[str]:
        return self.cors_allowed_origins.split(",") if self.cors_allowed_origins else []

    @property
    def visitor_upload_enabled(self) -> bool:
        return (
            self.app_env.lower() in {"development", "dev", "demo"}
            and urlsplit(self.supabase_url or "").hostname == f"{URMIND_DEV_SHADOW_REF}.supabase.co"
            and bool(self.database_pooler_url and self.supabase_secret_key)
        )

    @field_validator("supabase_jwks_url")
    @classmethod
    def _jwks_url(cls, value: str | None) -> str | None:
        if not value:
            return None
        parts = urlsplit(value)
        if parts.scheme != "https" or not parts.hostname or parts.path != JWKS_PATH:
            raise ValueError(
                f"SUPABASE_JWKS_URL deve ser https://<project-ref>.supabase.co{JWKS_PATH}"
            )
        return value

    @model_validator(mode="after")
    def _jwks_do_mesmo_projeto(self) -> "Settings":
        if self.supabase_url and self.database_pooler_url:
            auth_ref = (urlsplit(self.supabase_url).hostname or "").split(".")[0]
            db_parts = urlsplit(self.database_pooler_url)
            username = db_parts.username or ""
            hostname = db_parts.hostname or ""
            # Supavisor usernames are `<role>.<project_ref>` (postgres or urmind_runtime).
            db_ref = (
                username.rsplit(".", 1)[1]
                if "." in username
                else hostname.removeprefix("db.").split(".")[0]
                if hostname.startswith("db.") and hostname.endswith(".supabase.co")
                else ""
            )
            if not db_ref or db_ref != auth_ref:
                raise ValueError(
                    "SUPABASE_URL e DATABASE_POOLER_URL apontam para projetos diferentes"
                )
        if self.vision_execution_mode == "shadow" and self.shadow_model_version_id is None:
            raise ValueError("SHADOW_MODEL_VERSION_ID obrigatório em modo shadow")
        if self.vision_execution_mode == "shadow" and self.app_env.lower() not in {
            "development",
            "dev",
            "demo",
        }:
            raise ValueError("modo shadow permitido somente em DEV/DEMO")
        if self.vision_execution_mode == "shadow" and (
            urlsplit(self.supabase_url or "").hostname != f"{URMIND_DEV_SHADOW_REF}.supabase.co"
            # Least-privilege runtime (urmind_runtime) or the legacy admin identity,
            # always on the single authorized DEV project.
            or urlsplit(self.database_pooler_url or "").username
            not in {f"urmind_runtime.{URMIND_DEV_SHADOW_REF}", f"postgres.{URMIND_DEV_SHADOW_REF}"}
        ):
            raise ValueError("modo shadow permitido somente no Urmind DEV confirmado")
        if (
            self.supabase_url
            and self.supabase_jwks_url
            and urlsplit(self.supabase_jwks_url).netloc != urlsplit(self.supabase_url).netloc
        ):
            raise ValueError("SUPABASE_JWKS_URL aponta para um projeto diferente de SUPABASE_URL")
        return self

    @classmethod
    def from_environment(cls) -> "Settings":
        return cls.model_validate(os.environ)


@lru_cache
def get_settings() -> Settings:
    return Settings.from_environment()
