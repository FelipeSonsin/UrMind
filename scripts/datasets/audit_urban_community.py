"""Reanálise das sete pastas do Urban Community Issues, pasta a pasta.

O pacote foi julgado uma vez: `pothole` entrou como reforço de treino e as
outras seis foram recusadas. Este script refaz o exame com o dado na mão, para
que a recusa seja uma conclusão conferível e não uma decisão herdada.

Para cada pasta ele mede conteúdo, quantidade, formato, qualidade da anotação e
compara com o motivo da recusa registrado em `app.ml.taxonomy`. Ele **não
decide** aproveitamento: produz a evidência que sustenta ou derruba cada recusa.

O que nenhum número aqui resolve: o pacote não traz `data.yaml`, então o mapa
id→classe continua inferido das pastas. Consistência interna (uma classe por
pasta) é evidência de organização, não da intenção de quem publicou.

    python -B scripts/datasets/audit_urban_community.py
"""

from __future__ import annotations

import argparse
import json
import zipfile
from collections import Counter

from _core import (
    DATASETS_DIR,
    PROJECT_ROOT,
    configure_stdout,
    file_sha256,
    is_cloud_only,
    provenance,
    write_json_report,
    write_text_safe,
)

RAW_ROOT = DATASETS_DIR / "raw" / "urban_community"
PACKAGE = RAW_ROOT / "urban-community-issues.zip"
SCAN_MANIFEST = DATASETS_DIR / "manifests" / "urban_community_scan.jsonl"
REPORT_NAME = "urban_community_audit.json"

# Motivos registrados em `backend/app/ml/taxonomy.py` quando a fonte foi julgada.
# Ficam aqui para que a reanálise confronte a recusa em vez de repeti-la.
PREVIOUS_DECISION = {
    "pothole": ("ACEITA", "única categoria mapeada; entra como reforço de URMIND_ROAD_D40"),
    "cracks": (
        "RECUSADA",
        "trinca genérica em classe única; não distingue D00/D10/D20 (§8.2)",
    ),
    "open_manhole": (
        "RECUSADA",
        "bueiro aberto só vira URMIND_MANHOLE com dataset e protocolo próprios (§8.2)",
    ),
    "good_road": (
        "RECUSADA",
        "classe negativa do pacote original; não é dano nem objeto da V1",
    ),
    "animal": ("RECUSADA", "fora do escopo funcional do UrMind"),
    "traffic_lights": (
        "RECUSADA",
        "semáforo é sinalização vertical; URMIND_SIGNAGE exige dataset próprio (§8.2)",
    ),
    "waste_container": ("RECUSADA", "fora do escopo funcional do UrMind"),
}

# Natureza da recusa. A distinção importa: recusa de ESCOPO não é reversível por
# mais dado nem por melhor formato — só por mudança de taxonomia, que é decisão
# de produto. Recusa de FORMATO/QUALIDADE seria reversível por trabalho técnico.
REFUSAL_KIND = {
    "cracks": "taxonomia: a V1 não tem classe de trinca genérica",
    "open_manhole": "taxonomia: URMIND_MANHOLE é classe futura bloqueada",
    "good_road": "não é objeto: são imagens sem anotação",
    "animal": "escopo: fora do domínio funcional",
    "traffic_lights": "taxonomia: URMIND_SIGNAGE é classe futura bloqueada",
    "waste_container": "escopo: fora do domínio funcional",
}


def find_base():
    base = RAW_ROOT / "Data_sets"
    if (base / "Data_sets").is_dir():
        base = base / "Data_sets"
    if not base.is_dir():
        raise SystemExit(f"{base}: pacote não extraído")
    return base


def parse_label(text: str) -> tuple[list[dict], list[str]]:
    """Lê um .txt YOLO. Linha inválida é reportada, nunca silenciada."""
    caixas, problemas = [], []
    for numero, linha in enumerate(text.splitlines(), start=1):
        partes = linha.split()
        if not partes:
            continue
        if len(partes) != 5:
            problemas.append(f"linha {numero}: {len(partes)} campos, esperado 5")
            continue
        try:
            classe = int(float(partes[0]))
            cx, cy, w, h = (float(v) for v in partes[1:])
        except ValueError:
            problemas.append(f"linha {numero}: campo não numérico")
            continue
        caixas.append({"class_id": classe, "cx": cx, "cy": cy, "w": w, "h": h})
    return caixas, problemas


