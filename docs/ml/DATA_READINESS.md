# Preparação do próximo ciclo visual

## Run YOLOX-S com limite de 10 horas — 25/09/2026

O usuário limitou o treinamento a 10 horas. O run anterior de 50 épocas foi encerrado após cerca de 2.500 iterações da primeira época; preservou logs e smoke, mas ainda não tinha checkpoint principal. Uma primeira tentativa de contrato reduzido falhou antes de treinar por campo de metadata não aceito; a cópia do contrato falho está no diretório da tentativa. O contrato corrigido `datasets/metadata/yolox_model_experimental_20260925_10h.json` mantém o mesmo TRAIN/VALIDATION, D00/D10/D20/D40, YOLOX-S, pesos oficiais COCO, batch 1, resolução 640×640 e zero workers. Define 6 épocas, warmup 1, no-aug 1 e validação ao final. O launcher tem corte automático aos 35.100 segundos (9h45), incluindo preparação e smoke, antes do limite de 36.000 segundos.

O novo run `yolox-s-rdd4-experimental-20260925-10h-r1` passou no smoke e iniciou treino principal com atualização de pesos confirmada. Prazo absoluto registrado: 26/09/2026 01:06:34 UTC (25/09/2026 22:06:34 em São Paulo). Estado e heartbeat: `models/checkpoints/experimental_20260925_rdd4_10h_r1/run_state.json`; log principal: `train.stderr.log` no mesmo diretório. O checkpoint principal será salvo ao fim de cada época; ainda não havia um na primeira verificação. O preflight deste novo run mediu 2.008.862.720 bytes livres após importar PyTorch, acima do piso cauteloso de 2.000.000.000 bytes; a RAM livre durante o treino oscila e continua sendo monitorada no heartbeat. A flag de override permanece registrada por pedido do usuário, mas não foi necessária para passar o piso nesta tentativa. Nenhum TEST/Frozen Test foi aberto e o modelo continua experimental, sem promoção. As seções abaixo registram etapas anteriores.

Atualização 26/09/2026: o run terminou `COMPLETED` (exit 0, 6 épocas; validação só ao final, logo best = last). Ver `docs/PROJECT_STATE.md`.

## Treinamento experimental em andamento — 25/09/2026

O usuário autorizou iniciar com a RAM atual. O piso cauteloso de 2.000.000.000 bytes foi mantido; o preflight após PyTorch mediu 1.374.654.464 bytes e o launcher registrou `user_low_ram_override=true`. O smoke YOLOX-S passou com uma iteração, loss finita, atualização real de pesos, uso de GPU e checkpoint recarregável. O launcher iniciou o run principal `yolox-s-rdd4-experimental-20260925` a partir dos pesos oficiais COCO; a primeira atualização do otimizador e centenas de passos foram observados no log. A execução permanece **EXPERIMENTAL / NOT_APPROVED_FOR_SERVING**. Acompanhar `models/checkpoints/experimental_20260925_rdd4/run_state.json` e `train.stderr.log` no mesmo diretório. Não iniciar outra instância. O TEST/Frozen Test não foi aberto; o novo holdout V3 continua pendente. O histórico abaixo descreve checkpoints anteriores a este início.

Atualização 26/09/2026: este run terminou `FAILED` (`training_exit_code` 4294967295); só `smoke.pt` existe.


## Run experimental de quatro classes autorizado em 25/09/2026

O pedido atual do proprietário autoriza **treino experimental**, separado de aprovação científica V3 e de promoção para produção. O contrato [yolox_model_experimental_20260925.json](../../datasets/metadata/yolox_model_experimental_20260925.json) reutiliza o treinador YOLOX-S de quatro classes na ordem D00/D10/D20/D40, com pesos COCO oficiais e `batch=1`, `validation_batch=1`, `workers=0`, `pin_memory=false`, AMP e 50 épocas. A LR alvo escala com batch 1 para 0,00015625; o início do warmup de três épocas é 0,00001 para que os primeiros passos realmente atualizem pesos. O smoke encurta apenas seu relógio de warmup, sem alterar o cronograma do run principal. O DatasetVersion V3 continua DRAFT e as outras 31 categorias permanecem no escopo do produto.

O produtor canônico derivou 23.827 imagens TRAIN/34.523 caixas do manifesto RDD2022 autorizado historicamente, retirando três imagens da Índia declaradas ambíguas no pacote humano de oito. VALIDAÇÃO histórica contém 3.858 imagens. O produtor verificou ausência de sobreposição por caminho/hash/grupo de país com os manifests VALIDATION e TEST, sem abrir imagens nem avaliador de TEST. A revisão humana das oito amostras não foi extrapolada para a fonte inteira; o uso experimental é autorizado pelo pedido atual, sem declarar uso operacional aprovado. Ver [relatório derivado](../../datasets/reports/detection_experimental_20260925.json).

Preflight desta execução: a RAM livre após importar PyTorch oscilou abaixo do piso de 2.000.000.000 bytes; o valor mais recente e seu instante estão em `datasets/reports/ml_preparation_state.json`. **Smoke e treino principal ainda não iniciados** neste checkpoint. Solicitado ao usuário salvar o trabalho e fechar apenas aplicativos não essenciais, sem interromper o Worker. Próximo ponto: repetir preflight, executar `scripts/datasets/run_yolox_experimental.py` somente se o piso passar; o helper verifica atualização de parâmetros e checkpoint no smoke e então inicia um único run principal a partir dos pesos COCO originais.

O conversor COCO V3 foi reforcado: recusa placeholders de grupo, exige evidencia de grupo e auditoria de quase duplicatas marcadas VERIFIED, e impede que o mesmo grupo/cluster atravesse TRAIN e VALIDATION. Isso nao certifica os grupos atuais; nenhuma imagem foi convertida.

## Preparacao sem aprendizado ? 25/09/2026

Nesta execucao, o pedido vigente proibiu smoke training, backward, optimizer.step e qualquer fit. Nenhum deles foi executado. O CLI `app.ml.training --dry-run` executa passos de treino no codigo atual; portanto nao e um preflight seguro e nao foi usado. O unico comando executado para hardware e estado foi `backend\.venv\Scripts\python.exe -B scripts\datasets\preflight_training.py --write`.

O import das oito decisoes segue imutavel: cinco aprovadas cobrem D00/D10/D20/D40 com cobertura explicita para as quatro classes; tres ambiguas ficam excluidas. O grupo bruto `UNCONFIRMED` foi preservado. O validador agora aceita grupo vazio ou placeholder e deriva `UNCONFIRMED`; um grupo preenchido pelo revisor recebe `REPORTED_UNVERIFIED`, nunca prova isolamento de cena. O HTML historico pinado foi preservado para manter a identidade do pacote ja revisado; o template corrigido vale para pacotes futuros.

O preflight mais recente esta registrado em `datasets/reports/ml_preparation_state.json`; a RAM livre apos importar PyTorch ficou abaixo do piso cauteloso de 2 GB. O piso cauteloso de 2 GB nao foi atingido. Nenhum processo do usuario foi encerrado; nao havia processo Python do Worker identificado. A memoria de treino continua nao medida.

Dependencias locais opcionais do XGBoost foram instaladas nas versoes fixadas em `backend/pyproject.toml`: xgboost 3.4.1, scikit-learn 1.9.1 e shap 0.52.0; `pip check` passou. O protocolo de rotulos e export point-in-time foram testados sem fit. Nenhum corpus tabular elegivel exportado foi encontrado nos artefatos locais; nao inferir contagem zero no banco remoto. Pipeline tabular continua parcial para early stopping, calibracao e registro cientifico; dados ainda nao prontos.

