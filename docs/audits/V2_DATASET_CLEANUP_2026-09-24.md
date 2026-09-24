# V2 dataset cleanup / taxonomy V3 — 2026-09-24

## Aditivo — remoção autorizada do quality gate

Decisão posterior supera o KEEP histórico abaixo. Alvo único:
datasets/metadata/yolox_quality_gate.json, untracked, 1.829 bytes.
SHA-256 recalculado pelo gerador oficial antes de excluir:
93b83a32c26eb828c57e2c208b66c73dac518327a48483c52961ed13c88c59f0.
Conteúdo integral preservado na seção histórica deste relatório.
Removido individualmente com SendToRecycleBin; Test-Path=False confirmado.
Nenhum quality gate substituto criado. Total acumulado: 19 arquivos e
28.844.712 bytes lógicos enviados à Lixeira, que não foi esvaziada.
Não houve escrita Supabase nesta operação adicional.

Dependências: removida a entrada required no artifact_contract.yaml; registry
regenerado exclusivamente pelo gerador oficial (51 scripts / 72 artefatos).
refresh_registry.py --check: zero stale/missing/unregistered/orphaned/unreadable.
EXTENDED_EXISTING_COMPONENT: serving.py agora recusa contrato ausente com
QUALITY_GATE_NOT_AVAILABLE antes de I/O do modelo/artefatos na seleção e no
lock operacional, e na função de aplicação dos critérios. Sem defaults nem
substituição silenciosa de thresholds. Novo teste em test_model_closure.py usa
path temporário inexistente e proíbe leitura de config/modelo; falhou antes do
fix e passou após. Nenhuma inferência/avaliação científica foi executada.

Busca final por nome do arquivo e identificador do contrato: zero referências
operacionais em código/configs/scripts/registry/testes/frontend. Restam apenas
as referências deliberadas NESTE relatório, inclusive o conteúdo integral
solicitado. “Zero referências” não significa apagar evidência histórica.
PROJECT_STATE atualizado; as decisões KEEP e resultados abaixo são anteriores
e foram superados exclusivamente por este aditivo.

Regressão focada: 55 passed. Frontend: 45 passed, Prettier e build/TypeScript
passaram. Ruff, formatação dos Python alterados, mypy (72 módulos) e
git diff --check passaram. Build mantém warning preexistente de chunk >500 kB.
Hashes do peso de origem e dos dois registros Frozen Test continuam iguais
aos registrados abaixo. Nenhuma alteração em raw, imagens, rótulos, taxonomia
ou .env. Sem falhas de permissão/arquivo em uso nesta execução.

Suíte backend final: 1230 passed / 20 skipped / 2 warnings (59,22 s).
Variação: +1 teste negativo, nenhum teste removido nesta operação adicional.
Skips: 19 integrações DEV opt-in sem URLs no processo e 1 live externo opt-in;
não executados separadamente, não tratados como pass. Sem alteração cloud que
exigisse teste remoto nesta remoção local. Gates aplicáveis: PASS; findings
novos pendentes: nenhum. Não representa training readiness nem E2E real.

## Resultado atual — PARTE A concluída

PARTE_A_STATUS=PASS; V2_DATASET_STATUS=REMOVED.
REMOVED_FILES=18; REMOVED_LOGICAL_BYTES=28842883.
NEW_SPLIT_REQUIRED=YES; YOLOX_TRAINING_READY=NO.

Escopo deste PASS: retomada A1–A4 dos 18 caminhos autorizados; não encerra
findings do produto nem a regra elétrica candidata da Parte B anterior.
Nenhum item AMBIGUOUS pendente de exclusão: yolox_quality_gate.json passou a
KEEP por decisão explícita do usuário e pela análise abaixo.

Remoção individual via Microsoft.VisualBasic.FileIO.FileSystem.DeleteFile com
RecycleOption.SendToRecycleBin, sem recursão ou exclusão permanente. Antes:
18 paths absolutos restritos à raiz do projeto, tamanho e SHA-256 revalidados,
checagem de atributos de recall e bloqueio de paths protegidos. Depois:
Test-Path=False para cada arquivo. Todos eram untracked; não existiam remoções
tracked para encenar no Git. Nenhum commit. Lixeira NÃO esvaziada; recuperação
permanece sujeita ao Windows/OneDrive. Cópias externas/backups não expurgados.

### Ajustes de dependências

- backend/app/ml/training.py: ausência dos manifests/split/autorização agora
  falha com TrainingGateError DATASET_SPLIT_NOT_AVAILABLE antes de prosseguir.
