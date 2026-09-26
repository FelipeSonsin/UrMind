# Estado dos datasets

Atualizado em 2026-09-12 — data da última regeneração da seção gerada por
`scripts/datasets/refresh_status.py` (ao final deste arquivo). As seções manuais
abaixo foram acrescentadas depois, cada uma com a própria data, e não foram
regeneradas. O gerador reescreve o arquivo inteiro: não executá-lo sem antes mover
essas seções para `docs/ml/DATA_READINESS.md`.

## Conjuntos de desenvolvimento da sprint visual — 26/09/2026

Registro: `datasets/metadata/visual_sprint_dev_sets.json` (gravado antes das métricas).
Uso exclusivamente de desenvolvimento (inferência do ONNX `d429bde8…`, sem treino):

- **IRD Dashcam (439):** CAMERA_DEV, `tune` = índice ≤ 560, `check` = > 560. Já estava
  excluído de treino e de holdout; continua assim. Caixas da fonte, não revisadas
  (`human_validated_images=0` em `ird_dashcam_candidate_audit.json`): os números de câmera
  são de desenvolvimento, não avaliação oficial nem prova de produção.
- **RTK (701):** HARD_NEGATIVE_DEV; máscaras da fonte (sem revisão humana registrada)
  usadas só para descrever o que está sob cada alarme. **Estado novo: exposto ao
  desenvolvimento** — não usar depois como avaliação brasileira independente.
- **VALIDATION RDD2022 (3858):** guarda de regressão (já usada no ajuste anterior).
- TEST, Frozen Test, EXTERNAL_TEST e holdout UNIVALI **não foram lidos**. Nenhum rótulo,
  split, manifesto ou arquivo bruto foi alterado. Cache derivado (saída bruta do ONNX,
  ~42 MB com as vistas TTA, ignorado pelo Git): `datasets/reports/cache/visual_sprint/d429bde8a9bd/`.

## Run YOLOX-S com limite de 10 horas — 25/09/2026

O usuário limitou o treinamento a 10 horas. O run anterior de 50 épocas foi encerrado após cerca de 2.500 iterações da primeira época; preservou logs e smoke, mas ainda não tinha checkpoint principal. Uma primeira tentativa de contrato reduzido falhou antes de treinar por campo de metadata não aceito; a cópia do contrato falho está no diretório da tentativa. O contrato corrigido `datasets/metadata/yolox_model_experimental_20260925_10h.json` mantém o mesmo TRAIN/VALIDATION, D00/D10/D20/D40, YOLOX-S, pesos oficiais COCO, batch 1, resolução 640×640 e zero workers. Define 6 épocas, warmup 1, no-aug 1 e validação ao final. O launcher tem corte automático aos 35.100 segundos (9h45), incluindo preparação e smoke, antes do limite de 36.000 segundos.

O novo run `yolox-s-rdd4-experimental-20260925-10h-r1` passou no smoke e iniciou treino principal com atualização de pesos confirmada. Prazo absoluto registrado: 26/09/2026 01:06:34 UTC (25/09/2026 22:06:34 em São Paulo). Estado e heartbeat: `models/checkpoints/experimental_20260925_rdd4_10h_r1/run_state.json`; log principal: `train.stderr.log` no mesmo diretório. O checkpoint principal será salvo ao fim de cada época; ainda não havia um na primeira verificação. O preflight deste novo run mediu 2.008.862.720 bytes livres após importar PyTorch, acima do piso cauteloso de 2.000.000.000 bytes; a RAM livre durante o treino oscila e continua sendo monitorada no heartbeat. A flag de override permanece registrada por pedido do usuário, mas não foi necessária para passar o piso nesta tentativa. Nenhum TEST/Frozen Test foi aberto e o modelo continua experimental, sem promoção. As seções abaixo registram etapas anteriores.

Atualização 26/09/2026: o run terminou `COMPLETED` (exit 0, 6 épocas; `best.pt` SHA-256 `425ed936…`); ver `docs/PROJECT_STATE.md`.

## Uso experimental em andamento — 25/09/2026

O run `yolox-s-rdd4-experimental-20260925` está treinando YOLOX-S com o manifesto derivado TRAIN de 23.827 imagens RDD2022, na ordem D00/D10/D20/D40; 3.858 imagens VALIDATION permanecem para acompanhamento. As três imagens ambíguas seguem excluídas. Smoke de uma iteração passou e o treino principal confirmou `optimizer.step` e alteração real de pesos. Pesos oficiais COCO são a inicialização do run principal. O usuário autorizou prosseguir com RAM inferior ao piso cauteloso, registrado no estado do run. Este uso não concede aprovação V3 nem promoção operacional. TEST/Frozen Test permanece fechado. As notas abaixo representam checkpoints anteriores.

Atualização 26/09/2026: este run terminou `FAILED` (`training_exit_code` 4294967295); só `smoke.pt` existe no diretório.

## Corpus experimental RDD2022 de quatro classes — 25/09/2026

Derivado de TRAIN V1 com autorização **experimental** do proprietário nesta sessão: 23.827 imagens, 34.523 caixas D00/D10/D20/D40. Três imagens da Índia com decisão humana ambígua foram excluídas; originais e decisões permaneceram intactos. VALIDATION V1: 3.858 imagens. Manifestos TRAIN, VALIDATION e TEST não compartilham caminho, hash de imagem ou grupo de país; TEST não foi aberto para avaliação. Fonte oficial RDD2022/CC BY 4.0 e histórico V1 estão vinculados no `datasets/reports/detection_experimental_20260925.json`. Isso não é aprovação V3, nem capacidade operacional validada para 35 categorias. Treino aguarda RAM livre de pelo menos 2.000.000.000 bytes após importar PyTorch; o valor recente está em `datasets/reports/ml_preparation_state.json`.

O conversor COCO V3 foi reforcado: recusa placeholders de grupo, exige evidencia de grupo e auditoria de quase duplicatas marcadas VERIFIED, e impede que o mesmo grupo/cluster atravesse TRAIN e VALIDATION. Isso nao certifica os grupos atuais; nenhuma imagem foi convertida.

## Atualizacao sem treinamento — 25/09/2026

Oito decisoes humanas importadas e preservadas; cinco aprovadas com cobertura das quatro classes, tres ambiguas excluidas do subconjunto proposto. Todos os grupos brutos sao `UNCONFIRMED`; nenhum split TRAIN/VALIDATION V3 foi autorizado. O preflight registra status de grupo derivado por item e recusa placeholders como evidencia de cena. Figshare RDD v1/CC BY 4.0 verificados para o lote, sem transferencia automatica da autorizacao V1. O ultimo preflight, registrado em `datasets/reports/ml_preparation_state.json`, ficou abaixo do piso cauteloso de 2 GB de RAM livre apos PyTorch. Nenhum treino ou fit executado. XGBoost/scikit-learn/SHAP foram instalados apenas no ambiente local; corpus tabular elegivel nao foi encontrado nos artefatos locais.


## Pacote mínimo de revisão para smoke YOLOX V3 — 25/09/2026

O produtor existente `scripts/datasets/review_annotations.py --build --smoke-rdd`
selecionou oito imagens locais do manifesto histórico TRAIN RDD: uma por
D00/D10/D20/D40 em Índia e Japão. A seleção foi determinística e conferiu hashes
de imagem, XML, seleção, manifesto e release. O pacote local pinado em
`datasets/processed/annotation_review/review_6225e89cfebf.html` contém as
caixas originais e exige revisão de completude para cada uma das quatro classes.
O HTML é local e ignorado pelo Git. As oito decisões humanas foram validadas e
importadas de `Downloads/annotation_review_decisions.json` (SHA-256
`8cb075492cbbb9a25e868b3b07f8a7726cdadb237dbdb93d5adf8026fc3c40f5`).
Foram cinco aprovações semânticas e três ambiguidades (India D10/D20/D40).
Todos os oito grupos foram informados como `UNCONFIRMED`, sem ID de cena.
Nenhuma dessas imagens foi autorizada para smoke V3; o uso V1 não transfere
permissão. Não há negativo selecionado nem nova divisão TRAIN/VALIDATION.

Das 130 propostas anteriores, nenhuma é pré-requisito direto deste smoke RDD
Índia/Japão. As 24 Norway D40 são candidatas de revisão para eventual ciclo RDD
mais amplo; as outras 106 são de fontes/classes fora deste smoke. As 461 caixas
Norway, incluindo 430 pequenas, permanecem intocadas. Uma revisão direcionada
das oito imagens não prova qualidade estatística da fonte.

Medições desta retomada variaram de 0,407/0,297 GB a 2,861/2,306 GB livres
antes/depois de importar PyTorch; RTX 4050 com 4.915–4.924 MiB livres.
O piso cauteloso de 2 GB foi atingido em uma medição intermediária; as
medidas oscilam. O checkpoint contém a medição mais recente.
`SMOKE_DATA_READY=false` e
`HARDWARE_GATE=false` até medir o perfil real do loader e otimizador,
`SMOKE_PASSED=false`; zero iterações. Release Figshare RDD v1, licença CC BY 4.0
e MD5 oficial do arquivo foram reconfirmados; proveniência e licença da fonte
passaram para este lote, com atribuição obrigatória. Cinco imagens aprovadas
cobrem as quatro classes; as três ambíguas ficam excluídas do smoke. O teste
aguarda agrupamento, autorização V3 e contrato de treino V3.

