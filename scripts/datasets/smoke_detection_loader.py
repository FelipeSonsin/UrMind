"""Executa o consumer real sem treino, epochs, otimização ou backprop."""

from __future__ import annotations

import sys
from itertools import islice

import torch
from _core import DATASETS_DIR, PROJECT_ROOT, file_sha256, write_json_report

sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.ml.detection_dataset import (
    AuthorizedDetectionDataset,
    build_yolox_dataloader,
    load_authorized_manifest,
)
from app.ml.yolox_model import instantiate_model, validate_yolox_batch


def main() -> int:
    path = DATASETS_DIR / "manifests/detection_train_authorized.jsonl"
    rows = load_authorized_manifest(path, intended_split="TRAIN")
    dataset = AuthorizedDetectionDataset(rows, mode="train")
    # Smoke prova o caminho real sem carregar o dataset inteiro em memória e sem
    # executar época: duas batches são suficientes para exercitar decode/labels.
    loader = build_yolox_dataloader(dataset, batch_size=2, num_workers=0, pin_memory=True)
    batches = list(islice(loader, 2))

    def locally_present(row: dict) -> bool:
        return (PROJECT_ROOT / row["image_path"]).is_file()

    negative = next(row for row in rows if not row["boxes"] and locally_present(row))
    one_box = next(row for row in rows if len(row["boxes"]) == 1 and locally_present(row))
    multi_box = next(row for row in rows if len(row["boxes"]) > 1 and locally_present(row))
    mixed_dataset = AuthorizedDetectionDataset([negative, one_box, multi_box], mode="train")
    mixed_batch = next(iter(build_yolox_dataloader(
        mixed_dataset, batch_size=3, num_workers=0, pin_memory=True
    )))
    images, targets, _, _ = mixed_batch
    model = instantiate_model()
    validate_yolox_batch(model, images, targets)

    cuda_ready = torch.cuda.is_available()
    vram_used = 0
    if cuda_ready:
        torch.cuda.empty_cache()
        before = torch.cuda.memory_allocated()
        cuda_images = images.to("cuda", non_blocking=False)
        cuda_targets = targets.to("cuda", non_blocking=False)
        torch.cuda.synchronize()
        vram_used = torch.cuda.memory_allocated() - before
        validate_yolox_batch(model, cuda_images, cuda_targets)
        del cuda_images, cuda_targets
    report = {
        "manifest": "datasets/manifests/detection_train_authorized.jsonl",
        "manifest_sha256": file_sha256(path),
        "manifest_loaded": True,
        "records": len(rows),
        "images": len(dataset),
        "positive_images": sum(bool(row["boxes"]) for row in rows),
        "negative_images": sum(not row["boxes"] for row in rows),
        "boxes": sum(len(row["boxes"]) for row in rows),
        "batches": len(batches),
        "batch_size": 2,
        "num_workers": 0,
        "pin_memory": True,
        "collate": "torch_default_collate_as_used_by_official_yolox",
        "windows_safe": True,
        "sampled_images": sum(batch[0].shape[0] for batch in batches),
        "mixed_batch": {
            "images_shape": list(images.shape),
            "images_dtype": str(images.dtype),
            "targets_shape": list(targets.shape),
            "targets_dtype": str(targets.dtype),
            "boxes_per_sample": (targets[:, :, 3] > 0).sum(dim=1).tolist(),
        },
        "cuda": {
            "available": cuda_ready,
            "batch_transferred": cuda_ready,
            "vram_used_bytes": vram_used,
        },
        "yolox_input_compatible": True,
        "forward_executed": False,
        "data_available": bool(rows),
        "epochs": 0,
        "backprop": False,
        "status": "PASSED" if rows and batches else "BLOCKED_NO_AUTHORIZED_DATA",
        "passed_consumer_integrity": bool(rows and batches),
        "data_manifest_schema_ready": bool(rows),
        "data_model_interface_ready": True,
    }
    write_json_report("detection_loader_smoke.json", report)
    print(
        f"YOLOX interface smoke: records={len(rows)} batches={len(batches)} "
        f"mixed_boxes={(targets[:, :, 3] > 0).sum(dim=1).tolist()} cuda={cuda_ready}"
    )
    return 0 if rows and batches else 2


if __name__ == "__main__":
    raise SystemExit(main())
