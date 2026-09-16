"""Validação da derivada do Urban Community: geometria, duplicatas, contaminação.

Esta fonte é reforço de TREINO. O risco que importa não é ela medir mal — ela
nunca mede nada —, é ela **contaminar quem mede**: se uma imagem daqui for a
mesma que está no teste do RDD2022 ou no holdout brasileiro do UNIVALI, o
detector treina no que depois vai ser avaliado, e a métrica sobe sozinha.

Por isso a verificação cruzada é fail-closed contra as duas fontes de avaliação.
Ausência de comparação não é ausência de contaminação.

    python -B scripts/datasets/validate_urban_community.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import yaml
from _core import (
    DATASETS_DIR,
    PROJECT_ROOT,
    configure_stdout,
    file_sha256,
    is_cloud_only,
    provenance,
    require_local,
    write_json_report,
)

# A regra que aplica a decisão de duplicata mora no módulo de autorização. O
# validador a importa em vez de reescrevê-la: duas implementações da mesma
# decisão foram exatamente o defeito que deixou `b` treinar depois de `keep_a`.
sys.path.insert(0, str(PROJECT_ROOT / "backend"))
from app.datasets.authorization import (
    DUPLICATE_DIFFERENT,
    DUPLICATE_KEPT,
    DUPLICATE_PENDING,
    DUPLICATE_REJECTED,
    resolve_duplicate_pair,
)

BOXES_MANIFEST = DATASETS_DIR / "manifests" / "urban_community_boxes.jsonl"
SCAN_MANIFEST = DATASETS_DIR / "manifests" / "urban_community_scan.jsonl"
CONVERSION_REPORT = DATASETS_DIR / "reports" / "urban_community_conversion.json"
AUDIT_REPORT = DATASETS_DIR / "reports" / "urban_community_audit.json"
AUDIT_SHEET = DATASETS_DIR / "annotations" / "urban_community_box_audit_v1.jsonl"
DUP_SHEET = DATASETS_DIR / "annotations" / "urban_community_duplicate_audit_v1.jsonl"
HUMAN_STATUS = DATASETS_DIR / "reports" / "urban_community_human_audit_status.json"
RDD_INVENTORY = DATASETS_DIR / "manifests" / "rdd2022_inventory.jsonl"
ARTIFACT_REGISTRY = DATASETS_DIR / "metadata" / "artifact_registry.json"
ARTIFACT_CONTRACT = DATASETS_DIR / "metadata" / "artifact_contract.yaml"
UNIVALI_BOXES = DATASETS_DIR / "manifests" / "univali_br_boxes.jsonl"
REPORT_NAME = "urban_community_validation.json"

# Inventários sem os quais a verificação de contaminação não significa nada.
REQUIRED_INVENTORIES = ("rdd2022", "univali_br")

ALLOWED_CLASSES = {"URMIND_ROAD_D40", None}
DHASH_THRESHOLD = 4
RDD_AUTHORITY_KEY = "rdd2022_cross_source_reference"
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}


def _project_relative(path: Path) -> str:
    return path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()


def _official_image_count(path: Path) -> int:
    """Conta imagens na lista nominal oficial, sem consultar o inventário."""
    stack: list[str] = []
    images = 0
    for line in require_local(path).read_text(encoding="utf-8-sig").splitlines():
        directory = re.match(r"^([| ]*)(?:\+---|\\---)(.+)$", line)
        if directory:
            depth = len(directory[1]) // 4
            stack = stack[:depth] + [directory[2]]
            continue
        if not stack:
            continue
        name = re.sub(r"^[| ]+", "", line).strip()
        if name and Path(name).suffix.lower() in _IMAGE_SUFFIXES:
            images += 1
    return images


def _rdd_authority(path: Path) -> tuple[dict, str | None]:
    """Resolve a autoridade independente e valida todos os vínculos estáveis."""
    contract = (
        yaml.safe_load(require_local(ARTIFACT_CONTRACT).read_text(encoding="utf-8"))
        or {}
    )
    authority = contract.get(RDD_AUTHORITY_KEY)
    if not isinstance(authority, dict):
        return {}, f"contrato sem `{RDD_AUTHORITY_KEY}`"

    required = (
        "dataset_id",
        "version",
        "population_id",
        "source_definition",
        "source_definition_sha256",
        "source_index",
        "source_index_sha256",
        "expected_records",
        "inventory",
        "inventory_sha256",
    )
    missing = [field for field in required if not authority.get(field)]
    if missing:
        return authority, "autoridade RDD2022 incompleta: " + ", ".join(missing)
    source_definition = PROJECT_ROOT / authority["source_definition"]
    definition_sha = file_sha256(require_local(source_definition))
    if definition_sha != authority["source_definition_sha256"]:
        return (
            authority,
            "fingerprint da definição versionada RDD2022 diverge do contrato",
        )
    definition = json.loads(
        require_local(source_definition).read_text(encoding="utf-8-sig")
    )
    if (
        definition.get("name") != authority["dataset_id"]
        or definition.get("version") != authority["version"]
    ):
        return authority, "dataset_id ou versão diverge da definição versionada RDD2022"

    inventory_relative = _project_relative(path)
    if authority["inventory"] != inventory_relative:
        return authority, "caminho completo do inventário diverge do contrato"

    source_index = PROJECT_ROOT / authority["source_index"]
    source_sha = file_sha256(require_local(source_index))
    if source_sha != authority["source_index_sha256"]:
        return authority, "fingerprint da lista nominal oficial diverge do contrato"
    source_count = _official_image_count(source_index)
    if source_count != authority["expected_records"]:
        return authority, "cardinalidade declarada diverge da lista nominal oficial"

    authority = {
        **authority,
        "source_definition_actual_sha256": definition_sha,
        "source_index_actual_sha256": source_sha,
        "source_index_records": source_count,
    }
    return authority, None


def dhash128(path: Path) -> tuple[int, float]:
    """Mesmo dHash do resto do projeto; números comparáveis entre fontes."""
    from PIL import Image, ImageStat

    with Image.open(path) as image:
        image.load()
        gray = image.convert("L")
        stddev = round(ImageStat.Stat(gray).stddev[0], 4)
        horizontal = list(gray.resize((9, 8), Image.Resampling.BILINEAR).getdata())
        vertical = list(gray.resize((8, 9), Image.Resampling.BILINEAR).getdata())
        bits = 0
        for y in range(8):
            for x in range(8):
                bits = (bits << 1) | int(
                    horizontal[y * 9 + x] > horizontal[y * 9 + x + 1]
                )
        for y in range(8):
            for x in range(8):
                bits = (bits << 1) | int(
                    vertical[y * 8 + x] > vertical[(y + 1) * 8 + x]
                )
    return bits, stddev


def hamming_pairs(hashes: dict[str, int], threshold: int) -> list[dict]:
    bands = threshold + 1
    widths = [128 // bands + int(i < 128 % bands) for i in range(bands)]
    buckets: dict = defaultdict(list)
    pairs, seen = [], set()
    for name, value in hashes.items():
        offset = 0
        candidates: set[str] = set()
        keys = []
        for index, width in enumerate(widths):
            key = (index, (value >> offset) & ((1 << width) - 1))
            keys.append(key)
            candidates.update(buckets[key])
            offset += width
        for other in candidates:
            distance = (value ^ hashes[other]).bit_count()
            if distance <= threshold:
                par = tuple(sorted((name, other)))
                if par not in seen:
                    seen.add(par)
                    pairs.append({"a": par[0], "b": par[1], "distance": distance})
        for key in keys:
            buckets[key].append(name)
    return pairs


def _load_reference(path: Path) -> tuple[set[str], list[int], int]:
    """SHA-256 e dHash de uma fonte de avaliação, sem abrir imagem nenhuma."""
    shas: set[str] = set()
    hashes: list[int] = []
    linhas = 0
    for line in require_local(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        linhas += 1
        registro = json.loads(line)
        if registro.get("sha256"):
            shas.add(registro["sha256"])
        if registro.get("dhash128"):
            hashes.append(int(registro["dhash128"], 16))
    return shas, hashes, linhas


def cross_source_check(
    name: str,
    path: Path,
    shas: set[str],
    hashes: dict[str, int],
    threshold: int,
    *,
    skip: bool,
) -> dict:
    """Compara contra uma fonte de avaliação. Fail-closed, como no UNIVALI."""
    base = {
        "reference": name,
        "compared_against": path.relative_to(PROJECT_ROOT).as_posix(),
        "skipped": True,
        "valid": False,
        "inventory_sha256": None,
        "expected_records": None,
        "inventory_records": 0,
        "hashable_records": 0,
        "unreadable_records": 0,
        "cloud_only_records": 0,
        "completeness_ratio": 0.0,
        "completeness_status": "UNVERIFIED",
        "authority_dataset_id": None,
        "authority_version": None,
        "population_id": None,
        "source_index": None,
        "source_index_sha256": None,
        "threshold": threshold,
    }
    if skip:
        return {**base, "reason": "--skip-cross-source foi usado"}
    if not path.is_file():
        return {**base, "reason": "manifesto de referência não existe"}
    try:
        authority, authority_error = _rdd_authority(path)
        registry = json.loads(
            require_local(ARTIFACT_REGISTRY).read_text(encoding="utf-8-sig")
        )
        expected = authority.get("expected_records")
        inventory_relative = _project_relative(path)
        registered = next(
            (
                item
                for item in registry.get("artifacts", [])
                if item.get("path") == inventory_relative
            ),
            None,
        )
        current_sha = file_sha256(require_local(path))
        records = [
            json.loads(line)
            for line in require_local(path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (
        OSError,
        RuntimeError,
        json.JSONDecodeError,
        ValueError,
        StopIteration,
    ) as exc:
        return {**base, "reason": f"manifesto de referência ilegível: {exc}"}
    if not records:
        return {**base, "reason": "manifesto de referência vazio"}

    cloud_only = sum(
        "cloud_only_not_read" in record.get("image_errors", []) for record in records
    )
    unreadable = sum(
        bool(record.get("image_errors"))
        and "cloud_only_not_read" not in record.get("image_errors", [])
        for record in records
    )
    hashable = [
        record for record in records if record.get("sha256") and record.get("dhash128")
    ]
    count = len(records)
    ratio = (
        round(count / expected, 8)
        if isinstance(expected, int) and expected > 0
        else 0.0
    )
    measured = {
        **base,
        "inventory_sha256": current_sha,
        "expected_records": expected,
        "inventory_records": count,
        "hashable_records": len(hashable),
        "unreadable_records": unreadable,
        "cloud_only_records": cloud_only,
        "completeness_ratio": ratio,
        "authority_dataset_id": authority.get("dataset_id"),
        "authority_version": authority.get("version"),
        "population_id": authority.get("population_id"),
        "source_index": authority.get("source_index"),
        "source_index_sha256": authority.get("source_index_actual_sha256"),
    }
    if authority_error:
        return {**measured, "reason": authority_error}
    if not isinstance(expected, int) or expected <= 0:
        return {**measured, "reason": "cardinalidade autoritativa ausente ou inválida"}
    if authority.get("inventory_sha256") != current_sha:
        return {
            **measured,
            "reason": "fingerprint do inventário diverge da autoridade versionada",
        }
    if (
        registered is None
        or registered.get("sha256") != current_sha
        or registered.get("size_bytes") != path.stat().st_size
    ):
        return {
            **measured,
            "reason": "registro exato do inventário diverge do artifact_registry.json",
        }
    if count != expected:
        return {
            **measured,
            "completeness_status": "INCOMPLETE_CARDINALITY",
            "reason": f"inventário parcial: {count} de {expected} registros autoritativos",
        }
    if len(hashable) != count or unreadable or cloud_only:
        return {
            **measured,
            "completeness_status": "INCOMPLETE_HASH_COVERAGE",
            "reason": (
                f"cobertura de hashes incompleta: {len(hashable)} de {count}; "
                f"{unreadable} ilegíveis, {cloud_only} cloud-only"
            ),
        }

    try:
        ref_shas = {record["sha256"] for record in hashable}
        ref_hashes = [int(record["dhash128"], 16) for record in hashable]
    except (TypeError, ValueError) as exc:
        return {**measured, "reason": f"hash inválido no inventário: {exc}"}

    exatas = sorted(set(shas) & ref_shas)
    minimo, abaixo = 128, 0
    if ref_hashes:
        for valor in hashes.values():
            melhor = min((valor ^ outro).bit_count() for outro in ref_hashes)
            minimo = min(minimo, melhor)
            if melhor <= threshold:
                abaixo += 1
    contaminado = bool(exatas or abaixo)
    return {
        **measured,
        "reference": name,
        "compared_against": path.relative_to(PROJECT_ROOT).as_posix(),
        "skipped": False,
        "valid": not contaminado,
        "completeness_status": "COMPLETE",
        "reference_records": count,
        "reference_images_with_hash": len(ref_hashes),
        "exact_sha256_matches": len(exatas),
        "nearest_dhash_distance": minimo if ref_hashes else None,
        "images_within_threshold": abaixo,
        "threshold": threshold,
        "reason": None if not contaminado else "contaminação detectada",
        "verdict": (
            f"sem contaminação detectável entre urban_community e {name}"
            if not contaminado
            else f"CONTAMINAÇÃO contra {name}: treino tocaria dado de avaliação"
        ),
    }


def _rel(path: Path) -> str:
    try:
        return path.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def univali_hashes(path: Path) -> dict:
    """SHA-256 e dHash das imagens do UNIVALI, com a conta do que NÃO foi medido.

    O manifesto cita caminho, não hash, então o cálculo é feito aqui — e cada
    imagem que não pôde entrar é contada pelo motivo. Antes o laço só pulava a
    ausente e seguia: com todas ausentes, a lista de hashes saía vazia e o
    `min()` da comparação estourava; com algumas ausentes, a comparação saía
    "limpa" sem ter olhado parte do holdout.

    Marcador do OneDrive NUNCA é aberto: `read_bytes()` dispararia o download.
    """
    inventario = {
        "records": 0,
        "hashed": 0,
        "missing": 0,
        "cloud_only": 0,
        "unreadable": 0,
        "shas": set(),
        "hashes": [],
    }
    for line in require_local(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        inventario["records"] += 1
        registro = json.loads(line)
        imagem = PROJECT_ROOT / registro["image_relpath"]
        if not imagem.is_file():
            inventario["missing"] += 1
            continue
        if is_cloud_only(imagem):
            inventario["cloud_only"] += 1
            continue
        try:
            sha = hashlib.sha256(imagem.read_bytes()).hexdigest()
            valor, _ = dhash128(imagem)
        except (OSError, ValueError):
            inventario["unreadable"] += 1
            continue
        inventario["shas"].add(sha)
        inventario["hashes"].append(valor)
        inventario["hashed"] += 1
    return inventario


def univali_cross_check(
    path: Path, shas: set[str], hashes: dict[str, int], threshold: int, *, skip: bool
) -> dict:
    """Contaminação contra o holdout brasileiro. Fail-closed, sem estourar.

    Inventário vazio ou incompleto NÃO é "0 matches": é comparação que não
    aconteceu, ou aconteceu contra parte do conjunto. As duas situações reprovam
    com a causa registrada — e nenhuma delas levanta exceção, porque um crash não
    produz relatório e sem relatório ninguém sabe o que faltou.
    """
    base = {
        "reference": "univali_br",
        "compared_against": _rel(path),
        "skipped": True,
        "valid": False,
        "inventory_sha256": None,
        "threshold": threshold,
    }
    if skip:
        return {**base, "status": "SKIPPED", "reason": "--skip-cross-source foi usado"}
    if not path.is_file():
        return {
            **base,
            "status": "MANIFEST_MISSING",
            "reason": "manifesto de referência não existe",
        }
    if is_cloud_only(path):
        return {
            **base,
            "status": "MANIFEST_CLOUD_ONLY",
            "reason": "o próprio manifesto é marcador do OneDrive; abri-lo o hidrataria",
        }

    try:
        inventario = univali_hashes(path)
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        return {
            **base,
            "status": "MANIFEST_UNREADABLE",
            "reason": f"manifesto ilegível: {exc}",
        }

    contagem = {
        "reference_records": inventario["records"],
        "reference_images_with_hash": inventario["hashed"],
        "reference_images_missing": inventario["missing"],
        "reference_images_cloud_only": inventario["cloud_only"],
        "reference_images_unreadable": inventario["unreadable"],
    }
    causas = (
        f"{inventario['missing']} ausente(s), {inventario['cloud_only']} cloud-only "
        f"não hidratada(s), {inventario['unreadable']} ilegível(is)"
    )
    if not inventario["records"]:
        return {
            **base,
            **contagem,
            "status": "EMPTY_MANIFEST",
            "reason": "manifesto de referência vazio",
        }
    if not inventario["hashed"]:
        return {
            **base,
            **contagem,
            "status": "EMPTY_HASH_INVENTORY",
            "reason": (
                f"nenhuma das {inventario['records']} imagens pôde ser medida ({causas}). "
                "Comparação não executada: isto não é ausência de contaminação."
            ),
        }
    if inventario["hashed"] < inventario["records"]:
        return {
            **base,
            **contagem,
            "status": "INCOMPLETE_HASH_INVENTORY",
            "reason": (
                f"{inventario['hashed']} de {inventario['records']} imagens medidas "
                f"({causas}). Uma imagem não medida do holdout pode ser justamente a "
                "contaminada, então a verificação parcial não aprova."
            ),
        }

    exatas = sorted(set(shas) & inventario["shas"])
    minimo, abaixo = 128, 0
    for valor in hashes.values():
        melhor = min((valor ^ outro).bit_count() for outro in inventario["hashes"])
        minimo = min(minimo, melhor)
        if melhor <= threshold:
            abaixo += 1
    contaminado = bool(exatas or abaixo)
    return {
        **base,
        **contagem,
        "skipped": False,
        "valid": not contaminado,
        "status": "CONTAMINATED" if contaminado else "OK",
        "inventory_sha256": file_sha256(path),
        "exact_sha256_matches": len(exatas),
        "nearest_dhash_distance": minimo if hashes else None,
        "images_within_threshold": abaixo,
        "reason": None if not contaminado else "contaminação detectada",
        "verdict": (
            "sem contaminação detectável entre urban_community e univali_br"
            if not contaminado
            else "CONTAMINAÇÃO contra o holdout brasileiro"
        ),
    }


_RESOLVED_PAIRS = {
    (DUPLICATE_KEPT, DUPLICATE_REJECTED),
    (DUPLICATE_REJECTED, DUPLICATE_KEPT),
    (DUPLICATE_DIFFERENT, DUPLICATE_DIFFERENT),
}


def blocking_duplicate_pairs(
    identicas: list[dict], dup_rows: list[dict], rows: list[dict], scan_sha: str | None
) -> list[dict]:
    """Pares dHash 0 que continuam bloqueando, cada um com o motivo.

    Um par só deixa de bloquear quando as duas coisas são verdade: há decisão
    humana válida e vinculada ao scan auditado, E o manifesto derivado já aplica
    essa decisão. A segunda condição é a que faltava — `same_scene_keep_a`
    riscava o par aqui e deixava `b` no manifesto com o portão aberto.
    """
    por_par = {linha.get("pair_id"): linha for linha in dup_rows}
    por_stem = {str(row["stem"]): row for row in rows}
    caminhos = {stem: row["image_relpath"] for stem, row in por_stem.items()}

    bloqueantes = []
    for par in identicas:
        a, b = str(par["a"]), str(par["b"])
        linha = por_par.get(f"{a}__{b}")
        if linha is None:
            bloqueantes.append(
                {**par, "reason": "par sem linha na folha de duplicatas"}
            )
            continue

        estados = resolve_duplicate_pair(linha, scan_sha=scan_sha, image_paths=caminhos)
        if estados not in _RESOLVED_PAIRS:
            bloqueantes.append(
                {**par, "reason": f"decisão não resolve o par: {estados}"}
            )
            continue

        aplicada = True
        for stem, esperado in zip((a, b), estados, strict=True):
            atual = por_stem.get(stem, {}).get("duplicate_review_status")
            if esperado == DUPLICATE_REJECTED:
                aplicada &= atual == DUPLICATE_REJECTED
            else:
                # O lado mantido pode estar bloqueado por OUTRO par — isso é mais
                # restritivo, não menos. O que não pode é a conversão não ter
                # visto este par (NOT_IN_PAIR) ou tê-lo visto com outro vínculo.
                aplicada &= atual in {
                    DUPLICATE_KEPT,
                    DUPLICATE_DIFFERENT,
                    DUPLICATE_REJECTED,
                    DUPLICATE_PENDING,
                }
        if not aplicada:
            bloqueantes.append(
                {
                    **par,
                    "reason": (
                        "decisão registrada, mas o manifesto derivado ainda não a "
                        "aplica: reconverta antes de validar"
                    ),
                }
            )
    return bloqueantes


def chain_check() -> dict:
    """Confere que a derivada validada é a mesma que a conversão produziu.

    Sem isto, o relatório de validação prova apenas que *alguma* versão do
    manifesto passou: bastaria regerar a derivada depois da validação para ter um
    aprovado que não corresponde ao arquivo em disco. Aqui cada elo é recomputado
    e comparado com o que a etapa anterior registrou.

    Fail-closed: elo ausente, ilegível ou divergente reprova a cadeia. Não ter
    conferido nunca conta como ter conferido e passado.
    """
    elos: list[dict] = []

    def elo(nome: str, arquivo, esperado, *, origem: str) -> None:
        atual = file_sha256(arquivo) if arquivo.is_file() else None
        elos.append(
            {
                "link": nome,
                "file": arquivo.relative_to(PROJECT_ROOT).as_posix(),
                "recorded_in": origem,
                "recorded_sha256": esperado,
                "current_sha256": atual,
                "valid": bool(atual) and bool(esperado) and atual == esperado,
                "reason": (
                    None
                    if atual and esperado and atual == esperado
                    else (
                        "arquivo ausente"
                        if atual is None
                        else "a etapa anterior não registrou o sha256"
                        if not esperado
                        else "sha256 diverge do registrado"
                    )
                ),
            }
        )

    if not CONVERSION_REPORT.is_file():
        return {
            "valid": False,
            "reason": "relatório de conversão ausente: a cadeia não tem origem",
            "links": [],
        }
    try:
        conversao = json.loads(
            require_local(CONVERSION_REPORT).read_text(encoding="utf-8-sig")
        )
    except (OSError, RuntimeError, json.JSONDecodeError) as exc:
        return {"valid": False, "reason": f"conversão ilegível: {exc}", "links": []}

    integridade = conversao.get("integrity", {})
    origem = "datasets/reports/urban_community_conversion.json#integrity"
    elo(
        "boxes_manifest",
        BOXES_MANIFEST,
        integridade.get("boxes_manifest_sha256"),
        origem=origem,
    )
    elo(
        "scan_manifest",
        SCAN_MANIFEST,
        integridade.get("scan_manifest_sha256"),
        origem=origem,
    )
    elo(
        "audit_report",
        AUDIT_REPORT,
        integridade.get("audit_report_sha256"),
        origem=origem,
    )

    # A auditoria humana também é elo: um status gerado contra uma conversão
    # antiga descreveria a revisão de outra versão das caixas. Aconteceu — o
    # status apontava para uma conversão que já não existia em disco — e nenhum
    # validador percebeu, porque cada artefato era conferido sozinho.
    if HUMAN_STATUS.is_file():
        try:
            humano = json.loads(
                require_local(HUMAN_STATUS).read_text(encoding="utf-8-sig")
            )
        except (OSError, RuntimeError, json.JSONDecodeError) as exc:
            elos.append(
                {
                    "link": "human_audit_status",
                    "file": HUMAN_STATUS.relative_to(PROJECT_ROOT).as_posix(),
                    "valid": False,
                    "reason": f"ilegível: {exc}",
                }
            )
        else:
            humano_integridade = humano.get("integrity", {})
            origem_humana = (
                "datasets/reports/urban_community_human_audit_status.json#integrity"
            )
            elo(
                "human_audit_conversion_link",
                CONVERSION_REPORT,
                humano_integridade.get("conversion_report_sha256"),
                origem=origem_humana,
            )
            elo(
                "human_audit_boxes_link",
                BOXES_MANIFEST,
                humano_integridade.get("boxes_manifest_sha256"),
                origem=origem_humana,
            )
            elo(
                "human_audit_sheet",
                AUDIT_SHEET,
                humano_integridade.get("sheet_sha256"),
                origem=origem_humana,
            )
    else:
        elos.append(
            {
                "link": "human_audit_status",
                "file": HUMAN_STATUS.relative_to(PROJECT_ROOT).as_posix(),
                "valid": False,
                "reason": "status da auditoria humana ausente",
            }
        )

    invalidos = [e["link"] for e in elos if not e["valid"]]
    return {
        "valid": not invalidos,
        "reason": None
        if not invalidos
        else f"elos divergentes ou ausentes: {invalidos}",
        "links": elos,
        "policy": (
            "o relatório que libera esta fonte vale para os arquivos cujos hashes "
            "estão aqui, e para nenhum outro"
        ),
    }


def _human_rows() -> list[dict]:
    """Linhas da folha de auditoria humana, se ela existir. Nunca escreve nada."""
    if not AUDIT_SHEET.is_file():
        return []
    try:
        return [
            json.loads(line)
            for line in require_local(AUDIT_SHEET)
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip()
        ]
    except (OSError, RuntimeError, json.JSONDecodeError):
        return []


def _duplicate_rows() -> list[dict]:
    """Decisões humanas por par de duplicata. Ausente = tudo pendente."""
    if not DUP_SHEET.is_file():
        return []
    try:
        return [
            json.loads(line)
            for line in require_local(DUP_SHEET)
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip()
        ]
    except (OSError, RuntimeError, json.JSONDecodeError):
        return []


def main() -> int:
    configure_stdout()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dhash-threshold", type=int, default=DHASH_THRESHOLD)
    parser.add_argument("--skip-cross-source", action="store_true")
    parser.add_argument("--no-report", action="store_true")
    args = parser.parse_args()

    rows = [
        json.loads(line)
        for line in require_local(BOXES_MANIFEST)
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]

    failures: list[str] = []
    geometry_errors: list[dict] = []
    class_errors: list[dict] = []
    missing_images: list[str] = []
    hashes: dict[str, int] = {}
    stddevs: dict[str, float] = {}
    sha_index: dict[str, list[str]] = defaultdict(list)
    candidatas = quarentena = autorizadas = 0
    grupos: Counter = Counter()

    print(f"Validando {len(rows)} imagens derivadas")
    for indice, row in enumerate(rows, start=1):
        largura, altura = row["image_width"], row["image_height"]
        grupos[row["group"]] += 1
        for box in row["boxes"]:
            if box["quarantine_reasons"]:
                quarentena += 1
            else:
                candidatas += 1
            if box["training_allowed"]:
                autorizadas += 1
            if box["urmind_class"] not in ALLOWED_CLASSES:
                class_errors.append({"stem": row["stem"], "class": box["urmind_class"]})
            # A invariante central da derivada: classe atribuída sem autorização
            # é exatamente o defeito que o módulo de portões existe para impedir.
            if box["urmind_class"] is not None and not box["training_allowed"]:
                class_errors.append(
                    {
                        "stem": row["stem"],
                        "problem": "classe da V1 sem autorização de treino",
                    }
                )
            if box["training_allowed"] and box["blocking_gates"]:
                class_errors.append(
                    {"stem": row["stem"], "problem": "autorizada com portão aberto"}
                )
            problemas = []
            if not box["xmin"] < box["xmax"]:
                problemas.append("x1 < x2 violado")
            if not box["ymin"] < box["ymax"]:
                problemas.append("y1 < y2 violado")
            if not 0 <= box["xmin"] < largura:
                problemas.append("0 <= x1 < largura violado")
            if not 0 < box["xmax"] <= largura:
                problemas.append("0 < x2 <= largura violado")
            if not 0 <= box["ymin"] < altura:
                problemas.append("0 <= y1 < altura violado")
            if not 0 < box["ymax"] <= altura:
                problemas.append("0 < y2 <= altura violado")
            if problemas:
                geometry_errors.append({"stem": row["stem"], "problems": problemas})

        imagem = PROJECT_ROOT / row["image_relpath"]
        if not imagem.is_file():
            missing_images.append(row["stem"])
            continue
        sha_index[
            hashlib.sha256(require_local(imagem).read_bytes()).hexdigest()
        ].append(row["stem"])
        valor, desvio = dhash128(imagem)
        hashes[row["stem"]] = valor
        stddevs[row["stem"]] = desvio
        if indice % 100 == 0 or indice == len(rows):
            print(f"  {indice}/{len(rows)}")

    exatas = [
        {"sha256": sha, "stems": sorted(stems)}
        for sha, stems in sorted(sha_index.items())
        if len(stems) > 1
    ]
    proximas = hamming_pairs(
        {k: v for k, v in hashes.items() if stddevs.get(k, 0) >= 10},
        args.dhash_threshold,
    )
    # Distância 0 não é "parecida": é a mesma cena, possivelmente só reencodada.
    # Tratá-la como nota contradiz a própria regra que removeu as cópias byte a
    # byte — e multiplica o peso daquela cena no reforço. Bloqueia até alguém
    # olhar; `duplicate` na folha de auditoria é o caminho para resolver.
    identicas = [par for par in proximas if par["distance"] == 0]
    # A decisão tem de ser sobre o PAR. Antes bastava aprovar uma caixa qualquer
    # em cada imagem para o bloqueio sumir, sem que ninguém tivesse julgado a
    # duplicação — aprovação semântica respondendo uma pergunta que não é a dela.
    bloqueantes = blocking_duplicate_pairs(
        identicas,
        _duplicate_rows(),
        rows,
        file_sha256(SCAN_MANIFEST) if SCAN_MANIFEST.is_file() else None,
    )

    cruzadas = [
        cross_source_check(
            "rdd2022",
            RDD_INVENTORY,
            set(sha_index),
            hashes,
            args.dhash_threshold,
            skip=args.skip_cross_source,
        )
    ]
    cruzadas.append(
        univali_cross_check(
            UNIVALI_BOXES,
            set(sha_index),
            hashes,
            args.dhash_threshold,
            skip=args.skip_cross_source,
        )
    )

    cadeia = chain_check()
    if not cadeia["valid"]:
        failures.append(f"cadeia de proveniência inválida: {cadeia['reason']}")

    conferidas = {c["reference"] for c in cruzadas if c["valid"]}
    for obrigatoria in REQUIRED_INVENTORIES:
        if obrigatoria not in conferidas:
            failures.append(
                f"inventário obrigatório '{obrigatoria}' não conferido: a "
                "verificação de contaminação NÃO está aprovada"
            )
    for cruzada in cruzadas:
        if not cruzada["valid"]:
            failures.append(
                f"verificação cruzada contra {cruzada['reference']} inválida: {cruzada['reason']}"
            )
    if geometry_errors:
        failures.append(f"{len(geometry_errors)} caixas violam invariantes geométricas")
    if class_errors:
        failures.append(f"{len(class_errors)} caixas com classe fora do permitido")
    if missing_images:
        failures.append(f"{len(missing_images)} imagens citadas não existem")
    if exatas:
        failures.append(f"{len(exatas)} grupos de duplicata exata dentro do reforço")
    if bloqueantes:
        failures.append(
            f"{len(bloqueantes)} par(es) de quase-duplicata com distância dHash 0 "
            "sem decisão humana POR PAR registrada em "
            "datasets/annotations/urban_community_duplicate_audit_v1.jsonl: "
            "a mesma cena entraria duas vezes no reforço"
        )

    report = {
        "version": 1,
        "scope": "validação da derivada urban_community_boxes.jsonl (reforço de treino)",
        **provenance(
            __file__,
            source_dataset="urban_community",
            source_version="kaggle-2025",
            transform="validação de geometria, classes, duplicatas e contaminação cruzada",
            params={
                "dhash_threshold": args.dhash_threshold,
                "allowed_classes": sorted(str(c) for c in ALLOWED_CLASSES),
                "cross_source_references": ["rdd2022", "univali_br"],
            },
        ),
        "inputs": len(rows),
        "outputs": len(rows) - len(missing_images),
        "dropped": 0,
        "drop_reasons": {},
        "integrity": {
            "boxes_manifest_sha256": file_sha256(BOXES_MANIFEST),
            "scan_manifest_sha256": file_sha256(SCAN_MANIFEST)
            if SCAN_MANIFEST.is_file()
            else None,
            "audit_report_sha256": file_sha256(AUDIT_REPORT)
            if AUDIT_REPORT.is_file()
            else None,
            "conversion_report_sha256": (
                file_sha256(CONVERSION_REPORT) if CONVERSION_REPORT.is_file() else None
            ),
            "provenance_chain": cadeia,
            "required_inventories": list(REQUIRED_INVENTORIES),
            "required_inventories_verified": sorted(
                conferidas & set(REQUIRED_INVENTORIES)
            ),
            "cross_source_valid": all(c["valid"] for c in cruzadas),
            "cross_source_references": {
                c["reference"]: c.get("inventory_sha256") for c in cruzadas
            },
        },
        "raw_modified": False,
        "passed": not failures,
        "failures": failures,
        "counts": {
            "images": len(rows),
            "semantic_candidate_boxes": candidatas,
            "training_authorized_boxes": autorizadas,
            "quarantined_boxes": quarentena,
            "groups": dict(grupos),
        },
        "geometry": {
            "violations": geometry_errors[:20],
            "violation_count": len(geometry_errors),
        },
        "classes": {
            "allowed": sorted(str(c) for c in ALLOWED_CLASSES),
            "violations": class_errors[:20],
            "rule": (
                "nenhuma caixa carrega classe da V1 sem `training_allowed`; "
                "nenhuma é autorizada com portão aberto"
            ),
        },
        "duplicates": {
            "exact_sha256_groups": exatas,
            "near_duplicate_pairs": proximas,
            "near_duplicate_count": len(proximas),
            "identical_dhash_pairs": identicas,
            "blocking_near_duplicate_pairs": bloqueantes,
            "duplicate_review_sheet": DUP_SHEET.relative_to(PROJECT_ROOT).as_posix(),
            "duplicate_review_status": (
                "PENDING_HUMAN_DUPLICATE_REVIEW" if bloqueantes else "REVIEWED"
            ),
            "note": (
                "Quase-duplicata em conjunto de TREINO não é vazamento, é "
                "repetição: infla o peso da mesma cena sem acrescentar "
                "informação. Distância > 0 fica registrada para revisão; "
                "distância 0 BLOQUEIA, porque é a mesma cena e deixá-la passar "
                "contradiz a regra que removeu as cópias byte a byte."
            ),
        },
        "cross_source_contamination": cruzadas,
        "leakage_policy": (
            "Esta fonte só reforça treino. A garantia que ela precisa dar é não "
            "conter imagem que apareça no teste do RDD2022 nem no holdout do "
            "UNIVALI — é isso que a verificação cruzada mede."
        ),
    }

    if args.no_report:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        destino = write_json_report(REPORT_NAME, report)
        print(f"\nRelatório: {destino.relative_to(PROJECT_ROOT)}")

    print(
        f"  candidatas        : {candidatas} | quarentena: {quarentena} | "
        f"autorizadas a treinar: {autorizadas}"
    )
    print(
        f"  duplicatas exatas : {len(exatas)} | quase-duplicatas: {len(proximas)} "
        f"(dHash 0 bloqueando: {len(bloqueantes)})"
    )
    print(f"  cadeia de hashes  : {'íntegra' if cadeia['valid'] else cadeia['reason']}")
    for cruzada in cruzadas:
        print(
            f"  vs {cruzada['reference']:<12}: {cruzada.get('verdict') or cruzada['reason']}"
        )
    print(f"  resultado         : {'APROVADO' if not failures else 'FALHOU'}")
    for falha in failures:
        print(f"    - {falha}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
