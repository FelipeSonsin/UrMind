# Estado operacional do UrMind

## Áreas, mapa e porteiro — rodada parcial de24/09/2026

Branch `feature/areas-mapa-porteiro`, checkpoint `63d730d`. Reutilizados mapa/upload/Storage/relatos.
Adicionados Meus relatos (inclui sem localização), Sobre/privacidade, clustering, lista acessível,
painel de foto público/privado e bottom sheet; porteiro técnico no navegador e servidor antes do
Storage, métricas de rejeição em AuditLog e quality.photo_gate privado. Cena e rostos NÃO verificados;
foto tecnicamente aceita permanece NEEDS_REVIEW. Nenhum modelo instalado/restaurado.
Verificação fresca:1246 backend/21skips;45 Vitest;106 Playwright/2skips;6 DEV selecionados/14deselected.
Ruff/mypy/Prettier/TypeScript/build/diff aprovados. Head permanece0022, nenhuma migration nova.
Escopo completo PARTIAL; gate BLOCKED: faltam protocolo, ações sobre Capture sem Event, novos IDs
públicos/DTO com arredondamento, filtros completos, pHash, consentimento versionado, config/auditoria
completa, cena/rostos licenciados/calibrados e validação física/realtime. Não é readiness de treinamento.
Detalhes e comandos: `docs/audits/AREAS_MAPA_PORTEIRO_DIAGNOSIS_2026-09-24.md`.

## Foto → relato no mapa — 24/09/2026

Implementado no fluxo canônico: descrição opcional (500 caracteres), GPS do dispositivo
→ EXIF do servidor → manual, conflito GPS/EXIF, foto preservada sem localização
(`location_required`) e marcador privado de Capture independente de Event/modelo.
Head DEV/local: `0022_capture_report_markers`. Realtime/RLS de Captures: autor/revisor;
camada pública genérica `PUBLIC_CAPTURE_MARKERS_ENABLED=false` por padrão.
Verificação: 1239 backend aprovados / 21 skips; 6 integrações DEV aprovadas;
45 frontend; 100 Playwright / 2 skips; Ruff, mypy, Prettier, TypeScript, build e diff verdes.
Status PARTIAL: falta comprovar WebSocket entre sessões reais, celular físico/HTTPS
e exercitar downgrade seguro. Nenhum modelo restaurado/treinado; Auth e `.env` intactos.
Diagnóstico, evidências e roteiro: `docs/audits/PHOTO_TO_MAP_DIAGNOSIS_2026-09-24.md`.

## Dataset V2 / taxonomia — 24/09/2026

V2_DATASET_STATUS=REMOVED; NEW_SPLIT_REQUIRED=YES; YOLOX_TRAINING_READY=NO.
Os 18 derivados V2 (28.842.883 bytes) foram verificados por SHA-256 e movidos
individualmente à Lixeira, após confirmação local pelo gerador oficial.
DatasetVersion DEV ba253b0b-0ade-46dc-95bc-3219a03e816e arquivado em
split.lifecycle.status=ARCHIVED, preservando fingerprints/manifests históricos,
com AuditLog archive_v2_dataset_artifacts (b06d158a-6bdf-4e9d-b43a-19bb8bf664ee).
Consumidores sem split falham fechado; testes de contrato usam fixtures temporárias.
Gate de qualidade antigo também removido para a Lixeira (1.829 bytes), por
autorização posterior: total 19 arquivos / 28.844.712 bytes. Nenhum gate novo
criado; consumidores sem contrato retornam QUALITY_GATE_NOT_AVAILABLE.
Conteúdo histórico e SHA-256 preservados no relatório. Raw, imagens, rótulos,
taxonomia, peso de origem, registros Frozen Test e .env não alterados nesta retomada.
Taxonomia canônica ampliada para `urmind-issue-taxonomy-v3`: 35 classes,
18 novas DATA_REQUIRED, sem ampliar allowlist visual. O nome histórico do JSON
de pesquisa `taxonomy_v2_dataset_candidates.json` foi preservado deliberadamente.
Detalhes e pendências: `docs/audits/V2_DATASET_CLEANUP_2026-09-24.md`.
Próximo split: estratificação dentro de cada país, negativos suficientes na
validação, decisão explícita sobre micro-caixas D40 Norway, sem rotação/shear
na augmentation (D00 × D10). Não reutilizar autorização V2 como autorização nova.

