"""Integrações externas oficiais do UrMind, sem downloads implícitos."""

from app.services.external_sources.registry import Integration, integration_registry

__all__ = ["Integration", "integration_registry"]
