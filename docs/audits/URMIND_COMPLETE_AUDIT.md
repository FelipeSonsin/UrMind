# URMIND_COMPLETE_AUDIT

Data: 24/09/2026. Escopo: auditoria, sem correção de implementação.
Referência Git: `feat/urmind-v1-public-center`, HEAD `437c21d`.
Ambiente consultado: exclusivamente Urmind DEV, `impm...ggy`.

## EXECUTIVE_SUMMARY

**FINAL_VERDICT: URMIND_NEEDS_ATTENTION**

O backend/frontend canônicos e a infraestrutura DEV existem e passam a regressão declarada. Isso não equivale a produto completo nem a E2E real comprovado. Foram reproduzidos 1.177 testes de backend, 17 integrações DEV, 53 testes de arquitetura, 45 testes frontend e 76 testes Playwright aprovados. Os skips continuam separados abaixo.

Os principais impedimentos são:

1. O teste E2E real está incompatível com a rota atual e com as regras de acesso/publicação. Portanto, **não falta somente a foto**.
2. Há 142 entradas preexistentes no status Git, incluindo implementação central não versionada. Um checkout de HEAD não reproduz o estado auditado.
3. A quota compartilhada da imagem pública permite esgotamento anônimo por consultas a UUIDs inexistentes.
4. A Fase 9 não possui semântica histórica point-in-time equivalente à Fase 4; há também uma conversão concreta de ausência de janela para zero.
5. Faltam foto externa com localização confirmada, recorte viário real, demonstração HTTPS/celular físico e operação contínua do Worker comprovada.
6. Treinamentos continuam bloqueados por dados/revisão/holdout e Ground Truth, não pela simples existência de ferramentas.

Nenhum finding foi corrigido. Nenhum commit, deploy, treinamento, promoção, migration ou alteração de `.env` foi executado. O único arquivo de conteúdo criado nesta auditoria é este relatório. Testes/builds produziram seus artefatos locais ignorados habituais. Fixtures DEV foram removidas/rollbackadas; contagens finais foram verificadas.

## Método, evidência e limites

Categorias utilizadas: **VERIFICADO** = evidência observada nesta rodada; **PARCIAL** = implementação/evidência incompleta; **HISTÓRICO** = relatório anterior, não reexecutado; **NÃO VERIFICADO** = falta evidência. PASSED em regressão de componente não certifica operação pública completa.

A auditoria combinou leitura de código crítico, documentos, contratos e relatórios científicos, consultas SQL read-only, conector Supabase, regressão local, fixtures DEV isoladas e revisão independente de segurança. Não houve teste ofensivo contra serviço público.

**Limite importante:** não foi realizada leitura humana integral de cada arquivo em `backend/`, `frontend/`, `datasets/`, `models/` e `mlruns/`. Houve inventário, buscas e leitura aprofundada dos caminhos relacionados aos contratos auditados. Saídas longas de documentos tiveram truncamento; não se declara comparação integral MD/DOCX nem cobertura exaustiva de todo o repositório. Não foram recalculados todos os hashes de datasets/checkpoints, auditado todo o histórico Git por secrets, nem reconstruído Supabase vazio nesta rodada. Esses itens permanecem não verificados, não aprovados por inferência.

O diretório oficial encontrado é `docs/Planinng/`, não `docs/Planning/`. Foram consultados MASTER_PLAN, extração do DOCX, PROJECT_STATE, FAIR_DEMO_CHECKLIST, README, contratos e relatórios relevantes. Não se alterou documentação de origem para resolver divergências.

### SKILLS_USED / PLUGINS_USED / CONNECTORS_USED

Catálogo de skills disponibilizado pelo Codex consultado; não havia comando `/skills` executável nesta interface. `deep-recon` não estava disponível: substituído por inspeção direta, sem inventar skill equivalente.

Skills efetivamente aplicadas: `brainstorming` (conferência de escopo, sem planejamento de implementação), `karpathy-guidelines`, `no-duplicate-files`, `correction-review` (revisão sem correção autorizada), `urmind-adversarial-review`, `urmind-verification-gate`, `urmind-cv-dataset-audit`, `urmind-dataset-integrity`, `superpowers:systematic-debugging`, `supabase:supabase`, `supabase:supabase-postgres-best-practices`, `example-skills:frontend-design` (auditoria, sem redesign), `document-skills:docx`, `codex-security:security-scan`.

Aplicação concreta: fronteiras de duplicação e consumidores; reprodução de quota/missingness; revisão de RLS/privilégios/transações; separação de dados científicos e fixtures; gates condicionados a evidência; revisão de estados e rotas mobile; achados com reprodução e impacto.

Plugins/conectores efetivamente usados: **Supabase**, para projeto, SQL e advisors; **Codex Security**, para consultar scan anterior e orientar revisão independente. Scan anterior `7c0a9661-88ca-4718-aac8-8d03ac8b08ae`, concluído em 24/09/2026, é evidência histórica de escopo focado. **Não foi iniciado novo scan oficial completo**, pois seu fluxo criaria outros artefatos além do relatório autorizado. Houve revisão estática atual, inclusive por agente independente, sem gravações.

Product Design, Data, GitHub, Sites, Computer Use e Visualize foram considerados no catálogo, mas não utilizados como conectores nesta auditoria. Playwright foi executado localmente; isso não constitui uso do conector Computer Use nem validação em celular físico.

## CURRENT_REAL_ARCHITECTURE

```text
React/PWA + Supabase Auth
  -> FastAPI autenticado / upload validado / ownership
  -> Storage privado + Capture PostgreSQL
  -> trigger pgmq / inference_jobs
  -> Worker Python / resolver explícito shadow ou produção
  -> ONNX / Detection com ModelVersion
  -> Event / PostGIS / contexto externo
  -> FeatureBuilder / snapshot
  -> assess_features / RiskAssessment / DecisionTrace
  -> APIs públicas filtradas ou privadas por role
  -> MapLibre / Review / consenso ou adjudicação / export elegível
```