## Limpeza autorizada dos treinamentos — 24/09/2026

81 artefatos locais de treinos antigos removidos para a Lixeira: checkpoints
dos quatro runs, ONNX, saídas experimentais e MLflow local. Inventário:
`docs/audits/TRAINING_CLEANUP_2026-09-24.md`. Código, dados brutos, contratos,
`.env`, peso pré-treinado de origem e ledger/resultado Frozen Test preservados.

ModelVersion DEV `2527af02-c995-4e5a-b6c6-d6904c8d35b5`: **ARCHIVED**,
`shadow_authorized=false`, decisão científica **REJECTED**, promoted_at nulo.
Não há modelo operacional disponível. E2E visual bloqueado até novo modelo
autorizado; não restaurar pesos silenciosamente. Claims anteriores de shadow
disponível são históricos. Nenhum novo treino foi iniciado.
YOLOX_TRAINING_READY=NO; XGBOOST_TRAINING_READY=NO: limpeza não supre revisão,
holdout independente, Ground Truth nem remediações técnicas abertas.
Regressão após limpeza: **1205 passed / 20 skipped**, Ruff focado, mypy e
diff check aprovados. Integrações DEV não reexecutadas nesta limpeza.

## Atualização de histórico arquivado — 24/09/2026

Head atual canônico/DEV: **0021_history_snapshot_retention**. F-05 continua
PARTIAL/OPEN: há captura forward-only e replay de observações congeladas no
AuditLog, com SHA256 e retenção contra UPDATE/DELETE. Não reconstrói cutoffs
anteriores sem arquivo; não autoriza features científicas nem substitui a
integração temporal completa do export tabular.

Backend: **1205 passed / 20 skipped**; DEV separado: **19 passed**. Os skips
são 19 integrações opt-in e um live externo, não PASS. Fixtures históricas
revertidas e ausência de resíduos confirmada por SQL. Revisão independente
sem achado concreto no recorte; F-05 abrangente permanece aberto.
Nenhum treinamento, Frozen Test, `.env` ou dado científico alterado.
Os cabeçalhos seguintes registram rodadas anteriores, não o head atual.

## Atualização de quota distribuída — 24/09/2026

Head canônico/DEV: **0020_public_image_quota**. F-03 avançou: a admissão de
imagens públicas usa PostgreSQL compartilhado, limites separados lookup/download,
caller/recurso/global e transação curta antes do Storage. Não depende mais apenas
de memória por processo. RLS ativo, sem grants a anon/authenticated. Runtime
privilegiado F-04 continua OPEN; carga de feira/proxy final ainda não certificada.

Verificação final: backend **1203 passed / 19 skipped** (18 DEV opt-in e 1 live);
DEV separado **18 passed**; Ruff/mypy aprovados. Migration única, nenhuma env
alterada. Corrigida remoção idempotente Storage e limpeza independente de fixtures.
Consulta final: zero Captures, registros de quota e objetos de integração residuais.
Evidência anterior com 0019 abaixo é histórica, não o head atual.


## Atualização mobile — F-12, 24/09/2026

Lazy loading de captura/login/revisão reduz JavaScript estático inicial de
675,12 para 580,04 kB contando preloads; mapa e precache total não diminuíram.
Corrigido overflow real em 320px no detalhe público. Vitest 45 PASS;
Playwright 98 PASS / 2 SKIP (E2E real); arquitetura 53 PASS; history/tabular/features
65 PASS. Build/TypeScript/Prettier aprovados; head único 0019_context_retention.
F-12 continua PARTIAL: health real, rede lenta e operação física não comprovados.
Treinamentos continuam bloqueados por dados/revisão e Ground Truth, além dos
findings técnicos abertos. Não houve alteração de `.env` ou do Supabase.


## Atualização de remediação — continuação de 24/09/2026

F-06 **RESOLVED** no serviço de histórico: cobertura desconhecida não gera zero
nem janela disponível; contagens observadas ficam separadas. Testes focados 65
PASS, revisão independente sem findings, backend 1199 PASS / 18 SKIP; mypy e
Ruff focado aprovados. Alembic permanece `0019_context_retention`.

