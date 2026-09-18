"""Regera datasets/metadata/sources.csv a partir do disco.

Gerado, nao escrito a mao: o numero publicado tem de vir do disco e dos
relatorios, senao a documentacao envelhece sem ninguem perceber.
"""

import argparse
import csv
import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "datasets/raw"
REPORTS = ROOT / "datasets/reports"

COLS = [
    "dataset_name",
    "official_source_url",
    "version",
    "license",
    "purpose",
    "local_path_relative",
    "local_size_bytes",
    "download_status",
    "date_accessed",
    "checksum_algo",
    "checksum_verified",
    "relevant_classes",
    "adapter",
    "notes",
]

def size_of(name: str) -> int:
    d = RAW / name
    if not d.is_dir():
        return 0
    return sum(p.stat().st_size for p in d.rglob("*") if p.is_file())


ROWS = [
    {
        "dataset_name": "rtk_br",
        "official_source_url": "https://data.mendeley.com/datasets/hssswvmjwf/1",
        "version": "mendeley-hssswvmjwf-v1",
        "license": "CC BY 4.0",
        "purpose": "road_damage",
        "download_status": "downloaded_extracted",
        "checksum_algo": "sha256 (arquivo oficial RTK.zip)",
        "checksum_verified": "sim",
        "relevant_classes": "pothole(candidate only); cracks(unmapped)",
        "adapter": "",
        "notes": (
            "701 imagens e class-masks oficiais. Pothole permanece candidato sem "
            "mapping/autorizacao; class-map nao prova semantica de instancia. ZIP "
            "temporario removido apos extracao; SHA-256 registrado na proveniencia."
        ),
    },
    {
        "dataset_name": "rdd2022",
        "official_source_url": (
            "https://figshare.com/articles/dataset/RDD2022_-_The_multi-national_Road_"
            "Damage_Dataset_released_through_CRDDC_2022/21431547"
        ),
        "version": "figshare-21431547-v1 (2022-crddc)",
        "license": "CC BY 4.0",
        "purpose": "road_damage",
        "download_status": "downloaded_extracted_exception_above_cap",
        "checksum_algo": "md5 (ZIP oficial)",
        "checksum_verified": "sim",
        "relevant_classes": "D00|D10|D20|D40",
        "adapter": "rdd2022",
        "notes": (
            "EXCECAO autorizada ao teto de 7 GB por dataset. A poda da origem Norway "
            "foi construida, executada e validada (6,49 GB, quatro classes intactas), "
            "mas o OneDrive a reverteu pela protecao de exclusao em massa e o usuario "
            "decidiu manter a fonte como esta. Nenhum objeto anotado se perdeu: as "
            "contagens de D00/D10/D20/D40 continuam completas; as 168 imagens que a "
            "restauracao parcial nao trouxe de volta sao todas negativas. Ver "
            "reports/rdd2022_reduction.json e reports/rdd2022_norway_partial_restore.json."
        ),
    },
    {
        "dataset_name": "univali_br",
        "official_source_url": "https://data.mendeley.com/datasets/t576ydh9v8/4",
        "version": "v4",
        "license": "CC BY 4.0",
        "purpose": "road_damage",
        "download_status": "downloaded_extracted",
        "checksum_algo": "sha256",
        "checksum_verified": "sim",
        "relevant_classes": "POTHOLE(mask)",
        "adapter": "univali_br",
        "notes": (
            "Imagens brasileiras de rodovia federal. Mascaras originais preservadas; "
            "conversao para caixa nao executada, porque perde informacao e precisa "
            "ser decisao registrada."
        ),
    },
    {
        "dataset_name": "urban_community",
        "official_source_url": (
            "https://www.kaggle.com/datasets/rajeevpaudel1/urban-community-issues"
        ),
        "version": "kaggle-2025",
        "license": (
            "CC0 declarada pelo uploader; NAO valida para todo o conteudo "
            "(imagens do Open Images listadas como CC BY 2.0)"
        ),
        "purpose": "urban_damage",
        "download_status": "downloaded_extracted",
        "checksum_algo": "sha256 (local)",
        "checksum_verified": "somente local",
        "relevant_classes": "pothole",
        "adapter": "urban_community",
        "notes": (
            "Sem checksum oficial publicado. O pacote nao traz data.yaml; o mapa "
            "id->classe foi inferido das pastas. Agrupamento fraco: usar como reforco, "
            "nao como conjunto de teste. PUBLICACAO RED (BLOCKED_LICENSE / "
            "BLOCKED_PROVENANCE): 1.800/2.518 imagens com nome compativel com ID do "
            "Open Images (inferido pelo padrao); 9 verificadas diretamente, 9/9 iguais "
            "em tamanho; Open Images lista CC BY 2.0 sem garantir cada imagem; 718 sem "
            "origem comprovada. CC0 do uploader nao substitui o direito dos autores. "
            "Nao republicar."
        ),
    },
    {
        "dataset_name": "project_sidewalk",
        "official_source_url": (
            "https://huggingface.co/datasets/projectsidewalk/rampnet-crop-model-dataset-round1"
        ),
        "version": "521f74ff752d57824400c8f7d5ca4717efa7bf16",
        "license": "MIT declarada na ficha oficial",
        "purpose": "accessibility",
        "download_status": "downloaded_subset",
        "checksum_algo": "sha256 (oficial)",
        "checksum_verified": "sim",
        "relevant_classes": "curb_ramp_keypoints",
        "adapter": "project_sidewalk",
        "notes": (
            "Recorte oficial de 5 shards Parquet dos 13,4 GB publicados; cada arquivo "
            "confere com o SHA-256 da fonte. Imagem embutida na linha, lida sem extrair. "
            "Keypoint nao vira caixa; recusado para a V1 com motivo."
        ),
    },
    {
        "dataset_name": "rampnet",
        "official_source_url": "https://huggingface.co/datasets/projectsidewalk/rampnet-dataset",
        "version": "ee882e3f3c779dc13182f307bca616e50d9b8c5c",
        "license": "MIT declarada na ficha oficial",
        "purpose": "accessibility",
        "download_status": "downloaded_subset",
        "checksum_algo": "sha256 (oficial)",
        "checksum_verified": "sim",
        "relevant_classes": "curb_ramp_keypoints + coordenada real",
        "adapter": "rampnet",
        "notes": (
            "Recorte oficial de 5 shards Parquet dos 462 GB publicados. Produz dois "
            "registros por rampa: ponto normalizado na panoramica (KeypointSample) e "
            "coordenada lat/lon real (GeoRecord, unico que exercita PostGIS aqui)."
        ),
    },
    {
        "dataset_name": "camber",
        "official_source_url": "https://zenodo.org/records/21361827",
        "version": "zenodo-21361827",
        "license": (
            "CC BY 4.0 no deposito Zenodo (CSV e TXT); licenca explicita do MP4/GPX "
            "externos nao demonstrada"
        ),
        "purpose": "longitudinal_risk",
        "download_status": "downloaded_sample",
        "checksum_algo": "md5 (anexos oficiais)",
        "checksum_verified": "parcial",
        "relevant_classes": "model_detections_not_ground_truth",
        "adapter": "camber",
        "notes": (
            "Amostra de um registro (video 50 e rota 51). As deteccoes do CSV vem de um "
            "YOLO de terceiros com user_confirmed vazio: e saida de modelo, nao ground "
            "truth, e nao vira rotulo de treino. PUBLICACAO YELLOW (REVIEW_REQUIRED, "
            "EXTERNAL_HEAVY_MEDIA_LICENSE_NOT_ESTABLISHED): DOI 10.5281/zenodo.21361827, "
            "v1.0. MP4/GPX em S3 externo; GPX MD5 == ETag e MP4 == ETag multipart. "
            "Metadata declara video com blur (sem verificacao independente). Grant: "
            "Zenodo 101156387 vs CORDIS 101146800."
        ),
    },
    {
        "dataset_name": "bdd100k",
        "official_source_url": "https://doc.bdd100k.com/download.html",
        "version": "v1-10k",
        "license": "BDD100K license; conferir antes de publicar resultado",
        "purpose": "navigation",
        "download_status": "downloaded_subset",
        "checksum_algo": "crc32 do ZIP + sha256 local",
        "checksum_verified": "somente local",
        "relevant_classes": "navigation_not_road_damage",
        "adapter": "bdd100k",
        "notes": (
            "Recorte oficial 10K + mapas de segmentacao. O MD5 da documentacao oficial "
            "tem 31 digitos hex e e invalido; nao ha checksum oficial utilizavel, entao "
            "a autenticidade da origem nao esta provada. Os ZIPs nao sao extraidos."
        ),
    },
    {
        "dataset_name": "global_streetscapes",
        "official_source_url": "https://huggingface.co/datasets/NUS-UAL/global-streetscapes",
        "version": "manual_labels-2024",
        "license": "CC BY-SA 4.0 (metadata; imagens Mapillary/KartaView tambem na origem)",
        "purpose": "urban_context",
        "download_status": "downloaded_subset",
        "checksum_algo": "sha256 por imagem (local)",
        "checksum_verified": "somente local",
        "relevant_classes": "contexto urbano; nenhuma classe da V1",
        "adapter": "global_streetscapes",
        "notes": (
            "SUBSTITUI mapillary_msls, cujo portal exige login. Redistribui imagem do "
            "proprio Mapillary (14.610) e do KartaView (397) sob CC BY-SA 4.0, sem "
            "gating e sem custo. Recorte estratificado por continente x plataforma da "
            "via x clima x iluminacao, espalhado por cidade e sequencia: 108 paises, "
            "398 cidades, 1.946 sequencias. Streaming seletivo: nada foi baixado para "
            "depois ser apagado. CC BY-SA obriga atribuicao e mesma licenca em derivados. "
            "PUBLICACAO YELLOW (REVIEW_REQUIRED / BLOCKED_ATTRIBUTION): sem campo de "
            "fotografo na selecao local (source, orig_id, sequence_id, sha256); "
            "termos do Mapillary para republicacao em massa nao resolvidos."
        ),
    },
]