Há um backend e um frontend canônicos. Não foi encontrado segundo núcleo necessário ao fluxo atual. API, schemas, services e repositories têm responsabilidades identificáveis; `core.py` concentra extensa orquestração e deve ser tratado como área de risco em mudanças futuras. Nenhum ciclo de import foi comprovado nesta auditoria.

**CURRENT_REAL_END_TO_END_FLOW:** contratos e componentes integrados em código; etapas isoladas e transações exercitadas por testes. Não existe nesta rodada cadeia de foto externa real passando pelo frontend, ONNX, Event e mapa. Fixtures de integração não substituem essa prova.

**CANONICAL_FLOW_STATUS:** implementado com validação parcial de produto. Novos assessments usam `assess_features()`. A busca de consumidores não encontrou uso operacional legítimo de `assess()` legado para novos eventos; sua permanência não prova dois motores ativos. Remoção não autorizada nesta rodada.

## CURRENT_GIT_STATE

| Campo | Resultado |
|---|---|
| Branch / HEAD | `feat/urmind-v1-public-center` / `437c21d` |
| Worktrees | Um worktree registrado |
| Submódulo YOLOX | `6ddff4824372906469a7fae2dc3206c7aa4bbaee` |
| Status inicial | 142 entradas: 82 modificadas e 60 `??` (diretórios podem agregar arquivos) |
| Diff tracked inicial | 11.342 inserções / 984 remoções |
| Commits criados | Nenhum |
| GIT_REPRODUCIBILITY_STATUS | BLOCKED para reproduzir estado atual a partir de HEAD |
| UNCOMMITTED_WORK_STATUS | Extenso, misturado, preexistente |
| COMMIT_PLAN_REQUIRED | YES, em execução posterior autorizada |
| SECRET_TRACKING_STATUS | `.env` backend/frontend ignorados e não rastreados; scan completo de histórico não realizado |
| CLEAN_CHECKOUT_RISK | HIGH: módulos centrais e documentação ainda untracked |

Classificação das mudanças preexistentes, sem apropriação/autocommit:

| Categoria | Caminhos/grupos representativos |
|---|---|
| PHASE_7 / SECURITY / PRIVACY | API core/public, auth, config, main, services storage/photo_ingest/public_view, worker, schemas |
| TAXONOMY | `app/schemas/issue_taxonomy.py`, `app/datasets/taxonomy_candidates.py`, registry JSON, fixture frontend |
| PHASE_8 | `app/ml/tabular.py`, `tests/test_tabular_phase8.py` |
| PHASE_9 | `app/services/history.py`, `tests/test_history_phase9.py`, repository |
| FRONTEND / MAP | App, CapturePage, PublicPages, UrbanMap, contracts, auth/API/drafts, mapConfig, estilos |
| APIS | `app/services/external_sources/`, contexto, importador OSM, testes externos |
| OBSERVABILITY | logging, observability, pilot_check e respectivos testes |
| DEPLOY | main, vite.config, exemplos env, manifests/configuração de pacote |
| DOCUMENTATION | README, PROJECT_STATE, FAIR_DEMO_CHECKLIST, EXTERNAL_INTEGRATIONS, D40_ROOT_CAUSE_ANALYSIS |
| DATASET_RESEARCH | relatórios IRD/RDD, candidatos V2, scripts acquisition/audit |
| TEST | testes modificados/untracked de cada domínio, Playwright e fixtures |
| UNRELATED ao escopo mobile | alterações científicas ML/treino/evaluator, manifests/splits/autorizações; schemas/testes Scout/multimodal preexistentes |
| UNKNOWN / reconciliação pendente | `mlflow.db` raiz e propriedade exata de mudanças sobrepostas |

Os arquivos científicos não foram descartados nem alterados. Há `mlflow.db` na raiz e banco sob `mlruns/`; isso exige identificar finalidade/lineage, não autoriza classificá-los como duplicatas apagáveis. Alguns artefatos V1 têm atributo ReparsePoint: isso, isoladamente, não prova cloud-only ou corrupção. Disponibilidade offline completa não foi certificada.

## CURRENT_SUPABASE_STATE

**ENVIRONMENT_ALIGNMENT_STATUS: PASSED.** URLs carregadas dos env locais de backend/frontend correspondem ao DEV. Chave frontend do tipo publishable. Valores secretos não reproduzidos. Nenhum projeto antigo foi consultado.

| Componente | Evidência atual |
|---|---|
| SUPABASE_STATUS | Projeto ACTIVE_HEALTHY, `impm...ggy` |
| PostgreSQL | SQL: 17.6; versão de plataforma informada: 17.6.1.166 |
| POSTGIS_STATUS | 3.3.7; operações espaciais read-only válidas |
| ALEMBIC_STATUS | Banco e head local `0019_context_retention`; 19 revisions, head único |
| SCHEMA_DRIFT_STATUS | PARTIAL: estruturas críticas inspecionadas; sem diff integral de schema reconstruído |
| RLS_STATUS | 17 tabelas públicas com RLS; anon sem grants; authenticated SELECT restrito em events/risk_assessments |
| STORAGE_STATUS | Bucket `captures` privado, 10 MiB, JPEG/PNG/WebP; teste real com remoção |
| QUEUE_STATUS | pgmq/inference_jobs, trigger Capture e testes reais de fila/rollback |
| REALTIME_STATUS | events + risk_assessments publicados; entrega autorizada exercitada por integração |
| AUTH_STATUS | Settings ao vivo: anonymous users habilitado; JWT/JWKS/roles exercitados por integração |
| MODEL_REGISTRY_STATUS | Um modelo, shadow; nenhum modelo production-approved |

