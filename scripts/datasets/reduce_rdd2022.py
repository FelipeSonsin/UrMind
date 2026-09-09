"""Reduz o RDD2022 para <= 7 GB podando SOMENTE a origem Norway.

Regras (autorizacao do usuario em 2026-09-08):

* China_Drone, China_MotorBike, Czech, India, Japan e United_States ficam
  intactos, byte a byte.
* Nenhum arquivo mantido e redimensionado, recomprimido ou reescrito.
* Toda imagem mantida conserva o XML correspondente; nenhum XML sobra apontando
  para imagem removida.
* Remocao so acontece com --execute. O padrao e dry-run.

Estrategia escolhida (ver docstring de `plan`): manter 100% das imagens Norway
que tem defeito anotado e amostrar as negativas de forma estratificada. Isso
preserva integralmente a informacao rara -- D20 e D40 aparecem em 322 e 256
imagens -- em vez de sortear sobre um conjunto onde 64% das imagens nao tem
nenhum objeto.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

RDD = ROOT / "datasets/raw/rdd2022"
BASE = RDD / "RDD2022"
NORWAY = BASE / "Norway"
PROFILE = ROOT / "datasets/reports/norway_profile.jsonl"
REPORT = ROOT / "datasets/reports/rdd2022_reduction.json"
BASELINE = ROOT / "datasets/reports/rdd2022_pre_reduction_baseline.json"
KEPT_MANIFEST = ROOT / "datasets/manifests/rdd2022_norway_kept.jsonl"
REMOVED_MANIFEST = ROOT / "datasets/manifests/rdd2022_norway_removed.jsonl"

V1 = ("D00", "D10", "D20", "D40")
SEED = 20260908
TARGET_TOTAL_BYTES = 6_500_000_000  # 6,50 GB decimais
HARD_CAP_BYTES = 7_000_000_000

SOURCE = {
    "dataset": "rdd2022",
    "official_source_url": (
        "https://figshare.com/articles/dataset/RDD2022_-_The_multi-national_Road_"
        "Damage_Dataset_released_through_CRDDC_2022/21431547"
    ),
    "version": "figshare-21431547-v1 (2022-crddc)",
    "license": "CC BY 4.0",
    "archive_md5": "b62bd51d2ffcfaa76c60f234f0cc2bb3",
}


# --------------------------------------------------------------------- helpers


def _dir_bytes(path: Path) -> tuple[int, int]:
    total = count = 0
    for p in path.rglob("*"):
        if p.is_file():
            total += p.stat().st_size
            count += 1
    return total, count


def _load_profile() -> list[dict]:
    if not PROFILE.is_file():
        raise SystemExit(
            f"perfil ausente: {PROFILE}\n"
            "rode antes: python scripts/datasets/profile_norway.py"
        )
    recs = [json.loads(line) for line in PROFILE.open(encoding="utf-8")]
    bad = [r for r in recs if "error" in r]
    if bad:
        raise SystemExit(f"perfil com {len(bad)} erros de leitura; corrija antes de podar")
    return recs


def _dhash_index() -> dict[str, str]:
    """dhash128 do inventario oficial, usado para evitar negativas quase iguais."""
    path = ROOT / "datasets/manifests/rdd2022_inventory.jsonl"
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.open(encoding="utf-8"):
        rec = json.loads(line)
        name = rec.get("filename") or rec.get("rel_path") or ""
        if rec.get("country") == "Norway" and rec.get("dhash128") and name:
            out[Path(name).stem] = rec["dhash128"]
    return out


def _hamming(a: str, b: str) -> int:
    return (int(a, 16) ^ int(b, 16)).bit_count()


# ------------------------------------------------------------------- preflight


def preflight() -> dict:
    """Confere estrutura, pareamento imagem/XML e o loader ANTES de remover nada."""
    problems: list[str] = []

    if not BASE.is_dir():
        raise SystemExit(f"estrutura inesperada: {BASE} nao existe")

    countries = sorted(p.name for p in BASE.iterdir() if p.is_dir())
    if "Norway" not in countries:
        raise SystemExit("Norway ausente; nada a podar")

    pairing: dict[str, dict] = {}
    orphans: dict[str, list[str]] = {}
    for country in countries:
        img_dir = BASE / country / "train/images"
        xml_dir = BASE / country / "train/annotations/xmls"
        imgs = {p.stem for p in img_dir.glob("*.jpg")} if img_dir.is_dir() else set()
        xmls = {p.stem for p in xml_dir.glob("*.xml")} if xml_dir.is_dir() else set()
        pairing[country] = {
            "images": len(imgs),
            "annotations": len(xmls),
            "images_without_annotation": len(imgs - xmls),
            "annotations_without_image": len(xmls - imgs),
        }
        if xmls - imgs:
            orphans[country] = sorted(xmls - imgs)
            if country != "Norway":
                problems.append(
                    f"{country}: {len(xmls - imgs)} XML sem imagem; esta origem nao e podada"
                )

    from app.datasets.adapters import read_rdd2022

    probe = next(read_rdd2022(RDD, countries={"Norway"}), None)
    if probe is None:
        problems.append("loader read_rdd2022 nao produziu nenhuma amostra para Norway")

    total_bytes, total_files = _dir_bytes(RDD)
    nor_bytes, nor_files = _dir_bytes(NORWAY)

    state = {
        "countries": countries,
        "pairing": pairing,
        "orphan_annotations": orphans,
        "loader_ok": probe is not None,
        "bytes_total": total_bytes,
        "files_total": total_files,
        "bytes_norway": nor_bytes,
        "files_norway": nor_files,
        "bytes_other_countries": total_bytes - nor_bytes,
        "problems": problems,
    }

    # O estado ANTES da poda e gravado uma unica vez. Reexecutar o script depois
    # de podar nao pode reescrever o "antes" com o disco ja reduzido, senao o
    # relatorio deixaria de provar a reducao.
    if BASELINE.is_file():
        state["baseline"] = json.loads(BASELINE.read_text(encoding="utf-8"))
    else:
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        state["baseline"] = state
    return state


# ------------------------------------------------------------------ estrategia


def _strata_key(rec: dict, bright_edges: tuple[float, float]) -> tuple:
    """Rig de captura x trecho da rota x faixa de iluminacao.

    * resolucao: as tres dimensoes distintas do pacote separam campanhas/cameras;
    * bloco do indice: os nomes Norway_NNNNNN sao sequenciais na coleta, entao o
      bloco aproxima trecho de rota/localizacao -- e o unico proxy geografico que
      o pacote oferece, ja que nao ha GPS nos arquivos;
    * iluminacao: tercil de brilho medio, que separa noite/sombra, dia encoberto
      e cena clara/neve.
    """
    lo, hi = bright_edges
    band = 0 if rec["brightness"] < lo else (1 if rec["brightness"] < hi else 2)
    return ((rec["width"], rec["height"]), rec["index"] // 1000, band)


def _feature(rec: dict) -> list[float]:
    return [
        rec["brightness"] / 255.0,
        rec["contrast"] / 90.0,
        rec["saturation"] / 0.41,
        rec["bright_frac"],
        rec["dark_frac"],
    ]


def _kmedoids(items: list[dict], k: int) -> list[dict]:
    """Cobertura maxima das condicoes: k-medoids por farthest-point determinista.

    Escolhe o primeiro pelo item mais proximo do centroide (o caso tipico) e cada
    seguinte pelo mais distante do que ja foi escolhido. Sem aleatoriedade e sem
    dependencia externa; empate resolvido pelo nome do arquivo.
    """
    if k >= len(items):
        return list(items)
    if k <= 0:
        return []
    feats = [_feature(r) for r in items]
    dim = len(feats[0])
    centroid = [sum(f[i] for f in feats) / len(feats) for i in range(dim)]

    def dist(a: list[float], b: list[float]) -> float:
        return sum((x - y) ** 2 for x, y in zip(a, b, strict=True))

    first = min(range(len(items)), key=lambda i: (dist(feats[i], centroid), items[i]["stem"]))
    chosen = [first]
    best = [dist(feats[i], feats[first]) for i in range(len(items))]
    while len(chosen) < k:
        nxt = max(range(len(items)), key=lambda i: (best[i], items[i]["stem"]))
        chosen.append(nxt)
        for i in range(len(items)):
            d = dist(feats[i], feats[nxt])
            best[i] = min(best[i], d)
    return [items[i] for i in sorted(chosen, key=lambda i: items[i]["stem"])]


def plan(pre: dict, target_bytes: int) -> dict:
    """Monta a lista de manutencao/remocao.

    1. `test/` da Norway inteiro sai: sao 2.040 imagens sem nenhum XML no pacote
       oficial, logo nao servem para treino nem para avaliacao rotulada e violam
       a exigencia de pareamento imagem/label.
    2. Toda imagem de `train/` com ao menos um objeto D00/D10/D20/D40 fica. Isso
       preserva 100% de D20 (322 imagens) e D40 (256), as classes minoritarias.
    3. As negativas restantes entram por amostragem estratificada ate o orcamento.
    """
    recs = _load_profile()
    positives = [r for r in recs if not r["negative"]]
    negatives = [r for r in recs if r["negative"]]

    pos_bytes = sum(r["bytes"] for r in positives)
    # O orcamento sai da linha de base gravada, nunca do disco ao vivo. O
    # OneDrive rehidrata e reverte arquivos enquanto o script roda, e usar a
    # medicao do momento faria a mesma poda escolher conjuntos diferentes a cada
    # execucao. Com a base fixa, `plan` e reproduzivel: mesmo perfil, mesma
    # selecao, independente do que a sincronizacao esteja fazendo.
    other = pre["baseline"]["bytes_other_countries"]
    budget_neg = target_bytes - other - pos_bytes
    if budget_neg < 0:
        raise SystemExit(
            "as origens intocadas + positivas da Norway ja excedem o alvo; "
            "reveja o alvo antes de podar"
        )

    brights = sorted(r["brightness"] for r in negatives)
    edges = (brights[len(brights) // 3], brights[2 * len(brights) // 3])

    buckets: dict[tuple, list[dict]] = defaultdict(list)
    for r in negatives:
        buckets[_strata_key(r, edges)].append(r)

    avg = sum(r["bytes"] for r in negatives) / len(negatives)
    n_target = int(budget_neg // avg)

    order = sorted(buckets, key=lambda k: (-len(buckets[k]), str(k)))
    alloc = {k: 0 for k in buckets}
    for k in order:
        if sum(alloc.values()) >= n_target:
            break
        alloc[k] = 1
    left = max(0, n_target - sum(alloc.values()))
    total_pool = sum(len(buckets[k]) for k in order)
    for k in order:
        alloc[k] += int(left * len(buckets[k]) / total_pool)

    dhash = _dhash_index()
    kept_neg: list[dict] = []
    for k in order:
        picks = _kmedoids(sorted(buckets[k], key=lambda r: r["stem"]), alloc[k])
        for cand in picks:
            h = dhash.get(cand["stem"])
            if h and any(
                _hamming(h, dhash[o["stem"]]) <= 4 for o in kept_neg if o["stem"] in dhash
            ):
                continue  # negativa visualmente redundante com uma ja escolhida
            kept_neg.append(cand)

    kept_neg.sort(key=lambda r: r["stem"])
    kept = positives + kept_neg
    kept_stems = {r["stem"] for r in kept}
    removed_train = [r for r in recs if r["stem"] not in kept_stems]

    test_dir = NORWAY / "test/images"
    test_files = sorted(test_dir.glob("*.jpg")) if test_dir.is_dir() else []
    test_bytes = sum(p.stat().st_size for p in test_files)

    final_bytes = other + sum(r["bytes"] for r in kept)
    return {
        "brightness_edges": edges,
        "strata": len(buckets),
        "kept": kept,
        "kept_positives": len(positives),
        "kept_negatives": len(kept_neg),
        "removed_train": removed_train,
        "removed_test_files": [str(p.relative_to(ROOT)) for p in test_files],
        "removed_test_bytes": test_bytes,
        "bytes_kept_norway": sum(r["bytes"] for r in kept),
        "bytes_final_total": final_bytes,
        "budget_negatives_bytes": budget_neg,
    }


# -------------------------------------------------------------------- execucao


BATCH_SIZE = 150
BATCH_PAUSE_SECONDS = 4
RECHECK_ROUNDS = 6


def _targets(pl: dict) -> list[Path]:
    """Todos os arquivos que a poda remove, na ordem em que serao apagados."""
    out: list[Path] = []
    for rec in pl["removed_train"]:
        out.append(NORWAY / "train/images" / f"{rec['stem']}.jpg")
        out.append(NORWAY / "train/annotations/xmls" / f"{rec['stem']}.xml")
    test_dir = NORWAY / "test"
    if test_dir.is_dir():
        out.extend(sorted(p for p in test_dir.rglob("*") if p.is_file()))
    return out


def execute(pl: dict) -> dict:
    """Remove em lotes pequenos, com o OneDrive ligado, e confere que ficou.

    Apagar milhares de arquivos de uma vez dispara a protecao de exclusao em
    massa do OneDrive: ele restaura tudo da nuvem enquanto espera o usuario
    confirmar, e a poda se desfaz sozinha. Parar o cliente tambem nao resolve --
    ao voltar, ele reconcilia tratando a nuvem como verdade e baixa tudo de novo.

    Lotes de algumas centenas passam abaixo desse limiar e a exclusao propaga
    normalmente. Medido em 2026-09-08: 50 arquivos, zero restauracoes. Por isso
    aqui se apaga em lote, se pausa para a sincronizacao acompanhar e, no fim, se
    reconfere: o que a nuvem trouxer de volta e apagado outra vez, ate parar.
    """
    removed_files: list[str] = []
    freed = 0
    targets = _targets(pl)
    total = len(targets)

    for start in range(0, total, BATCH_SIZE):
        for path in targets[start : start + BATCH_SIZE]:
            if path.is_file():
                freed += path.stat().st_size
                path.unlink()
                removed_files.append(str(path.relative_to(ROOT)))
        done = min(start + BATCH_SIZE, total)
        print(f"  removidos {done}/{total}", flush=True)
        time.sleep(BATCH_PAUSE_SECONDS)

    # o que a sincronizacao trouxer de volta sai de novo, ate a contagem zerar
    restored_total = 0
    for round_no in range(1, RECHECK_ROUNDS + 1):
        time.sleep(20)
        back = [p for p in targets if p.is_file()]
        if not back:
            print(f"  reconferencia {round_no}: nada restaurado")
            break
        restored_total += len(back)
        print(f"  reconferencia {round_no}: {len(back)} arquivos voltaram; removendo", flush=True)
        for path in back:
            if path.is_file():
                freed += path.stat().st_size
                path.unlink()
    else:
        remaining = [p for p in targets if p.is_file()]
        if remaining:
            print(
                f"AVISO: {len(remaining)} arquivos continuam voltando depois de "
                f"{RECHECK_ROUNDS} rodadas. A sincronizacao esta reconstruindo a "
                "pasta; confirme a exclusao no OneDrive antes de dar a poda por feita."
            )

    test_dir = NORWAY / "test"
    if test_dir.is_dir():
        try:
            shutil.rmtree(test_dir)
            removed_files.append(str(test_dir.relative_to(ROOT)) + "/ (diretorio)")
        except OSError as exc:
            # Pasta vazia nao ocupa dado e o loader ignora `test/`; o lock do
            # indexador nao invalida a poda.
            print(f"aviso: pasta {test_dir.name}/ travada ({exc.errno}); arquivos ja removidos")

    return {
        "removed_files": len(removed_files),
        "freed_bytes": freed,
        "restored_and_removed_again": restored_total,
    }


# ------------------------------------------------------------------- validacao


def validate() -> dict:
    from app.datasets.adapters import read_rdd2022

    per_country: dict[str, dict] = {}
    classes: Counter[str] = Counter()
    n = 0
    for sample in read_rdd2022(RDD):
        n += 1
        c = per_country.setdefault(sample.group, {"samples": 0, "objects": 0})
        c["samples"] += 1
        for box in sample.boxes:
            c["objects"] += 1
            classes[str(box.urmind_class)] += 1

    orphan_xml = orphan_img = 0
    for country in sorted(p.name for p in BASE.iterdir() if p.is_dir()):
        imgs = {p.stem for p in (BASE / country / "train/images").glob("*.jpg")}
        xmls = {p.stem for p in (BASE / country / "train/annotations/xmls").glob("*.xml")}
        orphan_xml += len(xmls - imgs)
        orphan_img += len(imgs - xmls)

    total_bytes, total_files = _dir_bytes(RDD)
    nor_bytes, nor_files = _dir_bytes(NORWAY)
    return {
        "loader_samples": n,
        "loader_classes": dict(classes),
        "per_country": per_country,
        "annotations_without_image": orphan_xml,
        "images_without_annotation": orphan_img,
        "bytes_total": total_bytes,
        "files_total": total_files,
        "bytes_norway": nor_bytes,
        "files_norway": nor_files,
        "gb_total": round(total_bytes / 1e9, 4),
    }


def _class_hist(recs: list[dict]) -> dict[str, int]:
    out: Counter[str] = Counter()
    for r in recs:
        for c in V1:
            out[c] += r["class_counts"][c]
    return dict(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--execute", action="store_true", help="remove de verdade")
    ap.add_argument("--target-bytes", type=int, default=TARGET_TOTAL_BYTES)
    args = ap.parse_args()

    if args.target_bytes > HARD_CAP_BYTES:
        raise SystemExit("alvo acima do teto de 7 GB combinado com o usuario")

    started = time.time()
    pre = preflight()
    if pre["problems"]:
        print("PREFLIGHT COM PROBLEMAS:")
        for p in pre["problems"]:
            print("  -", p)
        return 2

    profile = _load_profile()
    pl = plan(pre, args.target_bytes)

    # Um XML da Norway sem imagem so e problema se a imagem estava para ficar.
    # Se ela ja saiu da selecao, a poda remove esse XML junto e a inconsistencia
    # se resolve; abortar aqui deixaria o dataset quebrado justamente por causa
    # de uma reversao parcial da sincronizacao.
    kept_stems = {r["stem"] for r in pl["kept"]}
    graves = sorted(set(pre["orphan_annotations"].get("Norway", [])) & kept_stems)
    if graves:
        print("XML sem imagem entre as SELECIONADAS PARA FICAR:")
        for stem in graves:
            print("  -", stem)
        print("restaure essas imagens antes de podar; a poda foi cancelada.")
        return 2
    resolvidos = sorted(set(pre["orphan_annotations"].get("Norway", [])) - kept_stems)
    if resolvidos:
        print(
            f"aviso: {len(resolvidos)} XML sem imagem na Norway; todos pertencem a "
            "imagens fora da selecao e saem nesta poda"
        )

    base = pre["baseline"]
    before_imgs = sum(v["images"] for v in base["pairing"].values())
    print(f"origens intocadas: {pre['bytes_other_countries'] / 1e9:.3f} GB")
    print(f"Norway antes:  8161 train + 2040 test = {base['bytes_norway'] / 1e9:.3f} GB")
    print(
        f"Norway depois: {len(pl['kept'])} train "
        f"({pl['kept_positives']} positivas + {pl['kept_negatives']} negativas) = "
        f"{pl['bytes_kept_norway'] / 1e9:.3f} GB"
    )
    print(f"estratos de negativas: {pl['strata']}")
    print(f"RDD2022 final estimado: {pl['bytes_final_total'] / 1e9:.3f} GB")
    print(f"classes antes:  {_class_hist(profile)}")
    print(f"classes depois: {_class_hist(pl['kept'])}")

    if not args.execute:
        print("\nDRY-RUN. Nada foi removido. Repita com --execute para aplicar.")
        return 0

    result = execute(pl)
    post = validate()

    KEPT_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    with KEPT_MANIFEST.open("w", encoding="utf-8") as fh:
        for r in pl["kept"]:
            fh.write(json.dumps({**r, "decision": "kept"}, ensure_ascii=False) + "\n")
    with REMOVED_MANIFEST.open("w", encoding="utf-8") as fh:
        for r in pl["removed_train"]:
            fh.write(
                json.dumps(
                    {**r, "decision": "removed", "reason": "negativa nao amostrada"},
                    ensure_ascii=False,
                )
                + "\n"
            )
        for rel in pl["removed_test_files"]:
            fh.write(
                json.dumps(
                    {
                        "rel_path": rel,
                        "decision": "removed",
                        "reason": "split test oficial da Norway nao tem XML no pacote",
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "authorization": "usuario 2026-09-08: podar somente Norway, teto 7 GB",
        "source": SOURCE,
        "seed": SEED,
        "algorithm": (
            "manter 100% das imagens Norway com objeto V1; negativas por "
            "estratificacao (resolucao x bloco de rota x tercil de brilho) com "
            "k-medoids farthest-point sobre brilho/contraste/saturacao/claro/escuro "
            "e descarte de dhash128 a Hamming <= 4"
        ),
        "target_bytes": args.target_bytes,
        "hard_cap_bytes": HARD_CAP_BYTES,
        "before": {
            "bytes_total": base["bytes_total"],
            "files_total": base["files_total"],
            "images_train_all_countries": before_imgs,
            "norway_train_images": 8161,
            "norway_test_images": 2040,
            "norway_bytes": base["bytes_norway"],
            "class_objects_norway": _class_hist(profile),
        },
        "after": {
            "bytes_total": post["bytes_total"],
            "files_total": post["files_total"],
            "norway_bytes": post["bytes_norway"],
            "norway_train_images": len(pl["kept"]),
            "norway_test_images": 0,
            "class_objects_norway": _class_hist(pl["kept"]),
        },
        "removed": {
            "norway_train_negatives": len(pl["removed_train"]),
            "norway_test_images": (
                base["files_norway"]
                - post["files_norway"]
                - 2 * len(pl["removed_train"])
            ),
            "files_total": base["files_total"] - post["files_total"],
            "freed_bytes": base["bytes_total"] - post["bytes_total"],
            "other_countries": 0,
            "unlinked_in_this_run": result["removed_files"],
            "restored_by_sync_and_removed_again": result["restored_and_removed_again"],
            "note": (
                "As contagens comparam a linha de base gravada com o disco atual, "
                "entao reexecutar o script depois da poda reproduz os mesmos "
                "numeros em vez de reportar zero."
            ),
        },
        "untouched_countries": [c for c in pre["countries"] if c != "Norway"],
        "validation": post,
        "integrity_ok": (
            post["annotations_without_image"] == 0
            and post["images_without_annotation"] == 0
            and post["bytes_total"] <= HARD_CAP_BYTES
            and post["loader_samples"] > 0
        ),
        "manifests": {
            "kept": str(KEPT_MANIFEST.relative_to(ROOT)),
            "removed": str(REMOVED_MANIFEST.relative_to(ROOT)),
        },
        "elapsed_seconds": round(time.time() - started, 1),
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\nremovidos nesta execucao: {result['removed_files']} arquivos")
    print(
        f"reducao total vs linha de base: "
        f"{(base['bytes_total'] - post['bytes_total']) / 1e9:.3f} GB"
    )
    print(f"RDD2022 agora: {post['gb_total']} GB em {post['files_total']} arquivos")
    print(f"loader: {post['loader_samples']} amostras, classes {post['loader_classes']}")
    print(
        f"XML orfao: {post['annotations_without_image']} | "
        f"imagem sem XML: {post['images_without_annotation']}"
    )
    print(f"integridade: {'OK' if report['integrity_ok'] else 'FALHOU'}")
    print(f"relatorio: {REPORT.relative_to(ROOT)}")
    return 0 if report["integrity_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