out = ROOT / "datasets/metadata/sources.csv"


def previous_rows(path: Path) -> dict[str, dict[str, str]]:
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return {row["dataset_name"]: row for row in csv.DictReader(fh)}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reviewed",
        action="append",
        default=[],
        choices=[row["dataset_name"] for row in ROWS],
        help="fonte cuja evidencia foi efetivamente revisada nesta execucao",
    )
    parser.add_argument(
        "--review-date",
        type=date.fromisoformat,
        help="data ISO da revisao; obrigatoria quando --reviewed for usado",
    )
    args = parser.parse_args()
    if bool(args.reviewed) != bool(args.review_date):
        parser.error("--reviewed e --review-date devem ser usados juntos")
    return args


def resolve_date_accessed(
    row: dict, previous: dict[str, str] | None, reviewed: set[str], review_date: date | None
) -> str:
    """Preserva a data; so uma revisao explicitamente declarada pode altera-la."""
    if row["dataset_name"] in reviewed and review_date is not None:
        return review_date.isoformat()
    if not previous or not previous.get("date_accessed"):
        raise RuntimeError(
            f"{row['dataset_name']}: fonte nova exige --reviewed e --review-date"
        )
    evidence_columns = set(COLS) - {
        "date_accessed",
        "local_path_relative",
        "local_size_bytes",
    }
    changed = sorted(
        column
        for column in evidence_columns
        if str(row.get(column, "")) != previous.get(column, "")
    )
    if changed:
        raise RuntimeError(
            f"{row['dataset_name']}: evidencia mudou em {', '.join(changed)}; "
            "declare --reviewed e --review-date"
        )
    return previous["date_accessed"]


