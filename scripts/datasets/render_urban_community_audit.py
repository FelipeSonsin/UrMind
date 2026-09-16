"""Auditoria visual do Urban Community: o que existe DENTRO das caixas.

A pergunta que nenhuma contagem responde: as 478 caixas de `pothole` contêm
buraco? O pacote não traz `data.yaml`, o nome da pasta é a única fonte do
rótulo, e nome de pasta não é evidência. A única forma de conferir é olhar.

Produz dois artefatos:

    contact_sheet_<pasta>.png   grade de recortes das caixas, para julgar o
                                conteúdo de muitas de uma vez
    panel_<pasta>__<arquivo>.png  imagem inteira com as caixas desenhadas

A seleção é determinística (ordenada por área), estratificada entre caixas
grandes, medianas e pequenas. O script NÃO registra veredito: quem olha decide,
e a decisão vai para a folha de auditoria.

    python -B scripts/datasets/render_urban_community_audit.py
    python -B scripts/datasets/render_urban_community_audit.py --folder cracks
"""

from __future__ import annotations

import argparse
import json

from _budget import preflight
from _core import (
    DATASETS_DIR,
    PROJECT_ROOT,
    configure_stdout,
    file_sha256,
    output_path,
    provenance,
    require_local,
    write_json_report,
)

SCAN_MANIFEST = DATASETS_DIR / "manifests" / "urban_community_scan.jsonl"
OUTPUT_DIR = DATASETS_DIR / "processed" / "urban_community" / "visual_audit"
REPORT_NAME = "urban_community_visual_audit.json"

BOX_COLOR = (255, 64, 0)
CELL = 224