F-09: as duas corridas encontradas na revisão foram reproduzidas e corrigidas:
troca de conta durante auth-origin não é adotada pelo envio, e signup cancelado
não instala sessão tardia. Há guarda antes da persistência pelo SDK e 12 testes
de fronteira aprovados, incluindo criação anônima legítima; revisão independente
sem finding concreto novo. F-09 geral segue PARTIAL até validação operacional real.
Regressão frontend: Vitest 45 PASS, Playwright 94 PASS / 2 SKIP (E2E real).
Prettier, TypeScript e build aprovados; warning de bundle permanece OPEN.
F-05/PIT, quota distribuída, runtime limitado, concorrência real de
publicação, mapas/admin, health/bundle, Git limpo e deploy continuam OPEN.

Roadmap imediato: quota distribuída F-03; continuar F-01/F-05/F-10/F-04 e
demais achados; provar E2E real antes da feira. Treinamentos continuam proibidos
nesta execução e bloqueados por revisão/dados humanos. Fonte detalhada:
`audits/URMIND_REMEDIATION_REPORT.md`. Baselines abaixo são históricos quando
divergirem desta atualização; mocks não equivalem a pipeline real.

Esta é a matriz de evidência operacional, subordinada ao planejamento de
`Planinng/MASTER_PLAN.md` e ao DOCX correspondente. Não é outro plano de arquitetura.
Última execução iniciada em 24/09/2026: um backend e um frontend canônicos; somente
Urmind DEV (`impm...ggy`). Treinamento, Frozen Test e robótica fora desta execução.

## Baseline desta execução

- Backend: `cd backend; .venv/Scripts/python.exe -m pytest -q -ra`:
  **1107 passed, 17 skipped** (16 integrações opt-in; 1 serviço externo opt-in).
- Frontend: `npm test -- --run`: **40 passed**.
- DEV confirmado pelo conector Supabase: PostgreSQL 17.6.1, PostGIS 3.3,
  Alembic `0019_context_retention`; zero Captures e zero RoadSegments no início.
- Backend e frontend com origens DEV iguais; arquivos de credenciais ignorados.
- Há alterações anteriores não commitadas em múltiplas fases. Não representam
  trabalho integralmente produzido ou validado nesta execução.

## Matriz de execução

| Trilha | Componentes canônicos | Dependência | Risco de conflito | Verificação |
|---|---|---|---|---|
| A/K | Auth, storage, rotas core/public, repositories | DEV | alto; edição sequencial | ownership, quotas, publicação, integração |
| B/C/D | issue_taxonomy, report, public schemas | avaliação Phase 5 persistida | médio; API conectada depois | taxonomia, missingness, linguagem condicional |
| E/J | App, EventsPage, EventDetail, MapLibre | contratos API | médio; frontend privado isolado | deep links, roles, mobile, build |
| F | external_sources/context | aplicabilidade das fontes | baixo | testes unit/live opt-in |
| G/H/I | candidatos, tabular, history | dados revisados/histórico | alto científico; preservar | leakage e contratos; sem treino |
| L/O | main, checklist, observability | URL HTTPS e operação | médio | CSP, startup, métricas |

## Problemas abertos

| ID | Estado | Evidência/ação necessária |
|---|---|---|
| REAL-E2E | OPEN | falta foto não científica e localização confirmada; sem prova foto→mapa |
| ROAD-AREA | OPEN | DEV sem trechos; importar recorte OSM só depois da coordenada real |
| HTTPS-FAIR | OPEN | falta URL HTTPS operacional estável e validação em aparelho real |
| ANON-AUTH | RESOLVED | duas identidades anônimas distintas, JWT/JWKS reais; excluídas após teste |
| IMAGE-PRIVACY | RESOLVED no contrato testado | JPEG derivado sem EXIF/ICC; original intacto; privacidade visual exige atestado humano |
| PUBLICATION | RESOLVED no contrato testado | autorização vinculada à última Review; proprietário pode ver resultado privado; consulta SQL testada no DEV |
| DECODE-ADMISSION | RESOLVED | limite antes de decodificar; executor limitado; cancelamento não libera thread ainda ativa |
| PRIVATE-ROUTES | RESOLVED navegação | deep links/roles corretos; gestão completa admin/ground truth ainda OPEN |
| YOLOX-DATA | OPEN | BLOCKED_DATA/BLOCKED_REVIEW; Frozen Test rejeitado permanece imutável |
| XGB-GT | OPEN | Ground Truth insuficiente; nenhum treino autorizado |
| PREDICTION-HISTORY | OPEN | histórico insuficiente; apenas análise descritiva permitida |
| TAXONOMY-CURATION | OPEN | classes candidatas sem curadoria/validação; não emitidas pelo modelo |

