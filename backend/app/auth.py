"""Validação dos JWT do Supabase Auth (MASTER_PLAN §17, §25 passo 2).

O token é verificado pela chave pública publicada no JWKS do projeto — nunca pela
secret key. Assinatura, issuer, audience e expiração são obrigatórios: um token
que falhe em qualquer um deles vira 401, sem detalhe que ajude a forjar outro.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated, Any

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings, get_settings

AUDIENCE = "authenticated"
# Papel do UrMind em `app_metadata` — só o servidor do Auth grava; o usuário não altera.
REVIEWER_ROLES = frozenset({"reviewer", "admin"})
ALGORITHMS = ["ES256", "RS256"]

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class AuthenticatedUser:
    id: str
    email: str | None
    role: str
    urmind_role: str | None = None

    @property
    def can_review(self) -> bool:
        return self.urmind_role in REVIEWER_ROLES


class AuthNotConfiguredError(RuntimeError):
    """SUPABASE_URL/SUPABASE_JWKS_URL ausentes: não há como validar usuário."""


def issuer_for(settings: Settings) -> str:
    if not settings.supabase_url:
        raise AuthNotConfiguredError("SUPABASE_URL não configurada")
    return f"{settings.supabase_url}/auth/v1"


@lru_cache
def _jwks_client(jwks_url: str) -> jwt.PyJWKClient:
    # Cache de chaves do PyJWKClient: busca de novo só quando aparece um `kid` novo.
    return jwt.PyJWKClient(jwks_url, cache_keys=True, lifespan=3600, timeout=10)


def decode_token(token: str, settings: Settings, *, key: Any | None = None) -> dict[str, Any]:
    """Valida e decodifica. `key` só é passado em teste, com par de chaves local."""
    if not settings.supabase_jwks_url:
        raise AuthNotConfiguredError("SUPABASE_JWKS_URL não configurada")
    signing_key = key if key is not None else _jwks_client(
        settings.supabase_jwks_url
    ).get_signing_key_from_jwt(token).key
    return jwt.decode(
        token,
        signing_key,
        algorithms=ALGORITHMS,
        audience=AUDIENCE,
        issuer=issuer_for(settings),
        options={"require": ["exp", "iat", "sub", "aud", "iss"]},
        leeway=30,
    )


async def require_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> AuthenticatedUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Autenticação necessária",
            headers={"WWW-Authenticate": "Bearer"},
        )
    settings = get_settings()
    try:
        # PyJWKClient faz I/O síncrono ao buscar o JWKS; fora do event loop.
        claims = await asyncio.to_thread(decode_token, credentials.credentials, settings)
    except AuthNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail="Autenticação não configurada") from exc
    except (jwt.PyJWTError, jwt.PyJWKClientError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido ou expirado",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    app_metadata = claims.get("app_metadata") or {}
    return AuthenticatedUser(
        id=str(claims["sub"]),
        email=claims.get("email"),
        role=str(claims.get("role", "")),
        urmind_role=app_metadata.get("urmind_role") if isinstance(app_metadata, dict) else None,
    )


CurrentUser = Annotated[AuthenticatedUser, Depends(require_user)]
