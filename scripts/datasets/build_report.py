"""Relatório final e registro de artefatos, gerados dos resultados reais da auditoria."""

from __future__ import annotations

import csv
import io
import json
from datetime import UTC, datetime

from _budget import preflight
from _core import (
    DATASETS_DIR,
    PROJECT_ROOT,
    free_disk_bytes,
    relative_to_project,
    require_local,
    write_text_safe,
)

MODIFIED = [
    ".gitignore",
    "datasets/.gitignore",
    "datasets/README.md",
    "datasets/STATUS.md",
    "datasets/metadata/sources.csv",
    "datasets/metadata/storage_budget.yaml",
    "datasets/manifests/rdd2022.json",
    "datasets/manifests/univali_br.json",
    "datasets/manifests/camber.json",
    "scripts/datasets/_core.py",
    "scripts/datasets/_budget.py",
    "scripts/datasets/_taxonomy.py",
    "scripts/datasets/audit_storage.py",
    "scripts/datasets/audit_rdd2022.py",
    "scripts/datasets/find_duplicates.py",
    "scripts/datasets/propose_subset.py",
    "scripts/datasets/make_splits.py",
    "scripts/datasets/validate_portability.py",
]


def read(relative):
    return json.loads(
        require_local(DATASETS_DIR / relative).read_text(encoding="utf-8-sig")
    )


def gb(value):
    return f"{value / 1_000_000_000:.4f} GB"


def table(headers, rows):
    return "\n".join(
        [
            "| " + " | ".join(headers) + " |",
            "|" + "|".join("---" for _ in headers) + "|",
        ]
        + ["| " + " | ".join(str(v) for v in row) + " |" for row in rows]
    )


