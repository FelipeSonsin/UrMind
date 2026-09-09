"""Duplicatas exatas e candidatos visuais do inventário RDD2022, sem apagar arquivos."""

import argparse
from collections import defaultdict

from _budget import preflight
from _core import configure_stdout, file_sha256, write_json_report
from audit_rdd2022 import INVENTORY, read_inventory


class UnionFind:
    def __init__(self, items):
        self.parent = {item: item for item in items}

    def find(self, item):
        while item != self.parent[item]:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, first, second):
        a, b = self.find(first), self.find(second)
        if a != b:
            self.parent[max(a, b)] = min(a, b)


def detect(rows, threshold=4):
    if not 0 <= threshold <= 8:
        raise ValueError("threshold precisa estar entre 0 e 8")
    by_sha = defaultdict(list)
    by_hash = defaultdict(list)
    for row in rows:
        if row.get("sha256"):
            by_sha[row["sha256"]].append(row)
        if row.get("dhash128") and row.get("gray_stddev", 0) >= 10:
            by_hash[int(row["dhash128"], 16)].append(row)
    exact = []
    for sha, members in sorted(by_sha.items()):
        if len(members) > 1:
            exact.append(
                {
                    "sha256": sha,
                    "files": sorted(r["rel_path"] for r in members),
                    "reclaimable_bytes": sum(r["size_bytes"] for r in members)
                    - members[0]["size_bytes"],
                }
            )
    # threshold+1 bandas disjuntas garantem ao menos uma banda igual para Hamming <= threshold.
    count = threshold + 1
    widths = [128 // count + int(i < 128 % count) for i in range(count)]
    hashes = sorted(by_hash)
    union = UnionFind(hashes)
    buckets = defaultdict(list)
    edges = 0
    for h in hashes:
        candidates = set()
        offset = 0
        keys = []
        for i, width in enumerate(widths):
            key = (i, (h >> offset) & ((1 << width) - 1))
            keys.append(key)
            candidates.update(buckets[key])
            offset += width
        for other in sorted(candidates):
            if (h ^ other).bit_count() <= threshold:
                union.union(h, other)
                edges += 1
        for key in keys:
            buckets[key].append(h)
    components = defaultdict(list)
    for h, members in by_hash.items():
        components[union.find(h)].extend(members)
    near = []
    for root, members in sorted(components.items()):
        if len(members) < 2 or len({r["sha256"] for r in members}) < 2:
            continue
        near.append(
            {
                "cluster": f"dhash128:{root:032x}",
                "files": sorted(r["rel_path"] for r in members),
                "countries": sorted({r["country"] for r in members}),
                "reclaimable_bytes_if_review_approved": sum(
                    r["size_bytes"] for r in members
                )
                - max(r["size_bytes"] for r in members),
            }
        )
    return exact, near, edges


def main():
    configure_stdout()
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--threshold", type=int, default=4)
    p.add_argument(
        "--near",
        action="store_true",
        help="compatibilidade; candidatos visuais sempre calculados",
    )
    p.add_argument("--no-report", action="store_true")
    a = p.parse_args()
    check = preflight(
        "relatório duplicatas", 32_000_000, 64_000_000, raise_on_block=True
    )
    rows = read_inventory()
    exact, near, edges = detect(rows, a.threshold)
    report = {
        "algorithm_version": 2,
        "source_inventory_sha256": file_sha256(INVENTORY),
        "images_analyzed": len(rows),
        "images_hashed": sum(bool(r.get("sha256")) for r in rows),
        "images_with_visual_hash": sum(bool(r.get("dhash128")) for r in rows),
        "method_exact": "SHA-256 completo; todas as imagens locais",
        "method_near": "dHash horizontal+vertical 128 bits; bandas completas; componentes conexos transitivos",
        "near_threshold": a.threshold,
        "near_edges_between_hashes": edges,
        "near_candidate_filter": "gray_stddev >= 10; candidatos exigem revisão visual; similaridade não prova mesma sessão",
        "exact_duplicates": exact,
        "near_duplicates": near,
        "exact_duplicate_groups": len(exact),
        "near_duplicate_groups": len(near),
        "exact_reclaimable_bytes": sum(g["reclaimable_bytes"] for g in exact),
        "near_reclaimable_bytes_upper_bound": sum(
            g["reclaimable_bytes_if_review_approved"] for g in near
        ),
        "note": "Não somar economia exata e visual: pode haver sobreposição. Nenhuma exclusão nem descarte automático de near-duplicates.",
        "preflight": check.as_dict(),
    }
    if not a.no_report:
        write_json_report("rdd2022_duplicates.json", report)
    print(
        {
            k: v
            for k, v in report.items()
            if k not in ("exact_duplicates", "near_duplicates", "preflight")
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
