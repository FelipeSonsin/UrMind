"""Atribuição do papel de revisor (MASTER_PLAN §16.2, §17).

O papel vive em `app_metadata.urmind_role` do Supabase Auth: só o servidor escreve
lá, com a secret key, e o backend o lê do JWT. O frontend nunca decide quem revisa.

    python -m app.services.reviewer_admin --list
    python -m app.services.reviewer_admin --grant pessoa@exemplo.org
    python -m app.services.reviewer_admin --revoke pessoa@exemplo.org
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

import httpx

from app.auth import REVIEWER_ROLES
from app.config import Settings, get_settings

ROLE_KEY = "urmind_role"
REVIEWER = "reviewer"
TIMEOUT_S = 20.0


class ReviewerAdminError(RuntimeError):
    """Falha na administração de papéis. A mensagem nunca inclui a secret key."""


def _headers(settings: Settings) -> dict[str, str]:
    if not settings.supabase_url or not settings.supabase_secret_key:
        raise ReviewerAdminError("SUPABASE_URL/SUPABASE_SECRET_KEY ausentes")
    return {
        "apikey": settings.supabase_secret_key,
        "Authorization": f"Bearer {settings.supabase_secret_key}",
    }


def _users(settings: Settings, client: httpx.Client) -> list[dict[str, Any]]:
    response = client.get(
        f"{settings.supabase_url}/auth/v1/admin/users",
        headers=_headers(settings),
        timeout=TIMEOUT_S,
    )
    if response.status_code != 200:
        raise ReviewerAdminError(f"listagem recusada (HTTP {response.status_code})")
    return list(response.json().get("users", []))


def _find(settings: Settings, client: httpx.Client, email: str) -> dict[str, Any]:
    for user in _users(settings, client):
        if user.get("email", "").lower() == email.lower():
            return user
    raise ReviewerAdminError(f"usuário não encontrado: {email}")


def set_role(
    settings: Settings, client: httpx.Client, email: str, role: str | None
) -> dict[str, Any]:
    user = _find(settings, client, email)
    # A API admin faz merge de app_metadata: omitir a chave não apaga nada.
    # Para remover o papel é preciso enviá-la explicitamente como null.
    metadata: dict[str, Any] = {**(user.get("app_metadata") or {}), ROLE_KEY: role}
    response = client.put(
        f"{settings.supabase_url}/auth/v1/admin/users/{user['id']}",
        headers=_headers(settings),
        json={"app_metadata": metadata},
        timeout=TIMEOUT_S,
    )
    if response.status_code != 200:
        raise ReviewerAdminError(f"atualização recusada (HTTP {response.status_code})")
    body = response.json()
    return {
        "id": body["id"],
        "email": body["email"],
        ROLE_KEY: (body.get("app_metadata") or {}).get(ROLE_KEY),
        "can_review": (body.get("app_metadata") or {}).get(ROLE_KEY) in REVIEWER_ROLES,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--list", action="store_true", help="usuários e papéis atuais")
    action.add_argument("--grant", metavar="EMAIL", help="concede o papel de revisor")
    action.add_argument("--revoke", metavar="EMAIL", help="remove o papel")
    args = parser.parse_args(argv)
    settings = get_settings()
    try:
        with httpx.Client() as client:
            if args.list:
                for user in _users(settings, client):
                    role = (user.get("app_metadata") or {}).get(ROLE_KEY) or "—"
                    print(f"{user['email']}\t{role}")
                return 0
            email = args.grant or args.revoke
            result = set_role(settings, client, email, REVIEWER if args.grant else None)
            print(
                f"{result['email']}: urmind_role={result[ROLE_KEY] or '—'} can_review={result['can_review']}"
            )
            return 0
    except (ReviewerAdminError, httpx.HTTPError) as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
