# Áreas, mapa e porteiro — diagnóstico e execução

## Fechamento do bloco 6 e verificação — 24/09/2026

Esta seção substitui os estados anteriores para a continuação atual. As seções
abaixo são evidência histórica, não uma declaração do estado vigente.
**IMPLEMENTATION_STATUS=PARTIAL; VERIFICATION_GATE=BLOCKED.** Nenhuma suíte executada
tem falha residual; calibração, aparelho físico e entrega Realtime ao vivo não foram
comprovados. Não chamar a entrega completa de DONE com base em mocks.

### Implementado nesta continuação

- D7: aviso versionado público e aceite explícito por identidade. Migration
  `0027_capture_privacy_consent`, RLS de leitura exclusivamente do titular, gravação
  pelo backend. Sem aceite atual, o upload para antes do Storage. Versão antiga
  recebe 409; o rascunho permanece local. Aceite idempotente não muda a data original.
- D2: consulta PostGIS no raio configurado (25 m default) mostra somente relatos
  próprios ou já publicados. Não enumera relatos privados de terceiros. Confirmação
  anexa a nova Capture como evidência, preservando foto/local originais, sem um
  segundo marcador ou Event. Revalidação sob lock curto após Storage e compensação
  em falha. Contagem de pessoas usa proprietários distintos, sem divulgar identidades.
  O revisor desanexa, com Review operacional + AuditLog + reenfileiramento.
  Atualização do pai notifica seus assinantes sem revelar o proprietário da filha.
- Migration `0028_report_evidence_actions`: decisão operacional `detach_evidence`
  não é voto científico, não se vincula a Event e não entra no consenso/Ground Truth.
  Downgrade recusa remover semântica com revisões existentes. `0027` também recusa
  apagar consentimentos existentes. Up/down/up das duas revisions exercitados no DEV.
- D4: exportação CSV/GeoJSON dos pontos carregados e filtrados, com allowlist explícita;
  sem identidade, Storage path, imagem ou EXIF. CSV protege células de fórmula.
- D5: sugestão condicional por domínio canônico e link SP156 somente com aviso de
  jurisdição de São Paulo. Nunca afirma encaminhamento ou órgão validado.
- D8: mesmo TaxonomyPanel no público e painel interno, sem registry paralelo.
- Fila interna: filtros de status/família/classe/período/conflito/verificação pendente,
  prioridade existente primeiro, senão mais antigos. Link direto `/app/relato/:id`
  protegido pelo backend; detalhe inclui original assinado, porteiro e histórico.
- Correção: localização humana confirmada permite marcador mesmo se original era
  ausente, sem substituir original. Classes humanas candidatas passam pelo contrato
  frontend sem ampliar a allowlist do Worker.
- Tema claro/escuro segue preferência do dispositivo; controles de captura com
  alvos de toque de 44 px. Sem inverter foto/tiles. Teste 320 px reproduziu ausência
  de tema e passou após correção. O input file transparente é medido pelo label clicável.

Componentes: EXTENDED_EXISTING_COMPONENT (Capture, Review, AuditLog, fila, UrbanMap,
drafts, export e taxonomia). Novas migrations justificadas por consentimento persistido
e separação explícita de ação operacional/voto; nenhum backend/mapa/fila paralelo.

### Evidências novas e limites

| Verificação / comando | Resultado fresco | Nível |
|---|---|---|
| `python -m pytest tests/test_core_service.py tests/test_auth_storage.py -q` | 145 passed | unit/contratos HTTP com dependências substituídas |
| `python -m pytest -q -ra` no backend | **1276 passed / 29 skipped**, 2 warnings, 93,34 s | suíte offline |
| `pytest tests/test_db_integration.py -q -k 'not supabase_auth_real_roles_login_and_jwks and not realtime_delivers_event_change_to_reviewer'` com env DEV carregado somente no processo | **26 passed / 2 deselected**, 29,85 s | PostgreSQL/PostGIS/Storage/Queue/RLS reais |
| `npm test -- --run` | **48 passed**, 5 arquivos | Vitest |
| `npx playwright test --workers=2` | **120 passed / 2 skipped**, 56,3 s | browser desktop/mobile com APIs simuladas |
| `python -m ruff check app tests` + migrations novas | PASS | estático |
| `python -m mypy app` | PASS, 73 arquivos | estático |
| `npx prettier --check .`; `npx tsc --noEmit`; `npm run build` | PASS | formato/tipos/build |
| `python -m alembic heads` e SELECT DEV | **0028_report_evidence_actions**, head único | local + banco |
| `git diff --check` | PASS | diff |

Baseline solicitado 1246: +30 testes offline aprovados. Desde a última rodada1272,
+4 aprovados; novas integrações opt-in elevam os skips offline de25 para29. Os29 são
os28 testes em `tests/test_db_integration.py` (lista autoritativa nesse arquivo) e
`tests/live/test_external_sources_live.py` (externo opt-in). Dos28,26 executados no
DEV; somente `test_supabase_auth_real_roles_login_and_jwks` e
`test_realtime_delivers_event_change_to_reviewer` foram deselected porque criam/removem
usuários Auth, proibido nesta tarefa. RLS/publication Realtime foram testados sem
alterar Auth; isso NÃO prova entrega websocket de ponta a ponta. Os2 Playwright skips
são `real-e2e.spec.ts` nos projetos desktop/mobile, sem foto/localização autorizadas;
o harness científico antigo ainda exige shadow arquivado e não é prova deste fluxo
humano sem modelo. SKIPPED != PASSED.

`test_human_report_real_storage_publication_and_cleanup` comprovou imagem sintética
própria + EXIF → Storage real → Capture → confirmação humana → Event human_review
sem Detection → derivada sanitizada → DTO/publicação → retirada. Original byte a
byte preservado, derivada sem EXIF, cleanup executado. Chamada de handler com identidade
de fixture, NÃO login anônimo nem E2E de cidadão/celular. Teste de colisão executa
corpo canônico do trigger com entropia forçada em tabelas temporárias isoladas;
não substitui a função de produção. Consentimento e desanexação foram verificados
com RLS real. Consulta pós-testes: zero consentimentos, revisões de desanexação e
anexações de fixture remanescentes.

