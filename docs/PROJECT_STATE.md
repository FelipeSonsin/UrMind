# Estado operacional do UrMind

## Consolidação local das duas sessões de 26/09/2026 (sem commit, push ou deploy)

Frontend/GPS/mapa/UX/ao vivo (seção "Experiência pública simplificada") e YOLO/inferência
(seção "Sprint de melhoria visual") conferidos juntos na mesma árvore.

- **Sistema visual ativo inalterado:** ONNX `d429bde8…`, perfil `2702eb15`; manifesto
  publicado e worker do navegador idênticos ao HEAD; `OnnxDetector.detect` (Worker) no
  caminho histórico. Fatiamento, TTA, escala 448, limiares alternativos, WBF/NMM/IOS,
  graph capture e IO binding **não** estão ativos: existem só como avaliação
  (`app/ml/sliced_inference.py`, `detect_frame`, `slicedInference.ts`, `frontend/bench/`).
- **Correções:** `docs/ml/V3_GUARDRAILS.md` sem contradição (treino proibido por padrão,
  só com autorização explícita e condições); seleção explícita no relatório da sprint
  (`BEST_SYSTEM=A`, `THRESHOLD_CANDIDATE_REJECTED=true`); `tile_size` só inteiro (Python =
  TS); IRD/RTK marcados como desenvolvimento; RTK descritivo; runtime do navegador marcado
  como transcrição manual (a bancada agora exporta o próprio JSON); latência de CPU medida
  e gravada no relatório; bloco antigo do Worker em `LIVE_DETECTION.md` marcado como
  superado.
- **Gates:** backend 1557 passed / 37 skipped / 0 failed; Ruff (`app` + `tests`) e mypy
  (`app`, 77 arquivos) limpos; Vitest 138 passed; tsc; build; Playwright completo 172
  passed / 2 skipped (E2E real sem foto/GPS revisados, por projeto) com 4 workers;
  `git diff --check` limpo nos arquivos rastreados.
- **Pendente:** teste físico de câmera/GPS; `prettier --check .` acusa 3 arquivos gerados
  já presentes no HEAD (`public/geo/*.geojson` e o manifesto publicado, cujo hash está
  congelado) — não reformatados.

## Sprint de melhoria visual D00–D40 sem troca de modelo — 26/09/2026 (local)

Detalhes, tabela de técnicas e reprodução: `docs/ml/VISUAL_SPRINT_2026-09-26.md`. Sem
commit, push, deploy, promoção, Frozen Test, mudança no manifesto publicado, no Worker ou
no `SHADOW_MODEL_VERSION_ID`.

- **Baseline congelado:** `VS-BASELINE-60f9c748…` (ONNX `d429bde8…`, perfil `2702eb15`),
  `datasets/reports/visual_sprint/baseline_freeze.json`.
- **Conjuntos DEVELOPMENT_ONLY** (regras gravadas antes das métricas):
  `datasets/metadata/visual_sprint_dev_sets.json` — IRD Dashcam 4K como CAMERA_DEV
  (tune/check por índice), RTK (Brasil) como HARD_NEGATIVE_DEV, VALIDATION só como guarda.
- **Harness:** `app.ml.camera_dev` reproduz exatamente o baseline registrado (mAP50
  0,159632; F1 0,2804) e tem paridade exata com o `OnnxDetector`.
- **Resultado:** FULL/TILED/HYBRID × NMS/IOS/NMM/WBF, limiar próprio nos tiles, limiares
  por classe, NMS, TTA (espelho, escala 448) e escala 448 isolada: **nenhuma** melhora
  recall e F1 juntos na câmera. HYBRID sobe o recall (0,248 → 0,311 no CHECK 1080p) mas
  quadruplica os FP (F1 0,235 → 0,114). Limiar F1-ótimo sobe o F1 (0,250) mas baixa o
  recall. Erro dominante: FN sem caixa (62 %); teto de recall no limiar mínimo 0,32.
  No RTK, 74 dos 100 alarmes fora de dano são D20 sobre regiões `roadPaved` da máscara da
  fonte (descritivo; "paralelepípedo" é interpretação não conferida, não Ground Truth).
- **Seleção (no relatório):** `BEST_SYSTEM=A`, modelo `d429bde8…`, perfil `2702eb15…`,
  `THRESHOLD_CANDIDATE_REJECTED=true` (limiares F1-ótimos baixam o recall e pioram a
  VALIDATION). IRD/RTK só para desenvolvimento: não são avaliação oficial nem prova de
  produção; IRD `human_validated_images=0`; RTK não volta como holdout brasileiro.
- **Runtime (Edge, este notebook):** WebGPU roda na Intel integrada mesmo com
  `high-performance` (RTX ociosa); `session.run` 292 ms, total 309 ms; IO binding e graph
  capture funcionam mas ganham < 3 %; WASM 1.221 ms.
- **Pesos:** `MODEL_WEIGHTS_BOTTLENECK=YES`; fine-tuning **não executado** — sem dado de
  câmera revisado e autorizado para treino. `docs/ml/V3_GUARDRAILS.md` foi revisado em
  26/09/2026: treino por agente proibido por padrão, só com autorização explícita,
  dataset autorizado, labels revisadas, escopo e tempo aprovados; Frozen Test protegido.
- **Código:** `app/ml/sliced_inference.py` + espelho TS (avaliado e rejeitado, sem uso no
  produto), `OnnxDetector.detect_frame` (padrão = caminho histórico), histórico curto de
  confiança no `TemporalTracker`, bancada `frontend/bench/` (só desenvolvimento).
- **Pendente:** teste com câmera física (exige pessoa e aparelhos); conjunto de câmera
  brasileiro revisado para treino/holdout.

## Experiência pública simplificada — 26/09/2026 (local, ainda não publicada)

