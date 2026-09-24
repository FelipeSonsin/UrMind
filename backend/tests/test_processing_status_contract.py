"""The frontend parses capture status with a strict z.enum; both lists must match."""

from __future__ import annotations

import re
from pathlib import Path

from app.schemas.core import TERMINAL_PROCESSING_STATUSES, CaptureProcessingStatus

FRONTEND_CONTRACTS = Path(__file__).resolve().parents[2] / "frontend/src/domain/contracts.ts"


def _frontend_statuses() -> set[str]:
    source = FRONTEND_CONTRACTS.read_text(encoding="utf-8")
    block = re.search(
        r"captureProcessingSchema = z\.object\(\{.*?status: z\.enum\(\[(.*?)\]\)", source, re.DOTALL
    )
    assert block, "captureProcessingSchema.status not found in frontend contracts"
    return set(re.findall(r"'([a-z_]+)'", block.group(1)))


def test_frontend_processing_statuses_match_backend_enum() -> None:
    assert _frontend_statuses() == {status.value for status in CaptureProcessingStatus}


def test_detection_completed_is_not_terminal() -> None:
    assert CaptureProcessingStatus.DETECTION_COMPLETED not in TERMINAL_PROCESSING_STATUSES
    assert CaptureProcessingStatus.COMPLETED in TERMINAL_PROCESSING_STATUSES


def test_no_supported_detection_is_canonical_and_legacy_name_is_not_public() -> None:
    statuses = _frontend_statuses()
    assert "no_supported_detection" in statuses
    assert "no_detection" not in statuses
    assert CaptureProcessingStatus("no_supported_detection") in TERMINAL_PROCESSING_STATUSES