- scripts/datasets/build_detection_manifests.py: V2 sem split/status interrompe
  antes de materializar outputs.
- scripts/datasets/prepare_rdd_split_authorization.py: ausência de V2_SPLIT
  interrompe antes de criar folha de autorização.
- scripts/datasets/audit_ird_dashcam.py: comparação sem manifests V2 interrompe;
  ausência NÃO vira ausência de duplicatas/contaminação.
- backend/tests/test_training_engine.py: retirado glob de configs reais; fixture
  temporária independente cobre os perfis simples/augmentation/baixo uso de workers.
  Mantidas recusas de TEST, requisitos EMA, loaders e parâmetros sem rotation/shear.
  Removido apenas test_v2_train_and_validation_exclude_v1_historical_test:
  validava exclusivamente o conteúdo do split real que o usuário mandou remover.
  Não foi substituído por falsa prova de independência de um futuro holdout.
- backend/tests/test_artifact_registry.py: teste de falha antes de escrita para
  os três consumidores CLI. Nenhum dado real lido/escrito por esse teste.
- datasets/metadata/artifact_contract.yaml: 18 entradas removidas de required;
  demais declarações preservadas, inclusive quality_gate e taxonomia.
- datasets/metadata/artifact_registry.json: regenerado EXCLUSIVAMENTE via
  scripts/datasets/refresh_registry.py. Verificação posterior: 51 scripts,
  73 artefatos, zero stale/missing/unregistered/orphaned/unreadable.

EXTENDED_EXISTING_COMPONENT em produção/testes; nenhum módulo ou registry paralelo.
As referências V2 restantes são contratos de geradores históricos e consumidores
com guarda testada, ou documentação histórica. Não há leitura V2 na importação;
não há entradas órfãs V2 no registry/required. make_splits_v2.py foi preservado
como código, não executado; não constitui autorização para recriar split.
Nenhum ponteiro *.dvc/dvc.yaml/dvc.lock V2 encontrado na varredura de nomes.

### DEV — arquivamento auditado

Alvo reconfirmado imediatamente antes da escrita pelo plugin oficial:
Urmind DEV, impm…ggy, ACTIVE_HEALTHY.
DatasetVersion ba253b0b-0ade-46dc-95bc-3219a03e816e:
split.lifecycle.status=ARCHIVED.
Não existe coluna status dedicada; usado metadado lifecycle no JSONB existente,
sem migration. Não foi apagado nem alterado fingerprint, referência histórica,
classes, licença ou identidade do dataset.

Transação única: SELECT FOR UPDATE + validação ID/nome/versão/fingerprint +
UPDATE lifecycle + INSERT AuditLog. Recusa repetição se operação já registrada.
AuditLog b06d158a-6bdf-4e9d-b43a-19bb8bf664ee,
operation=archive_v2_dataset_artifacts, actor=user-authorized-maintenance.
Timestamp: 2026-09-24T15:03:53Z.
Verificação SQL posterior: lineage_preserved=true; audit_hash_valid=true.
Hash sobre JSONB before/after/entity_id em UTF-8; preservados snapshots integrais.
Nenhuma operação em Auth, ModelVersion, outro projeto ou estrutura do banco.
Alembic mantém 0021_history_snapshot_retention.

### Testes / evidências desta retomada

| Verificação | Resultado | Nível |
|---|---|---|
| 3 testes novos de ausência antes do fix | 3 falhas reproduzidas por mensagem genérica | unit |
| pytest tests/test_training_engine.py tests/test_artifact_registry.py -q | 49 passed | unit/arquivos temporários |
| pytest -q -ra backend | 1229 passed / 20 skipped / 2 warnings | suíte local |
| npm test -- --run | 45 passed | frontend unit |
| npm run format:check | passed | Prettier frontend completo |
| npm run build | passed | TypeScript + Vite/PWA |
| ruff check . | passed após corrigir parametrização duplicada | backend static |
| mypy app | passed, 72 source files | static |
| ruff format --check arquivos Python backend alterados | passed | static |
| refresh_registry.py --check | passed, zero divergência | filesystem real |
| SHA-256 dos 18 DELETE | recalculados e conferidos antes da remoção | filesystem real |
| DatasetVersion/AuditLog DEV | archival + lineage/hash verificados | banco real |
| ausências dos 18 paths | confirmadas individualmente | filesystem real |
| git diff --check | passed, avisos LF/CRLF apenas | static |