Sem commit, push ou deploy nesta rodada. Nada muda em pesos, treinamento, Frozen Test,
`.env`, credenciais, RLS ou publicação.

- **GPS da foto tirada agora:** "Tirar foto", "Abrir câmera" e "Capturar e registrar"
  (ao vivo) leem o GPS do aparelho automaticamente. Uma posição guardada só é
  reaproveitada até 30 s (`FRESH_MS`); nenhuma leitura com mais de 60 s é aceita
  (`MAX_FIX_AGE_MS`, também no fallback de baixa precisão, que antes aceitava 5 min); e
  a posição só vale para a foto se foi lida a até 2 min do instante dela
  (`fixMatchesPhoto`). Precisão, horário, rumo e velocidade seguem para o backend. Trocar
  de foto invalida a resposta pendente do GPS.
- **Galeria:** GPS do EXIF (sem precisão inventada, o servidor relê o original) ou ponto
  no mapa. O GPS atual nunca é atribuído a foto da galeria (o botão "Usar GPS atual para
  esta foto" saiu).
- **Sem campos de latitude/longitude** na interface. Estados: "Obtendo localização…",
  "Localização obtida" (com precisão), "Localização encontrada na foto", "Local marcado no
  mapa", "Precisamos que você confirme onde a foto foi tirada", "Localização
  indisponível". O fallback é visual (tocar no mapa → marcador → "Confirmar localização")
  e tem alternativa por teclado: "Marcar o centro do mapa". Sem localização o relato ainda
  pode ser enviado e fica "Necessita localização" (sem ocorrência geográfica).
- **Relato do autor no mapa:** após o envio, a página do relato seleciona e enquadra o
  ponto (Capture, nunca um Event artificial), com situação, precisão e, depois da análise,
  o trecho de via associado separado do ponto informado. Continua após recarregar.
  Início e Mapa mostram só os relatos da própria sessão (`only_mine`); a equipe vê todos
  apenas na área interna. O mapa público segue mostrando só publicações.
- **Situação do relato (backend `capture_markers`):** acrescentados `processing` (etapas
  do Worker em andamento; `queued` continua "Recebido") e `published` (confirmado **e**
  publicado pela regra `_PUBLISHED`). Ponto ajustado à via, distância e nome da via saem
  só para relatos analisados e só na visão do dono/revisor. Deploy: o frontend novo aceita
  o backend antigo; o backend novo com o frontend antigo quebraria a lista de relatos até
  o Vercel publicar (publicar juntos, Vercel primeiro).
- **Filtros do mapa:** opções reais (vocabulário completo de situação do tipo de ponto
  exibido; famílias e classes presentes ou emitíveis pelo modelo, com a classe presa à
  família; família em português), datas digitadas como dd/mm/aaaa no dia civil local,
  "X pontos visíveis" com singular, estado vazio com "Limpar filtros", filtros ocultos
  quando não há pontos. Mapa operacional com "Tudo / Meus relatos / Ocorrências
  publicadas".
- **Nomes no mapa:** o estilo do OpenFreeMap usa `name_en` ("New Fribourg", "Federal
  District"); passa a usar o nome local do OSM (`name`). O `name:pt` dos blocos não serve
  (rótulos da Wikidata como "Liberdade (bairro de São Paulo)"). As 27 capitais da visão
  nacional são conferidas por teste contra a malha de UFs do IBGE; Rio de Janeiro caía na
  água da baía da malha simplificada e foi corrigido para o Centro. Atribuição
  OpenFreeMap/OpenStreetMap preservada.
- **Início:** sem a barra técnica (API, banco, detector, Scout, trechos) e sem Scout;
  chamada para registrar, "Seus relatos" com situação e mapa das ocorrências publicadas.
  O diagnóstico técnico continua em `#/system`, fora da navegação; `#/live` leva à
  detecção ao vivo. O componente do Scout foi removido do frontend ativo (histórico no Git).
- **Detecção ao vivo:** ver `docs/LIVE_DETECTION.md` (FPS da câmera desacoplado, ritmo
  adaptativo, "Detalhes técnicos" recolhido).
- **Gates desta rodada:** Vitest 137 (árvore inteira, incluindo testes de outra sessão);
  tsc; build; Prettier; Playwright 172 passed / 2 skipped com 4 workers (E2E real exige
  foto/GPS revisados; com 10 workers e a máquina carregada houve 4 timeouts que passam
  isolados); backend 1520 passed / 37 skipped; integração DEV dos marcadores 4 passed;
  Ruff; mypy dos arquivos tocados; `git diff --check`.
- **Limitações:** câmera/GPS físicos não testados (PHYSICAL_TEST_PENDING, matriz em
  `FAIR_DEMO_CHECKLIST.md`); as caixas ao vivo refletem o quadro de uma latência atrás;
  dispositivos que não entregam posição recente caem no mapa.

## Remediação da auditoria A25 — 26/09/2026 (vigente)

Detalhes e evidências: `docs/audits/URMIND_REMEDIATION_REPORT.md` (topo) e
`docs/audits/URMIND_COMPLETE_AUDIT.md`. Ações humanas: `docs/USER_ACTIONS_PENDING.md`.

- **Publicado:** Vercel → Render conectado (base `/api/v1` e CORS corrigidos pelo
  usuário); o código publicado ainda é `e57db61`, sem a detecção ao vivo.
- **Gates locais:** backend 1504 passed / 37 skipped / 0 failed; integração DEV 33 passed
  (3 que criam usuários Auth excluídos); mypy e Ruff limpos; Vitest; tsc; build; Playwright.
- **Banco (DEV, head `0034_runtime_search_path`):** 0032 (demo sem acesso cliente), 0033
  (role `urmind_runtime` de menor privilégio, 24 policies) e 0034 (`search_path` com
  `extensions`) aplicadas. `backend/.env` usa `urmind_runtime`; migrations, registro de
  modelo e manutenção usam `Database(role="admin")`/`MIGRATION_DATABASE_URL`. Capture órfã
  auditada e removida; 12.808 RoadSegments reais (OSM FECAP 3 km), snap verificado.
- **XGBoost:** worktree reconciliado num único `tabular.py`; `review_confirmed` ligado ao
  DecisionTrace como estimativa consultiva (`DISABLED` sem modelo promovido). Attain
  preservado como experimento abaixo do baseline. `XGBOOST_OPERATIONAL=NO` (0 rótulos).
- **YOLOX:** `MODEL_STATUS=EXPERIMENTAL_SHADOW` (`a3ff07ea`, ONNX `d429bde8…`), não promovido.
  Navegador e Worker **unificados no perfil `2702eb15`** (autorização shadow própria, com as
  limitações do proprietário); paridade 8/8 em 6 imagens autorizadas
  (`datasets/reports/live_detection_parity_2702eb15.json`). `d2b6e1ab` preservado como
  histórico/rollback documental. ONNX no bucket público `models` (Storage DEV), entregue ao
  build pela `LIVE_MODEL_ONNX_URL`. Não é production ready; sem Frozen Test nem fotos do Brasil.
- **Próxima etapa científica YOLOX (pré-requisitos, sem treino nesta rodada):** holdout
  independente e autorizado (grupos de cena/rota verificados, não abrir o Frozen Test);
  critérios numéricos por classe pré-registrados antes de medir; avaliação externa/Brasil
  (UNIVALI/Urban após revisão humana); novo DatasetVersion com hash; parar de ajustar
  limiares na mesma VALIDATION.
- **Taxonomia:** `PRODUCT_SCOPE=35`; `CURRENT_MODEL_CLASSES=D00,D10,D20,D40`
  (EXPERIMENTAL); `DATA_READY=0` no ciclo v3; `REVIEW_REQUIRED`: 130 casos (UNIVALI 32,
  Urban 50, piloto Noruega 48) + 6 categorias `NEEDS_HUMAN_REVIEW`; `FUTURE_WAVES` 4/8/23
  preservadas. O MVP D00–D40 não depende das 35.
- **Photo gate:** `PHOTO_GATE_STATUS=UNCALIBRATED_FAIL_SAFE` (toda foto fica
  `NEEDS_REVIEW`; publicação exige atestado humano de conteúdo); `PHOTO_GATE_REQUIRED=NO`
  para o piloto privado, `YES` antes de aceite automático/público;
  `PHOTO_GATE_BLOCKER=corpus 20+20 (+5+5 rostos) consentido/licenciado`.
- **Worker:** supervisionado (`scripts/deploy/worker.ps1`, PID registrado, heartbeat,
  parada graciosa, recusa de duplicata) com a role de runtime. **E2E real e dispositivos
  físicos:** pendentes (exigem foto real no local piloto e aparelhos).

## Ponto de operação da detecção ao vivo sem retreino — 25/09/2026

Por decisão do usuário, nenhum treino novo: o modelo mantido é o do run `10h-r1` (`best.pt` `425ed936…`, ONNX `d429bde8…`, hash inalterado). Só o pré e o pós-processamento foram ajustados em VALIDATION (TEST fechado). Perfil publicado: letterbox sem ampliar imagem menor que 640, NMS por classe 0,45 e limiares D00 0,03 / D10 0,2 / D20 0,07 / D40 0,2. F1 macro 0,205 (original) → 0,242 (1º ajuste) → 0,281; mAP50 0,116 → 0,122 → 0,159; mAP50-95 0,036 → 0,051. Ganho confirmado em metades por blocos contíguos e reproduzido pelo ONNX real (mAP50 0,1596) e no navegador real (6/6 detecções iguais ao Worker). Estudo de escala: ampliar prejudica (800 px = 0,064; 960 = 0,029); 448/384 foram melhores só nesta VALIDATION e não foram adotados; pesos EMA piores (0,109). A regra "não ampliar" só afeta imagens menores que 640 px; quadros de câmera 720p/1080p não mudam. D40 cai levemente (F1 0,227 → 0,215). É ajuste de ponto de operação, não calibração de probabilidade; não elimina risco de sobreajuste nem mede uso no Brasil. CLAHE e TTA com espelhamento foram medidos e rejeitados. Perfil de inferência versionado (`inference_profile.sha256` `2702eb15…`), com os dois perfis anteriores guardados para rollback. `OnnxDetector`/Worker aceitam o mesmo perfil e têm paridade exata com o decode do navegador (82/82 em 40 imagens), mas o Worker em DEV ainda resolve `yolox-s-quality-rebuild`: registrar o ONNX `d429bde8…` como ModelVersion exige autorização. **[Correção 26/09/2026: superado. O `.env` aponta para o ModelVersion shadow `a3ff07ea` (ONNX `d429bde8…`), mas registrado com o perfil de rollback `d2b6e1ab` (com ampliação); o navegador usa `2702eb15` (sem ampliação). Os perfis NÃO são iguais; ver seção de 26/09.]** Página ao vivo ganhou camada temporal de apresentação (momentânea × persistente), avisos heurísticos de qualidade e latência p50/p95 por etapa; nenhum ganho temporal foi medido sem vídeos autorizados. XGBoost: o worktree `feat/xgboost-preparation-parallel` tem três runs Attain Low/High `EXPERIMENTAL_CANDIDATE`, não promovidos e sem superar o baseline; nada foi conectado ao runtime. Detalhes em [LIVE_DETECTION.md](LIVE_DETECTION.md) e `datasets/reports/live_detection_calibration_d429bde8a9bd_noupscale.json`.

## Detecção ao vivo no navegador — 25/09/2026

Nova aba `#/deteccao-ao-vivo` (detalhes em [LIVE_DETECTION.md](LIVE_DETECTION.md)): câmera do aparelho, ONNX Runtime Web 1.30.0 num Web Worker (WebGPU validado ou WASM 1 thread), contrato YOLOX idêntico ao `app.ml.serving`, no máximo uma inferência em voo e captura para o rascunho canônico. Estado: **interface e contratos prontos, detecção real bloqueada** — não há ONNX finalizado nem autorizado para distribuição; a página mostra "Modelo de detecção indisponível" e não desenha caixas. CSP do backend recebeu só `'wasm-unsafe-eval'`. O manifesto do navegador é gerado por `python -m app.ml.browser_model` a partir do export oficial, com autorização explícita de uso e distribuição. O proprietário autorizou uso e distribuição do export EXPERIMENTAL do run `10h-r1`. Após o treino terminar (`COMPLETED`, 20:01), `scripts/ml/publish_live_detection_after_training.ps1` exportou `yolox-s-model-v2-d429bde8a9bd` (paridade PyTorch × ONNX aprovada; VALIDATION mAP50 0,108, mAP50-95 0,033 — fraco) e publicou em `frontend/public/models/`. Paridade navegador × backend em 6 imagens de VALIDATION: 14/15 detecções com IoU ≥ 0,97; 1 divergência por empate no NMS. WebGPU, ~306 ms, ~2,1 análises/s. Pendentes: teste com câmera física e envio dos artefatos no deploy. O treino não foi afetado: a publicação só começou depois que ele saiu.

## Run YOLOX-S com limite de 10 horas — 25/09/2026

O usuário limitou o treinamento a 10 horas. O run anterior de 50 épocas foi encerrado após cerca de 2.500 iterações da primeira época; preservou logs e smoke, mas ainda não tinha checkpoint principal. Uma primeira tentativa de contrato reduzido falhou antes de treinar por campo de metadata não aceito; a cópia do contrato falho está no diretório da tentativa. O contrato corrigido `datasets/metadata/yolox_model_experimental_20260925_10h.json` mantém o mesmo TRAIN/VALIDATION, D00/D10/D20/D40, YOLOX-S, pesos oficiais COCO, batch 1, resolução 640×640 e zero workers. Define 6 épocas, warmup 1, no-aug 1 e validação ao final. O launcher tem corte automático aos 35.100 segundos (9h45), incluindo preparação e smoke, antes do limite de 36.000 segundos.

O novo run `yolox-s-rdd4-experimental-20260925-10h-r1` passou no smoke e iniciou treino principal com atualização de pesos confirmada. Prazo absoluto registrado: 26/09/2026 01:06:34 UTC (25/09/2026 22:06:34 em São Paulo). Estado e heartbeat: `models/checkpoints/experimental_20260925_rdd4_10h_r1/run_state.json`; log principal: `train.stderr.log` no mesmo diretório. O checkpoint principal será salvo ao fim de cada época; ainda não havia um na primeira verificação. O preflight deste novo run mediu 2.008.862.720 bytes livres após importar PyTorch, acima do piso cauteloso de 2.000.000.000 bytes; a RAM livre durante o treino oscila e continua sendo monitorada no heartbeat. A flag de override permanece registrada por pedido do usuário, mas não foi necessária para passar o piso nesta tentativa. Nenhum TEST/Frozen Test foi aberto e o modelo continua experimental, sem promoção. As seções abaixo registram etapas anteriores.

## Treinamento YOLOX-S experimental em execução — 25/09/2026

Run `yolox-s-rdd4-experimental-20260925`: smoke passou com atualização de pesos e checkpoint; treino principal iniciado a partir dos pesos COCO e atualização do otimizador verificada no primeiro passo. São 23.827 imagens TRAIN e 3.858 VALIDATION, classes D00/D10/D20/D40, 50 épocas, batch 1, 640×640, zero workers e AMP. O usuário autorizou executar com a RAM atual abaixo do piso cauteloso; o piso não foi alterado. Estado e heartbeat: `models/checkpoints/experimental_20260925_rdd4/run_state.json`; log: `train.stderr.log` no mesmo diretório. Sem Frozen Test, promoção ou substituição do Worker. O corpus V3 de 35 categorias permanece DRAFT. As notas abaixo registram estados anteriores. (Atualização 26/09/2026: este run terminou `FAILED`, exit 4294967295.)

## YOLOX-S experimental autorizado — 25/09/2026

O proprietário autorizou iniciar um novo fine-tuning **experimental**, apenas D00/D10/D20/D40. Foi preparado um contrato V1 derivado de quatro classes, identificado internamente como `yolox-s-model-v2`, com 23.827 imagens TRAIN RDD2022, 3.858 VALIDATION, três imagens ambíguas excluídas, pesos oficiais COCO, 50 épocas e perfil batch 1/zero workers. O relatório de derivação e o contrato estão em `datasets/reports/detection_experimental_20260925.json` e `datasets/metadata/yolox_model_experimental_20260925.json`. Nenhum modelo será promovido ou usado no Worker por este run. O Frozen Test segue fechado; as 35 categorias do produto não foram reduzidas.

O contrato/loader passou na checagem e o launcher sequencial de smoke mais treino principal está pronto. **Ainda não houve optimizer.step:** as medições após PyTorch continuam abaixo do piso cauteloso do projeto de 2.000.000.000 bytes; o valor mais recente está em `datasets/reports/ml_preparation_state.json`. Retomar do preflight após o usuário salvar o trabalho e fechar aplicativos não essenciais. Não encerrar o Worker ou baixar o piso apenas para iniciar.

O conversor COCO V3 foi reforcado: recusa placeholders de grupo, exige evidencia de grupo e auditoria de quase duplicatas marcadas VERIFIED, e impede que o mesmo grupo/cluster atravesse TRAIN e VALIDATION. Isso nao certifica os grupos atuais; nenhuma imagem foi convertida.

## Checkpoint de preparacao sem treinamento ? 25/09/2026

O usuario proibiu qualquer aprendizado nesta execucao. Nenhum smoke, backward, optimizer.step ou fit foi executado. Cinco decisoes aprovadas cobrem D00/D10/D20/D40, mas todos os grupos de cena permanecem nao confirmados; tres ambiguidades foram excluidas. Proveniencia/licenca RDD e origem oficial do checkpoint COCO foram verificadas. `urmind-urban-vision-v3-DRAFT` continua sem classes, corpus, splits ou holdout autorizados. O trainer atual ainda e V1/V2, e `--dry-run` chama treino, portanto nao foi usado como preflight. O comando seguro e `backend\.venv\Scripts\python.exe -B scripts\datasets\preflight_training.py --write` na raiz do repositorio. RAM livre apos PyTorch no ultimo preflight: abaixo do piso cauteloso de 2 GB; valor pontual em `datasets/reports/ml_preparation_state.json`. Dependencias opcionais XGBoost locais instaladas, mas sem fit nem corpus tabular elegivel local verificado. Retomar da resolucao de grupos/duplicatas e autorizacao V3, depois implementar contrato V3 e repetir preflight; pedir autorizacao de smoke apenas apos todos os gates.


## Smoke YOLOX V3 de quatro classes — 25/09/2026

Foi criado, com a ferramenta de revisão existente, um pacote local de oito
imagens RDD TRAIN (Índia/Japão, D00/D10/D20/D40) com identidade e hashes
verificados. O usuário entregou oito decisões; o CLI validou e importou cinco
aprovações e três ambiguidades (India D10/D20/D40). Todos os grupos constam
`UNCONFIRMED`. Original em `Downloads/annotation_review_decisions.json`, hash
`8cb075492cbbb9a25e868b3b07f8a7726cdadb237dbdb93d5adf8026fc3c40f5`;
cópia e importação ficam na área local ignorada de revisão. A revisão semântica
não concede autorização de treinamento. Urban Community não faz
parte deste smoke e continua pendente. O produto conserva 35 categorias.

O checkpoint `datasets/reports/ml_preparation_state.json` separa
`SOFTWARE_READY`, `SMOKE_DATA_READY`, `SMOKE_PASSED` e `LONG_TRAINING_READY`.
Todos continuam falsos. Histórico TEST V1 preservado, não aberto; novo holdout
V3 ausente. O trainer/loader/evaluator/ONNX ainda não possuem contrato V3
aprovado, portanto não há comando legítimo de treinamento principal.
Preflights mediram RAM livre entre 0,407–2,861 GB antes e 0,297–2,306 GB
após importar PyTorch; o checkpoint contém a medição mais recente e a condição
do piso cauteloso de 2 GB. Smoke: zero iterações, sem pico
de RAM/VRAM de treinamento medido. Nenhum aplicativo foi fechado pelo agente.
O download de streaming do release oficial YOLOX-S 0.1.1rc0 coincidiu com o
SHA-256 local `f55ded7181e1b0c13285c56e7790b8f0e8f8db590fe4edb37f0b7f345c913a30`;
nenhum peso foi substituído. Fonte Figshare RDD v1, licença CC BY 4.0 e MD5
oficial foram verificados; grupo e autorização V3 continuam abertos.

## Retomada do preflight local — 25/09/2026

`datasets/reports/ml_preparation_state.json` foi atualizado após nova medição.
RAM total 16,87 GB; livre antes/depois de importar PyTorch variou de
~1,68/1,20 GB a ~1,30/0,90 GB entre duas medições.
O processo de preflight cresceu de ~23 para ~512 MB RSS. RTX 4050 Laptop:
6.141 MiB totais, ~4.917 MiB livres no `nvidia-smi`; PyTorch não reservou VRAM.
O piso cauteloso de 2 GB não foi reduzido. Solicitado apenas fechar aplicativos
não essenciais após salvar o trabalho; nenhum processo foi encerrado pelo agente.

O trainer agora entrega o contrato selecionado à fábrica da cabeça YOLOX,
vincula o checkpoint à ordem de classes desse contrato e aceita batch de
VALIDATION independente. Testes focais passaram. Isso é preparação de interface:
o loader, o avaliador, o export ONNX e o Worker ainda precisam do contrato V3
aprovado e paridade; `TRAINER_V3_STATUS=PARTIAL`, não há treino V3 elegível.
O pacote HTML de 115.289.879 bytes permanece com o SHA-256 pinado correto.
Sem decisões humanas, autorização nova, split V3 ou Frozen Test aberto.

## Preparação técnica de ML — 25/09/2026

Na branch local `ml/urmind-training-prep`, a execução vinculou evidências de
release a 80 das 130 propostas de revisão, corrigiu validação/importação do
pacote HTML pinado e gerou `urmind-urban-vision-v3-DRAFT` com 130 candidatos em
quarentena. Zero decisões humanas, zero classes autorizadas, nenhum split final.
Conversor COCO com recusa de supervisão parcial e preflight executável têm
testes focais. Checkpoint para retomar: `datasets/reports/ml_preparation_state.json`.

GPU CUDA e YOLOX estão presentes, mas RAM disponível <1 GB na medição e
`validate_readiness` exige split autorizado. Smoke YOLOX: zero iterações.
Treinamento principal, XGBoost, Frozen Test, promoção, deploy e push não foram
executados. O extra `tabular-ml` foi fixado para instalação local futura; labels
de severidade, risco e prioridade continuam pendentes. O loader/contrato atual
suporta apenas quatro classes V1 e precisa de adaptação após decisão da ordem
V3. Evidência e ações por etapa: `docs/ml/DATA_READINESS.md`.

## Plano de liberação de dados — 25/09/2026

Preparação executável do próximo ciclo em `docs/ml/DATA_READINESS.md`, com três
relatórios gerados em `datasets/reports/`: `training_class_plan.json`,
`review_summary.json`, `missing_data_plan.json`. Produtor existente estendido;
pacote de 130 propostas reaproveitado e validado por hashes, sem nova seleção.

Taxonomia v3 mantida com 35 categorias: quatro candidatas a detector, cinco atributos
contextuais, três categorias de revisão e 23 sem dados prontos. Nenhuma classe
aprovada para novo treino; ordem da cabeça vazia. Ondas 4/8/23, mantendo todo o escopo.
29 planos de aquisição individuais, sem download ou autorização automática.
130 casos disponíveis para inspeção, zero decisões humanas. Registro histórico de
release não é verificação de direitos por imagem: 80 precisam vincular a evidência
existente ao novo ciclo; 50 Urban precisam também resolver origem/direitos e parent antigo.

YOLOX DATA/CLASS/HOLDOUT gates permanecem abertos. Próxima ação humana: direitos e
revisão dos lotes. Próxima ação técnica: validar exports, reconciliar linhagem pelo
produtor e atualizar gates somente com evidência real. Nenhum treino/deploy/push.

## Escopo completo preparado — 25/09/2026

Taxonomia preservada: **urmind-issue-taxonomy-v3, 35 categorias**. Não reduzida
às quatro classes históricas nem a lote de nove. Matriz executável e formulários
por categoria: `datasets/reports/taxonomy_coverage.json`; decisões, fontes,
contagens e ações completas: `datasets/STATUS.md`, seção de escopo completo.
`REQUESTED_PRODUCT_SCOPE=35`; `TRAINABLE_CLASS_SET=[]` e
`VALIDATED_MODEL_CAPABILITIES=[]` para novo ciclo. **FULL_REQUESTED_SCOPE_READY=NO**.

Entregues ferramenta offline e pacote local com 130 propostas: 32 UNIVALI,
50 Urban Community e piloto TRAIN 24 Norway D40 + 12 D43 + 12 D44.
`datasets/processed/annotation_review/review_1b00e44bcd58.html` fica local/ignorado.
Nenhuma decisão humana preenchida. Urban tem parent hash antigo; aprovação bloqueada
até reconciliação. Preservadas 461 D40 Norway, inclusive 430 pequenas.
Recontagem XML TRAIN encontrou D43=310/D44=3195; candidatos, sem mapeamento aprovado.

Renderer genérico endurecido: prosa livre não vira evidência; referências por campo,
literal relatado separado de inferência e regras condicionais. Contrato público exige
linhagem de avaliação e mantém desconhecidos. Não demonstra verdade semântica do banco.
Protocolo/formulário tabular separado em
`datasets/annotations/tabular_labeling_protocol.json`; export PIT existente preservado.
Risco/severidade/prioridade/recorrência não usam `review_confirmed` como substituto.

Treino, smoke training, Frozen Test, promoção, deploy, push, `.env`, banco e modelos
ativos não foram executados/alterados. Próximo passo: revisão humana dos lotes,
reconciliação Urban e coleta consentida guiada pela matriz. Readiness YOLOX continua NO.

## Preparação científica YOLOX/XGBoost — 25/09/2026

**VERIFICATION_GATE=BLOCKED; YOLOX_DATA_READY=NO; YOLOX_HOLDOUT_READY=NO;
XGBOOST_LABELS_READY=NO.** Consolidação e evidências atuais em
`datasets/STATUS.md`; aditivo metodológico em `docs/D40_ROOT_CAUSE_ANALYSIS.md`.
Os registros anteriores de treinamento/readiness não são autorização para novo ciclo.

Piloto `review_confirmed` (25/09/2026, noite): gate shadow agora é por artefato
(`datasets/metadata/shadow_authorizations/`); `yolox-s-model-v2` registrado como
`EXPERIMENTAL_SHADOW` só no Urmind DEV (`a3ff07ea…`, não promovido), Worker resolve o
modelo com o perfil `d2b6e1ab…` **[Correção 26/09/2026: esse é o perfil de rollback com ampliação; o navegador passou a `2702eb15…` sem ampliação — perfis divergentes até nova autorização shadow.]** Revisão oculta scores
antes da decisão e exige motivo de rejeição; só erro visual vira negativo. Pipeline
XGBoost completo, mas **0 exemplos elegíveis**: nenhum caso real processado.
Detalhes e bloqueios em `datasets/STATUS.md` (seção XGBoost).

Hashes TRAIN/VALIDATION V1 conferem; recontadas 42.244 caixas geometricamente
válidas nesses manifestos, sem comprovar semântica. D00/D10/D20/D40 têm dados
RDD e aprovação histórica restrita ao MODEL V1. Nenhuma classe demonstrou
autorização completa para novo treinamento. Folhas humanas UNIVALI (32) e
Urban Community (50) continuam integralmente pendentes. Grupo e fingerprint
de imagem não cruzam TRAIN/VALIDATION, mas sessões/near-duplicates e cobertura
de classes ainda impedem afirmar ausência global de leakage.

D40 Norway: 430/461 caixas menores que 32² em área projetada; nenhuma removida.
Essa escala não prova anotação errada. Causalidade alegada no relatório antigo
foi qualificada por aditivo, preservando histórico. Novo split/holdout e critérios
numéricos por classe exigem aprovação; V2/quality gate removidos não recriados.

XGBoost atual tem alvo `review_confirmed`, sem equivalência a risco, severidade
ou prioridade. Allowlist, snapshot pré-revisão e máscaras temporais têm testes;
corpus real, missingness, cobertura histórica e desempenho do detector que gera
features não foram medidos no Supabase nesta rodada. Não usar boxes perfeitas
como substituto de detecções operacionais. Early stopping/calibração e split
temporal devem integrar futuro contrato aprovado; harness segue bloqueado.

Descrições públicas usam avaliação persistida, causas vazias e limitações;
renderer genérico aceita textos livres sem vínculo de evidência por frase.
Gate global de descrições permanece parcial, exigindo guarda antes de admitir
afirmações de causa, medidas físicas, probabilidade de acidente ou urgência.

Verificação nova: 86 testes focados + 298 testes relacionados passaram; Ruff
focado e mypy de três módulos passaram. Fixtures não comprovam corpus real,
paridade ONNX ou E2E cloud. Raw medido por atributos: 36.446.728.071 bytes,
sem placeholders reportados; tetos individuais observados, inclusive exceção
RDD de 14 GB. Relatórios antigos de inventário permanecem históricos.
Sem treinamento/smoke training, acesso ao Frozen Test, inferência, exclusão,
alteração de `.env`, deploy ou push. Próximo passo: adjudicar anotações e
grupos, aprovar holdout independente e auditar export tabular point-in-time.

## Validação territorial operacional — 24/09/2026

No Urmind DEV, a migration `0030_brazil_territory` instalou uma tabela privada com
malha simplificada do Brasil obtida da API oficial do IBGE, fixada por SHA-256.
A geometria original retornou inválida; o importador aplicou `ST_MakeValid` e
`ST_CollectionExtract` antes da gravação e comprovou a validade no PostGIS.
O upload localizado, a definição manual posterior e a correção pelo revisor agora
consultam essa malha antes da gravação da localização; ponto comprovadamente fora
do Brasil retorna 422, e malha indisponível retorna 503. Um ponto próximo da
borda, dentro da incerteza limitada do GPS ou da malha simplificada, é aceito
como `uncertain` e sinalizado para revisão. Os endpoints de marcadores operacionais
e de eventos publicados exigem cobertura espacial da mesma malha. O enquadramento
retangular do MapLibre continua sendo apenas uma ajuda visual, não o gate.

Esta política limita a operação do produto, **não** a seleção científica: imagens,
datasets, provenance e candidatos de treino de outros países continuam preservados
e não são filtrados nos exports científicos. Nenhum treino, avaliação ou Frozen
Test foi executado. A malha simplificada não resolve todos os casos costeiros e
fronteiriços; uma revisão humana deve decidir os casos sinalizados. O E2E físico
com celular e a prontidão científica YOLOX/XGBoost continuam pendentes.

Verificação: testes unitários de admissão e autoria; no DEV, teste com São Paulo
`inside`, Buenos Aires `outside`, e marcadores com Capture temporária de cada lado
mostraram só o ponto brasileiro. As fixtures foram revertidas por transação.
Segurança residual: `DATABASE_POOLER_URL` está autenticando como `postgres` e
`urmind_runtime` não existe no DEV. RLS nega leitura direta a `anon` e
`authenticated`, mas o runtime ainda não está sob menor privilégio. Não foi
alterado `.env`; F-04 permanece OPEN até provisionamento e troca manual da URL.
Regressão desta rodada: backend offline `1312 passed / 36 skipped`; integração
DEV sem criação de usuários Auth `32 passed / 3 deselected`; frontend Vitest
`49 passed`, Playwright `132 passed / 2 skipped`, build/TypeScript, Prettier,
Ruff, mypy e `git diff --check` passaram. Os skips/deselections não são provas
de E2E físico nem dos testes Auth/Realtime excluídos nesta rodada.

## Mobile Brasil/GPS — 24/09/2026, recorte anterior

O mapa operacional agora inicia no enquadramento brasileiro, restringe a navegação
e não mostra pontos fora **desse viewport**. Isso ainda não é uma validação
territorial por polígono: um ponto de país vizinho dentro do retângulo pode
continuar entrando. A malha oficial do IBGE deve ser instalada e a admissão de
novas Captures deve ser testada no backend antes de afirmar `BRAZIL_ONLY=YES`.
Nenhum dado histórico ou científico de fora do Brasil foi descartado.

A câmera continua solicitando GPS automaticamente. Foto da galeria conserva a
leitura EXIF; a posição atual do telefone só é usada depois de confirmação
explícita do usuário. Correção manual confirmada pode superar EXIF sem apagar
a coordenada EXIF original da provenance. A navegação pública principal tem
Início, Registrar, Meus relatos e Mapa; rotas técnicas anteriores permanecem
acessíveis por URL, e a área interna preserva seus controles de papel.

Verificação desta mudança: backend `1304 passed / 34 skipped`; Vitest `49 passed`;
Playwright `132 passed / 2 skipped`; Ruff, mypy, TypeScript/build, Prettier e
`git diff --check` passaram. O E2E físico continua entre os dois skips.
`live-check` observou HTTP 200 de Supabase Auth health, Overpass, Open-Meteo,
GeoSampa, BrasilAPI, ViaCEP, OpenFreeMap e SIDRA; Nominatim não foi sondado
porque o lease global estava ocupado. Isso não prova falha de chave ou do provedor.
Não foi executado treinamento nem alterado `.env`/Frozen Test. Pré-treino segue
bloqueado por revisão/holdout científico e Ground Truth insuficiente.

## Continuação vigente — 24/09/2026, fechamento em verificação

IMPLEMENTATION_STATUS=PARTIAL. Head DEV único `0029_geocoding_coordination`, com
upgrade/downgrade/upgrade verificados; novas tabelas de cache/lease privadas com RLS.
Timeline allowlisted do titular, navegação mobile interna, keyset e export de relatos
em streaming de lotes100 (teste1200), histórico/retry de endereço sem depender do
modelo, métricas agregadas e health observado estendem os componentes existentes.
Auth real e Realtime autorizados: titular/revisor receberam Capture em0,391s;
visitante B não recebeu em5s; publicação humana e limpeza de fixtures comprovadas.
Integração usa imagem sintética e funções canônicas: não é foto real/celular.
Script HTTPS executado com API/Worker e QR externo, health/ready/CSP OK, encerrado
automaticamente. Auth URLs/.env não alterados. CLIP/YuNet `UNCALIBRATED`,
sem inferência real nesta continuação; fotos autorizadas
e inspeção de desfoque ainda faltam. Gates não aprovam treinamento.
Complemento: Models também paginados por cursor; Captures localizadas recebem
`report_context` degradável (clima/SIDRA/via PostGIS) sem depender de Event/modelo.
GT: contagens globais e export NDJSON em lotes de 100 passaram com 1200 Events
sintéticos; `training_authorized=false`, sem DatasetVersion aprovada.
Com Worker real concorrente, corrigida revalidação de publicação para aceitar
atualizações operacionais de Capture sem afrouxar revisão/evidência; teste de fila
conta job ativo ou arquivado. Script da feira recusa Worker preexistente.
Regressão atual: backend 1303 passed/34 skipped; integração DEV 33 passed
com Worker ativo; Vitest 48; Playwright 126 passed/2 skipped;
Ruff/mypy/TypeScript/Prettier/build verdes.
OPEN: filtros/ordenação globais da fila; recorte OSM após confirmar centro; corpus de
calibração e celular físico. Evidências e testes finais
no relatório `docs/audits/AREAS_MAPA_PORTEIRO_DIAGNOSIS_2026-09-24.md`.
Registros abaixo são históricos quando contradizem esta seção.

## Áreas/Mapa/Porteiro — fechamento do bloco 6 (24/09/2026)

Estado vigente acima dos registros históricos abaixo: IMPLEMENTATION_STATUS=PARTIAL;
VERIFICATION_GATE=BLOCKED por calibração/fotos e E2E físico/Realtime não comprovados.
Consentimento versionado/RLS, evidência adicional sem ponto duplicado, desanexação
auditada sem voto Ground Truth, export CSV/GeoJSON filtrado, sugestão condicional,
taxonomia interna e tema do dispositivo implementados. Head único DEV:
`0028_report_evidence_actions`; migrations0027/0028 up/down/up exercitadas com guards.
Backend1276 passed/29 skipped; DEV26 passed/2 deselected (testes que alteram Auth
excluídos); Vitest48; Playwright120 passed/2 skipped; Ruff/mypy/Prettier/TS/build verdes.
Os skips não são aprovação. Integração sintética real de Storage/publicação humana
não é E2E cidadão/celular. Nenhum detector disponível/restaurado; C2 sem calibração
continua NEEDS_REVIEW. OPEN:20+20 fotos autorizadas, recall C4/desfoque, Realtime ao
vivo, telefone HTTPS, endereço após localização manual/limite global Nominatim,
linha do tempo completa do titular/navegação interna dedicada/paginação.
Relatório autoritativo desta rodada:
`docs/audits/AREAS_MAPA_PORTEIRO_DIAGNOSIS_2026-09-24.md` (seção inicial).
.env/Auth/Frozen Test/peso de origem preservados. Treinamentos não executados.

## Porteiro com referências verificadas (24/09/2026)

pHash e aviso EXIF conectados; YuNet verificado para privacidade/blur da derivada.
CLIP exportado ONNX, somente REFERENCE, nunca detector urbano; calibração BLOCKED_INPUT
por falta de 20 positivas + 20 negativas próprias/licenciadas revisadas.
Sem calibração: NEEDS_REVIEW, nunca falsa validação da cena. Nenhum treino executado.
Artefatos locais ignorados no Git; fonte/hash/export documentados no relatório Áreas.
Concorrência pHash protegida por lease distribuído `0026_photo_admission_leases`:
upgrade/downgrade/upgrade DEV passaram; 4 testes DEV focados passaram.
Backend 1272 passed / 25 skipped; Playwright 116 passed / 2 skipped; Ruff/mypy verdes.
Evidência real de recall C4 permanece OPEN. Bloco 6 pendente.

## Mapa filtrável e endereço (24/09/2026)

UrbanMap existente: filtros sincronizados com URL, contador e ícones/legenda.
Endereço aproximado do upload persistido via Nominatim existente; falha não bloqueia.
Backend focado 134 passed; DEV 2 passed; Vitest 46; Playwright público 40.
Sem migration nova neste bloco; head 0025. Porteiro de cena/rostos e bloco 6 OPEN.

## Área interna persistida (24/09/2026)

DEV head `0025_operational_configuration` (up/down/up verificados). Administração com
limiares persistidos/AuditLog/RLS; modelos, auditoria e indicadores consultam APIs reais.
Ground Truth conectado ao export tabular, sem treino autorizado nem features inventadas.
Flag pública genérica: default OFF no banco; .env/Auth preservados.
Playwright interno 20 passed; DEV configuração/RLS 1 passed; full anterior à última
extensão 1258 passed / 24 skipped. Blocos 4–6 e lacunas de A2 seguem OPEN.

## Revisão humana de relatos sem modelo (24/09/2026)

DEV head `0024_capture_reviews`. Review/AuditLog canônicos atendem relatos sem Event;
Event origin=human_review somente por ação humana, sem Detection artificial.
Painel no mapa interno conectado a confirmação, rejeição, duplicação e publicação.
Regressão: backend 1250 passed / 23 skipped; Vitest 45; Playwright 110 / 2 skipped.
Publicação real completa e Realtime desta ação ainda não comprovados; blocos seguintes abertos.

## Continuação Áreas/Mapa/Porteiro — identidade (24/09/2026)

Head DEV: `0023_report_identity`, upgrade/downgrade/upgrade verificados.
Identidade pública independente e protocolo do proprietário implementados; detalhe no relatório AREAS_MAPA_PORTEIRO.
Baseline desta alteração: backend 1248 passed / 22 skipped; DEV focado 2 passed;
Vitest 45 passed; Playwright 108 passed / 2 skipped; Ruff/mypy/TypeScript/build/diff verdes.
Ainda não representa conclusão dos blocos 2–6 nem prova de revisão/publicação real sem modelo.

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