Extensions observadas: plpgsql, pg_stat_statements, uuid-ossp, pgcrypto, supabase_vault, postgis, pgmq. Índices espaciais GiST presentes em vias, captura, eventos e rota. `Detection.model_version_id` NOT NULL e FK RESTRICT. Triggers de fila, timestamps, ordenação serializada, proteção de proveniência e retenção de assessment presentes.

Políticas Realtime exigem `app_metadata.urmind_role` reviewer/admin. Sem policies de cliente no Storage; acesso de evidência mediado pelo backend. Advisors: 15 INFO de tabelas RLS sem policy (compatível com fail-closed), dois WARN relativos ao papel authenticated também incluir sessões anônimas. Não são prova automática de exploração: claim administrativo não é atribuível pelo payload comum. Recomenda-se testar explicitamente a paridade de negação anônima em RLS/API.

**DATA_COUNTS após limpeza:** users 0; storage objects 0; captures 0; detections 0; events 0; road_segments 0; risk_assessments 0; reviews 0; audit_log 0; queue pendente 0; model_versions 1; dataset_versions 1. Não há Ground Truth nem histórico real neste DEV para treinamento.

Testes podem consumir valores de sequences mesmo com rollback; não houve reset para ocultar isso. Nenhuma fixture de negócio permaneceu. Nenhuma migration aplicada nesta auditoria.

## DOCUMENTATION_VS_REALITY

O cabeçalho atualizado do README conflita com seu corpo: o corpo ainda declara 230 testes, apenas três integrações, Supabase/Storage/Auth/Worker inexistentes e secrets de Supabase ainda não consumidos. A seção de API também descreve caminhos antigos sem esclarecer todas as restrições atuais. Isso pode induzir operação incorreta.

MASTER_PLAN/DOCX preservam roadmap histórico e fluxo de modelo promovido; precisam explicitar a exceção DEV shadow rejeitado, os contratos atuais e escopo mobile. Não foi certificada equivalência técnica integral entre os dois nesta rodada. Referências Scout devem permanecer históricas, não como trabalho necessário à feira.

PROJECT_STATE está mais próximo do código, mas a conclusão de prontidão condicionada apenas à foto precisa incorporar o defeito do harness real. EXTERNAL_INTEGRATIONS/README mencionam 14 integrações enquanto o catálogo atual é mais amplo. Comentário de configuração sobre Supabase não consumido está obsoleto.

## Gates por fase

| Campo | Status e limite |
|---|---|
| PHASE_3_STATUS / PHASE_3_GATE | BLOCKED_DATA / BLOCKED_REVIEW; BLOCKED |
| PHASE_3_BLOCKERS | Revisão humana, independência/autorizações do novo holdout, qualidade científica rejeitada |
| YOLOX_TRAINING_READY | NO |
| PHASE_4_STATUS / PHASE_4_GATE | Implementada; PASSED na regressão de domínio + DEV desta rodada |
| PHASE_5_STATUS / PHASE_5_GATE | Implementada por regras; PASSED na regressão de domínio + DEV |
| PHASE_6_STATUS / PHASE_6_GATE | Implementada; PASSED na regressão de domínio + DEV |
| PHASE_7_STATUS / PHASE_7_GATE | PARCIAL, BLOCKED_CODE + BLOCKED_INPUT/OPERATION; BLOCKED |
| REAL_E2E_READINESS | NÃO comprovada; harness requer correção antes de prova válida |
| PHASE_8_SCAFFOLDING_STATUS / PHASE_8_GATE | PARCIAL, contratos/export/guardas testados; BLOCKED como pipeline completo |
| XGBOOST_TRAINING_READY | NO |
| PHASE_9_DATA_PIPELINE_STATUS / PHASE_9_GATE | PARCIAL, CLI descritiva; BLOCKED por semântica histórica e integração incompleta |
| PHASE_9_PREDICTION_STATUS | BLOCKED_HISTORY; nenhuma probabilidade validada |
| PHASE_10A_STATUS / PHASE_10A_GATE | Registry/pesquisa implementados; PASSED para registry offline, BLOCKED para curadoria/ativação de classes |

Os PASS das Fases 4–6 não significam calibração científica de risco, dados humanos já disponíveis ou E2E visual real. Não foi reexecutada reconstrução desde banco vazio.

### Fase 3 / MODEL_STATUS / ARTIFACT_INTEGRITY

ModelVersion DEV `2527af02-c995-4e5a-b6c6-d6904c8d35b5`: EXPERIMENTAL_SHADOW, autorização URMIND_DEV_ONLY, promoted_at nulo, closure/Frozen Test REJECTED. Nenhum production-approved no registry consultado. Classificação canônica distingue shadow, rejected, archived, quarantined, reference e produção com gate aprovado.

Hash ONNX quality rebuild recalculado: `7d91f7f6f0c0cc6aafc491f13329b2e084cc0d8e80831884354a1a4808dcbde8`, correspondente ao contrato. Hash do resultado Frozen Test já fechado: `8c663b17d4a2bbe817af3be8457142f9b27f2da6e47ad0051634b4444fa21e42`, correspondente à evidência registrada. Leitura/hash não reabriu avaliação científica. Parity aprovada é evidência do contrato histórico, não execução nova. Run preservado: `4c557dfdae5b4e48b0733f7ad73ebe2c`; best/last presentes, 107.864.651 bytes cada. Não foi refeito inventário criptográfico integral de RAW/EMA/MLflow/checkpoints.

