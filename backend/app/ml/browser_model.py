"""Publica um ONNX já exportado para a detecção ao vivo do navegador.

Só lê o registro gravado por `app.ml.serving export` (`models/serving/<id>-<sha>.json`)
e o ONNX ao lado dele; nunca lê checkpoint, nunca treina, nunca exporta. Publicar
entrega os bytes do modelo a qualquer visitante, por isso uso e distribuição exigem
autorização explícita na linha de comando.

    python -m app.ml.browser_model --manifest models/serving/<id>-<sha>.json \
        --status EXPERIMENTAL --authorize-use --authorize-distribution \
        --authorization-ref "docs/...#decisao"

Saída: `frontend/public/models/<id>-<sha>.onnx` e `frontend/public/models/live-detection.json`
(contrato em `frontend/src/domain/liveDetection.ts`).

`--calibration` aplica um ponto de operação calibrado só em VALIDATION para este mesmo
ONNX (hash conferido): NMS por classe e um limiar de confiança por classe. Não mexe nos
pesos; só no pós-processamento.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path
from typing import Any

from app.ml.serving import (
    PROJECT_ROOT,
    _canonical_artifact_hash,
    _is_sha256,
    sha256_file,
    validate_registration_manifest,
)
from app.ml.yolox_model import load_model_config

MANIFEST_NAME = "live-detection.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "frontend" / "public" / "models"
STATUSES = ("APPROVED", "EXPERIMENTAL", "DEMONSTRATION")
REJECTED_QUALITY = {"WEAK", "NOT APPROVED", "NOT_APPROVED", "FAILED", "REJECTED"}
# Único pré-processamento que o navegador reproduz (YOLOX preproc, BGR 0..255, pad 114).
SUPPORTED_PREPROC = "yolox.data.data_augment.preproc"


class BrowserPublishError(ValueError):
    pass


def _inside(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise BrowserPublishError(f"caminho fora do projeto: {path}") from exc
    return resolved


def build_browser_manifest(
    record_path: Path,
    *,
    status: str,
    authorize_use: bool,
    authorize_distribution: bool,
    authorization_ref: str,
    max_detections: int = 100,
    closure_path: Path | None = None,
    calibration_path: Path | None = None,
    project_root: Path = PROJECT_ROOT,
) -> tuple[dict[str, Any], Path]:
    """Valida o registro de export e devolve (manifesto do navegador, ONNX de origem)."""
    if status not in STATUSES:
        raise BrowserPublishError(f"status científico inválido: {status}")
    if not authorize_use or not authorize_distribution:
        raise BrowserPublishError(
            "publicar exige --authorize-use e --authorize-distribution explícitos"
        )
    if not authorization_ref.strip():
        raise BrowserPublishError("--authorization-ref é obrigatório")
    if not 0 < max_detections <= 1000:
        raise BrowserPublishError("max_detections fora de 1..1000")

    record_path = _inside(record_path, project_root)
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BrowserPublishError(f"registro de export ilegível: {record_path.name}") from exc
    try:
        # Exige VALIDATION medida e paridade PyTorch × ONNX aprovada.
        validate_registration_manifest(record, promote=False)
    except ValueError as exc:
        raise BrowserPublishError(str(exc)) from exc
    if str(record.get("quality_classification", "")).upper() in REJECTED_QUALITY:
        raise BrowserPublishError("o registro classifica o modelo como não aprovado")

    onnx_value = record.get("onnx_path")
    if not isinstance(onnx_value, str) or not onnx_value.endswith(".onnx"):
        raise BrowserPublishError("registro sem onnx_path finalizado")
    onnx = _inside(project_root / onnx_value, project_root)
    if not onnx.is_file():
        raise BrowserPublishError(f"ONNX ausente: {onnx_value}")
    onnx_sha256 = record.get("onnx_sha256")
    if not _is_sha256(onnx_sha256) or sha256_file(onnx) != onnx_sha256:
        raise BrowserPublishError("ONNX diverge do checksum registrado")

    contract_value = record.get("model_contract_path")
    if not isinstance(contract_value, str):
        raise BrowserPublishError("registro sem contrato do modelo")
    contract_path = _inside(project_root / contract_value, project_root)
    contract_sha256 = record.get("model_contract_sha256")
    if not contract_path.is_file() or sha256_file(contract_path) != contract_sha256:
        raise BrowserPublishError("contrato do modelo ausente ou alterado desde o export")
    try:
        contract = load_model_config(contract_path)
    except ValueError as exc:
        raise BrowserPublishError(str(exc)) from exc
    if (contract.get("preprocessing") or {}).get("implementation") != SUPPORTED_PREPROC:
        raise BrowserPublishError("pré-processamento do contrato não é o suportado no navegador")
    class_names = contract["canonical_class_names"]
    input_size = contract["test_size"]
    if record.get("class_names") != class_names or record.get("input_size") != input_size:
        raise BrowserPublishError("classes ou input_size do export divergem do contrato")

    lock = record.get("operating_point_lock")
    if isinstance(lock, dict):
        score_threshold = lock.get("confidence_threshold")
        nms_threshold = lock.get("nms_threshold")
    else:
        score_threshold = record.get("serving_score_threshold")
        nms_threshold = (contract.get("evaluation") or {}).get("nms_threshold")

    def _unit_threshold(name: str, value: Any) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= 1:
            raise BrowserPublishError(f"limiar de {name} ausente ou inválido")
        return float(value)

    score_threshold = _unit_threshold("score", score_threshold)
    nms_threshold = _unit_threshold("nms", nms_threshold)

    closure_sha256: str | None = None
    if closure_path is not None:
        closure_path = _inside(closure_path, project_root)
        try:
            closure = json.loads(closure_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BrowserPublishError("closure manifest ilegível") from exc
        closure_sha256 = closure.get("closure_manifest_sha256")
        if not _is_sha256(closure_sha256) or closure_sha256 != _canonical_artifact_hash(closure):
            raise BrowserPublishError("closure manifest adulterado ou sem hash canônico")
        # O closure congela o checkpoint; o ONNX se liga a ele pelo checkpoint_sha256 do export.
        closure_checkpoint = (closure.get("checkpoint") or {}).get("sha256")
        if not _is_sha256(closure_checkpoint) or closure_checkpoint != record.get(
            "checkpoint_sha256"
        ):
            raise BrowserPublishError("closure manifest é de outro checkpoint")
    if status == "APPROVED":
        if closure_sha256 is None or not isinstance(lock, dict):
            raise BrowserPublishError("APPROVED exige closure manifest e operating point lock")
        if (lock.get("quality_contract") or {}).get("decision") != "APPROVED":
            raise BrowserPublishError("APPROVED exige decisão de qualidade APPROVED em VALIDATION")

    postprocess: dict[str, Any] = {
        "score_threshold": float(score_threshold),
        "nms_threshold": float(nms_threshold),
        "nms": "class_agnostic",
        "max_detections": max_detections,
    }
    calibration_sha256: str | None = None
    letterbox: dict[str, Any] = {"pad_value": 114, "anchor": "top-left"}
    if calibration_path is not None:
        calibration_path = _inside(calibration_path, project_root)
        postprocess = _calibrated_postprocess(
            calibration_path,
            onnx_sha256=onnx_sha256,
            class_names=list(class_names),
            max_detections=max_detections,
            project_root=project_root,
        )
        calibration_sha256 = sha256_file(calibration_path)
        upscale = json.loads(calibration_path.read_text(encoding="utf-8")).get(
            "letterbox_upscale", True
        )
        if not isinstance(upscale, bool):
            raise BrowserPublishError("letterbox_upscale da calibração inválido")
        if not upscale:
            # Imagem menor que a entrada não é ampliada; o limiar foi medido assim.
            letterbox["upscale"] = False

    published_name = f"{contract['model_id']}-{onnx_sha256[:12]}.onnx"
    manifest = {
        "schema_version": 1,
        "model_id": contract["model_id"],
        "model_version": published_name.removesuffix(".onnx"),
        "scientific_status": status,
        "use_authorized": True,
        "distribution_authorized": True,
        "authorization_ref": authorization_ref.strip(),
        "onnx": {
            "path": f"/models/{published_name}",
            "sha256": onnx_sha256,
            "size_bytes": onnx.stat().st_size,
        },
        "input": {
            "name": "images",
            "size": list(input_size),
            "layout": "NCHW",
            "dtype": "float32",
            "color": "BGR",
            "range": "0..255",
            "normalization": "none",
            "letterbox": letterbox,
        },
        "output": {"name": "output", "format": "yolox_decoded_cxcywh_obj_cls"},
        "class_names": list(class_names),
        "postprocess": postprocess,
        "provenance": {
            "registration_manifest_sha256": sha256_file(record_path),
            "closure_manifest_sha256": closure_sha256,
            "contract_sha256": contract_sha256,
        },
    }
    manifest["inference_profile"] = {
        "sha256": inference_profile_sha256(manifest),
        "calibration_sha256": calibration_sha256,
    }
    return manifest, onnx


def inference_profile_sha256(manifest: dict[str, Any]) -> str:
    """Identidade da inferência inteira, não só dos pesos: ONNX, classes, entrada,
    saída e pós-processamento. Mudar limiar ou NMS muda o perfil."""
    from app.ml.training import canonical_sha256

    return canonical_sha256(
        {
            "onnx_sha256": manifest["onnx"]["sha256"],
            "class_names": manifest["class_names"],
            "input": manifest["input"],
            "output": manifest["output"],
            "postprocess": manifest["postprocess"],
        }
    )


def _calibrated_postprocess(
    path: Path,
    *,
    onnx_sha256: str,
    class_names: list[str],
    max_detections: int,
    project_root: Path,
) -> dict[str, Any]:
    """Ponto de operação calibrado: só VALIDATION, só para o ONNX exato."""
    try:
        calibration = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BrowserPublishError("calibração ilegível") from exc
    if calibration.get("onnx_sha256") != onnx_sha256:
        raise BrowserPublishError("calibração é de outro ONNX")
    if calibration.get("split") != "VALIDATION" or calibration.get("test_accessed") is not False:
        raise BrowserPublishError("calibração precisa ser medida só em VALIDATION")
    # A declaração "VALIDATION" só vale presa ao manifesto realmente usado.
    validation = calibration.get("validation_manifest") or {}
    if not isinstance(validation.get("path"), str) or not _is_sha256(validation.get("sha256")):
        raise BrowserPublishError("calibração sem manifesto de VALIDATION com hash")
    validation_path = _inside(project_root / validation["path"], project_root)
    if not validation_path.is_file() or sha256_file(validation_path) != validation["sha256"]:
        raise BrowserPublishError("manifesto de VALIDATION da calibração ausente ou alterado")
    nms = calibration.get("nms")
    nms_threshold = calibration.get("nms_threshold")
    thresholds = calibration.get("class_score_thresholds")
    if nms not in ("class_agnostic", "per_class"):
        raise BrowserPublishError("calibração com NMS desconhecido")
    if not isinstance(thresholds, dict) or list(thresholds) != class_names:
        raise BrowserPublishError("limiares da calibração não cobrem as classes na ordem")
    values = [thresholds[name] for name in class_names]
    for value in (nms_threshold, *values):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= 1:
            raise BrowserPublishError("limiar da calibração ausente ou inválido")
    return {
        "score_threshold": float(min(values)),
        "nms_threshold": float(nms_threshold),
        "nms": nms,
        "max_detections": max_detections,
        "class_score_thresholds": [float(value) for value in values],
    }


def publish(manifest: dict[str, Any], onnx: Path, output_dir: Path) -> Path:
    """Cópia atômica do ONNX, conferida por hash, e só então o manifesto."""
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / Path(manifest["onnx"]["path"]).name
    staging = target.with_suffix(".onnx.part")
    shutil.copyfile(onnx, staging)
    if sha256_file(staging) != manifest["onnx"]["sha256"]:
        staging.unlink()
        raise BrowserPublishError("cópia do ONNX corrompida")
    os.replace(staging, target)
    manifest_path = output_dir / MANIFEST_NAME
    temporary = manifest_path.with_suffix(".json.part")
    temporary.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", "utf-8")
    os.replace(temporary, manifest_path)
    return manifest_path


MODEL_BUCKET = "models"
MODEL_BUCKET_LIMIT_BYTES = 50 * 1024 * 1024


def storage_object_path(manifest: dict[str, Any]) -> str:
    """Immutable, content-addressed: another version or hash never shares a path."""
    return (
        f"{manifest['model_version']}/{manifest['onnx']['sha256']}/"
        f"{Path(manifest['onnx']['path']).name}"
    )


def publish_to_storage(
    manifest_path: Path, onnx: Path, settings: Any, client: Any
) -> dict[str, Any]:
    """Publish the distribution-authorized ONNX to the public model bucket.

    Admin-only (service key, never the frontend). The versioned browser manifest is
    the source of truth for version, SHA-256 and size. The bucket is separate from
    user photos; an existing object is verified, never overwritten; the public URL
    is downloaded and hashed before the result is returned.
    """
    import hashlib

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        manifest.get("distribution_authorized") is not True
        or manifest.get("use_authorized") is not True
    ):
        raise BrowserPublishError("manifesto sem autorização de uso e distribuição")
    if manifest.get("scientific_status") == "REJECTED":
        raise BrowserPublishError("modelo rejeitado não é distribuído")
    expected, size = manifest["onnx"]["sha256"], manifest["onnx"]["size_bytes"]
    if sha256_file(onnx) != expected or onnx.stat().st_size != size:
        raise BrowserPublishError("ONNX local diverge do manifesto")
    base = settings.supabase_url.rstrip("/") + "/storage/v1"
    key = settings.supabase_secret_key
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    bucket = client.get(f"{base}/bucket/{MODEL_BUCKET}", headers=headers)
    if bucket.status_code in (400, 404):
        created = client.post(
            f"{base}/bucket",
            headers=headers,
            json={
                "id": MODEL_BUCKET,
                "name": MODEL_BUCKET,
                "public": True,
                "file_size_limit": MODEL_BUCKET_LIMIT_BYTES,
                "allowed_mime_types": ["application/octet-stream"],
            },
        )
        if created.status_code not in (200, 201):
            raise BrowserPublishError(f"bucket não criado: HTTP {created.status_code}")
    elif bucket.status_code != 200:
        raise BrowserPublishError(f"bucket ilegível: HTTP {bucket.status_code}")
    elif bucket.json().get("public") is not True:
        raise BrowserPublishError("bucket de modelos existe mas não é público")
    object_path = storage_object_path(manifest)
    public_url = f"{base}/object/public/{MODEL_BUCKET}/{object_path}"
    existing = client.get(public_url)
    uploaded = False
    if existing.status_code != 200:
        response = client.post(
            f"{base}/object/{MODEL_BUCKET}/{object_path}",
            headers={**headers, "Content-Type": "application/octet-stream", "x-upsert": "false"},
            content=onnx.read_bytes(),
        )
        if response.status_code not in (200, 201):
            raise BrowserPublishError(f"upload recusado: HTTP {response.status_code}")
        uploaded = True
        existing = client.get(public_url)
    body = existing.content
    if (
        existing.status_code != 200
        or hashlib.sha256(body).hexdigest() != expected
        or len(body) != size
    ):
        raise BrowserPublishError("objeto público não confere com SHA-256/tamanho do manifesto")
    return {
        "bucket": MODEL_BUCKET,
        "object_path": object_path,
        "public_url": public_url,
        "sha256": expected,
        "size_bytes": size,
        "model_version": manifest["model_version"],
        "class_names": manifest["class_names"],
        "inference_profile_sha256": (manifest.get("inference_profile") or {}).get("sha256"),
        "manifest_schema_version": manifest.get("schema_version"),
        "uploaded": uploaded,
    }


def storage_main(argv: list[str]) -> int:
    import httpx

    from app.config import get_settings

    parser = argparse.ArgumentParser(prog="python -m app.ml.browser_model publish-storage")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    manifest_path = args.output_dir / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    onnx = args.output_dir / Path(manifest["onnx"]["path"]).name
    with httpx.Client(timeout=300, follow_redirects=True) as client:
        try:
            result = publish_to_storage(manifest_path, onnx, get_settings(), client)
        except BrowserPublishError as exc:
            raise SystemExit(f"BROWSER_MODEL_STORAGE_BLOCKED: {exc}") from exc
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    import sys

    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["publish-storage"]:
        return storage_main(argv[1:])
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", type=Path, required=True, help="registro de export")
    parser.add_argument("--status", choices=STATUSES, required=True)
    parser.add_argument("--authorize-use", action="store_true")
    parser.add_argument("--authorize-distribution", action="store_true")
    parser.add_argument("--authorization-ref", required=True)
    parser.add_argument("--closure", type=Path)
    parser.add_argument("--calibration", type=Path, help="ponto de operação calibrado")
    parser.add_argument("--max-detections", type=int, default=100)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--dry-run", action="store_true", help="valida sem copiar nada")
    args = parser.parse_args(argv)
    try:
        manifest, onnx = build_browser_manifest(
            args.manifest,
            status=args.status,
            authorize_use=args.authorize_use,
            authorize_distribution=args.authorize_distribution,
            authorization_ref=args.authorization_ref,
            max_detections=args.max_detections,
            closure_path=args.closure,
            calibration_path=args.calibration,
        )
    except BrowserPublishError as exc:
        raise SystemExit(f"BROWSER_MODEL_PUBLISH_BLOCKED: {exc}") from exc
    if not args.dry_run:
        print(f"publicado: {publish(manifest, onnx, args.output_dir)}")
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
