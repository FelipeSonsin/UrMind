# Sprint de melhoria visual D00/D10/D20/D40 — 26/09/2026

Objetivo: o maior ganho possível de detecção na câmera **sem trocar arquitetura, sem
abrir o Frozen Test, sem promover e sem mexer em produção**, priorizando recall/FN sem
destruir precisão, FP e latência. Treino só se os pesos fossem comprovadamente o gargalo,
com teto absoluto de 2 h.

**Resultado em uma linha:** nenhuma técnica sem treino melhorou recall **e** F1 na
câmera; os pesos são o gargalo (o modelo não gera candidato algum para ~70 % dos
objetos, mesmo no limiar mínimo); o fine-tuning curto não foi executado porque não há
dado de domínio de câmera revisado e autorizado para treino (condição exigida pelo
guardrail, §1). Produção, manifesto publicado, Worker e Frozen Test intactos.

**Seleção registrada** (`selection` em `camera_dev_report_20260926.json`):
`BEST_SYSTEM=A`, `SELECTED_MODEL=d429bde8…`, `SELECTED_PROFILE=2702eb15…`,
`THRESHOLD_CANDIDATE_REJECTED=true`, `runtime_change=NONE`, `publishable_candidate=null`.
Os perfis avaliados ficam em `evaluated_candidates` com `not_for_publication=true` e
`status=REJECTED_NOT_SELECTED`.

**Uso dos dados:** IRD e RTK foram usados **somente para desenvolvimento**. Não são
avaliação oficial nem holdout, e os números de câmera abaixo **não provam desempenho em
produção**. O IRD tem `human_validated_images=0` (caixas da fonte, sem revisão humana do
UrMind); o RTK não tem revisão humana registrada e não deve ser reutilizado como holdout
brasileiro independente.

## 1. Baseline congelado

`VS-BASELINE-60f9c748979feddb83d2a65ac49eeccc4fb1f63a61cc8848e03ea18ee80032be`
(`datasets/reports/visual_sprint/baseline_freeze.json`, gerado por
`python -m app.ml.camera_dev baseline`; o ID é o SHA-256 do conteúdo canônico e o comando
recusa sobrescrever com outro conteúdo).

| Item | Valor |
|---|---|
| ONNX | `models/serving/yolox-s-model-v2-d429bde8a9bd.onnx`, SHA-256 `d429bde8…`, 35.855.173 B, opset 17 |
| Checkpoint | `best.pt` época 5 do run `10h-r1`, raw, SHA-256 `425ed936…` |
| Contrato | `datasets/metadata/yolox_model_experimental_20260925_10h.json` (`d39999a2…`) |
| Classes | URMIND_ROAD_D00, D10, D20, D40 (nesta ordem) |
| Pré-processamento | 640×640, BGR 0..255, sem normalização, letterbox canto superior esquerdo, padding 114, **sem ampliar** |
| Perfil | `2702eb15…`: limiares 0,03/0,2/0,07/0,2, NMS por classe 0,45, máx. 100 |
| Métricas registradas (VALIDATION) | mAP50 0,1596; mAP50-95 0,0509; F1 macro 0,2804 |
| Navegador | WebGPU (iGPU Intel), `session.run` p50 292 ms / total p50 309 ms, p95 320 ms (1080p) |
| Worker | `CPUExecutionProvider`; `session.run` nesta máquina (medido pelo `report`, 12 quadros IRD 1080p): vista global p50 45,7 / p95 48,5 ms, tile p50 46,0 / p95 48,9 ms; HYBRID 1080p estimado em ~414 ms/quadro (`latency_cpu_onnxruntime.measured`, remedido a cada execução do `report`) |

Nada disso foi reajustado durante a sprint; tudo abaixo compara contra ele.

## 2. Conjuntos (todos DEVELOPMENT_ONLY)

Definição, razões e regras de decisão **gravadas antes de calcular qualquer métrica do
IRD**: `datasets/metadata/visual_sprint_dev_sets.json`.

| Conjunto | Fonte | Uso | GT |
|---|---|---|---|
| CAMERA_DEV | IRD Dashcam 4K (Iraque), 439 quadros; `tune` = índice ≤ 560 (279), `check` = > 560 (160) | escolher em `tune`, confirmar em `check`; simulado em 4K, 1080p e 720p | caixas YOLO publicadas pela fonte, não revisadas pelo UrMind |
| HARD_NEGATIVE_DEV | RTK (UFSC, Brasil), 701 imagens 352×288 + 104 quadros vazios do IRD | só descrever alarmes pela classe da máscara da fonte sob a caixa | máscara semântica da fonte, sem revisão humana registrada |
| Guarda | VALIDATION RDD2022 (China), 3858 | não piorar o que existia (já viciada a favor do baseline) | XML RDD2022 |