def load_scan() -> list[dict]:
    return [
        json.loads(line)
        for line in require_local(SCAN_MANIFEST).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def pick(rows: list[dict], folder: str, count: int) -> list[tuple[dict, dict]]:
    """Recortes estratificados por área: grandes, medianas e pequenas."""
    caixas = [
        (row, box)
        for row in rows
        if row["folder"] == folder
        for box in row["boxes"]
    ]
    caixas.sort(key=lambda par: -par[1]["area_fraction"])
    if not caixas:
        return []
    terco = max(1, count // 3)
    meio = len(caixas) // 2
    selecao = caixas[:terco] + caixas[meio : meio + terco] + caixas[-terco:]
    vistos: set[tuple[str, int]] = set()
    saida = []
    for row, box in selecao:
        chave = (row["stem"], box["index"])
        if chave in vistos:
            continue
        vistos.add(chave)
        saida.append((row, box))
    return saida[:count]


def crop_cell(row: dict, box: dict, margin: float = 0.15):
    """Recorte da caixa com folga, para o contexto aparecer junto do objeto."""
    from PIL import Image

    with Image.open(require_local(PROJECT_ROOT / row["image_relpath"])) as handle:
        image = handle.convert("RGB")
    largura, altura = image.size
    dx = int((box["xmax"] - box["xmin"]) * margin)
    dy = int((box["ymax"] - box["ymin"]) * margin)
    caixa = (
        max(0, box["xmin"] - dx),
        max(0, box["ymin"] - dy),
        min(largura, box["xmax"] + dx),
        min(altura, box["ymax"] + dy),
    )
    if caixa[2] <= caixa[0] or caixa[3] <= caixa[1]:
        return None
    return image.crop(caixa).resize((CELL, CELL), Image.Resampling.LANCZOS)


def contact_sheet(rows: list[dict], folder: str, count: int, columns: int):
    from PIL import Image, ImageDraw

    escolhidos = pick(rows, folder, count)
    celulas = []
    for row, box in escolhidos:
        recorte = crop_cell(row, box)
        if recorte is None:
            continue
        desenho = ImageDraw.Draw(recorte)
        desenho.rectangle([0, 0, CELL - 1, CELL - 1], outline=BOX_COLOR, width=3)
        desenho.text((6, 6), f"{row['stem']}#{box['index']}", fill=(255, 255, 0))
        celulas.append((recorte, row, box))

    if not celulas:
        return None, []
    linhas = (len(celulas) + columns - 1) // columns
    folha = Image.new("RGB", (columns * CELL, linhas * CELL), (18, 18, 18))
    for indice, (celula, _, _) in enumerate(celulas):
        folha.paste(celula, ((indice % columns) * CELL, (indice // columns) * CELL))
    return folha, celulas


def draw_panel(row: dict, scale: float = 0.6):
    from PIL import Image, ImageDraw

    with Image.open(require_local(PROJECT_ROOT / row["image_relpath"])) as handle:
        image = handle.convert("RGB")
    desenho = ImageDraw.Draw(image)
    for box in row["boxes"]:
        desenho.rectangle(
            [box["xmin"], box["ymin"], box["xmax"], box["ymax"]], outline=BOX_COLOR, width=4
        )
    alvo = (int(image.width * scale), int(image.height * scale))
    return image.resize(alvo, Image.Resampling.LANCZOS)


AUDIT_SHEET = DATASETS_DIR / "annotations" / "urban_community_box_audit_v1.jsonl"
SHEET_DIR = OUTPUT_DIR / "box_audit"
DUP_SHEET = DATASETS_DIR / "annotations" / "urban_community_duplicate_audit_v1.jsonl"
DUP_DIR = OUTPUT_DIR / "duplicates"


def render_sheet_panels(scale: float = 0.6) -> list[dict]:
    """Um painel por linha da folha de auditoria humana.

    A folha pedia que o revisor abrisse o contact sheet da pasta inteira e
    procurasse a caixa certa entre 48 recortes — o que é um convite a decidir
    pela lembrança do recorte errado. Aqui cada linha ganha a imagem inteira,
    com a caixa em julgamento destacada em laranja e as outras caixas da mesma
    imagem em cinza, que é o que permite julgar `annotation_incomplete`: só se
    vê o que ficou de fora olhando o quadro todo.

    O script renderiza e nada mais. Nenhuma decisão é escrita.
    """
    from PIL import Image, ImageDraw

    if not AUDIT_SHEET.is_file():
        return []

    linhas = [
        json.loads(line)
        for line in require_local(AUDIT_SHEET).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    por_imagem: dict[str, list[dict]] = {}
    for linha in linhas:
        por_imagem.setdefault(linha["image_id"], []).append(linha)

    SHEET_DIR.mkdir(parents=True, exist_ok=True)
    gerados = []
    for linha in linhas:
        caminho = require_local(PROJECT_ROOT / linha["image_relpath"])
        with Image.open(caminho) as handle:
            imagem = handle.convert("RGB")
        desenho = ImageDraw.Draw(imagem)

        # Contexto primeiro, alvo por cima: as outras caixas da imagem ficam
        # discretas, e a que está sendo julgada fica inconfundível.
        for outra in por_imagem[linha["image_id"]]:
            if outra["box_id"] == linha["box_id"]:
                continue
            desenho.rectangle(outra["bbox_xyxy"], outline=(120, 120, 120), width=2)
        desenho.rectangle(linha["bbox_xyxy"], outline=BOX_COLOR, width=4)
        desenho.text((8, 8), linha["box_id"], fill=(255, 255, 0))

        # JPEG a 60%: a decisão é "isto é uma cavidade?", e essa pergunta não
        # depende de pixel sem perda. Em PNG na escala original os 50 painéis
        # ocupavam 122 MB — caro demais para material de revisão.
        alvo = (int(imagem.width * scale), int(imagem.height * scale))
        saida = SHEET_DIR / f"{linha['box_id'].replace('#', '_box')}.jpg"
        imagem.resize(alvo, Image.Resampling.LANCZOS).save(
            saida, format="JPEG", quality=85, optimize=True
        )
        gerados.append(
            {
                "box_id": linha["box_id"],
                "panel": saida.relative_to(PROJECT_ROOT).as_posix(),
                "bytes": saida.stat().st_size,
                "boxes_in_image": linha["boxes_in_image"],
                "strata": linha["strata"],
            }
        )
    return gerados


def render_duplicate_panels(scale: float = 0.5) -> list[dict]:
    """Painel lado a lado para cada par dHash 0.

    dHash igual significa hash perceptual igual, não "a mesma foto". Duas cenas
    distintas com enquadramento e textura parecidos colidem. Quem decide precisa
    ver as duas imagens juntas, no mesmo painel — comparar de memória, abrindo um
    arquivo de cada vez, é como o erro entra.
    """
    from PIL import Image, ImageDraw

    if not DUP_SHEET.is_file():
        return []

    pares = [
        json.loads(line)
        for line in require_local(DUP_SHEET).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    DUP_DIR.mkdir(parents=True, exist_ok=True)
    gerados = []
    for par in pares:
        if not (par.get("a_image_relpath") and par.get("b_image_relpath")):
            continue
        lados = []
        for lado in ("a", "b"):
            with Image.open(require_local(PROJECT_ROOT / par[f"{lado}_image_relpath"])) as handle:
                imagem = handle.convert("RGB")
            alvo = (int(imagem.width * scale), int(imagem.height * scale))
            lados.append(imagem.resize(alvo, Image.Resampling.LANCZOS))

        altura = max(l.height for l in lados)
        largura = sum(l.width for l in lados) + 12
        painel = Image.new("RGB", (largura, altura + 26), (18, 18, 18))
        x = 0
        for lado, imagem in zip(("a", "b"), lados):
            painel.paste(imagem, (x, 26))
            ImageDraw.Draw(painel).text(
                (x + 6, 6), f"{lado}={par[lado]}  ({par[f'{lado}_boxes']} caixas)", fill=(255, 255, 0)
            )
            x += imagem.width + 12
        ImageDraw.Draw(painel).text(
            (largura - 150, 6), f"dHash={par['dhash_distance']}", fill=(255, 120, 0)
        )

        saida = DUP_DIR / f"pair_{par['a']}__{par['b']}.jpg"
        painel.save(saida, format="JPEG", quality=88, optimize=True)
        gerados.append(
            {
                "pair_id": par["pair_id"],
                "panel": saida.relative_to(PROJECT_ROOT).as_posix(),
                "bytes": saida.stat().st_size,
            }
        )
    return gerados


def main() -> int:
    configure_stdout()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", action="append", default=None)
    parser.add_argument("--crops", type=int, default=48)
    parser.add_argument("--columns", type=int, default=8)
    parser.add_argument("--panels", type=int, default=3)
    parser.add_argument("--no-report", action="store_true")
    parser.add_argument(
        "--duplicate-panels",
        action="store_true",
        help=(
            "renderiza um painel lado a lado por par dHash 0, para a decisão de "
            "duplicata ser tomada olhando as duas cenas juntas."
        ),
    )
    parser.add_argument(
        "--audit-panels",
        action="store_true",
        help=(
            "renderiza um painel por linha da folha de auditoria humana, para "
            "que a revisão caixa a caixa possa ser feita olhando o quadro inteiro."
        ),
    )
    args = parser.parse_args()

    rows = load_scan()
    pastas = args.folder or sorted({row["folder"] for row in rows})

    if args.duplicate_panels:
        preflight("painéis de duplicata", 10_000_000, 10_000_000, raise_on_block=True)
        paineis = render_duplicate_panels()
        print(
            f"Painéis de duplicata: {DUP_DIR.relative_to(PROJECT_ROOT)} "
            f"({len(paineis)} arquivos, {sum(p['bytes'] for p in paineis) / 1e6:.2f} MB)"
        )
        print("  status: PENDING_HUMAN_DUPLICATE_REVIEW — nenhuma decisão registrada")
        return 0

    if args.audit_panels:
        # Modo dedicado à revisão humana: só os painéis da folha, nada mais.
        preflight("painéis da auditoria humana", 15_000_000, 15_000_000, raise_on_block=True)
        paineis = render_sheet_panels()
        bytes_paineis = sum(p["bytes"] for p in paineis)
        print(
            f"Painéis por caixa: {SHEET_DIR.relative_to(PROJECT_ROOT)} "
            f"({len(paineis)} arquivos, {bytes_paineis / 1e6:.2f} MB)"
        )
        print("  nenhuma decisão foi registrada: revisar é trabalho de pessoa")
        return 0

    preflight(
        "auditoria visual do Urban Community",
        len(pastas) * 6_000_000,
        len(pastas) * 6_000_000,
        raise_on_block=True,
    )

    gerados: dict = {}
    total_bytes = 0
    for pasta in pastas:
        folha, celulas = contact_sheet(rows, pasta, args.crops, args.columns)
        entradas = []
        if folha is not None:
            destino = output_path(OUTPUT_DIR / f"contact_sheet_{pasta}.png")
            destino.parent.mkdir(parents=True, exist_ok=True)
            folha.save(destino, format="PNG", optimize=True)
            total_bytes += destino.stat().st_size
            entradas.append(
                {
                    "kind": "contact_sheet",
                    "path": destino.relative_to(PROJECT_ROOT).as_posix(),
                    "crops": len(celulas),
                    "cells": [
                        {
                            "stem": r["stem"],
                            "index": b["index"],
                            "class_id": b["class_id"],
                            "area_fraction": b["area_fraction"],
                        }
                        for _, r, b in celulas
                    ],
                }
            )

        com_caixa = [r for r in rows if r["folder"] == pasta and r["boxes"]]
        com_caixa.sort(key=lambda r: -len(r["boxes"]))
        for row in com_caixa[: args.panels]:
            painel = draw_panel(row)
            destino = output_path(OUTPUT_DIR / f"panel_{pasta}__{row['stem']}.png")
            destino.parent.mkdir(parents=True, exist_ok=True)
            painel.save(destino, format="PNG", optimize=True)
            total_bytes += destino.stat().st_size
            entradas.append(
                {
                    "kind": "panel",
                    "path": destino.relative_to(PROJECT_ROOT).as_posix(),
                    "stem": row["stem"],
                    "boxes": len(row["boxes"]),
                }
            )
        gerados[pasta] = entradas
        print(f"  {pasta:<16} {len(celulas)} recortes + {len(entradas) - 1} painéis")

    report = {
        "version": 1,
        "scope": (
            "evidência visual do conteúdo das caixas do Urban Community. Não "
            "registra veredito: olhar é trabalho de quem revisa."
        ),
        **provenance(
            __file__,
            source_dataset="urban_community",
            source_version="kaggle-2025",
            transform="renderização de recortes e painéis para auditoria visual",
            params={
                "folders": pastas,
                "crops_per_folder": args.crops,
                "columns": args.columns,
                "panels_per_folder": args.panels,
                "selection": "determinística por área; grandes, medianas e pequenas",
            },
        ),
        "inputs": len(rows),
        "outputs": sum(len(v) for v in gerados.values()),
        "dropped": 0,
        "drop_reasons": {},
        "integrity": {"scan_manifest_sha256": file_sha256(SCAN_MANIFEST)},
        "raw_modified": False,
        "output_dir": OUTPUT_DIR.relative_to(PROJECT_ROOT).as_posix(),
        "total_bytes": total_bytes,
        "artifacts": gerados,
        "review_status": "PENDENTE — nenhum humano registrou revisão destes recortes",
    }

    if args.no_report:
        print(json.dumps({k: len(v) for k, v in gerados.items()}, indent=2))
        return 0

    destino = write_json_report(REPORT_NAME, report)
    print(f"\nSaída: {OUTPUT_DIR.relative_to(PROJECT_ROOT)} ({total_bytes / 1e6:.2f} MB)")
    print(f"Relatório: {destino.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