**PROMOTION_SAFETY:** guardas e classificação testadas; produção fail-closed sem modelo aprovado. **SCIENTIFIC_BLOCKERS:** não desaparecem pela autorização shadow. Nenhum treinamento, benchmark científico ou Frozen Test executado.

### Fases 4–6

FeatureBuilder usa `urmind-features-v1`, missingness explícita, fontes, contexto temporal e snapshot em RiskAssessment. Migrações 0017–0019 tratam legado, ordem persistida e retenção; contexto posterior não deve reescrever avaliação antiga. Regressões de snapshot, ordenação e histórico passaram.

`assess_features()` separa Impact, Severity, Risk e Priority, com `urmind-risk-rules-v1` e `calibration_required`. Valores ordinais não são probabilidade. Responsibility/Action permanecem em camadas próprias. DecisionTrace e lineage são contratos reais, não saída LLM.

Review preserva inferência, exige identidade/role autenticada, consenso de revisores distintos ou adjudicação admin para elegibilidade; conflitos não se tornam Ground Truth. AuditLog/transações/export foram exercitados no DEV. O banco estar vazio significa que sistema de review existe, mas dados rotulados suficientes não existem.

### Fase 7 / REAL_E2E_BLOCKERS

Capture recuperável por `#/processando/:capture_id`, consulta com ownership e estados intermediários existem. `completed` depende de Event, snapshot, RiskAssessment e trace; `detection_completed` não é usado como sucesso final. Sem detecção suportada não deve nascer problema fictício. GPS/EXIF/manual preservam origem declarada; snap não substitui original.

**PUBLIC_FLOW_STATUS:** integrado em código/testes, sem prova real completa. **PRIVATE_FLOW_STATUS:** eventos/review existentes; admin/ground-truth têm superfícies informativas incompletas, não console completo. **REAL_E2E_BLOCKERS:** F-01 abaixo; foto externa e localização; zero RoadSegments; HTTPS estável e aparelho físico; Worker contínuo e pipeline ONNX com upload real não demonstrados nesta rodada. Anonymous Auth habilitado não equivale à prova ponta a ponta de sessão anônima real.

## TAXONOMY_STATUS

`urmind-issue-taxonomy-v2`: 17 classes, sete famílias, registry tipado. ACTIVE_CLASSES: nenhuma comprovada como production-approved. EXPERIMENTAL_CLASSES: D00, D10, D20, D40. DATA_REQUIRED_CLASSES: FALLEN_TREE, FALLEN_BRANCH, ILLEGAL_DUMPING, ROAD_DEBRIS, OPEN_MANHOLE, BLOCKED_DRAIN, FLOODED_ROAD, SIDEWALK_DAMAGE, SIDEWALK_OBSTRUCTION, DAMAGED_TRAFFIC_SIGN, FALLEN_TRAFFIC_SIGN, DAMAGED_STREETLIGHT_POLE, DAMAGED_BARRIER.

Worker mantém allowlist visual atual; testes bloqueiam emissão automática de classes DATA_REQUIRED. Registry não implica dataset treinável. Há mapeamentos de apresentação legados, como CLASS_LABELS no relatório, cuja equivalência deve continuar testada; não se comprovou segundo sistema de taxonomia operacional.

## DATASET_STATUS / STORAGE_BUDGET_STATUS

**Fatos observados nos relatórios existentes:** IRD local: 439 imagens, 104 labels vazios, zero validações humanas registradas; zero matches exatos RDD e zero near-duplicates no limiar auditado, mas seis grupos internos próximos. Ausência de match nesse procedimento não prova independência universal. UNIVALI: 0/32 revisões humanas e semântica de instância pendente. Urban Community: 0/50 amostras revisadas; 451 boxes sem decisões individuais, quatro pares duplicados pendentes. RDD possui autorizações/manifests V2 locais ainda untracked.

A exclusão anterior de demonstração de `Dash_0559.jpg` permanece registrada; seu grupo conservador cobre a fonte correlacionada (1.006 frames de origem, 439 locais). Não foi usada em E2E e não foi removida do raw. Não deve voltar a futuro holdout.

**Inferências:** ferramentas de aquisição/análise estão mais prontas que os dados científicos. **Desconhecidos:** integridade atual de todo raw, espaço alocado real no OneDrive, licenças revalidadas hoje, independência humana definitiva. **Blockers:** curadoria, revisão, licença/proveniência específica, split/holdout autorizado. **Decisão conservadora:** nenhum dataset/classe promovido por esta auditoria.

| Fonte candidata registrada | Readiness preservado |
|---|---|
| Roadway Flooding/Mendeley, TACO, Roboflow Illegal Dumping, Lost and Found | NEEDS_HUMAN_REVIEW |
| Urban Community Open Manhole, Roboflow Open-Manholes, Milton Street View, Damaged Signs Kaggle, RoadObstacle21 | NEEDS_HUMAN_REVIEW |
| FloodNet, Tornado treefall | DOMAIN_MISMATCH: aéreo não equivale street-level |
| Mapillary Vistas, SideGuide, SideSeeing | LICENSE_BLOCKED |
| Mapillary Traffic Sign, Project Sidewalk/RampNet para as classes pretendidas | NOT_USABLE nesse mapeamento |

Todas as 13 classes novas permanecem sem readiness de treinamento comprovado. Não se refez pesquisa web/licenciamento nem baixou dado. Budget registrado: 40 GB, 7 GB/fonte e exceção RDD; ~35,3 GB é estimativa histórica, **não medição atual**. Não se apagou lineage para liberar espaço.

## Fase 8 / TARGET_LEAKAGE_STATUS