Sem grupo de cena ou quase duplicatas adjudicadas, autorizacao especifica V3, contrato de consumidor V3, corpus e splits elegiveis, e novo holdout independente, DatasetVersion permanece DRAFT. Nenhum comando de smoke/treino principal e apresentado como executavel. O preflight pode ser repetido apos o usuario salvar trabalho e fechar aplicativos nao essenciais; isso nao garante memoria suficiente.

**Verificacao desta etapa:** 54 testes focais de revisao/preflight/manifestos/splits e 22 tabulares sem fit passaram; testes que chamam treino/fit foram excluidos pelo pedido. Ruff, mypy e registro de artefatos sao validados ao encerrar. Veredicto: BLOCKED para autorizacao de smoke e treino longo.


## Retomada mínima do smoke D00/D10/D20/D40 — 25/09/2026

**Escopo:** quatro classes candidatas para teste técnico; as 35 categorias da
taxonomia v3 permanecem no produto. O pacote de oito imagens RDD Índia/Japão
provém exclusivamente do TRAIN histórico, com hashes de imagem/XML/manifestos
conferidos. Sua saída é revisão humana, não um DatasetVersion autorizado.
`review_6225e89cfebf.html` é local, ignorado e pinado no contrato de artefatos.
O formulário exige estado PRESENT_ANNOTATED, ABSENT_REVIEWED, NOT_ANNOTATED ou
AMBIGUOUS para cada uma das quatro classes por imagem. `NOT_ANNOTATED` jamais
vira negativo. Oito decisões foram importadas; cinco aprovam semântica, três
declaram ambiguidade e nenhuma imagem virou TRAIN V3. O JSON original permaneceu
em `Downloads/annotation_review_decisions.json` (7.377 bytes; SHA-256
`8cb075492cbbb9a25e868b3b07f8a7726cdadb237dbdb93d5adf8026fc3c40f5`).
O import imutável é `datasets/processed/annotation_review/decisions_e446e5be364fe8f2.json`.
Todos os grupos informados são `UNCONFIRMED`, que não identifica cena.

| Dimensão | Estado atual | Fechamento necessário |
|---|---|---|
| Software | Parcial: treinador seleciona contrato, mas loader/evaluator/ONNX e Worker V3 pendentes | ordem aprovada, testes de índices/checkpoint e paridade real |
| Dados smoke | D00: 2 aprovações; D10/D20/D40: 1 aprovação e 1 ambiguidade cada; zero autorizações V3 | usar somente cinco aprovadas após demais gates; três ambíguas excluídas |
| Partição | TRAIN histórico identificado; grupo por país não prova cena nem ausência de quase duplicatas | adjudicar grupo e impedir cruzamento de identidades protegidas |
| Holdout | TEST histórico V1 preservado; novo V3 ausente | criar protocolo independente antes do treino longo, sem abrir Frozen Test |
| Hardware | RAM livre observada 0,407/0,297 a 2,861/2,306 GB antes/depois de PyTorch; VRAM livre 4.915–4.928 MiB | consultar checkpoint para última medição; ainda sem pico de treino |
| Smoke | Zero iterações, zero updates, picos não medidos | executar até 100 iterações ou 10 min somente após todos os gates |

| Classe | Revisão do lote | Procedência/licença | Grupo | Uso V3 |
|---|---|---|---|---|
| D00 | 2 aprovadas | Figshare v1 / CC BY 4.0 verificados | 2 `UNCONFIRMED` | não autorizado |
| D10 | 1 aprovada; India ambígua excluída | Figshare v1 / CC BY 4.0 verificados | 1 candidata `UNCONFIRMED` | não autorizado |
| D20 | 1 aprovada; India ambígua excluída | Figshare v1 / CC BY 4.0 verificados | 1 candidata `UNCONFIRMED` | não autorizado |
| D40 | 1 aprovada; India ambígua excluída | Figshare v1 / CC BY 4.0 verificados | 1 candidata `UNCONFIRMED` | não autorizado |

As cinco imagens aprovadas têm estado explícito para as quatro classes.
O preflight recusa qualquer aprovação com outra classe `NOT_ANNOTATED` ou
`AMBIGUOUS`. Nenhuma das três imagens ambíguas entra no subconjunto candidato.
Sem grupo de cena verificável, nem mesmo o subconjunto de cinco recebe papel
TRAIN V3. Country group do V1 não substitui cena/sequência.

Das 130 propostas amplas, zero bloqueiam diretamente este subconjunto.
24 Norway D40 interessam ao possível treino RDD mais amplo; 106 são futuras
para este escopo. Oito novas revisões de completude são necessárias para os
exemplos propostos. Auditoria representativa da fonte e autorização do novo
ciclo seguem separadas; revisar oito casos não libera todo RDD. O registro
Figshare v1 confirma CC BY 4.0 e MD5 oficial `b62bd51d2ffcfaa76c60f234f0cc2bb3`;
o snapshot local e o relatório de extração/release vinculam imagens e XMLs.
Proveniência e licença da fonte passam para este lote, com atribuição CC BY.
Histórico V1 não prova nova autorização V3.
As 461 caixas Norway (430 pequenas) foram preservadas. O carregador de pesos
agora recusa ausência de parâmetros fora da cabeça de classificação e registra
SHA-256 local. O checkpoint local `models/pretrained/yolox_s.pth` carregou
456 parâmetros compatíveis no YOLOX-S real; seis parâmetros da cabeça de
classificação ficaram inicializados novamente, zero pesos não esperados.
SHA-256 local: `f55ded7181e1b0c13285c56e7790b8f0e8f8db590fe4edb37f0b7f345c913a30`.
Streaming da [URL oficial do release YOLOX-S](https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_s.pth)
em 25/09/2026 produziu os mesmos 72.089.125 bytes e SHA-256 do arquivo local.
Origem oficial por comparação byte a byte confirmada; não foi encontrado SHA-256
publicado separadamente pelo autor. Nenhum checkpoint UrMind rejeitado foi usado.

**Retomada:** preservar as três decisões `AMBIGUOUS` fora do smoke. Confirmar
grupo real das cinco imagens candidatas, revisar isolamento e autorizar
explicitamente o lote V3 antes de criar split.
Concluir consumidores V3 e repetir preflight. Sem essas evidências, não executar
smoke nem emitir comando de treino longo.

### Evidência e verificação desta retomada

**Fatos.** O pacote fixado contém oito imagens TRAIN com hashes atuais; oito
decisões humanas importadas, cinco aprovações e três ambiguidades. O checkpoint COCO local foi carregado no YOLOX-S real:
456 parâmetros carregados, seis da classificação não carregados, nenhum
parâmetro inesperado. O TEST histórico não foi aberto. Preflight mais recente,
incluindo bloqueios e RAM, está em `ml_preparation_state.json`.

**Inferências.** O subconjunto evita depender de Urban para o primeiro teste
técnico. A licença do release RDD cobre o lote vinculado, mas não concede
autorização científica V3 nem prova qualidade estatística ou outras 31 categorias.

**Desconhecidos.** Três rótulos ambíguos, grupos de cena, quase duplicatas,
autorização específica V3 e pico de memória do otimizador. Nenhuma dessas
lacunas foi convertida em PASS.

**Blockers.** Para smoke: autorização V3, grupos/partição, contrato
loader/evaluator V3 e
memória segura. Para treino longo: corpus e validação próprios, novo holdout V3
e paridade ONNX/Worker. Para avaliação oficial: manter Frozen Test fechado até
protocolo e independência comprovados.

**Decisão conservadora.** `training_authorized=false` e
`official_evaluation_authorized=false` para `urmind-urban-vision-v3-DRAFT`.
Os oito registros são propostas locais, sem papel TRAIN V3 autorizado.

**VERIFICATION_VERDICT=BLOCKED** para o smoke real; os testes de software
abaixo passaram nos caminhos especificados.