Baseline anterior 1226/20 → 1229/20: -1 teste exclusivo do split removido,
+3 casos de ausência (TRAIN, VALIDATION, split), +1 teste consumidores CLI.
Contra baseline original 1205/20: +24 passed, skips inalterados.
Os testes de configuração usam fixtures, NÃO provam treinamento/E2E científico.
Skips mantidos: 19 integração DEV opt-in sem URLs no processo da suíte e 1 live
externo opt-in. Arquivamento DEV foi verificado separadamente por SQL real,
não contado como execução desses 19 testes. Dois warnings de depreciação
Starlette/httpx e AnyIO; warning Vite de chunk mapa >500 kB preexistente.
Sem falha de arquivo em uso/acesso negado nesta retomada; nenhum contorno de
OneDrive/.venv aplicado. Não se atribuiu falha de fixture a ambiente:
fixture herdava degrees=10 do V1 e omitia prefetch no perfil de um worker;
foram explicitados zero degrees/shear e prefetch válido, sem mudar contrato V1.

### Proteções verificadas

Hashes antes/depois iguais:
- peso origem: f55ded7181e1b0c13285c56e7790b8f0e8f8db590fe4edb37f0b7f345c913a30;
- Frozen ledger: c8b92312b6809b2d9dfe69dd954ff383d3a4d226c9d501cd8bf0411e428a889e;
- Frozen result: 8c663b17d4a2bbe817af3be8457142f9b27f2da6e47ad0051634b4444fa21e42;
- quality gate: 93b83a32c26eb828c57e2c208b66c73dac518327a48483c52961ed13c88c59f0.

Nenhuma escrita/exclusão em imagens, rótulos (inclusive vazios/órfãos), raw,
taxonomia, .env, peso origem ou registros Frozen Test. Só os 18 derivados da
allowlist foram enviados à Lixeira. Nenhum treino, avaliação científica,
benchmark, Frozen Test ou download executado. Full pytest exercita lógica
isolada com fixtures; não iniciou runs científicos.
Os manifests V2 com nome frozen_test estavam na lista explícita autorizada;
não confundir sua remoção com os dois registros protegidos em models/serving.

### yolox_quality_gate.json — KEEP, conteúdo e interpretação

O arquivo não aponta a um path de split, mas referencia explicitamente
VALIDATION_V2, V2 E1 EMA e V2 E2 memory-efficient EMA em evidence_basis.
Os limites foram derivados/congelados para aquele ciclo, incluindo métricas
por China e D40. A estrutura do gate é reutilizável; seus valores NÃO são
critério genérico aprovado para dataset novo. Preservado byte a byte.
Conteúdo integral:

```json
{
  "schema_version": 1,
  "name": "urmind-yolox-quality-rebuild-validation-gate",
  "source_role": "VALIDATION",
  "frozen_before_training_completion": true,
  "selection_method": {
    "confidence_candidates": [0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5],
    "nms_candidates": [0.5, 0.65],
    "eligibility": "all criteria must pass",
    "ranking": "highest D40 F1, then overall F1, then lowest FP per negative image, then lower confidence, then lower NMS",
    "test_access": "forbidden"
  },
  "criteria": [
    {"metric": "map50_95", "operator": ">=", "value": 0.22},
    {"metric": "f1", "operator": ">=", "value": 0.52},
    {"metric": "per_class.D40.ap50_95", "operator": ">=", "value": 0.17},
    {"metric": "per_class.D40.precision", "operator": ">=", "value": 0.4},
    {"metric": "per_class.D40.recall", "operator": ">=", "value": 0.4},
    {"metric": "by_group.country:China.map50_95", "operator": ">=", "value": 0.25},
    {"metric": "by_group.country:China.per_class_ap50_95.D40", "operator": ">=", "value": 0.17},
    {"metric": "negative_image_false_positives.false_positives_per_negative_image", "operator": "<=", "value": 0.14}
  ],
  "evidence_basis": {
    "honest_baseline": "V2 E1 EMA on VALIDATION_V2",
    "baseline_map50_95": 0.206199,
    "baseline_f1": 0.522659,
    "baseline_d40_ap50_95": 0.152024,
    "baseline_china_map50_95": 0.339259,
    "baseline_china_d40_ap50_95": 0.362145,
    "baseline_fp_per_negative_image": 0.138686,
    "augmented_screening": "V2 E2 memory-efficient EMA",
    "augmented_map50_95": 0.201802,
    "rationale": "Exige melhora material sobre os screenings honestos sem permitir novo colapso D40/China nem regressão operacional em negativas. Os limites são específicos deste ciclo e foram congelados antes da avaliação do rebuild e antes de qualquer Frozen Test."
  }
}
```