`app/ml/tabular.py` contém export/allowlist, schema, elegibilidade e controles temporais/grupos, com testes. Scaffolding não equivale a harness completo validado de baseline, XGBoost, calibração, SHAP, MLflow e promoção. Readiness mantém autorização de treino falsa; target preparado inclui `review_confirmed`, que não equivale automaticamente a label científico de risco/severidade.

XGBoost, SHAP e sklearn não estão disponíveis no ambiente inspecionado; ONNX Runtime está. Nenhum pacote instalado nesta rodada. Ground Truth real no DEV: zero. **TARGET_LEAKAGE_STATUS: guardas testadas, validação empírica com dataset real inexistente.** Promoção por flags/contratos ainda requer prova de artefatos e integração operacional. Não declarar Fase 8 READY completa.

## Fase 9 / DESCRIPTIVE_ANALYTICS_STATUS

HistoryRepository + `services/history.py` fornecem agregação por trecho/grade/janela e GeoJSON/CLI. Não se comprovou integração completa em APIs pública/privada e frontend. Hotspots são descritivos, não previsão. F-05/F-06 impedem afirmar reconstrução histórica segura. **HISTORY_BLOCKERS:** zero eventos reais, filtro baseado no status atual, ausência de temporalidade de conhecimento e consumidores de produto incompletos.

## STRUCTURED_ANALYSIS_STATUS

Contrato `urmind-urban-analysis-v1` e templates determinísticos descrevem identificação, evidência, dados ausentes, impactos, limitações e consequências potenciais. DIAGNOSIS_STATUS: descritivo/limitado, não diagnóstico físico medido. POTENTIAL_CONSEQUENCES_STATUS: hipóteses condicionais, não causalidade demonstrada. Possíveis causas não são preenchidas como fatos sem fonte.

PRESCRIPTION_STATUS / RESPONSIBILITY_ROUTING_STATUS: infraestrutura de regras/catálogo existe; disponibilidade de órgão/ação concreta depende das regras aplicáveis. Não há evidência de cobertura operacional completa de todas as classes/jurisdições. Não confundir texto genérico com prescrição validada em campo. DECISION_TRACE_STATUS: persistência e contratos testados; ausência de E2E real permanece.

## MAP_STATUS

MapLibre/OSM, GeoJSON e pontos existem. PUBLIC_MAP_STATUS: filtrado por publicação, não todos os Events recém-criados. PRIVATE_MAP_STATUS: parcial. ROADSEGMENT_STATUS: **zero no DEV**; importador existe, deve aguardar localização real para recorte OSM com provenance. Nenhuma via fictícia criada fora de fixtures removidas.

REALTIME_MAP_STATUS: infraestrutura e atualização testadas parcialmente; não há prova de mapa final com evento E2E. MAP_PRIVACY_STATUS: separação público/privado e DTOs; original/snap distintos. `UrbanMap.tsx` utiliza severidade para cor e não implementa integralmente marcadores por prioridade/família/status nem clustering solicitado. Não chamar mapa 2D completo.

## API_INTEGRATION_STATUS

Registry/CLI de integrações existe e é mais amplo que a documentação de 14 fontes. Health/last-success/last-failure no catálogo não constituem telemetria real: há campos UNKNOWN. Não foram disparadas todas as APIs externas nesta auditoria.

| Provider | Estado comprovado / limite |
|---|---|
| Supabase PostgreSQL/PostGIS/Auth/JWKS/Storage/pgmq/Realtime | ACTIVE nos testes DEV; fluxo anônimo mobile completo não provado |
| Supabase Data API | Exposição mínima por grants/RLS inspecionada |
| Nominatim / Overpass / Open-Meteo | Consumidores de contexto existem; PARTIAL, chamadas live gerais não revalidadas |
| GeoSampa / SIDRA / BrasilAPI / ViaCEP | Adapters/configuração/CLI e testes; PARTIAL, não prova de uso automático útil em todo Event |
| OSM / Geofabrik | Importação/aquisição preparada; sem novo recorte real no DEV |
| MapLibre / OpenFreeMap / CARTO | Frontend configurado com fallback quando aplicável; produção HTTPS não validada |

FALLBACK_STATUS: falha contextual é representada como indisponibilidade, não dado fabricado. PROVENANCE_STATUS: estruturas presentes; frescor/health persistente por provider incompleto. ORPHAN_INTEGRATIONS: nenhuma remoção recomendada sem rastrear CLI; adapters sem consumo automático não são necessariamente órfãos. BROKEN_INTEGRATIONS: nenhuma falha live específica comprovada além dos gaps do harness; indisponibilidade não testada não foi classificada como sucesso.

## FRONTEND_STATUS / DEPLOY_STATUS

PUBLIC_FRONTEND_STATUS: rotas de landing, captura, processamento, resultado, mapa e demo existem; regressão mockada passa. PRIVATE_FRONTEND_STATUS: eventos/reviews funcionais em testes; dashboard não certifica painel operacional completo; admin/ground-truth incompletos. BROKEN_ROUTES: incompatibilidade concreta no teste real F-01; não se comprovou link público quebrado generalizado.

MOBILE_READINESS_STATUS: PARTIAL. Câmera/upload/GPS/EXIF/manual e refresh possuem código/testes; não foram exercitados Android/iPhone físicos. Playwright mobile usa Chromium emulado, não Safari. 320px, permissões reais, câmera real e rede móvel não certificados. ACCESSIBILITY_STATUS: auditoria visual/estática parcial, sem certificação WCAG. DEMO_MODE_STATUS: UI distingue exemplo revisado; não há exemplo real publicado no DEV vazio que prove fallback da feira.

DEPLOY_READINESS_STATUS: PARTIAL. HTTPS_STATUS: sem URL pública estável validada nesta rodada. CSP_STATUS: configuração e regressão existentes; produção não certificada. QR_READINESS_STATUS: BLOCKED por URL estável. WORKER_DEPLOY_STATUS: processo contínuo supervisionado não comprovado. FAIR_DEMO_CHECKLIST_STATUS: existe, mas checklist não prova execução. Não foi feito deploy.