Warnings: deprecações Starlette/httpx e anyio; chunk lazy MapLibre1038,00kB
(276,17kB gzip). Entrada288,89kB (88,47kB gzip). Nenhum erro OneDrive/acesso negado.
Proxy ECONNREFUSED em testes de falha/offline não representa backend real validado.
Formatação final detectou apenas finais de linha em dois Python alterados; corrigidos
somente nesses arquivos e verificados novamente.

### Status por item (não confundir código funcional com validação real)

| Item | Status | Evidência / detalhe restante |
|---|---|---|
| A1 | PARTIAL | Protocolo, titular, drafts, tema e320px testados; linha do tempo completa do titular ainda não expõe todo histórico de Review. |
| A2 | PARTIAL | APIs/páginas internas reais, filtros, admin, GT, modelos/auditoria funcionais; menu interno ainda reutiliza layout geral, sem bottom-nav interna dedicada; listas limitadas ao recorte500. |
| B | PARTIAL | IDs públicos, painel/foto, filtros/URL, clustering/lista/legenda/export testados; entrega Realtime ao vivo de duas sessões ainda não comprovada. |
| C1 | DONE | Qualidade técnica, SHA/pHash, EXIF antigo, lease distribuído; unit + DEV. Limiares são heurísticos, não calibração científica. |
| C2 | BLOCKED_INPUT | Artefato/ONNX/código prontos, mas faltam20 positivas +20 negativas próprias/licenciadas revisadas; UNCALIBRATED → NEEDS_REVIEW. |
| C4 | PARTIAL | YuNet real carrega; rejeição/flag por caixas e contrato testados; falta medir recall/falsos positivos em fotos autorizadas com rosto. |
| D1 | DONE | Identidade server-side, colisão/lookup restrito DEV, protocolo/refresh Playwright. |
| D2 | DONE | Um ponto, anexação confirmada, contagem distinta, desanexação auditada; DEV + Playwright. Só sugere próprios/publicados; não revela outro titular privado. |
| D3 | PARTIAL | Endereço persistido no upload com local; atribuição manual posterior não refaz reverse geocoding; limite Nominatim1req/s é por processo. |
| D4 | DONE | CSV/GeoJSON allowlist e filtros, Vitest + download Playwright; export do recorte carregado, não de todo o banco. |
| D5 | DONE | Domínio do registry + encaminhamento condicional, sem envio automático; detalhe Playwright. |
| D6 | PARTIAL | Blur forte com margem na derivada, EXIF removido e original intacto; teste controlado não comprova recall de rosto real. |
| D7 | DONE | Aceite explícito/versionado, falha antes do Storage, owner RLS e nova versão testados. |
| D8 | DONE | TaxonomyPanel reutilizado no público/interno, equivalência registry e Playwright. |

### Segurança, integridade e revisão