### Gate final e limites

GATE_PARTE_A=PASS, findings P0/P1 próprios desta limpeza: nenhum após verificação.
Hash, schema de inventário, paths absolutos, produtores/consumidores, guardas,
registry e banco real verificados. Nenhum novo órfão/duplicado identificado.
Não é certificação de integridade de todo raw nem prontidão de treinamento:
novos splits e autorização ainda exigem revisão humana e gates independentes.
As lições D40 permanecem obrigatórias: estratificar dentro de cada país,
negativos suficientes na validação, decisão explícita sobre micro-caixas Norway,
sem rotação/shear (D00 × D10).

Skills nesta retomada: urmind-dataset-integrity, no-duplicate-files,
karpathy-guidelines, urmind-verification-gate, superpowers:systematic-debugging,
supabase:supabase. Conector Supabase e leitura de documentação Microsoft:
https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-fscc/ca28ec38-f155-4768-81d6-4bfeb8586fc9
Pinned expressa intenção de residência; recall flags indicam indisponibilidade.
A prova usada foi leitura/hash pelo gerador oficial, sem mudar esse gerador.
Changelog Supabase tentou leitura mas retornou content-type não suportado;
nenhuma API/schema nova implementada, só SQL no schema real inspecionado.


## Inventário A1 final — arquivos locais confirmados

Usuário fixou os arquivos no OneDrive. PowerShell ainda informa Archive +
ReparsePoint + Pinned (525344), sem Offline/RecallOnOpen/RecallOnDataAccess.
Python stat informa Archive + Pinned (524320), reparse_tag=0. O gerador oficial
refresh_registry.sha aceitou e leu todos os 18 arquivos, sem alterar guards,
hidratar ou remover atributos. Hashes abaixo foram recalculados dos bytes.
As seções anteriores à retomada, abaixo, são histórico da primeira tentativa.

| Caminho | Bytes | SHA-256 confirmado | Git | Decisão |
|---|---:|---|---|---|
| `datasets/splits/rdd2022_v2_domain_shift_probe.txt` | 573090 | b55d5be5a7992e54c18650eadfb20e3dc897d715371ddb58f839576741763baf | untracked | DELETE → Lixeira; ausência confirmada |
| `datasets/splits/rdd2022_v2_frozen_test.txt` | 258439 | e121a2af8acf5fdf531294c356c392c83826c4bdee59d7121180a20fc916aab1 | untracked | DELETE → Lixeira; ausência confirmada |
| `datasets/splits/rdd2022_v2_splits.json` | 10029 | e21391b82f824e0865a3c15dcd4129f3fd08870cf2cbf7659ebf4ce2fe0238a9 | untracked | DELETE → Lixeira; ausência confirmada |
| `datasets/splits/rdd2022_v2_train.txt` | 1322543 | e3956ad65fa7982b94774e7339f881471f96202094e3849274977415f5e12d71 | untracked | DELETE → Lixeira; ausência confirmada |
| `datasets/splits/rdd2022_v2_validation.txt` | 296492 | d27c01d620b924963588826b61322630868ee9ff6588feb29769d72f8a3594ee | untracked | DELETE → Lixeira; ausência confirmada |
| `datasets/manifests/detection_v2_domain_shift_probe.jsonl` | 5982234 | 0b6c9502c58e0482a52f272f770c88e0642461ff71511499c56550352818e5d3 | untracked | DELETE → Lixeira; ausência confirmada |
| `datasets/manifests/detection_v2_frozen_test_authorized.jsonl` | 2824797 | fa13873d3e2fe2e77e21baf57a9e762464b9ddda586c9834b74788a8fc928c79 | untracked | DELETE → Lixeira; ausência confirmada |
| `datasets/manifests/detection_v2_train_authorized.jsonl` | 14283086 | ba1b05eaa4f0c65ebbd2038012b1900295fc7425be7ac35849aef81cc5ee2ad6 | untracked | DELETE → Lixeira; ausência confirmada |
| `datasets/manifests/detection_v2_validation_authorized.jsonl` | 3251453 | 38e2a6378670eefa2e821291fc1d9ccd65d469cea87b79f6c341b42f3f54cfd3 | untracked | DELETE → Lixeira; ausência confirmada |
| `datasets/annotations/rdd2022_split_authorization_v2.json` | 2339 | f51449ce9ddf533ae8e0be44f2239cfe75845f7f9608a972d21930c85c4bd0f8 | untracked | DELETE → Lixeira; ausência confirmada |
| `datasets/reports/detection_manifests_v2.json` | 2678 | 42017ba83cd7812e79697cac11efee976c584b8c1239c5959c822ba1a989cf86 | untracked | DELETE → Lixeira; ausência confirmada |
| `datasets/reports/rdd2022_v2_negative_policy.json` | 3120 | ad5b74e933a92ed4e83a72e75cfe8c472fa09881220276ffd07ec6a908967400 | untracked | DELETE → Lixeira; ausência confirmada |
| `datasets/reports/rdd2022_v2_split_authorization_review.json` | 9875 | 1d1a8b5b6addb6bd0e0302a272eefa76ae0b8cd3432466689505f23f45d6d07d | untracked | DELETE → Lixeira; ausência confirmada |
| `datasets/reports/rdd2022_v2_split_authorization_status.json` | 609 | 6daaf881be1b574843daa8f1dab1980c11f79dbb56b6dca133e908a1440cd9a3 | untracked | DELETE → Lixeira; ausência confirmada |
| `datasets/metadata/yolox_model_v2_screening_e1.json` | 5321 | 5e60f4de5d6d3b46c0f0b19577f2c999c15bcbd7782e4d68446844d65672dd28 | untracked | DELETE → Lixeira; ausência confirmada |
| `datasets/metadata/yolox_model_v2_screening_e2.json` | 5731 | 9091117d91f4880fe46239eaf2c0f1b62d9aa482f3bb73451288e8655f04aa2a | untracked | DELETE → Lixeira; ausência confirmada |
| `datasets/metadata/yolox_model_v2_screening_e2_memory_efficient.json` | 5565 | 6898b60baf293eb740633258cd8635b2dabb1f16e9c493fabe6281251c4aeacb | untracked | DELETE → Lixeira; ausência confirmada |
| `datasets/metadata/yolox_quality_rebuild.json` | 5482 | 583023fb19752184b524d89e69945a071370a03db11c1ffa19e93a17ecdf9fd5 | untracked | DELETE → Lixeira; ausência confirmada |