O IRD já estava excluído de treino e de holdout futuro. O RTK passa a estar **exposto ao
desenvolvimento**: não usar depois como avaliação brasileira independente. TEST, Frozen
Test e o holdout UNIVALI não foram lidos.

## 3. Harness e verificação

`backend/app/ml/camera_dev.py` guarda a saída bruta do ONNX por vista (acima de 0,01) e
reproduz qualquer perfil pela **mesma** `select_detections` do `OnnxDetector` (extraída de
`serving.py`, sem mudar o resultado). Verificações:

- paridade exata (diferença 0) entre `OnnxDetector.detect_frame` real e o cache em 3
  imagens × 3 resoluções × 3 modos × 2 fusões, e nas vistas de TTA;
- o cache reproduz o baseline registrado **exatamente**: mAP50 0,159632, mAP50-95
  0,050861, F1 macro 0,2804 (convenção: mAP pelo ranking completo no piso 0,01;
  P/R/F1/FP/FN no ponto de operação);
- espelho TypeScript (`frontend/src/domain/slicedInference.ts`) reproduz o Python num caso
  golden gerado pelo backend (grades e as quatro fusões).

## 4. Onde o modelo erra (baseline, IRD 1080p, 439 quadros)

| | Valor |
|---|---|
| Recall micro / precisão micro / F1 macro | 0,223 / 0,169 / 0,206 (CHECK: 0,248 / 0,188 / 0,235) |
| FN 554 | **PURE_MISS 341** (nenhuma caixa com IoU ≥ 0,1), BAD_BOX 205 (0,1 ≤ IoU < 0,5), WRONG_CLASS 8; SMALL_OBJECT marca 63 |
| FP 784 | BACKGROUND 405, BAD_BOX 335, NEGATIVE_IMAGE 25, WRONG_CLASS 19 |
| Confusão D00/D10/D20/D40 | quase nula: 6 D10→D00, 1 D00→D20, 1 D10→D40, 1 D20→D00, 1 D40→D00 |
| Qualidade (heurística do navegador) | nenhum quadro do IRD marcado como escuro, estourado ou borrado |
| IoU 0,3 em vez de 0,5 (CHECK) | recall 0,248 → 0,379 (D20 0,43 → 0,62): parte do "erro" é convenção de caixa da fonte |

**MAIN_ERROR_MODE = FALSE_NEGATIVE sem caixa (PURE_MISS)**, seguido de localização
(BAD_BOX). Confusão entre classes, sombra e baixa luz não dominam neste conjunto.

**Teto de recall** (CHECK, limiar 0,01 em todas as classes): FULL 0,323 com 930 FP;
HYBRID 0,404 com 4.243 FP. Nenhum pós-processamento passa desse teto.

**Brasil (RTK, descritivo):** 136 caixas em 701 imagens; 100 sem pixel de trinca/buraco
suficiente sob a caixa, das quais **80 sobre regiões da classe `roadPaved`** da máscara
(74 delas D20), 10 sobre `roadAsphalt`, 7 fora da pista e 2 sobre `patchs`. A descrição do
dataset sugere que `roadPaved` seja pavimento de blocos/paralelepípedo, mas isso é
interpretação, não conferida imagem a imagem: é descrição da máscara da fonte, não Ground
Truth nem causa comprovada do alarme. Imagens com pixel de trinca/buraco tocadas por
alguma caixa: 24 de 164 (14,6 %).

**DOMAIN_SHIFT_STATUS = PRESENTE (indício de desenvolvimento):** F1 macro 0,206 no IRD
contra 0,280 na VALIDATION, em parte confundido com convenção de caixa (IoU 0,3); no RTK
os alarmes D20 concentram-se em regiões `roadPaved`.

## 5. Técnicas

Regra de modo (pré-registrada): recall e F1 macro sobem em TUNE **e** CHECK contra FULL na
mesma resolução, nenhuma classe perde mais de 0,03 de F1.