Um problema só muda para RESOLVED com correção, teste e revisão. Mocks não
comprovam operação DEV nem substituem a foto real. Nenhuma conclusão anterior
de dataset/licença é revogada por este documento.

## Entrega por fase e trilha

`GENERAL_STATUS = PARTIAL`. Não há conclusão global nem treinamento nesta rodada.

| Campo | Estado comprovado |
|---|---|
| PHASE_3_STATUS / PHASE_3_DATA_STATUS | BLOCKED_DATA / BLOCKED_REVIEW; evidência científica preservada |
| YOLOX_TRAINING_READY | NO: holdout independente e revisão/autorizações de dados pendentes |
| PHASE_4_STATUS | implementação preservada; snapshots/histórico/PostGIS passaram na regressão DEV |
| PHASE_5_STATUS | implementação preservada; avaliação por regras e trace passaram; parâmetros provisórios continuam assim |
| PHASE_6_STATUS | implementação preservada; revisão, consenso, adjudicação e export testados no DEV |
| PHASE_7_STATUS | READY_FOR_REAL_E2E em código, sem prova real completa |
| PHASE_7_REAL_E2E_READY | NO para execução integral imediata: foto/localização e área viária ainda ausentes |
| TAXONOMY_V2_STATUS / VERSION | registry tipado estendido; `urmind-issue-taxonomy-v2` |
| ACTIVE_CLASSES | nenhuma classe/modelo aprovado para produção |
| EXPERIMENTAL_CLASSES | D00, D10, D20, D40 |
| DATA_REQUIRED_CLASSES | FALLEN_TREE, FALLEN_BRANCH, ILLEGAL_DUMPING, ROAD_DEBRIS, OPEN_MANHOLE, BLOCKED_DRAIN, FLOODED_ROAD, SIDEWALK_DAMAGE, SIDEWALK_OBSTRUCTION, DAMAGED_TRAFFIC_SIGN, FALLEN_TRAFFIC_SIGN, DAMAGED_STREETLIGHT_POLE, DAMAGED_BARRIER |
| STRUCTURED_DESCRIPTION_STATUS / DIAGNOSIS_STATUS | contrato `urmind-urban-analysis-v1` conectado ao DTO e frontend; templates determinísticos |
| POTENTIAL_CONSEQUENCES_STATUS | condicionais, só a partir de avaliação Phase 5; causas não inventadas |
| PRESCRIPTION_STATUS / RESPONSIBILITY_ROUTING_STATUS | catálogo/regra existentes preservados; domínios tipados; catálogo completo ampliado ainda pendente |
| DECISION_TRACE_STATUS | preservado e consumido; modelo/dataset não apresentados como promoção |
| MAP_STATUS / PUBLIC_MAP_STATUS | MapLibre funcional em navegador com dados de teste; somente Events publicados |
| PRIVATE_MAP_STATUS | mapa existente reutilizado; todas as camadas/filtros privados solicitados ainda não completos |
| ROADSEGMENT_STATUS | DEV zero trechos; importador existente testado; precisa coordenada real antes de importar OSM |
| REALTIME_MAP_STATUS | Events/RiskAssessment publicados no DEV; integração real passou; atualização de mapa em E2E real não comprovada |
| API_REGISTRY_STATUS | registry existente ampliado de 14 para 20 entradas, sem segundo sistema |
| API_STATUS_BY_PROVIDER | Supabase testado; Nominatim/Overpass/Open-Meteo/GeoSampa/SIDRA/BrasilAPI/ViaCEP catalogados, health atual UNKNOWN; OpenFreeMap utilizado como contrato de mapa, tiles reais não provados neste teste |
| ORPHAN_INTEGRATIONS | seis fontes runtime antes ausentes do registry agora vinculadas a implementações; inventário não prova health |
| FALLBACK_STATUS / PROVENANCE_STATUS | indisponibilidade explícita; nenhuma configuração é tratada como dado ou sucesso de consulta |
| PHASE_8_SCAFFOLDING_STATUS | HARDENED/PARTIAL: export/split/hashes/leakage testados; autorização vinculada a DatasetVersion ainda não implementada |
| PHASE_8_TRAINING_STATUS / XGBOOST_TRAINING_READY | BLOCKED_DATA + BLOCKED_AUTHORIZATION / NO |
| TARGET_LEAKAGE_STATUS | contexto temporal desconhecido excluído; label exige Review correspondente; cortes temporais respeitam disponibilidade do label; ordenação/hash determinísticos |
| PHASE_9_DATA_PIPELINE_STATUS | scaffolding descritivo existente testado, sem prova operacional de histórico real |
| PHASE_9_DESCRIPTIVE_ANALYTICS_STATUS | contagens descritivas, sem probabilidade; API/UI completa de hotspots ainda pendente |
| PHASE_9_PREDICTION_STATUS | BLOCKED_HISTORY |
| PHASE_10A_STATUS | registry/pesquisa parcial; curadoria/licenças/dados ainda OPEN |
| PUBLIC_FRONTEND_STATUS | fluxo mobile/resultado/refresh/estado experimental testados com API simulada |
| PRIVATE_FRONTEND_STATUS | deep links, gates e dashboard reais em código; ground-truth/admin têm indisponibilidade explícita, não gestão completa |
| MOBILE_READINESS_STATUS | emulação desktop/mobile passou; câmera/GPS em aparelhos Android/iPhone reais não verificados |
| DEMO_MODE_STATUS | exige exemplo real revisado/publicado; DEV vazio, nenhum exemplo fabricado |
| PRIVACY_STATUS / EXIF_SANITIZATION_STATUS | derivada sanitizada por Event, proxy sem path, revogação revalidada; retenção/exclusão final ainda OPEN |
| SECURITY_STATUS | controles corrigidos e revisão independente; scan focado, não certificação exaustiva |
| CSP_STATUS | ativa na PWA servida pelo backend; teste de marcador renderizado sob CSP passou; host HTTPS final pendente |
| DEPLOY_READINESS_STATUS / HTTPS_STATUS | PARTIAL / URL HTTPS estável não provisionada |
| QR_READINESS_STATUS | BLOCKED_HTTPS; nenhum QR definitivo gerado |
| FAIR_DEMO_CHECKLIST_STATUS | atualizado com comandos, limites, privacidade e pendências |
| OBSERVABILITY_STATUS | dashboard usa métricas existentes; lineage não chama shadow de promovido; catálogo completo de métricas ainda não implementado |
| MOBILE_CAPTURE_STATUS / CAMERA_UPLOAD_STATUS | seleção/câmera web existentes; upload/validação testados, aparelho real pendente |
| GPS_STATUS / EXIF_STATUS / MANUAL_LOCATION_STATUS | contratos preservados/testados; nunca coordenada default; confirmação real pendente |
| LOCATION_PROVENANCE_STATUS | alegação do dispositivo/cliente, nunca precisão ou cronologia comprovada |
| STORAGE_IMAGE_PERSISTENCE_STATUS | bucket privado e compensação testados no DEV; nenhuma foto real capturada nesta rodada |
| CAPTURE_PROCESSING_STATUS | `no_supported_detection` canônico, alias antigo legível; inferência concluída não antecipa análise |
| MAP_MARKER_STATUS | marcador efetivamente renderizado por Worker MapLibre em teste browser; fixture explícita |
| PHOTO_TO_MAP_E2E_STATUS | REAL_E2E_BLOCKED_INPUT |
| DATABASE_MIGRATION_STATUS | head único `0019_context_retention`; nenhuma migration criada/aplicada nova |

