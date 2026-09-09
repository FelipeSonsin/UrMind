import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = BACKEND_DIR.parent
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"
ALEMBIC_VERSIONS_DIR = BACKEND_DIR / "alembic" / "versions"
load_dotenv(BACKEND_DIR / ".env")

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
    model_config = ConfigDict(frozen=True)

    app_name: str = "URMIND"
    app_env: str = "development"
    # Conexão direta ao PostgreSQL/PostGIS do Supabase (SQLAlchemy 2 + psycopg 3).
    # É o único acesso a dados do backend (§4.3): nada de PostgREST com secret key.
    database_url: str | None = Field(default=None, alias="DATABASE_URL", repr=False)

    # Ainda não consumidas por nenhum código. Ficam opcionais até o passo 3 do
    # §25 (Storage + Capture) e o Worker do §7 realmente usarem Storage e Queue.
    supabase_url: str | None = Field(default=None, alias="SUPABASE_URL")
    supabase_secret_key: str | None = Field(default=None, alias="SUPABASE_SECRET_KEY", repr=False)
    db_pool_size: int = Field(default=5, alias="DB_POOL_SIZE")
    db_max_overflow: int = Field(default=5, alias="DB_MAX_OVERFLOW")
    db_echo: bool = Field(default=False, alias="DB_ECHO")

    @classmethod
    def from_environment(cls) -> "Settings":
        return cls.model_validate(os.environ)


@lru_cache
def get_settings() -> Settings:
    return Settings.from_environment()