| # | Técnica | Estado | Resultado (CHECK 1080p salvo indicação) | Custo | Motivo |
|---|---|---|---|---|---|
| 5 | TILED 640, overlap 20 % | REJECTED | 4K F1 0,013; 1080p 0,043; 720p 0,096 (baseline 0,235/0,225) | 32/8/6 inferências | recortes na escala nativa ampliam a textura e o modelo alucina |
| 6 | HYBRID (global + tiles) | REJECTED | recall 0,248 → 0,311 (+25 %), FP 346 → 1.411, F1 0,235 → 0,114; 720p: R 0,338, F1 0,152; 4K: R 0,267, F1 0,109 | 9 inferências, ~2,8 s por passada no iGPU | mais recall, mas FP ×4 |
| 7 | Fusão NMS / NMS-IOS / NMM / WBF | TESTED, nenhuma salva o modo | melhor F1 HYBRID 1080p: WBF 0,1147 ≈ NMS 0,1144; IOS/NMM cortam FP (1.411 → 1.056/1.107) mas também recall | — | nenhuma supera FULL |
| 5b | Limiar próprio nos tiles (pós-hoc) | REJECTED | escolhido em TUNE; CHECK: 1080p F1 0,186, 720p F1 0,212 (baseline 0,235/0,225) | 9/7 inferências | falhou na confirmação |
| 8 | Limiares por classe (F1 máximo em TUNE) | TESTED, não selecionado | D00 0,07 / D10 0,2 / D20 0,13 / D40 0,07: F1 0,235 → 0,250, FP 346 → 268, **recall 0,248 → 0,236**; VALIDATION F1 0,280 → 0,267 | 0 | passa a regra F1 pré-registrada, mas reduz recall (prioridade do pedido) e piora a VALIDATION |
| 9 | NMS por classe {0,3; 0,45; 0,55; 0,65} | REJECTED (sem troca) | melhor em TUNE 0,55, não confirmado em CHECK (0,243 < 0,250) | 0 | ganho não se repete |
| 10 | Camada temporal | NOT_MEASURABLE (melhoria de apresentação feita) | acrescentado histórico curto de confiança por observação (`recentScores`, `meanScore`, 5 amostras) | 0 | sem vídeo com GT autorizado não há medida de recall/F1 |
| 11 | Troca de cena / movimento | REUSED | `isSceneChange` + reset do tracker já existentes | 0 | — |
| 12, 15 | Seleção de quadro e detail pass adaptativo | NOT_APPLICABLE | dependiam de um modo fatiado aceito | — | fatiamento rejeitado |
| 13 | CLAHE | NOT RETESTED | rejeitado antes (mAP50 0,159 → 0,062); sem condição nova | — | — |
| 14 | TTA de foto: espelho, escala 448, ambos | REJECTED | recall 0,28–0,33, F1 0,19–0,22 (base 0,248/0,235) | 2–3 inferências | FP sobem mais que TP |
| 14b | Escala 448 no lugar da 640 (pós-hoc) | REJECTED | R 0,248 → 0,261, P 0,188 → 0,222, FP −15 %, F1 micro +0,026, mas **D20 F1 0,316 → 0,154** (também cai em TUNE) | 1 inferência | regressão forte de uma classe; direção promissora para D00/D40, reavaliar em conjunto independente |
| 16, 17 | Câmera × inferência desacopladas, `requestVideoFrameCallback` | FEITO por outra sessão nesta data (`AdaptiveCadence`, `FrameFreshness`) | — | — | — |
| 18 | Perfil por etapa | TESTED | pré 15 ms, `session.run` 292 ms, decode/NMS 0,2 ms: GPU é o gargalo; perfil de kernels indisponível (sem `timestamp-query`) | — | — |
| 19 | WebGPU × WASM | TESTED | WebGPU 309 ms × WASM 1 thread 1.221 ms (4×) | — | já é o padrão com fallback |
| 19b | `powerPreference: high-performance` | NOT_EFFECTIVE | o navegador continuou na Intel gen-12lp; a RTX 4050 não é usada | — | o Windows escolhe a GPU por aplicativo |
| 20 | Graph capture | TESTED, REJECTED | funciona (kernels compatíveis); 309 → 305 ms | — | ganho no ruído |
| 21 | IO binding / GPU I/O | TESTED, REJECTED | 309 → 301 ms | — | ganho no ruído; o custo é a computação |
| 22 | Fallback WASM | PRESERVED | ~0,8 análise/s nesta máquina | — | sem mudança |
| 23 | Hard negatives | MINED (avaliação) | no RTK, alarmes D20 concentram-se em regiões `roadPaved` (descritivo); FPs do IRD categorizados | — | revisão humana antes de qualquer uso em treino |
| 24 | Hard positives | MINED (avaliação) | 341 PURE_MISS no IRD (dev-only, não pode ir a treino) | — | — |

Relatórios: `datasets/reports/visual_sprint/camera_dev_report_20260926.json` (plano
pré-registrado + seleção), `camera_dev_tta_20260926.json` (TTA) e
`browser_runtime_20260926.json` (`SOURCE=MANUAL_BENCH_TRANSCRIPTION`: números copiados à
mão da bancada; a bancada agora exporta o próprio JSON por "Baixar resultado (JSON)").

## 6. Pesos e fine-tuning

`MODEL_WEIGHTS_BOTTLENECK=YES` (regra pré-registrada: PURE_MISS é a maior causa de FN no
CHECK e o recall do CHECK é 0,248 < 0,5; teto de recall 0,32 no limiar mínimo).

`SHORT_FINETUNE_EXECUTED=NO`. Bloqueios, cada um suficiente:

1. **Sem dado de domínio de câmera revisado e autorizado para treino.** O IRD é
   CAMERA_DEV e já estava vetado para treino; UNIVALI é holdout congelado; Urban Community
   tem 50 revisões pendentes; RTK é máscara (não instância) e agora está exposto ao
   desenvolvimento. O pedido exige hard positives/negatives e exemplos de câmera
   revisados: hoje o conjunto compacto só teria amostra do próprio TRAIN RDD — não seria
   adaptação ao domínio de câmera.
2. **Guardrail** `docs/ml/V3_GUARDRAILS.md` (revisado em 26/09/2026 a pedido do
   proprietário): treino por agente é proibido por padrão e só ocorre com autorização
   explícita para aquele run, dataset autorizado, labels revisadas, escopo e tempo
   aprovados. Hoje faltam o dataset de câmera autorizado e as labels revisadas.
3. RAM medida no preflight (somente leitura): 6,3 GB livres após PyTorch — **não** é
   bloqueio agora.

Proposta para quando os bloqueios caírem (não executada): novo run a partir de `best.pt`
(`425ed936…`), `TRAINING_TYPE=CAMERA_DOMAIN_ADAPTATION`, conjunto compacto com câmera
revisada (incluindo pavimento de blocos revisado por pessoa como hard negative e buracos
de câmera como hard positive) + amostra balanceada do TRAIN; cabeça primeiro (15–30 min), depois estágios
finais, teto de 120 min, early stopping curto, avaliação no mesmo harness (A/B/C/D). O
estudo de escala (448) e o fatiamento sugerem treinar também na escala de tile, se o
fatiamento for reavaliado depois.

`OLD_WEIGHTS_RESULT` = seção 4; `NEW_WEIGHTS_RESULT` = não existe.

## 7. Sistemas A/B/C/D

| Sistema | Estado |
|---|---|
| A: pesos atuais + inferência atual | medido (baseline) |
| B: pesos atuais + inferência melhorada | medido; nenhuma variante melhorou recall **e** F1 → B = A |
| C, D: pesos com fine-tuning curto | não existem (fine-tuning bloqueado) |

`BEST_SYSTEM = A` (baseline inalterado). `IMPROVEMENT_SOURCE = NONE`.
`READY_FOR_HOLDOUT_REVIEW = NO`. Nenhum manifesto candidato foi gerado. A escolha é
calculada por `select_system` (critério gravado no próprio relatório: recall **e** F1 de
câmera sobem no CHECK, sem classe perdendo mais de 0,03 de F1 e sem piorar a VALIDATION);
os limiares F1-ótimos foram rejeitados porque baixam o recall (0,248 → 0,236) e pioram a
VALIDATION (F1 0,280 → 0,267).

## 8. Navegador × Worker

Nenhuma mudança de inferência foi aceita, então o contrato não mudou: o Worker e o
navegador continuam no perfil `2702eb15`. O suporte a fatiamento existe só como código
avaliado: `OnnxDetector.detect_frame(slicing=...)` (padrão = caminho histórico) e o espelho
TS, com paridade testada; o worker do navegador **não** foi alterado.

## 9. Pendências e riscos

- Teste com câmera física (webcam, Android Chrome, iPhone Safari): **não executado**; o
  navegador pede permissão de câmera fora da página e exige uma pessoa e aparelhos.
  CAMERA_FPS e as taxas reais de análise continuam sem medida física nesta sprint.
- O IRD é Iraque/dashcam com caixas da fonte não revisadas; o CHECK tem 21 D20 e 13
  negativos (variação alta). Resolução 1080p/720p simulada por redução.
- A escala 448 e o limiar F1-ótimo mostram direções reais (mais precisão; mais recall em
  D00/D40) que só devem ser reavaliadas com critério pré-registrado num conjunto de câmera
  independente e revisado.
- `docs/ml/V3_GUARDRAILS.md` foi revisado em 26/09/2026 (proibição por padrão +
  autorização explícita com condições); a contradição com as autorizações anteriores do
  proprietário está resolvida.

## Reproduzir

```
cd backend
python -m app.ml.camera_dev baseline
python -m app.ml.camera_dev cache --set validation   # e --set rtk, --set ird
python -m app.ml.camera_dev cache-tta
python -m app.ml.camera_dev report --output ../datasets/reports/visual_sprint/camera_dev_report_20260926.json
python -m app.ml.camera_dev tta-report --output ../datasets/reports/visual_sprint/camera_dev_tta_20260926.json
```

Cache em `datasets/reports/cache/visual_sprint/d429bde8a9bd/` (ignorado pelo Git, ~42 MB com as vistas TTA).
Bancada de runtime (só desenvolvimento, fora do build):
`http://127.0.0.1:5173/bench/runtime.html?auto=1&configs=wasm,webgpu,webgpu-io,webgpu-graph`.