## Preflight retomado — 25/09/2026

Os índices históricos de TRAIN/VALIDATION foram lidos linha a linha, sem abrir
imagens: 23.830/3.858 imagens, 34.526/7.718 caixas, 17,3/3,1 MB de manifests.
Resoluções mais frequentes de TRAIN: 600×600, 720×720, 4040×2035 e 3643×2041.
Uma imagem RGB decodificada a 640² custa ~1,23 MB antes de mosaico, tensores e
otimizador. Estes números descrevem o corpus histórico, não um TRAIN V3 autorizado.
Perfil candidato medido/documentado: GPU única, zero workers em ambos loaders,
sem pin/cache/prefetch, batch inicial 1 para TRAIN e VALIDATION, 640² e AMP sujeito
a estabilidade. Nenhum limite de segurança foi rebaixado, nenhum smoke foi iniciado.
O estado e os hashes atuais estão em `datasets/reports/ml_preparation_state.json`.

## Execução local V3 — 25/09/2026

O relatório `datasets/reports/urban_vision_v3_draft.json` e o pool derivado em
`datasets/processed/urban_vision_v3/candidate_pool.jsonl` materializam 130
propostas em `QUARANTINE`, sem papéis TRAIN/VALIDATION/FROZEN_TEST. Evidência de
release oficial foi vinculada a 80 propostas RDD/UNIVALI com hashes de imagem,
anotação/máscara e manifestos; 50 Urban ainda exigem origem/direitos por imagem.
Todos os 130 continuam sem decisão semântica humana e sem autorização do novo
ciclo. Não houve download, modificação raw, conversão científica ou novo split.

`scripts/datasets/review_annotations.py --validate/--import-decisions` valida
o export do HTML pinado e persiste decisões imutáveis sem conceder treino.
`build_detection_manifests.py` agora dispõe de conversor COCO que recusa
supervisão parcial e caixas fora da imagem; foi testado apenas com fixtures.
`datasets/reports/ml_preparation_state.json` registra hashes, etapas e o
preflight real. CUDA/YOLOX estão presentes; RAM disponível abaixo de 1 GB e
ausência de TRAIN V3 autorizado impedem smoke. O contrato atual do trainer
também exige autorização de split. Detalhes e fontes oficiais:
`docs/ml/DATA_READINESS.md`.

## Plano executável do próximo ciclo — 25/09/2026

Nova camada de planejamento, sem nova auditoria de raw: `training_class_plan.json`,
`review_summary.json` e `missing_data_plan.json` em `datasets/reports/` são produzidos
por `scripts/datasets/review_annotations.py --plan`, reutilizando o pacote de 130 casos.
Índice operacional: `docs/ml/DATA_READINESS.md`. Esta seção esclarece estados de
planejamento/direitos; os resultados históricos abaixo permanecem preservados.

35 categorias: 4 `DETECTOR_CLASS` candidatas, 5 `CONTEXT_ATTRIBUTE`, 3 `REVIEW_ONLY`,
0 `NOT_PHOTO_DETECTABLE`, 23 `DATA_NOT_READY`. Ondas: 4/8/23 categorias.
Adequação visual não é autorização. `CLASS_ORDER=[]`, sem split final.
O escopo faltante de dados continua 29; os estados primários têm outro denominador.

130 propostas disponíveis para inspeção, zero aprovadas. Métricas semânticas não
avaliadas permanecem `null`. 80 registros históricos de release oficial são preservados;
não transformados em verificação de direitos por imagem. As 130 propostas precisam
vincular evidência ao novo ciclo; 50 Urban têm ainda origem/direitos desconhecidos e
parent desatualizado. Formulários de procedência e ações por proposta preparados.
CC0 do uploader não prova direitos de terceiros. Nenhuma aprovação foi rebaseada.

D00/D10/D20/D40: distribuições, países/grupos, caixas pequenas, fingerprints e vazios
recontados em TRAIN/VALIDATION, por manifestos registrados. Completude e near-duplicates
continuam pendentes. D43=310 e D44=3195 caixas TRAIN são contagens anteriores reutilizadas,
com 24 propostas no lote; não houve novo scan XML. Open manhole: 152 imagens/154 caixas
históricas Urban, sem autorização; RTK dreno normal não vira problema.
Norway: 461 D40 preservadas, sem excluir as 430 pequenas.

Plano individual para as 29 lacunas: exemplos, negativos difíceis, anotação, grupos,
domínio, direitos e custo. Pilotos propostos, não critérios de suficiência científica.
Faixa total de 0,957–3,828 GB; limite superior não cabe na folga atual. Toda aquisição
continua condicionada a medição real e orçamento por fonte/global; nenhum download.
Severidade, risco e prioridade XGBoost têm coleta separada no plano, usando formulário
canônico existente e snapshots PIT; nenhum label derivado de review_confirmed/regras.

## Escopo completo e preparação executável — 25/09/2026

Esta seção complementa e prevalece sobre conclusões anteriores de escopo/readiness.
Taxonomia local e registro em `main` conservados: **urmind-issue-taxonomy-v3, 35 categorias**.
IDs, definições, exemplos/exclusões, tarefas, fontes, bloqueios e ações por categoria estão
em `reports/taxonomy_coverage.json`, produzido por `app.datasets.cli coverage --write`.
Não houve renumeração, redução a quatro/nove classes nem alteração da cabeça YOLOX.

### Três conjuntos distintos

- `REQUESTED_PRODUCT_SCOPE`: todos os 35 IDs do registro `ISSUES`.
- `TRAINABLE_CLASS_SET`: vazio para **novo ciclo**. Aprovação histórica RDD V1 não migra automaticamente.
- `VALIDATED_MODEL_CAPABILITIES`: vazio para o novo escopo; não apaga resultados históricos.
- `YOLOX_DATA_READY=NO`, `YOLOX_HOLDOUT_READY=NO`, `FULL_REQUESTED_SCOPE_READY=NO`.

A matriz inclui formulário vazio de coleta por categoria, direitos/procedência, grupo,
anotação original/derivada, estado do objeto, evidência contextual, duas revisões e adjudicação.
Cada bloqueio tem `blocker_actions`; números aprovados iguais a zero têm escopo explícito
de autorização do novo ciclo, não significam inexistência de imagens históricas.
O formulário registra as 35 classes como `NOT_ANNOTATED` até revisão pertinente.
Não se converte ausência de anotação em negativo. Estratégia mínima: completar anotação
humana ou restringir a tarefa; não se afirma que label vazio/ignore flag altera a loss YOLOX.

### Novas verificações limitadas a TRAIN/VALIDATION

Recontagem dos XMLs originais **somente das imagens referenciadas nos dois manifestos
TRAIN/VALIDATION já identificados abaixo**; zero XML ausente. Nenhum XML TEST foi aberto.

| Papel | IDs nativos encontrados e quantidade de caixas |
|---|---|
| TRAIN | D00=14173; D10=5777; D20=8685; D40=5891; D01=43; D11=14; D43=310; D44=3195; D50=2522 |
| VALIDATION | D00=4104; D10=2359; D20=934; D40=321; Repair=353; Block crack=2 |

D43 aparece em 286 imagens TRAIN; D44 em 2615. São candidatos a
`URMIND_FADED_ROAD_MARKING`, **não mapeamento autorizado**. A inspeção do rótulo e da
semântica visual precisa distinguir faixa desgastada, marcação normal e caixa ambígua.
D01/D11 não foram convertidos automaticamente em D00/D10. D50/Repair não são defeito
por definição. Esta contagem não é inventário de todos os XMLs protegidos do acervo.
Mantidas as 461 D40 Norway, inclusive 430 pequenas: tamanho não comprova erro.

### Inventário reconciliado: dez fontes, usos delimitados

Contagens e hashes históricos permanecem abaixo; nova medição de armazenamento foi
por atributos locais, sem reabrir conteúdos protegidos nem baixar/hidratar arquivos.

| Fonte local | Anotação efetiva / uso permitido nesta preparação | Pendência concreta |
|---|---|---|
| rdd2022 | XML bbox; D00/D10/D20/D40 históricos; D43/D44 candidatos | piloto semântico, completude e autorização de novo ciclo |
| ird_dashcam | bbox, 439 anotadas/104 vazias no recorte histórico | preservar exclusão da demonstração e de seus grupos; não reintroduzir treino |
| univali_br | máscaras POTHOLE/CRACK/LANE | 32 revisões pendentes; máscara/componente não é instância; não liberar população |
| rtk_br | máscaras cracks, pothole, water-puddle, storm-drain, patch, markings | 701 imagens; 57 com pothole/104 componentes candidatos; rever instâncias e near-duplicates |
| urban_community | caixas nativas, 451 propostas históricas | 50 revisões pendentes; origem/direitos por imagem; resolver parent hash desatualizado |
| bdd100k | recorte de segmentação em ZIP, 10000 imagens históricas | contexto/ativos normais; não implica placa ou semáforo danificado |
| global_streetscapes | 15007 imagens/rótulos de cena históricos | diversidade/iluminação/contexto; sem boxes de ocorrência |
| project_sidewalk | recorte local de rampas em Parquet/keypoints | não é o conjunto externo de obstáculos/superfície; não inventar bbox |
| rampnet | panoramas, pontos e coordenadas de rampa | presença de rampa não comprova ausência em outra travessia |
| camber | CSV de detecções/predições de terceiros, GPS/rota | sem revisão humana não é ground truth |