DEV confirmado mascarado `impm…ggy` em ambos os envs, sem modificá-los. Advisors:
15 INFO de RLS sem policies (tabelas backend-only/fail-closed preexistentes);3 WARN
de anonymous access (consentimentos apenas auth.uid e Event/Risk exigem papel
app_metadata). Não abrir policies para eliminar avisos. Referência oficial:
[Anonymous access policies](https://supabase.com/docs/guides/database/database-advisors?queryGroups=lint&lint=0012_auth_allow_anonymous_sign_ins).
Nenhuma certificação ampla de segurança alegada.

Revisão de correção rastreou upload/consentimento/compensação, fronteira de ownership,
anexação concorrente/revalidação e distinção desanexação × voto. O defeito de voto
operacional foi reproduzido e corrigido filtrando confirm/correct/reject no consumidor,
não apagando a evidência. Formato/tipos/consumidores revisados; nenhum segundo fluxo.
Hashes YuNet/CLIP/encoder ONNX recalculados e iguais aos registrados abaixo.
Peso de origem yolox_s.pth continua SHA256
`f55ded7181e1b0c13285c56e7790b8f0e8f8db590fe4edb37f0b7f345c913a30`.
Nenhum Frozen Test, treino, benchmark científico, detector ou .env alterado/restaurado.

### Gate e ações mínimas para PASS

| Prioridade | OPEN | Ação |
|---|---|---|
| P1 | C2 sem calibração e C4/D6 sem recall real | Fotos próprias/licenciadas, revisão humana e protocolo do porteiro, sem usar datasets científicos. |
| P1 | E2E físico e entrega Realtime não verificados | Executar com duas sessões existentes e telefone HTTPS, sem mudar Auth; registrar evidência. |
| P2 | D3 endereço posterior/múltiplos processos | Enriquecimento explícito após localização e rate limit global antes de múltiplas instâncias. |
| P2 | A1/A2 apresentação/histórico/paginação | Linha do tempo do titular, navegação interna mobile e paginação, reutilizando APIs atuais. |

Para calibração, forneça pasta **fora do repositório e dos datasets científicos**, por
exemplo `C:\Users\felip\Pictures\UrMindPorteiro\positivas` e `negativas`, com ao menos
20 fotos reais de rua/calçada/via/infraestrutura e20 negativas distribuídas entre
interior, selfie/rosto consentido, documento sem PII, print, comida e animal. Informar
autoria/licença e rótulo humano de cada foto; incluir rostos grandes/pequenos consentidos
para C4. Não versionar fotos pessoais. Não executar inferência nessas fotos sem o
registro de origem/escopo. A pergunta sobre fotos já foi enviada; não há resposta ainda.

Teste manual: use os três comandos do roteiro histórico abaixo. Aceite o aviso antes
do envio, envie foto própria clara>=640px+GPS/manual, copie protocolo e recarregue.
Foto escura deve rejeitar sem perder draft. Interior/selfie **não tem rejeição por
cena garantida** até calibração; rosto grande identificado por YuNet pode rejeitar,
mas não é detecção urbana. Reenvio igual rejeita; foto diferente até25m de relato
próprio/publicado oferece anexação. Revisor existente abre `/app/reviews`, confirma
classe humana (consenso/admin conforme regra), atesta privacidade e publica; conferir
foto sanitizada no mapa público e testar despublicação. Desanexar mantém a evidência
e não cria voto Ground Truth. Testar outro visitante, logout, rede lenta e GPS negado.

Commits anteriores: e8853c7(base),10ba477(identidade),ce00127(revisão),
c3b3da3(interno),a979475(mapa),16c9172(porteiro). Este fechamento entra em
`feat(melhorias): consentimento, evidencias adicionais e verificacao final`, sem push.
Os cinco relatórios/imagens IRD/RDD untracked preexistentes ficam fora do commit.
Skills usadas no fechamento: karpathy-guidelines, no-duplicate-files,
superpowers:systematic-debugging, correction-review, example-skills:frontend-design,
supabase:supabase, urmind-verification-gate; leitura adversarial focada, sem subagentes.
Conector Supabase: SELECT e advisors; integração pelo cliente existente. Estado final
honesto: blocos funcionais integrados, não entrega100% nem autorização de treinamento.

## Bloco 5 — referências e porteiro (24/09/2026)

- C1: pHash DCT 64 bits, comparação Postgres por proprietário/recebimento nos últimos
  30 dias, limite persistido; duplicata prévia recusada antes do Storage. Aviso EXIF
  antigo não bloqueia nem inventa fuso. Migration `0026_photo_admission_leases` serializa
  envios por proprietário entre processos, com token e expiração, sem lock durante I/O.
  Upgrade/downgrade/upgrade DEV passaram. Sessões independentes, token incorreto,
  expiração, RLS e grants foram exercitados no DEV.
- C2: OpenCLIP ViT-B-32/laion2b_s34b_b79k, revisão
  `1a25a446712ba5ee05982a381eed697ef9b435cf`, MIT declarada no model card imutável.
  Fonte safetensors: 605143316 bytes, SHA256
  `ac4f8c4b88af6d963118cbf40ad93176d092abbedfcb752601ae1866352656e6`.
  ONNX somente encoder: 351613650 bytes, SHA256
  `7ed89580c61104c11a0d6ac277fde4b106258b1938d643dbaa53e65b2774b5b5`.
  Textos ingleses/embeddings, pré-processamento, parity e fonte versionados em
  `datasets/metadata/photo_reference_export.json`. Parity absoluta/relativa 1e-4
  passou em entrada sintética; NÃO é avaliação científica ou acurácia de cena.
  Encoder CPU aquecido: 28–31 ms (5 execuções); chamada fria completa: 1,064 s.
  Calibração **BLOCKED_INPUT**: zero fotos próprias revisadas, não 20+20.
  Modelo executa, mas não rejeita/aceita automaticamente por cena sem calibração;
  estado UNCAlIBRATED/NEEDS_REVIEW explícito. Model card adverte contra implantação
  sem testes específicos; não foi apresentado como validado para produção.
- C4/D6: YuNet OpenCV Zoo, revisão `26cc381e4d2594bb9f47a26eb8fd96c94a13660d`,
  licença do arquivo **MIT**, 232589 bytes, SHA256
  `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4`.
  Rosto dominante rejeita; pequeno marca revisão; derivada recebe blur com margem.
  Original preservado; atestado humano continua obrigatório inclusive para placas.
  Sem artefato, NOT_VERIFIED explícito e atestado humano, sem afirmar blur automático.
  Carregamento OpenCV corrigido pela API de bytes para caminhos Unicode Windows.
- Ambos registrados no DEV como REFERENCE, sem promoted_at/shadow autorizado:
  YuNet `76eb8ab3-0145-40cb-bc32-a7ee6ad608d1`, CLIP
  `dedb1af0-3b5b-4344-a309-0b935de83f9a`, AuditLog register_photo_reference.
  Detector científico arquivado não foi restaurado.
- Aquisição reutiliza `acquire_registered.py --reference-model <id> --execute`;
  export: `--export-reference-scene --execute`. Sem sobrescrita implícita; orçamento
  verificado (<40 GB), hash/revisão obrigatórios; runtime não baixa nem hidrata.
- Evidência atual: backend completo 1272 passed / 25 skipped (24 integrações opt-in,
  1 serviço externo); auth/storage 88 passed; DEV lease/pHash/Storage/publicação/política
  4 passed / 20 deselected; Ruff/mypy passaram. Frontend Vitest 46 passed, TypeScript,
  build e Prettier passaram; Playwright 116 passed / 2 E2Es reais skipped (workers=2).
  Uma execução com 10 workers teve timeout de screenshot; reprodução isolada e execução
  completa com 2 workers passaram. Testes controlados de blur não provam recall de rostos.
  Registry intermediário desatualizado foi corrigido pelo gerador oficial.
- OPEN: calibração com fotos próprias, teste real de rosto licenciado e calibração dos
  limiares; calibração e rollout não autorizados por mocks.
- Novo módulo photo_reference: NEW_COMPONENT_JUSTIFIED, responsável por resolução e
  inferência dos artefatos de pré-processamento, separado do Storage e do detector YOLOX.
  Acquisition registry/Storage/porteiro/admin: EXTENDED_EXISTING_COMPONENT.

Fontes verificadas: [CLIP model card imutável](https://huggingface.co/laion/CLIP-ViT-B-32-laion2B-s34B-b79K/blob/1a25a446712ba5ee05982a381eed697ef9b435cf/README.md),
[YuNet MIT](https://github.com/opencv/opencv_zoo/blob/26cc381e4d2594bb9f47a26eb8fd96c94a13660d/models/face_detection_yunet/LICENSE).

## Bloco 4 — mapa e endereço (24/09/2026)

UrbanMap canônico estendido com filtros de status/família/classe/período UTC,
estado na URL, contador, legenda e ícones por família. Deep link preserva os filtros.
Geolocalização local e direções reutilizam os controles existentes.
Upload consulta Nominatim pelo cliente existente (cache, User-Agent, limite 1 req/s
por processo, timeout 3 s, uma tentativa); guarda endereço e provenance na Capture.
Falha externa não impede relato. Correção humana de ponto não reutiliza endereço antigo.
Público recebe somente rua de relato publicado, nunca metadados privados da Capture.

Evidências: backend focado auth/public 134 passed; integração DEV Storage/publicação/
marcador 2 passed / 21 deselected; Vitest 46 passed; Playwright público 40 passed;
mypy, Ruff, TypeScript, build, Prettier e diff check passaram.
Reuso: EXTENDED_EXISTING_COMPONENT para mapa, repositórios, upload e provider.
Limites OPEN: rate limit Nominatim distribuído entre processos; endereço após marcação
manual posterior ao envio; prova completa em celular físico. Blocos 5–6 ainda pendentes.

## Bloco 3 — operação interna persistida (continuação 24/09/2026)

- `0025_operational_configuration`: upgrade/downgrade/upgrade no DEV comprovados. Sem alteração de .env/Auth. Defaults apenas para linha ausente; erro/contrato inválido não libera upload. Escrita admin, leitura interna reviewer/admin, AuditLog atômico, RLS e grants sem acesso direto anon/authenticated. Camada pública genérica permanece desligada por default; configuração passa a ser canônica no banco, não na antiga flag de ambiente.
- Administração agora salva limiares técnicos; navegador lê subconjunto público e servidor revalida. Modelos somente leitura com classificação canônica; auditoria paginada/filtrada mostra envelope, não actor/payload privado.
- Painel usa contagens reais de Capture e motivos de rejeição; dia em UTC explicitado. Fila inclui Capture sem Event/localização; detalhe reutiliza CaptureReviewPanel. Ground Truth reutiliza consenso/adjudicação e exportador tabular; exige snapshot persistido anterior ao início da revisão, não inventa features ou autorização de treino.
- Endpoint de publicação resolve deep link independentemente da lista em cache; falha reproduzida no Playwright e corrigida. Configuração salva é cancelada na saída/troca de sessão na UI.
- Evidências: focused backend 148 passed; DEV política/RLS/contadores/modelos/auditoria/export vazio 1 passed (22 deselected); Playwright interno 20 passed desktop/mobile após correção; Ruff/mypy/build/TypeScript passaram. Full backend anterior à última extensão: 1258 passed / 24 skipped. Nova regressão final obrigatória permanece pendente.
- Limites ainda OPEN: filtros completos da fila, detalhe por rota própria, prova de export não vazio com GT real, publicação/Realtime integrada sem mocks e blocos 4–6. Não é declaração de A2 integralmente concluída.
- Classificação no-duplicate: repositórios, CoreService, APIs, upload e mapa EXTENDED_EXISTING_COMPONENT; OperationsPage extrai conteúdo operacional da rota admin existente e recebe páginas reais relacionadas, sem frontend paralelo.

## Continuação — identidade, 24/09/2026

### Bloco 2 — revisão humana sem Event de modelo

`0024_capture_reviews` estende a tabela Review existente com capture_id e permite Event ausente; não cria sistema paralelo. Upgrade/downgrade/upgrade DEV passaram antes das fixtures.
APIs de revisão exigem reviewer/admin. Rejeição pode permanecer sem Event. Confirmação com classe humana explícita cria Event origin=human_review, sem Detection, confiança visual ou ModelVersion. Classes candidatas da taxonomia são aceitas apenas no contrato de revisão, não no Worker.
Review/AuditLog, histórico, correção de localização separada, duplicado por protocolo e publicação/despublicação estão conectados no painel do mapa interno. Consenso/adjudicação continuam canônicos; uma revisão isolada não autoriza publicação nem Ground Truth.
DEV: teste de rejeição sem Event, adjudicação posterior, preservação da coordenada original, ausência de Detection e duas Reviews passou. Falha inicial identificou cópia binária de geography que exigia Shapely; corrigida usando a representação EWKT do próprio banco, sem nova dependência.
Playwright desktop/mobile: relato sem modelo → classe humana → adjudicação admin → atestado de privacidade → publicação → foto sanitizada no mapa público passou, com API simulada (não prova E2E real). Full backend **1250 passed / 23 skipped**; frontend **45 passed**; Playwright **110 passed / 2 skipped**; Prettier/build/TypeScript/Ruff/mypy/diff check verdes.
Limites abertos: ainda falta prova integrada completa de Storage/publicação humana real e teste de Realtime desta ação. GT tabular mantém seu gate anterior de evidência/modelo/snapshot: não inventa linhagem para relatos puramente humanos.


- Base anterior preservada no commit `e8853c7` (`feat(base): meus-relatos, sobre, mapa, porteiro técnico`). Os cinco arquivos locais em datasets/reports não entraram.
- Migration `0023_report_identity`: public_id aleatório independente em Capture/Event, protocolo URM sem caracteres ambíguos, backfill, índices únicos e geração server-side com repetição limitada e advisory lock por candidato. Downgrade recusa descartar identidades já emitidas.
- DEV impm…ggy: upgrade → downgrade → upgrade executados; banco sem Capture/Event antes da operação. Nenhum `.env` ou Auth alterado.
- Consulta por protocolo vinculada ao proprietário, rota `#/relato/:protocolo`, protocolo copiável e recuperável após refresh. APIs públicas de Event/imagem resolvem identificador público; DTOs não devolvem UUID interno. Camada pública genérica usa public_id e coordenada arredondada. Links do resultado do proprietário usam event_public_ids.
- Reuso: CaptureRepository, CoreService, rotas existentes, schemas e UrbanMap; nenhuma API de upload, fila ou mapa paralela.
- Teste novo reproduziu UUID/coordenada precisa públicos antes da correção. Backend full: **1248 passed / 22 skipped** (21 DEV opt-in + 1 provider externo). DEV focado: **2 passed / 19 deselected**, Storage/EXIF/marcador e 20 identidades com isolamento por proprietário e constraint de colisão; fixtures limpas.
- Frontend: **45 Vitest passed; 108 Playwright passed / 2 real-E2E skipped**. Quatro falhas iniciais de fixtures que ainda usavam event_ids internos foram corrigidas para event_public_ids, sem fallback público ao UUID interno. Build/TypeScript/Ruff/mypy/diff check passaram.
- Limite: retry aleatório de colisão está implementado; a integração exercitou a constraint de colisão, não forçou a RNG a repetir um candidato. E2E sem modelo → revisão → publicação pertence ao bloco seguinte e ainda não está comprovado. Os blocos 2–6 não são considerados concluídos por esta evidência.


## Checkpoint

Branch `feature/areas-mapa-porteiro`; checkpoint `63d730d` preserva trabalho anterior, não comprova novas funcionalidades. 134 arquivos; 26906 inserções e 1582 exclusões preexistentes. Nenhum push. Inclui alterações preexistentes em contratos Scout, sem alteração nesta tarefa. Excluídos os cinco relatórios/imagens locais em datasets/reports; nenhum raw, peso, .env real, cache ou build adicionado. Busca de padrões de tokens/segredos nos candidatos não encontrou ocorrências; isso não é certificação exaustiva de segurança.

Arquivos incluídos:

```text
.gitignore
README.md
backend/.env.example
backend/alembic/versions/0020_public_image_quota.py
backend/alembic/versions/0021_history_snapshot_retention.py
backend/alembic/versions/0022_capture_report_markers.py
backend/app/api/v1/core.py
backend/app/api/v1/public.py
backend/app/api/v1/routes.py
backend/app/auth.py
backend/app/config.py
backend/app/datasets/taxonomy_candidates.py
backend/app/logging.py
backend/app/main.py
backend/app/ml/detection_dataset.py
backend/app/ml/evaluator.py
backend/app/ml/metrics.py
backend/app/ml/serving.py
backend/app/ml/tabular.py
backend/app/ml/tracking.py
backend/app/ml/training.py
backend/app/ml/yolox_model.py
backend/app/models/core.py
backend/app/observability.py
backend/app/pilot_check.py
backend/app/repositories/core.py
backend/app/schemas/core.py
backend/app/schemas/issue_taxonomy.py
backend/app/schemas/multimodal.py
backend/app/schemas/public.py
backend/app/schemas/scout.py
backend/app/services/context.py
backend/app/services/core.py
backend/app/services/external_sources/__init__.py
backend/app/services/external_sources/__main__.py
backend/app/services/external_sources/auth.py
backend/app/services/external_sources/bulk.py
backend/app/services/external_sources/checks.py
backend/app/services/external_sources/http.py
backend/app/services/external_sources/registry.py
backend/app/services/external_sources/sidra.py
backend/app/services/history.py
backend/app/services/osm_import.py
backend/app/services/photo_ingest.py
backend/app/services/public_view.py
backend/app/services/report.py
backend/app/services/storage.py
backend/app/worker.py
backend/pyproject.toml
backend/tests/architecture/test_no_llm_dependency.py
backend/tests/live/test_external_sources_live.py
backend/tests/test_app_bootstrap.py
backend/tests/test_artifact_registry.py
backend/tests/test_auth_storage.py
backend/tests/test_core_service.py
backend/tests/test_db_integration.py
backend/tests/test_detection_dataset.py
backend/tests/test_event_context.py
backend/tests/test_external_sources.py
backend/tests/test_external_sources_cli.py
backend/tests/test_external_sources_live_checks.py
backend/tests/test_features.py
backend/tests/test_history_phase9.py
backend/tests/test_ird_acquisition.py
backend/tests/test_issue_taxonomy.py
backend/tests/test_ml_metrics.py
backend/tests/test_mlops_local.py
backend/tests/test_model_closure.py
backend/tests/test_observability.py
backend/tests/test_osm_import.py
backend/tests/test_photo_ingest.py
backend/tests/test_pilot_check.py
backend/tests/test_processing_status_contract.py
backend/tests/test_public_api.py
backend/tests/test_report.py
backend/tests/test_scout_multimodal_contracts.py
backend/tests/test_supabase_config.py
backend/tests/test_tabular_phase8.py
backend/tests/test_taxonomy_candidates.py
backend/tests/test_training_engine.py
backend/tests/test_worker_model_resolution.py
backend/tests/test_yolox_evaluator.py
datasets/metadata/artifact_contract.yaml
datasets/metadata/artifact_registry.json
datasets/metadata/storage_budget.yaml
datasets/metadata/taxonomy_v2_dataset_candidates.json
docs/D40_ROOT_CAUSE_ANALYSIS.md
docs/EXTERNAL_INTEGRATIONS.md
docs/FAIR_DEMO_CHECKLIST.md
docs/PROJECT_STATE.md
docs/audits/PHOTO_TO_MAP_DIAGNOSIS_2026-09-24.md
docs/audits/TRAINING_CLEANUP_2026-09-24.md
docs/audits/URMIND_COMPLETE_AUDIT.md
docs/audits/URMIND_REMEDIATION_REPORT.md
docs/audits/V2_DATASET_CLEANUP_2026-09-24.md
frontend/.env.example
frontend/README.md
frontend/package-lock.json
frontend/package.json
frontend/src/App.tsx
frontend/src/components/EventDetail.tsx
frontend/src/components/SignIn.tsx
frontend/src/components/UrbanMap.tsx
frontend/src/components/public/Diagnosis.tsx
frontend/src/domain/contracts.ts
frontend/src/domain/public.test.ts
frontend/src/domain/public.ts
frontend/src/mapConfig.test.ts
frontend/src/mapConfig.ts
frontend/src/pages/CapturePage.tsx
frontend/src/pages/EventsPage.tsx
frontend/src/pages/public/PublicPages.tsx
frontend/src/services/api.ts
frontend/src/services/auth.ts
frontend/src/services/drafts.ts
frontend/src/services/publicApi.test.ts
frontend/src/services/publicApi.ts
frontend/src/styles.css
frontend/tests/app.spec.ts
frontend/tests/approved-real-e2e-images.json
frontend/tests/fixtures.ts
frontend/tests/private-routes.spec.ts
frontend/tests/public.spec.ts
frontend/tests/real-e2e.spec.ts
frontend/tests/responsive.spec.ts
frontend/tests/taxonomy.fixture.json
frontend/vite.config.ts
scripts/datasets/acquire_ird_dashcam.py
scripts/datasets/analyze_rdd2022_geometry.py
scripts/datasets/audit_ird_dashcam.py
scripts/datasets/build_detection_manifests.py
scripts/datasets/make_splits_v2.py
scripts/datasets/prepare_rdd_split_authorization.py
scripts/ml/launch_phase3_memory_efficient.ps1
```

## Diagnóstico anterior às alterações comportamentais

- Rotas canônicas em `frontend/src/App.tsx`: públicas início, captura/registrar, mapa/map, eventos/resultado, transparência, sistema, demo, drafts e processamento por capture_id. Internas `#/app/dashboard`, eventos/detalhe, reviews, ground-truth, mapa e admin. Não há Meus relatos com protocolo nem detalhe de Capture independente de Event.
- Guardas frontend usam `/api/v1/me` e access_token atual; anônimo não ganha papel interno. Backend exige usuário, reviewer/admin conforme rota; claims de app_metadata. Política DEV `captures_mobile_read` é SELECT authenticated com predicado de ownership/papel. Nenhuma alteração de Auth autorizada.
- `CaptureRepository.report_markers` seleciona somente Captures mobile com ponto; autor vê suas próprias, revisor todas. Portanto relatos sem localização não aparecem nessa consulta. `CoreService.capture_markers` não inventa classe/risco sem análise.
- `UrbanMap` é o mapa único MapLibre. Clique cria Popup textual e chama onSelect. Não há clustering nem painel responsivo com foto nesse componente. Lista de acessibilidade está sr-only. App tem lista privada e foto selecionada, separadas do clique do mapa público.
- Foto privada: GET `/captures/{id}/image`, ownership ou reviewer, URL assinada com expiração e no-store. Público: proxy `/public/events/{id}/image`, somente derivada sanitizada com publicação/review vigente. Sem exposição de original autorizada no mapa público.
- Review/consenso/adjudicação, publicação com atestado visual e AuditLog já existem para Events. Captures sem Event não podem ser transformadas em Event artificial para reutilizar essas ações.
- Upload POST `/captures/photo`: admission de tentativas, validação MIME real/bytes/pixels/Pillow, deduplicação owner+SHA, quotas de Captures, Storage privado, commit com compensação. Não verifica ainda nitidez/exposição/resolução mínima/cena/rostos; não há registro de rejeições técnicas separado dos logs.
- `quality` JSONB existente permite metadados versionados do porteiro/consentimento sem nova tabela de estado. Isso não substitui índices/uniqueness necessários para um protocolo curto.
- Nenhum modelo visual ativo deve ser presumido. C2/C4 exigem licença, artefato/checksum e calibração própria antes de aprovação; não serão simulados.

## Direção visual (frontend-design)

Estender a identidade cartográfica existente: verde #123f36, superfície #f5f6f2, branco #ffffff, texto #182c25, âmbar #a16207 e alerta #7f1d1d. Reutilizar tipografia e escalas locais, sem fontes remotas. Mapa e conteúdo de evidência comandam a hierarquia, não novos cards decorativos. Painel de detalhe alinhado à esquerda, lateral no desktop e inferior no celular; controles com foco visível e 44px. Revisão do desenho: evitar segundo mapa e popup HTML; conteúdo em React escapado, usando a mesma seleção para mapa e lista acessível.

## Verificação

## Entrega desta rodada — PARTIAL

Componentes EXTENDED_EXISTING_COMPONENT: UrbanMap, App, PublicPages, drafts/validatePhoto, Storage/validate_image, endpoint de upload, CaptureRepository/CoreService e testes canônicos. Nenhum segundo mapa, frontend, backend, fila ou upload. O único arquivo novo é este relatório obrigatório (NEW_COMPONENT_JUSTIFIED: evidência solicitada).

| Parte | Status | Evidência e limite |
|---|---|---|
| A1 | PARTIAL | `#/meus-relatos`, consulta only_mine+include_unlocated, descrição escapada, data, seleção da foto privada, retorno à localização pendente; `#/sobre` reaproveita transparência/taxonomia e aviso. Faltam protocolo curto, rota por protocolo, timeline completa, miniaturas de cada item e modo escuro. |
| A2 | PARTIAL | Mantidas rotas canônicas `#/app/*`, guardas backend e layout interno. Mapa privado ganhou painel de foto/resultado técnico. Faltam novas ações sobre Capture sem Event, fila completa, auditoria/config e completude dos painéis. Nenhuma tela decorativa criada. |
| B | PARTIAL | UrbanMap ganhou clustering/expansão, geolocalização local, seleção pela lista acessível, painel lateral e bottom sheet; foto pública consultada sem token pelo DTO/proxy existente; privada somente com autorização. Coordenadas no texto/link público arredondadas. Faltam filtros completos, ícones por família, ID público separado, deep link, endereço de Capture e arredondamento consistente no payload público (não apenas UI). |
| C1 | PARTIAL | Servidor e navegador verificam min_side=640, brilho médio 20..240, variância Laplaciano >=25 em amostra até512px. Servidor continua autoritativo e usa executor limitado. Rejeição técnica retorna422 antes do Storage, registra somente métricas/motivos no AuditLog; tentativas que chegam à API contam no admission existente. Faltam pHash, recusa de duplicata (SHA continua idempotente), aviso EXIF antigo, limiares administrativos/calibração própria, contagem de recusas locais e agregação no painel. |
| C2 | BLOCKED | Nenhum CLIP baixado/ativado; faltam artefato/licença/checksum registrados e fotos próprias para calibração. Não há classificação de cena. |
| C4 | BLOCKED | Nenhum detector facial baixado/ativado; faltam artefato/licença/checksum e validação. Não há rejeição automática por rosto. |
| C3 futuro | PARTIAL | Reutilizado contrato `no_supported_detection`, nenhum modelo restaurado; encaixe prévio mantido, não comprova inferência real. |
| D1 | PARTIAL | Protocolo curto ainda não implementado; UUID existente não é apresentado como novo protocolo. |
| D2 | PARTIAL | Anexação/desfazer por proximidade ainda não implementados. |
| D3 | PARTIAL | Contexto de Event existente preservado; reverse geocoding persistido de Capture ainda ausente. Nenhuma geocodificação em massa executada. |
| D4 | PARTIAL | Exportação GT existente preservada; CSV/GeoJSON filtrados de relatos ainda ausentes. |
| D5 | PARTIAL | Responsibility/Action existentes preservados; canal municipal específico não inventado. |
| D6 | BLOCKED | Depende do detector facial validado; sanitização EXIF e atestado humano existentes preservados, não equivalem a blur facial. |
| D7 | PARTIAL | Aviso legível em Sobre; aceite versionado e persistido antes do envio ainda ausente. |
| D8 | PARTIAL | Sobre reutiliza lista da taxonomia da transparência; painel interno dedicado ainda ausente. |

Fotos aprovadas tecnicamente são persistidas com `quality.photo_gate.status=NEEDS_REVIEW`, `technical_status=ACCEPTED`, `scene_status=NOT_VERIFIED`, `face_status=NOT_VERIFIED`. Isso evita certificar uma cena/privacidade não verificadas. Não é detecção de problema. As regras v1 são operacionais provisórias, não calibração científica. Imagem interna suficientemente nítida/clara pode ser aceita para revisão; C2 está desligada.

Nenhuma migration nova: campos versionados em quality e AuditLog já existentes. Head único local e DEV `0022_capture_report_markers`. Nenhum DDL, Auth, .env ou credencial alterado. Nenhum modelo pré-treinado utilizado: nome/licença/SHA256/tamanho = não aplicável. Nenhum treino, Frozen Test, benchmark ou restauração executado.

## Evidências frescas

| Verificação | Nível / comando | Resultado |
|---|---|---|
| Testes novos vermelhos | pytest owner_report_list/report_photo_gate; Playwright mapa/meus-relatos/porteiro | Ausência reproduzida antes das implementações; teste de geometria mobile também falhou antes do bottom sheet. |
| Backend completo | `.venv/Scripts/python.exe -m pytest -q -ra` | **1246 passed /21 skipped /2 warnings**, 137.43s; +7 vs1239, nenhum teste removido. |
| Focados backend | pytest test_auth_storage/test_core_service/test_public_api | **175 passed**; após formatação, core_service **51 passed**. |
| DEV | pytest test_db_integration -k 'photo_report_marker or rls_and_realtime_publication or migrations_ficam or postgis_disponivel or capture_trigger_queue or realtime_rls_reviewer' | **6 passed /14 deselected**; Storage real, EXIF sintético próprio, DB/PostGIS/Queue/RLS; fixtures removidas pelo teste. Não é foto E2E real nem autenticação real de visitante nesse upload. |
| Ambiente | URLs backend/frontend e refs runtime/migration comparadas antes do teste | `impm...ggy`, alinhados; conteúdo .env não impresso nem alterado. |
| Frontend unit | `npm test -- --run` | **45 passed**. |
| Browser | `npx playwright test`, build atualizado | **106 passed /2 skipped**, última rodada54.3s; +6 casos executados (3 novos testes ×2 viewports). API/Auth simuladas; não certifica telefone físico. |
| Tipo/build | `npx tsc --noEmit`; `npm run build` | PASS; warning chunks>500kB permanece (entrada589.60kB, mapa1033.75kB sem gzip). |
| Ruff | check app tests; format --check dos7Python alterados | PASS. |
| mypy | `python -m mypy app` | PASS,72 arquivos. |
| Prettier | `npx prettier --check .` | PASS; somente arquivos alterados formatados. |
| Git | `git diff --check` | PASS; avisos LF/CRLF não são falhas. |
| Alembic | `python -m alembic heads` +SELECT DEV | único `0022_capture_report_markers`; sem migration nova, upgrade/downgrade desta tarefa não aplicáveis. |
| Inspeção visual | screenshot Playwright map-detail desktop/mobile | Primeiro painel excedia viewport; teste acrescentado e corrigido para bottom sheet fixo. |

Skips: os20 testes de `test_db_integration.py` são opt-in na suíte offline. Os6 acima foram executados separadamente. Os14 não reexecutados: history_archive_retains_observations_and_rejects_mutation; public_image_quota_shared_atomic_expiring_and_private; publication_owner_isolation_and_latest_review_gate_real_postgres; public_capture_quota_uses_real_postgres_ordered_window; storage_real_upload_download_signed_url_and_cleanup; storage_compensates_real_db_constraint_failure; history_excludes_backdated_event_inserted_later; legacy_order_is_not_counted_as_trusted_history; commit_order_serializes_concurrent_event_inserts; supabase_auth_real_roles_login_and_jwks; evento_faz_snap_no_trecho_viario_e_aparece_na_busca_por_raio; deteccoes_viram_evento_deduplicado_com_risco_revisao_e_auditoria; ground_truth_consensus_export_with_real_storage; realtime_delivers_event_change_to_reviewer. Também pulado `tests/live/test_external_sources_live.py` (externo opt-in). Browser: `real-e2e.spec.ts` em desktop/mobile (foto real/ambiente opt-in ausentes). SKIPPED != PASSED.

Falhas durante desenvolvimento foram corrigidas: argumento novo no spy de repository; imports Ruff; estado do detalhe inicialmente aplicado ao componente incorreto; teste de browser rodando build antigo; geometria do painel mobile. A primeira tentativa do teste novo não tinha stub de API e foi corrigida antes do red/green válido. Duas execuções Playwright simultâneas iniciais colidiram no diretório de traces (ENOENT); repetidas sequencialmente, sem falha final. Sem erro de acesso negado/arquivo em uso atribuído ao OneDrive nesta rodada.

## Gate de verificação — BLOCKED

| Prioridade | Finding OPEN | Impacto / ação mínima |
|---|---|---|
| P1 | C2/C4 e D6 sem artefatos verificados/calibração | Não anunciar bloqueio de interiores, rostos ou desfoque. Registrar licenças/hashes e validar com fotos próprias antes de ativar. |
| P1 | Escopo A/B/D incompleto conforme tabela | Concluir protocolo, ações Capture/review auditadas, IDs públicos, filtros, consentimento e config sem duplicar o fluxo. Não anunciar entrega completa. |
| P1 | Privacidade do contrato público ainda não atende integralmente o novo requisito | IDs internos UUID ainda existem no contrato legado e coordenadas do payload não foram arredondadas. UI arredondada não é controle de acesso. Manter camada genérica desligada até concluir DTO/ID público. |
| P1 | Sem validação física/realtime de duas sessões no fluxo novo | Executar celular HTTPS com reviewer e dois visitantes; mocks não substituem isolamento realtime ao vivo. |
| P2 | C1 parcial e limiares não calibrados | pHash, aviso EXIF, configuração auditada, taxas de falsa rejeição e métricas agregadas ainda pendentes. |
| P2 | Original privado/rejeições ao vivo não cobertos integralmente | Novo AuditLog de rejeição comprovado em teste com repositório substituído, não no DEV. Upload válido real DEV comprovado. |
| P2 | Lista limitada a500 registros, sem paginação | Não chamar de histórico completo de grande volume. |

Cobertura estrutural: componentes/produtores/consumidores inspecionados por diff e imports; nenhum novo script registrado ou artefato obrigatório criado (manifests, hashes de modelo e producers não aplicáveis). Registry científico, raw, Frozen Test e peso de origem não alterados. Falhas de Storage/ownership continuam nas regressões existentes; classe e risco não adicionados ao relato não analisado. Cloud-only: testes DEV acima; Auth/Realtime completo e celular permanecem unverified. Nenhuma certificação de segurança ampla emitida.

Skills efetivamente usadas: no-duplicate-files, karpathy-guidelines, superpowers:systematic-debugging, superpowers:test-driven-development, superpowers:verification-before-completion, example-skills:frontend-design, urmind-verification-gate, supabase:supabase, supabase:supabase-postgres-best-practices. Plugin/conector: Supabase execute_sql somente leitura, além de testes DEV por cliente existente. Ferramentas locais de shell/patch/imagem; consulta de documentação oficial [RLS Supabase](https://supabase.com/docs/guides/database/postgres/row-level-security). Nenhum subagente acionado. Skill gate impediu declaração de conclusão por evidência parcial.

## Roteiro manual no celular

Não alterar .env/Auth nem habilitar modelo. Na raiz, abrir três terminais PowerShell:

```powershell
# Terminal1
cd backend
.venv/Scripts/python.exe -m app
```

```powershell
# Terminal2 (Worker continua fail-closed sem modelo autorizado)
cd backend
.venv/Scripts/python.exe -m app.worker
```

```powershell
# Terminal3: configuração somente do processo
cd frontend
$env:VITE_DEV_HTTPS = '1'
npm run dev -- --host 0.0.0.0
```

1. Celular na mesma rede: abrir URL HTTPS da rede exibida pelo Vite. Câmera/GPS exigem certificado confiável e secure context; ignorar aviso TLS nem sempre habilita essas APIs. Se bloqueadas, registrar bloqueio operacional, não inventar localização.
2. `#/registrar`: escolher foto própria nítida >=640px em ambos os lados; escrever descrição; GPS ou confirmar ponto manual. Sem ponto, relato permanece sem marcador e pode receber localização depois.
3. Foto escura/pequena/desfocada deve exibir motivo/dica sem perder descrição. Foto de interior bem iluminada **não será bloqueada por cena**: C2 está BLOCKED. Não interpretar NEEDS_REVIEW como problema detectado.
4. Após upload, refresh em `#/processando/:capture_id`; `#/meus-relatos` mostra os relatos da sessão. Selecionar lista/mapa abre detalhe privado; sem modelo, marcador continua como análise indisponível.
5. Entrar com conta reviewer já existente em `#/login`; abrir `#/app/mapa`, selecionar relato e ver original autorizado. `#/app/reviews` e detalhe de Event mantêm ações existentes; relato sem Event ainda não tem o novo workflow de ações completo.
6. Público: `#/mapa` mostra foto somente após publicação pelo fluxo existente; conferir foto sanitizada, painel lateral/ inferior, link de direções e lista por teclado. Camada genérica continua desligada por padrão. Não há ainda deep link por novo ID público.
7. Conferir visitante B sem acesso aos relatos de A; logout limpa seleção/foto; repetir em320px e aparelho físico. Ainda não executado nesta rodada.

Git: somente checkpoint `63d730d`; implementação/documentação subsequentes permanecem no working tree para revisão. Os cinco arquivos científicos excluídos do checkpoint continuam locais, não apagados. Próximo passo: fechar IDs/DTO público e protocolo/ações de Capture, depois porteiro completo e configuração auditada; não começar treinamento.
