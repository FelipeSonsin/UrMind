"""Sprint visual: conjuntos de desenvolvimento e avaliação offline do ONNX por vista.

Tudo aqui é **DEVELOPMENT_ONLY**: mede e escolhe modos de inferência, fusão, limiares e
NMS; nada disso pode ser reapresentado depois como avaliação final independente.
TEST, Frozen Test e o holdout UNIVALI nunca são lidos por este módulo.

Conjuntos (definição e razões em ``datasets/metadata/visual_sprint_dev_sets.json``):

- ``ird`` (CAMERA_DEV): IRD Dashcam 4K, 439 quadros com caixas da fonte. Já excluído
  de treino e de holdout futuro; dividido por índice de quadro em ``tune`` (≤ 560) e
  ``check`` (> 560), o que mantém cada grupo de quase-duplicatas conhecido de um lado só.
  É simulado em 4K nativo, 1080p e 720p (redução ``INTER_AREA``), as resoluções da foto
  do aparelho e da câmera ao vivo.
- ``rtk`` (HARD_NEGATIVE_DEV): RTK (Santa Catarina, Brasil), 352×288, máscaras da fonte
  de faixa, remendo, bueiro, tampa, poça, trinca e buraco. Só categoriza alarmes falsos
  pelo que está sob a caixa; máscara não vira instância nem rótulo D00–D40.
- ``validation`` (guarda): VALIDATION RDD2022 (China), já usada no ajuste anterior; serve
  só para ver se uma mudança derruba o que já existia.

O cache guarda, por imagem, resolução e vista, a saída bruta do ONNX acima de
``SCORE_FLOOR`` (caixas no sistema da vista e ``objectness × classe``). Qualquer perfil
com limiares ≥ ``SCORE_FLOOR`` é reproduzido exatamente por ``select_detections`` — a
mesma função do ``OnnxDetector`` — sem rodar o modelo de novo.

    python -m app.ml.camera_dev cache --set ird
    python -m app.ml.camera_dev report --output ../datasets/reports/visual_sprint/<nome>.json
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import time
import zipfile
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from app.ml.metrics import Box, GroundTruth, Prediction, evaluate, iou
from app.ml.serving import PROJECT_ROOT, decode_raw_output, select_detections, sha256_file
from app.ml.sliced_inference import (
    SlicingConfig,
    finalize_detections,
    merge_view_detections,
    offset_detections,
    plan_views,
)

CLASS_NAMES: tuple[str, ...] = (
    "URMIND_ROAD_D00",
    "URMIND_ROAD_D10",
    "URMIND_ROAD_D20",
    "URMIND_ROAD_D40",
)
ONNX_PATH = PROJECT_ROOT / "models/serving/yolox-s-model-v2-d429bde8a9bd.onnx"
ONNX_SHA256 = "d429bde8a9bd76ced404124d6fdb733914ed74688e14836200ef183f82a510e9"
INPUT_SIZE = (640, 640)
SCORE_FLOOR = 0.01
CACHE_ROOT = PROJECT_ROOT / "datasets/reports/cache/visual_sprint" / ONNX_SHA256[:12]
CACHE_OVERLAP = 0.2
CACHE_TILE = 640

IRD_ACQUISITION = PROJECT_ROOT / "datasets/reports/ird_dashcam_candidate_acquisition.json"
IRD_RAW = PROJECT_ROOT / "datasets/raw/ird_dashcam"
IRD_LABELS_ZIP_SHA256 = "c120ac89fb3d0d5fb9d6dcd8d6ed504761eb85abb1609f3af7615a319e10d2ff"
IRD_TUNE_MAX_INDEX = 560
IRD_CLASS_MAP = {0: 0, 1: 1, 2: 2, 3: 3}
"""Classe da fonte → índice D00/D10/D20/D40; 4 (SpeedBump) fica fora (0 instâncias)."""
IRD_RESOLUTIONS: dict[str, tuple[int, int]] = {
    "4k": (3840, 2160),
    "1080p": (1920, 1080),
    "720p": (1280, 720),
}

VALIDATION_MANIFEST = PROJECT_ROOT / "datasets/manifests/detection_validation_authorized.jsonl"
VALIDATION_MANIFEST_SHA256 = "aa686ab48c53bdca5f58a099d91c4697275912bdaa8eb491273b5794b7e26d7a"
RTK_SCAN = PROJECT_ROOT / "datasets/manifests/rtk_br_scan.jsonl"
RTK_SCAN_SHA256 = "d088ee07c15c5463006e3229b27b0c361d2be299f935660cb86231e632809a7c"
RTK_CLASSES = {
    0: "background",
    1: "roadAsphalt",
    2: "roadPaved",
    3: "roadUnpaved",
    4: "roadMarking",
    5: "speedBump",
    6: "catsEye",
    7: "stormDrain",
    8: "manholeCover",
    9: "patchs",
    10: "waterPuddle",
    11: "pothole",
    12: "craks",
}
RTK_DAMAGE = (11, 12)
RTK_CONFUSION = {
    4: "MARKING_CONFUSION",
    6: "MARKING_CONFUSION",
    5: "SPEED_BUMP_CONFUSION",
    7: "MANHOLE_CONFUSION",
    8: "MANHOLE_CONFUSION",
    9: "PATCH_CONFUSION",
    10: "WATER_REFLECTION_CONFUSION",
}

SMALL_OBJECT_TENSOR_PX = 32
"""GT cujo maior lado, no tensor 640 da vista global, fica abaixo disto: SMALL_OBJECT."""
QUALITY_GRID = 160
QUALITY_DARK = 60
QUALITY_BRIGHT = 225
QUALITY_BLURRY = 150
"""Mesmos limites e mesma medida de ``measureFrameQuality`` do navegador."""


class DevSetError(RuntimeError):
    """Evidência ausente ou divergente: o conjunto é recusado, nunca adivinhado."""


@dataclass(frozen=True)
class DevImage:
    set_name: str
    image_id: str
    path: Path
    sha256: str
    subset: str
    truths: tuple[tuple[int, float, float, float, float], ...]
    """Classe D00–D40 e caixa normalizada 0–1 (x0, y0, x1, y1)."""
    mask_path: Path | None = None
    mask_sha256: str | None = None


def _read_verified(path: Path, expected: str) -> bytes:
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected:
        raise DevSetError(f"SHA-256 divergente: {path.relative_to(PROJECT_ROOT).as_posix()}")
    return data


def _decode_bgr(data: bytes) -> np.ndarray:
    import cv2

    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise DevSetError("imagem não decodificável")
    return image


def ird_index(image_id: str) -> int:
    return int(image_id.rsplit("_", 1)[1])


def ird_subset(image_id: str) -> str:
    return "tune" if ird_index(image_id) <= IRD_TUNE_MAX_INDEX else "check"


def parse_yolo_labels(text: str) -> tuple[tuple[int, float, float, float, float], ...]:
    """YOLO normalizado (classe cx cy w h) → classe D00–D40 e xyxy normalizado."""
    truths = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) != 5:
            raise DevSetError(f"linha {number}: {len(parts)} campos")
        source_class = int(parts[0])
        cx, cy, w, h = (float(value) for value in parts[1:])
        if not all(math.isfinite(v) for v in (cx, cy, w, h)) or w <= 0 or h <= 0:
            raise DevSetError(f"linha {number}: caixa inválida")
        if source_class not in IRD_CLASS_MAP:
            continue
        truths.append(
            (
                IRD_CLASS_MAP[source_class],
                max(0.0, cx - w / 2),
                max(0.0, cy - h / 2),
                min(1.0, cx + w / 2),
                min(1.0, cy + h / 2),
            )
        )
    return tuple(truths)


def ird_images() -> list[DevImage]:
    acquisition = json.loads(IRD_ACQUISITION.read_text(encoding="utf-8"))
    labels_zip = IRD_RAW / "IRD-Dataset_v1.0.0_labels_bbox.zip"
    if acquisition.get("labels_zip_sha256") != IRD_LABELS_ZIP_SHA256 or (
        sha256_file(labels_zip) != IRD_LABELS_ZIP_SHA256
    ):
        raise DevSetError("ZIP de rótulos do IRD diverge do registro de aquisição")
    images = []
    with zipfile.ZipFile(labels_zip) as archive:
        for row in acquisition["images"]:
            image_id = Path(row["image"]).stem
            text = archive.read(f"labels_bbox/{image_id}.txt").decode("utf-8")
            images.append(
                DevImage(
                    set_name="ird",
                    image_id=image_id,
                    path=IRD_RAW / row["image"],
                    sha256=row["sha256"],
                    subset=ird_subset(image_id),
                    truths=parse_yolo_labels(text),
                )
            )
    if len(images) != int(acquisition["selected_images"]):
        raise DevSetError("contagem do IRD diverge do registro de aquisição")
    return images


def validation_images() -> list[DevImage]:
    if sha256_file(VALIDATION_MANIFEST) != VALIDATION_MANIFEST_SHA256:
        raise DevSetError("manifesto de VALIDATION diverge do hash usado no ajuste anterior")
    images = []
    with VALIDATION_MANIFEST.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if row["split"] != "VALIDATION" or row["authorization_status"] != (
                "VALIDATION_AUTHORIZED"
            ):
                raise DevSetError("linha fora de VALIDATION no manifesto de VALIDATION")
            width, height = float(row["image_width"]), float(row["image_height"])
            truths = tuple(
                (
                    CLASS_NAMES.index(box["canonical_class"]),
                    box["bbox"][0] / width,
                    box["bbox"][1] / height,
                    box["bbox"][2] / width,
                    box["bbox"][3] / height,
                )
                for box in row["boxes"]
            )
            path = PROJECT_ROOT / row["image_path"]
            images.append(
                DevImage(
                    set_name="validation",
                    image_id=path.stem,
                    path=path,
                    sha256=row["source_fingerprint"],
                    subset=path.parent.parent.parent.name,
                    truths=truths,
                )
            )
    return images


def rtk_images() -> list[DevImage]:
    if sha256_file(RTK_SCAN) != RTK_SCAN_SHA256:
        raise DevSetError("manifesto de scan do RTK diverge do registro de proveniência")
    images = []
    with RTK_SCAN.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            images.append(
                DevImage(
                    set_name="rtk",
                    image_id=row["sample_id"],
                    path=PROJECT_ROOT / row["image_path"],
                    sha256=row["image_sha256"],
                    subset=row["published_split"],
                    truths=(),
                    mask_path=PROJECT_ROOT / row["mask_path"],
                    mask_sha256=row["mask_sha256"],
                )
            )
    return images


SET_LOADERS = {"ird": ird_images, "validation": validation_images, "rtk": rtk_images}


def frame_quality(frame_bgr: np.ndarray) -> dict[str, float]:
    """``measureFrameQuality`` do navegador sobre a imagem na escala do modelo."""
    import cv2

    height, width = frame_bgr.shape[:2]
    ratio = min(INPUT_SIZE[0] / height, INPUT_SIZE[1] / width, 1.0)
    resized = cv2.resize(
        frame_bgr,
        (max(1, int(width * ratio)), max(1, int(height * ratio))),
        interpolation=cv2.INTER_LINEAR,
    ).astype(np.float64)
    rows = (np.arange(QUALITY_GRID) * resized.shape[0] // QUALITY_GRID).astype(int)
    cols = (np.arange(QUALITY_GRID) * resized.shape[1] // QUALITY_GRID).astype(int)
    grid = resized[rows][:, cols]
    luminance = 0.299 * grid[..., 2] + 0.587 * grid[..., 1] + 0.114 * grid[..., 0]
    center = luminance[1:-1, 1:-1]
    lap = (
        4 * center
        - luminance[:-2, 1:-1]
        - luminance[2:, 1:-1]
        - luminance[1:-1, :-2]
        - luminance[1:-1, 2:]
    )
    return {"brightness": float(luminance.mean()), "sharpness": float(lap.var())}


def quality_flags(quality: dict[str, float]) -> list[str]:
    if quality["brightness"] < QUALITY_DARK:
        return ["LOW_LIGHT"]
    if quality["brightness"] > QUALITY_BRIGHT:
        return ["OVEREXPOSED"]
    return ["BLUR"] if quality["sharpness"] < QUALITY_BLURRY else []


def _resolutions(image: DevImage, native: tuple[int, int]) -> dict[str, tuple[int, int]]:
    if image.set_name != "ird":
        return {"native": native}
    if native != IRD_RESOLUTIONS["4k"]:
        raise DevSetError(f"{image.image_id}: IRD fora de 3840×2160")
    return dict(IRD_RESOLUTIONS)


def _view_candidates(
    session: Any, frame: np.ndarray, view: tuple[int, int, int, int]
) -> tuple[np.ndarray, np.ndarray, float]:
    from app.ml.serving import _preprocess

    x0, y0, x1, y1 = view
    tensor, ratio = _preprocess(frame[y0:y1, x0:x1], INPUT_SIZE, upscale=False)
    started = time.perf_counter()
    (output,) = session.run(None, {"images": tensor})
    elapsed = (time.perf_counter() - started) * 1000
    xyxy, scores = decode_raw_output(output, ratio)
    keep = scores.max(axis=1) > SCORE_FLOOR
    return xyxy[keep], scores[keep], elapsed


TTA_RESOLUTION = "1080p"
TTA_SMALL_SIDE = 448
"""Escala menor (não ampliar): o quadro reduzido a 448 px no lado maior, padding até 640."""
TTA_KINDS: tuple[str, ...] = ("flip", "s448")
"""Espelho horizontal (não troca D00/D10: linha vertical continua vertical) e escala menor."""


def tta_view(frame: np.ndarray, kind: str) -> tuple[np.ndarray, float]:
    """Imagem transformada que vai ao modelo e o fator de escala de volta ao quadro."""
    import cv2

    height, width = frame.shape[:2]
    if kind == "flip":
        return np.ascontiguousarray(frame[:, ::-1]), 1.0
    if kind == "s448":
        scale = TTA_SMALL_SIDE / max(width, height)
        size = (round(width * scale), round(height * scale))
        return cv2.resize(frame, size, interpolation=cv2.INTER_AREA), scale
    raise DevSetError(f"TTA desconhecida: {kind}")


def tta_to_frame(detections: np.ndarray, kind: str, scale: float, width: int) -> np.ndarray:
    """Leva ``[x0, y0, x1, y1, score, classe]`` da vista TTA de volta ao quadro."""
    mapped = np.array(detections, dtype=np.float64, copy=True).reshape(-1, 6)
    if kind == "flip":
        mapped[:, [0, 2]] = width - mapped[:, [2, 0]]
    else:
        mapped[:, :4] /= scale
    return mapped


def build_tta_cache(*, limit: int | None = None) -> dict[str, Any]:
    """Vistas TTA do IRD em 1080p (a vista global já está no cache principal)."""
    import cv2
    import onnxruntime as ort  # type: ignore[import-untyped]

    if sha256_file(ONNX_PATH) != ONNX_SHA256:
        raise DevSetError("ONNX diverge do baseline congelado")
    session = ort.InferenceSession(str(ONNX_PATH), providers=["CPUExecutionProvider"])
    target = CACHE_ROOT / "ird_tta"
    target.mkdir(parents=True, exist_ok=True)
    width, height = IRD_RESOLUTIONS[TTA_RESOLUTION]
    timings: list[float] = []
    images = ird_images()[:limit]
    for image in images:
        destination = target / f"{image.image_id}.npz"
        if destination.is_file():
            continue
        original = _decode_bgr(_read_verified(image.path, image.sha256))
        frame = cv2.resize(original, (width, height), interpolation=cv2.INTER_AREA)
        arrays: dict[str, Any] = {}
        meta: dict[str, Any] = {"image_sha256": image.sha256, "size": [width, height], "views": {}}
        for kind in TTA_KINDS:
            view, scale = tta_view(frame, kind)
            boxes, scores, elapsed = _view_candidates(
                session, view, (0, 0, view.shape[1], view.shape[0])
            )
            arrays[f"{kind}/boxes"] = boxes.astype(np.float32)
            arrays[f"{kind}/scores"] = scores.astype(np.float32)
            meta["views"][kind] = {"size": [view.shape[1], view.shape[0]], "scale": scale}
            timings.append(elapsed)
        arrays["meta"] = np.frombuffer(json.dumps(meta).encode("utf-8"), dtype=np.uint8)
        temporary = destination.with_suffix(f".{os.getpid()}.tmp.npz")
        np.savez_compressed(temporary, **arrays)
        temporary.replace(destination)
    return {
        "set": "ird_tta",
        "images": len(images),
        "session_run_ms": persist_build_timings(target, timings),
    }


def persist_build_timings(target: Path, timings: Sequence[float]) -> dict[str, Any] | None:
    """Grava o resumo de ``session.run`` desta construção de cache (um arquivo por processo).

    Sem inferência nova (tudo já em cache) não há amostra e nada é gravado.
    """
    from app.ml.serving import _timing_summary

    if not timings:
        return None
    summary = {
        "source": "MEASURED_DURING_CACHE_BUILD",
        "provider": "CPUExecutionProvider",
        "logical_cpus": os.cpu_count(),
        "generated_at": datetime.now(UTC).isoformat(),
        "caveat": "máquina compartilhada durante a construção; comparar só com a mesma condição",
        **_timing_summary(list(timings)),
    }
    (target / f"_build_timings.{os.getpid()}.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def tta_detections(
    entry: CacheEntry,
    tta: dict[str, np.ndarray],
    tta_meta: dict[str, Any],
    profile: Profile,
    kinds: Sequence[str],
    *,
    include_full: bool = True,
) -> np.ndarray:
    """Vista global + vistas TTA, cada uma pela seleção do perfil, fundidas entre vistas.

    ``include_full=False`` troca a vista global pela TTA (ex.: só a escala 448).
    """
    width = entry.meta["resolutions"][TTA_RESOLUTION]["size"][0]
    per_view = [
        profile_detections(entry, TTA_RESOLUTION, replace(profile, slicing=SlicingConfig()))
    ][: 1 if include_full else 0]
    for kind in kinds:
        view_width, view_height = tta_meta["views"][kind]["size"]
        pixels = select_detections(
            tta[f"{kind}/boxes"],
            tta[f"{kind}/scores"],
            (view_height, view_width),
            class_score_thresholds=profile.thresholds,
            nms_threshold=profile.nms_threshold,
            score_threshold=0.0,
        )
        pixels = finalize_detections(pixels, max_detections=profile.max_detections)
        per_view.append(tta_to_frame(pixels, kind, tta_meta["views"][kind]["scale"], width))
    merged = merge_view_detections(
        per_view, method=profile.slicing.merge, threshold=profile.slicing.merge_threshold
    )
    return finalize_detections(merged, max_detections=profile.max_detections)


def build_cache(
    set_name: str, *, limit: int | None = None, reverse: bool = False
) -> dict[str, Any]:
    """Roda o ONNX uma vez por vista e guarda a saída acima de ``SCORE_FLOOR``."""
    import cv2
    import onnxruntime as ort  # type: ignore[import-untyped]

    if sha256_file(ONNX_PATH) != ONNX_SHA256:
        raise DevSetError("ONNX diverge do baseline congelado")
    session = ort.InferenceSession(str(ONNX_PATH), providers=["CPUExecutionProvider"])
    images = SET_LOADERS[set_name]()[:limit]
    if reverse:  # um segundo processo pode percorrer a lista pelo fim
        images = images[::-1]
    target = CACHE_ROOT / set_name
    target.mkdir(parents=True, exist_ok=True)
    planner = SlicingConfig(mode="hybrid", tile_size=CACHE_TILE, overlap=CACHE_OVERLAP)
    timings: list[float] = []
    for image in images:
        destination = target / f"{image.image_id}.npz"
        if destination.is_file():
            continue
        original = _decode_bgr(_read_verified(image.path, image.sha256))
        meta: dict[str, Any] = {"image_sha256": image.sha256, "resolutions": {}}
        arrays: dict[str, Any] = {}
        for label, (width, height) in _resolutions(
            image, (original.shape[1], original.shape[0])
        ).items():
            frame = (
                original
                if (width, height) == (original.shape[1], original.shape[0])
                else cv2.resize(original, (width, height), interpolation=cv2.INTER_AREA)
            )
            views = plan_views(width, height, planner)
            for index, view in enumerate(views):
                boxes, scores, elapsed = _view_candidates(session, frame, view)
                arrays[f"{label}/{index}/boxes"] = boxes.astype(np.float32)
                arrays[f"{label}/{index}/scores"] = scores.astype(np.float32)
                timings.append(elapsed)
            meta["resolutions"][label] = {
                "size": [width, height],
                "views": [list(view) for view in views],
                "quality": frame_quality(frame),
            }
        arrays["meta"] = np.frombuffer(json.dumps(meta).encode("utf-8"), dtype=np.uint8)
        temporary = destination.with_suffix(f".{os.getpid()}.tmp.npz")
        np.savez_compressed(temporary, **arrays)
        temporary.replace(destination)
    return {
        "set": set_name,
        "images": len(images),
        "cache_dir": target.relative_to(PROJECT_ROOT).as_posix(),
        "session_run_ms": persist_build_timings(target, timings),
    }


@dataclass
class CacheEntry:
    image: DevImage
    meta: dict[str, Any]
    arrays: dict[str, np.ndarray]


def load_cache(set_name: str, images: Sequence[DevImage] | None = None) -> list[CacheEntry]:
    images = list(images if images is not None else SET_LOADERS[set_name]())
    entries = []
    for image in images:
        path = CACHE_ROOT / set_name / f"{image.image_id}.npz"
        if not path.is_file():
            raise DevSetError(f"cache ausente: {image.image_id} (rode `cache --set {set_name}`)")
        with np.load(path) as data:
            arrays = {key: data[key] for key in data.files}
        meta = json.loads(arrays.pop("meta").tobytes().decode("utf-8"))
        if meta["image_sha256"] != image.sha256:
            raise DevSetError(f"cache de {image.image_id} é de outra imagem")
        entries.append(CacheEntry(image=image, meta=meta, arrays=arrays))
    return entries


@dataclass(frozen=True)
class Profile:
    name: str
    thresholds: tuple[float, ...]
    nms_threshold: float = 0.45
    slicing: SlicingConfig = field(default_factory=SlicingConfig)
    max_detections: int = 100
    tile_thresholds: tuple[float, ...] | None = None
    """Limiares só das vistas de tile (pós-hoc); ``None`` = os mesmos da vista global."""

    def fields(self) -> dict[str, Any]:
        return {
            "class_score_thresholds": list(self.thresholds),
            "tile_class_score_thresholds": (
                None if self.tile_thresholds is None else list(self.tile_thresholds)
            ),
            "nms": "per_class",
            "nms_threshold": self.nms_threshold,
            "max_detections": self.max_detections,
            "slicing": self.slicing.profile_fields(),
        }


BASELINE = Profile("BASELINE_2702eb15", (0.03, 0.2, 0.07, 0.2))


def _selected_views(views: list[list[int]], mode: str) -> list[int]:
    if mode == "full" or len(views) == 1:
        return [0]
    return list(range(1, len(views))) if mode == "tiled" else list(range(len(views)))


def profile_detections(entry: CacheEntry, resolution: str, profile: Profile) -> np.ndarray:
    """Detecções finais do perfil no quadro da resolução, pela mesma seleção do produto."""
    if min(profile.thresholds + (profile.tile_thresholds or ())) < SCORE_FLOOR:
        raise DevSetError("limiar abaixo do piso do cache: resultado não seria exato")
    slicing = profile.slicing
    if slicing.mode != "full" and (
        slicing.tile_size != CACHE_TILE or slicing.overlap != CACHE_OVERLAP
    ):
        raise DevSetError("fatiamento diferente do cache: rode o cache com essa grade")
    info = entry.meta["resolutions"][resolution]
    per_view = []
    for index in _selected_views(info["views"], slicing.mode):
        thresholds = (
            profile.tile_thresholds
            if index > 0 and profile.tile_thresholds is not None
            else profile.thresholds
        )
        x0, y0, x1, y1 = info["views"][index]
        pixels = select_detections(
            entry.arrays[f"{resolution}/{index}/boxes"],
            entry.arrays[f"{resolution}/{index}/scores"],
            (y1 - y0, x1 - x0),
            class_score_thresholds=thresholds,
            nms_threshold=profile.nms_threshold,
            score_threshold=0.0,
        )
        pixels = finalize_detections(pixels, max_detections=profile.max_detections)
        per_view.append(offset_detections(pixels, (x0, y0, x1, y1)))
    merged = merge_view_detections(
        per_view, method=slicing.merge, threshold=slicing.merge_threshold
    )
    return finalize_detections(merged, max_detections=profile.max_detections)


def truth_boxes(entry: CacheEntry, resolution: str) -> list[tuple[int, Box]]:
    width, height = entry.meta["resolutions"][resolution]["size"]
    result = []
    for class_index, x0, y0, x1, y1 in entry.image.truths:
        if x1 > x0 and y1 > y0:
            result.append((class_index, Box(x0 * width, y0 * height, x1 * width, y1 * height)))
    return result


def is_small(box: Box, frame_size: Sequence[int]) -> bool:
    ratio = min(INPUT_SIZE[0] / frame_size[1], INPUT_SIZE[1] / frame_size[0], 1.0)
    return max(box.x2 - box.x1, box.y2 - box.y1) * ratio < SMALL_OBJECT_TENSOR_PX


def _boxes(detections: np.ndarray) -> list[tuple[int, Box, float]]:
    return [
        (int(c), Box(x0, y0, x1, y1), float(s))
        for x0, y0, x1, y1, s, c in detections.tolist()
        if x1 > x0 and y1 > y0
    ]


def classify_image_errors(
    detections: np.ndarray,
    truths: list[tuple[int, Box]],
    *,
    frame_size: Sequence[int],
    image_flags: Iterable[str] = (),
) -> dict[str, Any]:
    """Casamento guloso por classe (IoU 0,5, maior score primeiro) e causa de cada erro.

    Mesmo casamento de ``app.ml.metrics``; categorias por evidência geométrica:
    ``WRONG_CLASS`` (IoU ≥ 0,5 com GT de outra classe), ``BAD_BOX`` (0,1 ≤ IoU < 0,5),
    ``PURE_MISS`` (GT sem nenhuma caixa com IoU ≥ 0,1: o "problema visível, nenhuma
    caixa") e ``BACKGROUND`` (alarme sem GT por perto). ``SMALL_OBJECT`` e os sinais de
    qualidade são marcas adicionais, não causas exclusivas.
    """
    preds = sorted(_boxes(detections), key=lambda item: -item[2])
    matched_truth: set[int] = set()
    matched_pred: set[int] = set()
    for p_index, (p_class, p_box, _) in enumerate(preds):
        best, best_iou = -1, 0.5
        for t_index, (t_class, t_box) in enumerate(truths):
            if t_index in matched_truth or t_class != p_class:
                continue
            overlap = iou(p_box, t_box)
            if overlap >= best_iou:
                best, best_iou = t_index, overlap
        if best >= 0:
            matched_truth.add(best)
            matched_pred.add(p_index)
    flags = sorted(set(image_flags))
    fn: list[dict[str, Any]] = []
    for t_index, (t_class, t_box) in enumerate(truths):
        if t_index in matched_truth:
            continue
        overlaps = [(iou(p_box, t_box), p_class) for p_class, p_box, _ in preds]
        if any(o >= 0.5 and c != t_class for o, c in overlaps):
            cause = "WRONG_CLASS"
        elif any(o >= 0.1 for o, _ in overlaps):
            cause = "BAD_BOX"
        else:
            cause = "PURE_MISS"
        tags = [*flags, *(["SMALL_OBJECT"] if is_small(t_box, frame_size) else [])]
        fn.append({"class": CLASS_NAMES[t_class], "cause": cause, "tags": tags})
    fp: list[dict[str, Any]] = []
    for p_index, (p_class, p_box, score) in enumerate(preds):
        if p_index in matched_pred:
            continue
        overlaps = [(iou(p_box, t_box), t_class) for t_class, t_box in truths]
        if any(o >= 0.5 and c != p_class for o, c in overlaps):
            cause = "WRONG_CLASS"
        elif any(o >= 0.1 for o, _ in overlaps):
            cause = "BAD_BOX"
        else:
            cause = "BACKGROUND" if truths else "NEGATIVE_IMAGE"
        fp.append({"class": CLASS_NAMES[p_class], "cause": cause, "score": round(score, 4)})
    small_truths = [t for t in truths if is_small(t[1], frame_size)]
    return {
        "tp": len(matched_truth),
        "fn": fn,
        "fp": fp,
        "small_support": len(small_truths),
        "small_matched": sum(
            1
            for t_index, t in enumerate(truths)
            if t_index in matched_truth and is_small(t[1], frame_size)
        ),
    }


def score_set(
    entries: Sequence[CacheEntry],
    resolution: str,
    profile: Profile,
    *,
    iou_threshold: float = 0.5,
    detections_for: Callable[[CacheEntry], np.ndarray] | None = None,
) -> dict[str, Any]:
    """Métricas do perfil: ``app.ml.metrics.evaluate`` + taxonomia de erros.

    ``detections_for`` substitui o caminho de vistas do cache (ex.: TTA), com o mesmo
    casamento e as mesmas categorias.
    """
    predictions: list[Prediction] = []
    ground_truths: list[GroundTruth] = []
    fn_causes: Counter[str] = Counter()
    fn_by_class: dict[str, Counter[str]] = defaultdict(Counter)
    fn_tags: Counter[str] = Counter()
    fp_causes: Counter[str] = Counter()
    fp_by_class: dict[str, Counter[str]] = defaultdict(Counter)
    small_support = small_matched = 0
    negative_images = negative_with_fp = negative_fp = 0
    detections_total = 0
    for entry in entries:
        info = entry.meta["resolutions"][resolution]
        detections = (
            detections_for(entry)
            if detections_for is not None
            else profile_detections(entry, resolution, profile)
        )
        truths = truth_boxes(entry, resolution)
        detections_total += len(detections)
        image_id = entry.image.image_id
        for class_index, box, score in _boxes(detections):
            predictions.append(Prediction(image_id, CLASS_NAMES[class_index], box, score))
        for class_index, box in truths:
            ground_truths.append(GroundTruth(image_id, CLASS_NAMES[class_index], box))
        errors = classify_image_errors(
            detections,
            truths,
            frame_size=info["size"],
            image_flags=quality_flags(info["quality"]),
        )
        for item in errors["fn"]:
            fn_causes[item["cause"]] += 1
            fn_by_class[item["class"]][item["cause"]] += 1
            fn_tags.update(item["tags"])
        for item in errors["fp"]:
            fp_causes[item["cause"]] += 1
            fp_by_class[item["class"]][item["cause"]] += 1
        small_support += errors["small_support"]
        small_matched += errors["small_matched"]
        if not truths:
            negative_images += 1
            negative_fp += len(errors["fp"])
            negative_with_fp += bool(errors["fp"])
    result = evaluate(
        predictions,
        ground_truths,
        labels=list(CLASS_NAMES),
        score_threshold=0.0,
        iou_threshold=iou_threshold,
    ).as_persisted()
    per_class = result["per_class"]
    assert isinstance(per_class, dict)
    f1s = [values["f1"] for values in per_class.values() if values["f1"] is not None]
    tp = sum(values["true_positives"] for values in per_class.values())
    fp = sum(values["false_positives"] for values in per_class.values())
    fn = sum(values["false_negatives"] for values in per_class.values())
    return {
        "images": len(entries),
        "resolution": resolution,
        "profile": profile.name,
        "macro_f1": round(sum(f1s) / len(f1s), 6) if f1s else None,
        "micro_recall": result["recall"],
        "micro_precision": result["precision"],
        "map50": result["map50"],
        "map50_95": result["map50_95"],
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "detections": detections_total,
        "small_object_recall": (round(small_matched / small_support, 6) if small_support else None),
        "small_object_support": small_support,
        "negative_images": negative_images,
        "negative_images_with_fp": negative_with_fp,
        "negative_image_fp": negative_fp,
        "per_class": per_class,
        "confusion": result["confusion"],
        "fn_causes": dict(fn_causes.most_common()),
        "fn_tags": dict(fn_tags.most_common()),
        "fn_by_class": {k: dict(v.most_common()) for k, v in sorted(fn_by_class.items())},
        "fp_causes": dict(fp_causes.most_common()),
        "fp_by_class": {k: dict(v.most_common()) for k, v in sorted(fp_by_class.items())},
    }


def _mask(entry: CacheEntry) -> np.ndarray:
    from PIL import Image

    assert entry.image.mask_path is not None and entry.image.mask_sha256 is not None
    data = _read_verified(entry.image.mask_path, entry.image.mask_sha256)
    return np.asarray(Image.open(io.BytesIO(data)))


def categorize_mask_detection(mask: np.ndarray, box: Sequence[float]) -> str:
    """Descreve, pela classe da máscara da fonte, o que está sob uma caixa.

    ``DAMAGE_OVERLAP`` quando há pixels de trinca/buraco suficientes (não é acerto
    confirmado: a máscara não diz classe D00–D40 nem instância). Senão, a classe
    especial que ocupa ≥ 10 % da caixa; senão a textura de pista dominante ou fundo.
    É descrição da máscara (não revisada pelo UrMind), não verdade de campo nem causa
    comprovada do alarme.
    """
    height, width = mask.shape[:2]
    x0, y0 = max(0, math.floor(box[0])), max(0, math.floor(box[1]))
    x1, y1 = min(width, math.ceil(box[2])), min(height, math.ceil(box[3]))
    region = mask[y0:y1, x0:x1]
    if region.size == 0:
        return "EMPTY_REGION"
    counts = np.bincount(region.ravel(), minlength=len(RTK_CLASSES))
    area = region.size
    damage = int(sum(counts[c] for c in RTK_DAMAGE))
    if damage >= max(20, 0.02 * area):
        return "DAMAGE_OVERLAP"
    special = {c: counts[c] / area for c in RTK_CONFUSION}
    best = max(special, key=lambda c: special[c])
    if special[best] >= 0.10:
        return RTK_CONFUSION[best]
    road = {c: counts[c] / area for c in (1, 2, 3)}
    if sum(road.values()) < 0.5:
        return "OFF_ROAD"
    # Classe de pista da máscara sob a caixa: roadAsphalt, roadPaved ou roadUnpaved. Que
    # roadPaved seja pavimento de blocos/paralelepípedo é leitura da descrição do dataset,
    # não conferida imagem a imagem.
    return {1: "ASPHALT_TEXTURE", 2: "PAVED_TEXTURE", 3: "UNPAVED_TEXTURE"}[
        max(road, key=lambda c: road[c])
    ]


def score_rtk(entries: Sequence[CacheEntry], profile: Profile) -> dict[str, Any]:
    causes: Counter[str] = Counter()
    by_class: dict[str, Counter[str]] = defaultdict(Counter)
    damage_images = damage_hit = clean_images = clean_with_detection = 0
    for entry in entries:
        mask = _mask(entry)
        detections = profile_detections(entry, "native", profile)
        labels = [categorize_mask_detection(mask, row[:4]) for row in detections.tolist()]
        for label, row in zip(labels, detections.tolist(), strict=True):
            causes[label] += 1
            by_class[CLASS_NAMES[int(row[5])]][label] += 1
        has_damage = bool(np.isin(mask, RTK_DAMAGE).any())
        if has_damage:
            damage_images += 1
            damage_hit += "DAMAGE_OVERLAP" in labels
        else:
            clean_images += 1
            clean_with_detection += bool(labels)
    alarms = sum(v for k, v in causes.items() if k != "DAMAGE_OVERLAP")
    return {
        "images": len(entries),
        "profile": profile.name,
        "category_semantics": (
            "descrição da classe da máscara da fonte sob a caixa (PAVED_TEXTURE = roadPaved); "
            "não é Ground Truth revisado nem causa comprovada do alarme"
        ),
        "detections": sum(causes.values()),
        "non_damage_detections": alarms,
        "non_damage_per_image": round(alarms / len(entries), 6) if entries else None,
        "causes": dict(causes.most_common()),
        "causes_by_class": {k: dict(v.most_common()) for k, v in sorted(by_class.items())},
        "images_with_damage_pixels": damage_images,
        "damage_images_with_overlapping_detection": damage_hit,
        "image_level_damage_hit_rate": (
            round(damage_hit / damage_images, 6) if damage_images else None
        ),
        "images_without_damage_pixels": clean_images,
        "clean_images_with_any_detection": clean_with_detection,
    }


def threshold_curve(
    entries: Sequence[CacheEntry],
    resolution: str,
    profile: Profile,
    grid: Sequence[float],
) -> dict[str, list[dict[str, Any]]]:
    """P/R/F1/FP/FN por classe em cada limiar (as classes não interferem entre si)."""
    curve: dict[str, list[dict[str, Any]]] = {name: [] for name in CLASS_NAMES}
    for value in grid:
        scored = score_set(
            entries, resolution, replace(profile, thresholds=(value,) * len(CLASS_NAMES))
        )
        for name in CLASS_NAMES:
            values = scored["per_class"][name]
            curve[name].append(
                {
                    "threshold": value,
                    "precision": values["precision"],
                    "recall": values["recall"],
                    "f1": values["f1"],
                    "fp": values["false_positives"],
                    "fn": values["false_negatives"],
                    "tp": values["true_positives"],
                }
            )
    return curve


def best_thresholds(curve: dict[str, list[dict[str, Any]]]) -> tuple[float, ...]:
    """Limiar de maior F1 por classe; empate → o maior limiar (menos alarmes)."""
    chosen = []
    for name in CLASS_NAMES:
        points = [p for p in curve[name] if p["f1"] is not None]
        best = max(points, key=lambda p: (p["f1"], p["threshold"]))
        chosen.append(float(best["threshold"]))
    return tuple(chosen)


def subset(entries: Sequence[CacheEntry], name: str) -> list[CacheEntry]:
    return [entry for entry in entries if entry.image.subset == name]


def iter_sets(names: Iterable[str]) -> Iterator[tuple[str, list[CacheEntry]]]:
    for name in names:
        yield name, load_cache(name)


DEV_SETS_REGISTRATION = PROJECT_ROOT / "datasets/metadata/visual_sprint_dev_sets.json"
THRESHOLD_GRID: tuple[float, ...] = (
    0.01,
    0.02,
    0.03,
    0.05,
    0.07,
    0.1,
    0.13,
    0.16,
    0.2,
    0.25,
    0.3,
    0.35,
    0.4,
    0.5,
)
MERGES: tuple[tuple[str, float], ...] = (
    ("nms", 0.5),
    ("nms_ios", 0.5),
    ("nmm", 0.5),
    ("wbf", 0.55),
)
NMS_GRID: tuple[float, ...] = (0.3, 0.45, 0.55, 0.65)


def _brief(scored: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "images",
        "macro_f1",
        "micro_recall",
        "micro_precision",
        "tp",
        "fp",
        "fn",
        "small_object_recall",
        "small_object_support",
        "negative_images",
        "negative_images_with_fp",
        "negative_image_fp",
        "fn_causes",
        "fp_causes",
    )
    brief = {key: scored[key] for key in keys}
    brief["per_class"] = {
        name: {
            key: values[key]
            for key in (
                "precision",
                "recall",
                "f1",
                "true_positives",
                "false_positives",
                "false_negatives",
                "ap50",
            )
        }
        for name, values in scored["per_class"].items()
    }
    return brief


def ranking_map(entries: Sequence[CacheEntry], resolution: str, profile: Profile) -> dict[str, Any]:
    """mAP pelo ranking completo (limiares no piso do cache), como no registro do baseline.

    P/R/F1 medem o ponto de operação; o mAP mede o ranqueamento e por isso não usa os
    limiares do perfil (cortá-los trunca a curva e rebaixa o AP).
    """
    scored = score_set(
        entries, resolution, replace(profile, thresholds=(SCORE_FLOOR,) * len(CLASS_NAMES))
    )
    return {
        "map50": scored["map50"],
        "map50_95": scored["map50_95"],
        "ap50": {name: values["ap50"] for name, values in scored["per_class"].items()},
    }


def _class_regression(candidate: dict[str, Any], reference: dict[str, Any]) -> float:
    worst = 0.0
    for name in CLASS_NAMES:
        new, old = candidate["per_class"][name]["f1"], reference["per_class"][name]["f1"]
        if new is not None and old is not None:
            worst = min(worst, new - old)
    return round(worst, 6)


def _views_per_frame(entries: Sequence[CacheEntry], resolution: str, mode: str) -> int:
    views = entries[0].meta["resolutions"][resolution]["views"]
    return len(_selected_views(views, mode))


def build_report() -> dict[str, Any]:
    """Plano da sprint, na ordem pré-registrada em ``visual_sprint_dev_sets.json``."""
    ird = load_cache("ird")
    parts = {"tune": subset(ird, "tune"), "check": subset(ird, "check"), "all": ird}
    validation = load_cache("validation")
    rtk = load_cache("rtk")
    report: dict[str, Any] = {
        "schema_version": 1,
        "producer": "app.ml.camera_dev report",
        "generated_at": datetime.now(UTC).isoformat(),
        "baseline_freeze": _relative(BASELINE_FREEZE),
        "baseline_id": json.loads(BASELINE_FREEZE.read_text(encoding="utf-8"))["baseline_id"],
        "dev_sets_registration": _relative(DEV_SETS_REGISTRATION),
        "dev_sets_registration_sha256": sha256_file(DEV_SETS_REGISTRATION),
        "onnx_sha256": ONNX_SHA256,
        "cache": {
            "root": _relative(CACHE_ROOT),
            "score_floor": SCORE_FLOOR,
            "tile": CACHE_TILE,
            "overlap": CACHE_OVERLAP,
        },
        "test_accessed": False,
    }
    # 1. Baseline e checagem do harness.
    validation_base = score_set(validation, "native", BASELINE)
    validation_ranking = ranking_map(validation, "native", BASELINE)
    report["harness_check"] = {
        "validation_map50_ranking": validation_ranking["map50"],
        "validation_map50_95_ranking": validation_ranking["map50_95"],
        "validation_macro_f1": validation_base["macro_f1"],
        "expected": {"map50": 0.159632, "map50_95": 0.050861, "macro_f1": 0.2804},
        "reproduced": abs(validation_ranking["map50"] - 0.159632) < 1e-6
        and abs(validation_ranking["map50_95"] - 0.050861) < 1e-6
        and abs(validation_base["macro_f1"] - 0.2804) < 5e-4,
        "convention": "mAP pelo ranking completo (piso 0,01); P/R/F1/FP/FN no ponto de operação",
    }
    baseline: dict[str, Any] = {
        "validation": {**_brief(validation_base), "ranking": validation_ranking}
    }
    for resolution in IRD_RESOLUTIONS:
        for name, entries in parts.items():
            baseline[f"ird_{name}_{resolution}"] = {
                **_brief(score_set(entries, resolution, BASELINE)),
                **(
                    {"ranking": ranking_map(entries, resolution, BASELINE)}
                    if name == "check"
                    else {}
                ),
            }
    baseline["rtk"] = score_rtk(rtk, BASELINE)
    report["baseline"] = baseline
    # 2. Mineração de erros do baseline (causas por classe, marcas, confusão).
    mining = {}
    for resolution in IRD_RESOLUTIONS:
        scored = score_set(ird, resolution, BASELINE)
        mining[resolution] = {
            key: scored[key]
            for key in (
                "fn_causes",
                "fn_tags",
                "fn_by_class",
                "fp_causes",
                "fp_by_class",
                "confusion",
            )
        }
    report["error_mining_baseline"] = mining
    # 3. Modos × fusões × resoluções, com os limiares do baseline.
    slicing: dict[str, Any] = {}
    for resolution in IRD_RESOLUTIONS:
        for mode in ("tiled", "hybrid"):
            for merge, threshold in MERGES:
                config = SlicingConfig(mode=mode, merge=merge, merge_threshold=threshold)
                profile = replace(BASELINE, name=f"{mode}-{merge}@{threshold}", slicing=config)
                key = f"{resolution}/{mode}/{merge}@{threshold}"
                slicing[key] = {
                    "views_per_frame": _views_per_frame(ird, resolution, mode),
                    **{
                        part: _brief(score_set(parts[part], resolution, profile))
                        for part in ("tune", "check")
                    },
                }
    report["slicing"] = slicing
    report["slicing_decision"] = decide_slicing(report)
    # 4. Limiares por classe no modo escolhido (uma busca, em TUNE), confirmação em CHECK.
    report["thresholds"] = threshold_study(report, parts, validation, rtk)
    # 5. NMS por classe depois do modo e dos limiares.
    report["nms"] = nms_study(report, parts)
    # 6. Perfil avaliado por resolução contra o baseline, no mesmo protocolo.
    report["evaluated_candidates"] = final_comparison(report, parts, validation, rtk)
    selection = select_system(report)
    for resolution, verdict in selection["per_resolution"].items():
        report["evaluated_candidates"][resolution]["status"] = (
            "SELECTED" if verdict["selected"] else "REJECTED_NOT_SELECTED"
        )
        report["thresholds"][resolution]["selected_for_runtime"] = verdict["selected"]
    # 7. Pós-hoc (NÃO pré-registrado): limiar próprio nas vistas de tile.
    report["posthoc_tile_thresholds"] = posthoc_tile_thresholds(parts)
    # 8. Sensibilidade: quanto do FN é convenção de caixa (IoU 0,3 em vez de 0,5).
    report["iou_sensitivity"] = {
        resolution: {
            "baseline_check_iou05": report["baseline"][f"ird_check_{resolution}"]["micro_recall"],
            "baseline_check_iou03": score_set(
                parts["check"], resolution, BASELINE, iou_threshold=0.3
            )["micro_recall"],
            "baseline_check_iou03_per_class": {
                name: values["recall"]
                for name, values in score_set(
                    parts["check"], resolution, BASELINE, iou_threshold=0.3
                )["per_class"].items()
            },
        }
        for resolution in ("1080p",)
    }
    report["latency_cpu_onnxruntime"] = {
        "views_per_frame": {
            resolution: {
                mode: _views_per_frame(ird, resolution, mode)
                for mode in ("full", "tiled", "hybrid")
            }
            for resolution in IRD_RESOLUTIONS
        },
        "measured": measure_cpu_latency([entry.image for entry in ird[:LATENCY_SAMPLE_IMAGES]]),
    }
    # Seleção e papel dos dados logo depois do cabeçalho: é o que se lê primeiro.
    header = {key: report.pop(key) for key in list(report) if key in _HEADER_KEYS}
    return {**header, "selection": selection, "data_use": data_use(), **report}


_HEADER_KEYS = (
    "schema_version",
    "producer",
    "generated_at",
    "baseline_freeze",
    "baseline_id",
    "dev_sets_registration",
    "dev_sets_registration_sha256",
    "onnx_sha256",
    "cache",
    "test_accessed",
)


def _profile_for(report: dict[str, Any], resolution: str) -> Profile:
    decision = report["slicing_decision"].get(resolution) or {}
    if not decision.get("accepted"):
        return replace(BASELINE, name="full")
    mode, merge = decision["mode"], decision["merge"]
    return replace(
        BASELINE,
        name=f"{mode}-{merge['method']}@{merge['threshold']}",
        slicing=SlicingConfig(mode=mode, merge=merge["method"], merge_threshold=merge["threshold"]),
    )


def decide_slicing(report: dict[str, Any]) -> dict[str, Any]:
    """Regra pré-registrada de modo e fusão, por resolução."""
    decisions: dict[str, Any] = {}
    base = report["baseline"]
    for resolution in IRD_RESOLUTIONS:
        reference = {part: base[f"ird_{part}_{resolution}"] for part in ("tune", "check")}
        candidates = []
        for key, value in report["slicing"].items():
            res, mode, merge = key.split("/")
            if res != resolution:
                continue
            ok = (
                all(
                    value[part]["micro_recall"] > reference[part]["micro_recall"]
                    and value[part]["macro_f1"] > reference[part]["macro_f1"]
                    for part in ("tune", "check")
                )
                and _class_regression(value["check"], reference["check"]) >= -0.03
            )
            candidates.append(
                (ok, value["tune"]["macro_f1"], -value["views_per_frame"], mode, merge, value)
            )
        accepted = [c for c in candidates if c[0]]
        if not accepted:
            decisions[resolution] = {"accepted": False, "reason": "nenhum modo passou a regra"}
            continue
        best_f1 = max(c[1] for c in accepted)
        near = [c for c in accepted if best_f1 - c[1] < 0.005]
        _, _, _, mode, merge, value = max(near, key=lambda c: (c[2], c[1]))
        # Fusão: melhor F1 em TUNE entre as fusões do modo escolhido, sem perder para
        # NMS 0,5 em CHECK por mais de 0,005.
        same_mode = [c for c in accepted if c[3] == mode]
        nms_value = report["slicing"][f"{resolution}/{mode}/nms@0.5"]["check"]["macro_f1"]
        eligible = [c for c in same_mode if c[5]["check"]["macro_f1"] >= nms_value - 0.005]
        chosen = max(eligible or same_mode, key=lambda c: c[1])
        method, threshold = chosen[4].split("@")
        decisions[resolution] = {
            "accepted": True,
            "mode": mode,
            "merge": {"method": method, "threshold": float(threshold)},
            "views_per_frame": chosen[5]["views_per_frame"],
            "tune_macro_f1": chosen[5]["tune"]["macro_f1"],
            "check_macro_f1": chosen[5]["check"]["macro_f1"],
            "check_micro_recall": chosen[5]["check"]["micro_recall"],
            "accepted_candidates": sorted(f"{c[3]}/{c[4]}" for c in accepted),
        }
    return decisions


def threshold_study(
    report: dict[str, Any],
    parts: dict[str, list[CacheEntry]],
    validation: Sequence[CacheEntry],
    rtk: Sequence[CacheEntry],
) -> dict[str, Any]:
    study: dict[str, Any] = {}
    for resolution in IRD_RESOLUTIONS:
        profile = _profile_for(report, resolution)
        curve = threshold_curve(parts["tune"], resolution, profile, THRESHOLD_GRID)
        chosen = best_thresholds(curve)
        tuned = replace(profile, name=profile.name + "+thr", thresholds=chosen)
        check_before = score_set(parts["check"], resolution, profile)
        check_after = score_set(parts["check"], resolution, tuned)
        validation_after = score_set(validation, "native", replace(tuned, slicing=SlicingConfig()))
        rtk_after = score_rtk(rtk, replace(tuned, slicing=SlicingConfig()))
        rtk_before = report["baseline"]["rtk"]
        validation_before = report["baseline"]["validation"]
        gain = check_after["macro_f1"] - check_before["macro_f1"]
        rtk_ratio = (
            rtk_after["non_damage_per_image"] / rtk_before["non_damage_per_image"]
            if rtk_before["non_damage_per_image"]
            else None
        )
        passed = (
            gain >= 0.01
            and validation_after["macro_f1"] >= validation_before["macro_f1"] - 0.02
            and (rtk_ratio is None or rtk_ratio <= 1.5)
        )
        study[resolution] = {
            # Só a regra F1 pré-registrada; a escolha do sistema está em `selection`.
            "passed_preregistered_f1_rule": passed,
            "mode_profile": profile.name,
            "curve_tune": curve,
            "chosen_on_tune": dict(zip(CLASS_NAMES, chosen, strict=True)),
            "check_before": _brief(check_before),
            "check_after": _brief(check_after),
            "check_macro_f1_gain": round(gain, 6),
            "validation_after": _brief(validation_after),
            "rtk_after": rtk_after,
            "rtk_non_damage_ratio": None if rtk_ratio is None else round(rtk_ratio, 4),
        }
    return study


def nms_study(report: dict[str, Any], parts: dict[str, list[CacheEntry]]) -> dict[str, Any]:
    study: dict[str, Any] = {}
    for resolution in IRD_RESOLUTIONS:
        profile = _profile_for(report, resolution)
        thresholds = report["thresholds"][resolution]
        if thresholds["passed_preregistered_f1_rule"]:
            profile = replace(
                profile, thresholds=tuple(thresholds["chosen_on_tune"][n] for n in CLASS_NAMES)
            )
        rows = {}
        for value in NMS_GRID:
            candidate = replace(profile, nms_threshold=value)
            rows[str(value)] = {
                part: {
                    key: scored[key]
                    for key in ("macro_f1", "micro_recall", "micro_precision", "fp", "fn")
                }
                for part in ("tune", "check")
                for scored in [score_set(parts[part], resolution, candidate)]
            }
        best = max(rows, key=lambda k: rows[k]["tune"]["macro_f1"])
        current = rows[str(profile.nms_threshold)]
        change = (
            best != str(profile.nms_threshold)
            and rows[best]["tune"]["macro_f1"] - current["tune"]["macro_f1"] >= 0.005
            and rows[best]["check"]["macro_f1"] - current["check"]["macro_f1"] >= 0.005
        )
        study[resolution] = {"grid": rows, "best_on_tune": float(best), "change_accepted": change}
    return study


TILE_THRESHOLD_GRID: tuple[float, ...] = (0.3, 0.4, 0.5, 0.6, 0.7)


def posthoc_tile_thresholds(parts: dict[str, list[CacheEntry]]) -> dict[str, Any]:
    """HYBRID com limiar único mais alto nas vistas de tile, escolhido em TUNE.

    Pós-hoc: surgiu depois de ver que os tiles com os limiares do baseline explodem
    os falsos positivos. Escolha em TUNE, uma única avaliação em CHECK; mesmo passando,
    é candidato exploratório, não aceito pela regra pré-registrada.
    """
    study: dict[str, Any] = {}
    for resolution in ("1080p", "720p"):
        rows = {}
        for value in TILE_THRESHOLD_GRID:
            for merge, threshold in (("nms", 0.5), ("nms_ios", 0.5)):
                profile = replace(
                    BASELINE,
                    name=f"hybrid-{merge}-tile{value}",
                    slicing=SlicingConfig(mode="hybrid", merge=merge, merge_threshold=threshold),
                    tile_thresholds=(value,) * len(CLASS_NAMES),
                )
                scored = score_set(parts["tune"], resolution, profile)
                rows[f"{merge}/tile{value}"] = {
                    key: scored[key]
                    for key in ("macro_f1", "micro_recall", "micro_precision", "fp", "fn")
                }
        best = max(rows, key=lambda k: rows[k]["macro_f1"])
        merge, tile = best.split("/tile")
        chosen = replace(
            BASELINE,
            name=f"POSTHOC_hybrid-{merge}-tile{tile}",
            slicing=SlicingConfig(mode="hybrid", merge=merge, merge_threshold=0.5),
            tile_thresholds=(float(tile),) * len(CLASS_NAMES),
        )
        check = score_set(parts["check"], resolution, chosen)
        base_tune = score_set(parts["tune"], resolution, BASELINE)
        base_check = score_set(parts["check"], resolution, BASELINE)
        study[resolution] = {
            "status": "POSTHOC_NOT_PREREGISTERED",
            "grid_tune": rows,
            "chosen_on_tune": chosen.fields(),
            "baseline_tune": _brief(base_tune),
            "chosen_tune": rows[best],
            "baseline_check": _brief(base_check),
            "chosen_check": _brief(check),
            "check_macro_f1_gain": round(check["macro_f1"] - base_check["macro_f1"], 6),
            "check_recall_gain": round(check["micro_recall"] - base_check["micro_recall"], 6),
            "views_per_frame": _views_per_frame(parts["all"], resolution, "hybrid"),
        }
    return study


def candidate_profile(report: dict[str, Any], resolution: str) -> Profile:
    """Perfil avaliado: modo/fusão, limiares e NMS que passaram a própria regra.

    Avaliar não é selecionar: a escolha do sistema é de ``select_system``.
    """
    profile = _profile_for(report, resolution)
    thresholds = report["thresholds"][resolution]
    if thresholds["passed_preregistered_f1_rule"]:
        profile = replace(
            profile, thresholds=tuple(thresholds["chosen_on_tune"][n] for n in CLASS_NAMES)
        )
    nms = report["nms"][resolution]
    if nms["change_accepted"]:
        profile = replace(profile, nms_threshold=nms["best_on_tune"])
    return replace(profile, name=f"CANDIDATE_{resolution}")


def final_comparison(
    report: dict[str, Any],
    parts: dict[str, list[CacheEntry]],
    validation: Sequence[CacheEntry],
    rtk: Sequence[CacheEntry],
) -> dict[str, Any]:
    final: dict[str, Any] = {}
    for resolution in IRD_RESOLUTIONS:
        candidate = candidate_profile(report, resolution)
        small = replace(candidate, slicing=SlicingConfig())
        final[resolution] = {
            "not_for_publication": True,
            "evaluated_profile": candidate.fields(),
            "views_per_frame": _views_per_frame(parts["all"], resolution, candidate.slicing.mode),
            "baseline_check": report["baseline"][f"ird_check_{resolution}"],
            "candidate_check": {
                **_brief(score_set(parts["check"], resolution, candidate)),
                "ranking": ranking_map(parts["check"], resolution, candidate),
            },
            "candidate_tune": _brief(score_set(parts["tune"], resolution, candidate)),
            "candidate_validation": _brief(score_set(validation, "native", small)),
            "candidate_rtk": score_rtk(rtk, small),
        }
    return final


SELECTION_CRITERION = (
    "Pedido do proprietário de 26/09/2026 (prioridade de recall, item 44 e consolidação): "
    "só é selecionado um perfil que suba o recall micro E o F1 macro de câmera no CHECK, sem "
    "classe perdendo mais de 0,03 de F1 no CHECK e sem piorar o F1 macro da VALIDATION. "
    "Passar a regra F1 pré-registrada dos limiares não basta."
)


def select_system(report: dict[str, Any]) -> dict[str, Any]:
    """Escolha do sistema a partir dos candidatos avaliados; nunca publica nada."""
    manifest = json.loads(PUBLISHED_MANIFEST.read_text(encoding="utf-8"))
    baseline_validation = report["baseline"]["validation"]["macro_f1"]
    per_resolution: dict[str, Any] = {}
    for resolution, item in report["evaluated_candidates"].items():
        base, cand = item["baseline_check"], item["candidate_check"]
        reasons: list[str] = []
        if item["evaluated_profile"] == BASELINE.fields():
            reasons.append("perfil avaliado é o próprio baseline (nenhuma regra passou)")
        else:
            if cand["micro_recall"] <= base["micro_recall"]:
                reasons.append(
                    f"recall CHECK {base['micro_recall']} -> {cand['micro_recall']} (não sobe)"
                )
            if cand["macro_f1"] <= base["macro_f1"]:
                reasons.append(f"F1 macro CHECK {base['macro_f1']} -> {cand['macro_f1']}")
            worst = _class_regression(cand, base)
            if worst < -0.03:
                reasons.append(f"uma classe perde {worst} de F1 no CHECK")
            validation_f1 = item["candidate_validation"]["macro_f1"]
            if validation_f1 < baseline_validation:
                reasons.append(
                    f"F1 macro VALIDATION {baseline_validation} -> {validation_f1} (piora)"
                )
        per_resolution[resolution] = {"selected": not reasons, "reasons": reasons}
    selected = sorted(r for r, value in per_resolution.items() if value["selected"])
    f1_rule = {r for r, t in report["thresholds"].items() if t["passed_preregistered_f1_rule"]}
    return {
        "BEST_SYSTEM": "B" if selected else "A",
        "SELECTED_MODEL": ONNX_SHA256,
        "SELECTED_PROFILE": None if selected else manifest["inference_profile"]["sha256"],
        # Nulo quando nenhum limiar passou a regra F1: não havia candidato a rejeitar.
        "THRESHOLD_CANDIDATE_REJECTED": (not f1_rule & set(selected)) if f1_rule else None,
        "runtime_change": "PENDING_OWNER_AUTHORIZATION" if selected else "NONE",
        "publishable_candidate": None,
        "criterion": SELECTION_CRITERION,
        "per_resolution": per_resolution,
    }


def data_use() -> dict[str, Any]:
    """Papel de cada conjunto nesta sprint, lido das evidências registradas."""
    ird_audit = json.loads(
        (PROJECT_ROOT / "datasets/reports/ird_dashcam_candidate_audit.json").read_text(
            encoding="utf-8"
        )
    )
    return {
        "role": "DEVELOPMENT_ONLY",
        "official_evaluation": False,
        "production_evidence": False,
        "statement": (
            "Métricas de câmera deste relatório orientam o desenvolvimento; não são avaliação "
            "oficial, não são holdout e não provam desempenho em produção."
        ),
        "sets": {
            "ird": {
                "use": "CAMERA_DEV",
                "ground_truth": "caixas publicadas pela fonte, sem revisão humana do UrMind",
                "human_validated_images": ird_audit["human_validated_images"],
                "official_evaluation_authorized": ird_audit["official_evaluation_authorized"],
                "training_authorized": ird_audit["training_authorized"],
                "future_holdout": False,
                "evidence": "datasets/reports/ird_dashcam_candidate_audit.json",
            },
            "rtk": {
                "use": "HARD_NEGATIVE_DEV",
                "ground_truth": (
                    "máscara semântica da fonte; as categorias de alarme descrevem a classe da "
                    "máscara sob a caixa, não são verdade de campo"
                ),
                "human_review_recorded": False,
                "human_review_evidence": (
                    "RTK fora do pacote de revisão (datasets/reports/review_summary.json); "
                    "quase-duplicatas PENDING_HUMAN_ADJUDICATION (rtk_br_audit.json)"
                ),
                "future_independent_brazilian_holdout": False,
            },
            "validation": {"use": "REGRESSION_GUARD", "already_used_for_tuning": True},
        },
        "protected_not_read": ["TEST / Frozen Test", "EXTERNAL_TEST", "holdout UNIVALI"],
    }


LATENCY_SAMPLE_IMAGES = 12
LATENCY_WARMUP_VIEWS = 3


def latency_section(
    full_ms: Sequence[float],
    tile_ms: Sequence[float],
    views_per_frame: dict[str, tuple[int, int]],
) -> dict[str, Any]:
    """Resumo da latência de ``session.run`` e estimativa por quadro a partir das medianas.

    ``views_per_frame`` dá, por modo, (vistas globais, vistas de tile): TILED não tem
    vista global, HYBRID tem uma.
    """
    from app.ml.serving import _timing_summary

    full = _timing_summary(list(full_ms))
    tile = _timing_summary(list(tile_ms))
    return {
        "session_run_ms": {"full_view": full, "tile_view": tile},
        "per_frame_estimate_ms_p50": {
            mode: round(full_views * full["p50"] + tile_views * tile["p50"], 3)
            for mode, (full_views, tile_views) in views_per_frame.items()
        },
        "estimate_rule": "vistas globais × p50 global + vistas de tile × p50 de tile",
    }


def measure_cpu_latency(images: Sequence[DevImage], resolution: str = "1080p") -> dict[str, Any]:
    """Mede ``session.run`` no CPUExecutionProvider em quadros reais do IRD (vista global e tiles)."""
    import platform

    import cv2
    import onnxruntime as ort

    if sha256_file(ONNX_PATH) != ONNX_SHA256:
        raise DevSetError("ONNX diverge do baseline congelado")
    session = ort.InferenceSession(str(ONNX_PATH), providers=["CPUExecutionProvider"])
    width, height = IRD_RESOLUTIONS[resolution]
    views = plan_views(
        width, height, SlicingConfig(mode="hybrid", tile_size=CACHE_TILE, overlap=CACHE_OVERLAP)
    )
    full_ms: list[float] = []
    tile_ms: list[float] = []
    seen = 0
    for image in images:
        original = _decode_bgr(_read_verified(image.path, image.sha256))
        frame = cv2.resize(original, (width, height), interpolation=cv2.INTER_AREA)
        for index, view in enumerate(views):
            _, _, elapsed = _view_candidates(session, frame, view)
            seen += 1
            if seen > LATENCY_WARMUP_VIEWS:
                (full_ms if index == 0 else tile_ms).append(elapsed)
    return {
        "source": "MEASURED_BY_REPORT",
        "provider": session.get_providers()[0],
        "processor": platform.processor() or platform.machine(),
        "logical_cpus": os.cpu_count(),
        "resolution": resolution,
        "images": [image.image_id for image in images],
        "warmup_views_discarded": LATENCY_WARMUP_VIEWS,
        "caveat": "só session.run; máquina de desenvolvimento compartilhada; não é o navegador",
        **latency_section(
            full_ms,
            tile_ms,
            {"full": (1, 0), "tiled": (0, len(views) - 1), "hybrid": (1, len(views) - 1)},
        ),
    }


TTA_VARIANTS: tuple[tuple[tuple[str, ...], str, float, bool], ...] = (
    (("flip",), "nms", 0.5, True),
    (("flip",), "wbf", 0.55, True),
    (("s448",), "nms", 0.5, True),
    (("s448",), "wbf", 0.55, True),
    (("flip", "s448"), "nms", 0.5, True),
    (("flip", "s448"), "wbf", 0.55, True),
    # Pós-hoc: escala 448 NO LUGAR da 640 (uma vista, sem custo extra).
    (("s448",), "nms", 0.5, False),
)


def tta_study() -> dict[str, Any]:
    """TTA de foto estática no IRD 1080p, com a mesma regra do fatiamento.

    Acrescentado depois do pré-registro (item 14 do pedido: só foto estática, só com ganho
    mensurável); usa a regra de modo pré-registrada: recall e F1 sobem em TUNE e CHECK
    e nenhuma classe perde mais de 0,03 de F1 em CHECK.
    """
    ird = load_cache("ird")
    extra: dict[str, tuple[dict[str, np.ndarray], dict[str, Any]]] = {}
    for entry in ird:
        path = CACHE_ROOT / "ird_tta" / f"{entry.image.image_id}.npz"
        if not path.is_file():
            raise DevSetError(f"cache TTA ausente: {entry.image.image_id} (rode `cache-tta`)")
        with np.load(path) as data:
            arrays = {key: data[key] for key in data.files}
        meta = json.loads(arrays.pop("meta").tobytes().decode("utf-8"))
        if meta["image_sha256"] != entry.image.sha256:
            raise DevSetError(f"cache TTA de {entry.image.image_id} é de outra imagem")
        extra[entry.image.image_id] = (arrays, meta)
    parts = {"tune": subset(ird, "tune"), "check": subset(ird, "check")}
    base = {part: score_set(entries, TTA_RESOLUTION, BASELINE) for part, entries in parts.items()}
    rows: dict[str, Any] = {}
    for kinds, merge, threshold, include_full in TTA_VARIANTS:
        profile = replace(BASELINE, slicing=SlicingConfig(merge=merge, merge_threshold=threshold))

        def detect(
            entry: CacheEntry,
            kinds: tuple[str, ...] = kinds,
            p: Profile = profile,
            full: bool = include_full,
        ) -> np.ndarray:
            arrays, meta = extra[entry.image.image_id]
            return tta_detections(entry, arrays, meta, p, kinds, include_full=full)

        scored = {
            part: score_set(entries, TTA_RESOLUTION, profile, detections_for=detect)
            for part, entries in parts.items()
        }
        ok = (
            all(
                scored[part]["micro_recall"] > base[part]["micro_recall"]
                and scored[part]["macro_f1"] > base[part]["macro_f1"]
                for part in parts
            )
            and _class_regression(scored["check"], base["check"]) >= -0.03
        )
        name = "+".join(kinds) + f"/{merge}@{threshold}"
        rows[name if include_full else f"only_{'+'.join(kinds)}"] = {
            "views_per_frame": len(kinds) + int(include_full),
            "replaces_full_view": not include_full,
            "accepted_by_rule": ok,
            **{part: _brief(scored[part]) for part in parts},
        }
    return {
        "schema_version": 1,
        "producer": "app.ml.camera_dev tta-report",
        "generated_at": datetime.now(UTC).isoformat(),
        "baseline_id": json.loads(BASELINE_FREEZE.read_text(encoding="utf-8"))["baseline_id"],
        "resolution": TTA_RESOLUTION,
        "status": "ADDED_AFTER_PREREGISTRATION_SAME_RULE",
        "test_accessed": False,
        "selection": {
            "runtime_change": (
                "NONE"
                if not any(row["accepted_by_rule"] for row in rows.values())
                else "PENDING_OWNER_AUTHORIZATION"
            ),
            "publishable_candidate": None,
            "note": "TTA só seria candidata para foto estática, nunca para a câmera ao vivo",
        },
        "data_use": data_use(),
        "baseline": {part: _brief(value) for part, value in base.items()},
        "variants": rows,
    }


PUBLISHED_MANIFEST = PROJECT_ROOT / "frontend/public/models/live-detection.json"
CALIBRATION_REPORT = (
    PROJECT_ROOT / "datasets/reports/live_detection_calibration_d429bde8a9bd_noupscale.json"
)
SHADOW_AUTHORIZATION = (
    PROJECT_ROOT
    / "datasets/metadata/shadow_authorizations/yolox-s-model-v2-d429bde8a9bd-profile-2702eb15.json"
)
BASELINE_FREEZE = PROJECT_ROOT / "datasets/reports/visual_sprint/baseline_freeze.json"


def _relative(path: Path) -> str:
    return path.relative_to(PROJECT_ROOT).as_posix()


def baseline_record() -> dict[str, Any]:
    """Baseline congelado a partir das fontes reais, com hashes conferidos."""
    manifest = json.loads(PUBLISHED_MANIFEST.read_text(encoding="utf-8"))
    authorization = json.loads(SHADOW_AUTHORIZATION.read_text(encoding="utf-8"))
    calibration = json.loads(CALIBRATION_REPORT.read_text(encoding="utf-8"))
    artifact = authorization["artifact"]
    if not (
        manifest["onnx"]["sha256"] == artifact["onnx_sha256"] == ONNX_SHA256
        and sha256_file(ONNX_PATH) == ONNX_SHA256
        and manifest["inference_profile"]["calibration_sha256"] == sha256_file(CALIBRATION_REPORT)
        and list(manifest["class_names"]) == list(CLASS_NAMES) == artifact["class_names"]
        and tuple(manifest["postprocess"]["class_score_thresholds"]) == BASELINE.thresholds
        and manifest["postprocess"]["nms_threshold"] == BASELINE.nms_threshold
        and manifest["postprocess"]["nms"] == "per_class"
        and manifest["input"]["letterbox"]["upscale"] is False
    ):
        raise DevSetError("baseline publicado diverge do ONNX/perfil/calibração registrados")
    evidence = calibration["evidence"]
    return {
        "model": {
            "model_version": manifest["model_version"],
            "onnx_path": artifact["onnx_path"],
            "onnx_sha256": ONNX_SHA256,
            "onnx_size_bytes": manifest["onnx"]["size_bytes"],
            "checkpoint_sha256": artifact["checkpoint_sha256"],
            "checkpoint_weights": "best.pt model_state_dict (raw), época 5 do run 10h-r1",
            "contract_path": artifact["model_contract_path"],
            "contract_sha256": artifact["model_contract_sha256"],
            "class_order": list(CLASS_NAMES),
            "input_size": artifact["input_size"],
            "opset": artifact["opset"],
            "preprocessing": manifest["input"],
            "output": manifest["output"],
            "scientific_status": manifest["scientific_status"],
        },
        "profile": {
            "inference_profile_sha256": manifest["inference_profile"]["sha256"],
            "calibration_path": _relative(CALIBRATION_REPORT),
            "calibration_sha256": manifest["inference_profile"]["calibration_sha256"],
            "postprocess": manifest["postprocess"],
            "slicing": SlicingConfig().profile_fields(),
            "published_manifest_path": _relative(PUBLISHED_MANIFEST),
            "published_manifest_sha256": sha256_file(PUBLISHED_MANIFEST),
            "shadow_authorization_path": _relative(SHADOW_AUTHORIZATION),
            "shadow_authorization_sha256": sha256_file(SHADOW_AUTHORIZATION),
        },
        "metrics_validation_recorded": {
            "source": _relative(CALIBRATION_REPORT),
            "split": "VALIDATION (RDD2022 China, já usada para ajuste)",
            "onnx_check": evidence["onnx_check"],
            "per_class": evidence["per_class_new"],
        },
        "runtime_recorded": {
            "browser": {
                "provider": "WebGPU (Edge/Chrome, Windows, esta máquina); WASM 1 thread como fallback",
                "latency_ms": "304–317 (câmera simulada), 2,1–2,2 análises/s",
                "source": "docs/LIVE_DETECTION.md (medido em 25/09/2026)",
            },
            "worker": {
                "provider": "CPUExecutionProvider",
                "source": "datasets/reports/live_detection_parity_2702eb15.json",
            },
        },
        "previously_rejected": {
            "clahe": "mAP50 0,159 → 0,062 em VALIDATION (fora da distribuição de treino)",
            "mirror_tta": "+0,005 F1 dobrando a latência",
            "ema_weights": "mAP50 0,109 contra 0,122 dos pesos raw a 640",
            "larger_input": "mAP50 800 = 0,064; 960 = 0,029 (ampliar prejudica)",
            "source": "docs/LIVE_DETECTION.md; " + _relative(CALIBRATION_REPORT),
        },
    }


def freeze_baseline() -> dict[str, Any]:
    record = baseline_record()
    canonical = json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    baseline_id = "VS-BASELINE-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if BASELINE_FREEZE.is_file():
        existing = json.loads(BASELINE_FREEZE.read_text(encoding="utf-8"))
        if existing["baseline_id"] != baseline_id:
            raise DevSetError("baseline congelado já existe com outro conteúdo; não sobrescrevo")
        return existing
    document = {
        "baseline_id": baseline_id,
        "frozen_at": datetime.now(UTC).isoformat(),
        "producer": "app.ml.camera_dev baseline",
        "rule": "imutável: nenhuma melhoria re-ajusta este baseline; todas comparam contra ele",
        **record,
    }
    BASELINE_FREEZE.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_FREEZE.write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    cache = sub.add_parser("cache", help="roda o ONNX por vista e guarda a saída bruta")
    cache.add_argument("--set", choices=sorted(SET_LOADERS), required=True)
    cache.add_argument("--limit", type=int)
    cache.add_argument("--reverse", action="store_true")
    sub.add_parser("baseline", help="congela o baseline publicado (BASELINE_ID imutável)")
    cache_tta = sub.add_parser("cache-tta", help="vistas TTA (espelho, escala menor) do IRD")
    cache_tta.add_argument("--limit", type=int)
    tta = sub.add_parser("tta-report", help="TTA de foto estática com a regra do fatiamento")
    tta.add_argument("--output", type=Path, required=True)
    report = sub.add_parser("report", help="executa o plano pré-registrado e grava o relatório")
    report.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "cache-tta":
        print(json.dumps(build_tta_cache(limit=args.limit), indent=2))
        return 0
    if args.command == "tta-report":
        document = tta_study()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        variants = {
            key: {
                "accepted": value["accepted_by_rule"],
                "check_f1": value["check"]["macro_f1"],
                "check_recall": value["check"]["micro_recall"],
            }
            for key, value in document["variants"].items()
        }
        print(json.dumps(variants, indent=2))
        return 0
    if args.command == "report":
        document = build_report()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "output": str(args.output),
                    "harness": document["harness_check"],
                    "slicing": document["slicing_decision"],
                    "selection": {
                        key: document["selection"][key]
                        for key in (
                            "BEST_SYSTEM",
                            "SELECTED_MODEL",
                            "SELECTED_PROFILE",
                            "THRESHOLD_CANDIDATE_REJECTED",
                            "runtime_change",
                        )
                    },
                    "cpu_latency": document["latency_cpu_onnxruntime"]["measured"][
                        "session_run_ms"
                    ],
                },
                indent=2,
                default=str,
            )
        )
        return 0
    if args.command == "baseline":
        document = freeze_baseline()
        print(
            json.dumps({"baseline_id": document["baseline_id"], "path": _relative(BASELINE_FREEZE)})
        )
        return 0
    if args.command == "cache":
        summary = build_cache(args.set, limit=args.limit, reverse=args.reverse)
        summary["generated_at"] = datetime.now(UTC).isoformat()
        print(json.dumps(summary, indent=2))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
