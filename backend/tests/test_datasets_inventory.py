"""Inventário de `datasets/raw/` (§26: arquivo vazio não é componente pronto)."""

from __future__ import annotations

import hashlib

from app.config import PROJECT_DIR
from app.datasets.catalog import DatasetSource, ExpectedFile
from app.datasets.inventory import (
    DatasetState,
    FileState,
    inspect_source,
    portable_path,
)

CONTEUDO = b"conteudo de teste do urmind"
SHA256 = hashlib.sha256(CONTEUDO).hexdigest()


def _fonte(**kwargs) -> DatasetSource:
    from app.datasets.catalog import DatasetRole

    base = {
        "id": "fonte_teste",
        "title": "Fonte de teste",
        "homepage": "https://example.invalid",
        "license": "CC BY 4.0",
        "role": DatasetRole.TRAINING_V1,
        "version": "v1",
        "adapter": None,
        "expected_files": (
            ExpectedFile("pacote.zip", size_bytes=len(CONTEUDO), sha256=SHA256),
        ),
    }
    return DatasetSource(**{**base, **kwargs})


def test_pasta_inexistente_e_ausente(tmp_path):
    inventory = inspect_source(_fonte(), tmp_path)

    assert inventory.state is DatasetState.ABSENT
    assert inventory.blocking_reasons()


def test_pasta_so_com_readme_continua_ausente(tmp_path):
    """Criar a pasta e um README não faz o dataset existir."""
    raiz = tmp_path / "fonte_teste"
    raiz.mkdir()
    (raiz / "README.md").write_text("placeholder", encoding="utf-8")

    assert inspect_source(_fonte(), tmp_path).state is DatasetState.ABSENT


def test_arquivo_part_e_incompleto_mesmo_com_o_tamanho_certo(tmp_path):
    """Byte-count completo não fecha download: quem renomeia é uma pessoa."""
    raiz = tmp_path / "fonte_teste"
    raiz.mkdir()
    (raiz / "pacote.zip.part").write_bytes(CONTEUDO)

    inventory = inspect_source(_fonte(), tmp_path)
    report = inventory.files[0]

    assert inventory.state is DatasetState.INCOMPLETE
    assert report.state is FileState.INCOMPLETE
    assert report.actual_bytes == len(CONTEUDO)
    assert "renomear" in report.note
    assert not inventory.ready


def test_tamanho_diferente_do_publicado_e_reportado(tmp_path):
    raiz = tmp_path / "fonte_teste"
    raiz.mkdir()
    (raiz / "pacote.zip").write_bytes(b"curto")

    inventory = inspect_source(_fonte(), tmp_path)

    assert inventory.files[0].state is FileState.SIZE_MISMATCH
    assert inventory.state is DatasetState.INCOMPLETE


def test_arquivo_completo_fica_pronto_sem_conferir_checksum(tmp_path):
    raiz = tmp_path / "fonte_teste"
    raiz.mkdir()
    (raiz / "pacote.zip").write_bytes(CONTEUDO)

    inventory = inspect_source(_fonte(), tmp_path)

    assert inventory.state is DatasetState.READY
    assert inventory.files[0].state is FileState.PRESENT
    assert inventory.files[0].note == "checksum não conferido"
    assert inventory.checksums_verified is False


def test_verify_confirma_o_checksum_publicado(tmp_path):
    raiz = tmp_path / "fonte_teste"
    raiz.mkdir()
    (raiz / "pacote.zip").write_bytes(CONTEUDO)

    inventory = inspect_source(_fonte(), tmp_path, verify=True)

    assert inventory.files[0].state is FileState.VERIFIED
    assert inventory.ready


def test_conteudo_trocado_com_mesmo_tamanho_e_pego_pelo_checksum(tmp_path):
    """É exatamente o caso que o tamanho em bytes não detecta."""
    raiz = tmp_path / "fonte_teste"
    raiz.mkdir()
    (raiz / "pacote.zip").write_bytes(b"X" * len(CONTEUDO))

    inventory = inspect_source(_fonte(), tmp_path, verify=True)

    assert inventory.files[0].state is FileState.CORRUPT
    assert not inventory.ready
    assert "checksum não confere" in inventory.blocking_reasons()[0]


def test_arquivo_opcional_ausente_nao_bloqueia(tmp_path):
    raiz = tmp_path / "fonte_teste"
    raiz.mkdir()
    (raiz / "pacote.zip").write_bytes(CONTEUDO)

    fonte = _fonte(
        expected_files=(
            ExpectedFile("pacote.zip", size_bytes=len(CONTEUDO), sha256=SHA256),
            ExpectedFile("extra.txt", required=False),
        )
    )
    inventory = inspect_source(fonte, tmp_path)

    assert inventory.state is DatasetState.READY
    assert inventory.blocking_reasons() == ()


def test_fonte_sem_arquivo_declarado_fica_declared_only(tmp_path):
    """Project Sidewalk depende de exportação sob demanda; não há ZIP a esperar."""
    raiz = tmp_path / "fonte_teste"
    (raiz / "labels").mkdir(parents=True)
    (raiz / "labels" / "cidade.csv").write_text("lat,lng\n", encoding="utf-8")

    inventory = inspect_source(_fonte(expected_files=()), tmp_path)

    assert inventory.state is DatasetState.DECLARED_ONLY
    assert inventory.extracted_dirs == ("labels",)


def test_manifesto_nao_grava_caminho_desta_maquina():
    """O manifesto é versionado e será lido em outro computador."""
    dentro = PROJECT_DIR / "datasets" / "raw" / "rdd2022"

    assert portable_path(dentro) == "datasets/raw/rdd2022"
    assert ":" not in portable_path(dentro)


def test_caminho_fora_do_projeto_permanece_absoluto(tmp_path):
    """Sem relativo honesto a escrever, o absoluto é preservado em vez de inventado."""
    assert portable_path(tmp_path) == str(tmp_path)