def main():
    if (DATASETS_DIR / "reports/rdd_archive_release.json").exists():
        raise RuntimeError(
            "Relatório da etapa inicial é histórico; use o relatório de aquisição atual após mudanças autorizadas em raw"
        )
    preflight(
        "relatório final e registro de artefatos",
        2_000_000,
        4_000_000,
        raise_on_block=True,
    )
    # ``audit_storage.py`` is the single writer for this measured artifact.
    # The final report consumes that authoritative snapshot instead of silently
    # recomputing and overwriting it through a second producer.
    storage = read("reports/storage_audit.json")
    audit = read("reports/rdd2022_audit.json")
    dup = read("reports/rdd2022_duplicates.json")
    subset = read("reports/rdd2022_subset_proposal.json")
    split = read("splits/rdd2022_subset_splits.json")
    verification = read("reports/pipeline_verification.json")
    integrity = read("reports/source_integrity.json")
    safety = read("reports/raw_safety_verification.json")
    external = read("reports/external_locations.json")
    portability = read("reports/portability_check.json")
    if not all(r["passed"] for r in (verification, integrity, safety, portability)):
        raise RuntimeError("verificação falhou; relatório de conclusão bloqueado")
    sources = list(
        csv.DictReader(
            require_local(DATASETS_DIR / "metadata" / "sources.csv")
            .read_text(encoding="utf-8-sig")
            .splitlines()
        )
    )
    for row in sources:
        key = row["dataset_name"]
        if key in storage["by_dataset"]:
            row["local_size_bytes"] = str(storage["by_dataset"][key]["logical_size"])
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(sources[0]))
    writer.writeheader()
    writer.writerows(sources)
    write_text_safe(DATASETS_DIR / "metadata" / "sources.csv", buffer.getvalue())
    total = audit["totals"]
    selected = subset["subset"]
    rdd = storage["by_dataset"]["rdd2022"]
    labels = [
        "URMIND_ROAD_D00",
        "URMIND_ROAD_D10",
        "URMIND_ROAD_D20",
        "URMIND_ROAD_D40",
    ]
    lines = [
        "# UrMind — relatório da etapa de datasets",
        "Revisão complementar de presença, procedência, caches e divisão igual: [READINESS_REVIEW.md](READINESS_REVIEW.md).",
        f"\nGerado em {datetime.now(UTC).isoformat()}. GB = 1.000.000.000 bytes. Sem treino ou inferência.",
        "\n## 1–2. Estrutura inicial e final",
        "Inicial: raw, processed, annotations, metadata, splits, downloads, reports e manifests. Havia nove scripts e relatórios anteriores, mas nenhuma seleção por imagem nem split em lista.",
        "Final: raw, metadata, manifests, splits e reports. Por autorização posterior do usuário, foram removidas SOMENTE as pastas vazias processed, downloads e annotations. Nenhum arquivo dessas pastas existia. annotation (singular) não existia.",
        "Os nomes/pastas dentro de raw foram preservados. Não foram criados placeholders adicionais. A pasta de planejamento encontrada é docs/Planinng; ambos os documentos atuais foram lidos integralmente.",
        "\n## 3–5. Tamanhos atuais",
        table(
            ["Área", "Bytes lógicos", "GB decimais", "Bytes em disco"],
            [
                [name, m["logical_size"], gb(m["logical_size"]), m["size_on_disk"]]
                for name, m in [
                    ("datasets", storage["datasets_total"]),
                    ("RDD2022", rdd),
                ]
            ],
        ),
        f"ML contabilizado conservadoramente: {storage['ml_inventory']['total_ml_bytes']} bytes. A conta usa o maior entre lógico e alocado, somado ao conteúdo adicional identificado fora de datasets. Ambientes/dependências não são conteúdo essencial de dataset.",
        "Alocação medida via GetCompressedFileSizeW; não é mais a estimativa por clusters do relatório anterior. Não inclui toda a sobrecarga estrutural do volume. Cloud-only é contado logicamente e não hidratado.",
        f"Placeholders cloud-only em datasets: {storage['datasets_total']['cloud_only_count']}.",
        "\n## 6–7. Classes no RDD2022",
        table(
            ["Classe", "Imagens com a classe", "Objetos válidos", "% dos objetos"],
            [
                [
                    c,
                    total["images_per_class"].get(c, 0),
                    total["class_counts"].get(c, 0),
                    total["class_distribution_pct"].get(c, 0),
                ]
                for c in labels
            ],
        ),
        "Uma imagem pode conter mais de uma classe; as contagens de imagens por classe não devem ser somadas como total único. Labels originais permanecem no inventário e em raw.",
        "\n## 8. Distribuição por país/origem",
        table(
            [
                "Origem",
                "Imagens totais",
                "Objetos V1",
                "Negativas",
                "Bytes de imagens",
                "Arquivos na origem",
                "Bytes lógicos da pasta",
            ],
            [
                [
                    c,
                    m["images"],
                    m["objects"],
                    m["negative_images"],
                    m["size_bytes"],
                    m["storage"]["file_count"],
                    m["storage"]["logical_size"],
                ]
                for c, m in audit["by_country"].items()
            ],
        ),
        "São seis países e sete origens: China_Drone e China_MotorBike pertencem ao mesmo país. Detalhes de classes, alocação e contagens por origem estão em rdd2022_audit.json.",
        "\n## 9–11. Negativos, integridade e ausências",
        f"{total['negative_images']} negativas confirmadas (XML válido, zero objetos originais). {total['out_of_scope_only_images']} imagens apenas com classes fora da V1, mantidas separadas. {total['invalid_annotations']} XMLs associados a imagens com erro e {total['invalid_images']} imagens com falha de decodificação.",
        f"{total['images_without_annotation']} imagens sem XML, todas no test oficial. Imagens referenciadas por XML mas ausentes: {audit['missing_image_files']}. Lista nominal oficial: {integrity['rdd_archive_index']['actual_files']} arquivos, {len(integrity['rdd_archive_index']['missing'])} ausentes e {len(integrity['rdd_archive_index']['extra'])} extras.",
        "Anomalias: Japan_001265.xml contém caixa D20 degenerada; India_006389.xml contém D0w0 ambíguo. Não foram corrigidas em raw. A seleção exclui a imagem inteira com anotação problemática, sem inventar correção.",
        "MD5 do ZIP e dos três anexos oficiais RDD2022 conferidos; SHA-256 do UNIVALI confere. Os dois anexos CSV/metadata do CAMBER conferem com o MD5 oficial; MP4/GPX externos não têm checksum oficial registrado. Urban Community confere com o checksum local anterior, que não é checksum oficial. A lista nominal não equivale à checagem de CRC das entradas internas.",
        "\n## 12–14. Duplicatas e espaço potencial",
        f"Duplicatas exatas: {dup['exact_duplicate_groups']} grupos; economia potencial de {dup['exact_reclaimable_bytes']} bytes. Candidatos visuais: {dup['near_duplicate_groups']} grupos; limite superior hipotético de {dup['near_reclaimable_bytes_upper_bound']} bytes, sujeito a revisão.",
        "SHA-256 em todas as imagens locais. Similaridade: dHash horizontal+vertical de 128 bits, Hamming <= 4, filtro de desvio padrão >= 10 e componentes conexos. Relações transitivas ficam no mesmo grupo. Candidatos não provam duplicação nem identidade de sessão. Não somar economia visual e exata porque podem se sobrepor.",
        "O ZIP mestre RDD2022 ocupa 13.264.172.619 bytes, além da extração. Removê-lo no futuro, após autorização e decisão sobre recuperação, poderia reduzir a pasta para aproximadamente 13,84 GB sem eliminar imagens extraídas. Isso NÃO foi executado e não é classificado como duplicata byte a byte das imagens.",
        "\n## 15–21. Proposta de seleção e justificativa",
        f"Manifesto: datasets/manifests/rdd2022_subset_selection.jsonl. {selected['images']} imagens, {selected['objects']} objetos V1, {selected['negative_images']} negativos; {selected['size_bytes']} bytes de imagens ({gb(selected['size_bytes'])}). O tamanho de XMLs e manifestos deve ser acrescentado em uma futura materialização, cujo pico precisa de novo preflight.",
        table(
            ["Classe", "Imagens no subset", "Objetos no subset"],
            [
                [
                    c,
                    selected["images_per_class"].get(c, 0),
                    selected["class_counts"].get(c, 0),
                ]
                for c in labels
            ],
        ),
        table(
            ["Origem", "Imagens selecionadas", "Objetos", "GB imagens"],
            [
                [c, m["images"], m["objects"], gb(m["size_bytes"])]
                for c, m in subset["by_country"].items()
            ],
        ),
        "Seleção conservadora: preserva todas as imagens de train com XML íntegro e classe V1 ou negativo confirmado, exceto cópias byte a byte com annotation idêntica. Duplicatas com anotações diferentes permanecem para revisão. Mantém near-duplicates, com isolamento no split. Não usa score/byte, cota artificial, oversampling ou augmentation.",
        "Representatividade das quatro classes, origens e exemplos difíceis é preservada sem poda para caber num número arbitrário. Objetos pequenos e difficult do XML são registrados como indícios de dificuldade, não avaliação humana de qualidade visual. O conjunto íntegro já cabe abaixo do intervalo 12–14 GB; não há justificativa para preenchê-lo com redundância.",
        f"Fila de revisão: {len(subset['review_images'])} imagens. Exclusões de cópias exatas com mesma annotation: {len(subset['excluded_exact_copies'])}. Seed: {subset['seed']}; algoritmo v2 e hashes registrados. Nenhuma imagem copiada, movida ou removida.",
        "\n## 22. Proposta de train/validation/test",
        table(
            ["Split", "Imagens", "Objetos", "Proporção", "Grupos"],
            [
                [
                    name,
                    m["images"],
                    m["objects"],
                    f"{100 * m['fraction']:.2f}%",
                    ", ".join(m["groups"]),
                ]
                for name, m in split["splits"].items()
            ],
        ),
        "Países inteiros, união de componentes de duplicatas exatas/visuais, busca exaustiva sobre os grupos para cobertura de classes e proporções próximas de 70/15/15. Verificação independente: nenhum caminho, hash ou grupo detectado em múltiplos splits. Isso é isolamento dos vínculos conhecidos, não prova universal de ausência de leakage. Rotas, sessões e localizações não estão documentadas de forma utilizável no pacote.",
        "Teste é proposta independente por domínio/país e deve ser congelado antes do futuro treinamento. Não mede generalização brasileira. A proposta não foi promovida a configuração de YOLOX.",
        "Limitação do split: validação chinesa tem apenas 5 negativos, enquanto o teste tem 1.757. A validação não basta sozinha para medir falsos positivos em vias sem dano. Antes do treino, revisar protocolo de validação por domínio, sem acessar resultados do teste. As distribuições por país são naturalmente diferentes; não foram equalizadas artificialmente.",
        "Uma duplicata exata cruza train/test OFICIAIS dos Estados Unidos (United_States_003260 e United_States_005833). O test oficial não entra na seleção supervisionada. No split proposto, todos os membros selecionados de qualquer cluster conhecido ficam no mesmo lado.",
        "\n## 23. Tamanho das pastas efetivas",
        table(
            ["Pasta", "Bytes lógicos", "Bytes em disco", "Arquivos"],
            [
                [k, m["logical_size"], m["size_on_disk"], m["file_count"]]
                for k, m in storage["by_subfolder"].items()
            ],
        ),
        "As medidas são um snapshot anterior à gravação deste próprio relatório; pequenas diferenças de metadados são esperadas. Pastas vazias removidas tinham zero arquivos e zero bytes de conteúdo.",
        "\n## 24–26. Orçamento, disco e OneDrive",
        f"Alvo agregado 35 GB; máximo 40 GB, distribuição individual orientativa e redistribuível. Espaço livre medido: {storage['disk']['free_bytes']} bytes ({gb(storage['disk']['free_bytes'])}); preservar pelo menos 10 GB no pico.",
        "Projeto em pasta OneDrive: SIM, detectado pelo caminho. Não foi movido, não houve pausa/reconfiguração de sincronização nem hidratação forçada. DVC local foi inicializado sem remote; nenhum dataset foi adicionado ao cache DVC.",
        "\n## 27–28. Arquivos externos e preservação",
        table(
            ["Base dinâmica", "Localização relativa", "Bytes lógicos"],
            [
                [m["base"], m["relative_location"], m["logical_size"]]
                for m in external["matches"]
            ],
        ),
        "Busca limitada aos nomes relacionados em Downloads/Desktop/pasta pai; não é varredura completa do computador. Associação ao projeto é candidata pelo nome, não confirmação do conteúdo. Nenhum arquivo externo foi aberto para usar como planejamento, movido ou apagado.",
        f"Verificação de preservação de raw: {safety['passed']}. Assinatura de caminhos, tamanhos e mtime idêntica entre o baseline desta execução e a verificação final. O baseline foi tomado durante a auditoria, antes da remoção das pastas vazias. Ele não prova o histórico anterior à sessão. Não houve operação de escrita em raw.",
        "\n## 29–31. Arquivos modificados, scripts e metadata",
        "\n".join("- " + p for p in MODIFIED),
        "\nScripts adicionais desta continuação: "
        + ", ".join(
            p.name
            for p in sorted((PROJECT_ROOT / "scripts" / "datasets").glob("*.py"))
            if relative_to_project(p) not in MODIFIED
        )
        + ". Dependências declaradas em scripts/datasets/requirements.txt.",
        "\nMetadata atual: "
        + ", ".join(
            p.name for p in sorted((DATASETS_DIR / "metadata").iterdir()) if p.is_file()
        )
        + ". Taxonomia e mapeamentos originais preservados; fontes/orçamento atualizados. Registro de hashes dos artefatos em metadata/artifact_registry.json.",
        "\n## 32. Git e portabilidade",
        "Não existe repositório .git nesta cópia; não foi inicializado. .gitignore exclui raw, processed/downloads futuros, caches, arquivos temporários e ambientes, mantendo README, metadata, manifests, splits e relatórios versionáveis. Não foi incluída imagem, arquivo compactado nem credencial no Git.",
        f"Referências estruturadas verificadas: {portability['references_checked']}; aprovação de portabilidade: {portability['passed']}. Caminhos de dados relativos à raiz, resolução dinâmica com pathlib; sem symlinks/junctions criados. Registros antigos do backend marcados como legados e caminhos normalizados.",
        "\n## 33–34. Erros corrigidos, integração e riscos restantes",
        "- Corrigidos: GB/GiB; falso negativo por rótulo fora da V1; caixa inválida/valor não finito; falhas silenciosas em leituras; parser YAML parcial; escrita fora das pastas derivadas; dados cloud-only; algoritmo visual quadrático e truncamento dos grupos; independência de ordem; ligação por hashes; budget global com pico e reserva de disco.",
        "- A cadeia de datasets funciona e foi verificada com os dados reais. IDs de taxonomia compatíveis com schema existente. Não foi testado nem implementado runtime, backend, frontend ou treinamento.",
        "- Backend legado permite override externo URMIND_DATASETS_DIR, trabalha com caminhos relativos ao dataset e não consome automaticamente a seleção atual; seu critério usable exclui negativos. Ele continua intacto por restrição de escopo. artifact_contract.yaml documenta a ligação futura; não declarar integração automática pronta.",
        "- Licenças pendentes nas fontes futuras e abrangência às mídias externas CAMBER. Urban declara CC0 na ficha indexada, mas procedência detalhada e mapa numérico ainda exigem revisão. RDD2022 não substitui validação brasileira. Duas annotations precisam de decisão humana futura. Near-duplicates exigem confirmação visual.",
        "- Reconstrução sem arquivos grandes exige nova autorização e preflight: o ZIP externo contém ZIPs por origem; extração ingênua com todas as cópias simultâneas pode ultrapassar 40 GB. Scripts desta etapa não extraem nada.",
        "- A regra de orçamento protege estes scripts; não é quota do sistema operacional e não impede adições manuais ou sincronização do OneDrive. Uma auditoria futura contabiliza esse consumo e bloqueia novas operações quando necessário.",
        "\n## 35. Prioridade de futuras aquisições (nenhuma autorizada agora)",
        "1. Primeiro aproveitar e validar UNIVALI/DNIT já local; aquisição adicional brasileira somente se trouxer classes/regiões ausentes, após licença e protocolo. Preservar a reserva orientativa de 2 GB para dados próprios.",
        "2. Project Sidewalk: pequena exportação de uma cidade/área para acessibilidade. RampNet apenas em recorte útil e revisado; a divisão igual posterior reserva 4,125 GB a cada fonte, sem obrigação de preencher.",
        "3. CAMBER: novos CSV/GPS/metadata com relevância temporal e geográfica; vídeos somente se necessários. [Registro oficial](https://zenodo.org/records/21361827).",
        "4. Urban Community: não expandir antes de resolver procedência, licença e mapa de classes do material já local.",
        "5. BDD100K subset somente quando houver trabalho de navegação. 6. Mapillary subset somente para contexto/reconhecimento de lugar, com variante explicitamente escolhida.",
        "Essa ordem é uma recomendação técnica para o escopo atual. Por solicitação posterior, storage_budget.yaml v3 reserva 4,125 GB para cada uma das oito fontes (33 GB), 2 GB para dados próprios e 5 GB para modelos/processamento. É planejamento, não redução física do RDD2022 nem garantia de qualidade. A seleção existente de 10,85 GB foi preservada. Nenhuma aquisição cabe automaticamente: o pico e a ocupação real devem ser recalculados. Caches comuns de desenvolvimento podem usar suas localizações convencionais; dados volumosos de datasets/modelos devem permanecer no projeto.",
        "\nVerificações finais: "
        + ", ".join(verification["checks"])
        + ". Análise estática Ruff executada separadamente. A etapa para aqui e aguarda autorização para qualquer aquisição, alteração em raw ou integração fora do escopo.",
    ]
    write_text_safe(
        DATASETS_DIR / "reports" / "FINAL_REPORT.md", "\n\n".join(lines) + "\n"
    )
    status = f"""# Estado atual — etapa de datasets\n\nAuditoria concluída em 2026-09-08. Fonte detalhada: [relatório final](reports/FINAL_REPORT.md).\n\n- {total["images"]} imagens RDD2022 auditadas; {total["objects"]} caixas válidas da V1.\n- {total["negative_images"]} negativos confirmados; {total["invalid_annotations"]} annotations inválidas; originais preservados.\n- Manifesto proposto: {selected["images"]} imagens, {gb(selected["size_bytes"])}; nenhuma cópia física.\n- Splits e cadeia de hashes verificados. Teste permanece proposta a congelar antes do treino.\n- Pastas efetivas: raw, metadata, manifests, splits, reports. As três pastas vazias sem uso foram removidas por autorização.\n- Meta total 35 GB, máximo 40 GB; distribuição por fonte flexível.\n- Nenhum download, treino, inferência, DVC, Supabase ou alteração de backend/frontend executado.\n\nO backend legado não consome automaticamente os novos manifestos. Veja metadata/artifact_contract.yaml.\nOs relatórios anteriores foram substituídos por medições atuais; fatos históricos de ações de outra IA não são atribuídos a esta execução.\n"""
    write_text_safe(DATASETS_DIR / "STATUS.md", status)
    print("datasets/reports/FINAL_REPORT.md")
    print(
        {
            "datasets_logical_bytes": storage["datasets_total"]["logical_size"],
            "ml_accounted_bytes": storage["ml_inventory"]["total_ml_bytes"],
            "disk_free_bytes": free_disk_bytes(),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
