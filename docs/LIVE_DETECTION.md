# Detecção ao vivo (prévia no navegador)

> **Estado em 26/09/2026 (após autorização do proprietário).** Navegador e Worker usam o
> **mesmo perfil `2702eb15…`** (limiares 0,03/0,2/0,07/0,2, NMS por classe 0,45, sem ampliar
> imagens < 640) para o ONNX `d429bde8…`, somente como SHADOW EXPERIMENTAL no Urmind DEV
> (ModelVersion `a3ff07ea`, não promovido; autorização
> `datasets/metadata/shadow_authorizations/yolox-s-model-v2-d429bde8a9bd-profile-2702eb15.json`).
> Paridade 8/8 em 6 imagens autorizadas: `datasets/reports/live_detection_parity_2702eb15.json`.
> Paridade ≠ qualidade: sem Frozen Test, sem fotos do Brasil, ganho em fotos grandes de
> celular desconhecido, D40 ligeiramente pior, VALIDATION já usada para ajuste. O ONNX está
> no bucket público `models` (Storage DEV, caminho imutável por hash) e chega ao build pela
> `LIVE_MODEL_ONNX_URL` da Vercel; o perfil `d2b6e1ab` fica como histórico/rollback.

Rota `#/deteccao-ao-vivo`, item "Detecção ao vivo" da navegação principal. A câmera é a
do aparelho que abriu o navegador; os quadros são analisados localmente por ONNX
Runtime Web num Web Worker dedicado. Nada é enviado ao servidor até a pessoa tocar em
"Capturar e registrar".

Estado em 25/09/2026, 20:06: **modelo EXPERIMENTAL publicado localmente e funcionando
no navegador.** `yolox-s-model-v2-d429bde8a9bd` (run `10h-r1`, `best.pt` época 5, pesos
`raw`), exportado pelo `app.ml.serving export` com paridade PyTorch × ONNX aprovada
(8 imagens, Δcaixa ≤ 0,0014 px, Δscore ≤ 4,3e-6) e publicado por `app.ml.browser_model`
em `frontend/public/models/` (ONNX 35,9 MB, fora do Git). **Qualidade fraca:** VALIDATION
mAP50 = 0,108, mAP50-95 = 0,033; não é aprovação científica e o Frozen Test não foi aberto.
Sem manifesto publicado a página mostra "Modelo de detecção indisponível" e não desenha
caixas.

### Paridade navegador × `OnnxDetector` (medida em 25/09/2026)

Pelo caminho completo do produto: câmera simulada exibindo a imagem → `<video>` →
quadro → Web Worker → ONNX Runtime Web → decode/NMS → lista na tela; referência:
`OnnxDetector` do backend (CPU) nas mesmas 6 imagens de VALIDATION (`China_Drone_000002…8`,
512×512), limiar 0,25, NMS 0,65.

| Resultado | Valor |
|---|---|
| Detecções da referência reproduzidas | 14 de 15 com IoU 0,970–0,999 e \|Δscore\| ≤ 0,021 |
| Divergência | `China_Drone_000005`: 1 caixa D10 na referência, 2 no navegador (melhor IoU 0,785) |
| Causa da divergência | 8 candidatas D10 quase empatadas (0,408 × 0,397) e duas com IoU 0,663/0,659 contra a principal, a 0,01 do NMS 0,65: a mínima diferença de pixels (conversão de cor do vídeo, bilinear do canvas × `cv2.INTER_LINEAR`) troca a sobrevivente |
| Tolerância documentada | mesma classe, IoU ≥ 0,95 e \|Δscore\| ≤ 0,03 fora de empates de NMS; perto do limiar de NMS a contagem pode diferir em ±1 |
| Provider | WebGPU (Edge, Windows, esta máquina; sem cair para WASM) |
| Medido na tela | câmera ~20 fps (fonte simulada de 20 fps), 2,1–2,2 análises/s, latência 304–317 ms |

Isto usa câmera **simulada** com imagens reais; não é teste de webcam física.

## Arquivos

