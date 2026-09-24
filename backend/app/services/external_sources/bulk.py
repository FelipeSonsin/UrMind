"""Downloads bulk explícitos; nunca executados por startup, check ou importação."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from app.services.external_sources.http import ExternalHttpClient


class BulkSelectionError(ValueError):
    pass


UF_CODES = {
    "RO": "11",
    "AC": "12",
    "AM": "13",
    "RR": "14",
    "PA": "15",
    "AP": "16",
    "TO": "17",
    "MA": "21",
    "PI": "22",
    "CE": "23",
    "RN": "24",
    "PB": "25",
    "PE": "26",
    "AL": "27",
    "SE": "28",
    "BA": "29",
    "MG": "31",
    "ES": "32",
    "RJ": "33",
    "SP": "35",
    "PR": "41",
    "SC": "42",
    "RS": "43",
    "MS": "50",
    "MT": "51",
    "GO": "52",
    "DF": "53",
}


@dataclass(frozen=True)
class CnefeSelection:
    uf: str | None = None
    municipality_code: str | None = None

    def __post_init__(self) -> None:
        uf = self.uf.upper() if self.uf else None
        object.__setattr__(self, "uf", uf)
        if not uf and not self.municipality_code:
            raise BulkSelectionError("informe UF ou municipio para o CNEFE")
        if uf and uf not in UF_CODES:
            raise BulkSelectionError("UF invalida")
        if self.municipality_code and not re.fullmatch(r"\d{7}", self.municipality_code):
            raise BulkSelectionError("codigo de municipio deve ter 7 digitos")
        if self.municipality_code:
            inferred = next(
                (
                    sigla
                    for sigla, code in UF_CODES.items()
                    if self.municipality_code.startswith(code)
                ),
                None,
            )
            if inferred is None:
                raise BulkSelectionError("codigo de municipio sem UF reconhecida")
            if uf and inferred != uf:
                raise BulkSelectionError("municipio nao pertence a UF selecionada")
            object.__setattr__(self, "uf", inferred)

    @property
    def uf_directory(self) -> str:
        assert self.uf is not None
        return f"{UF_CODES[self.uf]}_{self.uf}"


@dataclass(frozen=True)
class BulkDownloadResult:
    path: Path
    file_size: int
    retrieved_at: str
    provenance_path: Path


def _write_manifest(path: Path, payload: dict[str, object]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


async def download_geofabrik(
    *,
    destination_dir: Path,
    pbf_url: str,
    md5_url: str,
    client: ExternalHttpClient,
) -> BulkDownloadResult:
    checksum_response = await client.get(md5_url, provider="geofabrik")
    checksum_response.raise_for_status()
    match = re.search(r"\b([0-9a-fA-F]{32})\b", checksum_response.text)
    if match is None:
        raise ValueError("MD5 da Geofabrik ausente ou malformado")
    expected_md5 = match.group(1).lower()
    filename = Path(urlsplit(pbf_url).path).name
    if not filename:
        raise ValueError("URL Geofabrik sem nome de arquivo")
    destination = destination_dir / filename
    receipt = await client.download(
        pbf_url,
        destination,
        hash_name="md5",  # checksum publicado pela Geofabrik
        expected_digest=expected_md5,
        timeout=300,
        provider="geofabrik",
    )
    retrieved_at = datetime.now(UTC).isoformat()
    provenance = destination.with_name(destination.name + ".provenance.json")
    try:
        _write_manifest(
            provenance,
            {
                "source": "Geofabrik Sudeste",
                "source_url": pbf_url,
                "checksum_url": md5_url,
                "source_version": "latest at retrieval",
                "retrieved_at": retrieved_at,
                "file_size": receipt.size_bytes,
                "md5": receipt.digest,
                "checksum_correlation_id": checksum_response.extensions.get(
                    "urmind_correlation_id"
                ),
                "download_correlation_id": receipt.correlation_id,
                "license": "ODbL 1.0 / OpenStreetMap contributors",
            },
        )
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return BulkDownloadResult(destination, receipt.size_bytes, retrieved_at, provenance)


async def download_cnefe(
    *,
    destination_dir: Path,
    base_url: str,
    selection: CnefeSelection,
    client: ExternalHttpClient,
) -> BulkDownloadResult:
    """Baixa uma UF ou um município; nunca permite o Brasil inteiro."""
    csv_base = urljoin(base_url.rstrip("/") + "/", "Arquivos_CNEFE/CSV/")
    listing_correlation_id: str | None = None
    if selection.municipality_code:
        directory_url = urljoin(csv_base, f"Municipio/{selection.uf_directory}/")
        listing = await client.get(directory_url, provider="ibge_cnefe")
        listing_correlation_id = listing.extensions.get("urmind_correlation_id")
        listing.raise_for_status()
        pattern = rf'href="({selection.municipality_code}_[^"/]+\.zip)"'
        candidates = sorted(set(re.findall(pattern, listing.text, flags=re.IGNORECASE)))
        if len(candidates) != 1:
            raise BulkSelectionError("arquivo municipal CNEFE nao encontrado de forma univoca")
        filename = candidates[0]
        source_url = urljoin(directory_url, filename)
        scope: dict[str, str | None] = {
            "uf": selection.uf,
            "municipality_code": selection.municipality_code,
        }
    else:
        filename = f"{selection.uf_directory}.zip"
        source_url = urljoin(csv_base, f"UF/{filename}")
        scope = {"uf": selection.uf}
    destination = destination_dir / filename
    receipt = await client.download(
        source_url,
        destination,
        hash_name="sha256",
        timeout=300,
        provider="ibge_cnefe",
    )
    retrieved_at = datetime.now(UTC).isoformat()
    provenance = destination.with_name(destination.name + ".provenance.json")
    try:
        _write_manifest(
            provenance,
            {
                "source": "IBGE CNEFE 2022",
                "source_url": source_url,
                "source_version": "Censo Demografico 2022",
                "selection": scope,
                "retrieved_at": retrieved_at,
                "file_size": receipt.size_bytes,
                "sha256_local": receipt.digest,
                "listing_correlation_id": listing_correlation_id,
                "download_correlation_id": receipt.correlation_id,
                "checksum_source": "local_sha256; upstream checksum not published",
                "license": "IBGE - dados publicos",
            },
        )
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return BulkDownloadResult(destination, receipt.size_bytes, retrieved_at, provenance)