## SECURITY_STATUS / PRIVACY_STATUS

Controles presentes: JWT/JWKS, roles do servidor, ownership, bloqueio de criação cliente de Detection/Event, paths de Storage gerados pelo servidor, validação de conteúdo/tamanho/MIME, quotas, bucket privado, DTO allowlist e sanitização de cópia pública. Original privado é separado da derivada sanitizada. Remoção de EXIF não equivale a anonimização de rostos/placas; publicação/revisão continua necessária.

Testes cobrem isolamento e sanitização com fixtures, não uma foto pessoal real publicada. Não se encontrou exploração comprovada de SQL injection, XSS, path traversal ou bypass de role no escopo revisado; isso não certifica ausência. Auditoria completa de dependências, histórico de secrets e resistência distribuída a abuso não realizada. Achados abaixo permanecem OPEN.

## OBSERVABILITY_STATUS

Logs estruturados, correlation_id, IDs de captura/job/modelo/evento e métricas operacionais existem. TRACEABILITY_STATUS: parcial, sem cadeia real integral demonstrada. METRICS_STATUS: fila/Worker/latências e consultas operacionais, não cobertura integral de todas as métricas desejadas. LOG_PRIVACY_STATUS: não encontrado secret nos trechos revisados; logs históricos completos não escaneados. MISSING_ALERTS: thresholds/alertas operacionais e painel de saúde de providers não comprovados. Não foi adicionado serviço.

## TEST_RESULTS

| Suíte/comando | Resultado fresco |
|---|---|
| Backend `pytest -q -ra` | **1177 passed, 18 skipped, 2 warnings**, 49,53 s |
| Backend focado adicional | 142 passed, 2 warnings; subconjunto, não somar como cobertura nova |
| Integração DEV `test_db_integration.py` | **17 passed, 2 warnings**, 16,99 s, adaptação no harness abaixo |
| Arquitetura | **53 passed**, 2 warnings, 0,81 s; sem hang |
| Vitest | **45 passed**, cinco arquivos |
| Playwright | **76 passed, 2 skipped**, 24 s |
| Ruff `check app tests` | PASSED |
| mypy `app` | PASSED, 72 arquivos-fonte |
| Prettier `--check src tests vite.config.ts` | PASSED nesse escopo |
| Frontend build | PASSED; warning de chunks >500 kB |
| Alembic heads | Um head: `0019_context_retention` |
| git diff --check | PASSED; avisos LF/CRLF não são falha de whitespace |

Baseline declarado reproduzido, sem regressão de suíte detectada. Isso não invalida findings em cenários ausentes dos testes.

**Adaptação auditável da integração:** a fixture original chama `upgrade()`. Para não aplicar migrations, um plugin pytest em memória substituiu exclusivamente essa chamada por conexão read-only e assert do head já existente. Nenhum arquivo de teste foi editado; demais testes executaram operações reais no DEV com fixtures. Logo, o resultado **não prova aplicação/reconstrução de migrations**. Testes de Storage/Auth também foram executados isoladamente antes; são sobrepostos aos 17, não somados. Limpeza confirmada pelas contagens finais.

**SKIPPED_TESTS:** os 17 testes DEV pulados pela suíte offline foram exercitados separadamente como acima. O outro skip backend de verificação externa opt-in continua não executado. Os dois skips Playwright são do E2E real condicionado a entrada/configuração; não aprovados. Não houve treino nem avaliação científica incluídos para inflar cobertura.

Warnings adicionais: Playwright informou conflito NO_COLOR/FORCE_COLOR e proxy de Auth com ECONNREFUSED em 127.0.0.1:8000 durante cenários mockados; testes passaram, mas isso reforça que não eram backend live. Build: index ~672 kB, UrbanMap ~1.022 kB e precache ~2.239 KiB; relevante para rede móvel. Dois warnings pytest não foram promovidos a finding sem causa comprovada. Nenhum hang reproduzido.

**COVERAGE_GAPS:** E2E real, Anonymous Auth pelo navegador ao vivo, Safari real, telefone físico, 320px, conexão lenta real, deploy, rebuild vazio, full schema diff, catálogo externo live completo, licenças atuais, scan integral de dependências/secrets e inventário científico criptográfico completo.

## Findings (todos OPEN; nenhuma correção autorizada)

### CRITICAL_FINDINGS

Nenhum confirmado no escopo auditado. Não significa certificação de segurança.

### HIGH_FINDINGS

**F-01 — Harness E2E real contradiz rota e política de publicação.**

- EVIDENCE/AFFECTED_PATH: `frontend/tests/real-e2e.spec.ts:141` espera `#/events/<uuid>`; `frontend/src/App.tsx:592` emite `#/resultado/<id>`. Linha 143 do teste faz GET público sem Bearer; linha 177 exige ponto no mapa público sem revisão/publicação anterior.
- REPRODUCTION: comparar regex com link emitido; mesmo corrigindo a regex, request fixture não recebe automaticamente o token guardado no localStorage da página. Event novo não publicado não fica público por ter completado análise.
- IMPACT: teste real não fecha mesmo com foto válida; “falta somente input” é claim excessivo. Também exige email/senha, não comprova caminho Anonymous Auth, e não cobre corretamente terminal sem detecção suportada.
- RECOMMENDED_FIX: alinhar rota, testar resultado do proprietário autenticado, separar publicação/revisão e mapa público, acrescentar sessão anônima real e terminal sem detecção. Não burlar publicação nem inserir Detection/Event.

**F-02 — Estado funcional não reproduzível a partir de HEAD.**