## Pesquisa e dados

`DATASET_RESEARCH_STATUS = UPDATED_CATALOG_ONLY`. Três fontes adicionais no
registry existente: RoadObstacle21 (NEEDS_HUMAN_REVIEW), SideGuide e SideSeeing
(LICENSE_BLOCKED). Termos condicionais não viraram licença irrestrita; frames,
objetos e obstáculos não viraram rótulos de dano automaticamente. Links primários
e limitações constam no JSON. Nenhum dataset baixado, inferido ou treinado.

`DATASET_READINESS_BY_CLASS`: NEEDS_HUMAN_REVIEW para FALLEN_TREE,
ILLEGAL_DUMPING, ROAD_DEBRIS, OPEN_MANHOLE, FLOODED_ROAD, DAMAGED_TRAFFIC_SIGN;
NEEDS_MORE_DATA para as demais candidatas. Isto não autoriza treinamento.
`STORAGE_BUDGET_STATUS`: teto 40 GB preservado; ~35,3 GB é a estimativa recebida,
não uma nova medição. Sem aquisição nem remoção científica nesta rodada.
`RESEARCH_FINDINGS_PRESERVED = YES`: fontes anteriormente rejeitadas preservadas.

## Evidência final reproduzida

Todos os comandos abaixo foram executados no backend/frontend canônicos.

