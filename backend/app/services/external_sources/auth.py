"""Guards de autenticação para operações opcionais de plataforma."""

from app.config import Settings


class ExternalAuthError(RuntimeError):
    pass


def require_hf_token(settings: Settings) -> str:
    if not settings.hf_token:
        raise ExternalAuthError("HF_AUTH_REQUIRED")
    return settings.hf_token


def require_kaggle_token(settings: Settings) -> str:
    if not settings.kaggle_api_token:
        raise ExternalAuthError("KAGGLE_AUTH_REQUIRED")
    return settings.kaggle_api_token