## Histórico da primeira tentativa (superado pela retomada acima)

STATUS=PARTIAL. VERDICT=BLOCKED.
REMOVED_FILES=0; REMOVED_LOGICAL_BYTES=0.
18 candidatos DELETE totalizam 28.842.883 bytes lógicos, **não removidos**.
Não houve esvaziamento da Lixeira, exclusão permanente, commit ou escrita Supabase.

## Bloqueio da remoção

Todos os 18 candidatos apresentam FileAttributes=1056 (Archive + ReparsePoint).
O contrato local em scripts/datasets/refresh_registry.py rejeita ReparsePoint
em require_local_file/_is_cloud_only antes de fingerprint. Não houve bypass desse
contrato nem hidratação automática. ReparsePoint não prova, sozinho, que o
conteúdo seja cloud-only: disponibilidade e hash permanecem não verificados.
O inventário abaixo é de nomes/metadados; não constitui inventário final
autorizado por conteúdo. Cópias externas e sincronização OneDrive não foram auditadas.
A skill urmind-dataset-integrity exige ação explícita do usuário ou cópia local
autorizada antes de materializar placeholders.

Ação mínima: disponibilizar cópias locais verificáveis destes arquivos e confirmar
sua localização; resolver a classificação conservadora ReparsePoint com evidência
de disponibilidade antes de calcular hashes. Em seguida revisar dependências e
mover cada DELETE individualmente à Lixeira. Não remover diretórios por padrão.

## Inventário DELETE pendente

São candidatos inequívocos por papel/nome, mas a identidade por SHA-256 ainda
precisa ser verificada. Nenhum SHA foi copiado de registry como prova local.

