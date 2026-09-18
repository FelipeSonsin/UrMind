import os
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

BACKEND_DIR = Path(__file__).resolve().parents[1]
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
    database_pooler_url: str | None = Field(
        default=None, alias="DATABASE_POOLER_URL", repr=False
    )

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
    db_pool_size: int = Field(default=5, alias="DB_POOL_SIZE")
    db_max_overflow: int = Field(default=5, alias="DB_MAX_OVERFLOW")
    db_echo: bool = Field(default=False, alias="DB_ECHO")

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
