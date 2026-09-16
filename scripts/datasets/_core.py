"""Base compartilhada dos scripts de dataset do UrMind.

Três responsabilidades, e nenhum script de auditoria deveria reimplementar
qualquer uma delas:

**Achar a raiz do projeto sem depender da máquina.** `PROJECT_ROOT` sobe a
partir deste arquivo até encontrar as marcas do projeto. Nenhum caminho de
usuário, disco ou pasta sincronizada aparece em lugar nenhum — é o que permite
entregar a pasta `FECART Sistema` para outra pessoa e tudo continuar valendo.

**Impedir que um script toque em algo fora do projeto.** `assert_inside_project`
resolve o caminho canônico (seguindo symlink, `..` e maiúsculas do Windows) e
recusa qualquer alvo que não esteja sob a raiz. Toda operação destrutiva futura
passa por aqui antes de existir.

**Falar de espaço em disco com honestidade.** `logical_size` é a soma dos bytes
dos arquivos; `size_on_disk` é o que o sistema de arquivos realmente reserva, e
os dois divergem em pastas com muitos arquivos pequenos. Arquivo *cloud-only* do
OneDrive é contado à parte e **nunca é hidratado** só para ser medido.

Este módulo é stdlib puro de propósito: os scripts precisam rodar em uma máquina
que ainda não instalou o backend.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import shutil
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

__all__ = [
    "DATASETS_DIR",
    "PROJECT_ROOT",
    "RAW_DIR",
    "BudgetError",
    "DirSizes",
    "OutsideProjectError",
    "assert_inside_project",
    "detect_cloud_sync",
    "file_sha256",
    "free_disk_bytes",
    "human_bytes",
    "is_cloud_only",
    "measure_dir",
    "provenance",
    "relative_to_project",
    "timestamp",
    "write_json_report",
]

_MARKERS = ("docs", "datasets", "scripts")


def _find_project_root(start: Path) -> Path:
    """Sobe até a pasta que contém as marcas do projeto.

    Não usa variável de ambiente nem caminho fixo: o script pode ser chamado de
    qualquer diretório de trabalho, em qualquer máquina, e ainda assim resolve.
    """
    for candidate in (start, *start.parents):
        if all((candidate / marker).is_dir() for marker in _MARKERS):
            return candidate
    raise RuntimeError(
        f"raiz do projeto não encontrada a partir de {start}; "
        f"esperava uma pasta contendo {', '.join(_MARKERS)}"
    )


PROJECT_ROOT = _find_project_root(Path(__file__).resolve().parent)
DATASETS_DIR = PROJECT_ROOT / "datasets"
RAW_DIR = DATASETS_DIR / "raw"

_CHUNK = 1024 * 1024
# Windows: atributos que marcam arquivo do OneDrive ainda não baixado.
_FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x00400000
_FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x00040000
_FILE_ATTRIBUTE_OFFLINE = 0x00001000


class OutsideProjectError(RuntimeError):
    """Alvo fora da raiz do projeto. Nenhuma operação prossegue."""


class BudgetError(RuntimeError):
    """Operação recusada pelo orçamento de armazenamento."""


def relative_to_project(path: Path) -> str:
    """Caminho POSIX relativo à raiz. É o único formato que entra em manifesto."""
    return path.resolve().relative_to(PROJECT_ROOT).as_posix()


def assert_inside_project(path: Path) -> Path:
    """Recusa qualquer caminho que não esteja sob a raiz do projeto.

    Resolve antes de comparar, então `..`, symlink e junction não escapam. É a
    guarda que toda operação destrutiva precisa atravessar — e ela existe
    mesmo enquanto nenhuma operação destrutiva foi implementada.
    """
    resolvido = Path(path).resolve()
    raiz = PROJECT_ROOT.resolve()
    if resolvido != raiz and raiz not in resolvido.parents:
        raise OutsideProjectError(
            f"{resolvido} está fora de {raiz}; operação abortada por segurança"
        )
    return resolvido


def human_bytes(value: float) -> str:
    tamanho = float(value)
    for unidade in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(tamanho) < 1024 or unidade == "TiB":
            return (
                f"{tamanho:,.2f} {unidade}".replace(",", "_")
                .replace(".", ",")
                .replace("_", ".")
            )
        tamanho /= 1024
    return f"{value} B"


def is_cloud_only(path: Path) -> bool:
    """Arquivo existe como placeholder do OneDrive, sem conteúdo local.

    Fora do Windows a resposta é sempre `False`: o conceito não existe.
    Ler o arquivo para descobrir provocaria o download — exatamente o que a
    auditoria não pode fazer.
    """
    if sys.platform != "win32":
        return False
    atributos = path.stat().st_file_attributes
    marcas = (
        _FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS
        | _FILE_ATTRIBUTE_RECALL_ON_OPEN
        | _FILE_ATTRIBUTE_OFFLINE
    )
    return bool(atributos & marcas)


def _cluster_size(path: Path) -> int:
    """Tamanho do cluster do volume, para estimar ocupação real em disco."""
    if sys.platform != "win32":
        return 4096
    try:
        setores = ctypes.c_ulong()
        bytes_por_setor = ctypes.c_ulong()
        livres = ctypes.c_ulong()
        total = ctypes.c_ulong()
        raiz = str(Path(path).resolve().anchor)
        ok = ctypes.windll.kernel32.GetDiskFreeSpaceW(
            ctypes.c_wchar_p(raiz),
            ctypes.byref(setores),
            ctypes.byref(bytes_por_setor),
            ctypes.byref(livres),
            ctypes.byref(total),
        )
        if ok:
            return setores.value * bytes_por_setor.value
    except (AttributeError, OSError):  # pragma: no cover - depende do SO
        pass
    return 4096


@dataclass
class DirSizes:
    """Medição de uma pasta. Logical e on-disk são coisas diferentes."""

    path: str
    logical_size: int = 0
    size_on_disk: int = 0
    file_count: int = 0
    dir_count: int = 0
    cloud_only_count: int = 0
    cloud_only_logical: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "path": self.path,
            "logical_size": self.logical_size,
            "logical_size_human": human_bytes(self.logical_size),
            "size_on_disk": self.size_on_disk,
            "size_on_disk_human": human_bytes(self.size_on_disk),
            "file_count": self.file_count,
            "dir_count": self.dir_count,
            "cloud_only_count": self.cloud_only_count,
            "cloud_only_logical": self.cloud_only_logical,
            "errors": self.errors,
        }


def measure_dir(root: Path) -> DirSizes:
    """Percorre a pasta uma vez e devolve logical, on-disk e placeholders.

    `os.scandir` traz o tamanho junto com a entrada do diretório, então a
    medição não abre nenhum arquivo — e portanto não hidrata nada do OneDrive.
    """
    root = assert_inside_project(root)
    resultado = DirSizes(path=relative_to_project(root))
    if not root.exists():
        return resultado

    pilha = [root]
    while pilha:
        atual = pilha.pop()
        try:
            entradas = list(os.scandir(atual))
        except OSError as exc:
            raise OSError(
                f"falha ao medir {relative_to_project(atual)}: {exc.strerror}"
            ) from exc
        for entrada in entradas:
            try:
                caminho = Path(entrada.path)
                stat = entrada.stat(follow_symlinks=False)
                # Pais são internos e os filhos vêm de scandir. Rejeitar links antes
                # de descer evita resolver 100 mil caminhos completos repetidamente.
                if entrada.is_symlink() or getattr(stat, "st_reparse_tag", 0) in (
                    0xA0000003,
                    0xA000000C,
                ):
                    raise OutsideProjectError(
                        "symlink/junction não permitido na auditoria"
                    )
                if entrada.is_dir(follow_symlinks=False):
                    resultado.dir_count += 1
                    pilha.append(Path(entrada.path))
                    continue
                tamanho = stat.st_size
                resultado.file_count += 1
                resultado.logical_size += tamanho
                attrs = getattr(stat, "st_file_attributes", 0)
                if attrs & (
                    _FILE_ATTRIBUTE_OFFLINE
                    | _FILE_ATTRIBUTE_RECALL_ON_OPEN
                    | _FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS
                ):
                    resultado.cloud_only_count += 1
                    resultado.cloud_only_logical += tamanho
                else:
                    resultado.size_on_disk += allocated_bytes(caminho)
            except OSError as exc:
                raise OSError(
                    f"falha ao medir {relative_to_project(Path(entrada.path))}: {exc.strerror}"
                ) from exc
    return resultado


def free_disk_bytes(path: Path | None = None) -> int:
    return shutil.disk_usage(path or PROJECT_ROOT).free


def detect_cloud_sync(path: Path | None = None) -> dict:
    """Detecta se o projeto está em pasta sincronizada. Só reporta, não age."""
    alvo = (path or PROJECT_ROOT).resolve()
    partes = [p.lower() for p in alvo.parts]
    provedores = {
        "onedrive": any("onedrive" in p for p in partes),
        "dropbox": any("dropbox" in p for p in partes),
        "google_drive": any(
            p in {"google drive", "my drive", "googledrive"} for p in partes
        ),
        "icloud": any("iclouddrive" in p.replace(" ", "") for p in partes),
    }
    detectados = [nome for nome, achou in provedores.items() if achou]
    return {
        "project_root_is_synced": bool(detectados),
        "providers": detectados,
        "note": (
            "Somente reportado. Nenhum script altera, pausa ou move a sincronização."
        ),
    }


def file_sha256(path: Path, limit_bytes: int | None = None) -> str:
    """SHA-256 em streaming. `limit_bytes` lê só o começo, para triagem rápida."""
    path = require_local(path)
    digest = hashlib.sha256()
    lidos = 0
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            if limit_bytes is not None and lidos + len(chunk) > limit_bytes:
                digest.update(chunk[: limit_bytes - lidos])
                break
            digest.update(chunk)
            lidos += len(chunk)
    return digest.hexdigest()


def iter_files(root: Path, suffixes: Iterable[str] | None = None) -> Iterator[Path]:
    """Arquivos em ordem determinística.

    `sorted` não é detalhe: a ordem de `scandir` depende do sistema de arquivos,
    e um manifesto que dependa dela não seria reproduzível em outra máquina.
    """
    normalizados = {s.lower() for s in suffixes} if suffixes else None

    def fail(error):
        raise error

    for atual, dirs, arquivos in os.walk(root, onerror=fail):
        assert_inside_project(Path(atual))
        dirs.sort()
        for name in dirs:
            child = Path(atual) / name
            assert_inside_project(child)
            if child.is_symlink() or (
                hasattr(Path, "is_junction") and child.is_junction()
            ):
                raise OutsideProjectError("symlink/junction não permitido")
        for nome in sorted(arquivos):
            caminho = assert_inside_project(Path(atual) / nome)
            if normalizados is None or caminho.suffix.lower() in normalizados:
                yield caminho


def provenance(
    script_file: str,
    *,
    source_dataset: str,
    source_version: str,
    transform: str,
    params: dict,
) -> dict:
    """Bloco de proveniência exigido de toda versão derivada (§10.2).

    O contrato está em `datasets/metadata/artifact_contract.yaml`, seção
    `derived_manifest_contract`, e `validate_manifests.py` confere. Ele existe
    porque uma derivada sem origem, semente e script não é reproduzível — e um
    recorte de dataset que não se reproduz não sustenta a métrica que sair dele.

    `script_file` é o `__file__` de quem chama; o caminho é gravado relativo à
    raiz para o registro continuar valendo em outra máquina.
    """
    return {
        "source_dataset": source_dataset,
        "source_version": source_version,
        "transform": transform,
        "params": params,
        "script": relative_to_project(Path(script_file)),
        "generated_at": timestamp(),
    }


def timestamp() -> str:
    """Instante local COM fuso. Instante sem fuso não é instante."""
    from datetime import datetime

    return datetime.now().astimezone().isoformat(timespec="seconds")


def write_json_report(name: str, payload: dict, subdir: str = "reports") -> Path:
    """Grava um relatório em `datasets/<subdir>/`, sempre dentro do projeto."""
    caminho = output_path(DATASETS_DIR / subdir / name)
    write_text_safe(
        caminho,
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False) + "\n",
    )
    return caminho


def require_local(path: Path) -> Path:
    path = assert_inside_project(path)
    if is_cloud_only(path):
        raise RuntimeError(f"cloud-only: leitura recusada: {relative_to_project(path)}")
    return path


def allocated_bytes(path: Path) -> int:
    """Alocação informada pelo SO, sem leitura do conteúdo."""
    if sys.platform != "win32":
        stat = path.stat()
        if not hasattr(stat, "st_blocks"):
            raise RuntimeError("SO não informa alocação; medição indisponível")
        return stat.st_blocks * 512
    function = _allocation_function()
    high = ctypes.c_ulong()
    ctypes.set_last_error(0)
    low = function(str(path), ctypes.byref(high))
    if low == 0xFFFFFFFF and ctypes.get_last_error():
        raise ctypes.WinError(ctypes.get_last_error())
    return (high.value << 32) | low


@lru_cache(maxsize=1)
def _allocation_function():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    function = kernel.GetCompressedFileSizeW
    function.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_ulong)]
    function.restype = ctypes.c_ulong
    return function


def output_path(path: Path) -> Path:
    path = assert_inside_project(path)
    if path.parent == DATASETS_DIR and path.name in (
        "README.md",
        "STATUS.md",
        "README.md.tmp",
        "STATUS.md.tmp",
    ):
        return path
    allowed = [
        DATASETS_DIR / name
        for name in (
            "reports",
            "manifests",
            "splits",
            "metadata",
            "processed",
            "annotations",
        )
    ]
    if not any(base in path.parents for base in allowed):
        raise OutsideProjectError(
            "escrita permitida apenas nas pastas derivadas de datasets; raw é read-only"
        )
    return path


def write_text_safe(path: Path, text: str) -> None:
    """Escrita atômica com orçamento verificado antes de criar o temporário."""
    path = output_path(path)
    data = text.encode("utf-8")
    if len(data) > 128_000_000:
        raise BudgetError("artefato excede 128 MB; exige novo planejamento de pico")
    from _budget import preflight

    reserved = ((len(data) + 4095) // 4096 + 1) * 4096
    preflight("escrita de artefato derivado", reserved, reserved, raise_on_block=True)
    if free_disk_bytes() - len(data) < 10_000_000_000:
        raise BudgetError("escrita reduziria margem livre abaixo de 10 GB")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path(path.with_name(path.name + ".tmp"))
    with temporary.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def configure_stdout() -> None:
    """Console do Windows abre em cp1252 e quebraria nos acentos dos relatórios."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