| Caminho | Bytes | SHA-256 | Git | Classificação |
|---|---:|---|---|---|
| `datasets/splits/rdd2022_v2_domain_shift_probe.txt` | 573090 | UNVERIFIED_REPARSEPOINT | untracked | DELETE, execução bloqueada |
| `datasets/splits/rdd2022_v2_frozen_test.txt` | 258439 | UNVERIFIED_REPARSEPOINT | untracked | DELETE, execução bloqueada |
| `datasets/splits/rdd2022_v2_splits.json` | 10029 | UNVERIFIED_REPARSEPOINT | untracked | DELETE, execução bloqueada |
| `datasets/splits/rdd2022_v2_train.txt` | 1322543 | UNVERIFIED_REPARSEPOINT | untracked | DELETE, execução bloqueada |
| `datasets/splits/rdd2022_v2_validation.txt` | 296492 | UNVERIFIED_REPARSEPOINT | untracked | DELETE, execução bloqueada |
| `datasets/manifests/detection_v2_domain_shift_probe.jsonl` | 5982234 | UNVERIFIED_REPARSEPOINT | untracked | DELETE, execução bloqueada |
| `datasets/manifests/detection_v2_frozen_test_authorized.jsonl` | 2824797 | UNVERIFIED_REPARSEPOINT | untracked | DELETE, execução bloqueada |
| `datasets/manifests/detection_v2_train_authorized.jsonl` | 14283086 | UNVERIFIED_REPARSEPOINT | untracked | DELETE, execução bloqueada |
| `datasets/manifests/detection_v2_validation_authorized.jsonl` | 3251453 | UNVERIFIED_REPARSEPOINT | untracked | DELETE, execução bloqueada |
| `datasets/annotations/rdd2022_split_authorization_v2.json` | 2339 | UNVERIFIED_REPARSEPOINT | untracked | DELETE, execução bloqueada |
| `datasets/reports/detection_manifests_v2.json` | 2678 | UNVERIFIED_REPARSEPOINT | untracked | DELETE, execução bloqueada |
| `datasets/reports/rdd2022_v2_negative_policy.json` | 3120 | UNVERIFIED_REPARSEPOINT | untracked | DELETE, execução bloqueada |
| `datasets/reports/rdd2022_v2_split_authorization_review.json` | 9875 | UNVERIFIED_REPARSEPOINT | untracked | DELETE, execução bloqueada |
| `datasets/reports/rdd2022_v2_split_authorization_status.json` | 609 | UNVERIFIED_REPARSEPOINT | untracked | DELETE, execução bloqueada |
| `datasets/metadata/yolox_model_v2_screening_e1.json` | 5321 | UNVERIFIED_REPARSEPOINT | untracked | DELETE, execução bloqueada |
| `datasets/metadata/yolox_model_v2_screening_e2.json` | 5731 | UNVERIFIED_REPARSEPOINT | untracked | DELETE, execução bloqueada |
| `datasets/metadata/yolox_model_v2_screening_e2_memory_efficient.json` | 5565 | UNVERIFIED_REPARSEPOINT | untracked | DELETE, execução bloqueada |
| `datasets/metadata/yolox_quality_rebuild.json` | 5482 | UNVERIFIED_REPARSEPOINT | untracked | DELETE, execução bloqueada |

## KEEP / AMBIGUOUS

| Caminho / grupo | Classificação | Razão |
|---|---|---|
| backend/app/schemas/issue_taxonomy.py | KEEP | Registry de produto ampliado, não treino V2 |
| datasets/metadata/taxonomy_v2_dataset_candidates.json | KEEP | Pesquisa canônica, nome histórico preservado |
| frontend/tests/taxonomy.fixture.json | KEEP | Fixture sincronizada do contrato público |
| docs/D40_ROOT_CAUSE_ANALYSIS.md | KEEP | Lições históricas explicitamente protegidas |
| scripts/datasets/make_splits_v2.py | KEEP | Código gerador, não output; não executado |
| datasets/metadata/yolox_quality_gate.json | AMBIGUOUS | Contrato de qualidade compartilhado, não demonstrado como exclusivamente V2 |
| datasets/raw, migrations, produção e testes não exclusivos | KEEP | Fora da autorização destrutiva |
| models/pretrained/yolox_s.pth | KEEP | Peso de origem protegido |
| models/serving/frozen-test-ledger-*.json / frozen-test-result-*.json | KEEP | Dois registros protegidos; não abertos/executados |

O inventário KEEP é de escopo, não fingerprint completo desses grupos.
Nenhum item AMBIGUOUS foi apagado. A busca por nomes incluiu arquivos ocultos e
excluiu .git, .venv, node_modules, caches, raw e third_party. Não encontrou
ponteiros *.dvc, dvc.yaml ou dvc.lock para os candidatos. Não foi feita limpeza
de cache DVC por associação presumida. Inventário de referências internas/DVC
continua pendente para declarar remoção completa.

## Dependências e ausência de remoção

