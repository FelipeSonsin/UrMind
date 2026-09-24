from __future__ import annotations

import os

import pytest

from app.config import Settings
from app.services.external_sources.checks import run_live_checks

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("URMIND_RUN_LIVE_EXTERNAL_CHECKS") != "1",
        reason="live checks externos são opt-in",
    ),
]


@pytest.mark.asyncio
async def test_public_external_services_respond_to_small_checks() -> None:
    results = {item.name: item for item in await run_live_checks(Settings())}
    for name in ("Geofabrik", "Overpass", "OpenFreeMap", "IBGE SIDRA", "Hugging Face"):
        assert results[name].status in {"OK", "OK_PROVIDER", "OK_PUBLIC", "AVAILABLE"}