| Arquivo | Papel |
|---|---|
| `frontend/src/pages/LiveDetectionPage.tsx` | Estado da câmera/modelo, ciclo de inferência, overlay, captura |
| `frontend/src/workers/liveDetection.worker.ts` | Web Worker do navegador: download, checksum, sessão ONNX, pré/pós-processamento |
| `frontend/src/domain/liveDetection.ts` | Contrato puro: manifesto, letterbox, tensor BGR, decode/NMS, mapeamento de tela, controle de fila |
| `frontend/src/services/cameraStream.ts` | `getUserMedia`/`enumerateDevices` compartilhado (também usado por `components/Camera.tsx`) |
| `frontend/src/domain/slicedInference.ts` | Fatiamento TILED/HYBRID e fusões, espelho do backend; **avaliado e rejeitado**, não importado pelo produto (só testes e bancada) |
| `frontend/bench/runtime.html` | Bancada de runtime só para desenvolvimento, fora do build de produção |

## Contrato do modelo (espelho de `backend/app/ml/serving.py`)

- Entrada `images`, `float32 [1,3,H,W]`, **BGR**, 0..255, sem normalização
  (`yolox.data.data_augment.preproc`).
- Letterbox: `r = min(H/h, W/w)`, dimensões truncadas como `int()`, imagem no canto
  **superior esquerdo**, preenchimento 114.
- Saída `output` `[1, N, 5 + C]` já decodificada (`decode_in_inference=True`):
  `cx, cy, w, h` em pixels da entrada, objectness, classes. Não há decode de âncoras no
  navegador; âncoras não são desenhadas.
- `score = objectness × classe`, limiar estrito (`>`), mesma área `+1` de
  `yolox.utils.demo_utils.nms`; depois caixas `/ r`, recortadas ao quadro. NMS aplicado
  uma única vez. Dois modos declarados em `postprocess.nms`:
  - `class_agnostic`: argmax de classe, limiar global, NMS entre todas as classes;
  - `per_class` (`multiclass_nms_class_aware`): cada classe acima do próprio limiar em
    `class_score_thresholds` é candidata e o NMS roda dentro de cada classe.
- Caixas alinhadas aos eixos (`x_min, y_min, x_max, y_max`) no quadro original. Sem OBB,
  sem ângulo.
- Qualquer manifesto com outro pré-processamento é recusado (não se assume que todo
  YOLO é igual).
- `inference_profile.sha256` identifica a inferência inteira (ONNX, classes, entrada,
  saída e pós-processamento), não só os pesos: o mesmo ONNX com outro limiar ou NMS é
  outro perfil. `calibration_sha256` aponta o relatório que justificou os limiares; o
  gerador só aceita calibração presa ao hash do manifesto de VALIDATION realmente usado.
  Perfis anteriores guardados para comparar ou reverter (republicar com o manifesto
  correspondente): `models/serving/live-detection.rollback-class-agnostic-0.25.json`
  (original) e `models/serving/live-detection.rollback-per-class-upscale.json` (1º ajuste).
- `input.letterbox.upscale = false`: imagem menor que a entrada não é ampliada (razão
  ≤ 1, padding 114). Ausente = `preproc` original. Quadros de câmera 720p/1080p são
  maiores que 640 e não mudam; só fotos pequenas mudam de escala.

### Ponto de operação ajustado (25/09/2026)

Sem retreino: só o pós-processamento do ONNX `d429bde8a9bd` foi ajustado em VALIDATION.
É **ajuste de ponto de operação**, não calibração probabilística: o score YOLOX
(objectness × classe) continua não sendo probabilidade de o dano existir. Limiares e NMS
também são escolhas ajustadas aos dados; as conferências abaixo reduzem, mas não
eliminam, o risco de sobreajuste, e a generalização para cenas brasileiras não foi medida.
Evidência completa em `datasets/reports/live_detection_calibration_d429bde8a9bd.json`.

Conferências: metades por hash do caminho (escolhe numa, confere na outra, nos dois
sentidos) e metades por blocos contíguos de 100 e 500 imagens, que mantêm sequências
vizinhas do mesmo lado. Grupos de cena reais não são conhecidos. Em todos os quatro cortes
por bloco o perfil novo supera o antigo em +0,035 a +0,039 de F1 macro. Matching um-para-um
por classe (`app.ml.metrics`), IoU 0,5; F1 macro = média simples das quatro classes;
cache com a saída bruta do ONNX (sem filtro de 0,25), mesmas regras antes e depois.