RTK: dreno normal não vira obstruído; água/poça não vira alagamento; remendo não vira
defeito; markings não implica desgaste. Animais, contêineres, postes e semáforos normais
são contexto ou candidatos a negativos **a revisar**. Grupos de cena/panorama/rota e
duplicatas devem preceder splits; não usar desempenho do detector para escolher teste.

### Revisão humana utilizável, isolada do aplicativo

Ferramenta: `scripts/datasets/review_annotations.py`.
Pacote local: `datasets/processed/annotation_review/review_1b00e44bcd58.html`.
Abrir diretamente no navegador local; não publicar em Vercel. Contém originais e
caixas/máscaras, definição, procedência/hash, grupo, decisão, completude, justificativa,
correção derivada e histórico exportável/retomável. Sem rede e sem escrita no banco.

- 32 UNIVALI + 50 Urban Community existentes, ainda sem aprovação.
- Piloto TRAIN: 24 D40 Norway + 12 D43 + 12 D44, estratificado por área.
  É seleção para revisão semântica, não seleção de teste nem revisão da população.
- Exportar JSON no botão da ferramenta. Validar com
  `python -B scripts/datasets/review_annotations.py --include-rdd --validate <export.json>`.
- O validador vincula pacote, folhas, originais e manifestos por hash; exige revisor,
  instante com timezone, grupo, justificativa e correção quando aplicável.
- Apenas a classe proposta pode receber estado neste formulário; as outras 34
  permanecem `NOT_ANNOTATED`. `ABSENT_REVIEWED` não se propaga entre classes/imagens.
- Urban: as 50 linhas apontam para hash antigo do manifesto pai, embora o hash da
  própria folha ainda confira. `STALE_PARENT` impede aprovação. Correções/ambiguidade
  podem ser propostas; reconciliar linhagem pelo produtor antes de aprovar/importar.
- Aprovação de linha nunca concede licença, autorização de treino ou promoção.
  Importação/adjudicação canônica é etapa separada; originais não foram sobrescritos.

Pré-flight real do pacote: 37.751.842.083 bytes ocupados em datasets/models/mlruns;
estimativa adicional 115.871.128; final estimado **37.867.713.211 < 40.000.000.000**.
Nenhuma aquisição externa. Exceção RDD e limites por fonte conservados.
Fontes externas sem tamanho final/licença resolvidos permanecem em dry-run, sem download.

### Pesquisa primária e lacunas de aquisição (25/09/2026)

Registro versionado reutilizado: `metadata/taxonomy_v2_dataset_candidates.json`.
O nome histórico v2 foi preservado; conteúdo/escopo não rebaixam a taxonomia v3.