def box_quality(box: dict, width: int, height: int) -> list[str]:
    """Defeitos de uma caixa YOLO normalizada, em pixels reais da imagem."""
    problemas = []
    if not all(0.0 <= v <= 1.0 for v in (box["cx"], box["cy"], box["w"], box["h"])):
        problemas.append("coordenada fora de [0,1]")
    if box["w"] <= 0 or box["h"] <= 0:
        problemas.append("largura ou altura não positiva")
    x1 = (box["cx"] - box["w"] / 2) * width
    y1 = (box["cy"] - box["h"] / 2) * height
    x2 = (box["cx"] + box["w"] / 2) * width
    y2 = (box["cy"] + box["h"] / 2) * height
    if x1 < -1 or y1 < -1 or x2 > width + 1 or y2 > height + 1:
        problemas.append("caixa extrapola a imagem")
    if (x2 - x1) < 2 or (y2 - y1) < 2:
        problemas.append("caixa com menos de 2 px de lado")
    if box["w"] * box["h"] > 0.95:
        problemas.append("caixa cobre quase o quadro inteiro")
    return problemas


def audit_folder(folder, base) -> tuple[dict, list[dict]]:
    from PIL import Image

    label_dir, image_dir = folder / "labels", folder / "images"
    if not (label_dir.is_dir() and image_dir.is_dir()):
        return {"error": "pasta sem images/ ou labels/"}, []

    imagens = sorted(p for p in image_dir.iterdir() if p.is_file())
    rotulos = sorted(p for p in label_dir.glob("*.txt"))
    stems_img = {p.stem for p in imagens}
    stems_lbl = {p.stem for p in rotulos}

    class_ids: Counter = Counter()
    problemas_formato: list[str] = []
    problemas_qualidade: Counter = Counter()
    areas: list[float] = []
    aspect: list[float] = []
    tamanhos: Counter = Counter()
    caixas_por_imagem: Counter = Counter()
    vazios = 0
    total_caixas = 0
    linhas_scan: list[dict] = []
    erros_imagem: list[dict] = []

    for rotulo in rotulos:
        stem = rotulo.stem
        imagem = next((p for p in imagens if p.stem == stem), None)
        if imagem is None:
            continue
        try:
            with Image.open(imagem) as handle:
                handle.load()
                width, height = handle.size
        except Exception as exc:  # noqa: BLE001 - defeito da fonte é dado
            erros_imagem.append({"file": imagem.name, "error": f"{type(exc).__name__}: {exc}"})
            continue

        caixas, defeitos = parse_label(rotulo.read_text(encoding="utf-8", errors="replace"))
        problemas_formato.extend(f"{rotulo.name}: {d}" for d in defeitos)
        if not caixas:
            vazios += 1
        caixas_por_imagem[len(caixas)] += 1
        tamanhos[(width, height)] += 1

        detalhes = []
        for indice, caixa in enumerate(caixas):
            total_caixas += 1
            class_ids[caixa["class_id"]] += 1
            areas.append(caixa["w"] * caixa["h"])
            aspect.append(caixa["w"] / caixa["h"] if caixa["h"] else 0.0)
            defeitos_caixa = box_quality(caixa, width, height)
            for defeito in defeitos_caixa:
                problemas_qualidade[defeito] += 1
            detalhes.append(
                {
                    "index": indice,
                    "class_id": caixa["class_id"],
                    "cx": caixa["cx"],
                    "cy": caixa["cy"],
                    "w": caixa["w"],
                    "h": caixa["h"],
                    "xmin": round((caixa["cx"] - caixa["w"] / 2) * width),
                    "ymin": round((caixa["cy"] - caixa["h"] / 2) * height),
                    "xmax": round((caixa["cx"] + caixa["w"] / 2) * width),
                    "ymax": round((caixa["cy"] + caixa["h"] / 2) * height),
                    "area_fraction": round(caixa["w"] * caixa["h"], 6),
                    "defects": defeitos_caixa,
                }
            )

        linhas_scan.append(
            {
                "folder": folder.name,
                "stem": stem,
                "image_relpath": imagem.relative_to(PROJECT_ROOT).as_posix(),
                "label_relpath": rotulo.relative_to(PROJECT_ROOT).as_posix(),
                "image_width": width,
                "image_height": height,
                "boxes": detalhes,
            }
        )

    decisao, motivo = PREVIOUS_DECISION.get(folder.name, ("DESCONHECIDA", ""))
    resumo = {
        "content": folder.name,
        "images": len(imagens),
        "labels": len(rotulos),
        "annotation_format": "yolo_txt normalizado (class_id cx cy w h)",
        "boxes": total_caixas,
        "empty_label_files": vazios,
        "class_ids": dict(sorted(class_ids.items())),
        "single_class_id": len(class_ids) <= 1,
        "boxes_per_image": dict(sorted(caixas_por_imagem.items())),
        "image_sizes_top": {f"{w}x{h}": n for (w, h), n in tamanhos.most_common(5)},
        "distinct_image_sizes": len(tamanhos),
        "labels_without_image": sorted(stems_lbl - stems_img)[:10],
        "images_without_label": sorted(stems_img - stems_lbl)[:10],
        "unreadable_images": erros_imagem,
        "format_problems": problemas_formato[:10],
        "format_problem_count": len(problemas_formato),
        "quality_problems": dict(problemas_qualidade),
        "box_area_fraction": {
            "mean": round(sum(areas) / len(areas), 5) if areas else None,
            "min": round(min(areas), 6) if areas else None,
            "max": round(max(areas), 6) if areas else None,
        },
        "previous_decision": decisao,
        "previous_reason": motivo,
        "refusal_kind": REFUSAL_KIND.get(folder.name),
    }
    return resumo, linhas_scan