| | Original | 1º ajuste | **Publicado agora** |
|---|---|---|---|
| Letterbox | amplia | amplia | **não amplia** |
| NMS | agnóstico 0,65 | por classe 0,45 | por classe 0,45 |
| Limiar D00 / D10 / D20 / D40 | 0,25 (todas) | 0,05 / 0,13 / 0,35 / 0,13 | **0,03 / 0,2 / 0,07 / 0,2** |
| F1 macro (VALIDATION) | 0,205 | 0,242 | **0,281** |
| F1 macro China Drone / MotorBike | 0,162 / 0,250 | 0,194 / 0,287 | 0,252 / 0,306 |
| mAP50 / mAP50-95 | 0,116 / 0,036 | 0,122 / 0,036 | **0,159 / 0,051** |

Perfil publicado: `datasets/reports/live_detection_calibration_d429bde8a9bd_noupscale.json`
(perfil `2702eb15…`). Nos quatro cortes por bloco supera o 1º ajuste em +0,034 a +0,045 de
F1. O D40 cai levemente (F1 0,227 → 0,215); as outras três classes sobem. O ONNX publicado
com o `_preprocess(upscale=False)` do backend reproduz a medição (mAP50 0,1596, F1 0,2804).
No navegador real (Chrome, WebGPU, modo "Analisar imagem"), 3 imagens de VALIDATION
deram as mesmas 6 detecções do `OnnxDetector` (caixas iguais ao pixel, score ±0,01).

**Estudo de escala** (PyTorch, `best.pt` raw, VALIDATION inteira, NMS por classe 0,45),
mAP50 por entrada: 960 = 0,029; 800 = 0,064; 640 = 0,122; 640 sem ampliar = 0,159;
512 = 0,166; 448 = 0,185; 384 = 0,182. O modelo erra mais quando a imagem é esticada acima
da escala vista no treino (Japão 600 px, Índia 720 px). Adotada só a regra geral "não
ampliar"; 448/384 foram melhores apenas nesta VALIDATION (China 512 px) e seriam ajuste de
escala específico do dataset. Pesos EMA do mesmo checkpoint também foram medidos e ficaram
piores (mAP50 0,109 a 640): continua `model_state_dict` (raw).

Medidos e rejeitados: realce CLAHE (mAP50 cai para 0,062, fora da distribuição de treino),
TTA com espelhamento (+0,005 de F1 por dobrar a latência), pesos EMA e entradas maiores.
O TRAIN é Índia/Japão/Noruega e a VALIDATION é China: o número mede generalização para outro
país, não uso no Brasil. Republicar: `python -m app.ml.browser_model ... --calibration
datasets/reports/live_detection_calibration_d429bde8a9bd_noupscale.json`.

### Navegador × Worker do backend

`OnnxDetector` (`backend/app/ml/serving.py`) aceita o mesmo perfil (`nms_mode`,
`class_score_thresholds`, `letterbox_upscale`); o Worker o lê de `ModelVersion.metrics.serving`
(`nms`, `class_score_thresholds`, `letterbox_upscale`, `max_detections`,
`inference_profile_sha256`) e grava o hash do
perfil no resultado da captura. Sem esses campos, nada muda. Paridade do decode do
navegador contra o `OnnxDetector.postprocess` real, sobre as mesmas saídas do ONNX de 40
imagens de VALIDATION: 82/82 detecções, mesma classe, |Δscore| ≤ 5×10⁻⁷, |Δcaixa| ≤
0,00004 px. A diferença de pré-processamento (canvas × `cv2`) é a medida na seção de
paridade acima.

**[Superado em 26/09/2026 — ver o cabeçalho deste arquivo]** Histórico de 25/09: o
Worker ainda resolvia o ModelVersion `yolox-s-quality-rebuild` (ONNX `7d91f7f6…`,
agnóstico 0,65, limiar 0,25) e o ONNX `d429bde8…` não estava registrado. Depois da
autorização shadow do proprietário, o Worker passou a resolver o ModelVersion
`a3ff07ea` (ONNX `d429bde8…`) com o mesmo perfil `2702eb15` do navegador; nada foi
promovido.

### Camada temporal, qualidade e desempenho (apresentação)

- **Observações momentâneas × persistentes** (`TemporalTracker`): cada detecção é associada
  à do quadro anterior da mesma classe com IoU ≥ 0,3 e intervalo ≤ 1,5 s. A que reaparece
  vira "persistente"; a primeira aparição é "momentânea" (caixa tracejada). Não remove
  nenhuma detecção bruta, não confirma nada, não cria Event. Encerrada ao pausar, trocar
  de câmera, sair da rota, logout, em imagem avulsa e em troca brusca de cena. Reversível
  pelo controle na página. Sem vídeos autorizados, **nenhum ganho temporal foi medido**.