- EVIDENCE/AFFECTED_PATH: status Git de 142 entradas; `app/ml/tabular.py`, `app/schemas/issue_taxonomy.py`, `app/services/history.py`, adapters e testes/documentos untracked.
- REPRODUCTION: `git status --short`, `git ls-files` para esses caminhos; HEAD não contém o estado testado. Não se criou checkout novo nesta auditoria.
- IMPACT: risco de perda, entrega parcial e testes passando apenas na máquina atual.
- RECOMMENDED_FIX: revisão de propriedade e commits lógicos autorizados, preservando lineage e excluindo secrets. Nenhum commit nesta rodada.

### MEDIUM_FINDINGS

**F-03 — Esgotamento anônimo da quota compartilhada de imagens públicas.**

- EVIDENCE/AFFECTED_PATH: `backend/app/api/v1/public.py:72` e 331–350; `services/storage.py:66`. Limiter de 60/min usa a mesma identidade `public-image` antes de verificar existência/publicação.
- REPRODUCTION: 60 UUIDs válidos mas inexistentes consomem o orçamento; o próximo acesso legítimo recebe 429. Reprodução local da classe confirmou esgotamento; não se atacou endpoint live.
- IMPACT: visitante anônimo consegue negar imagens a outros visitantes naquele processo/janela.
- RECOMMENDED_FIX: orçamento por origem/caller confiável e limite global de recurso separado; não depender só de mover o limiter após lookup de ID público.

**F-04 — Runtime PostgreSQL usa identidade privilegiada.**

- EVIDENCE/AFFECTED_PATH: parsing mascarado do username do DATABASE_POOLER_URL identifica `postgres...`; configuração distingue URL runtime/migration, não roles efetivamente limitadas.
- REPRODUCTION: inspeção local sem imprimir password/URL; comparar grants necessários ao runtime com papel configurado.
- IMPACT: maior blast radius se backend for comprometido; RLS de cliente não limita conexão privilegiada.
- RECOMMENDED_FIX: role runtime mínima e migrations separadas, com testes de permissões. Sem mudança de credenciais nesta auditoria.

**F-05 — Histórico Fase 9 usa conhecimento atual em consultas antigas.**

- EVIDENCE/AFFECTED_PATH: `backend/app/repositories/core.py:421`–440, HistoryRepository filtra occurred_at e status atual, sem cutoff de persistência/revisão equivalente ao snapshot Fase 4.
- REPRODUCTION: consultar passado; inserir posteriormente evento backdated ou revisar status depois; repetir consulta. A query admite informação que ainda não era conhecida. Análise estática, sem criar dados permanentes.
- IMPACT: relatório histórico não reproduzível e risco de future leakage se reutilizado em treino. Não foi comprovado vazamento em modelo treinado; consumidor atual é limitado/CLI.
- RECOMMENDED_FIX: explicitar retrospectivo versus point-in-time, preservar instante de conhecimento/status e bloquear reutilização científica sem essa semântica.

**F-06 — Janela histórica ausente vira zero.**

- EVIDENCE/AFFECTED_PATH: `backend/app/services/history.py:170`–174, `.get(..., 0)`.
- REPRODUCTION: `describe_segment({'confirmed_events_last_30d': 3}, days=14)` produz zero para janela de 14 dias não calculada. Reprodução local feita, sem arquivo editado.
- IMPACT: ausência de dado apresentada como ausência comprovada de ocorrência; alcance atual limitado ao consumidor descritivo.
- RECOMMENDED_FIX: rejeitar janela não disponível ou retornar desconhecido com disponibilidade explícita.

**F-07 — Documentação operacional contraditória.**

- EVIDENCE/AFFECTED_PATH: README, seções Estado atual/Como executar, declara Supabase não provisionado e Auth/Storage não usados, apesar de cabeçalho/DEV comprovarem o contrário.
- REPRODUCTION: leitura conjunta das seções e consultas DEV desta auditoria.
- IMPACT: operação/aceitação baseada em instrução obsoleta, inclusive interpretação de endpoints e etapas científicas.
- RECOMMENDED_FIX: reconciliar README, status, checklist e planejamento MD/DOCX; preservar histórico identificado como tal.

**F-08 — Operação pública da feira não demonstrada.**

- EVIDENCE/AFFECTED_PATH: zero dados/roads no DEV, E2E real skipped, ausência de URL HTTPS estável/Worker supervisionado comprovados.
- REPRODUCTION: contagens SQL e resultados de suíte; checklist não é evidência de execução.
- IMPACT: risco de demonstração falhar apesar da regressão verde.
- RECOMMENDED_FIX: após F-01, foto/localização externa autorizada, recorte OSM real e teste HTTPS em telefone físico, sem fixtures substituindo pipeline.

### LOW_FINDINGS

**F-09 — Estado de processamento retido após logout.** `frontend/src/App.tsx:315`–319 retorna sem sessão antes de limpar captureStatus; render 583–590 não exige sessão. Em navegador compartilhado, logout na tela de processamento pode deixar status/modelo/links anteriores visíveis. API continua protegida: não é IDOR remoto confirmado. Reproduzir logout nessa tela; limpar estado e condicionar render ao proprietário atual.

**F-10 — Locks de publicação atravessam I/O Storage.** `backend/app/api/v1/core.py:522`–555 mantém transação/locks durante download/upload. Impacto: contenção sob lentidão. Recomenda-se preparar artefato fora do lock e validar atomicamente a versão/estado; não remover invariantes. Não houve teste de carga.

**F-11 — Mapa e área privada incompletos frente ao escopo declarado.** UrbanMap usa severidade, sem cobertura integral de prioridade/família/clustering; App contém páginas admin/ground-truth informativas, não fluxos completos. Impacto: readiness superestimado, não falha comprovada do núcleo. Recomenda-se definir aceitação mínima de feira e implementar/retestar em rodada própria.