def package_contents(*, authorized: bool = False) -> dict:
    """Lista o que o ZIP oficial traz sem extrair nada — e só sob autorização.

    O catálogo registra que o pacote acompanha pesos `.pt` de um treino de
    terceiros. Duas coisas diferentes já são conhecidas sem abrir nada:

    1. nenhum código do pipeline referencia esses pesos — busca por `.pt` em
       `backend/app` e `scripts/` não encontra uso —, então eles são artefato
       externo desnecessário para o funcionamento atual;
    2. peso de treino de terceiros não é modelo do UrMind e não entra em
       `model_versions` (§9) mesmo que estivesse em uso.

    Nada disso exige inspecionar 786 MB, e por isso a inspeção **não acontece
    sozinha**. Ela é opt-in por `--inspect-package`, e continua bloqueada se o
    ZIP for marcador do OneDrive, caso em que abri-lo dispararia download. Um
    script de auditoria não hidrata pacote em silêncio: a verificação fica
    registrada como PENDENTE, que é a informação verdadeira.
    """
    if not PACKAGE.is_file():
        return {"present": False}

    nuvem = is_cloud_only(PACKAGE)
    base = {
        "present": True,
        "is_cloud_only": nuvem,
        "third_party_weights_used_by_pipeline": False,
        "pipeline_usage_evidence": (
            "busca por `.pt` em backend/app e scripts/: nenhuma referência de "
            "carregamento. Os pesos são artefato externo, não requisito do "
            "funcionamento atual."
        ),
        "weights_note": (
            "Peso de treino de terceiros não é modelo do UrMind e não entra em "
            "model_versions (§9). Fica no pacote original, sem uso."
        ),
    }
    if nuvem:
        return {
            **base,
            "inspected": False,
            "verification": "PENDING_CLOUD_ONLY",
            "reason": (
                "ZIP é marcador do OneDrive; abrir hidrataria o pacote. A pasta "
                "extraída já está local e é o que esta auditoria mede."
            ),
        }
    if not authorized:
        return {
            **base,
            "inspected": False,
            "verification": "PENDING_EXPLICIT_AUTHORIZATION",
            "reason": (
                "inspeção do ZIP não é automática: exige `--inspect-package`. O "
                "arquivo está local agora, mas depender do estado de hidratação "
                "do momento faria a auditoria oscilar entre abrir e não abrir 786 "
                "MB sem ninguém decidir."
            ),
        }
    with zipfile.ZipFile(PACKAGE) as pacote:
        nomes = pacote.namelist()
    extensoes = Counter(("." + n.rsplit(".", 1)[-1].lower()) if "." in n else "<sem>" for n in nomes)
    pesos = [n for n in nomes if n.lower().endswith((".pt", ".pth", ".onnx", ".weights"))]
    return {
        **base,
        "inspected": True,
        "verification": "INSPECTED_UNDER_EXPLICIT_AUTHORIZATION",
        "entries": len(nomes),
        "extensions": dict(extensoes.most_common(10)),
        "third_party_weights": pesos,
        "third_party_weights_count": len(pesos),
    }