- **Qualidade da captura** (heurística, no worker, grade 160×160 da imagem já
  redimensionada): pouca luz (< 60), clara demais (> 225), possível desfoque (variância do
  Laplaciano < 150, só com luz útil), movimento (> 30 de diferença média) e resolução
  baixa (< 360 px). Limites conferidos em 96 imagens de VALIDATION: originais têm nitidez
  p5 = 376; borradas σ 2,5 têm p95 = 125; escurecidas têm brilho p50 = 37 contra p5 = 110
  das originais. Só orientam a pessoa; não descartam o quadro nem são rótulos.
- **Desempenho medido na página**: FPS da câmera, análises/s, latência do último quadro,
  latência p50/p95 das últimas 60 análises e tempo de pré-processamento, modelo e
  pós-processamento. Decode + NMS custam < 1 ms (p95 0,27 ms no perfil antigo e 0,87 ms no
  novo, 40 saídas reais); o gargalo é o `session.run`. ONNX, quantização e FP16 não foram
  alterados.

## Autorização de uso e distribuição (25/09/2026)

O proprietário do projeto autorizou nesta sessão ("autorize e resolva tudo que
precisa") o **uso na prévia** e a **distribuição ao navegador** do ONNX exportado do run
`yolox-s-rdd4-experimental-20260925-10h-r1` (contrato
`datasets/metadata/yolox_model_experimental_20260925_10h.json`, classes D00/D10/D20/D40).

Escopo e limites:

- status **EXPERIMENTAL**; não é aprovação científica, promoção, registro no banco nem
  troca do modelo do Worker; Frozen Test continua fechado;
- vale só se o export oficial passar: `best.pt` selecionado em VALIDATION, paridade
  PyTorch × ONNX aprovada e métricas de VALIDATION presentes;
- os bytes publicados ficam copiáveis por qualquer visitante do site;
- não inclui deploy.

Execução: `scripts/ml/publish_live_detection_after_training.ps1` espera o processo de
treino terminar (só consulta a lista de processos a cada 2 min), roda o export, o
gerador `app.ml.browser_model` e o build, e registra tudo em
`models/checkpoints/experimental_20260925_rdd4_10h_r1/live_detection_publish.log`.
Qualquer portão reprovado encerra com `BLOCKED …` no log e nada é publicado.

## Distribuição dos pesos

O navegador precisa dos bytes do modelo: publicar o ONNX **torna-o copiável por qualquer
visitante**. Só publique com autorização explícita de distribuição.

1. Manifesto em endereço fixo `frontend/public/models/live-detection.json` (servido
   como `/models/live-detection.json`). O cliente nunca escolhe URL; o caminho do ONNX
   precisa casar `^/models/<nome>.onnx$` (mesma origem, sem `..`, sem CDN).
2. ONNX em `frontend/public/models/<nome>.onnx`. `*.onnx` ali é ignorado pelo Git; o
   arquivo entra só no deploy. **Desde 26/09/2026 o manifesto é versionado** (fixa o ONNX
   por SHA-256 e tamanho) e o build o entrega de forma reproduzível
   (`frontend/build/liveModel.ts`): cópia local com hash divergente falha o build; sem
   cópia local, o build baixa de `LIVE_MODEL_ONNX_URL` (https, variável de build sem
   prefixo `VITE_`) e confere tamanho e SHA-256; sem peso verificado, o manifesto é
   retirado do `dist` e o app mostra "Modelo de detecção indisponível" (nunca caixas
   falsas). Pendente: hospedar o ONNX em origem controlada e definir
   `LIVE_MODEL_ONNX_URL` na Vercel (decisão/ação do proprietário).
3. Campos exigidos (schema em `browserModelManifestSchema`): `model_version`,
   `scientific_status` (`APPROVED`/`EXPERIMENTAL`/`DEMONSTRATION`; `REJECTED` é
   recusado), `use_authorized`, `distribution_authorized`, `authorization_ref`,
   `onnx.sha256`, `onnx.size_bytes`, contrato de entrada/saída, `class_names` na ordem do
   artefato (canônicas `URMIND_*`), `score_threshold`, `nms_threshold`,
   `max_detections`, `provenance.closure_manifest_sha256` e `contract_sha256`.
4. O manifesto é gerado, nunca escrito à mão, por `backend/app/ml/browser_model.py` a
   partir do registro gravado por `python -m app.ml.serving export`:

   ```
   cd backend
   python -m app.ml.browser_model --manifest ../models/serving/<id>-<sha>.json \
       --status EXPERIMENTAL --authorize-use --authorize-distribution \
       --authorization-ref "<onde está a decisão>" [--closure <closure.json>] [--dry-run]
   ```

   Bloqueia (`BROWSER_MODEL_PUBLISH_BLOCKED`) sem as duas autorizações explícitas, sem
   VALIDATION medida ou paridade PyTorch × ONNX aprovada, com modelo classificado como
   rejeitado, ONNX ou contrato com hash divergente, pré-processamento diferente de
   `yolox.data.data_augment.preproc`, classes/input_size divergentes ou caminho fora do
   projeto. `APPROVED` exige closure com hash canônico do **mesmo checkpoint** e operating
   point lock com decisão de qualidade `APPROVED` (o navegador também recusa `APPROVED`
   sem closure). Limiares vêm do lock; sem lock, `serving_score_threshold` do export e o
   NMS do contrato. A cópia do ONNX é conferida por hash e só então o manifesto é gravado.
   Nunca lê checkpoint nem exporta; não deve rodar enquanto o treino disputar RAM.
5. O worker confere tamanho e SHA-256 antes de criar a sessão, confere nomes de
   entrada/saída e roda uma inferência de prova para validar o formato `[1,N,5+C]`.
   Classe a mais que o manifesto → erro "classe desconhecida", sem caixas.

Somente as classes do artefato aparecem; as 35 categorias do produto não são
reconhecidas automaticamente.

## Execução no navegador

- `onnxruntime-web@1.30.0` (versão fixa). Import `onnxruntime-web/webgpu`; o `.wasm`
  (`ort-wasm-simd-threaded.asyncify.wasm`, 26,8 MB, ~6,7 MB gzip) vem do mesmo pacote
  via `?url`, servido pela própria origem.
- WebGPU quando há adaptador **e** a inferência de prova passa; senão WASM com 1 thread
  (não exige COOP/COEP). `env.wasm.proxy = false`: já estamos num worker dedicado.
- Biblioteca e modelo só carregam ao tocar em "Iniciar detecção"; o worker e o `.wasm`
  ficam fora do precache do PWA e do chunk inicial.
- Uma sessão por aba, batch 1, canvas e tensor reutilizados, `ImageBitmap` e tensores de
  saída liberados a cada quadro; `session.release()` + `terminate()` ao sair.
- **FPS da câmera desacoplado da inferência (26/09/2026).** O `<video>` fica sempre no
  palco e toca no ritmo da câmera; as caixas vêm por um canvas de overlay que só é
  redesenhado quando chega um resultado novo. Antes, durante a análise o palco mostrava o
  quadro analisado (atualizado só na taxa do modelo, 2–5/s) e o vídeo virava miniatura:
  era essa a causa da imagem "travada". O canvas do quadro analisado agora só aparece
  para uma imagem avulsa ("Analisar imagem").
- No máximo **uma** inferência em voo e nenhuma fila. O próximo quadro é pedido ao vídeo
  com `requestVideoFrameCallback` (o mais novo apresentado; sem a API, `currentTime`), e
  `FrameFreshness` impede enviar duas vezes o mesmo `mediaTime`. Quadros intermediários
  são descartados.
- **Ritmo adaptativo** (`AdaptiveCadence`, `domain/liveDetection.ts`): teto de 5
  análises/s (200 ms), modelo ocupado no máximo 75% do tempo; latência maior que a média
  recua na hora (aparelho aquecendo, outra aba pesada), latência menor encurta o intervalo
  no máximo 15% por resultado até o teto. Não há FPS de inferência prometido.
- Cada quadro leva `frame_id`, timestamp, largura e altura; a resposta só é aceita se for
  da execução atual e do quadro em voo. Pausa, troca de câmera, saída da rota e logout
  invalidam respostas pendentes. Pausar tira as caixas da tela (caixas antigas sobre um
  vídeo que continua andando enganariam).
- Consequência declarada: as caixas refletem o quadro de uma latência atrás (centenas de
  ms). Com a câmera parada coincidem; com a câmera em movimento podem ficar ligeiramente
  deslocadas até o próximo resultado.
- Taxas medidas separadamente, em "Detalhes técnicos" (recolhido): FPS da câmera por
  `requestVideoFrameCallback` (sem ele, o valor declarado pelo dispositivo, rotulado
  assim), análises/s, intervalo atual do ritmo, latência do último quadro e p50/p95.
- Medição **simulada** (26/09/2026, Edge headless, câmera de canvas a 15 fps, modelo real
  d429bde8, WebGPU): câmera 14,1 fps; análises 1,4/s; intervalo 489 ms; latência
  p50/p95 371/380 ms; pré·modelo·pós 10·344·0 ms. Não vale como medida de aparelho real.

## Câmera e privacidade

- `getUserMedia` só após o toque em "Iniciar câmera"; `audio: false`; tamanho
  preferido 1920×1080 (`ideal`, nunca `exact`); traseira preferida no modo automático;
  seleção por `deviceId` com lista relida após a permissão; resolução entregue lida do
  `<video>`.
- Erros explicados: sem HTTPS/localhost, navegador sem suporte, permissão negada, câmera
  ausente, ocupada, configuração incompatível, desconexão (`ended`).
- Encerrar, sair da rota, trocar de conta/logout (página remontada por sessão): tracks
  paradas, `srcObject` removido, canvases zerados, resultados pendentes invalidados.
- Página oculta: a câmera é **desligada** e não volta sozinha.
- Nenhum vídeo, áudio ou quadro é gravado ou enviado continuamente.

## Interface (26/09/2026)

A tela mostra só o que a pessoa usa: câmera, seletor de câmera (quando há mais de uma),
Iniciar/Encerrar câmera, Iniciar/Pausar detecção, Capturar e registrar, Analisar imagem,
caixas com o nome da classe e a confiança (0 a 1, explicada como "não é gravidade nem
confirmação"). O estado aparece como "Detecção pronta", "Preparando a detecção…" ou
"Detecção indisponível" com o motivo. Versão, status científico, perfil de inferência,
provedor (WebGPU/WASM), taxas e latências ficam em "Detalhes técnicos", recolhido.

## Capturar e registrar

Grava o quadro do `<video>` que está na tela, na resolução da câmera, **sem** caixas nem
textos (o overlay é outro canvas), com `captured_at` do instante da captura; valida com
`validatePhoto`, salva como rascunho local (`source: pwa_photo`) e abre o registro normal
(`#/capture`). O registro pede na hora o GPS do aparelho (o aquecimento do GPS já começa
na página ao vivo, se houver permissão) e só o aceita se a leitura for de até 2 minutos
do instante da foto; sem permissão ou sem sinal, pede o ponto no mapa. O envio usa o
caminho canônico de Capture; o backend refaz a análise oficial. A prévia nunca cria
Detection nem Event, e não chama clima/histórico/risco.

## Analisar imagem do aparelho

"Analisar imagem" encerra a câmera, decodifica a imagem escolhida (orientação EXIF
aplicada) e envia um único quadro ao mesmo worker ONNX, com o mesmo overlay. É só
prévia visual: nada é enviado, a etiqueta mostra "Imagem do aparelho · prévia sem
localização", não há marcador e o EXIF não é lido como local. "Capturar e registrar" fica desabilitado nesse
modo, porque regravaria a foto como câmera (`pwa_photo`, horário atual, sem EXIF);
registrar uma imagem da galeria continua sendo em `#/registrar`, que preserva o original
para o servidor. Abrir a câmera descarta a prévia. Uma imagem de dataset analisada aqui
não comprova classe, gravidade, localização nem desempenho independente.

## CSP

O backend serve o PWA na mesma origem com CSP. Para o ONNX Runtime Web foi acrescentado
somente `'wasm-unsafe-eval'` a `script-src` (`backend/app/main.py`): permite compilar
WebAssembly, `eval` de JavaScript continua proibido (teste em
`backend/tests/test_app_bootstrap.py`). `worker-src 'self' blob:` já cobria o worker.
Sem COOP/COEP. Um host estático separado (ex.: Vercel) precisa da mesma diretiva se
passar a enviar CSP; hoje o repositório não define CSP para ele.

## Limitações de navegador e desempenho

- Redimensionamento do canvas (bilinear do navegador) não é bit a bit igual ao
  `cv2.INTER_LINEAR`; a tolerância de paridade só pode ser medida com o ONNX real.
- WASM em 1 thread é lento para YOLOX-S 640×640 em CPU fraca; o ritmo se ajusta, mas
  latência de centenas de ms é esperada. Não há medição em aparelho real ainda
  (PHYSICAL_TEST_PENDING); a única medida é a simulada acima.
- WebGPU em workers depende do navegador; Safari/iOS tende a cair em WASM.
- O `.wasm` custa ~6,7 MB comprimidos no primeiro uso, mais o ONNX.
- Com o YOLOX treinando na mesma máquina, não rodar benchmark de inferência contínua.

## Testes

- `frontend/src/domain/liveDetection.test.ts` (Vitest): manifesto/autorização/URL,
  checksum e download incompleto, letterbox paisagem/retrato, tensor BGR, score,
  restauração de coordenadas, NMS, limiar, recorte, classe desconhecida, `contain`,
  espelhamento, devicePixelRatio, fila, respostas atrasadas, taxa medida, erros de câmera,
  ritmo adaptativo (recuo imediato, subida de no máximo 15% por resultado, teto) e quadro
  repetido nunca reenviado.
- `frontend/tests/live-detection.spec.ts` (Playwright, desktop e mobile): rota e
  navegação, sem pedido de câmera antes do toque, sem microfone, permissão negada,
  ocupada, ausente, troca de câmera, encerrar, sair da rota, página oculta, desconexão,
  modelo ausente, download 404, checksum divergente, captura com GPS do instante (com e
  sem permissão) sem upload, e — com o modelo real local, em série — o vídeo continua no
  palco em tamanho cheio e apresenta muito mais quadros do que o modelo analisa no mesmo
  intervalo (a contagem absoluta varia com a carga da máquina e não é critério).

Fixtures provam interface e contratos. **Não** são teste de câmera física nem de
detecção real.

## Sprint de melhoria visual (26/09/2026)

Medido em conjuntos de desenvolvimento de câmera (IRD Dashcam 4K, RTK Brasil) contra o
baseline congelado `VS-BASELINE-60f9c748…`; detalhes em
`docs/ml/VISUAL_SPRINT_2026-09-26.md`. **Nada mudou no perfil publicado.**

- Fatiamento (TILED/HYBRID, tile 640, overlap 20 %, fusões NMS/IOS/NMM/WBF) e TTA
  (espelho, escala 448) foram **rejeitados**: sobem o recall e multiplicam os alarmes
  falsos; CLAHE não foi retestado. O worker do navegador segue com uma vista por quadro.
- O perfil por etapa confirma o gargalo na GPU (`session.run` ~292 ms de ~309 ms no
  iGPU); IO binding e graph capture funcionam, mas ganham < 3 % e não foram adotados.
  Neste notebook o navegador usa a GPU integrada mesmo pedindo `high-performance`.
- `TemporalTracker` passou a guardar um histórico curto de confiança por observação
  (`recentScores`, `meanScore`, 5 amostras). Continua sem filtrar nem confirmar nada.
- `frontend/bench/runtime.html` (fora do build) mede WASM, WebGPU, IO binding, graph
  capture e o custo de uma passada fatiada no navegador real; o resultado pode ser
  exportado pela própria página ("Baixar resultado (JSON)").
- Resultados de câmera vêm de conjuntos só de desenvolvimento (IRD sem revisão humana do
  UrMind; RTK sem revisão registrada): não são avaliação oficial nem prova de produção.

## Pendências

1. Teste físico: webcam do notebook, Android Chrome e iPhone Safari; registrar navegador,
   dispositivo, provider, latência e FPS medidos (exige os aparelhos e uma pessoa).
2. Deploy: `frontend/public/models/` (ONNX + `live-detection.json`) fica fora do Git; um
   deploy a partir do repositório precisa receber esses dois arquivos como artefato,
   senão a aba mostra "Modelo de detecção indisponível".
3. Qualidade: o modelo é fraco (mAP50 0,108); melhorar exige novo treino/dados, fora
   deste escopo. A sprint visual de 26/09 confirmou que só pós-processamento não resolve
   (pesos são o gargalo) e que treino exige dados de câmera revisados e autorizados
   (`docs/ml/V3_GUARDRAILS.md` §1).

Resolvido em 25/09/2026: gerador do manifesto (`backend/tests/test_browser_model.py`,
14 testes), E2E de logout com a câmera aberta, autorização registrada, export e
publicação do modelo EXPERIMENTAL e paridade navegador × backend medida.