**F-12 — Health de integrações e bundle mobile.** Catálogo com UNKNOWN não prova última execução; build avisa chunks grandes. Recomenda-se separar configuração de telemetria e medir carregamento móvel antes de otimizar. Sem afirmar outage/performance ruim não medida.

## DUPLICATION / ORPHANS / OBSOLETE_COMPONENTS

Não encontrado segundo backend/frontend operacional. Mappings de labels/contratos entre Python e TypeScript exigem testes de equivalência; testes atuais ajudam, mas não eliminam toda duplicação semântica. Legacy assess permanece sem consumidor novo identificado. `mlflow.db` raiz versus `mlruns/` não deve ser apagado sem investigação de lineage.

ORPHANS: integração CLI sem consumidor frontend não é automaticamente órfã. Fase 9 e parte da Fase 8 são componentes preparados sem cadeia de produto completa. OBSOLETE_COMPONENTS: documentação e descrições operacionais antigas, não autorização de remoção científica. BROKEN_COMPONENTS: harness real F-01 e missingness F-06. PARTIAL_COMPONENTS: mapa, área admin/ground-truth, observabilidade/provider health, deploy, Phase 8/9.

## Revisão adversarial / correction-review

Revisão independente read-only confirmou F-03 e F-09; revisão principal reproduziu o primeiro localmente e confrontou o segundo com o render. Os findings de E2E/histórico foram confrontados com consumidores e limites para não confundir risco futuro com exploração atual. Não foi feita etapa de correção: todos permanecem OPEN. Nenhum skipped foi promovido a PASS; nenhuma fixture foi apresentada como E2E real.

**Aguardando etapa de correcao.**

## OPEN_PROBLEMS / RESOLVED_PROBLEMS_VERIFIED

OPEN: F-01 a F-12; dados/revisão/holdout da Fase 3; Ground Truth Fase 8; histórico Fase 9; curadoria/licenças novas classes; foto/localização externa; HTTPS/celular; reconstrução limpa/Git; cobertura de auditoria explicitada acima.

RESOLVED_PROBLEMS_VERIFIED nesta rodada: **nenhuma correção nova**. Revalidados comportamentos preexistentes: regressão de Fases 4–6, arquitetura termina sem hang, head único, Storage compensation, contratos Auth/RLS/Queue/Realtime, shadow separado de produção e envs alinhados. Revalidação não apaga blockers não cobertos.

DOCUMENTATION_UPDATES_REQUIRED: README obsoleto; PROJECT_STATE deve incluir F-01; checklist deve separar mock/DEV isolado/E2E/celular; catálogo de integrações e comentários config; master/DOCX precisam equivalência formal e distinção roadmap/entrega atual.

COMMITS_REQUIRED: sim, posteriormente e por domínio após revisão; não incluir `.env`, fotos pessoais, caches ou banco MLflow sem classificação. Preservar trabalho científico preexistente, não misturá-lo automaticamente a commit mobile.

## TOP_NEXT_ACTIONS / RECOMMENDED_EXECUTION_ORDER

1. Corrigir F-01 e reproduzir seus cenários com testes que falhem antes da correção; não enfraquecer ownership/publicação.
2. Corrigir quota pública e estado pós-logout; verificar em regressão e revisão independente.
3. Corrigir semântica de histórico/missingness e bloquear uso tabular indevido.
4. Reconciliar documentação/Git e provar checkout reproduzível, sem tocar Frozen Test.
5. Obter foto externa aos datasets e localização confirmada; importar somente recorte OSM real necessário.
6. Executar E2E real anônimo, refresh, ONNX shadow, resultado privado do proprietário e publicação/mapa quando autorizados; registrar IDs e tempos sem PII.
7. Validar HTTPS/telefone/Worker supervisionado e contingência da feira.
8. Completar recorte de mapa/área privada/observabilidade definido para entrega; depois curadoria e treinamento sob novos gates científicos.

WHAT_CAN_BE_DONE_BEFORE_TRAINING: todos os fixes de código acima, documentação, versionamento, permissões runtime, UI mínima, telemetria, harness e deploy readiness.

WHAT_REQUIRES_REAL_DATA: E2E visual externo, recorte espacial, revisão/holdout YOLOX, Ground Truth tabular, histórico temporal e validação de novas classes.

WHAT_REQUIRES_USER_INPUT: foto real não científica com localização confirmada; decisão/credenciais manuais caso necessário para hospedagem; revisão humana e autorizações científicas. Nenhuma credencial deve constar do relatório.

### Fechamento de integridade de dados

- Arquivos alterados pelo agente: somente este relatório; artefatos ignorados de testes/build não são código científico.
- Gates afetados: Fases 3/7/8/9 e curadoria 10A bloqueadas nos escopos descritos; regressões 4–6 aprovadas.
- Testes executados: tabela TEST_RESULTS; skips preservados.
- Pendências humanas: foto/localização, revisão de dados e holdout, decisões de publicação/deploy.
- Riscos: perda de trabalho uncommitted, disponibilidade pública, semântica temporal e claims de prontidão excessivos.

## Conclusão

**INFRASTRUCTURE_READY_EXCEPT_TRAINING: NO**

**ONLY_MAJOR_WORK_REMAINING:** correções E2E/segurança/histórico; reprodutibilidade Git/documental; foto/localização e prova real; operação HTTPS/mobile/Worker; partes de mapa/área privada/observabilidade; curadoria/licenças/holdout; Ground Truth e histórico; então treinamentos autorizados.

**FINAL_VERDICT: URMIND_NEEDS_ATTENTION**