def main() -> int:
    configure_stdout()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-report", action="store_true")
    parser.add_argument(
        "--inspect-package",
        action="store_true",
        help=(
            "abre o índice do ZIP oficial para listar os pesos .pt de terceiros. "
            "Opt-in: sem esta opção a verificação fica registrada como pendente e "
            "nenhum byte do pacote é lido."
        ),
    )
    args = parser.parse_args()

    base = find_base()
    pastas = sorted(p for p in base.iterdir() if p.is_dir())
    print(f"Auditando {len(pastas)} pastas em {base.relative_to(PROJECT_ROOT)}")

    por_pasta: dict[str, dict] = {}
    scan: list[dict] = []
    for pasta in pastas:
        resumo, linhas = audit_folder(pasta, base)
        por_pasta[pasta.name] = resumo
        scan.extend(linhas)
        print(
            f"  {pasta.name:<16} {resumo.get('images', 0):>4} imagens | "
            f"{resumo.get('boxes', 0):>4} caixas | {resumo.get('previous_decision')}"
        )

    ids_por_pasta = {n: list(v.get("class_ids", {})) for n, v in por_pasta.items()}
    conflitos = [n for n, ids in ids_por_pasta.items() if len(ids) > 1]
    duplicado = [
        ident
        for ident, quantas in Counter(i for ids in ids_por_pasta.values() for i in ids).items()
        if quantas > 1
    ]

    report = {
        "version": 1,
        "scope": (
            "reanálise das sete pastas do Urban Community: conteúdo, quantidade, "
            "formato, qualidade e confronto com a recusa anterior. Não decide "
            "aproveitamento e não converte nada."
        ),
        **provenance(
            __file__,
            source_dataset="urban_community",
            source_version="kaggle-2025",
            transform="auditoria por pasta com medição de qualidade de anotação",
            params={"folders": [p.name for p in pastas]},
        ),
        "inputs": sum(v.get("images", 0) for v in por_pasta.values()),
        "outputs": len(scan),
        "dropped": 0,
        "drop_reasons": {},
        "integrity": {
            "package_sha256_local": (
                file_sha256(PACKAGE)
                if PACKAGE.is_file() and not is_cloud_only(PACKAGE)
                else None
            ),
            "package_is_cloud_only": PACKAGE.is_file() and is_cloud_only(PACKAGE),
            "official_checksum": None,
            "official_checksum_note": (
                "O Kaggle não publica checksum deste pacote. O SHA-256 acima é "
                "local: prova que o arquivo não mudou aqui, não que corresponde à "
                "publicação original."
            ),
            "scan_manifest": SCAN_MANIFEST.relative_to(PROJECT_ROOT).as_posix(),
        },
        "raw_modified": False,
        "provenance": {
            "homepage": "https://www.kaggle.com/datasets/rajeevpaudel1/urban-community-issues",
            "author": "Rajeev Paudel",
            "license_declared": "CC0 na ficha do Kaggle",
            "image_origin_declared": False,
            "limitation": (
                "A ficha declara CC0 do pacote; a origem e os direitos de cada "
                "imagem não são declarados. Registrar isso em qualquer resultado "
                "publicado é obrigação, não formalidade."
            ),
        },
        "class_id_map": {
            "source": "inferido das pastas; o pacote não traz data.yaml",
            "ids_per_folder": ids_por_pasta,
            "one_id_per_folder": not conflitos,
            "folders_mixing_ids": conflitos,
            "ids_shared_between_folders": duplicado,
            "verdict": (
                "consistência interna sustenta a inferência"
                if not conflitos and not duplicado
                else "inconsistência: a inferência não se sustenta"
            ),
            "still_not_official": True,
        },
        "package": package_contents(authorized=args.inspect_package),
        "folders": por_pasta,
    }

    if args.no_report:
        print(json.dumps(report["folders"], indent=2, ensure_ascii=False))
        return 0

    write_text_safe(
        SCAN_MANIFEST, "\n".join(json.dumps(linha, ensure_ascii=False) for linha in scan) + "\n"
    )
    report["integrity"]["scan_manifest_sha256"] = file_sha256(SCAN_MANIFEST)
    destino = write_json_report(REPORT_NAME, report)
    print(f"\nVarredura: {SCAN_MANIFEST.relative_to(PROJECT_ROOT)}")
    print(f"Relatório: {destino.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
