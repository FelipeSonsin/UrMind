"""Source-byte acquisition checks; no network or protected TEST access."""

from __future__ import annotations

import hashlib
import io
import sys
import zipfile
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts" / "datasets"
sys.path.insert(0, str(SCRIPTS))
from acquire_ird_dashcam import _extract_member, _Stream


def test_stream_extracts_original_member_and_verifies_sha(tmp_path: Path) -> None:
    original = b"jpeg-original-bytes" * 100
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("images/Dash_0001.jpg", original)
    buffer.seek(0)
    with zipfile.ZipFile(buffer) as archive:
        info = archive.getinfo("images/Dash_0001.jpg")
    buffer.seek(0)
    stream = _Stream(buffer)
    target = tmp_path / "images" / "Dash_0001.jpg"

    _extract_member(stream, info, target, hashlib.sha256(original).hexdigest())

    assert target.read_bytes() == original
    assert not target.with_suffix(".jpg.part").exists()


def test_stream_rejects_wrong_published_image_sha(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("images/Dash_0001.jpg", b"original")
    buffer.seek(0)
    with zipfile.ZipFile(buffer) as archive:
        info = archive.getinfo("images/Dash_0001.jpg")
    buffer.seek(0)

    with pytest.raises(ValueError, match="image integrity mismatch"):
        _extract_member(_Stream(buffer), info, tmp_path / "image.jpg", "0" * 64)