args = parse_args()
anteriores = previous_rows(out)
for row in ROWS:
    name = row["dataset_name"]
    row["local_path_relative"] = f"datasets/raw/{name}"
    row["local_size_bytes"] = size_of(name)
    row["date_accessed"] = resolve_date_accessed(
        row, anteriores.get(name), set(args.reviewed), args.review_date
    )

with out.open("w", encoding="utf-8", newline="") as fh:
    writer = csv.DictWriter(fh, fieldnames=COLS)
    writer.writeheader()
    for row in ROWS:
        writer.writerow({k: row.get(k, "") for k in COLS})

total = sum(r["local_size_bytes"] for r in ROWS)
print(f"sources.csv: {len(ROWS)} fontes, {total / 1e9:.2f} GB")
for r in ROWS:
    print(f"  {r['dataset_name']:22s} {r['local_size_bytes'] / 1e9:6.2f} GB")

removed = ROOT / "datasets/metadata/removed_sources.json"
removed.write_text(
    json.dumps(
        {
            "mapillary_msls": {
                "removed_at": "2026-09-08",
                "official_source_url": "https://github.com/mapillary/mapillary_sls",
                "reason": (
                    "O portal oficial exige login para qualquer download, inclusive a "
                    "amostra. O projeto nao tem credencial e nao pode usar servico pago."
                ),
                "replaced_by": "global_streetscapes",
                "replacement_rationale": (
                    "Cobre a mesma funcao — imagem street-level de contexto urbano — e "
                    "de fato redistribui imagem do proprio Mapillary sob CC BY-SA 4.0, "
                    "sem login. Ainda acrescenta rotulo humano de clima, iluminacao, "
                    "plataforma da via e qualidade, que o MSLS nao oferecia."
                ),
                "alternatives_considered": {
                    "Mapillary Vistas": "exige conta",
                    "Cityscapes": "exige registro",
                    "KartaView API": (
                        "aberto, mas exigiria raspagem imagem a imagem; o Global "
                        "Streetscapes ja entrega KartaView empacotado e rotulado"
                    ),
                },
            }
        },
        ensure_ascii=False,
        indent=2,
    ),
    encoding="utf-8",
)
print("removed_sources.json gravado")
