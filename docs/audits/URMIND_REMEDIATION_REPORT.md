# UrMind — remediação parcial, 24/09/2026

## Exclusão autorizada de treinamentos anteriores

81 artefatos removidos para Lixeira; inventário em `TRAINING_CLEANUP_2026-09-24.md`.
Modelo DEV antigo arquivado e shadow desautorizado; REJECTED preservado.
Sem inferência visual operacional até novo modelo autorizado. A limpeza não
fecha F-01–F-12 nem os gates de dados. Não equivale a readiness científica.

## F-05 — arquivo forward-only de observações (continuação)

**STATUS: PARTIAL / OPEN.** Não equivale à conclusão da Fase 9 ou readiness científica.

- Causa: consultar Events/reviews atuais não reproduz o conhecimento de um
  relatório anterior. O modo retrospectivo permanece explicitamente separado.
- Correção: HistoryRepository captura linhas/cutoff em uma única instrução MVCC,
  com limite de 10.000 observações (excesso aborta, nunca trunca silenciosamente).
  Reutiliza AuditLog; não cria tabela ou serviço paralelo. SHA256 cobre todo
  payload JSON; o replay valida schema, timezone, cobertura, janela e hash.
- Migration 0021_history_snapshot_retention aplicada via Alembic somente DEV:
  bloqueia UPDATE/DELETE e conversão de outro registro em snapshot. Downgrade
  recusa remover proteção enquanto houver arquivo histórico. Não concede acesso
  cliente e não altera policies existentes.
- CLI canônica: `python -m app.services.history --days 90` continua retrospectiva
  e read-only; `--days 90 --freeze` grava uma nova observação server-time;
  `--snapshot UUID` reproduz exclusivamente o arquivo persistido.
- Teste RED: duas funções de snapshot ausentes; depois 27 testes history passam.
  Integração DEV verifica roundtrip JSONB/hash, revisão posterior, Event backdated,
  preservação do arquivo e negação de UPDATE/DELETE. São fixtures isoladas
  rollbackadas, NÃO E2E visual nem dados científicos.
- Regressão: backend **1205 passed / 20 skipped / 2 warnings**; DEV separado
  **19 passed / 2 warnings**. CLI `--help`, head único, mypy 72 arquivos passam.
  Ruff completo, format dos cinco arquivos Python alterados e `git diff --check`
  aprovados (avisos Git LF/CRLF não são falhas). Frontend não alterado nem
  reexecutado neste bloco; seus resultados anteriores são históricos.
  Skips offline: 19 DEV opt-in (executados separadamente) + 1 live externo.
- Revisão independente somente leitura: nenhum defeito concreto no recorte.
  Limites: chamadores internos/credenciais de escrita são confiáveis; hash não
  autentica origem; trigger não impede superusuário/restore administrativo.
  A captura utiliza o isolamento READ COMMITTED padrão; não promete equivalência
  para sessões externas com outro isolamento.
- Verificação cloud: head 0021, zero snapshots/eventos de teste remanescentes.
- Gate F-05 abrangente: **BLOCKED**. O arquivo não reconstrói qualquer cutoff
  passado e não está integrado como feature científica autorizada no export.
  `scientific_feature_eligible=False` permanece obrigatório. Cobertura de consulta
  não prova cobertura completa do mundo real, e occurred_at continua alegação.
- Para PASS: concluir integração/contratos científicos dos consumidores, validar
  cutoffs de label e dados reais autorizados. E2E, runtime mínimo, deploy,
  curadoria/holdout, GT suficiente e Git reproduzível continuam OPEN.