| Fonte / versão consultada | Evidência e limite | Próxima ação preparada |
|---|---|---|
| [DamageArbiter/Milton](https://github.com/rayford295/DamageArbiter), Figshare 28801208.v2; [HF](https://huggingface.co/datasets/Rayford295/BiTemporal-StreetView-Damage) | 2556 cenas pós-Milton; severidade de cena e descrições humanas/LLM distintas; HF CC BY-NC 4.0, código MIT não concede direitos de imagens | confirmar identidade entre releases/direitos; separar autoria da descrição; anotar árvore/galho/detrito com boxes humanos |
| [TACO](https://github.com/pedropro/TACO) | lixo segmentado COCO; contribuições `annotations_unofficial` não revisadas; direitos Flickr por imagem | filtrar revisadas/licenciadas; não confundir lixo disperso com descarte irregular/entulho |
| [Roadway Flooding v1](https://data.mendeley.com/datasets/t395bwcvbw/1), 2019-10-09, Sazara/Cetin/Iftekharuddin | 441 imagens; CC BY 4.0; formato efetivo de arquivos/anotações, grupos e tamanho ainda não verificados | inspecionar amostra autorizada/manifests antes de aquisição; água não indica profundidade, causa ou risco |
| [Project Sidewalk CV 2021](https://github.com/ProjectSidewalk/sidewalk-cv-2021) e [tagger validado](https://huggingface.co/datasets/projectsidewalk/sidewalk-tagger-ai-validated) | recortes/labels de rampa, falta de rampa, obstáculo e superfície; agrupar mesmo panorama; não equivalem ao recorte local | verificar licença da mídia/release e qualidade dos rótulos por tarefa; manter validado separado de contribuições |
| [Condição de sinais](https://ieeexplore.ieee.org/document/10371993/) e [TSDA](https://github.com/dsphamgithub/tsda) | condição/oclusão/dano, incluindo dados sintéticos; release/licença efetivos não confirmados | solicitar/confirmar pacote e direitos antes de aquisição; sem inferir dano de mera presença |

Não foi identificada nesta pesquisa uma fonte primária com pacote e licença já
verificados que feche poste, defensa, cabo, fiação e ponto de ônibus danificados.
Ação concreta: formulários por categoria na matriz para coleta consentida, com ativo
normal de contraste e dupla revisão. Não há cobertura alegada nessas classes.
Candidatos adicionais **não ativados**: lixo disperso, poça e trinca genérica.
Benefício/dados/sobreposição/custo constam em `new_concept_candidates`; priorizar as 35.

### Objeto, estado, contexto e avaliação

Definições e exclusões da matriz são o contrato: árvore presente não é árvore caída;
cabo visível não comprova energização; carro parado não comprova abandono; água não
comprova vazamento. Classes contextuais exigem observação contextual/inspeção e
incerteza explícita. `incident_id`/`related_issue_codes` unem impactos relacionados:
árvore caída obstruindo calçada não se torna automaticamente duas ocorrências.

Pré-registro por categoria: precisão/recall, AP quando bbox/máscara, falsos positivos
em negativos difíceis, matriz de confusões, estratos de domínio/tamanho, intervalos
por grupos independentes e quantidade de grupos. Limites numéricos e tamanho de
amostra devem ser acordados antes da avaliação final; nenhum valor foi inventado.
Frozen Tests históricos e exclusões continuam lacrados; nenhum novo split gerado.
Compatibilidade treino/ONNX/Worker continua sujeita às ressalvas da consolidação abaixo.

### Descrições, alvo tabular e decisão conservadora

O renderer passou a recusar previsões/limitações livres e a exigir referências por
campo. Prosa livre do motor não é reutilizada como fato. Valores textuais registrados
aparecem como literais atribuídos; não se tornam conclusão do modelo. Referências
têm origem, campo, instante, tipo e limitação; Capture e RoadSegment usam suas
origens reais. No contrato público, avaliação exige ID/instante/versão; consequências
são condicionais, com domínio conhecido e regra versionada. Sem esses elementos,
avaliação/ação dependente fica indisponível. Verdade semântica não é comprovada por
um hash ou formato válido: o adaptador resolve registros persistidos, revisão humana
continua necessária. APIs e renderers têm regressões; não se afirma auditoria do banco real.

`annotations/tabular_labeling_protocol.json` contém cinco alvos separados, formulário
vazio, critérios de dupla anotação/adjudicação, features, cutoff, diversidade, métricas,
baseline, early stopping/calibração/holdout separados. `review_confirmed` continua o
único alvo implementado pelo export de treino; não substitui severidade, risco,
prioridade ou recorrência. Risco requer desfecho/horizonte/exposição; recorrência,
seguimento e censura. Scores de regras não são ground truth independente.
Export existente `CoreService.tabular_ground_truth` conserva snapshots point-in-time;
testes de fixtures não medem missingness/diversidade do banco real nem fecham leakage.
Features do detector operacional devem ser coletadas antes da revisão; anotações
perfeitas não podem fingir qualidade real do detector.

**Arquitetura:** reutilizados taxonomia, candidatos, contratos, export e renderers;
estendidos produtor da matriz, protocolos e refresh seletivo. Ferramenta offline nova
justificada porque painéis anteriores eram leitura, e Review do app trata ocorrências,
não anotações de treino. Registry atualizado seletivamente; entradas protegidas
preservadas sem alegar reverificação. Nenhum modelo/serviço paralelo introduzido.

**OPEN acionáveis:** adjudicar piloto D43/D44 e Norway; reconciliar parent Urban;
32+50 revisões; licenças por imagem; completude de todas as classes ativas; grupos
independentes; coleta para categorias sem dados; rubricas/alvos tabulares e corpus
operacional; critérios numéricos de avaliação. Ferramentas e formulários preparados,
aprovação humana e capacidades científicas não falsificadas.

### Verificação final desta preparação

- Taxonomia local igual ao arquivo de `main`, por comparação de bytes com normalização
  de newline; contagem direta 35. Matriz: seis categorias com candidatos locais,
  29 com `MISSING_DATA`; todas exigem revisão e avaliação, zero autorizadas no novo ciclo.
- Pacote HTML: 115.289.879 bytes; SHA-256
  `0f1e9e1806f6c2acc40e7449661b7d52e09aeed9ba48e170893a10921ece37e7`.
  `git check-ignore` confirmou exclusão do pacote local. Não contém decisões humanas.
- `pytest` focal: **192 passed**, dois avisos de depreciação Starlette/httpx/anyio,
  sem falhas. Arquivos: `test_core_service`, `test_public_api`, `test_report`,
  `test_annotation_review`, `test_taxonomy_candidates`, `test_tabular_phase8`,
  `test_artifact_registry`. Fixtures, sem banco, treino ou conjunto protegido.
- Ruff passou nos arquivos alterados; mypy passou em cinco módulos afetados
  (`report`, `public_view`, `core`, `taxonomy_candidates`, `tabular`).
  `git diff --check` passou. Avisos Git de LF/CRLF não são falhas de conteúdo.
- Edge headless local: 130 linhas, navegação, propriedade DOM `disabled` para
  aprovação Urban desatualizada, exportação de fixture `TEST_ONLY` com 34 classes
  restantes `NOT_ANNOTATED`; zero erros JavaScript e zero requisições HTTP(S).
  Nenhum export de decisão real foi criado. Chromium do Playwright estava ausente;
  foi usado Edge instalado, sem download. `isDisabled` de option não representou a
  propriedade nativa; o teste foi corrigido para consultar `option.disabled`.
- Revisão independente encontrou inicialmente prosa livre em referências e fontes
  incorretas; depois, identificadores/textos com quebra de linha e mapa de cobertura
  sem validação. Correções e regressões aplicadas. Confirmação focal independente:
  **40 passed**, nenhum achado remanescente nesses caminhos. Não é validação científica.
- Registry: refresh/check seletivos dos três artefatos de preparação, zero divergência;
  outros hashes históricos preservados e explicitamente não reverificados.

Reprodução a partir da raiz, usando `backend/.venv/Scripts/python.exe`:
`scripts/datasets/review_annotations.py --dry-run --include-rdd` mede o pacote;
`--build --include-rdd` cria pacote novo e recusa sobrescrever histórico.
Para matriz e formulário, executar em `backend/`:
`python -m app.datasets.cli coverage --write` e
`python -m app.ml.tabular --label-form --write` (não inicia treinamento).
O pacote é vinculado ao estado atual: alterações em originais/folhas exigem novo pacote.

Arquivos reutilizados/atualizados: `backend/app/datasets/{cli,taxonomy_candidates}.py`,
`backend/app/ml/tabular.py`, `backend/app/services/{report,core,public_view}.py`,
`backend/app/schemas/public.py`, `backend/app/api/v1/public.py`, testes correspondentes,
`scripts/datasets/refresh_registry.py`, `metadata/{artifact_contract.yaml,artifact_registry.json,
taxonomy_v2_dataset_candidates.json}`, este STATUS e
`docs/{PROJECT_STATE.md,D40_ROOT_CAUSE_ANALYSIS.md,Planinng/MASTER_PLAN.md}`.
Novos necessários: `scripts/datasets/review_annotations.py`, seu teste,
`reports/taxonomy_coverage.json`, `annotations/tabular_labeling_protocol.json`
e pacote HTML local ignorado. Evidências gráficas históricas IRD preexistentes preservadas.

## Consolidação científica vigente — 25/09/2026

**VERDICT=BLOCKED. YOLOX_DATA_READY=NO; YOLOX_HOLDOUT_READY=NO;
XGBOOST_LABELS_READY=NO.** Esta seção prevalece sobre os inventários históricos
abaixo. Consolida evidências existentes, não concede autorização de treinamento.
Revisão do código: `e57db6182de2be37a8176b845c87e35376c32a76`.
Planejamento localizado em `docs/Planinng/MASTER_PLAN.md` (grafia real).
Não houve treino, smoke training, inferência de modelo, acesso ao Frozen Test,
deploy, push, exclusão ou regeneração de splits. Não foram lidos `.env` ou dados
do Supabase. Testes usam fixtures; não demonstram qualidade científica real.

### Fatos

#### Escopo, identidade e cadeia oficial

Mantidos `metadata/artifact_contract.yaml`, `metadata/artifact_registry.json`,
`metadata/class_mapping.yaml`, `metadata/yolox_model_v1.json` e os produtores
existentes: `build_detection_manifests.py`, conversores de máscaras, folhas de
revisão e `refresh_registry.py`. Nenhum manifesto gerado foi editado à mão.
`reports/pre_training_readiness.json` é um relatório histórico do MODEL V1;
seus campos de interface/engine não autorizam novo treinamento.
O consumidor `AuthorizedDetectionDataset` verifica manifesto, papel, autorização,
hash da imagem, dimensões e disponibilidade local. Isso não verifica semântica.

Recalculados com `scripts/datasets/_core.py:file_sha256/require_local`:

| Artefato | SHA-256 atual | Resultado |
|---|---|---|
| `manifests/detection_train_authorized.jsonl` | `899853da0ed47fc0c456dfb7cf87f15c2262364119a28002ae4a546611ea699c` | igual ao relatório V1 |
| `manifests/detection_validation_authorized.jsonl` | `aa686ab48c53bdca5f58a099d91c4697275912bdaa8eb491273b5794b7e26d7a` | igual ao relatório V1 |
| `annotations/univali_instance_audit_v1.jsonl` | `2d5ff1037272c8e0c8a6a411cfa5b5fbeebab5b0e03a5217658fa14a34756bf0` | igual ao status de revisão |
| `annotations/urban_community_box_audit_v1.jsonl` | `76b988fa27b5bd5a8340fe7f0658fb6b1a604149ae37a3a7c1cd3b5e0a28325e` | igual ao status de revisão |

Esses hashes identificam os manifestos/folhas, não substituem revalidação dos
bytes de todas as imagens. Não foi recalculado hash de conteúdo TEST, de seu
ledger ou de derivados protegidos. Nenhum resultado histórico de TEST foi
utilizado para selecionar novos parâmetros nesta consolidação.

Conferência seletiva do registry: nove hashes iguais, zero divergências, para
os dois manifestos de detecção acima, autorização RDD V1, duas folhas humanas,
`univali_br_boxes.jsonl`, `urban_community_boxes.jsonl`, metadata YOLOX V1 e
`pre_training_readiness.json`. Usado o hash oficial local e comparação com
`artifact_registry.json`; não executado refresh global que percorre TEST.

#### Classes, cobertura e estados independentes

Contagem nova exclusivamente nos manifestos TRAIN/VALIDATION existentes:

| Classe canônica | Caixas TRAIN | Caixas VALIDATION | Autorização verificável |
|---|---:|---:|---|
| URMIND_ROAD_D00 | 14.173 | 4.104 | registro histórico limitado ao MODEL V1 |
| URMIND_ROAD_D10 | 5.777 | 2.359 | registro histórico limitado ao MODEL V1 |
| URMIND_ROAD_D20 | 8.685 | 934 | registro histórico limitado ao MODEL V1 |
| URMIND_ROAD_D40 | 5.891 | 321 | registro histórico limitado ao MODEL V1 |

TRAIN: 23.830 imagens, 34.526 caixas, 9.794 registros sem caixas.
VALIDATION: 3.858 imagens, 7.718 caixas, cinco registros sem caixas.
Todas as 42.244 caixas desses dois manifestos passaram novamente em limites,
ordenação e dimensões positivas usando as dimensões declaradas. Não foram
redecodificadas as imagens: `geometric_valid` aqui é limitado ao manifesto.
Há zero imagens ausentes ou marcadas cloud-only nesse recorte pela inspeção
de atributos; a situação difere do inventário antigo, sem atribuir sua mudança
a uma ação desta execução.

| Fonte / classe | Evidência e cobertura | Estado conservador |
|---|---|---|
| RDD2022 / D00, D10, D20, D40 | XML VOC e mapa explícito; contagens acima | candidatos semânticos; autorização V1 histórica, não autorização de novo ciclo |
| UNIVALI / POTHOLE | máscaras; derivada conserva UNIVALI_POTHOLE; 32/32 linhas da folha sem decisão | componente não é instância validada; D40 bloqueada |
| UNIVALI / CRACK, LANE | trinca genérica e faixa | não converter em D00/D10/D20 ou dano de sinalização |
| RTK / pothole | relatório: 57 imagens, 104 componentes candidatos em 701 imagens | máscara semântica; sem autorização de instâncias ou treino |
| RTK / demais classes | códigos nativos documentados; craks genérica, tampas, remendos e superfícies | não inferir dano ativo, bueiro aberto ou subclasse de trinca |
| Urban Community / pothole | mapa semântico aprovado; 451 caixas sem decisão individual; folha 50/50 pendente | somente candidato a TRAIN_REINFORCEMENT; avaliação proibida |
| Urban Community / outras classes e vazios | mapa de IDs inferido de pastas; relatório registra 100 imagens sem anotação | cobertura parcial/desconhecida; não são negativos comprovados |
| IRD Dashcam | relatório existente: 439 imagens rotuladas, 104 labels vazios | treino/avaliação não autorizados; grupo inteiro excluído de holdout após reserva de demo |
| Sidewalk, RampNet, BDD100K, Global Streetscapes, CAMBER | keypoints, segmentação, contexto ou saídas de detector de terceiros | nenhuma nova classe YOLOX autorizada; CAMBER não fornece Ground Truth humano de risco |

Contagem de estados nesta rodada: `geometric_valid`=42.244 caixas do manifesto
RDD TRAIN/VALIDATION; `semantic_candidate`=essas classes mapeadas, sem inferir
correção de cada caixa; `human_validated`=zero decisões nas 82 linhas das duas
folhas conferidas, população RDD desconhecida; `training_authorized` para novo
ciclo=nenhuma fonte demonstrada. Não somar estados como se fossem etapas cumpridas.
`APPROVED_FOR_MODEL_V1`, atribuída a `user_explicit_approval`, permanece intacta
e restrita à versão/população/fingerprints originais; sua cadeia completa com
splits protegidos não foi reaberta.

Não existe matriz comprovada de exaustividade fonte × classe × imagem. Classe
não anotada é desconhecida. RDD recusa D01/D11/D43/D44/D50/Repair/Block crack/D0w0
conforme contrato; recusa não significa ausência de objetos. Imagem sem caixas,
imagem só com classes excluídas e negativo humano são estados distintos.
O loader comum trata caixas ausentes como background: antes de misturar fontes
parcialmente anotadas é obrigatório comprovar exaustividade para todas as classes
ativas ou implementar/validar uma política explícita de regiões/classes ignoradas.
Não foi introduzida tal política nesta rodada.

#### D40 Norway: decisão sem corte automático

Recalculados apenas os 461 boxes Norway de TRAIN. Com
`r=min(640/image_width,640/image_height)`, área projetada
`(xmax-xmin)*(ymax-ymin)*r²`: mediana **100,7205554513 px²**;
**430/461 (93,2755%)** abaixo de 1.024 px². Isso reproduz a observação de escala
histórica, não sua alegação causal de que as caixas sejam ruído.
Área menor que 32² não equivale a ambas as dimensões menores que 32.
**Decisão: preservar todas as caixas; nenhuma poda ou reponderação automática.**
Examinar sob protocolo humano imagem original e projeção do modelo, contraste,
oclusão, truncamento, contexto e caixa, separando perda por resolução de erro
semântico. Estratificar por tamanho/país e incluir caixas maiores como controle;
registrar identidade do revisor e adjudicação. Nenhuma imagem TEST pode entrar.
Qualquer nova transformação exige versão derivada, fingerprints e autorização.
O aditivo em `docs/D40_ROOT_CAUSE_ANALYSIS.md` delimita as alegações históricas.

#### Grupos, duplicatas e holdout

TRAIN tem três grupos declarados; VALIDATION tem um. Interseção de grupos e de
`source_fingerprint` entre esses papéis: **zero**. Isso confirma isolamento
declarado/identidade registrada, não ausência de near-duplicates ou sessões comuns.
RDD não fornece rota/sessão suficiente. Não reexecutada busca visual completa.
RTK mantém candidatos de near-duplicates cruzando splits publicados; dHash é
triagem, não adjudicação. Urban Community conserva sua revisão de duplicatas
e proibição de usar TRAIN_REINFORCEMENT para seleção, calibração ou métricas.

As remoções V2 e do quality gate registradas em
`docs/audits/V2_DATASET_CLEANUP_2026-09-24.md` não foram revertidas. Novo split
e novo contrato de avaliação continuam necessários. Excluir pesos não desfaz
contaminação histórica. Preservadas exclusões do TEST histórico e da sonda de
domain shift. IRD Dashcam inteiro permanece excluído de futuro holdout até
prova independente de grupos; não basta retirar somente a imagem da demo.

#### Armazenamento

Medição nova por atributos/stat com `_core.measure_dir`, sem abrir mídia:

| Fonte | Bytes lógicos atuais | Teto decimal GB |
|---|---:|---:|
| RDD2022 | 13.671.317.053 | 14, exceção explícita somente desta fonte |
| UNIVALI | 483.203.492 | 0,5 |
| Urban Community | 1.491.788.588 | 1,5 |
| RTK | 104.842.893 | 7 |
| IRD Dashcam | 1.055.531.504 | 7 |
| Project Sidewalk | 5.364.439.538 | 7 |
| RampNet | 6.841.369.819 | 7 |
| CAMBER | 95.830.501 | 0,2 |
| BDD100K | 1.271.665.460 | 7 |
| Global Streetscapes | 6.066.739.223 | 7 |

Raw total: **36.446.728.071 bytes**; zero cloud-only/erros reportados por atributos
nesses diretórios. Todos os tetos individuais acima passam no escopo raw.
O teto agregado de 40 GB não substitui esses tetos. Bytes lógicos não são
espaço físico liberável; novas derivações exigem novo preflight incluindo modelos,
processados e temporários. O antigo total de 35,29 GB não descreve o raw atual.
Medição agregada por stat: `datasets`=36.652.849.553, `models`=1.029.095.334,
`mlruns`=0 bytes; soma=37.681.944.887 bytes, abaixo de 40 GB nesse escopo.
Nenhum conteúdo de modelo ou ledger protegido foi aberto nessa medição.

#### Treino, ONNX e Worker

Contrato preservado: classes ordenadas D00/D10/D20/D40; entrada 640×640,
BGR, float32 CHW/BCHW, faixa 0–255, padding 114, sem normalização adicional;
mesma função YOLOX `preproc`. Dataset decodifica RGB/PIL e converte para BGR;
Worker decodifica BGR/OpenCV. Targets partem de xyxy, tornam-se class/cxcywh;
export habilita decoding da head e consumidor aplica pós-processamento e escala.
Não se presume igualdade bit a bit entre decodificadores JPEG.
Testes de contrato passaram, mas faltam checkpoint/ONNX atuais e comparação
numérica com tolerâncias pré-fixadas, hashes e imagens TRAIN/VALIDATION permitidas.
Não se afirma paridade operacional nem desempenho com modelos removidos.

#### XGBoost: alvo, rótulos e tempo

Alvo executável atual: **review_confirmed**, decisão humana binária confirm/reject
vinculada a Review persistida, Event e autor. Correção de classe não vira rótulo
binário. Esse alvo estima confirmação sob o processo de revisão e sua seleção;
não representa automaticamente verdade física nem ocorrência de acidente.

| Saída proposta | Alvo que seria necessário | Rótulo disponível/decisão |
|---|---|---|
| Confirmação | decisão binária adjudicada sob protocolo estável | contrato existe; corpus real elegível não medido nesta rodada |
| Severidade | inspeção ordinal independente com critérios mensuráveis | não comprovado; nunca usar review_confirmed ou regra atual como verdade |
| Risco | desfecho definido, exposição, horizonte e censura | não comprovado; score ordinal não é probabilidade |
| Prioridade | julgamento independente ou ranking com política/capacidade explícita | não comprovado; decisão da própria regra é alvo circular |
| Deterioração/recorrência | observações repetidas, janela de seguimento e censura | não comprovado; ausência de registro não é ausência de ocorrência |

`tabular.py` contém 12 features allowlisted; exclui identidade, decisões,
severidade, prioridade e revisões. `None` não vira zero. Contexto desconhecido
ou pós-evento é mascarado; analytics retrospectivo é recusado.
`CoreService.tabular_ground_truth` seleciona snapshot anterior ao início de qualquer
revisão, verifica Review persistida e mantém training_authorized=false.
Histórico usa ordem serializada de commit e occurred_at anterior; legado sem
ordem confiável vira missing. Snapshot imutável não prova completude histórica.

Não houve consulta ao banco nem export novo: contagens reais de snapshots,
positivos/negativos, cobertura temporal, missingness por feature/fonte/classe,
grupos e desfechos permanecem **UNVERIFIED**, nunca zero inventado. O piso
provisório 200 exemplos/50 por alvo/10 grupos não é justificativa de poder
estatístico nem autorização. `build_tabular_dataset_version` usa split por grupo;
`temporal_split` existe, mas não é automaticamente o split desse produtor.
Atualização 25/09/2026: o pipeline autorizado existe em `tabular.py`
(`python -m app.ml.tabular --train EXPORT --contract C --authorization A --out DIR`):
contrato pré-registrado (cortes, orçamento, paciência, método de calibração, seed),
autorização humana vinculada a `dataset_sha256` + `contract_sha256`, split temporal
TRAIN/VALIDATION/CALIBRATION/TEST com isolamento de grupos, early stopping só em
VALIDATION, calibração Platt/isotônica na coorte própria, teste final contra o prior
com bootstrap por grupo, auditoria de vazamento, artefatos com SHA-256 e
`load_promoted_model` fail-closed (exige `promotion.json` humano ligado ao report).
Testado só com fixtures; nenhum treino real: banco DEV tem 0 reviews/0 events.

Piloto de coleta (25/09/2026, consulta direta ao Urmind DEV): 1 Capture
(`URM-3RF4ZK9U`) sem imagem no Storage, sem ponto, `quality` vazio e nunca
enfileirada (0 jobs pendentes; 5 arquivados de Captures que não existem mais):
registro órfão, não processável. **Processamento backend bloqueado:** `.env` usa
`VISION_EXECUTION_MODE=shadow` com o ModelVersion `2527af02…` (yolox-s-quality-rebuild),
hoje `ARCHIVED`/`shadow_authorized=false`; `configured_vision_model` o recusa e todo
upload terminaria em `model_not_available`. Nenhum vision promovido. O ONNX do
run 10h-r1 (`yolox-s-model-v2-d429bde8a9bd`) existe em `models/serving`, mas não
está registrado no banco. Imagens de `datasets/demo` não servem ao piloto:
coordenadas sintéticas e presença no TRAIN do detector (confiança in-sample).

Registro shadow autorizado pelo proprietário e **recusado pelo gate** antes de
qualquer escrita (`model_versions` continua com 3 linhas; `.env` intacto):
`register --shadow-dev` está fixado no artefato `yolox-s-quality-rebuild-7d91f7f6f0c0`
e exige seu laudo de Frozen Test e o `operating-point-lock-verified.json`, ambos
ausentes após a limpeza de 24/09 (`FileNotFoundError`); mesmo presentes, o
`onnx_path` do v2 seria recusado. Não há caminho shadow para o v2 sem nova
decisão sobre o gate. `tests/approved-real-e2e-images.json` está vazio.

**Gate corrigido (25/09/2026, autorização explícita do proprietário).** `--shadow-dev`
não depende mais do artefato antigo: exige `--shadow-authorization` com um arquivo
`urmind-shadow-dev-authorization-v1` em `datasets/metadata/shadow_authorizations/`,
gerado por `python -m app.ml.serving authorize-shadow` (hashes calculados, nunca
digitados). O arquivo prende manifesto de export, ONNX, checkpoint (best.pt conferido),
contrato, ordem de classes, input, opset, VALIDATION e paridade do próprio export,
`run_state` COMPLETED do run e o perfil de inferência calibrado; exige escopo
`URMIND_DEV_ONLY`/projeto DEV, `production_approved=false`, `revoked=false`, responsável
e evidência da aprovação. `SHADOW_DEV_AUTHORIZED` ≠ `PRODUCTION_APPROVED`: `--promote`
com `--shadow-dev` continua recusado e o status científico gravado é `EXPERIMENTAL`
(Frozen Test `NOT_EVALUATED`). O Worker recusa o shadow se o arquivo sumir, mudar ou for
revogado (`shadow_authorization_current`). Arquivo ausente vira erro com o caminho.
Registrado: ModelVersion `a3ff07ea-2d13-494f-ba4b-f79db51483c3`
(`final-epoch5-d429bde8a9bd`), registro repetido devolve o mesmo ID (4 linhas no total).
`backend/.env`: só `SHADOW_MODEL_VERSION_ID` mudou; reversão segura documentada é
`VISION_EXECUTION_MODE=disabled` (o `2527af02…` ARCHIVED não é fallback).
Perfil Worker = navegador: `inference_profile_sha256` d2b6e1ab… nos dois (NMS por
classe 0,45; limiares 0,05/0,13/0,35/0,13; teto 100); o Worker grava o hash em
`quality.inference`. Paridade de configuração não é qualidade (mAP50 0,108).
Photo gate sem `photo_gate_calibration.json`: opcional para captura privada
(`UNCALIBRATED`), publicação segue exigindo atestado humano de privacidade; não alterado.
Capture `URM-3RF4ZK9U`: irrecuperável (Storage do DEV com 0 objetos), sem Event/Review,
fora da elegibilidade tabular por construção; mantida sem edição (não há mecanismo de
marcação; criar um ficou fora do escopo).

Revisão para `review_confirmed`: antes da primeira decisão humana, a fila e o detalhe
ocultam score YOLOX, severidade, prioridade e relatório. Rejeitar exige motivo
(`[motivo:<código>]` em `notes`): só `erro_visual` pode virar negativo;
`duplicidade`, `localizacao` e `imagem_inconclusiva` ficam fora do export com motivo.
Não existe decisão "inconclusivo" persistível (o CHECK de `reviews.decision` aceita só
confirm/correct/reject/detach_evidence): caso incerto fica pendente, sem rótulo.
XGBoost: padrões `tree_method=hist`, `n_jobs=1`, `max_depth=3` (contrato pode mudar),
sem pesos de classe; isotônica recusada abaixo de 1000 exemplos de calibração;
`tabular_runtime`/`predict_review_confirmed` degradam para DISABLED/UNAVAILABLE sem
probabilidade e são apenas consultivos. Ainda não ligados ao Worker/API/DecisionTrace.
Features: `urmind-features-v1` (12) inalterado. O worktree paralelo
`feat/xgboost-preparation-parallel` cria `urmind-pavement-visual-severity-v1` para o
experimento Attain (Low/High D20/D40), separado de `review_confirmed`. Candidatas para um
futuro `urmind-features-v2` do alvo de confirmação, a extrair no mesmo código do
snapshot: contagem por classe, distribuição dos scores, área/posição relativas das
caixas, qualidade da imagem já medida no upload e indicadores de ausência; área de bbox
não é profundidade e score não é gravidade.
Worker local PID 28580, API em 127.0.0.1:8000 e Vite em 127.0.0.1:5173 iniciados para o
piloto; nenhum caso real processado ainda (falta imagem autorizada com local confirmado).
Hospedagem: frontend `https://urmind-lime.vercel.app` chama `https://urmind-api.onrender.com`
(health/ready 200) e autentica no mesmo Supabase DEV `impmeitwtusjtwjouggy` (verificado
no bundle público). Uploads feitos no site publicado entram na mesma fila pgmq que o
Worker local consome; o web service (`python -m app`) não embute Worker nem ONNX.
Nada desta rodada foi publicado: blinding da revisão, motivo de rejeição e regra de
export existem só no código local até commit/deploy. Existência de Worker próprio no
Render (que competiria pela fila com o modelo ARCHIVED) não verificada.

Imagens-piloto (VALIDATION `VALIDATION_AUTHORIZED`, RDD2022 CC BY 4.0, fora do TRAIN
do 10h-r1 e dos manifestos de TEST, sem GPS EXIF → só demonstração técnica visual,
nunca Capture geográfica nem label XGBoost): China_Drone_000000 (D10, sha256
656c49a3db02…), 000002 (D00, abe99380b95f…), 000012 (D20, 84f37ca4e107…),
000048 (D40, dd3540d4a669…), 000170 (sem anotação, b2e6461b1a5e…).
`OnnxDetector` do Worker, offline, CPU, limiar 0,25: checksum/contrato aceitos;
0/4 classes anotadas encontradas (000002 → D20 0,91), falso positivo D10 0,39 no
negativo. Coerente com VALIDATION mAP50 0,108: não usar como evidência de qualidade.
Aba `#/deteccao-ao-vivo` ganhou "Analisar imagem": prévia visual local de uma
imagem do aparelho no mesmo worker ONNX, com "Localização não informada", sem
envio, Capture, marcador ou "Capturar e registrar" (que regravaria a foto como
câmera, sem EXIF). Registro de imagem da galeria continua em `#/registrar`.

Confianças e contagens devem vir de detecções operacionais persistidas com
model_version, configuração, timestamp e política de seleção. Boxes perfeitas
de anotação não substituem erros, misses e falsos positivos operacionais.
Para dados usados no treino do detector, exigir predições fora do fold/grupo ou
coorte posterior independente. Relatos originados só por revisão humana são
outro estrato; não fabricar Detection/confiança. Viés de seleção dos Events e
missingness dependente do detector devem ser medidos antes de autorizar XGBoost.

#### Descrições e evidência

Inspeção do caminho real: `CoreService.event_dossier` consulta registros
persistidos e monta `ReportInput`; `build_urban_analysis` usa apenas avaliação
persistida Phase 5, emite causas vazias e consequências condicionais, conserva
limitações e exige triagem quando faltam regras. Não há chamada LLM.
Regras ordinais, rótulo visual e confidence não provam profundidade, dimensões
físicas, causa, probabilidade de acidente ou urgência técnica.

Limite encontrado: `ReportInput.evidence/predictions/action.label` aceita texto
livre e o renderer não valida provenance por frase. O chamador atual não fornece
predictions, mas persistência de texto por si só não prova sua validade.
**Bloqueada a aprovação geral de descrições arbitrárias.** Para fechar o gate,
exigir allowlist de afirmações e vínculo com evidência/versão, recusar causas,
medidas físicas, probabilidades e urgência sem mensuração/protocolo independente.
Preservar revisão humana e incerteza. Nenhum código de produção foi modificado.

### Inferencias

As quatro classes RDD são as únicas com dados de detecção e autorização histórica
explícita encontrada; não existe prontidão demonstrada para novo ciclo.
O desbalanceamento de domínios e de negativos é risco de generalização, não
prova causal isolada da falha histórica. Escala pequena pode reduzir informação;
não permite concluir automaticamente erro do anotador.

### Desconhecidos

Exaustividade de cada classe por fonte/imagem; revisão qualificada RDD; grupos
de captura completos; adjudicação de near-duplicates; bytes atuais de toda a
mídia; cadeia completa de autorização protegida; holdout novo independente;
paridade ONNX atual; corpus tabular real e point-in-time de todos os produtores;
suporte empírico para rótulos de risco/severidade/prioridade.

### Blockers

| Prioridade | Estado / escopo | Evidência / impacto | Fechamento mínimo |
|---|---|---|---|
| P1 | OPEN, treino YOLOX | autorização V1 não cobre novo ciclo; revisão/partial labels pendentes | adjudicar cobertura/negativos/caixas e autorizar versão exata |
| P1 | OPEN, avaliação oficial | histórico contaminado, V2 removido, IRD excluído | novo holdout com linhagem e grupos independentes, mantido selado |
| P1 | OPEN, XGBoost | corpus/targets científicos insuficientemente demonstrados | export elegível, auditoria temporal e protocolo de rótulos por alvo |
| P1 | OPEN, treino→Worker | sem paridade numérica atual/quality gate vigente | contrato aprovado e comparação controlada antes de promover |
| P2 | OPEN, descrições | campos livres não vinculam evidência por afirmação | guarda de provenance e testes negativos no caminho persistido |
| P2 | OPEN, inventário | relatórios antigos de readiness divergem da presença atual | refresh seletivo oficial em escopo permitido, sem reabrir TEST |

#### Pré-registro proposto antes de qualquer avaliação final

Este protocolo é preparação documental, não limiar aprovado nem treinamento.

**YOLOX:** apresentar AP50:95 e AP50 por classe e macro, curva PR, recall e
precision no ponto operacional escolhido só em VALIDATION, FP/imagem em negativos
comprovados e suporte por classe/fonte/tamanho. Intervalos por bootstrap de grupos;
sem grupos independentes suficientes, reportar estimativa inconclusiva.
D00/D10: auditar orientação da via e confusão entre classes; não rotacionar rótulos
sem validar semântica. D20: separar malha, remendo e cavidade; D40: estratificar
Norway/escala e confusões D20, sombra, água e tampa. Classe ausente/não exaustiva
não recebe AP zero como se fosse negativa validada.

Critério comum por classe: anotação e suporte aprovados, sem vazamento conhecido,
precision/recall/FP compatíveis com custo operacional declarado e sem regressão
material frente a baseline comparável em amostras nunca vistas por nenhum modelo.
Pisos numéricos de precision/recall, teto de FP, margem de não inferioridade,
número mínimo de grupos e orçamento de latência estão **OPEN para aprovação
humana antes do lock**; não reutilizar thresholds do quality gate removido.
Sem esses valores e protocolo selado não abrir avaliação final. Não escolher
threshold, tamanho ou política D40 pelo TEST histórico.

**XGBoost:** baseline primário é o prior calculado só em TRAIN; comparar também
modelo simples com mesmo alvo e mesmas features, nunca score de risco como rótulo.
Usar validação temporal com isolamento por componentes de Event/Capture/segmento,
cortes fixados antes do ajuste e rótulos disponíveis antes de cada cutoff.
Separar seleção de hiperparâmetros/early stopping, calibração e teste final;
se poucos grupos, bloquear em vez de reciclar holdout. Ajustar imputação e demais
transformações só em TRAIN. Early stopping monitora log loss em VALIDATION,
preserva best_iteration; paciência e orçamento ficam pré-fixados no contrato.
Calibrar em coorte separada (método escolhido antes de olhar TEST), reportar
Brier, log loss, PR-AUC, confiabilidade e suporte por classe/fonte/model_version,
com intervalos agrupados. Exigir ganho sobre prior e custo operacional aprovado;
accuracy isolada não autoriza promoção. Gate e harness continuam bloqueados.

#### Verificação e pesquisas

Comandos executados em `backend/`, com `python=.venv/Scripts/python.exe`:

```text
python -B -m pytest tests/test_tabular_phase8.py tests/test_features.py tests/test_report.py tests/test_ml_taxonomy.py tests/test_ml_splits.py -q -p no:cacheprovider
# exit 0: 86 passed, 2 warnings de depreciação
python -B -m pytest tests/test_detection_dataset.py tests/test_detection_manifest_builder.py tests/test_univali_guards.py tests/test_univali_semantics.py tests/test_urban_community.py tests/test_readiness_cloud_only.py tests/test_history_phase9.py tests/test_worker_model_resolution.py tests/test_yolox_evaluator.py -q -p no:cacheprovider
# exit 0: 298 passed, mesmos 2 warnings
python -B -m ruff check app/ml/tabular.py app/ml/detection_dataset.py app/ml/serving.py app/services/features.py app/services/report.py tests/test_tabular_phase8.py tests/test_detection_dataset.py tests/test_report.py
# exit 0
python -B -m mypy app/ml/tabular.py app/services/features.py app/services/report.py --follow-imports=silent
# exit 0: 3 módulos, não é typecheck integral
git diff --check
# exit 0; apenas aviso Git de conversão LF/CRLF
```

| Check obrigatório | Nível / resultado | Escopo e limite |
|---|---|---|
| Focados e suíte relevante | unit/integration local, pass | 384 testes; fixtures não provam Supabase/corpus real |
| Lint/static | inspection, pass | arquivos listados; alterações apenas documentais |
| Manifestos/registry/hashes | inspection, parcial | hashes seguros acima; TEST e cadeia global unverified |
| Produtores/consumidores | inspection, pass no recorte | builder/conversores/loader/core existentes; sem nova pipeline |
| Fail-closed | unit, pass | hash stale, cloud-only, papel protegido, labels sem Review e autorização ausente recusados |
| Orphans/entrypoints | inspection, sem alteração de código | geração completa não executada por envolver papéis protegidos |
| Valores hardcoded | inspection, pass como identificação | 640/ordem classes são contrato; thresholds removidos não restaurados |
| Cloud/serviços | unverified | sem export Supabase, persistência real não revalidada |
| Mock fidelity | gap registrado | testes não demonstram corpus, modelo promovido ou precisão real |
| Pipeline real | unverified | treino, inferência e Frozen Test não executados |
| Storage | inspection por stat | tetos individuais raw acima; novo preflight obrigatório para derivações |

Reprodução das contagens sem abrir mídia ou TEST, da raiz:

```python
import sys, json, collections, statistics
from pathlib import Path
sys.path.insert(0, 'scripts/datasets')
from _core import require_local, file_sha256
for role in ('train', 'validation'):
    p = Path(f'datasets/manifests/detection_{role}_authorized.jsonl')
    rows = [json.loads(x) for x in require_local(p).read_text().splitlines()]
    print(role, file_sha256(p), len(rows), sum(not r['boxes'] for r in rows))
    print(collections.Counter(b['canonical_class'] for r in rows for b in r['boxes']))
    if role == 'train':
        areas = []
        for r in rows:
            for b in r['boxes']:
                if 'Norway' in r['image_path'] and b['original_class'] == 'D40':
                    x1, y1, x2, y2 = b['bbox']
                    ratio = min(640/r['image_width'], 640/r['image_height'])
                    areas.append((x2-x1)*(y2-y1)*ratio**2)
        print(len(areas), statistics.median(areas), sum(a < 32**2 for a in areas))
```

Pesquisas consultadas em 25/09/2026: [YOLOX, implementação fixada pelo projeto](https://github.com/Megvii-BaseDetection/YOLOX/blob/6ddff4824372906469a7fae2dc3206c7aa4bbaee/yolox/data/data_augment.py)
para conferir pré-processamento; [XGBoost, interface sklearn/early stopping](https://xgboost.readthedocs.io/en/stable/python/sklearn_estimator.html)
para distinguir eval_set/early stopping de simples fit. A documentação externa
não autoriza datasets. As decisões científicas acima são propostas locais e
dependem dos gates humanos, não são resultados dessas bibliotecas.

### Decisao conservadora

`training_authorized=nao autorizado` para novo ciclo YOLOX/XGBoost;
`official_evaluation_authorized=nao autorizado`. Aprovação histórica MODEL V1
preservada, sem extensão de escopo. Nenhuma nova classe declarada treinável.
TRAIN/VALIDATION existentes servem somente à conferência documental permitida;
TEST/EXTERNAL_TEST permanecem protegidos. Próximo passo: adjudicação humana de
anotações/cobertura e desenho de holdout independente, seguida de export tabular
e auditoria point-in-time do corpus real. Não executar treino para contornar gates.

**Arquivos alterados:** este estado canônico, aditivo D40 e PROJECT_STATE.
**Gates:** contratos locais testados closed/passed; prontidão científica open/blocked.
**Testes:** 384 passaram; lint/typecheck focados passaram; avaliação real não executada.
**Pendências:** revisão humana, labels, novo split/holdout, critérios numéricos e paridade.
**Riscos restantes:** anotações parciais, grupos desconhecidos, viés do detector/revisão,
provenance de texto livre e incompletude temporal. **VERDICT=BLOCKED.**

Atualizado em 2026-09-12. GB decimais (1 GB = 1.000.000.000 bytes).

As oito fontes estão presentes com dado real e somam **35,29 GB** dos 40 GB
disponíveis. Sete cabem no teto de **7 GB por dataset**; o RDD2022 é uma **exceção
autorizada** e está explicado abaixo. Todas são lidas pelo backend:
`python -m app.datasets.cli inventory` e `... read <fonte>` funcionam para as oito.

| Fonte | Papel | Em disco | Conteúdo | Integridade |
|---|---|---|---|---|
| `rdd2022` | treino V1 | 13,67 GB | 38.217 imagens com XML VOC em 7 origens; 4 classes de dano | MD5 oficial do ZIP + CRC dos 85.805 extraídos |
| `rampnet` | referência | 6,84 GB | panorâmicas + keypoints + coordenada real de rampa, em Parquet | SHA-256 oficial por shard |
| `global_streetscapes` | referência | 6,07 GB | 15.007 imagens street-level, 108 países, rótulo humano de cena | SHA-256 local por imagem (a fonte não publica por tarball) |
| `project_sidewalk` | referência | 5,36 GB | recortes de panorâmica + keypoints de rampa, em Parquet | SHA-256 oficial por shard |
| `urban_community` | treino V1 | 1,49 GB | 2.518 imagens em 7 pastas de classe (formato YOLO) | SHA-256 apenas local (a fonte não publica) |
| `bdd100k` | referência | 1,27 GB | 10.000 imagens de condução + mapas de segmentação, em ZIP | CRC do ZIP + SHA-256 local; **não há checksum oficial válido** |
| `univali_br` | treino V1 | 0,48 GB | 2.235 amostras com máscara, rodovias federais brasileiras | SHA-256 oficial |
| `camber` | referência | 0,10 GB | CSV de detecções, GPS e rota de um registro Zenodo | MD5 oficial dos anexos; mídia externa sem checksum publicado |

## O que mudou nesta etapa

**RDD2022 fica como exceção acima do teto, por decisão do usuário.** A poda da origem
Norway foi construída, executada e validada: manter 100% das 2.914 imagens com defeito e
amostrar as negativas em 66 estratos de resolução × trecho de rota × faixa de iluminação,
com k-medoids sobre brilho, contraste, saturação e proporção de área clara/escura, e
descarte de quase-duplicatas por dHash. O resultado medido foi 6,49 GB com **as quatro
classes intactas** e zero órfãos — está em `reports/rdd2022_reduction.json`.

O que impediu a poda de valer não foi a seleção, e sim o OneDrive: a proteção de exclusão
em massa (limiar padrão de 200 itens) restaura os arquivos da nuvem enquanto espera uma
confirmação do usuário. Isso aconteceu três vezes, inclusive com o cliente parado durante a
remoção — ao voltar, ele reconcilia tratando a nuvem como verdade. O usuário optou por
manter o RDD2022 como está em vez de mexer na política da máquina.

Estado atual do RDD2022, verificado:

- 38.217 amostras lidas pelo loader, em 7 origens;
- objetos por classe: D00 26.016, D10 11.830,
  D20 10.616, D40 6.544 — **completos**;
- 0 XML órfão e 0 imagem sem XML;
- 168 imagens da Norway não voltaram da restauração parcial do OneDrive. **Todas são
  negativas**: nenhum objeto anotado se perdeu. Contabilizado em
  `reports/rdd2022_norway_partial_restore.json`.

Os manifestos `manifests/rdd2022_norway_kept.jsonl` e `_removed.jsonl` e o script
`reduce_rdd2022.py` continuam prontos: se um dia a confirmação do OneDrive for dada, a poda
se aplica de novo e reproduz exatamente a mesma seleção, porque ela sai do perfil gravado e
não do disco do momento.

**Mapillary MSLS substituído pelo Global Streetscapes.** O portal oficial do MSLS exige
login e o projeto não tem credencial nem pode usar serviço pago. O Global Streetscapes
(NUS Urban Analytics Lab) publica no Hugging Face, sem gating e sob CC BY-SA 4.0, imagem
street-level do **próprio Mapillary** (14.610 das
15.007 selecionadas) e do KartaView (397).
É a via pública e gratuita para a mesma imagem, e ainda acrescenta rótulo humano de clima,
iluminação, plataforma da via e qualidade, que o MSLS não oferecia. O recorte cobre
108 países, 398 cidades e
1.946 sequências de captura. Motivo e alternativas avaliadas em
`metadata/removed_sources.json`.

**Quatro fontes saíram de `DEFERRED` e ganharam adaptador.** Antes desta etapa o catálogo
descrevia o Project Sidewalk como exportação CSV da API municipal — o que foi baixado é
Parquet com imagem embutida — e listava `rampnet` e `bdd100k` como adiados enquanto os dois
já estavam em disco. O sistema não conseguiria ler nenhum dos três. Agora:

- `project_sidewalk` e `rampnet` leem Parquet com a imagem dentro da linha, sem extrair;
  `extract_image` devolve o JPEG de uma linha citada quando alguém precisa dele;
- `rampnet` separa o ponto na imagem (`KeypointSample`) da coordenada no mundo (`GeoRecord`);
- `bdd100k` pareia imagem e máscara lendo os dois ZIPs **fechados**, sem duplicar 1,27 GB;
- `global_streetscapes` lê o CSV de rótulos gerado na aquisição.

`KeypointSample` é novo em `records.py`: ponto não é caixa, pelo mesmo motivo que máscara
não é. Nenhuma dessas quatro fontes vira classe da V1 — todas registram a recusa com motivo.

**Splits e manifesto de seleção reconciliados com o disco.** Os três arquivos de
`datasets/splits/` citavam imagens da Norway que não existem mais — referência quebrada que
só apareceria no próximo passo. `reconcile_after_reduction.py` remove as linhas órfãs sem
mover nenhuma imagem de lado: os 34.526 objetos de `train` continuam lá, o isolamento por
país foi reconferido e passou, e as proporções ficaram em 67,5 / 10,9 / 21,6 — praticamente
as que a busca original alcançou mantendo países inteiros. Evidência em
`reports/rdd2022_reconciliation.json`.

**Organização do repositório corrigida.** Existiam dois ambientes virtuais (`.venv` na raiz
e `backend/.venv`) e dois caches de lint. As bibliotecas que só o ambiente da raiz tinha
(`huggingface_hub`, `hf_xet`, `tqdm`) foram instaladas no `backend/.venv`, que passou a ser
o único, e as duplicatas saíram — 247 MB. Junto com elas saíram `datasets/raw/mapillary_msls`
(fonte aposentada) e `datasets/downloads/` (cache que o script rebaixa sozinho). Também foi
removido o BOM UTF-8 de sete arquivos: os três manifestos `manifests/*.json` estavam
**ilegíveis** por `json.load` por causa dele, e o `.gitignore` tinha o BOM colado na primeira
linha.

## O que continua valendo como limitação

- **BDD100K não tem checksum oficial utilizável.** O MD5 publicado na documentação tem 31
  dígitos hexadecimais e é inválido. Há CRC do ZIP e SHA-256 local, o que prova integridade
  do arquivo, não autenticidade da origem.
- **Urban Community** declara CC0 na ficha do Kaggle, mas a procedência das imagens não é
  declarada, e o mapa id→classe foi inferido das pastas.
- **CAMBER**: as detecções do CSV vêm de um YOLO de terceiros com `user_confirmed` vazio.
  É saída de modelo, não ground truth.
- **CC BY-SA 4.0** do Global Streetscapes obriga atribuição e mesma licença em derivados.
- Nenhuma fonte tem classe de calçada ou de acessibilidade na taxonomia §8.2. Rampa entra
  como keypoint recusado, não como classe de treino.
- **RDD2022 acima do teto de 7 GB**, por decisão registrada. As outras sete fontes estão
  dentro dele.
- **Presença e integridade de dados não demonstram desempenho de identificação.** Não houve
  treino nem inferência nesta etapa.

## Exclusão dentro do OneDrive

O projeto fica em pasta sincronizada e isso não é detalhe: apagar milhares de arquivos de
uma vez **é revertido**. A proteção de exclusão em massa restaura tudo da nuvem enquanto
espera confirmação, e parar o cliente também não resolve — ao voltar, ele reconcilia
tratando a nuvem como verdade e baixa tudo de novo. Os dois comportamentos foram observados
aqui. Lotes de algumas centenas passam abaixo do limiar e propagam normalmente, então
`reduce_rdd2022.py` apaga em lotes de 150, pausa e reconfere no fim, removendo de novo o que
a sincronização tiver trazido de volta — o que resolve exclusões pequenas, mas não venceu as
12 mil do RDD2022. Medir espaço nessa pasta exige o mesmo cuidado: o tamanho de um
placeholder é o da nuvem, não o do disco.

Para aplicar uma exclusão grande de fato é preciso uma destas duas coisas: confirmar o aviso
do próprio OneDrive na barra de tarefas, ou elevar
`HKCU\SOFTWARE\Policies\Microsoft\OneDrive\LocalMassDeleteFileDeleteThreshold`. Nenhuma
das duas foi feita aqui.

## Reproduzir

```text
python -B scripts/datasets/profile_norway.py
python -B scripts/datasets/reduce_rdd2022.py              # dry-run
python -B scripts/datasets/reduce_rdd2022.py --execute
python -B scripts/datasets/acquire_global_streetscapes.py # dry-run
python -B scripts/datasets/acquire_global_streetscapes.py --execute
python -B scripts/datasets/reconcile_after_reduction.py --execute
cd backend && python -m app.datasets.cli inventory
```

A seleção da poda vem do perfil gravado e da linha de base em
`reports/rdd2022_pre_reduction_baseline.json`, não do disco no momento da execução — por
isso ela é a mesma em qualquer reexecução, mesmo com a sincronização mexendo nos arquivos.
