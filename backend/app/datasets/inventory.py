"""Inventário do que existe em `datasets/raw/` (MASTER_PLAN §8.3 passo 1, §26).

O §26 diz que um componente só está pronto quando *existe comportamento real,
não arquivo vazio*. Pasta criada não é dataset baixado, e `.part` não é ZIP.
Este módulo responde, com o disco na mão, o que de fato está lá.

Duas escolhas conscientes:

**Checksum não roda por padrão.** Conferir MD5 de 13,2 GB leva minutos e lê o
arquivo inteiro. O tamanho em bytes já separa o download interrompido do
completo, então a verificação criptográfica é explícita (`verify=True`) e
existe para o momento em que o arquivo vai virar `dataset_version`.

**`.part` com o tamanho certo continua sendo `.part`.** O byte-count pode ter
chegado ao fim sem o downloader ter fechado o arquivo. O estado reportado é
`INCOMPLETE` com uma observação de que o tamanho bate — quem decide renomear é
uma pessoa, depois de conferir o checksum.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from app.config import PROJECT_DIR, datasets_raw_dir
from app.datasets.catalog import SOURCES, DatasetSource, ExpectedFile, get_source

__all__ = [
    "DatasetInventory",
    "DatasetState",
    "FileReport",
    "FileState",
    "file_digest",
    "inspect_all",
    "inspect_source",
    "portable_path",
]

_CHUNK = 1024 * 1024


class FileState(StrEnum):
    MISSING = "missing"
    INCOMPLETE = "incomplete"
    """Existe apenas como `.part`: download não finalizado."""

    SIZE_MISMATCH = "size_mismatch"
    PRESENT = "present"
    """Existe com o tamanho esperado (ou sem tamanho declarado). Sem checksum."""

    VERIFIED = "verified"
    """Checksum publicado pela fonte confere."""

    CORRUPT = "corrupt"
    """Checksum não confere. O arquivo não pode entrar em treino nem registro."""


class DatasetState(StrEnum):
    ABSENT = "absent"
    """Nem a pasta existe, ou ela está vazia."""

    INCOMPLETE = "incomplete"
    """Falta arquivo obrigatório, ou algum está em `.part`."""

    READY = "ready"
    """Todos os arquivos obrigatórios presentes."""

    DECLARED_ONLY = "declared_only"
    """Fonte sem arquivos obrigatórios declarados (adiada ou exportação sob demanda)."""


@dataclass(frozen=True)
class FileReport:
    relative_path: str
    state: FileState
    expected_bytes: int | None
    actual_bytes: int | None
    actual_path: str | None
    """Caminho realmente encontrado. Difere de `relative_path` quando é `.part`."""

    required: bool
    note: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.relative_path,
            "state": str(self.state),
            "expected_bytes": self.expected_bytes,
            "actual_bytes": self.actual_bytes,
            "found_at": self.actual_path,
            "required": self.required,
            "note": self.note,
        }


@dataclass(frozen=True)
class DatasetInventory:
    dataset_id: str
    root: Path
    state: DatasetState
    files: tuple[FileReport, ...]
    extracted_dirs: tuple[str, ...]
    """Subpastas presentes na raiz do dataset — sinal de extração já feita."""

    total_bytes: int
    checksums_verified: bool

    @property
    def ready(self) -> bool:
        return self.state is DatasetState.READY

    def blocking_reasons(self) -> tuple[str, ...]:
        """Por que este dataset ainda não pode virar `dataset_version`."""
        motivos: list[str] = []
        if self.state is DatasetState.ABSENT:
            motivos.append(f"nada em {self.root}")
        for report in self.files:
            if report.state is FileState.MISSING and report.required:
                motivos.append(f"{report.relative_path}: ausente")
            elif report.state is FileState.INCOMPLETE:
                detalhe = f" — {report.note}" if report.note else ""
                motivos.append(f"{report.relative_path}: download incompleto (.part){detalhe}")
            elif report.state is FileState.SIZE_MISMATCH:
                motivos.append(
                    f"{report.relative_path}: {report.actual_bytes} bytes, "
                    f"esperado {report.expected_bytes}"
                )
            elif report.state is FileState.CORRUPT:
                motivos.append(f"{report.relative_path}: checksum não confere")
        return tuple(motivos)

    def as_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "root": portable_path(self.root),
            "state": str(self.state),
            "total_bytes": self.total_bytes,
            "checksums_verified": self.checksums_verified,
            "extracted_dirs": list(self.extracted_dirs),
            "files": [f.as_dict() for f in self.files],
            "blocking_reasons": list(self.blocking_reasons()),
        }


def portable_path(path: Path) -> str:
    """Caminho relativo ao projeto quando possível.

    O manifesto é versionado e vai ser lido em outra máquina. Gravar
    um caminho absoluto ali amarraria o registro a este computador — ele
    deixaria de significar qualquer coisa no próximo clone. Quando o caminho
    está fora do projeto (o caso de um teste em pasta temporária), não há
    relativo honesto a escrever e o absoluto é preservado.
    """
    try:
        return path.resolve().relative_to(PROJECT_DIR).as_posix()
    except ValueError:
        return str(path)


def file_digest(path: Path, algorithm: str) -> str:
    """Hash em streaming. Arquivo de dataset não cabe na memória."""
    digest = hashlib.new(algorithm)
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _inspect_file(root: Path, expected: ExpectedFile, verify: bool) -> FileReport:
    target = root / expected.relative_path
    partial = root / f"{expected.relative_path}.part"

    if not target.exists() and partial.exists():
        size = partial.stat().st_size
        note = "download incompleto"
        if expected.size_bytes is not None and size == expected.size_bytes:
            note = (
                "tamanho bate com o publicado, mas o arquivo continua marcado como "
                ".part; conferir checksum e renomear manualmente"
            )
        return FileReport(
            relative_path=expected.relative_path,
            state=FileState.INCOMPLETE,
            expected_bytes=expected.size_bytes,
            actual_bytes=size,
            actual_path=partial.name,
            required=expected.required,
            note=note,
        )

    if not target.exists():
        return FileReport(
            relative_path=expected.relative_path,
            state=FileState.MISSING,
            expected_bytes=expected.size_bytes,
            actual_bytes=None,
            actual_path=None,
            required=expected.required,
        )

    size = target.stat().st_size
    if expected.size_bytes is not None and size != expected.size_bytes:
        return FileReport(
            relative_path=expected.relative_path,
            state=FileState.SIZE_MISMATCH,
            expected_bytes=expected.size_bytes,
            actual_bytes=size,
            actual_path=expected.relative_path,
            required=expected.required,
            note="tamanho difere do publicado pela fonte oficial",
        )

    if verify and (expected.sha256 or expected.md5):
        algorithm, declarado = ("sha256", expected.sha256) if expected.sha256 else (
            "md5",
            expected.md5,
        )
        obtido = file_digest(target, algorithm)
        confere = obtido == declarado
        return FileReport(
            relative_path=expected.relative_path,
            state=FileState.VERIFIED if confere else FileState.CORRUPT,
            expected_bytes=expected.size_bytes,
            actual_bytes=size,
            actual_path=expected.relative_path,
            required=expected.required,
            note=f"{algorithm} {'confere' if confere else f'divergente: {obtido}'}",
        )

    return FileReport(
        relative_path=expected.relative_path,
        state=FileState.PRESENT,
        expected_bytes=expected.size_bytes,
        actual_bytes=size,
        actual_path=expected.relative_path,
        required=expected.required,
        note=None if (expected.sha256 or expected.md5) is None else "checksum não conferido",
    )


def _directory_bytes(root: Path) -> int:
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file())


def inspect_source(
    source: DatasetSource | str,
    raw_root: Path | None = None,
    *,
    verify: bool = False,
) -> DatasetInventory:
    """Lê o disco e descreve o estado real de uma fonte. Não baixa nem move nada."""
    if isinstance(source, str):
        source = get_source(source)

    root = (raw_root or datasets_raw_dir()) / source.id

    if not root.is_dir():
        return DatasetInventory(
            dataset_id=source.id,
            root=root,
            state=DatasetState.ABSENT,
            files=tuple(
                _inspect_file(root, e, verify=False) for e in source.expected_files
            ),
            extracted_dirs=(),
            total_bytes=0,
            checksums_verified=False,
        )

    reports = tuple(_inspect_file(root, e, verify) for e in source.expected_files)
    subdirs = tuple(sorted(p.name for p in root.iterdir() if p.is_dir()))
    total = _directory_bytes(root)

    entradas_uteis = [
        p for p in root.iterdir() if p.name.lower() not in {"readme.md", ".gitignore"}
    ]
    if not entradas_uteis:
        state = DatasetState.ABSENT
    elif not source.expected_files:
        state = DatasetState.DECLARED_ONLY
    elif all(
        r.state in {FileState.PRESENT, FileState.VERIFIED}
        for r in reports
        if r.required
    ):
        state = DatasetState.READY
    else:
        state = DatasetState.INCOMPLETE

    return DatasetInventory(
        dataset_id=source.id,
        root=root,
        state=state,
        files=reports,
        extracted_dirs=subdirs,
        total_bytes=total,
        checksums_verified=verify,
    )


def inspect_all(
    raw_root: Path | None = None, *, verify: bool = False
) -> tuple[DatasetInventory, ...]:
    return tuple(inspect_source(s, raw_root, verify=verify) for s in SOURCES)