| Verificação | Evidência | Resultado e limite |
|---|---|---|
| Testes focais e regressão | pytest review, preflight, trainer: 61; suite de detector: 141 | PASS, unit/integration; sem treino |
| Lint, formato e tipos | Ruff check/format dos seis Python afetados; mypy trainer | PASS |
| Manifestos, hashes, registro | pacote HTML pinado, XML/imagem/source; refresh/check `--only` | PASS para artefatos consultados; outros não revalidados |
| Produtor/consumidor | `review_annotations.py --build --smoke-rdd` e `preflight_training.py --write` executados | PASS para revisão/checkpoint; loader V3 ausente |
| Falha fechada | testes de cobertura parcial, decisões duplicadas e peso incompleto | PASS unit; autorização V3 permanece bloqueada |
| TEST e bypass | seleção lê TRAIN; nenhum TEST ou inferência protegida | PASS no caminho executado; near-duplicates não adjudicados |
| Nuvem e mocks | sem download/cloud, sem backend público | não aplicável para teste local; origem oficial do peso não verificada |
| Pipeline real | oito imagens embutidas e peso local carregado; zero optimizer step | PASS apenas para pacote/pretrained, smoke BLOCKED |

Revisão independente focada não encontrou defeito na seleção, hashes ou
importação; não executou navegador nem inferência. `git diff --check` e o
registro escopado são conferidos ao encerrar. Correção de revisão autorizada
pelo usuário permanece local; nenhuma decisão humana foi produzida pelo agente.

## Continuação do preflight e código — 25/09/2026

Medições locais no checkpoint: 16,87 GB RAM total; disponível antes/depois do
import PyTorch variou de 1,68/1,20 GB a 1,30/0,90 GB; RSS do próprio preflight
23–28/511–512 MB. `nvidia-smi`
reportou 6.141 MiB totais e 4.917 MiB livres na RTX 4050 Laptop; torch ainda
alocava/reservava 0. Disco livre ~130 GB. O piso de 2 GB para iniciar loader e
otimizador permanece cauteloso; não é garantia de pico seguro. Sem treino, picos
de RAM/VRAM de YOLOX são **não medidos**, e throughput/duração do run longo não
podem ser estimados honestamente. O usuário foi solicitado apenas a fechar
aplicativos não essenciais depois de salvar o trabalho; nenhum processo foi morto.

`preflight_training.py` agora registra RAM antes/depois de carregar PyTorch,
VRAM total/livre/usada, RSS, disco, tamanhos e resoluções dos índices históricos
TRAIN/VALIDATION por streaming, perfil conservador e blockers exatos. Não abre
Frozen Test nem decodifica imagens. Nos manifests históricos, TRAIN tem
23.830 imagens/34.526 caixas em 17,3 MB e VALIDATION 3.858/7.718 em 3,1 MB;
não são um DatasetVersion V3 autorizado. Loader lê imagem sob demanda, fecha o
arquivo após conversão, mas mosaico/mixup e originais de 4040×2035 exigem
medição de pico quando o gate permitir. O perfil proposto usa uma GPU, batch 1,
VALIDATION batch 1, workers 0, `persistent_workers=false`, `pin_memory=false`,
prefetch ausente, 640² e sem `--occupy`; AMP depende de teste de estabilidade.

Corrigido defeito concreto no trainer único: `--contract` chega à fábrica do
modelo, checkpoints comparam a ordem do contrato selecionado e o batch de
VALIDATION pode diferir do batch de TRAIN. O caminho V1 continua compatível.
**TRAINER_V3_STATUS=PARTIAL**: contrato/classe autorizada V3 não existe, loader e
avaliador ainda fixam mapeamento V1 e a paridade ONNX/Worker não foi validada.
Não apresentar o comando de treino longo como executável até esse fechamento.
Zero smoke iterado: faltam TRAIN e split V3 autorizados, classe aprovada e RAM
acima do piso; o próprio contrato do trainer recusa a inicialização. O pacote
HTML existente conserva SHA-256 pinado e o importador valida versão/identidade,
hash e idempotência; nenhuma decisão foi importada nesta retomada.

XGBoost permanece independente: export point-in-time, formulário por alvo,
baseline e validação técnica estão preparados; dependências locais não estão
instaladas nem há labels reais suficientes. Não houve treino tabular. Próxima
ação humana consolidada: revisar o HTML e entregar o export JSON, além de
adjudicar direitos por imagem/autorização do novo ciclo; fechar aplicativos
não essenciais apenas se for seguro e avisar para repetir o preflight.

Este documento é o índice operacional da preparação, não uma autorização. As decisões
por categoria vivem nos relatórios gerados, sem nova taxonomia ou pipeline paralelo.
Versão consultada: `urmind-issue-taxonomy-v3`; 35 categorias preservadas.

## Execução técnica do ciclo V3 — 25/09/2026