Fontes técnicas consultadas: [isolamento PostgreSQL 17](https://www.postgresql.org/docs/17/transaction-iso.html).
Skills aplicadas: correction-review, no-duplicate-files, supabase:supabase,
supabase:supabase-postgres-best-practices, urmind-adversarial-review e
urmind-verification-gate; conector Supabase. Componentes estendidos: HistoryRepository,
history CLI e AuditLog. Migration nova necessária para retenção; nenhuma estrutura paralela.


## F-03 — quota distribuída aplicada no DEV

**STATUS: PARTIAL; subfinding de quota somente por processo corrigido e verificado.**

- Alvo reconfirmado pelo plugin: Urmind DEV (`impm...ggy`). URLs locais validadas
  em memória antes das execuções; nenhum `.env` alterado ou secret exibido.
- Migration **0020_public_image_quota**, após 0019, cria ledger temporário com
  checks, identity, índice stage/tempo e RLS. Nenhum grant cliente; mínimo
  SELECT/INSERT/DELETE para service_role. F-04/runtime limitado ainda OPEN.
- `PublicImageQuota` permanece em `app/repositories/core.py`, conforme arquitetura
  canônica. PostgreSQL serializa admissão por estágio usando advisory xact lock;
  relógio lido após aquisição. Limites por 60s: lookup caller120/global6000;
  download caller60/recurso30/global60. Falha DB retorna 503 sem fallback local.
- Consulta inexistente consome lookup, não download. JWT validado ou peer ASGI
  identificam caller; X-Forwarded-For recebido pelo endpoint não é confiado.
  Configuração de proxy confiável no deploy continua responsabilidade operacional.
- Cleanup remove entradas vencidas do estágio na próxima admissão, inclusive
  quando ela é recusada. Sem tráfego, entradas podem permanecer: não prometer
  expurgo físico exatamente em 60s. Não armazena token/IP bruto, somente hash.
- A API encerra a transação de leitura antes de adquirir conexão para quota e
  antes de Storage I/O; depois revalida publicação. Revisão independente encontrou
  starvation de pool no candidato inicial, corrigido; revisão da correção sem
  novo finding concreto. Nenhuma mudança em review/owner gates.
- Teste falhou antes (API seguia sem quota persistente); depois 80 testes focados
  passaram. Teste real com dois pools competindo pelo limite por recurso aceita
  exatamente um; verifica orçamento global, isolamento caller, expiração e grants.
  Headers falsos, UUIDs inexistentes e falha DB também cobertos offline.

### Regressões encontradas e corrigidas

- Fronteira arquitetural: import de SQLAlchemy pela API e novo módulo repository
  contrariavam regras existentes. Corrigidos sem enfraquecer testes: SQL/erros
  traduzidos no repositório canônico. Import circular com services eliminado.
- ORM: nome de check exigido pela convenção; corrigido antes de aplicar no DEV.
- Storage real: timeout/502 deixou uma fixture órfã; removida por API após
  identificação exata. Repetição de delete pelo endpoint individual retornou
  400 para objeto já ausente. Corrigido para DELETE bucket + `prefixes:[path]`,
  contrato do SDK oficial, com teste que falhou antes e passou depois.
- Cleanup de teste agora executa remoção DB mesmo se remoção Storage falhar e
  usa identidade única por execução. Uma Capture residual da tentativa anterior
  foi inspecionada (sem Event/Storage) e removida por ID e chave exatos.
  Somente fixtures descartáveis foram removidas, não evidências reais.

### Evidência final

| Check | Nível | Resultado |
|---|---|---|
| pytest completo | unit/local integration | 1203 passed, 19 skipped |
| test_db_integration.py com DEV validado | real service integration | 18 passed |
| Quota concorrente: pools independentes | PostgreSQL integration | PASS |
| API quota/privacidade + Auth/Storage | unit/mock HTTP | 112 passed |
| Ruff app/tests; mypy app | static | PASS, 72 módulos |
| Alembic heads + consulta DEV | inspection/real DB | único 0020_public_image_quota |
| Resíduos fixtures | real DB/Storage metadata | quota0, captures0, objects0 |
| diff check | local | PASS |

18 skips offline são os mesmos testes DEV executados separadamente; 1 live
externo não executado. Não somar rodadas nem chamar skip de PASS. Warnings
Starlette/httpx/AnyIO persistem. Frontend não alterado nesta rodada; seus números
abaixo pertencem à rodada anterior. E2E real continua não executado.

Limitações: sem ensaio de carga de feira, sem prova HTTP completa com pool de
uma conexão, sem downgrade aplicado, sem checkout limpo, sem métricas novas de
abuso além de logs categorizados. F-03 abrangente não marcado integralmente RESOLVED.
Falha de compensação após indisponibilidade persistente de Storage ainda exige
reconciliação operacional; não foi implementada fila durável de compensação.

Skills aplicadas: systematic-debugging, correction-review, Supabase,
Supabase Postgres Best Practices e revisão de candidato de segurança. Conector:
Supabase (projeto/SQL); documentação oficial PostgreSQL e SDK Supabase consultada.
Changelog Supabase consultado; nenhuma mudança aplicável ao protocolo de quota.
Fonte de delete: https://github.com/supabase/storage-js/blob/main/src/packages/StorageFileApi.ts

Gate global **BLOCKED**: F-01/F-02/F-04/F-05/F-07/F-08/F-09/F-10/F-11/F-12
continuam OPEN/PARTIAL. Nenhum treino, Frozen Test, promoção ou commit realizado.
YOLOX/XGBoost training readiness continuam **NO** por dados e revisão, além dos
blockers técnicos remanescentes. Próximo bloco: PIT e runtime de menor privilégio.

## Continuação — F-12 bundle e layout mobile

- F-06 novamente verificada: **65 passed** em history/tabular/features. O defeito
  de cobertura omitida descrito no estado inicial enviado já está corrigido;
  não foi reimplementado. Isso não encerra F-05/PIT.
- F-12 **PARTIAL**: captura, login, lista privada e detalhe privado agora usam
  lazy import no App canônico, com estado acessível de carregamento. O mapa já
  era lazy e não foi duplicado. O PWA mantém precache para uso offline.
- Medição de build: entrypoint **675,12 → 493,67 kB**; módulo compartilhado
  pré-carregado **86,37 kB**. Total estático inicial **580,04 kB** (redução ~14%).
  Gzip entry + preload **164,18 kB**, antes **195,56 kB**. Não confundir com
  tráfego total: mapa continua **1.021,75 kB**, e precache total ~**2.243 KiB**.
  A home ainda carrega seu mapa ao renderizá-lo; health real e perfil de rede
  lenta permanecem OPEN. Warning de chunk do mapa preservado, não suprimido.
- Teste de orçamento falhou antes (**675.120 bytes > 650.000**). Verifica agora
  entry e preloads do HTML servido. Links adicionados dinamicamente pelo mapa
  não são contados nesse orçamento estático, nem apresentados como economia.
- Novo viewport **320px** reproduziu overflow de **55px** no detalhe. Causa:
  `detail-grid` exigia 360px mínimos. Correção usa o menor valor entre largura
  disponível e 360px; não oculta conteúdo com overflow hidden.
- Regressão final: Vitest **45 passed**, Playwright **98 passed / 2 skipped**,
  incluindo 320px, rotas privadas, captura, logout, refresh e PWA offline.
  Dois skips continuam E2E real sem input aprovado. Arquitetura **53 passed**;
  head único **0019_context_retention**. TypeScript/build/Prettier aprovados.
  Warnings de dependências Starlette/AnyIO e chamadas não interceptadas a
  auth-origin sem backend local foram observados; não provam integração DEV.
- Alterações limitadas a `frontend/src/App.tsx`, `frontend/src/styles.css`,
  `frontend/tests/responsive.spec.ts` e documentação. Nenhuma alteração de env,
  banco, migrations, datasets, Frozen Test ou modelos neste bloco.
- Readiness científica **NO** para YOLOX e XGBoost: revisão/autorização dos dados
  e Ground Truth real suficiente não são substituídos por testes de interface.
  F-01/F-02/F-03/F-04/F-05/F-07/F-08/F-09/F-10/F-11 continuam OPEN/PARTIAL.

Revisão adversarial independente deste bloco: **nenhum defeito concreto**;
confirmou 580.045 bytes no HTML e chunks lazy no precache gerado. Não reexecutou
testes; instalação de PWA interrompida e atualização durante sessão permanecem
não verificadas. A varredura responsiva pode medir antes dos dados finais pois
aguarda `main`; não representa auditoria visual integral de todos os estados.

Gate abrangente: **BLOCKED**. Evidência local: unit (Vitest e backend focado),
browser integration com APIs simuladas (Playwright), build real e inspection
(precache/imports/HTML); diff check aprovado. Manifesto PWA produzido pelo build,
chunks alcançáveis e rotas exercitadas. Registries/hashes científicos e cloud são
N/A para este patch, não revalidados como readiness do projeto. Para PASS global:
fechar findings operacionais ainda abertos, provar pipeline real e satisfazer
gates de dados. Não houve certificação de fases 3/7/8 nem novo gate independente
abrangente das fases 4–6. Skills usadas neste bloco: systematic-debugging,
test-driven-development, correction-review, urmind-adversarial-review e
urmind-verification-gate. Nenhum conector Supabase necessário para esse patch.


## Atualização — correção das duas corridas de autenticação F-09

Esta atualização substitui o estado OPEN dos dois subfindings de autenticação
descritos na revisão histórica abaixo, não o estado dos demais findings.

- Causa reproduzida no navegador: uma operação iniciada sem sessão adotava a
  conta conectada durante `auth-origin`; um signup tardio persistia a sessão no
  SDK antes da verificação de cancelamento da aplicação.
- Correção no cliente Auth canônico: operação vinculada ao proprietário inicial,
  cancelamento da requisição de signup e validação antes da persistência da
  sessão pelo SDK. A criação anônima legítima identifica seu proprietário antes
  de notificar a aplicação. Troca externa de conta e logout invalidam a operação.
  Não foi criado outro cliente Auth ou sistema paralelo de sessões.
- Arquivos: `frontend/src/services/auth.ts`, `frontend/src/App.tsx`,
  `frontend/tests/app.spec.ts`. Preservadas alterações preexistentes nesses arquivos.
- Testes: falhas de atribuição de upload e substituição de sessão reproduzidas
  antes da correção. Depois, 12 testes de fronteira passaram (origin/signup ×
  troca/logout/sucesso × desktop/mobile), além dos testes anteriores de logout.
- Revisão independente da correção e da dependência instalada: nenhum finding
  concreto novo. Não constitui prova de linearizabilidade entre abas nem de
  autenticação ao vivo contra o DEV.
- Regressão: Vitest **45 passed**; Playwright **94 passed / 2 skipped**. Os dois
  skips são o E2E real sem imagem aprovada, não PASS. O primeiro Vitest revelou
  acesso a `localStorage` sem ambiente browser; corrigido com guarda, com nova
  execução integral aprovada. Prettier, TypeScript e build aprovados. Bundle
  inicial **675,12 kB / gzip 195,56 kB**, mapa **1.021,75 kB**: F-12 segue OPEN.
- Gate dos dois subfindings: **PASS no escopo browser/SDK com HTTP controlado**.
  F-09 geral permanece **PARTIAL**, aguardando validação operacional real de
  troca de usuário/logout; E2E real, deploy e outros findings não foram encerrados.
- `.env`, Supabase, migrations e artefatos científicos não alterados. Nenhum
  treinamento, commit, push ou promoção de modelo realizado neste bloco.

Próximo bloco: quota distribuída F-03; depois PIT, permissões runtime e
concorrência de publicação. Dados/revisão humana e Ground Truth continuam
blockers independentes de treinamento. **URMIND_REMEDIATION_PARTIAL**.

## Continuação autorizada — F-06 e F-09

Esta seção supersede o status anterior somente nos pontos explicitados abaixo;
as demais evidências abaixo pertencem à rodada anterior.

- F-06: **RESOLVED**, gate **PASS** no serviço descritivo/CLI existente. Cobertura
  omitida gera `unknown` e contagens completas `null`; cobertura insuficiente
  gera `insufficient_history`; `observed_window_counts` preserva apenas contagens
  observadas. Texto público exige disponibilidade explícita. Cobertura completa
  mantém zero real. Cobertura futura é erro de contrato. Não se infere cobertura
  pelo primeiro evento. Os testes anteriores de contagem agora declaram cobertura.
- Reprodução: seis falhas no novo teste antes da correção; depois **65 passed**
  em history + tabular + features. Revisão independente: **No findings**, com
  **47 passed** próprios em history/tabular. API/frontend não consomem diretamente
  esse relatório na busca realizada; não foi criado adaptador paralelo.
- Verificação F-06: backend completo **1199 passed / 18 skipped**, Ruff focado
  aprovado, mypy **72 arquivos** aprovado; head único `0019_context_retention`.
  Os 18 skips são 17 testes DEV opt-in e 1 live externo. Não reexecutados no DEV
  nesta continuação; os 17 PASS DEV da rodada anterior são evidência histórica.
- F-05 continua **OPEN**: cobertura de consulta não implementa conhecimento
  point-in-time nem prova completude da observação do mundo real. Export tabular
  continua recusando relatórios explicitamente retrospectivos.
- F-09: **PARTIAL / OPEN**. Upload agora possui operação cancelável, token
  vinculado à sessão admitida e guardas após awaits; polling é abortado no evento
  Auth; hidratação inicial não substitui evento Auth mais recente. Testes novos
  cobrem upload retido e polling não terminal retido após logout, em desktop/mobile
  emulados: **6 passed** incluindo o teste terminal anterior. Não é prova de Auth
  ao vivo ou celular físico. Revisão de segurança identificou os dois caminhos
  remanescentes descritos a seguir: gate F-09 **NEEDS_ATTENTION**.
- Git: patch tracked adicional `.git/urmind-remediation-continuation.patch` e
  inventário de paths untracked `.git/urmind-continuation-untracked-inventory.tsv`.
  Classificação inicial é heurística, não autoriza commits científicos. Nenhum
  commit criado; checkout limpo continua não verificado.
- Componentes **EXTENDED_EXISTING_COMPONENT**: history e testes existentes;
  cliente HTTP/Auth e App existentes. Nenhuma implementação paralela criada.

### Gate F-06: cobertura e limites

| Check | Nível | Resultado |
|---|---|---|
| Ausente/parcial/completa, zero, 7/30/90, JSON, texto | unit | PASS |
| FeatureBuilder e export tabular | unit | PASS; não prova dataset científico |
| Consumidores, CLI, defaults e fail-closed | inspection | PASS |
| Ruff/format/mypy, backend completo | local regression | PASS |
| Registry/hashes/manifests científicos | inspection | N/A: não alterados nem consumidos pela correção |
| Nova API/frontend/cloud | inspection | N/A: nenhuma superfície nova; CLI DB não executada |
| Revisão independente focada | review | No findings |

Ações para PASS de F-06: nenhuma no recorte definido. Phase 9 permanece parcial
por F-05 e integração de produto. Outros findings permanecem OPEN, não há
readiness de treinamento nem prova real foto→mapa.

### Revisão independente F-09 — pausa obrigatória

- severity: high
- arquivo: `frontend/src/App.tsx`
- localizacao: callback Auth / `send`, `ownerId` inicialmente nulo
- problema: envio iniciado sem sessão aceita uma sessão externa surgida enquanto
  `/public/auth-origin` está pendente; ao prosseguir, pode adotar conta/token novos.
- impacto: upload iniciado anteriormente pode ser atribuído a outro usuário sem
  novo comando de envio desse usuário.
- recomendacao: distinguir criação anônima pertencente à operação de autenticação
  externa; invalidar a operação na substituição e testar a janela anterior ao POST.

- severity: high
- arquivo: `frontend/src/services/auth.ts`
- localizacao: `ensureVisitorSession`, chamada `signInAnonymously`
- problema: o signal é verificado depois da chamada; a dependência instalada
  persiste a sessão e emite SIGNED_IN antes de retornar. Signup tardio não é
  cancelado antes da instalação da sessão.
- impacto: operação cancelada pode recriar sessão após logout ou substituir conta
  conectada durante a requisição.
- recomendacao: proteger criação/instalação da sessão com geração/cancelamento
  antes da persistência; reproduzir signup tardio com logout e troca de conta.

Evidência: revisão estática do código e dependência, não reprodução browser dessas
duas janelas. Não foram corrigidas após a primeira revisão, conforme a pausa da
skill adversarial. Cancelamento de upload já aceito pelo servidor não desfaz a
Capture; as garantias locais dizem respeito à continuação e exposição no browser.

### Resultados finais desta continuação

Backend **1199 passed / 18 skipped**; arquitetura **53 passed**; Vitest **45 passed**;
Playwright **82 passed / 2 skipped**. Os dois skips são o E2E real, sem entrada
aprovada. Ruff completo `app tests`, mypy `app`, Prettier dos arquivos alterados,
build e diff check aprovados. Bundle inicial **673,36 kB / gzip 195,01 kB**;
mapa **1.021,75 kB**. Não houve otimização F-12 e o warning continua registrado.
Nenhuma regressão de suíte restante; a revisão encontrou lacunas que a suíte não
cobre. Integração DEV não reexecutada nesta continuação; não somar PASS histórico.

Skills aplicadas nesta continuação: systematic-debugging (catálogo Superpowers),
karpathy-guidelines, no-duplicate-files, correction-review, review-agent,
urmind-adversarial-review, urmind-verification-gate, codex-security:fix-finding e
supabase:supabase para fronteira Auth. Consultada documentação oficial
`https://supabase.com/docs/reference/javascript/auth-onauthstatechange`;
changelog Markdown não foi recuperado pelo navegador. Nenhum conector cloud ou
plugin de deploy utilizado nesta continuação. Não se reivindica uso nominal das
skills de dataset/design que não tiveram operação neste bloco.

### Estado agregado solicitado

FINDINGS_RESOLVED: F-06. FINDINGS_PARTIAL: F-01/F-03/F-05/F-07/F-09/F-10.
FINDINGS_OPEN sem implementação adicional: F-02/F-04/F-08/F-11/F-12.
Todos os parciais também permanecem OPEN para aceitação integral.

GIT_REPRODUCIBILITY_STATUS=BLOCKED; COMMITS_CREATED=NONE;
CLEAN_CHECKOUT_STATUS=NOT_RUN; DOCUMENTATION_SYNC_STATUS=PARTIAL.
SECURITY_STATUS=NEEDS_ATTENTION; PRIVACY_STATUS=PARTIAL;
RUNTIME_DB_ROLE_STATUS=OPEN; PUBLICATION_TRANSACTION_STATUS=PARTIAL.

PHASE_3_STATUS=BLOCKED_DATA/BLOCKED_REVIEW; PHASE_3_DATA_STATUS=HUMAN_REVIEW_REQUIRED;
YOLOX_TRAINING_READY=NO; revisão/autorização/holdout independente continuam pendentes.
PHASE_4/5/6_STATUS=IMPLEMENTED; regressão local passa; gates operacionais abrangentes
não reexecutados nesta continuação. PHASE_7_STATUS=PARTIAL; PHASE_7_GATE=BLOCKED;
REAL_E2E_STATUS=NOT_RUN.

MAP_STATUS/PUBLIC_MAP_STATUS/PRIVATE_MAP_STATUS=PARTIAL;
PUBLIC_FRONTEND_STATUS/PRIVATE_FRONTEND_STATUS/MOBILE_READINESS_STATUS=PARTIAL.
API_HEALTH_STATUS/OBSERVABILITY_STATUS/MOBILE_BUNDLE_STATUS=PARTIAL.
DEPLOY_READINESS_STATUS=PARTIAL; HTTPS_STATUS/WORKER_SUPERVISION_STATUS=UNVERIFIED;
QR_READINESS_STATUS=BLOCKED.

PHASE_8_SCAFFOLDING_STATUS=PARTIAL; XGBOOST_TRAINING_READY=NO;
XGBOOST_REMAINING_BLOCKERS=Ground Truth real suficiente, PIT, dataset/split autorizado
e harness completo; TARGET_LEAKAGE_STATUS=PARTIAL_GUARDS_NOT_FULL_CERTIFICATION.
PHASE_9_DATA_PIPELINE_STATUS=PARTIAL; PHASE_9_PREDICTION_STATUS=BLOCKED_HISTORY.
PHASE_10A_STATUS=REGISTRY_RESEARCH_WITH_CURATION_PENDING.

WHAT_REQUIRES_USER_INPUT: etapa explícita de correção dos dois achados F-09;
revisão humana dos dados; configuração manual futura da URL runtime; aparelho
físico para prova mobile quando disponível. WHAT_REQUIRES_REAL_DATA: Ground Truth,
holdout independente, foto E2E com licença/proveniência/localização e histórico.
Não houve aquisição de foto nem alteração de dataset nesta continuação.

INFRASTRUCTURE_READY_EXCEPT_TRAINING=NO.
FINAL_VERDICT=URMIND_REMEDIATION_PARTIAL; gate agregado=BLOCKED.
Prioridade para PASS: corrigir/reproduzir janelas Auth F-09 (P1), depois concluir
findings restantes, operação/E2E e readiness científica; não basta executar treinos.

### Integridade de dados — fechamento deste bloco

Arquivos alterados: history.py, seu teste, App.tsx, services/api.ts, services/auth.ts,
app.spec.ts, PROJECT_STATE e este relatório; patch/inventário local sob `.git`.
Gates: F-06 closed/passed; demais gates amplos open/blocked ou não reexecutados.
Testes: resultados acima; nenhuma avaliação científica executada.
Pendências: revisão humana e autorizações científicas permanecem sem substituição.
Riscos restantes: nenhuma nova afirmação de independência/duplicatas/licenças;
lineage preexistente preservada. Nenhuma autorização nova de treino/avaliação.

## Veredito e limites

GENERAL_STATUS = PARTIAL
FINAL_VERDICT = URMIND_REMEDIATION_PARTIAL
INFRASTRUCTURE_READY_EXCEPT_TRAINING = NO

Este relatório registra somente a rodada atual. Não substitui a auditoria original nem atribui à rodada as alterações preexistentes. Nenhum treinamento, Frozen Test, promoção, alteração de `.env`, deploy, commit ou push foi realizado. Somente Urmind DEV (`impm...ggy`) foi acessado. Frontend/backend tiveram alinhamento DEV verificado sem imprimir credenciais.

## Findings: causa, correção, teste, revisão e risco restante

| Finding | Status | Causa / correção nesta rodada | Teste e verificação | Risco restante / próxima ação |
|---|---|---|---|---|
| F-01 | PARTIAL / OPEN | Harness usava rota e autenticação inadequadas. Passou a usar sessão anônima da UI, token do proprietário, rota resultado, terminal sem detecção e separar publicação. | Testes frontend passam; E2E real permanece skipped. | Foto/localização externa aprovada, execução real e review/publicação/mapa ainda não comprovados. |
| F-02 | OPEN | Working tree contém trabalho anterior misturado. Preservado diff rastreado em `.git/urmind-pre-remediation.patch`. | Git inspecionado; credenciais locais ignoradas. | Patch não é backup dos arquivos untracked. Inventário/classificação integral, commits e checkout limpo ainda pendentes. |
| F-03 | PARTIAL / OPEN | UUID inexistente consumia quota de download. Separadas admissão de lookup, caller autenticado/peer, recurso e download global após elegibilidade. | Regressão reproduziu 429 após UUIDs inexistentes; agora recurso legítimo permanece acessível no caso testado. | Limites em memória por processo; carga, proxy confiável e todas as combinações de quotas não comprovados. |
| F-04 | OPEN | Runtime de menor privilégio ainda não provisionado/validado. | Nenhuma nova prova de roles. | Provisionamento reproduzível, permissões negativas e troca manual de configuração pelo usuário. |
| F-05 | PARTIAL / OPEN | Histórico atual podia ser interpretado como conhecimento passado. Relatório explicitamente retrospectivo; pedido point-in-time rejeitado; export tabular rejeita marcador retrospectivo. | Novos testes de contrato passam. | Reconstrução point-in-time completa, persistência/knowledge cutoff e auditoria de todos os produtores pendentes. |
| F-06 | PARTIAL / OPEN | Ausência de janela virava zero no texto. Removido fallback; validação de janelas e cobertura explícita adicionadas. | Testes de missing, zero, inválido e cobertura insuficiente passam. Revisão independente encontrou defeito residual abaixo. | Cobertura omitida ainda resulta em available: não fechar finding. |
| F-07 | PARTIAL / OPEN | Documentação contém baselines e claims anteriores. Criado este relatório de evidência. | Relatório distingue provas atuais e pendências. | Sincronização integral de README, planos, DOCX e demais documentos pendente. |
| F-08 | OPEN | HTTPS/operação supervisionada não comprovados. | Nenhum deploy realizado. | URL estável, Worker supervisionado, testes físicos e QR pendentes. |
| F-09 | PARTIAL / OPEN | Estado de processamento sobrevivia à sessão. Estado vinculado à identidade/rota; logout limpa estado e deixa processamento. | Playwright desktop/mobile reproduziu vazamento de link antes da correção e passa depois. | Caso testado é terminal; falta prova dedicada de logout durante requisição/polling não terminal e troca real de usuário. |
| F-10 | PARTIAL / OPEN | Storage I/O ocorria com transação/lock aberto. Copiados valores, rollback antes de I/O e revalidação sob lock curto antes do commit; compensação preservada. | Testes verificam rollback antes de download, alteração durante I/O, falha de commit e remoção do derivado. | Concorrência PostgreSQL/Storage real e idempotência completa ainda não exercitadas. |
| F-11 | OPEN | Recorte completo mapa/área privada não implementado nesta rodada. | Regressão existente passa, não prova todos os requisitos. | Clustering/filtros/detalhes e páginas operacionais precisam verificação e complementação. |
| F-12 | OPEN | Saúde real e otimização mobile não concluídas. | Build medido: index 672,58 kB (gzip 194,77); mapa 1.021,75 kB. | Checks reais por integração, lazy loading e comparação antes/depois pendentes. |

Nenhum finding abrangente F-01–F-12 foi marcado RESOLVED nesta rodada.

## Achado da revisão independente

- severity: medium
- arquivo: `backend/app/services/history.py`
- localizacao: `segment_history` / `hotspot_report`, cobertura omitida
- problema: `coverage_start=None` ainda deixa janelas 7/30/90 como `available`. Um evento observado ontem pode ser apresentado como contagem completa de 90 dias sem evidência de cobertura.
- impacto: contagem parcial apresentada como histórico completo; F-06 permanece aberto.
- recomendacao: exigir cobertura explícita ou marcar janelas desconhecidas como indisponíveis, preservando separadamente contagens observadas; adicionar teste para argumento omitido.

A primeira revisão adversarial é somente leitura e exige pausa antes da correção. Não corrigido silenciosamente. Também não foram provadas corridas de publicação reais, quotas entre processos nem E2E sem mocks.

## Testes executados

| Suíte | Resultado atual |
|---|---|
| Backend completo | 1190 passed, 18 skipped, 2 warnings |
| Integração Urmind DEV, execução explícita separada | 17 passed, 2 warnings |
| Arquitetura | 53 passed |
| Vitest | 45 passed |
| Playwright | 78 passed, 2 skipped |
| Ruff check | PASSED |
| mypy | PASSED, 72 arquivos |
| Formatação backend | Apenas arquivos alterados nesta rodada |
| Prettier | PASSED nos três arquivos frontend alterados nesta rodada |
| Build frontend | PASSED, com bundle grande ainda pendente |
| git diff --check | PASSED; avisos de conversão LF/CRLF |

Skips backend: 17 integrações opt-in (executadas separadamente no DEV) e 1 teste externo opt-in não executado. Skips Playwright: 2 E2Es reais sem imagem aprovada/configuração necessária. Não são PASS. Warnings incluem deprecações Starlette/httpx e AnyIO; testes frontend simulados não comprovam serviços reais.

Consulta read-only após integração: Captures=0, Events=0, RoadSegments=0, Reviews=0. Essas contagens corroboram limpeza dessas entidades, não demonstram sozinhas ausência de todo resíduo Auth/Storage. Alembic permaneceu com head único `0019_context_retention`; nenhuma migration nova nesta rodada.

## Gates e readiness

| Gate | Estado desta rodada |
|---|---|
| Phase 3 readiness | BLOCKED_DATA / BLOCKED_REVIEW; YOLOX_TRAINING_READY=NO |
| Phase 4 | Regressão verde; gate abrangente não reexecutado independentemente |
| Phase 5 | Regressão verde; gate abrangente não reexecutado independentemente |
| Phase 6 | Regressão verde; gate abrangente não reexecutado independentemente |
| Phase 7 | BLOCKED: código/verificação residual, operação e input real |
| Phase 8 scaffolding | PARTIAL; proteção retrospectiva adicionada, PIT incompleto |
| Phase 8 training readiness | BLOCKED_DATA; XGBOOST_TRAINING_READY=NO |
| Phase 9 data pipeline | BLOCKED pelo achado de cobertura; predição BLOCKED_HISTORY |
| Phase 10A | Curadoria/revisão não avançadas nesta rodada |

REAL_E2E_STATUS=BLOCKED_INPUT, além das pendências operacionais e de validação listadas; não significa que todos os bloqueios de código tenham sido resolvidos. Modelo permanece experimental, cientificamente rejeitado; nenhuma autorização de produção emitida.

## Git e superfícies preservadas

Branch `feat/urmind-v1-public-center`, HEAD inicial `437c21d`. Nenhum commit criado. Alterações preexistentes preservadas; checkout limpo ainda não testado. Nenhum dataset, split, checkpoint ou resultado científico foi alterado por esta rodada. Arquivos científicos já modified/untracked no início não devem ser confundidos com alterações desta remediação. Não houve exclusão de arquivos nem novos dados científicos.

## Skills, plugins e conectores efetivamente aplicados

Aplicados: `karpathy-guidelines`, `no-duplicate-files`, `correction-review`, `urmind-adversarial-review`, `urmind-verification-gate`; instruções `review-agent` fornecidas pelo usuário; `superpowers:systematic-debugging`, `superpowers:test-driven-development`, `codex-security:fix-finding`, `supabase:supabase`, `supabase:supabase-postgres-best-practices`.

`deep-recon` não constava no catálogo: inspeção direta equivalente. Não se reivindica auditoria completa de datasets, design visual ou scan de segurança completo nesta rodada. Plugin/conector Supabase utilizado para inspeção DEV; Codex Security aplicado por skill e revisões independentes, não como certificação externa. Sites, GitHub, Product Design, Data e Visualize não utilizados.

## Próximas ações e trabalho maior restante

1. Correção explícita do achado adversarial de cobertura e teste de regressão.
2. Completar quotas/concorrência/logout não terminal e testes reais correspondentes.
3. Implementar runtime de menor privilégio e point-in-time completo.
4. Completar mapa/área privada, health e bundle mobile.
5. Sincronizar documentação, revisar dirty tree e provar checkout limpo antes de commits.
6. Preparar HTTPS/Worker; obter foto externa e coordenadas confirmadas; executar E2E real, publicação e celular físico.
7. Revisão humana/autorização de dados YOLOX e Ground Truth real suficiente para XGBoost. Nenhum desses requisitos pode ser substituído por fixtures.

ONLY_MAJOR_WORK_REMAINING inclui essas remediações, operação, dados/revisão humana e E2E, além dos futuros treinamentos. Não está limitado a treinar modelos.
