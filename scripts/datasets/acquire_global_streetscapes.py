"""Adquire um recorte do Global Streetscapes como substituto aberto do Mapillary MSLS.

Por que esta fonte, e nao o MSLS
--------------------------------
O portal oficial do Mapillary Street-Level Sequences exige login para baixar
qualquer coisa, inclusive a amostra, e o projeto nao tem credencial. O Global
Streetscapes (NUS Urban Analytics Lab) publica no Hugging Face, sem gating e sob
CC BY-SA 4.0, imagens street-level *das mesmas fontes* -- 58.147 vindas do
Mapillary e 1.999 do KartaView -- ja redistribuidas legalmente. Ou seja: e a
versao publica e gratuita do proprio Mapillary, que era a primeira preferencia.

Alem de cobrir a funcao original (contexto urbano street-level), ela acrescenta
o que o MSLS nao dava: rotulo humano de clima, iluminacao, plataforma da via,
qualidade, reflexo e brilho, com cidade, pais, continente e coordenada. Isso e
exatamente a diversidade de cenario que o UrMind precisa exercitar.

Como o espaco e respeitado
--------------------------
Os sete tarballs somam 23,85 GB e o teto por dataset e 5-7 GB. O script nao
baixa para depois apagar: ele decide a selecao a partir dos CSV de rotulo
(50 MB), abre os tarballs necessarios em streaming pela rede e grava em disco
somente os arquivos escolhidos. O pico de disco e o proprio recorte final.

Padrao dry-run. Só baixa com --execute.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import sys
import tarfile
import time
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

DATASET = "global_streetscapes"
REPO = "NUS-UAL/global-streetscapes"
REVISION = "main"
BASE_URL = f"https://huggingface.co/datasets/{REPO}/resolve/{REVISION}/"
HOMEPAGE = f"https://huggingface.co/datasets/{REPO}"
LICENSE = "CC BY-SA 4.0 (declarada na ficha oficial do dataset)"

RAW = ROOT / "datasets/raw" / DATASET
LABELS_DIR = RAW / "labels"
REPORT = ROOT / "datasets/reports" / f"acquisition_result_{DATASET}.json"
PLAN_PATH = ROOT / "datasets/metadata" / f"{DATASET}_acquisition_plan.json"

ATTRS = (
    "glare",
    "lighting_condition",
    "pano_status",
    "platform",
    "quality",
    "reflection",
    "view_direction",
    "weather",
)
META_COLS = (
    "uuid",
    "source",
    "orig_id",
    "city",
    "country",
    "continent",
    "lat",
    "lon",
    "datetime_local",
    "sequence_id",
    "sequence_index",
    "split",
    "img_path",
)

SEED = 20260908
TARGET_BYTES = 6_000_000_000
HARD_CAP_BYTES = 7_000_000_000
# Cada tarball e uma particao uniforme de 10.000 imagens do mesmo corpus, entao
# tres deles ja dao 30.000 candidatos cobrindo os seis continentes. Usar os sete
# so multiplicaria trafego sem ampliar a diversidade disponivel.
BUCKETS = ("1", "2", "3")


# ---------------------------------------------------------------------- rotulos


def _fetch(rel: str, dest: Path) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and dest.stat().st_size > 0:
        return dest.stat().st_size
    with urllib.request.urlopen(BASE_URL + rel) as resp, dest.open("wb") as fh:
        while chunk := resp.read(1 << 20):
            fh.write(chunk)
    return dest.stat().st_size


def load_labels(cache: Path) -> dict[str, dict]:
    """Une os 16 CSV oficiais numa linha por imagem."""
    rows: dict[str, dict] = {}
    for split in ("train", "test"):
        for attr in ATTRS:
            rel = f"manual_labels/{split}/{attr}.csv"
            local = cache / rel.replace("/", "__")
            _fetch(rel, local)
            with local.open(encoding="utf-8", errors="replace", newline="") as fh:
                for row in csv.DictReader(fh):
                    rec = rows.setdefault(
                        row["uuid"], {k: row.get(k, "") for k in META_COLS}
                    )
                    rec[attr] = row[attr]
    return rows


# ---------------------------------------------------------------------- selecao


def _bucket(rec: dict) -> str:
    """Tarball em que a imagem esta, lido do `img_path` oficial."""
    parts = rec["img_path"].split("/")
    return parts[1] if len(parts) > 2 else ""


def _stratum(rec: dict) -> tuple:
    """Continente x plataforma da via x clima x iluminacao.

    As quatro dimensoes que o usuario pediu preservar e que a fonte mede com
    rotulo humano: onde e o lugar, que tipo de via e, com que tempo e com que
    luz. Cidade nao entra como estrato porque sao 464 delas -- ela entra depois,
    como criterio de espalhamento dentro do estrato.
    """
    return (
        rec.get("continent") or "?",
        rec.get("platform") or "?",
        rec.get("weather") or "?",
        rec.get("lighting_condition") or "?",
    )


def select(rows: dict[str, dict], n_target: int) -> list[dict]:
    """Amostragem estratificada com piso por estrato e espalhamento por cidade.

    Dentro de cada estrato as imagens sao percorridas em rodadas por cidade e,
    dentro da cidade, por sequencia de captura. Assim uma cidade grande nao come
    a cota do estrato e duas fotos consecutivas da mesma rua nao entram antes de
    cidades ainda nao representadas.
    """
    pool = [r for r in rows.values() if _bucket(r) in BUCKETS]
    strata: dict[tuple, list[dict]] = defaultdict(list)
    for rec in pool:
        strata[_stratum(rec)].append(rec)

    order = sorted(strata, key=lambda k: (-len(strata[k]), str(k)))
    alloc = dict.fromkeys(order, 0)
    for key in order:  # piso: nenhum cenario desaparece
        if sum(alloc.values()) >= n_target:
            break
        alloc[key] = 1
    remaining = max(0, n_target - sum(alloc.values()))
    total = sum(len(strata[k]) for k in order)
    for key in order:
        alloc[key] += int(remaining * len(strata[key]) / total)

    chosen: list[dict] = []
    for key in order:
        by_city: dict[str, list[dict]] = defaultdict(list)
        for rec in sorted(strata[key], key=lambda r: (r["sequence_id"], r["uuid"])):
            by_city[rec["country"] + "/" + rec["city"]].append(rec)
        cities = sorted(by_city)
        picked: list[dict] = []
        index = 0
        while len(picked) < alloc[key] and any(index < len(by_city[c]) for c in cities):
            for city in cities:
                if len(picked) >= alloc[key]:
                    break
                if index < len(by_city[city]):
                    picked.append(by_city[city][index])
            index += 1
        chosen.extend(picked)

    # prioridade de corte: rodada entre estratos, para um estouro de bytes tirar
    # o excedente das caudas e nao zerar um cenario inteiro
    chosen.sort(key=lambda r: (_stratum(r), r["uuid"]))
    return chosen


# ------------------------------------------------------------------- streaming


def _already_written(bucket: str, wanted: dict[str, dict], out_dir: Path) -> list[dict]:
    """Imagens deste tarball que ja estao em disco de uma tentativa anterior."""
    done: list[dict] = []
    for uuid in wanted:
        dest = out_dir / bucket / f"{uuid}.jpeg"
        if dest.is_file() and dest.stat().st_size > 0:
            data = dest.read_bytes()
            done.append(
                {
                    "uuid": uuid,
                    "rel_path": str(dest.relative_to(RAW)).replace("\\", "/"),
                    "size_bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )
    return done


def stream_bucket(
    bucket: str, wanted: dict[str, dict], out_dir: Path, *, attempts: int = 3
) -> tuple[list[dict], str | None]:
    """Le `manual_labels/img/<bucket>.tar.gz` pela rede e grava so o que foi escolhido.

    O tarball tem ~4 GB e vem em fluxo unico: uma queda de conexao no meio perde
    a leitura, nao os arquivos ja gravados. Por isso a tentativa seguinte pula o
    que ja esta em disco -- ela regasta banda, nunca trabalho.

    O SHA-256 do tarball so e reportado quando o fluxo foi lido inteiro numa
    tentativa so; retomar invalida esse digest e ele volta `None`, em vez de um
    numero que nao corresponde a nada.
    """
    rel = f"manual_labels/img/{bucket}.tar.gz"
    written = _already_written(bucket, wanted, out_dir)
    if len(written) == len(wanted):
        print(f"  tarball {bucket} ja completo em disco ({len(written)} imagens)")
        return written, None
    if written:
        print(f"  retomando tarball {bucket}: {len(written)}/{len(wanted)} ja em disco")

    have = {w["uuid"] for w in written}
    resumed = bool(written)
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        outer = hashlib.sha256()

        class _Hashing:
            """Envelope que calcula o SHA-256 do tarball enquanto ele e consumido."""

            def __init__(self, raw, digest) -> None:
                self._raw = raw
                self._digest = digest

            def read(self, size: int = -1) -> bytes:
                chunk = self._raw.read(size)
                self._digest.update(chunk)
                return chunk

        try:
            with (
                urllib.request.urlopen(BASE_URL + rel) as resp,
                gzip.GzipFile(fileobj=_Hashing(resp, outer)) as gz,
                tarfile.open(fileobj=gz, mode="r|") as tar,
            ):
                for member in tar:
                    if not member.isfile():
                        continue
                    uuid = Path(member.name).stem
                    if uuid in have or uuid not in wanted:
                        continue
                    handle = tar.extractfile(member)
                    if handle is None:
                        continue
                    data = handle.read()
                    dest = out_dir / bucket / f"{uuid}.jpeg"
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(data)
                    have.add(uuid)
                    written.append(
                        {
                            "uuid": uuid,
                            "rel_path": str(dest.relative_to(RAW)).replace("\\", "/"),
                            "size_bytes": len(data),
                            "sha256": hashlib.sha256(data).hexdigest(),
                        }
                    )
        except (OSError, EOFError, tarfile.TarError) as exc:
            last_error = exc
            print(f"  tentativa {attempt}/{attempts} do tarball {bucket} caiu: {exc}")
            resumed = True
            continue

        return written, (None if resumed else outer.hexdigest())

    raise SystemExit(f"tarball {bucket}: {attempts} tentativas falharam; ultima: {last_error}")


# ------------------------------------------------------------------- relatorio


def _hist(rows, key) -> dict:
    return dict(Counter(r.get(key) or "?" for r in rows).most_common())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--execute", action="store_true", help="baixa e grava de verdade")
    ap.add_argument("--target-bytes", type=int, default=TARGET_BYTES)
    args = ap.parse_args()

    if args.target_bytes > HARD_CAP_BYTES:
        raise SystemExit("alvo acima do teto de 7 GB por dataset")

    cache = ROOT / "datasets/downloads" / DATASET
    print("lendo os CSV oficiais de rotulo (50 MB)...")
    rows = load_labels(cache)

    pool = [r for r in rows.values() if _bucket(r) in BUCKETS]
    avg_bytes = 389_000  # media medida no tarball 7 do proprio pacote
    n_target = min(len(pool), args.target_bytes // avg_bytes)
    chosen = select(rows, n_target)

    print(f"corpus rotulado: {len(rows)} imagens; candidatos nos tarballs {BUCKETS}: {len(pool)}")
    print(f"selecionadas: {len(chosen)} (~{len(chosen) * avg_bytes / 1e9:.2f} GB estimados)")
    print(f"continentes: {_hist(chosen, 'continent')}")
    print(f"plataforma:  {_hist(chosen, 'platform')}")
    print(f"clima:       {_hist(chosen, 'weather')}")
    print(f"iluminacao:  {_hist(chosen, 'lighting_condition')}")
    print(f"paises: {len({r['country'] for r in chosen})} | cidades: {len({r['city'] for r in chosen})}")
    print(f"fonte original: {_hist(chosen, 'source')}")

    plan = {
        "dataset": DATASET,
        "official_source_url": HOMEPAGE,
        "revision": REVISION,
        "license": LICENSE,
        "replaces": "mapillary_msls",
        "replacement_reason": (
            "O portal oficial do MSLS exige login para qualquer download. O Global "
            "Streetscapes redistribui, sob CC BY-SA 4.0 e sem gating, imagens "
            "street-level do proprio Mapillary e do KartaView, com rotulo humano de "
            "cenario. Cobre a funcao original sem credencial e sem custo."
        ),
        "seed": SEED,
        "buckets": list(BUCKETS),
        "target_bytes": args.target_bytes,
        "selection_algorithm": (
            "estratificacao por continente x plataforma da via x clima x iluminacao, "
            "com piso de 1 por estrato e espalhamento em rodadas por cidade e "
            "sequencia de captura"
        ),
        "selected": len(chosen),
        "group_basis": "sequence_id (sequencia de captura), para split sem vazamento",
    }
    PLAN_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLAN_PATH.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

    if not args.execute:
        print(f"\nplano gravado em {PLAN_PATH.relative_to(ROOT)}")
        print(
            f"DRY-RUN. Nada baixado. O --execute vai trafegar ~{len(BUCKETS) * 3.97:.1f} GB "
            f"pela rede e gravar ~{args.target_bytes / 1e9:.1f} GB em disco."
        )
        return 0

    acquired = RAW / f"acquired_{time.strftime('%Y%m%d')}"
    out_dir = acquired / "img"
    out_dir.mkdir(parents=True, exist_ok=True)

    by_bucket: dict[str, dict[str, dict]] = defaultdict(dict)
    for rec in chosen:
        by_bucket[_bucket(rec)][rec["uuid"]] = rec

    written: list[dict] = []
    tar_hashes: dict[str, str] = {}
    for bucket in BUCKETS:
        wanted = by_bucket.get(bucket, {})
        print(f"streaming tarball {bucket} ({len(wanted)} imagens escolhidas)...", flush=True)
        got, digest = stream_bucket(bucket, wanted, out_dir)
        tar_hashes[bucket] = digest
        written.extend(got)
        print(f"  gravadas {len(got)}; total {sum(w['size_bytes'] for w in written) / 1e9:.2f} GB")

    # corte por orcamento, se a media real ficou acima da estimada
    written.sort(key=lambda w: w["uuid"])
    order = {rec["uuid"]: i for i, rec in enumerate(chosen)}
    written.sort(key=lambda w: order.get(w["uuid"], 1 << 30))
    total = 0
    keep: list[dict] = []
    dropped = 0
    for item in written:
        if total + item["size_bytes"] > args.target_bytes:
            (RAW / item["rel_path"]).unlink(missing_ok=True)
            dropped += 1
            continue
        total += item["size_bytes"]
        keep.append(item)
    if dropped:
        print(f"corte por orcamento: {dropped} imagens removidas para caber em {args.target_bytes / 1e9:.1f} GB")

    kept_uuids = {w["uuid"] for w in keep}
    final = [r for r in chosen if r["uuid"] in kept_uuids]
    sizes = {w["uuid"]: w for w in keep}

    LABELS_DIR.mkdir(parents=True, exist_ok=True)
    labels_csv = LABELS_DIR / "global_streetscapes_selected.csv"
    cols = [*META_COLS, *ATTRS, "rel_path", "sha256", "size_bytes"]
    with labels_csv.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols)
        writer.writeheader()
        for rec in final:
            meta = sizes[rec["uuid"]]
            writer.writerow(
                {
                    **{k: rec.get(k, "") for k in META_COLS},
                    **{a: rec.get(a, "") for a in ATTRS},
                    "rel_path": meta["rel_path"],
                    "sha256": meta["sha256"],
                    "size_bytes": meta["size_bytes"],
                }
            )

    report = {
        "passed": bool(final) and total <= HARD_CAP_BYTES,
        "dataset": DATASET,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "official_source_url": HOMEPAGE,
        "revision": REVISION,
        "license": LICENSE,
        "replaces": "mapillary_msls",
        "images": len(final),
        "bytes": total,
        "gb": round(total / 1e9, 3),
        "labels_csv": str(labels_csv.relative_to(ROOT)).replace("\\", "/"),
        "tarball_sha256_as_streamed": tar_hashes,
        "checksum_limitations": (
            "O Hugging Face nao publica SHA-256 dos tarballs em formato consultavel "
            "por HEAD nesta consulta. O digest acima foi calculado sobre o fluxo "
            "recebido e cada JPEG gravado tem SHA-256 proprio no CSV; isso prova "
            "integridade local, nao autenticidade criptografica da origem."
        ),
        "distribution": {
            "continent": _hist(final, "continent"),
            "platform": _hist(final, "platform"),
            "weather": _hist(final, "weather"),
            "lighting_condition": _hist(final, "lighting_condition"),
            "quality": _hist(final, "quality"),
            "source": _hist(final, "source"),
            "split_oficial": _hist(final, "split"),
            "countries": len({r["country"] for r in final}),
            "cities": len({r["city"] for r in final}),
            "sequences": len({r["sequence_id"] for r in final}),
        },
        "selection_algorithm": plan["selection_algorithm"],
        "seed": SEED,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n{len(final)} imagens, {report['gb']} GB")
    print(f"{report['distribution']['countries']} paises, {report['distribution']['cities']} cidades, {report['distribution']['sequences']} sequencias")
    print(f"rotulos: {labels_csv.relative_to(ROOT)}")
    print(f"relatorio: {REPORT.relative_to(ROOT)}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
