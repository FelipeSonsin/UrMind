"""Extrai features reais de cada imagem Norway do RDD2022 para a amostragem estratificada.

Nao modifica nenhum arquivo. Le o XML VOC oficial e decodifica a imagem em
resolucao reduzida (JPEG draft) para medir iluminacao, saturacao e contraste.
Saida: datasets/reports/norway_profile.jsonl
"""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
NORWAY = ROOT / "datasets/raw/rdd2022/RDD2022/Norway"
OUT = ROOT / "datasets/reports/norway_profile.jsonl"

V1 = ("D00", "D10", "D20", "D40")


def _boxes(xml_path: Path) -> tuple[list[dict], int, int, bool]:
    tree = ET.parse(xml_path)
    root = tree.getroot()
    size = root.find("size")
    w = int(float(size.findtext("width") or 0))
    h = int(float(size.findtext("height") or 0))
    out: list[dict] = []
    degenerate = False
    for obj in root.findall("object"):
        name = (obj.findtext("name") or "").strip()
        bb = obj.find("bndbox")
        if bb is None:
            continue
        try:
            x0 = float(bb.findtext("xmin"))
            y0 = float(bb.findtext("ymin"))
            x1 = float(bb.findtext("xmax"))
            y1 = float(bb.findtext("ymax"))
        except (TypeError, ValueError):
            degenerate = True
            continue
        if x1 <= x0 or y1 <= y0:
            degenerate = True
            continue
        out.append(
            {
                "name": name,
                "area_frac": ((x1 - x0) * (y1 - y0)) / (w * h) if w and h else 0.0,
                "cy_frac": ((y0 + y1) / 2) / h if h else 0.0,
                "difficult": (obj.findtext("difficult") or "0").strip() == "1",
            }
        )
    return out, w, h, degenerate


def _photometry(img_path: Path) -> dict:
    with Image.open(img_path) as im:
        im.draft("RGB", (256, 256))  # decodificacao reduzida no proprio JPEG
        im = im.convert("RGB").resize((96, 54))
        px = list(im.getdata())
    n = len(px)
    lum = [0.299 * r + 0.587 * g + 0.114 * b for r, g, b in px]
    mean = sum(lum) / n
    var = sum((v - mean) ** 2 for v in lum) / n
    sat = 0.0
    for r, g, b in px:
        mx, mn = max(r, g, b), min(r, g, b)
        sat += 0.0 if mx == 0 else (mx - mn) / mx
    # fracao do quadro que e ceu/neve muito claro e fracao muito escura
    bright = sum(1 for v in lum if v > 200) / n
    dark = sum(1 for v in lum if v < 50) / n
    return {
        "brightness": round(mean, 3),
        "contrast": round(var**0.5, 3),
        "saturation": round(sat / n, 4),
        "bright_frac": round(bright, 4),
        "dark_frac": round(dark, 4),
    }


def _one(stem: str) -> dict | None:
    img = NORWAY / "train/images" / f"{stem}.jpg"
    xml = NORWAY / "train/annotations/xmls" / f"{stem}.xml"
    if not img.is_file() or not xml.is_file():
        return None
    try:
        boxes, w, h, degenerate = _boxes(xml)
        photo = _photometry(img)
    except Exception as exc:  # noqa: BLE001 - anomalia vira registro, nao crash
        return {"stem": stem, "error": f"{type(exc).__name__}: {exc}"}
    v1 = [b for b in boxes if b["name"] in V1]
    other = sorted({b["name"] for b in boxes if b["name"] not in V1})
    rec = {
        "stem": stem,
        "index": int(stem.split("_")[1]),
        "bytes": img.stat().st_size + xml.stat().st_size,
        "image_bytes": img.stat().st_size,
        "width": w,
        "height": h,
        "n_objects": len(v1),
        "classes": sorted({b["name"] for b in v1}),
        "class_counts": {c: sum(1 for b in v1 if b["name"] == c) for c in V1},
        "other_labels": other,
        "degenerate": degenerate,
        "mean_area_frac": round(sum(b["area_frac"] for b in v1) / len(v1), 6) if v1 else 0.0,
        "min_area_frac": round(min((b["area_frac"] for b in v1), default=0.0), 6),
        "mean_cy": round(sum(b["cy_frac"] for b in v1) / len(v1), 4) if v1 else 0.0,
        "n_difficult": sum(1 for b in v1 if b["difficult"]),
        "negative": not boxes,
    }
    rec.update(photo)
    return rec


def main() -> int:
    stems = sorted(p.stem for p in (NORWAY / "train/images").glob("*.jpg"))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    done = 0
    with OUT.open("w", encoding="utf-8") as fh, ProcessPoolExecutor() as pool:
        for rec in pool.map(_one, stems, chunksize=32):
            if rec is None:
                continue
            fh.write(json.dumps(rec) + "\n")
            done += 1
            if done % 500 == 0:
                print(f"{done}/{len(stems)}", flush=True)
    print(f"perfil concluido: {done} imagens -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