Referências encontradas em:
- datasets/metadata/artifact_contract.yaml: required artifacts V2;
- scripts/datasets/build_detection_manifests.py: produtor de manifests V2;
- scripts/datasets/prepare_rdd_split_authorization.py: autorização V2;
- scripts/datasets/audit_ird_dashcam.py: comparações contra manifests;
- scripts/datasets/make_splits_v2.py: gerador histórico;
- backend/tests/test_training_engine.py: bindings e conteúdo real de splits;
- backend/app/ml/serving.py: explicações históricas.

Essas referências não foram declaradas quebradas, pois os arquivos continuam
presentes. A2 permanece OPEN: ainda converter testes dependentes de conteúdo real
em fixtures temporárias, verificar falha fechada dos consumidores sem split e
atualizar contrato/registry pelo gerador oficial antes de remoção.
Nenhum teste foi apagado. Não se declarou DATASET_SPLIT_NOT_AVAILABLE implementado.

## Supabase DEV / migrations

Conector oficial confirmou Urmind DEV (impm…ggy), ACTIVE_HEALTHY.
Inspeção somente READ ONLY: detections.urmind_class, events.urmind_class,
reviews.corrected_class e responsibility_rules.urmind_class são TEXT.
Não há CHECK limitando os códigos de classe. **Migration não necessária/criada.**
Head único local e DEV: 0021_history_snapshot_retention.
DatasetVersion V2 encontrado: ba253b0b-0ade-46dc-95bc-3219a03e816e,
rdd2022-model-v2-quality-rebuild, version e21391b82f824e08.
Arquivamento com AuditLog archive_v2_dataset_artifacts permanece OPEN junto
da operação de limpeza. Nenhuma linha ou lineage apagada; Auth não alterado.

## Taxonomia implementada

EXTENDED_EXISTING_COMPONENT: registry Python, DTO público, validador de pesquisa,
registry JSON existente, fixture frontend, catálogo visual, testes existentes.
Nenhum módulo alternativo ou segundo registry foi criado.
Versão do conteúdo: urmind-issue-taxonomy-v3. 35 classes / 7 famílias:
4 experimentais legadas + 31 candidatas, das quais 18 adicionadas nesta rodada.
Não existe novo modelo operacional; o ModelVersion anterior permanece ARCHIVED.
EXPERIMENTAL_MODEL no registry é categoria de suporte, não afirma disponibilidade.

Novas candidatas (prefixo canônico URMIND_):
HAZARDOUS_TREE, VEGETATION_ON_POWER_LINES, VEGETATION_OBSTRUCTION,
FALLEN_POWER_LINE, EXPOSED_WIRING, SINKHOLE, OPEN_TRENCH,
DAMAGED_MANHOLE_COVER, FADED_ROAD_MARKING, ROAD_EROSION, DAMAGED_CURB,
MISSING_CURB_RAMP, DAMAGED_TACTILE_PAVING, DAMAGED_BUS_STOP,
OBSTRUCTED_TRAFFIC_SIGN, DAMAGED_TRAFFIC_LIGHT, ABANDONED_VEHICLE, WATER_LEAK.

Todas DATA_REQUIRED / NEEDS_MORE_DATA / revisão pendente, com definição,
risco, photo_detectable e limitações. 18 entradas NEEDS_DATA_SOURCE sem licença,
URL ou provenance inventada; nenhuma aquisição.
Famílias existentes reutilizadas (rede elétrica em URBAN_INFRASTRUCTURE).
Sem aliases novos: sem equivalência exata comprovada. Distinções:
tampa presente danificada != OPEN_MANHOLE; cratera/afundamento != D40;
vegetação causadora de obstrução != classe genérica de calçada obstruída;
placa encoberta != placa fisicamente danificada (definição ajustada).
Códigos V2 mantidos; DTO aceita metadados novos ausentes em dados antigos.
Labels legados mantidos, novas apresentações consultam get_issue canônico.
UI mostra Em desenvolvimento e limitações. Cabos têm aviso condicional de
não aproximação/toque; foto de semáforo não prova funcionamento;
foto de veículo não prova abandono e não autoriza guardar/exibir placa.

FALLEN_POWER_LINE possui triage_priority_hint=maximum_pending_validation.
Isso é **metadado candidato, não regra operacional de prioridade validada**.
Roteamento/prescrição operacional desta classe permanece pendente; não ativar
classes sem dados para satisfazer a prioridade. Não foi inventado órgão.
Nenhuma nova classe entrou na allowlist visual; Worker recusa todas as 31
DATA_REQUIRED nos testes. Modelagem/curadoria futura ainda necessária.