**Fatos.** A branch local `ml/urmind-training-prep` preserva o trabalho anterior. O
produtor vinculou 80/130 propostas ao registro de release oficial, manifesto com
checksum verificado, hash da imagem e hash da anotação/máscara; 50 Urban continuam
sem origem e direitos por imagem. Essa ligação é rastreabilidade da fonte, não
aprovação semântica, licença individual ou autorização do novo ciclo. A fonte
[RDD2022 no Figshare](https://figshare.com/articles/dataset/RDD2022_-_The_multi-national_Road_Damage_Dataset_released_through_CRDDC_2022/21431547)
declara CC BY 4.0 e uso em detecção; a ficha oficial da
[UNIVALI v4 no Mendeley](https://data.mendeley.com/datasets/t576ydh9v8/4)
declara CC BY 4.0, 2.235 imagens, máscaras e origem DNIT. Páginas consultadas em
25/09/2026. Os hashes locais provam integridade dos bytes presentes; não resolvem
direitos de terceiros nem a autorização científica do novo ciclo.

O importador de revisão usa o pacote HTML pinado de 130 casos, valida o export e
grava uma cópia derivada imutável e idempotente em
`datasets/processed/annotation_review/decisions_<hash>.json`. Ele mantém estados
separados para semântica, procedência, licença e autorização. Nenhuma decisão
humana foi recebida. Uso: abrir
`datasets/processed/annotation_review/review_1b00e44bcd58.html`, avaliar as
imagens/caixas/máscaras e completude, preencher revisor, grupo e justificativa,
exportar `annotation_review_decisions.json` para dentro do workspace, executar
`backend/.venv/Scripts/python.exe -B scripts/datasets/review_annotations.py --validate <arquivo>`
e então `--import-decisions <arquivo>`. Aprovar uma linha não autoriza treino.
Um parent Urban desatualizado impede aprovação até reconciliação canônica.

`build_detection_manifests.py --generation v3-draft` gerou a versão
`urmind-urban-vision-v3-DRAFT`: 130 candidatos em quarentena, 80 com evidência de
release ligada, zero elegíveis, `CLASS_ORDER=[]`, sem TRAIN/VALIDATION final e sem
abrir Frozen Test. O conversor `eligible_rows_to_coco` foi implementado no
produtor existente. Ele exige autorização exata, direitos/procedência verificados,
grupo, fingerprints, caixas válidas e cobertura explícita de toda classe ativa.
`NOT_ANNOTATED` e label vazio nunca viram negativo. Testes sintéticos verificam
o contrato; nenhuma conversão científica foi executada com propostas pendentes.

`preflight_training.py --write` gerou
`datasets/reports/ml_preparation_state.json` com etapa, hashes de entrada,
resultado e próxima ação. Python 3.12.10, PyTorch 2.11.0+cu128, submódulo YOLOX
`6ddff482`, RTX 4050 Laptop 6 GB e CUDA estavam presentes. Na medição,
RAM disponível variou de 0,34 a 0,90 GB, abaixo do piso técnico cauteloso de
2 GB para tentar loader/optimizer. Disco livre ~130 GB; o limite científico
global de dados continua separado do espaço livre do volume. O treinador
`app.ml.training --dry-run` exige o split autorizado via `validate_readiness`.
No V3 ainda não há TRAIN autorizado nem holdout. Resultado: **0 iterações**;
nenhum smoke real, treino longo, avaliação protegida ou checkpoint novo.

O export tabular já exige snapshot anterior à primeira revisão e Review
persistida, com separação por grupo/tempo. O formulário de alvos continua
independente: `review_confirmed`, severidade, risco, prioridade e recorrência
não compartilham Ground Truth. As dependências locais futuras foram fixadas em
`backend/pyproject.toml` no extra `tabular-ml`; não foram instaladas por falta de
RAM disponível para validar o ambiente nesta execução. Não há labels reais
suficientes, treino XGBoost, calibração ou SHAP executados.
Versões/rodas Python 3.12 consultadas em 25/09/2026 nas fichas oficiais de
[XGBoost](https://pypi.org/project/xgboost/),
[scikit-learn](https://pypi.org/project/scikit-learn/) e
[SHAP](https://pypi.org/project/shap/).
`app.ml.tabular --validate-export <arquivo.json>` agora confere o export
point-in-time, missingness, Review/Capture e versão do detector operacional;
sem versão, a prontidão fica bloqueada. O teste usa somente fixtures sintéticas.

**Inferências.** D00/D10/D20/D40 são a primeira proposta parcial; não equivalem
à cobertura das 35 categorias. D43/D44 e bueiro aberto permanecem candidatos
da Wave 2. A amostra de 130 revisões não aprova todas as imagens das fontes.

**Desconhecidos.** Completude por imagem/classe, grupos por sessão/rota, duplicatas
próximas, direitos Urban, decisão semântica e autorização do novo ciclo. O loader,
evaluator e contrato YOLOX atuais ainda fixam as quatro classes V1; uma ordem
V3 diferente exige adaptação e teste antes de emitir comando de treino longo.

**Blockers e próxima ação.** Importar decisões humanas reais e evidência de direitos;
adjudicar mapeamentos/completude/grupos; registrar escopo e classe autorizados;
construir split independente sem reaproveitar o teste histórico; adaptar o
consumer V3 à ordem aprovada; repetir preflight com RAM segura. O comando exato
do próximo treino permanece **indefinido** até existir contrato V3 aprovado.
Executar o comando V1 seria treinar outro ciclo, portanto não é substituto.

**Decisão conservadora.** `YOLOX_DATA_READY=NO`, `YOLOX_HOLDOUT_READY=NO`,
`SMOKE_PASSED=NO`, `XGBOOST_LABELS_READY=NO`. O checkpoint indica onde retomar
sem refazer auditorias. Verificação de implementação pode passar isoladamente;
o gate operacional do treinamento permanece `BLOCKED` até evidência real.

### Auditoria e verificação desta execução

Estados independentes no escopo V3: `geometric_valid=0` aprovado para o novo
ciclo, `semantic_candidate=130`, `human_validated=0`, `training_authorized=0`.
As 80 cadeias de release não alteram essas contagens. Papéis permitidos no
dataset V3: somente `QUARANTINE`; treino e avaliação oficiais **não autorizados**.

| Domínio | Evidência desta execução | Estado |
|---|---|---|
| Taxonomias/mapeamento | v3 35 IDs; plano 4/8/23; D43/D44 e manhole separados | candidatos, mapeamento novo aberto |
| Boxes/máscaras/componentes | COCO testa limites com fixture; máscara UNIVALI original com hash; nenhum componente promovido | geometria real/semântica aberta |
| Negativos/completude | estados por classe no pacote; `NOT_ANNOTATED` recusado pelo conversor | nenhum negativo novo aprovado |
| Duplicatas/grupos | teste rejeita hash repetido e grupo cruzando TRAIN/VALIDATION; sessão/rota reais pendentes | split V3 aberto |
| Papéis protegidos | produtor V3 lê propostas TRAIN e folhas locais, sem TEST; conversor recusa TEST | Frozen Test selado |
| Revisão humana | import valida pacote, IDs, digest, campos e repetições; zero exports reais | pendente |
| Direitos | release oficial/hash ligado a 80; Urban50 sem origem por imagem | uso novo pendente |
| XGBoost | export point-in-time, hash do snapshot, detector/version, missingness; fixtures | labels e dependências locais pendentes |

| Gate técnico | Comando/inspeção | Resultado e limite |
|---|---|---|
| Testes focais e regressão | `pytest test_core_service.py test_tabular_phase8.py test_preflight_training.py test_annotation_review.py test_detection_manifest_builder.py test_ml_splits.py test_yolox_stack.py test_yolox_evaluator.py test_training_engine.py -q` | 203 passaram; unit/integration, sem treino |
| Lint/static | Ruff check dos oito arquivos Python afetados; `git diff --check` | passaram; avisos Git CRLF informativos |
| Manifestos/hashes | relatório V3 contra SHA-256 do pool; checkpoint contra hash do draft | passaram; 130/80/0/0 |
| Registry | `refresh_registry.py --check --only` nos seis artefatos do contrato | zero drift no escopo; outros não revalidados |
| Produtor/consumidor | CLI real `--plan`, `--generation v3-draft`, `--write`; imports de revisão em fixture; `CoreService.tabular_ground_truth` em testes | produtores alcançáveis; import humano real não recebido |
| Fail-closed | fixtures para export duplicado/stale, autorização falsa, labels parciais, hash/grupo cruzados, lineage malformada e timezone | recusados |
| Cloud/ambiente | preflight local CUDA/VRAM/RAM/disco; sem hidratação automática | GPU presente; RAM insegura; deploy fora do escopo |
| Pipeline real | leitura dos relatórios/130 propostas e geração de pool local; nenhuma conversão elegível | draft válido; smoke e split final bloqueados |

Revisão independente encontrou três defeitos: isolamento de hash/grupo entre
papéis no COCO, linhagem/instante malformados no tabular e import de revisão
contado sem conferir integridade. Corrigidos e retestados; segunda revisão
somente leitura não encontrou achados remanescentes no escopo corrigido.
`VERIFICATION_VERDICT=BLOCKED` para treinamento operacional: revisão humana,
direitos, completude, grupo, holdout, contrato V3 e RAM continuam abertos.

## Artefatos e reprodução

- `datasets/reports/training_class_plan.json`: classificação primária, ondas, dados
  históricos recontados, autorização vazia e contrato proposto do próximo dataset.
- `datasets/reports/review_summary.json`: todas as 130 propostas, identidade da imagem,
  fonte, classe nativa/proposta, anotação, direitos, grupo, ambiguidades e formulário
  de resolução da procedência. Resumos cobrem também categorias sem propostas.
- `datasets/reports/missing_data_plan.json`: planos individuais das 29 categorias
  sem dados locais adequados; exemplos, negativos difíceis, grupos, domínio e orçamento.
- `datasets/reports/taxonomy_coverage.json`: definições e critérios canônicos reutilizados.
- `datasets/annotations/tabular_labeling_protocol.json`: formulário humano existente
  para alvos tabulares separados; não duplicado nesta execução.

Na raiz, executar `backend/.venv/Scripts/python.exe -B scripts/datasets/review_annotations.py --plan`.
O produtor existente foi estendido (`EXTENDED_EXISTING_COMPONENT`). Não cria pacote
de imagens novo, não abre XMLs, não gera split e não treina. Usa o HTML pinado no
contrato, valida hash do arquivo, pacote, mídia embutida, mídia local e insumos.
TRAIN/VALIDATION são lidos pelos manifestos existentes e seus hashes registrados.
Ausência, divergência, papel inesperado ou origem fora do escopo permitido interrompem.
Depois, usar o produtor oficial `refresh_registry.py --only` para os três relatórios.
O pacote HTML anterior e as folhas originais permanecem preservados.

## Como interpretar o plano

Cada categoria recebe exatamente um estado primário. `DETECTOR_CLASS` significa
adequação visual e existência de protocolo de bbox, **não aprovação de treino**.
As quatro classes de pavimento são candidatas nesse sentido. Relações espaciais
ficam em `CONTEXT_ATTRIBUTE`; sinais que não provam estado contextual ficam em
`REVIEW_ONLY`; demais candidatas visuais sem dados/protocolo prontos ficam em
`DATA_NOT_READY`. Nenhuma definição atual é classificada como `NOT_PHOTO_DETECTABLE`:
a v3 descreve sinais aparentes, não energização, causa, abandono comprovado ou
probabilidade de acidente. Esses últimos conceitos continuam proibidos como inferência
fotográfica. Não houve alteração de `ISSUES`, status `DATA_REQUIRED` ou cabeça YOLOX.

WAVE_1 é a mais próxima **comparativamente**, não praticamente liberada: ainda faltam
completude, grupos/duplicatas e autorização. WAVE_2 reúne fontes pesquisadas que exigem
direitos, curadoria e anotação. WAVE_3 reúne coleta nova, relações e inspeção.
Treino futuro com subconjunto deve ser identificado como **parcial**, nunca cobertura das 35.

`urmind-urban-vision-v3` é nome proposto, sem dataset instanciado. `CLASS_ORDER=[]`.
Só classes aprovadas explicitamente podem entrar na ordem congelada junto ao contrato
do modelo/export. Nenhum split final é criado. Preservar Frozen Tests, exclusões IRD
e proibições de avaliação de reforço UNIVALI/Urban; não atribuir novos papéis por este plano.

## Revisão humana e direitos

Abrir localmente `datasets/processed/annotation_review/review_1b00e44bcd58.html`.
As 130 propostas incluem 32 UNIVALI, 50 Urban/pothole e 48 RDD: 24 Norway D40,
12 D43 e 12 D44. Acesso à inspeção não constitui licença ou aprovação.
`HUMAN_REVIEW_READY` conta material disponível para inspeção local em quarentena.
`HIGH_CONFIDENCE`, `AMBIGUOUS`, `BAD_LABEL` e `MISSING_ANNOTATIONS` são `null`
enquanto não avaliados; não significam zero, acerto ou ausência de objetos.

80 propostas têm registro histórico de release oficial no catálogo. Esse registro
não foi convertido automaticamente em `LICENSE_VERIFIED` ou `PROVENANCE_VERIFIED`.
Neste relatório, ambos continuam UNKNOWN até vincular evidências concretas à imagem,
release e uso do novo ciclo. Isso não revoga nem reinterpreta autorização histórica V1.
As 50 Urban também têm origem/direitos sem comprovação e parent hash antigo.
Correspondência da caixa com o manifesto atual é verificada e registrada separadamente;
ela não resolve direitos, semântica ou stale approval. Nenhuma folha foi rebaseada.

Para RDD/UNIVALI, reutilizar documentos oficiais e checksums anteriores, vinculando-os
aos hashes das imagens e registrando responsável/escopo; repetir auditoria somente
se houver divergência. Para Urban, recuperar autor e URL originais, licença e atribuição
por imagem. CC0 do uploader, padrão de nome e tamanho igual não comprovam direitos.
Se a origem não puder ser estabelecida, excluir a imagem do **novo dataset** sem apagar raw.
O formulário por proposta contém campos concretos para resolver essa pendência.
Não editar os três relatórios gerados para registrar aprovação. Usar o formulário
como roteiro, conservar evidências oficiais e exportar decisões pelo pacote HTML.
Vincular cada evidência ao `PROPOSAL_ID` e `IMAGE_SHA256`; registrar direitos e
autorização no mecanismo canônico após adjudicação. O validador existente verifica
o export de anotações, mas não transforma esse export em validação de licença.

Open manhole é investigação separada: relatório histórico Urban registra 152 imagens
e 154 caixas; o lote de 50 atual contém potholes, não bueiros. RTK `storm-drain` é
classe de dreno, não prova abertura/obstrução. A matriz distingue abertura sem tampa,
tampa presente danificada, entrada obstruída e tampa/dreno normal. Candidatos externos
já catalogados permanecem sujeitos a direitos e revisão; nada adquirido automaticamente.

## Coleta mínima proposta e orçamento

Os números de coleta são **pisos de piloto propostos**, sujeitos à aprovação humana,
não amostra suficiente para treinamento ou avaliação: por categoria faltante, 30 cenas
positivas, 30 negativas revisadas e pelo menos 15 grupos independentes em três locais.
Usar definição/exclusões específicas da categoria; categorias contextuais exigem
relação entre objeto e cena, e categorias de revisão exigem evidência estruturada.
Quantidade final depende da diversidade, prevalência, discordância e incerteza medidas.

Estimativa explícita de 0,5–2 MB/imagem mais 10% metadata: 33–132 MB por piloto;
29 pilotos somam 0,957–3,828 GB, sem deduplicação presumida. O limite superior excede
a folga atual. Adquirir apenas após medir pacote/subconjunto e aprovar orçamento por fonte,
teto global e 10 GB livres em disco. Novas fontes precisam de alocação explícita;
não herdam automaticamente limite disponível. Exceção RDD continua apenas para RDD.
Nenhum download, descarte de modelo ou raw foi feito para acomodar o plano.

## Ground Truth tabular

`review_confirmed` não é severidade, risco ou prioridade. O plano remete ao formulário
existente e acrescenta ordem de coleta: aprovar rubrica/horizonte/política; piloto de
30 casos independentes por alvo com dois avaliadores; medir discordância/missingness;
adjudicar; só então fixar tamanho final e critérios. O piloto não habilita treinamento.

- Severidade: inspeção independente, rubrica física versionada, medidas/unidades,
  evidências e avaliação sem mostrar scores do detector/regras.
- Risco: exposição e desfecho adverso observado em horizonte predefinido, seguimento
  e censura. Sem desfecho não há label de risco. Relato confirmado não o substitui.
- Prioridade: ranking por especialistas sob política/capacidade explícitas, comparações
  e adjudicação independentes. Scores das regras não são verdade externa.

Usar snapshots point-in-time anteriores à revisão e features do detector operacional.
Não substituir detecções por anotações perfeitas. Não coletado banco real nesta tarefa.

## Verificação da preparação

**VERIFICATION_VERDICT=PASS somente para produção do plano**, sem autorizar dados,
classes, treinamento ou avaliação. Findings de implementação remanescentes: nenhum
na revisão focal. As pendências científicas são explicitamente o objeto do plano.

| Check | Nível e evidência | Resultado / limite |
|---|---|---|
| Testes focais | pytest `test_taxonomy_candidates.py`, `test_annotation_review.py` | 18 passaram após correções; fixtures |
| Suite relevante | pytest desses dois mais `test_artifact_registry.py`, `test_tabular_phase8.py` | 50 passaram; dois avisos de depreciação de dependências |
| Lint/formato | Ruff check e format --check nos quatro arquivos Python afetados | passaram |
| Tipagem | mypy `app/datasets/taxonomy_candidates.py --follow-imports=silent` | passou |
| Manifestos | pipeline `review_annotations.py --plan` | papéis TRAIN/VALIDATION, hashes registrados e categoria/versão conferidos |
| Registry | refresh/check `--only` contrato e três relatórios | zero divergências; demais entradas preservadas sem reverificação |
| Fingerprints | hash HTML/pacote/mídia e conferência final de 15 insumos distintos | passaram; não comprovam semântica ou direitos |
| Código órfão | inspeção do CLI, contrato, testes e execução do comando | comando alcança produtor e três relatórios |
| Script inalcançável | execução real `--plan` | saída 35 categorias / 130 propostas / 29 lacunas |
| Artefato sem produtor | três JSONs declaram produtor e input_sha256 | produtor existente estendido; não editados à mão |
| Bypass de manifesto | allowlist dos insumos do pacote, papel e registry conferidos | sem XML novo, sem split final ou consumer de treino novo |
| Fail-open | fixtures hash HTML errado, referência protegida, taxonomia incompleta/duplicada, CC0 e declaração CSV insuficiente | rejeição ou UNKNOWN; nenhuma autorização concedida |
| Valores fixos | inspeção estados/ondas, snapshot D43/D44/Norway e pisos de piloto | decisões de planejamento ou fatos históricos explicitados; não thresholds de treino |
| Cloud | require_local e bytes locais verificados no caminho real | nenhum serviço remoto necessário; sem hidratação automática |
| Fidelidade | fixtures separam estados; execução real valida arquivo de 130 propostas | sem banco, sem qualidade semântica ou treinamento inferidos dos testes |

Falhas observadas e corrigidas: alias de encoding inválido no novo leitor CSV;
fixture fora da raiz permitida pelo guard; inferência indevida de VERIFIED a partir
de duas células CSV. Regressão agora impede essa promoção. Revisão independente
confirmou correção e consistência dos relatórios, com 18 testes focais passando.

`git diff --check` passou. Pacote anterior não alterado. Não foram executados treino,
smoke training, inferência, Frozen Test, promoção, deploy, push, mudanças em raw ou banco.
Não há ação de implementação remanescente para o PASS do plano. Para liberação científica,
executar as ações humanas e técnicas das seções seguintes; todos os gates continuam abertos.

## Fatos

Relatórios e pacote existentes foram reaproveitados. Novos relatórios têm hashes dos
insumos e produtores. Recontagem limitada aos manifestos TRAIN/VALIDATION existentes.
Nenhuma decisão humana, autorização, split, classe ativa ou treino foi criado.

## Inferencias

Estados primários e ondas são decisões de planejamento derivadas da definição visual
e evidência disponível. Os pisos de piloto são propostas logísticas, não resultados
empíricos de suficiência. D43/D44 são candidatos, não mapeamento aprovado.

## Desconhecidos

Semântica/completude das propostas, grupos independentes reais, adjudicação de
near-duplicates, direitos vinculados ao novo ciclo, qualidade de domínios e tamanho
final de amostra. Registro histórico de licença não substitui sua evidência vinculada.

## Blockers

Cada categoria possui ação e critério nos JSONs. Ordem prática: preencher direitos;
resolver linhagem Urban; revisar originais/completude; adjudicar mapeamentos/duplicatas;
recuperar grupos; aprovar novas classes e uso; preparar split isolado somente em tarefa
posterior. As 461 Norway e as 430 pequenas permanecem preservadas.

O relatório histórico global de duplicatas contém identidades de papéis mistos e foi
consultado como evidência histórica. Essas identidades não foram usadas para seleção,
ajuste de taxonomia ou novo split. Nenhum Frozen Test foi aberto, avaliado ou inferido.

## Decisao conservadora

`training_authorized=false` e `official_evaluation_authorized=false` para o novo ciclo
`urmind-urban-vision-v3`, sem papéis autorizados. DATA, CLASS e HOLDOUT gates continuam
abertos. Permitido nesta execução apenas preparar relatórios e inspeção local em quarentena.
Critérios de liberação e formulários estão preparados; sua execução humana permanece pendente.

> Seções abaixo trazidas do worktree `feat/xgboost-preparation-parallel` na reconciliação de 26/09/2026; o código correspondente agora está no backend principal.

## Preparação XGBoost em worktree isolado — 25/09/2026

O desenvolvimento paralelo está descrito em [XGBOOST_PARALLEL_HANDOFF.md](XGBOOST_PARALLEL_HANDOFF.md). O export point-in-time existente ganhou rastreio de snapshot, cutoff, proveniência e missingness. Validação offline de labels, saída visual, splits, preflight e dry-run roda sem Torch/YOLOX/XGBoost. O protocolo draft separa confirmação de Review de severidade, risco e prioridade; os três alvos adicionais permanecem `TARGET_APPROVAL_PENDING`.

`LABEL_COUNT=NOT_VERIFIED`: nenhum banco ou corpus real foi consultado neste worktree. O Python 3.12 usado nos testes leves não possui XGBoost nem Pydantic. Comandos de treino, calibração e SHAP continuam bloqueados/deferidos, sem fit, modelo ou promoção. A preparação não altera o status do DatasetVersion visual, do YOLOX ou dos gates científicos anteriores.

## XGBoost tabular (worktree isolado, consulta atual 25/09/2026)

Consulta agregada somente leitura ao Supabase Urmind DEV: Events 0, Reviews 0, RiskAssessments 0, Predictions 0, Captures 1. Contagem real de labels humanos elegiveis por target: `review_confirmed=0`, `severity=0`, `risk=0`, `priority=0`. Nao ha linhas reais para validar schema de features, point-in-time ou split. Esses gates seguem `BLOCKED_DATA`, nao PASS. Saidas de regras em `risk_assessments` nao sao Ground Truth humano para severity/priority. O worktree XGBoost possui apenas contratos e testes sinteticos; treino nao iniciado.

A ordem de desbloqueio e: labels independentes com proveniencia e autorizacao; snapshots reais com tempos de disponibilidade; grupos verificaveis; manifests de exposicao YOLOX; DatasetVersion e split; preflight; treino CPU em um target por vez. O hash do extrator tabular draft foi atualizado para `46a720e398de941839017cbc98616e2f002e8b8bb9a2b8303fa368f58bea39eb` apos rejeitar tipos invalidos e historico futuro. Nenhum dado real foi reprocessado.

Verificacao somente leitura repetida em 25/09/2026 no mesmo Supabase DEV: `events=0`, `reviews=0`, `detections=0`, `risk_assessments=0`, `tabular_dataset_versions=0`, `captures=1`. Labels elegiveis por `review_confirmed`, `severity`, `risk` e `priority`: zero nesta fonte. O snapshot real ainda nao registra toda a linhagem visual exigida (checksum de artefato, ordem de classes e versoes de transformacao); nao foi demonstrado point-in-time nem split por grupo em linhas reais. O harness de treino CPU com limite de 10 horas foi preparado no worktree e testado sem fit. `XGBOOST_DATA_READY=NO`, `XGBOOST_READY_TO_TRAIN=NO`; o estado do YOLOX e seus gates nao foram alterados.

O produtor no worktree agora congela a linhagem visual de `ModelVersion.metrics.serving` ao criar futuros snapshots, com hash do checkpoint e do contrato, ordem de classes, status operacional e tempos de disponibilidade. O exportador rejeita metadados visuais futuros ou divergentes. Nenhum snapshot passado foi enriquecido retroativamente. O extrator tabular draft v4 usa `VISUAL_LINEAGE_VERSION=urmind-visual-lineage-v1` e hash `38cd5b782733d9a30937002b8d5f4766599ac8f9c85099727a307045f280161f`; inclui indicadores das quatro classes visuais do modelo v1 e maior area de bbox normalizada. O hash v2 acima fica historico. Consulta exata atual ao unico projeto Supabase acessivel confirmou novamente zero Events, Reviews e Detections. Esse codigo melhora o produtor futuro, mas nao transforma os quatro gates de dados reais em PASS.

O protocolo tabular v2 draft foi sincronizado ao schema v4. Importacao offline passa a auditar candidatos `severity`, `priority` e `risk` com fontes distintas, preservando `TARGET_APPROVAL_PENDING` e `training_eligible_rows=0` ate haver rubrica/outcome aprovado e corpus real. O formulario salvo e o protocolo gerado foram comparados semanticamente como JSON. Nao foram fabricados labels nem consultados checkpoints YOLOX.

O DatasetVersion tabular no worktree recebe nome derivado do SHA-256 das linhas e registra hashes dos contratos de features/labels. Seu split permanece `NOT_GENERATED` ate existir manifesto temporal de grupos separado. Este ajuste elimina a aparencia de split autorizado em um export ainda sem corpus real; `XGBOOST_DATA_READY=NO` permanece.

## XGBoost: anotacoes externas Attain (worktree isolado)

A contagem zero acima vale somente para labels de Event no Supabase DEV. A auditoria direta do Attain v1 encontrou 4.578 objetos com Low/High em classes mapeaveis sem ambiguidade ao YOLOX atual: D20 High 277 / Low 3.776; D40 High 110 / Low 415. Sao labels externos de severidade **visual** de dano, nao labels de `RiskAssessment.severity`, risco, prioridade ou `review_confirmed` do UrMind. Uma caixa invalida de classe nao elegivel foi excluida e registrada. O manifesto de anotacoes e seus hashes estao no worktree; ver `XGBOOST_PARALLEL_HANDOFF.md`.

Imagens, features por objeto, grupos de cena, DatasetVersion externo, split e runtime XGBoost ainda exigem verificacao. A fonte extraiu quadros de video a cada 250 ms e nao fornece aqui sessao/rota/timestamps suficientes para declarar independencia temporal. Treino experimental pode ser considerado somente com escopo e limitacoes explicitos; integracao operacional e avaliacao do sistema YOLOX + XGBoost permanecem bloqueadas ate testar caixas reais do detector e equivalencia do alvo.

## Atualizacao XGBoost Attain — treino experimental concluido em 25/09/2026

Esta secao atualiza apenas o estado da fonte externa. Os zeros de Event/Review acima continuam validos para o Supabase DEV consultado, mas nao descrevem o Attain. Foram verificados por SHA-256 os 1.656 pares imagem/anotacao WS v1/v2 (144.254.436 bytes de imagem). O DatasetVersion `attain-pavement-visual-severity-7280cb15abf42bc8` possui 4.578 linhas reais de severidade visual Low/High para D20/D40, extraidas do conteudo da imagem e geometria de caixas humanas pelo mesmo extrator versionado usado na analise offline. Nao foram inventados Event, GPS, chuva, tempo de captura, tempo do label, confianca do detector ou revisao do UrMind. `FEATURE_SCHEMA_HASH=95f96c2a022a1919ed357bcedbd1fcc09f3161e83af67d4b7d6676c54371da37`; `LABEL_SCHEMA_HASH=f3a0b15c58f1e1e68fe0f61cda26c163c8a44641b048f1eda7689c55ee10c877`.

Split interno WS v2 TRAIN 3.019 / WS v1 VALIDATION 1.559, respectivamente 592 e 495 grupos por hash exato de imagem, sobreposicao exata zero. A triagem dHash de miniaturas encontrou zero pares entre subsets com distancia ate 4 bits; e apenas busca de quase duplicatas, nao prova de sessao/rota/cena ou ordem temporal. `POINT_IN_TIME=SEMANTICA_EXTERNA_VERIFICADA_SEM_DATAS_INVENTADAS`; `SCENE_GROUP_STATUS=UNKNOWN`; `YOLOX_EXPOSURE=UNKNOWN`; avaliacao independente do sistema combinado bloqueada.

Treino XGBoost CPU, `hist`, uma thread, um unico target e early stopping em VALIDATION concluido. Modelo salvo como candidato experimental, sem promocao. O target do Attain e nivel **visual** de dano; `RiskAssessment.severity` do UrMind representa risco a quem passa. Essa equivalencia nao foi demonstrada. A validacao interna mostrou recall High 12/114 e Brier pior que o baseline de prevalencia; assim `XGBOOST_READY_FOR_INTEGRATION=NO`. `review_confirmed`, `severity` operacional, `risk` e `priority` do UrMind permanecem `TARGET_DATA_BLOCKED` na fonte consultada. Consultar o run, hashes, metricas, testes e reconciliacao em [XGBOOST_PARALLEL_HANDOFF.md](XGBOOST_PARALLEL_HANDOFF.md). Nenhum teste final, calibracao ou SHAP foi executado.

## XGBoost: continuacao do treino e gate de baseline

Um segundo fit experimental, sequencial, usou peso positivo calculado apenas em TRAIN e early stopping por `aucpr` em VALIDATION. Ele recuperou 103/114 High no limiar 0,5, mas criou 626 falsos positivos; average precision caiu de 0,194977 no primeiro modelo para 0,157207. O primeiro modelo tinha 12/114 High no mesmo limiar. O avaliador agora compara ambos a um baseline de prevalencia por tipo de dano ajustado somente em TRAIN. Esse baseline obteve log loss 0,2462 e Brier 0,0645 na VALIDATION; primeiro XGBoost 0,2492/0,0703, segundo 0,7123/0,2593. `MODEL_SELECTION_GATE=FAILS_CLASS_PRIOR_BASELINE` para ambos. Nenhum modelo foi promovido ou escolhido para a aplicacao. A falta de grupos de cena, teste independente, caixas do YOLOX final e equivalencia semantica com `RiskAssessment.severity` permanece bloqueadora. A inspecao limitada dos manifests do YOLOX ativo encontrou apenas RDD2022 declarado, sem Attain direto; nao altera `YOLOX_EXPOSURE_UNKNOWN`.

O avaliador externo agora recarrega o modelo e reproduz as 1.559 probabilidades da VALIDATION em CPU para cada um dos dois runs (`prediction_generation_verified=true`); isso fecha a prova de geracao dos arquivos avaliados, mas nao o gate cientifico. Um bootstrap exploratorio por hash de imagem (495 grupos, 400 amostras) deu, para o primeiro modelo versus baseline D20/D40, intervalo percentil 95% da diferenca de average precision `[-0,0156,+0,1092]` e da diferenca de Brier `[+0,0003,+0,0103]`. A vantagem de ordenacao nao foi demonstrada de forma robusta neste split, e o Brier ficou pior. EGY_PDD exige liberacao formal para acesso e CNRDD ainda nao teve o portal original acessivel; nenhum label novo foi incorporado. `XGBOOST_READY_FOR_INTEGRATION=NO`.

## XGBoost: audit de quase duplicatas e split de reposicao

Uma inspeccao posterior encontrou o mesmo trecho de via em imagens elegiveis dos dois subsets do Attain (`WS v1 000269` e `WS v2 000439`). O split anterior separava esses subsets e tinha zero hashes exatos repetidos, mas **nao tinha independencia de cena**. Seus dois runs permanecem apenas como evidencia historica de desenvolvimento. O audit dHash64 dos 1.087 hashes de imagem elegiveis encontrou 193 pares cruzados a distancia ate 12, envolvendo 143 imagens. O produtor agora exige esse audit antes de criar um novo split para treino e nao gera mais o split legado sem prova.

No split de reposicao, 522 linhas foram excluidas; TRAIN 2.652 linhas/504 hashes de imagem e VALIDATION 1.404/440. Nenhum par candidato do audit cruza esses papeis. Isto resolve **os candidatos detectados por esse metodo**, mas nao prova independencia de todas as cenas ou sequencias. O DatasetVersion original de 4.578 linhas permanece imutavel; split e aprovacao tem hashes novos. Um fit de reposicao em CPU terminou com `BEST_ITERATION=0` e recall High `0/107`; log loss `0,286539`, Brier `0,077717` e average precision `0,113032`, piores que o baseline D20/D40 ajustado so em TRAIN (`0,253224`, `0,066910`, `0,162925`). As 1.404 probabilidades foram reproduzidas a partir do modelo salvo. `MODEL_SELECTION_GATE=FAILS_CLASS_PRIOR_BASELINE`; `XGBOOST_READY_FOR_INTEGRATION=NO`. Detalhes e artefatos no handoff.

O gate de audit agora vale tambem para chamadas diretas da API de treino,
antes de carregar dependencias pesadas. Uma sensibilidade apenas diagnostica
por vizinhanca dos nomes de quadros abrangeria 625 das 1.087 imagens elegiveis
com raio de 10 indices em torno dos pares candidatos. Esses quadros nao foram
declarados duplicatas nem geraram outro split; continuam necessarios IDs de
video/rota/sessao para alegar avaliacao independente. O cartao do PaveBench
nao confirmou rotulos independentes por objeto compativeis e declara licenca
`CC BY-NC-SA 4.0`; seus dados nao foram incorporados.

O preflight externo agora mostra deslocamento de formato de imagem sem usar
labels: so 618/2.652 linhas TRAIN sao 640x640, enquanto 1.404/1.404 linhas
VALIDATION sao 640x640. A variacao total de formato e `0,766968`; alturas
medias normalizadas das caixas sao `0,1210` e `0,2682`. Este e um
diagnostico de distribuicao, nao prova de causa ou generalizacao. O modelo
existente continua abaixo do baseline e nao foi treinado novamente.

Auditoria somente leitura em 25/09/2026 21:29 UTC: as 1.087 imagens
elegiveis do Attain nao contem EXIF nem blocos embutidos de data/camera/GPS;
os grupos de video/rota continuam desconhecidos. No Supabase `Urmind DEV`,
`events=0`, `reviews=0`, `detections=0`, `risk_assessments=0`,
`predictions=0` e `captures=1`; os quatro targets internos continuam com
zero labels elegiveis. `public.tabular_dataset_versions` esta **ausente**,
nao vazia. Registros cronologicos anteriores com valor `0` para essa tabela
nao constituem contagem validada. Nenhum novo treino ou promocao foi feito.

## XGBoost Attain: diagnostico de formato e grupos sem novo treino

O run experimental mais recente continua abaixo do baseline D20/D40 de
prevalencia suavizada calculado em TRAIN: VALIDATION de 1.404 objetos,
baseline log loss `0,253224`, Brier `0,066910`, AP `0,162925`;
XGBoost `0,286539`, `0,077717`, `0,113032`. O avaliador vinculou linhas,
labels, hashes, split e artefato e calculou ambos na mesma VALIDATION.
Isto e comparavel **internamente**, mas nao mede generalizacao independente.

Todas as 1.087 imagens elegiveis sao JPEG RGB. TRAIN usa somente WS v2,
caixas XML em pixels e resolucoes `640x640`, `1479x508`, `1920x1080`;
VALIDATION usa somente WS v1, YOLO em coordenadas normalizadas (caixas ou
poligonos) e `640x640`. O mesmo extrator converte as caixas em coordenadas
normalizadas e produz 11 features na mesma ordem. Cinco amostras reais,
cobrindo os protocolos e resolucoes, tiveram recomputacao identica.
O WS v2 tambem possui poligono em 110 objetos elegiveis; o importador passou
a rejeitar divergencia relevante entre esse poligono e sua caixa XML. Os
847 XML preservaram contagens e exclusoes do manifesto nessa verificacao. O
preflight agora relata variacao total `1,0` do protocolo de anotacao entre
TRAIN/VALIDATION, alem da variacao `0,766968` das dimensoes. Esse diagnostico
nao identifica causalmente o motivo da queda nem elimina desvio de camera,
cena ou caixa humana versus detector.
O [artigo original do Attain](https://pmc.ncbi.nlm.nih.gov/articles/PMC12167439/)
descreve imagens finais apenas em `640x640` e `1479x508`; 142 arquivos WS v2
elegiveis publicados tem `1920x1080` nos cabecalhos. O extrator usa os bytes
reais, sem converter raw nem inferir camera pelo tamanho.
O YAML oficial WS v2 tambem omite `1920x1080`. Seus caminhos `train/val/test`
nao identificam arquivos por papel e nao substituem um manifesto de split.
A API anonima de arquivos lista todos os 809 WS v1 e 847 WS v2 em seus
respectivos folder IDs de imagens, sem papéis por arquivo. A API de arvore
de pastas exigiu autenticacao; nenhuma credencial foi usada.

O inventario oficial completo tem 4.589 arquivos: pares imagem/anotacao nos
tres subsets e tres mapas YAML, sem arquivo nomeado de rota/sessao/camera.
Os 1.087 hashes de imagem elegivel sao grupos **verificados apenas para a
propria imagem**. O audit dHash registra 18 componentes candidatos de 143
imagens, sem transformar similaridade ou indice de arquivo em identidade de
rota. Grupo de cena, ordem temporal e independencia final continuam UNKNOWN.
O artigo descreve cerca de 20 horas de video continuo e extracao de frames
a cada 250 ms antes de selecionar 2.293 imagens, mas nao liga arquivos
publicados aos videos, sessoes, cameras ou indices de frame. Isso reforca
o risco de dependencia sem provar relacao entre nomes sequenciais.
Na borda do limite dHash 12, o split ainda tem 18 pares TRAIN/VALIDATION a
distancia 13; o preflight/avaliador agora exibem essa sensibilidade como
diagnostico, sem declarar os pares como cenas verificadas. Nenhum split ou
modelo foi refeito com essa observacao.
O preflight real agora retorna `BLOCKED_GROUP_EVIDENCE`; a chamada direta
de treino recusa o mesmo bundle antes de importar XGBoost. O avaliador ainda
permite reproduzir os artefatos historicos, sem promovê-los.
No WS v1, `Alligator crack - Low` e `Alligator crack - low` sao dois IDs
originais reunidos sintaticamente em LOW; falta confirmar a rubrica com os
autores. Labels Attain continuam de severidade **visual** por objeto e nao
Ground Truth operacional de severidade/risco/prioridade do UrMind.
Os IDs `1`/`2` aparecem em 112/314 imagens WS v1 sem coocorrencia; isso
nao demonstra equivalencia semantica e mantem
`LABEL_MAPPING_STATUS=SYNTACTIC_ONLY_RUBRIC_UNVERIFIED`.
O preflight real passou a expor esse status no JSON junto de
`BLOCKED_GROUP_EVIDENCE`; a rejeicao de fit cita ambos os bloqueios antes
de importar o modelo. Nenhum run antigo foi reescrito.
Mesmo que um futuro split prove grupos, o fit segue bloqueado pela rubrica
ate haver evidencia da equivalencia Low/low e WS v1/v2. Teste sintetico
focal cobriu a independencia desse gate sem ajustar modelo.
O exportador agora rejeita `Medium` para o alvo binario D20/D40 antes de
gerar a linha. Os 1.656 arquivos de anotacao locais possuem zero `Medium`
nesses dois tipos; as 4.578 linhas ja exportadas seguem 4.191 LOW / 387 HIGH
e seu DatasetVersion nao foi reescrito.

O registro canonico previsto no ORM/migration e `public.dataset_versions`;
sua presenca no banco DEV nao foi verificada nesta rodada. A tabela distinta
`public.tabular_dataset_versions` estava ausente na consulta anterior.
Nenhuma consulta Supabase, migration, fit, TEST ou promocao ocorreu agora.
`READY_FOR_CONTROLLED_RETRAINING=NO` ate existir grupo oficial por
video/sessao/rota para os 1.087 arquivos, ou avaliacao externa independente
com rubrica e grupos equivalentes. Detalhes, hashes e solicitacao nao enviada
aos autores: [XGBOOST_PARALLEL_HANDOFF.md](XGBOOST_PARALLEL_HANDOFF.md).
