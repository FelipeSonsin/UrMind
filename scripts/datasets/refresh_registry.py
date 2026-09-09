"""Recalcula datasets/metadata/artifact_registry.json.

Gerado, nao escrito a mao: o numero publicado tem de vir do disco e dos
relatorios, senao a documentacao envelhece sem ninguem perceber.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
import hashlib
import json
import time

ROOT = Path(__file__).resolve().parents[2]
REG = ROOT / "datasets/metadata/artifact_registry.json"

d = json.loads(REG.read_text(encoding="utf-8"))


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        while chunk := fh.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


# scripts: todos os .py de scripts/datasets, sem __pycache__
scripts = sorted(
    p for p in (ROOT / "scripts/datasets").glob("*.py") if p.name != "__init__.py"
)
d["scripts"] = [
    {"path": str(p).replace("\\", "/"), "sha256": sha(p)} for p in scripts
]

# artefatos: os que ja estavam listados e continuam existindo, mais os desta etapa
antigos = [a["path"] for a in d.get("artifacts", [])]
novos = [
    "datasets/metadata/removed_sources.json",
    "datasets/metadata/global_streetscapes_acquisition_plan.json",
    "datasets/manifests/rdd2022_norway_kept.jsonl",
    "datasets/manifests/rdd2022_norway_removed.jsonl",
    "datasets/reports/norway_profile.jsonl",
    "datasets/reports/rdd2022_reduction.json",
    "datasets/reports/rdd2022_pre_reduction_baseline.json",
    "datasets/reports/rdd2022_norway_partial_restore.json",
    "datasets/reports/rdd2022_reconciliation.json",
    "datasets/reports/acquisition_result_global_streetscapes.json",
    "datasets/raw/global_streetscapes/labels/global_streetscapes_selected.csv",
]
todos = []
for rel in dict.fromkeys(antigos + novos):
    p = ROOT / rel
    if not p.is_file():
        print(f"  fora do registro (nao existe): {rel}")
        continue
    todos.append(
        {"path": rel.replace("\\", "/"), "size_bytes": p.stat().st_size, "sha256": sha(p)}
    )
d["artifacts"] = sorted(todos, key=lambda a: a["path"])

d["updated"] = time.strftime("%Y-%m-%d")
d["status"] = (
    "aquisicoes concluidas e legiveis pelo backend; RDD2022 mantido como excecao "
    "ao teto por decisao do usuario. Nao e configuracao de treinamento."
)

REG.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"registro: {len(d['scripts'])} scripts, {len(d['artifacts'])} artefatos")