| Verificação | Nível | Comando/inspeção | Resultado |
|---|---|---|---|
| Backend completo | unit/local | `.venv/Scripts/python.exe -m pytest -q -ra` | 1177 passed, 18 skipped |
| DEV | integration | mesma suíte `tests/test_db_integration.py`, env carregado localmente após validar ref das URLs | 17 passed |
| Arquitetura | inspection/test | `pytest tests/architecture -q` | 53 passed |
| Frontend | unit | `npm test -- --run` | 45 passed |
| Navegador | integration com API simulada | `npx playwright test` | 76 passed, 2 skipped |
| Mapa+CSP | browser local | backend em 8017 servindo dist; Playwright `--grep 'mapa mostra'` | 2 passed, marcador renderizado |
| Build | build | `npm run build` | passou; worker MapLibre emitido; aviso de chunks grandes |
| Ruff/mypy | static | `ruff check app tests`; `mypy app` | passaram, 72 módulos tipados |
| Formatting | static | Ruff apenas arquivos alterados; Prettier src/tests/vite | passou após corrigir formato, sem formatação ampla |
| Diff | inspection | `git diff --check` | passou |
| Alembic | inspection/DEV | `alembic heads`; consulta DEV | `0019_context_retention`, um head |
| Auth | real service | settings + dois signup anônimos + assinatura JWT/JWKS + cleanup | passou; duas exclusões 200 |
| Modelo shadow | real artifact | resolução DEV read-only + checksum/contrato + ONNX Runtime load | CPUExecutionProvider; REJECTED preservado; sem inferência |
| Segredos | inspection | `git check-ignore`; lista versionada; igualdade de refs sem valores | env ignorados/não modificados; ambos DEV |
| Registro/artifacts | inspection/test | taxonomia fixture sincronizada; validação candidatos; ONNX contrato/hash | passou no escopo alterado |
| Duplicação/orphans | inspection/test | arquitetura; reuse de services/schemas/registry; rotas privadas | um backend/frontend; sem novo sistema paralelo |

`SKIPPED_TESTS`: na suíte backend, 17 integrações opt-in são puladas e depois
executadas separadamente no DEV (17/17); 1 live-check genérico de fontes externas
continua não executado. No Playwright, os dois projetos do teste de foto real são
pulados por falta de imagem/localização/autorização. Nenhum skip é PASS.

`REGRESSIONS_FOUND / REGRESSIONS_FIXED`: quota de tentativas antes do decoder;
liberação prematura sob cancelamento; publicação implícita; imagem compartilhada
entre Events; revogação durante download; competição GET público × decoder;
status prematuro; status shadow privado ausente; resultado antigo após logout;
nota de captura perdida; Worker MapLibre ausente do bundle; leakage/tabular hash;
lineage observability com promoção presumida. Corrigidos com testes e revisão.

## Limitações abertas e gate

`VERIFICATION_GATE = BLOCKED` para a missão inteira. Gates de regressão local
e contratos DEV passaram; não equivalem à conclusão de todas as trilhas.

