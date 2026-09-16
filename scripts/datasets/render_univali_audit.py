"""Auditoria VISUAL da conversão: imagem + máscara + caixa, lado a lado.

Um script que termina sem exception não provou que a caixa está no lugar certo.
Este gera painéis para olho humano, escolhidos para cobrir os casos onde a
conversão erraria de formas diferentes:

    simple          uma região, tamanho mediano
    multi_region    várias regiões desconectadas na mesma imagem
    smallest        a menor região do dataset
    largest         a maior região do dataset
    border          região encostando na borda do quadro
    low_fill        região diagonal/alongada: a caixa cobre muito fundo
    empty_mask      máscara sem região anotada (nenhuma caixa deve sair)
    crack_overlap   POTHOLE que divide pixels com CRACK

Cada painel traz: RAW | máscara POTHOLE | RAW com caixas | RAW com máscara e
caixas sobrepostas. As imagens são reduzidas e gravadas em
`datasets/processed/univali_br/visual_audit/`, que é pasta derivada — os
originais não são copiados nem alterados.

    python -B scripts/datasets/render_univali_audit.py
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

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

BOXES_MANIFEST = DATASETS_DIR / "manifests" / "univali_br_boxes.jsonl"
SCAN_MANIFEST = DATASETS_DIR / "manifests" / "univali_br_mask_scan.jsonl"
AUDIT_SHEET = DATASETS_DIR / "annotations" / "univali_instance_audit_v1.jsonl"
OUTPUT_DIR = DATASETS_DIR / "processed" / "univali_br" / "visual_audit"
REPORT_NAME = "univali_visual_audit.json"

BOX_COLOR = (255, 64, 0)
MASK_COLOR = (0, 200, 255)


def choose_samples(rows: list[dict], scan_rows: dict, per_case: int) -> dict:
    """Seleção determinística por caso, sem sorteio."""
    cases: dict[str, list[dict]] = defaultdict(list)

    # Desde que as máscaras vazias passaram a ser preservadas, o manifesto tem
    # linha sem caixa nenhuma — 1.671 delas, com `mask_status` de semântica não
    # resolvida. `min()`/`max()` sobre uma lista vazia levanta ValueError, então
    # o ranking por área só pode ser calculado sobre quem tem caixa. As vazias
    # não somem: elas têm caso próprio (`empty_mask`), montado a partir do scan.
    com_caixa = [r for r in rows if r["boxes"]]

    by_smallest = sorted(
        com_caixa, key=lambda r: min(b["mask_area_px"] for b in r["boxes"])
    )
    by_largest = sorted(
        com_caixa, key=lambda r: -max(b["mask_area_px"] for b in r["boxes"])
    )
    by_fill = sorted(com_caixa, key=lambda r: min(b["fill_ratio"] for b in r["boxes"]))

    cases["smallest"] = by_smallest[:per_case]
    cases["largest"] = by_largest[:per_case]
    cases["low_fill"] = by_fill[:per_case]
    cases["multi_region"] = sorted(
        (r for r in com_caixa if len(r["boxes"]) >= 3), key=lambda r: -len(r["boxes"])
    )[:per_case]
    cases["border"] = [
        r for r in com_caixa if any(b["touches_border"] for b in r["boxes"])
    ][:per_case]
    cases["simple"] = [
        r
        for r in com_caixa
        if len(r["boxes"]) == 1
        and r["boxes"][0]["size_tier"] in ("medium_small", "medium_large")
        and not r["boxes"][0]["touches_border"]
    ][:per_case]

    empty = [
        directory
        for directory, row in scan_rows.items()
        if row["masks"].get("POTHOLE", {}).get("present")
        and not row["masks"]["POTHOLE"].get("foreground_px")
        and not row.get("image_error")
    ]
    cases["empty_mask"] = [
        {"directory": d, "boxes": [], "from_scan": True} for d in empty[:per_case]
    ]
    return dict(cases)


def audit_panel_rows(rows: list[dict], audit_rows: list[dict]) -> list[dict]:
    """Resolve a folha inteira para imagens do manifesto, sem decidir semântica."""
    wanted = {row["image_id"] for row in audit_rows}
    by_directory = {row["directory"]: row for row in rows}
    missing = sorted(wanted - set(by_directory))
    if missing:
        raise RuntimeError(
            "folha humana referencia imagens ausentes do manifesto: "
            + ", ".join(missing)
        )
    return [by_directory[directory] for directory in sorted(wanted)]


def render_panel(row: dict, scan_row: dict, output: Path, scale: float) -> dict:
    from PIL import Image, ImageDraw

    directory = row["directory"]
    base = DATASETS_DIR / "raw" / "univali_br" / "v1" / directory
    image_path = require_local(base / f"{directory}_RAW.jpg")
    mask_path = require_local(base / f"{directory}_POTHOLE.png")

    with Image.open(image_path) as handle:
        raw = handle.convert("RGB")
    with Image.open(mask_path) as handle:
        mask = handle.convert("L")

    width, height = raw.size
    boxes = row.get("boxes", [])

    mask_rgb = Image.merge(
        "RGB",
        (
            mask.point(lambda v: MASK_COLOR[0] if v else 0),
            mask.point(lambda v: MASK_COLOR[1] if v else 0),
            mask.point(lambda v: MASK_COLOR[2] if v else 0),
        ),
    )

    with_boxes = raw.copy()
    draw = ImageDraw.Draw(with_boxes)
    for box in boxes:
        draw.rectangle(
            [box["xmin"], box["ymin"], box["xmax"] - 1, box["ymax"] - 1],
            outline=BOX_COLOR,
            width=3,
        )

    overlay = Image.blend(raw, mask_rgb, 0.45)
    draw = ImageDraw.Draw(overlay)
    for box in boxes:
        draw.rectangle(
            [box["xmin"], box["ymin"], box["xmax"] - 1, box["ymax"] - 1],
            outline=BOX_COLOR,
            width=3,
        )

    panels = [raw, mask_rgb, with_boxes, overlay]
    target = (int(width * scale), int(height * scale))
    panels = [p.resize(target, Image.Resampling.LANCZOS) for p in panels]

    sheet = Image.new("RGB", (target[0] * 2, target[1] * 2), (18, 18, 18))
    for index, panel in enumerate(panels):
        sheet.paste(panel, ((index % 2) * target[0], (index // 2) * target[1]))

    # Mesma disciplina das demais escritas derivadas: o destino é conferido
    # contra as pastas permitidas e o espaço é reservado antes de gravar.
    output = output_path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output, format="PNG", optimize=True)

    return {
        "directory": directory,
        "panel": output.relative_to(PROJECT_ROOT).as_posix(),
        "panel_bytes": output.stat().st_size,
        "image_size": [width, height],
        "boxes": len(boxes),
        "box_details": [
            {
                "xyxy": [b["xmin"], b["ymin"], b["xmax"], b["ymax"]],
                "mask_area_px": b["mask_area_px"],
                "fill_ratio": b["fill_ratio"],
                "size_tier": b["size_tier"],
                "touches_border": b["touches_border"],
            }
            for b in boxes
        ],
        "mask_foreground_px": scan_row["masks"]["POTHOLE"].get("foreground_px")
        if scan_row
        else None,
        "layout": "cima-esq RAW | cima-dir máscara | baixo-esq caixas | baixo-dir sobreposição",
    }


def main() -> int:
    configure_stdout()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--per-case", type=int, default=2)
    parser.add_argument("--scale", type=float, default=0.5)
    parser.add_argument("--no-report", action="store_true")
    parser.add_argument(
        "--audit-panels",
        action="store_true",
        help="renderiza todas as imagens citadas pela folha humana, sem decidir nada",
    )
    args = parser.parse_args()

    rows = [
        json.loads(line)
        for line in require_local(BOXES_MANIFEST)
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    scan_rows = {
        json.loads(line)["directory"]: json.loads(line)
        for line in require_local(SCAN_MANIFEST)
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    }

    if args.audit_panels:
        audit_rows = [
            json.loads(line)
            for line in require_local(AUDIT_SHEET)
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip()
        ]
        cases = {"human_audit": audit_panel_rows(rows, audit_rows)}
    else:
        cases = choose_samples(rows, scan_rows, args.per_case)

    # Painel de 4 quadrantes a 50% de 1024x640 ≈ 1 MB no pior caso. Reserva o
    # pico antes de gravar qualquer coisa, como o resto do projeto faz.
    expected = sum(len(members) for members in cases.values())
    reserva = expected * 1_500_000
    preflight("auditoria visual do UNIVALI", reserva, reserva, raise_on_block=True)

    rendered: dict = {}
    total_bytes = 0

    for case, members in cases.items():
        rendered[case] = []
        for row in members:
            directory = row["directory"]
            output = OUTPUT_DIR / f"{case}__{directory}.png"
            detail = render_panel(row, scan_rows.get(directory), output, args.scale)
            detail["case"] = case
            rendered[case].append(detail)
            total_bytes += detail["panel_bytes"]
            print(f"  {case:<14} {directory}  ({detail['boxes']} caixas)")

    report = {
        "version": 1,
        "scope": (
            "auditoria visual da conversão máscara→caixa. Painéis para revisão "
            "humana; não substitui a validação automática e não valida semântica."
        ),
        **provenance(
            __file__,
            source_dataset="univali_br",
            source_version="mendeley-v4",
            transform="renderização de painéis de auditoria visual",
            params={
                "per_case": args.per_case,
                "scale": args.scale,
                "cases": sorted(cases),
                "selection": "determinística por ordenação, sem sorteio",
            },
        ),
        "inputs": len(rows),
        "outputs": sum(len(v) for v in rendered.values()),
        "dropped": 0,
        "drop_reasons": {},
        "integrity": {"boxes_manifest_sha256": file_sha256(BOXES_MANIFEST)},
        "raw_modified": False,
        "output_dir": OUTPUT_DIR.relative_to(PROJECT_ROOT).as_posix(),
        "total_bytes": total_bytes,
        "cases": rendered,
        "how_to_read": (
            "Em cada painel a máscara (ciano) precisa estar dentro da caixa "
            "(laranja) e a caixa precisa envolver a região apertadamente. Caixa "
            "sem máscara dentro, máscara fora da caixa ou caixa cobrindo o quadro "
            "inteiro invalidam a conversão, mesmo que o script tenha terminado sem erro."
        ),
        "review_status": "PENDENTE — nenhum humano registrou revisão destes painéis",
    }

    if args.no_report:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0

    destination = write_json_report(REPORT_NAME, report)
    print(
        f"\nPainéis: {OUTPUT_DIR.relative_to(PROJECT_ROOT)} ({total_bytes / 1e6:.2f} MB)"
    )
    print(f"Relatório: {destination.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