## Verificação

| Evidência | Nível | Resultado |
|---|---|---|
| pytest tests/test_issue_taxonomy.py tests/test_taxonomy_candidates.py | unit/API local | 50 passed antes do teste adicional de labels |
| pytest -q -ra (backend, final) | suíte local, mocks conforme testes existentes | 1226 passed / 20 skipped / 2 warnings |
| npm test -- --run | frontend unit | 45 passed |
| playwright public.spec.ts --grep 'transparência lista' | browser com API stub | 2 passed, desktop/mobile |
| ruff check . | static | passed |
| ruff format --check nos cinco Python alterados | static | passed após formatação |
| mypy app | static | passed, 72 source files |
| npm run format:check (frontend completo) | static | passed |
| npm run build | TypeScript/build/PWA | passed; warning chunk mapa >500 kB |
| alembic heads | inspection | 0021_history_snapshot_retention, único |
| Supabase schema/constraints/datasets | real database READ ONLY | inspeção realizada; não prova integração de escrita |
| hashes DELETE / remoção / ausência pós-remoção | real filesystem | UNVERIFIED / BLOCKED |
| git diff --check | static | passed, somente avisos de conversão LF/CRLF |

Comparação baseline 1205/20: +21 passed = 18 casos adicionais de rejeição
Worker + 2 testes V3/compatibilidade + 1 teste labels. Nenhum skip virou pass
por exclusão. 20 skips: 19 tests/test_db_integration.py sem URLs de integração
no processo offline, 1 tests/live/test_external_sources_live.py opt-in.
Não executados separadamente nesta rodada. Dois warnings de depreciação
Starlette/httpx/AnyIO. Playwright completo/E2E real não executados;
os dois testes de catálogo não provam inferência nem banco.

## Gate / findings

| Prioridade | Finding | Estado / ação |
|---|---|---|
| P1 | SHA/inventário local não verificado; DELETE não executado | BLOCKED: cópia local verificável, revisão, Lixeira individual |
| P1 | Dependências V2 e arquivamento DEV não concluídos | OPEN: A2/A3 após identidade dos artefatos |
| P2 | Prioridade máxima elétrica somente como hint candidato | OPEN: regra/revisão antes de ativação, sem ampliar Worker |
| P2 | Integração DEV e clean checkout não provados nesta rodada | UNVERIFIED; não inferir a partir de mocks |
| P3 | Warning de tamanho de bundle | Preservado, fora desta mudança |

Manifests/registry/fingerprints: BLOCKED para cleanup; geradores existentes
identificados, nenhum output científico regenerado manualmente.
Orphans/duplication: fontes existentes estendidas, nenhum módulo paralelo.
Fail-closed: cobertura unitária Worker candidatas; consumidores de split sem
arquivo ainda não verificados neste trabalho.
Hardcoded values: códigos/versões são contrato explícito do usuário, não
resultados científicos. Cloud-only: atributos conservadores, sem prova de
residência. Mock fidelity: UI stub não prova real pipeline.
Pipeline de treino/inferência/avaliação/benchmark/Frozen Test NÃO executado.

## Próximo split / segurança

Exigir estratificação dentro de cada país (nunca país inteiro por papel),
validação com negativos suficientes, decisão documentada sobre micro-caixas
D40 Norway, nenhuma rotação/shear (D00 × D10). Novo split exige nova revisão,
provenance, hashes e autorização; limpeza não confere training readiness.

Nenhuma alteração desta rodada em raw, peso de origem, dois registros Frozen
Test, .env, credenciais, Auth ou migrations. Nenhum arquivo com taxonomy no
nome/caminho foi apagado. Dados/artefatos externos não foram tocados.
Documentação histórica preservada com nota verdadeira, sem dizer REMOVED
enquanto a remoção não ocorreu.

## Skills / conectores

Skills aplicadas: no-duplicate-files, urmind-dataset-integrity,
karpathy-guidelines, urmind-verification-gate, supabase:supabase.
Plugin/conector: Supabase get_project e execute_sql (READ ONLY).
Sem agentes adicionais, sem ferramentas de treino, sem download.

Ações para PASS: disponibilização local verificada, inventário SHA completo,
resolver AMBIGUOUS, A2 + testes de ausência, Lixeira individual, arquivamento
DEV auditado, registry oficial, reexecutar gates e atualizar status.