| Prioridade | OPEN | Impacto / ação mínima |
|---|---|---|
| P1 | REAL-E2E / ROAD-AREA | obter foto não científica e coordenada confirmada; importar trecho OSM mínimo; executar pipeline sem INSERT manual |
| P1 | HTTPS-FAIR / MOBILE-DEVICE | provisionar URL HTTPS com frontend/API/Worker; validar aparelhos/rede; depois QR |
| P1 | PRIVATE-MANAGEMENT | implementar gestão ground truth/admin e interface de publicação, hoje indisponíveis explicitamente |
| P1 | XGB-GT / XGB-AUTH | ground truth suficiente e autorização vinculada ao dataset; depois avaliação temporal/calibração, sem atalho |
| P1 | YOLOX-DATA / TAXONOMY-CURATION | revisão, licença, semântica, isolamento e holdout independente |
| P1 | HISTORY-API | completar API/UI de histórico/hotspots; obter histórico antes de previsão |
| P2 | PROVIDER-HEALTH | health/freshness persistidos e live-check de todas as fontes ainda não comprovados |
| P2 | RETENTION | política técnica de retenção/exclusão e tratamento de rostos/placas quando necessário |
| P2 | PUBLICATION-LOCKS | chamada Storage ainda sob locks de publicação; latência limitada, mas otimizar transação antes de escalar |
| P2 | MULTIPROCESS-RATE | limite de tentativas é local ao processo; edge/rate distribuído necessário se ampliar deployment |
| P2 | METRICS-CATALOG | métricas existentes não cobrem todas as métricas pedidas; não inventar zeros |
| P3 | BUNDLE | chunks grandes; otimização de carga mobile posterior sem alterar semântica |

## Ferramentas e organização

`SKILLS_USED`: brainstorming (conferência interna da arquitetura aprovada),
karpathy-guidelines, no-duplicate-files, superpowers:systematic-debugging,
superpowers:dispatching-parallel-agents, supabase:supabase,
supabase:supabase-postgres-best-practices, codex-security:security-scan,
example-skills:frontend-design, document-skills:docx,
computer-use:computer-use, urmind-cv-dataset-audit, urmind-dataset-integrity,
correction-review, urmind-adversarial-review, urmind-verification-gate.
`deep-recon` e `review-agent` não estavam disponíveis: inspeção direta e revisores
independentes foram usados, sem inventar execução dessas skills.

`PLUGINS_USED`: Supabase, Codex Security, Computer Use; pacotes de skills
Superpowers e anthropic-agent-skills. `CONNECTORS_USED`: Supabase e Chrome/CUA.
Pesquisa via web em fontes primárias. Product Design foi consultado no catálogo,
mas sua auditoria visual formal não foi executada; Data/GitHub/Sites/Visualize
não foram usados para alterar ou publicar nada. Nenhum deploy efetuado.

`GIT_STATUS`: branch canônica `feat/urmind-v1-public-center`; único worktree.
`COMMITS_CREATED = NONE` nesta rodada. Há modificações e arquivos não rastreados
preexistentes, incluindo milhares de linhas de serving/treinamento e artefatos
científicos; não foram misturados em um commit amplo com estas correções.
`UNCOMMITTED_CHANGES`: implementação e testes continuam no worktree, junto das
alterações anteriores preservadas. Consolidação em commits revisados permanece OPEN.
`DOCUMENTATION_SYNC_STATUS`: README, checklist, integrações e este estado
atualizados. MASTER_PLAN e DOCX lidos/comparados, mantidos sem mudanças técnicas.

`WHAT_WAS_ACTUALLY_IMPLEMENTED`: controles e contratos descritos acima, rotas
privadas, análise determinística, privacy publication, CSP/worker, hardening
tabular, registry e pesquisa. `WHAT_WAS_ONLY_PREPARED`: deploy/checklist, classes
novas, treinamento, métricas completas e curadoria. `WHAT_REQUIRES_REAL_DATA`:
E2E, OSM local, holdout, ground truth, histórico. `WHAT_REQUIRES_USER_INPUT`:
foto própria com caminho e localização confirmada; posteriormente URL/hosting
e decisões de retenção. Não é necessário reenviar credenciais.

`NEXT_HIGHEST_VALUE_TASKS`: completar publicação/ground truth/admin na área
privada; fornecer foto/localização e provar o E2E; preparar HTTPS e testar no
celular; consolidar commits sem misturar ciência; só então avançar dados/treinos.

`INFRASTRUCTURE_READY_EXCEPT_TRAINING = NO`.
`ONLY_MAJOR_WORK_REMAINING`: E2E real, deployment HTTPS/mobile, gestão privada,
histórico/APIs/métricas completos, políticas operacionais, curadoria e dados
revisados, autorização de treino e treinamentos futuros. Não apenas treinamento.
