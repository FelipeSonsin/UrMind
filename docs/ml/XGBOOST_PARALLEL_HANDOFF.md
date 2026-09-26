# Passagem do XGBoost paralelo — 25/09/2026

**Estado atual:** tres treinos XGBoost de severidade **visual** D20/D40 foram concluidos
como candidatos experimentais no worktree. Nenhum passou o baseline por tipo de dano,
foi selecionado para uso operacional ou integrado ao UrMind. Os dois primeiros
usaram um split com sobreposicao visual de cena descoberta depois; o terceiro
usou uma quarentena por similaridade. Os relatos abaixo sao
cronologicos; mencoes antigas a treino nao executado descrevem etapas anteriores.

## Isolamento e base

- Worktree: `C:\Users\felip\AppData\Local\UrMind\worktrees\xgboost-preparation-parallel`.
- Branch: `feat/xgboost-preparation-parallel`; base Git: `e57db6182de2be37a8176b845c87e35376c32a76`.
- Checkout esparso: `backend`, `docs`, `scripts`, `datasets/metadata`; nenhum dataset visual, checkpoint, ambiente Python ou MLflow foi copiado.
- `AGENTS.md` não existe na base consultada.
- Working copy original não recebeu escritas deste trabalho. Na etapa inicial, YOLOX, GPU, treino tabular, migrations, deploy, commit e merge não foram executados pelo agente; os treinos tabulares experimentais posteriores estão documentados abaixo.

Arquivos locais ainda não commitados foram copiados de modo estável para os mesmos caminhos relativos, com SHA-256 antes e depois:

| Arquivo | SHA-256 da base local copiada |
|---|---|
| `backend/app/ml/tabular.py` | `3F3D754E2980AD12672CA2E0604C594DC2A2E76DA6AFDA37BA3156E2AEDEE61E` |
| `backend/tests/test_tabular_phase8.py` | `4EE551BC707C419571538AC772B54EC0DBE1566198775AD83479715DA5405CB9` |
| `backend/pyproject.toml` | `6B30F52BA2921C7E1680FA0D0A682518CDBB2B7F44D734C694FE1AA6F0FC896B` |
| `datasets/annotations/tabular_labeling_protocol.json` | `1BF5E7117DD6F245F0C8D6D9EE0843FB60264BC7844714BC5CCEE74C74733E65` |
| `docs/ml/DATA_READINESS.md` | `9CE7DC9065C08761661BBC370F5B584038DBF05F5D9031A2FA448290D251AD8D` |

`backend/app/services/core.py` da base Git já contém o exportador point-in-time. Alterações locais concorrentes nesse arquivo tratam do relatório público; elas não foram copiadas. Esse arquivo exige comparação de linhas durante a integração.

Durante este trabalho, `docs/ml/DATA_READINESS.md` no checkout original mudou do hash copiado `9CE7DC9065C08761661BBC370F5B584038DBF05F5D9031A2FA448290D251AD8D` para `F75CFEC0EC6A280C3AFDF66732A70B6232798CB20486FE2FC4316ECDC906EDC2`. A nova versão concorrente não foi copiada; reconciliar esse documento linha a linha depois do YOLOX.

Arquivos de trabalho alterados no worktree: `backend/app/ml/tabular.py`, `backend/app/services/__init__.py`, `backend/app/services/core.py`, `backend/pyproject.toml`, `backend/tests/test_tabular_phase8.py`, `backend/tests/test_tabular_preparation_stdlib.py`, `datasets/annotations/tabular_labeling_protocol.json`, `datasets/metadata/tabular_xgboost_draft_config.json`, `docs/ml/DATA_READINESS.md` e este documento. `services/__init__.py` usa import tardio para impedir carregamento do serviço web ao validar o módulo tabular.

## Contrato de alvo e rótulos

O pipeline existente permanece um único módulo tabular configurável. `review_confirmed` é confirmação binária de ocorrência por Review persistida e resolvida. Não representa dano, acidente nem prioridade. O formulário draft registra outros alvos sem aprová-los:

| Alvo | Tarefa e unidade | Origem e condição ainda necessária | Métricas propostas |
|---|---|---|---|
| `review_confirmed` | classificação binária por Event/snapshot, antes da primeira revisão | consenso ou adjudicação atribuída de Review; autorização de uso ainda ausente | log loss, Brier, AP, matriz de confusão, calibração e grupos |
| `severity` | ordinal de dano físico por Event/snapshot | inspeção independente, níveis e rubrica versionada; `TARGET_APPROVAL_PENDING` | MAE ordinal, kappa ponderado, recall por nível |
| `risk` | desfecho futuro por Event/snapshot | horizonte, exposição, desfecho observável e censura; `TARGET_APPROVAL_PENDING` | Brier, AP, calibração e estratos de exposição |
| `priority` | ranking ordinal por Event/snapshot | política de serviço, capacidade e julgamento independente; `TARGET_APPROVAL_PENDING` | concordância, NDCG e erros por política |

Risco ordinal produzido por regras não é probabilidade de acidente. Prioridade humana pode ser reproduzida somente como decisão de política, nunca apresentada como previsão independente de dano futuro. Scores das regras, confiança visual e `review_confirmed` não rotulam os demais alvos. Recorrência permanece no formulário anterior como alvo futuro separado.

Cada rótulo draft exige Event, Capture, grupos de cena/duplicata/sequência, snapshot e SHA-256, versão de feature/target/detector, origem, revisão de duas pessoas ou adjudicação, tempos e autorização de uso. Importador recusa valor binário inválido, duplicata, conflito, revisão incompleta, grupo `UNCONFIRMED` e autorização ausente. Importar rótulo válido não autoriza treino.

## Features, visual e splits

O exportador continua lendo `phase4_snapshot` imutável anterior à primeira revisão. Registra `assessment_id`, cutoff, hash do snapshot, schema, missingness e proveniência. A ordem numérica das features é fixada pelo SHA-256 `8d19602ffff5e9f51a579630899f08705a970ccc28ed82a94d3e33b0003b2eaa`. IDs, hashes, revisor e estado final de Review não entram nas features. Histórico sem ordem/cobertura confiável é missing; janela calculada vazia só vira zero com cobertura `VERIFIED`. Snapshots atuais não registram essa cobertura, então o uso histórico no treino fica bloqueado. Contexto adquirido depois do cutoff é missing. Exportação antiga sem versões de preprocessamento/postprocessamento visual permanece bloqueada para treino.

Contrato visual valida classe, confiança, bbox normalizada, dimensões, versão do modelo e versões de preprocessamento/postprocessamento. `NO_DETECTION_OBSERVED` não comprova ausência de problema. Origem distingue detector persistido, anotação humana e fixture sintética. O futuro checkpoint YOLOX ainda não está vinculado. Nenhuma detecção histórica foi reescrita.

O gerador de splits opera sobre componentes conexas de Event, Capture, RoadSegment, cena, duplicata e sequência. Mantém TRAIN, VALIDATION, CALIBRATION e TEST separados. O validador recusa grupos desconhecidos/sobrepostos, ordem temporal errada, label indisponível no corte e exposição YOLOX desconhecida em avaliação. Partições insuficientes ficam `BLOCKED_DATA`; não há corpus real exportado nesta rodada. Para medir o sistema combinado, verificar também imagens/grupos usados no treino ou na seleção do YOLOX. Predições fora da amostra podem ser necessárias; nenhum treino YOLOX adicional foi iniciado.

## Comandos e estado

Executar do diretório `backend` do worktree, com Python 3.12 e `-B`. Estes comandos não consultam banco quando usados sem `--dataset`:

```powershell
py -3.12 -B -m app.ml.tabular --label-form
py -3.12 -B -m app.ml.tabular --preflight --config ../datasets/metadata/tabular_xgboost_draft_config.json
py -3.12 -B -m app.ml.tabular --dry-run --config ../datasets/metadata/tabular_xgboost_draft_config.json
py -3.12 -B -m unittest tests.test_tabular_preparation_stdlib -q
```

Após obter export autorizado e pequeno, `--validate-export <json>`, `--validate-labels <json>`, `--generate-splits <export> --config <config> --output <json>` e `--validate-splits <json>` exercitam os contratos. `--export-dataset <export> --output <json>` só escreve se os pisos estruturais passarem; a autorização permanece externa. `--report --config <config>` gera resumo de bloqueios. `--evaluate-validation <json>` calcula métricas de probabilidades já produzidas, com papel VALIDATION e hashes declarados. Não acessa TEST.

`--train --config <config>`, `--calibrate` e `--shap` estão registrados, mas deliberadamente bloqueados/deferidos. Falta verificador de autorização oficial vinculado ao DatasetVersion, labels independentes, splits e ambiente isolado. O código atual **não executa fit autorizado nem salva modelo**. `train_xgboost` legado continua bloqueado por autorização. Não usar flags CLI como prova de runtime pronto. Uma implementação futura deverá fixar CPU, `hist`, uma thread, seed, early stopping apenas em VALIDATION, `best_iteration`, artefato selecionado e seus hashes; calibrador usa somente CALIBRATION; SHAP explica modelo congelado em amostra não protegida e não implica causalidade.

Dependências propostas no extra `tabular-ml` de `backend/pyproject.toml`: XGBoost 3.4.1, scikit-learn 1.9.1, SHAP 0.52.0. Nenhuma instalação foi feita neste worktree. Python 3.12 do sistema não possui XGBoost nem Pydantic; integração com taxonomia real e pytest existente ficam `EXECUTION_DEFERRED`.

`LABEL_COUNT=NOT_VERIFIED`. Nenhum acesso atual a Supabase foi feito. Não converter as oito revisões YOLOX em labels tabulares. Contagem em relatório antigo não é contagem atual.

Verificações executadas: `py -3.12 -B -m unittest tests.test_tabular_preparation_stdlib -q` (10 testes PASS), `ruff check` nos cinco arquivos Python afetados (PASS), `ruff format --check` nos três arquivos novos ou refatorados (PASS), AST de cinco arquivos (PASS), `git diff --check` (PASS), preflight e dry-run sem corpus (ambos `BLOCKED_DATA`, sem fit). `pytest` de `test_tabular_phase8.py`, suite relevante, integração com CoreService, calibração real, SHAP real, artefato e runtime XGBoost ficaram `EXECUTION_DEFERRED`: dependências ausentes no Python leve, RAM compartilhada e falta de autorização/dados. O teste visual isolado usa predicado sintético de classe; a taxonomia real não foi carregada.

## Reconciliação após YOLOX

1. Registrar HEAD e diffs novos no original sem sobrescrever arquivos; comparar `tabular.py`, teste tabular, `pyproject.toml`, `services/core.py`, protocolo e `DATA_READINESS.md` com este worktree.
2. Resolver sobreposições por função e preservar mudanças de YOLOX/relatório; não copiar arquivo inteiro por cima.
3. Confirmar artefato visual final permitido, versões de preprocessamento/postprocessamento, autorização de uso e independência dos grupos de avaliação.
4. Aprovar alvo/rubrica, coletar e adjudicar labels reais; validar snapshots, linhagem, autorização e split em ambiente próprio.
5. Instalar dependências apenas em ambiente tabular isolado após liberação de recursos; rodar testes focais, integração, lint e runtime antes de considerar merge ou treino.

Problemas OPEN: sem corpus tabular elegível, grupos de cena e quase duplicatas, versão final do detector, metadados visuais de preprocessamento, autorização vinculada ao DatasetVersion e verificador operacional de treino. Nenhum holdout protegido foi aberto. Preservar este worktree até a integração concluída.

## Atualizacao 25/09/2026: verificacao real e gates corrigidos

Esta secao substitui as afirmacoes anteriores sobre contagem nao verificada e avaliacao por papel declarado. O usuario priorizou `severity` e `priority`; `risk` exige horizonte/desfecho observavel, e `review_confirmed` permanece separado. Nenhum desses alvos recebeu labels inventados.

Consulta agregada, somente leitura, ao unico projeto Supabase acessivel (`Urmind DEV`, ref `impmeitwtusjtwjouggy`) em 25/09/2026: `events=0`, `captures=1`, `reviews=0`, `risk_assessments=0`, `predictions=0`, `detections=0`. Assim, `REAL_LABEL_COUNT=0`, `ELIGIBLE_LABEL_COUNT=0`, `GROUP_COUNT=0` e `TARGET_COUNTS={review_confirmed:0,severity:0,risk:0,priority:0}` neste banco. O schema nao contem tabela independente de labels severity/priority; `risk_assessments.severity` e `priority_score` seriam saidas de regras, nao Ground Truth humano. Nenhuma DatasetVersion tabular real foi gerada. Os oito exemplos de revisao YOLOX nao foram usados. `TARGETS_READY=[]`; todos os quatro alvos estao `TARGET_DATA_BLOCKED`.

Achado HIGH corrigido no CLI `--evaluate-validation`: requer caminhos de dataset, split, modelo, metadados de modelo, predicoes e registro oficial. Confere tamanho e SHA-256 de cada artefato registrado; recomputa hash de conteudo do dataset e split deterministico por grupos; liga versoes de dataset/target/modelo e feature schema; confere IDs e hashes de snapshots e extrai labels do dataset validado. Recusa eventos fora de VALIDATION, linhas extras/duplicadas e papel forjado. Teste sintetico passou; nenhum modelo real foi avaliado ou registrado.

Achado MEDIUM corrigido: estados `YOLOX_TRAIN_EXPOSED`, `YOLOX_VALIDATION_EXPOSED`, `YOLOX_OTHER_EXPOSED`, `YOLOX_NOT_EXPOSED_VERIFIED` e `YOLOX_EXPOSURE_UNKNOWN` tem validacao explicita. O indice le apenas manifests TRAIN/VALIDATION fornecidos e verifica bytes contra `artifact_registry.json`; TEST nao e aberto. Exposicao positiva por `source_fingerprint` propaga pelo grupo. A ausencia de correspondencia permanece UNKNOWN porque o export atual nao possui crosswalk completo Capture/imagem/cena/quase duplicatas; declaracao de `YOLOX_NOT_EXPOSED_VERIFIED` e recusada. Portanto `CROSS_MODEL_LEAKAGE_GATE=BLOCKED_EXPOSURE` para avaliacao independente do sistema combinado. Testes sinteticos de declaracao forjada e manifest adulterado passaram.

Config draft: CPU, `tree_method=hist`, `n_jobs=1`, `nthread=1`, `parallel_search=false`, tempo maximo configurado em `36000` segundos (10 horas). Preflight recusa valor acima disso; como nao houve fit, nao existe controlador runtime de prazo nem pico de RAM XGBoost medido. A RAM livre observada durante este turno variou de aproximadamente 1,04 GiB para 0,81 GiB. Processo YOLOX PID 4212 observado com aproximadamente 1,93 GiB RSS, sem qualquer acao sobre ele. Nao foi instalado pacote, iniciado treino, usado GPU, gerado SHAP/calibracao ou promovido modelo.

Verificacoes atuais: `py -3.12 -B -m unittest tests.test_tabular_preparation_stdlib -q` -> 12 PASS; preflight/dry-run sem dataset -> `BLOCKED_DATA`, `fit_called=false`; Ruff e diff conferidos ao encerrar. O teste de avaliacao usa artefato de bytes sinteticos e verifica contrato, nao inferencia do XGBoost. Suite maior, mypy, save/load, predicao real e runtime XGBoost ficam adiados por falta de corpus e memoria livre; nao equivalem a PASS.

Proxima acao: coletar e adjudicar labels humanos de severity e priority com rubricas versionadas; verificar grupos, snapshots e permissao de uso; gerar DatasetVersion e split autorizados por produtor oficial; completar crosswalk visual e validar ambientes. Depois integrar as mudancas de forma manual apos o YOLOX, instalar dependencias em ambiente tabular isolado, implementar/validar fit com limite runtime de 10 horas e executar um target por vez. O worktree permanece sem merge/commit.

### Atualizacao do contrato temporal e criterio de encerramento

`TABULAR_EXTRACTOR_VERSION=urmind-tabular-extractor-v2`, hash ordenado `46a720e398de941839017cbc98616e2f002e8b8bb9a2b8303fa368f58bea39eb`. O extrator recusa coercao de strings para booleano, contagem ou numero. Historico so vira zero/contagem se houver cobertura `VERIFIED`, ordem confiavel, `window_end_at`, `available_at` e `last_event_at` compativeis com o cutoff; informacao futura dispara `FEATURE_BLOCKED_FUTURE`, e prova ausente vira missing. O exportador valida novamente a prova temporal quando recebe features historicas numericas. Testes sinteticos tentam inserir review, evento, adjudicacao e contexto futuros, alem de historico posterior; nenhum desses valores pode passar como feature valida. O produtor real ainda nao fornece a cobertura e o timing exigidos: isso bloqueia o uso do historico, nao autoriza preencher zero.

O usuario nao fara revisao humana. Nao ha alternativa automatica valida para inventar Ground Truth de `severity` ou `priority`; a proxima etapa depende de labels independentes preexistentes ou de um processo externo de adjudicacao humana. Como o banco atual tem zero Events e zero Reviews, tambem nao ha `review_confirmed` treinavel. Nenhum dos quatro gates de dados reais (labels, features, point-in-time por linha, split/grupos) foi declarado PASS. `XGBOOST_DATA_READY=NO` e `XGBOOST_READY_TO_TRAIN=NO`.

### Estado verificado ao encerrar este turno

- `LABEL_SCHEMA_STATUS=DRAFT`; `REAL_LABEL_COUNT=0`; `ELIGIBLE_LABEL_COUNT=0`; `TARGETS_READY=[]`; `TARGETS_BLOCKED=[review_confirmed,severity,risk,priority]` no Supabase DEV acessivel.
- `FEATURE_SCHEMA_STATUS=SOFTWARE_CHECKS_PASS_REAL_DATA_BLOCKED`; `REAL_FEATURE_ROWS=0`; `MISSINGNESS_VALIDATED=NO_REAL_ROWS`; `VISION_LINEAGE_VALIDATED=NO_REAL_ROWS`.
- `POINT_IN_TIME_STATUS=SOFTWARE_CHECKS_PASS_REAL_DATA_BLOCKED`; `FUTURE_LEAKAGE_TESTS=PASS` em fixtures sinteticas; historico do produtor atual permanece missing sem cobertura temporal VERIFIED.
- `GROUP_SPLIT_STATUS=SOFTWARE_CHECKS_PASS_REAL_DATA_BLOCKED`; `GROUP_COUNT=0`; `TRAIN_GROUPS=0`; `VALIDATION_GROUPS=0`; `GROUP_OVERLAP=NOT_APPLICABLE_NO_ROWS`; `TEMPORAL_LEAKAGE=NOT_VERIFIED_REAL_DATA`.
- `XGBOOST_TRAINING_STARTED=NO`; `RUN_ID=NONE`; `MODEL_PATH=NONE`; `CALIBRATION_STATUS=BLOCKED_DATA`; `SHAP_STATUS=DEFERRED_UNTIL_MODEL_AND_MEMORY`.
- Testes finais focados: 14 `unittest` PASS. O avaliador de artefatos fornece apenas `ARTIFACT_BOUND_OFFLINE_METRICS`, com `prediction_generation_verified=false` e `scientific_validation=false`; o teste de artefato sintetico nao comprova inferencia.

### Continuação: harness de treino e verificação dos quatro bloqueios

Consulta agregada atual somente leitura ao mesmo projeto Supabase DEV em 25/09/2026: `events=0`, `reviews=0`, `risk_assessments=0`, `predictions=0`, `detections=0`, `captures=1`, `tabular_dataset_versions=0`. Portanto `REAL_LABEL_COUNT=0`, `ELIGIBLE_LABEL_COUNT=0`, `REAL_FEATURE_ROWS=0`, `GROUP_COUNT=0`, `TARGETS_READY=[]` e `TARGETS_BLOCKED=[review_confirmed,severity,risk,priority]` **nesta fonte**. Nenhuma outra fonte autorizada de labels foi identificada. Nao foi criado DatasetVersion real, split real ou fit. Os quatro gates de dados reais permanecem `BLOCKED_DATA`; os testes sinteticos nao mudam esse resultado.

O gerador de splits usa TRAIN/VALIDATION por padrao e so cria CALIBRATION/TEST com `include_holdouts=true` e grupos suficientes. Preflight exige ordem temporal, as duas classes nos dois papeis de treino, grupos isolados e features existentes ate o cutoff. Cobertura historica desconhecida permanece missing; por si so nao bloqueia uma linha se ambas as features historicas estiverem ausentes. A linhagem visual agora inclui `model_version`, SHA-256 do artefato do detector, ordem de classes, versoes de preprocessamento, postprocessamento e extracao de features. DatasetVersion com versoes visuais misturadas fica bloqueado. O produtor operacional atual guarda `model_version_id` e checksum ONNX, mas o snapshot tabular ainda nao contem todos esses campos: `VISION_LINEAGE_VALIDATED=NO_REAL_ROWS` e produtor a reconciliar. Nao foi lido o checkpoint YOLOX ativo.

O comando futuro `--train` chama um worker separado sob timeout de no maximo 36.000 segundos. O worker so importa NumPy, XGBoost e psutil depois de validar arquivos registrados, DatasetVersion, split deterministico e aprovacao vinculada aos hashes. Configura CPU, `hist`, uma thread, float32, um target, baseline de prevalencia e early stopping em VALIDATION. Monitora RAM e prazo por iteracao, salva checkpoint ao entrar em pressao de memoria e periodicamente no ultimo quinto do prazo, e registra modelo, metricas, linhagem e predicoes de VALIDATION. Timeout do supervisor encerra somente o worker XGBoost; o checkpoint mais recente deve ser conferido antes de retomar. Esse caminho **nao foi exercitado com fit**, save/load ou dados reais. Sem dados, aprovacao de uso por linha e registro oficial desses artefatos, executar o comando deve continuar bloqueado.

Comando preparado, a executar somente quando os insumos oficiais existirem, em ambiente tabular isolado e com RAM adequada:

```powershell
py -3.12 -B -m app.ml.tabular --train --dataset <dataset.json> --split <split.json> --config ../datasets/metadata/tabular_xgboost_draft_config.json --approval <approval.json> --artifact-registry ../datasets/metadata/artifact_registry.json --run-dir <diretorio-no-worktree>
```

O registro de artefatos e gerado pelo produtor oficial (`scripts/datasets/refresh_registry.py`) no checkout completo; nao editar `artifact_registry.json` manualmente. O trabalho isolado ainda nao tem insumos para atualizar o contrato oficial. O avaliador offline exige registrar tambem modelo, metadados e predicoes apos o fit e fornece apenas metricas vinculadas aos bytes; independencia cientifica e teste final exigem gate separado. Calibracao e SHAP permanecem adiados ate modelo treinado e dados separados. Nenhuma integracao ao backend publico, promocao, commit ou merge foi feita.

Verificacao local nesta continuacao: 16 testes de biblioteca padrao com fixtures sinteticas passaram, incluindo timeout de supervisor sem fit, ausencia versus zero historico, linhagem visual e autorizacao vinculada a hashes. Ruff e `git diff --check` sao gates finais desta etapa. Teste runtime do XGBoost, save/load, inferencia, mypy e avaliacao final: `EXECUTION_DEFERRED`; nao equivalem a PASS.

### Continuação: produtor visual point-in-time e teste do exportador existente

A busca de fontes mostrou um unico projeto Supabase acessivel (`Urmind DEV`) e nenhuma branch de banco. Consulta exata somente leitura: `events=0`, `reviews=0`, `detections=0`, `risk_assessments=0`, `predictions=0`, `captures=1`, `tabular_dataset_versions=0`. Na working copy original, `datasets/annotations` nao possui labels tabulares e `datasets/processed/tabular` nao existe. As anotacoes de outros datasets visuais nao foram convertidas em labels de Event do UrMind. Portanto as contagens elegiveis dos quatro targets permanecem zero na fonte acessivel.

O teste tabular preexistente revelou que `CoreService.tabular_ground_truth` produz `datetime` em memoria, mas o validador aceitava apenas texto ISO em `available_at` e `knowledge_cutoff`. O validador agora aceita ambos, preservando a exigencia de fuso e ordem temporal; `test_tabular_phase8.py` passou integralmente (25 testes).

`CoreService._feature_snapshot` agora copia para o snapshot, no instante da avaliacao, a linhagem de `model_versions.metrics.serving` quando todas as deteccoes pertencem ao ModelVersion do Event e possuem `created_at` confiavel. Copia hash do checkpoint, ordem de classes, hash do contrato de serving, status operacional e instante da ultima deteccao. O mesmo hash do contrato versiona pre e pos-processamento; `FeatureBuilder` registra sua propria versao. O exportador exige que esses valores sejam identicos a proveniencia congelada e que deteccao e metadados estivessem disponiveis ate o cutoff. Ausencia ou mistura de versoes bloqueia readiness. Nenhum snapshot antigo recebeu valores retroativos e nenhuma leitura do checkpoint ativo ocorreu.

O extrator foi versionado como `urmind-tabular-extractor-v4`, com `VISUAL_LINEAGE_VERSION=urmind-visual-lineage-v1` e `FEATURE_SCHEMA_HASH=38cd5b782733d9a30937002b8d5f4766599ac8f9c85099727a307045f280161f`; o config draft foi atualizado. As features numericas incluem agora quatro indicadores de classe visual D00/D10/D20/D40 e `bbox_area_ratio_max`, calculada de caixas normalizadas persistidas. Classes fora do contrato, caixa invalida, contagens inconsistentes e valores binarios fora de 0/1 sao recusados. Caixa ausente permanece missing; indicador zero quer dizer classe nao detectada, sem provar ausencia de problema. Os hashes v2 anteriores neste documento permanecem apenas historicos. Verificacoes finais: 17 testes leves de contrato e 100 testes pytest focados (tabular, FeatureBuilder e CoreService) passaram; nenhuma dessas fixtures comprova dados reais. O runtime XGBoost e os quatro gates de dados reais continuam bloqueados. A mudanca do produtor esta somente neste worktree e exigira reconciliacao antes de qualquer uso operacional.

### Continuação: auditoria de labels de targets pendentes

O formulario JSON canonico foi sincronizado com as features v4. Protocolo `urmind-tabular-labeling-v2-draft`, hash semantico `5d3b93d43ddd14454ba2f4a2ddaaa08dd53d1fcf327ba2e3fe3a9441b49af419`. A importacao agora pode **contar candidatos** de `severity`, `priority` e `risk` com procedencia atribuida sem aprovar seu treino. `severity` e `priority` exigem evidencia de inspecao independente e versao de rubrica; `risk` exige desfecho observado, horizonte, exposicao e follow-up temporal. `review_confirmed` so aceita Review persistida. Qualquer label de target pendente permanece com `TARGET_APPROVAL_PENDING` e `training_eligible_rows=0`; categorias propostas no formulario nao sao uma rubrica aprovada. Isso permite distinguir quantidade observada de autorizacao cientifica quando uma fonte real aparecer.

Verificacao atual do unico Supabase acessivel em 25/09/2026: `events=0`, `reviews=0`, `detections=0`, `risk_assessments=0`, `captures=1`; nenhuma fonte alternativa de labels de Event foi encontrada. Portanto `REAL_LABEL_COUNT=0` e `ELIGIBLE_LABEL_COUNT=0` nesta fonte. Um teste de contrato confere que o formulario JSON salvo corresponde exatamente ao protocolo gerado. 18 testes leves passaram. Nenhum fit foi iniciado; YOLOX permaneceu no PID 4212 na ultima observacao, com cerca de 1,04 GiB de RAM livre. Mypy foi adiado por memoria compartilhada limitada.

### Continuação: identidade do DatasetVersion

`CoreService.tabular_ground_truth` agora recebe de `build_tabular_dataset_version` um nome derivado do SHA-256 do conteudo, alem dos hashes de schema de features e labels e da versao do target. O campo `split` no DatasetVersion declara `NOT_GENERATED`: um resumo de split aleatorio anterior nao era prova de particao temporal com grupos verificados. `--export-dataset` exige nome e hash de conteudo presentes antes de gravar; o manifesto de TRAIN/VALIDATION permanece separado e ainda precisa ser gerado e verificado. Um teste CLI real confirma que export sem DatasetVersion nao grava arquivo. 19 testes leves e 25 testes tabulares existentes passaram sem acessar dados reais. O banco atual continua sem Events/Reviews e o treino permanece bloqueado.

Um teste adicional percorre `CoreService.tabular_ground_truth` com duas revisoes e snapshot sinteticos, depois valida o documento retornado contra o contrato do DatasetVersion. Confirmou nome ligado ao conteudo, hashes de schema, target version e `split=NOT_GENERATED`; readiness permaneceu `BLOCKED_DATA`. A suite tabular focada tem agora 26 testes PASS. Consulta exata repetida ao Supabase acessivel: `events=0`, `reviews=0`, `detections=0`, `risk_assessments=0`, `captures=1`; nenhum fit ou DatasetVersion real foi criado.

### Nova fonte externa: Attain v1, severidade visual de pavimento

O Supabase DEV continua sem labels de Event. A fonte externa [Attain v1](https://data.mendeley.com/datasets/nykrzdm74f/1) foi inspecionada diretamente por metadados e bytes oficiais, sem criar Events ficticios. Licenca declarada: CC BY 4.0. O artigo dos autores descreve anotacao manual por especialistas em pavimento e quadros extraidos de videos a cada 250 ms; isso exige cuidado especial com grupos de cena.

`scripts/datasets/acquire_attain_annotations.py` e o adaptador de origem, com hashes SHA-256 oficiais, emparelhamento exato imagem/anotacao e limite de armazenamento de 7 GB por fonte com piso de 10 GB livres. Foram verificados 809 arquivos WS v1 e 847 WS v2 (4.267.535 bytes de anotacoes e mapas). OS v1 tem apenas tipo de dano e nao entra em severidade. A auditoria encontrou 18.843 objetos brutos em WS v1/v2; uma caixa XML degenerada de `Faded marking` foi excluida, restando 18.842 objetos geometricamente validos. As classes dos arquivos sao Low/High; o artigo traz uma frase contraditoria sobre Medium, mas nenhum Medium foi contado nos arquivos auditados. O manifesto local ignorado pelo Git e `datasets/raw/attain/annotation_manifest.json`, SHA-256 `714ba932c8caf4d08ec484975e9a5b187e64c8260879b8d5b68ebcbc993f29a9`.

Mapeamento direto para as classes atuais do YOLOX: `Alligator crack -> D20` e `Pothole -> D40`. Existem 4.578 objetos validos nessas classes: D20 High 277 / Low 3.776; D40 High 110 / Low 415. `Linear crack` nao permite decidir D00 versus D10 e foi excluida. Esses 4.578 sao labels reais de **severidade visual do dano no dominio Attain**, nao Ground Truth de risco de acidente, prioridade nem confirmacao de Review. `RiskAssessment.severity` no UrMind significa risco oferecido a quem passa e admite unknown/low/medium/high/critical; Low/High do Attain nao deve ser copiado diretamente para esse campo sem contrato de equivalencia. O target experimental deve ser identificado separadamente como `pavement_visual_severity_low_high`.

As imagens pareadas somam 144.254.436 bytes segundo o repositorio e estao em verificacao de hash local para extrair atributos de aparencia e pesquisar duplicatas. Nenhum YOLOX foi importado, inferido ou interrompido. O novo extrator compartilhado em `backend/app/services/features.py` aceita apenas D20/D40, caixa normalizada e imagem orientada no mesmo referencial; produz geometria e descritores simples do recorte com ordem/hash fixos. Testes pequenos com imagens sinteticas passaram. Caixas humanas do Attain permanecem `ANNOTATION_BOX_PROXY`; desempenho com caixas do YOLOX do UrMind ainda nao foi medido. Captura, rotulo e sessao/rota nao possuem timestamps/IDs suficientes nessa origem; nao inventar datas nem declarar independencia de cenas. Nenhum modelo foi treinado nem integrado ate este ponto.

### Entrega atual: DatasetVersion e primeiro treino experimental de severidade visual

Os estados anteriores deste documento sao historicos. Neste passo, os 1.656 arquivos de imagem WS v1/v2 (144.254.436 bytes) foram baixados no worktree e verificados pelos SHA-256 publicados. O adaptador `scripts/datasets/acquire_attain_annotations.py --export-severity` gerou 4.578 linhas reais, uma por objeto D20/D40 elegivel. Cada linha liga imagem, anotacao, IDs da fonte, hash, classe, label original Low/High, caixa, features, grupo por hash de imagem, tempos desconhecidos explicitamente, origem humana da caixa e exposicao YOLOX desconhecida. O exportador verifica novamente os bytes de origem antes de produzir o artefato. Nenhum Event, Capture, GPS, chuva ou review ficticio foi criado.

- `DATASET_VERSION=attain-pavement-visual-severity-7280cb15abf42bc8`; `DATASET_SHA256=7280cb15abf42bc8a23aaa5c7371d1458bad199c285303454d0eef82cbf23ac5`.
- `FEATURE_SCHEMA_HASH=95f96c2a022a1919ed357bcedbd1fcc09f3161e83af67d4b7d6676c54371da37`; `LABEL_SCHEMA_HASH=f3a0b15c58f1e1e68fe0f61cda26c163c8a44641b048f1eda7689c55ee10c877`.
- Real labels: D20 High 277 / Low 3.776; D40 High 110 / Low 415. `review_confirmed`, `severity` operacional do UrMind, `risk` e `priority` tem zero labels no Supabase DEV consultado e nao foram treinados.
- Split interno `WS_V2_TRAIN_WS_V1_VALIDATION`: TRAIN 3.019 linhas / 592 hashes exatos de imagem; VALIDATION 1.559 linhas / 495 hashes exatos. Zero sobreposicao de hash exato. Uma triagem dHash 64 bits encontrou zero pares entre subsets com distancia ate 4 bits. Cenas, sequencias e ordem temporal nao puderam ser provadas; esta nao e avaliacao final independente. Todas as linhas mantem `YOLOX_EXPOSURE_UNKNOWN`.

O mesmo `backend/app/ml/tabular.py` agora suporta esse target externo sem trocar o target `review_confirmed` do caminho Event. O loader vincula hashes exatos do DatasetVersion, split, config, manifest de fonte, comprovante de imagens e autorizacao do usuario. O avaliador de VALIDATION confere IDs, fingerprints, modelo, metadados e probabilidades contra o split; um JSON com papel declarado nao basta. `predict_visual_severity_experimental` carrega o modelo salvo e usa o mesmo extrator em uma imagem/caixa fornecida, mas retorna `serving_eligible=false` e nao grava `RiskAssessment`. A funcao exige linhagem explicita quando a caixa vem de um detector. O modelo treinou com caixas humanas e nao foi testado com as caixas do YOLOX final.

Run unico concluido:

- `RUN_ID=xgb-pavement_visual_severity_low_high-20260925T190025904467Z-7280cb15`.
- `MODEL_PATH=datasets/processed/tabular/xgboost_runs/xgb-pavement_visual_severity_low_high-20260925T190025904467Z-7280cb15/model.json` (ignorado pelo Git); `MODEL_SHA256=00e238545c6269076f0d93f9cca951d31b9a27b5ceb8ff12172b2ba218041edf`.
- `XGBOOST_VERSION=3.4.1`, `GIT_SHA=e57db6182de2be37a8176b845c87e35376c32a76`, CPU, `hist`, `n_jobs=nthread=1`, profundidade 3, 300 arvores de orcamento, early stopping 20, seed 20260925. `BEST_ITERATION=20`; `BEST_VALIDATION_LOGLOSS=0.24915706467437793`. O fit terminou bem antes do limite de 10 horas.
- Baseline de prevalencia na VALIDATION: log loss 0,263581; Brier 0,068076; matriz `TN=1445,FP=0,FN=114,TP=0`. Modelo: log loss 0,249157; Brier 0,070326; matriz `TN=1426,FP=19,FN=102,TP=12`; recall High 0,1053. Derivados da mesma matriz, macro-F1 0,5624 (classe sem predicao positiva recebe F1=0) e balanced accuracy 0,5461. O ganho em log loss nao sustenta uso operacional, pois a maioria dos danos High nao foi identificada. Nao houve calibracao nem avaliacao final.
- Na mesma VALIDATION, D20: 1.349 linhas, `TN=1276,FP=11,FN=56,TP=6`; D40: 210 linhas, `TN=150,FP=8,FN=46,TP=6`. Ambos os tipos apresentam recall High baixo; estes numeros vieram apenas do avaliador ligado aos artefatos e ao split.
- `RAM_AVAILABLE_BEFORE_FIT=1031548928` bytes; `PEAK_XGBOOST_PROCESS_RSS=304054272` bytes; RAM disponivel registrada durante as iteracoes ~744 MB. O YOLOX permaneceu em execucao; nenhuma GPU, instalacao ou alteracao de `.venv` foi feita. O interpretador da `.venv` existente foi usado em modo de leitura com `-B`.

Verificacoes: 25 testes stdlib de contrato/dry-run, 2 do extrator, 3 da fonte Attain e 26 testes pytest tabulares focados passaram (56 no total), sem treino adicional. O modelo JSON foi carregado novamente e produziu matriz `(3,2)` com probabilidades identicas as tres primeiras linhas salvas; a analise offline da imagem real reproduziu a probabilidade de validacao. O avaliador offline confirmou 1.559 membros e as metricas vinculadas. Ruff focado e mypy focado (`--follow-imports=skip --ignore-missing-imports`) passaram; `git diff --check` passou. A primeira rodada de pytest sem plugin de async falhou em dois testes por configuracao do runner; a rodada focada com `pytest_asyncio.plugin` passou 26/26. As fixtures sinteticas comprovam software, nao qualidade cientifica.

Arquivos de codigo alterados nesta fase: `backend/app/ml/tabular.py`, `backend/app/services/features.py`, `scripts/datasets/acquire_attain_annotations.py`, `backend/tests/test_external_tabular_contract.py`, `backend/tests/test_severity_detection_features.py`, `scripts/datasets/test_acquire_attain_annotations.py`, `datasets/metadata/attain_severity_xgboost_draft_config.json`, este handoff e `docs/ml/DATA_READINESS.md`. Alteracoes tabulares de fases anteriores continuam no worktree em `backend/app/services/core.py`, `backend/app/services/__init__.py`, `backend/pyproject.toml` e testes correspondentes. Artefatos reais e dados brutos ficam ignorados pelo Git dentro do worktree; preservar ate decidir sua retencao e reproducao.

Pendencias para reconciliacao apos o YOLOX: comparar `tabular.py`, `features.py`, `core.py`, `services/__init__.py`, `pyproject.toml` e docs com a working copy principal; confirmar o artefato YOLOX final e suas versoes; obter grupos de sessao/rota ou desenhar avaliacao fora da amostra; provar exposicao das imagens ao treino/selecao YOLOX; medir com caixas reais do detector; definir se o alvo visual tem lugar proprio na API ou uma rubrica que demonstre equivalencia com `RiskAssessment.severity`; decidir limiar e calibracao com dados separados; fazer avaliacao final congelada; somente depois considerar integracao operacional. O candidato permanece `EXPERIMENTAL_CANDIDATE`, sem merge, commit, push, promocao ou escrita no backend publico. `XGBOOST_READY_FOR_INTEGRATION=NO`; `CALIBRATION_STATUS=BLOCKED_DATA`; `SHAP_STATUS=DEFERRED`.

### Continuacao: diagnostico da classe High e segundo treino controlado

O primeiro run foi reavaliado com os mesmos 1.559 membros de VALIDATION. Em TRAIN havia 273 High e 2.746 Low. WS v2 (TRAIN) e WS v1 (VALIDATION) diferem muito na geometria das anotacoes: area relativa media das caixas D20 Low 0,010 versus 0,042, respectivamente. O limiar 0,5 do primeiro modelo acertou 12/114 High. Reduzir para 0,2 acertaria 65/114, mas geraria 341 falsos positivos; esse teste usa a mesma VALIDATION de early stopping e nao autoriza um limiar operacional.

Foi treinado **um** segundo candidato sequencial, com peso positivo `2746/273=10,058608`, derivado somente de TRAIN, `eval_metric=aucpr`, direcao `maximize`, CPU `hist`, uma thread e os mesmos DatasetVersion/split/seed. O harness agora valida a combinacao de metrica/direcao, registra o peso calculado e faz preflight de pelo menos dez exemplos de cada nivel por tipo de dano em cada papel. Nenhuma busca paralela ou terceiro target foi iniciado.

- `RUN_ID=xgb-pavement_visual_severity_low_high-20260925T202512034859Z-7280cb15`; config `datasets/metadata/attain_severity_xgboost_balanced_config.json`, aprovada apenas para treino experimental pelos hashes exatos.
- `MODEL_SHA256=0ccb78dab0ca2207efa9b2765ee9d9ccbc3318908908bf554658928ed7f5cb2c`; `BEST_ITERATION=0`; `BEST_VALIDATION_AUCPR=0,1599684568`.
- VALIDATION: average precision 0,157207, log loss 0,712298, Brier 0,259271; `TN=819,FP=626,FN=11,TP=103`. A melhoria de recall no limiar 0,5 custa 626 falsos positivos e piora a ordenacao frente ao primeiro run (average precision 0,194977). O segundo modelo foi salvo e recarregado; tres probabilidades reproduziram exatamente o arquivo de validacao. `PEAK_XGBOOST_PROCESS_RSS=303951872` bytes; `RAM_AVAILABLE_BEFORE_FIT=2264625152` bytes; YOLOX nao foi tocado.

Uma referencia de prevalencia unica era fraca para duas classes de dano. O avaliador agora calcula, **sem olhar VALIDATION durante o ajuste**, um baseline de prevalencia separado para D20 e D40 com suavizacao de Laplace. Nesse mesmo split, o baseline por classe obteve average precision 0,152718, log loss 0,2462 e Brier 0,0645. Primeiro XGBoost: average precision 0,194977, log loss 0,249157, Brier 0,070326. Segundo: average precision 0,157207, log loss 0,712298, Brier 0,259271. **Ambos falham o gate de comparacao com o baseline por classe** em log loss e Brier. Nenhum foi selecionado para uso operacional, calibrado, promovido ou integrado; os dois runs historicos permanecem intactos. A avaliacao segue interna e sem holdout independente.

Inspecao somente leitura do contrato do processo YOLOX ativo (PID 4212) mostrou TRAIN `detection_experimental_20260925_train.jsonl` com 23.827 linhas e VALIDATION `detection_validation_authorized.jsonl` com 3.858; todos os registros lidos declaram `dataset_id=rdd2022`, `source_version=2022-crddc`, sem caminhos Attain. SHA-256 dos manifests: TRAIN `bc4408412642911f3759223f71542698617823221c1728e25ffe299c123a838c`, VALIDATION `aa686ab48c53bdca5f58a099d91c4697275912bdaa8eb491273b5794b7e26d7a`. Isso prova ausencia de Attain **declarado diretamente nesses dois manifests**, mas nao prova independencia de todas as imagens/cenas nem de pretreino. `YOLOX_EXPOSURE_UNKNOWN` e o bloqueio de avaliacao combinada permanecem.

Retomada: manter os dois runs sem sobrescrever; obter fonte de severidade visual com grupos/sessoes independentes ou dados UrMind com rubrica operacional apropriada; depois do YOLOX final, validar caixas reais e linhagem, separar avaliacao final e testar calibracao somente com papel proprio. Nao escolher outro hiperparametro por repetidas consultas a WS v1. Sem evidencia nova, a proxima mudanca de treino seria ajuste sobre a mesma VALIDATION e nao resolveria o gate cientifico.

### Continuacao: reproducao completa da VALIDATION e incerteza por imagem

O CLI `--evaluate-validation` da fonte externa agora recarrega o JSON de cada
modelo em CPU com uma thread, monta a matriz `float32` na ordem do schema e
reproduz **todas** as 1.559 probabilidades de VALIDATION. Divergencia de
probabilidade ou formato bloqueia a avaliacao. Os dois runs passaram, com
`prediction_generation_verified=true`, `verified_rows=1559` e runtime
XGBoost 3.4.1. A verificacao continua interna: nao abre TEST e nao certifica
independencia de cena, calibracao nem equivalencia ao campo operacional.
Um teste adversarial com probabilidade alterada foi rejeitado pelo recarregamento
do modelo. O avaliador tambem recusa `model_version` que nao coincida com o
`run_id` e metadados que aleguem promocao. Os 27 testes leves de contrato
passaram; Ruff, mypy focado e `git diff --check` passaram apos esta mudanca.

Reamostragem exploratoria deterministica de 400 amostras dos 495 grupos de
imagem da VALIDATION comparou cada modelo ao baseline D20/D40 ajustado so em
TRAIN. Para o primeiro run, a diferenca de average precision (modelo menos
baseline) teve mediana `+0,0451` e intervalo percentil 95% `[-0,0156,+0,1092]`;
o de Brier teve mediana `+0,0056` e intervalo `[+0,0003,+0,0103]` (menor e
melhor). Para o segundo run, a diferenca de log loss teve mediana `+0,4653` e
intervalo `[+0,4279,+0,4971]`. Isto mede variacao por hash exato de imagem
apenas neste split ja usado para selecao, nao incerteza por cena nem
generalizacao de dominio. Reforca a decisao de nao selecionar nenhum modelo.

Triagem focal de fontes adicionais: a pagina oficial do
[EGY_PDD](https://psu.edu.eg/en/egy_pdd-dataset/) informa 2D para deteccao e
3D para quantificar severidade, mas exige acordo assinado e liberacao para
uso academico; as anotacoes por objeto nao foram inspecionadas. O artigo
primario do [CNRDD](https://www.mdpi.com/2076-3417/12/15/7594) descreve
graus mild/moderate/severe por caixa e indica o portal dos autores, que nao
resolveu DNS nesta verificacao. Nenhuma dessas fontes foi adicionada ao
corpus ou tratada como validacao independente. Nao reutilizar um mirror sem
conferir licenca, rotulos, grupos e linhagem da publicacao original.

### Correcao posterior: mesmo trecho nos dois lados do split Attain

Inspecao dos bytes de imagem ja verificados mostrou quadros consecutivos muito
correlacionados em WS v2: 473 de 846 pares adjacentes tiveram dHash de 64 bits
com distancia ate 4. Entre WS v1 e WS v2, o audit do **corpus elegivel**
encontrou 193 pares candidatos a ate 12 bits envolvendo 143 imagens. A
comparacao lado a lado de `Attain_SMP_WS_v1_000269.jpg` com
`Attain_SMP_WS_v2_000439.jpg` mostra o mesmo trecho de via e carros, com
distancia dHash 11. Ambas possuem linhas tabulares. Portanto, o antigo
`WS_V2_TRAIN_WS_V1_VALIDATION` tem sobreposicao conhecida de cena apesar de
`exact_image_overlap=0`; seus dois runs sao historicos e nao servem como
validacao independente. dHash e uma triagem de candidatos, nao prova de que
todos os pares de mesma cena foram encontrados.

O produtor canonico `scripts/datasets/acquire_attain_annotations.py` agora
gera um audit deterministico sobre imagens elegiveis com SHA-256 oficial,
dHash, identidade de imagem e pares cruzados. Audit derivado
`datasets/processed/tabular/attain-cross-subset-dhash12-33e0701adc723a69.json`
(SHA-256 de arquivo `223953ddc2e3d61127b24764e2775d1dfe501abd48dc96e5c8235ee043db9522`).
O novo split `attain-pavement-visual-severity-7280cb15abf42bc8-split-dhash12-33e0701a.json`
mantem o mesmo DatasetVersion e exclui os dois lados de cada par candidato:
`EXCLUDED=522`, `TRAIN=2652` (504 hashes de imagem), `VALIDATION=1404`
(440 hashes), sobreposicao exata e candidatos dHash ate 12 cruzando os papeis
iguais a zero. Em TRAIN restam 250 High; em VALIDATION, 107. A independencia
de todas as cenas, rotas e sequencias continua `UNKNOWN`.

Novo preflight exige o audit; aprovacoes, split e run vinculam seu hash. O
exportador nao cria mais um split legado; treino e avaliacao externos sem
`--similarity-audit` sao rejeitados. Um teste adversarial recusa remocao de
pares do audit e papel forjado de uma linha `EXCLUDED`. O split antigo foi
rejeitado quando confrontado com o audit real; os arquivos historicos foram
preservados, sem sobrescrita.

Um unico fit de reposicao com os parametros originais, CPU `hist`, uma thread
e early stopping em VALIDATION foi concluido, sem tocar no YOLOX:

- `RUN_ID=xgb-pavement_visual_severity_low_high-20260925T205942931568Z-7280cb15`;
  `MODEL_SHA256=229de230a9206c5da6203e6f89e3ec51047d87e8eaece470f63f43a1a18973c0`;
  `BEST_ITERATION=0`, melhor log loss 0,286538.
- Nas 1.404 linhas de VALIDATION: log loss 0,286539, Brier 0,077717,
  average precision 0,113032; `TN=1297,FP=0,FN=107,TP=0` no limiar 0,5.
  Baseline por tipo de dano ajustado apenas em TRAIN: log loss 0,253224,
  Brier 0,066910, average precision 0,162925. O modelo falha nas tres
  metricas. O avaliador recarregou o artefato e reproduziu as 1.404
  probabilidades. Nao houve calibracao, SHAP, TEST ou promocao.
- `RAM_AVAILABLE_BEFORE_FIT=2462769152` bytes;
  `PEAK_XGBOOST_PROCESS_RSS=304259072` bytes. O processo YOLOX continuou
  ativo. O run permanece `EXPERIMENTAL_CANDIDATE` como registro historico,
  sem selecao para uso operacional.

Comando atual para o preflight externo, a partir de `backend` no worktree:

```powershell
python -B -m app.ml.tabular --preflight --config ../datasets/metadata/attain_severity_xgboost_draft_config.json --dataset ../datasets/processed/tabular/attain-pavement-visual-severity-7280cb15abf42bc8.json --split ../datasets/processed/tabular/attain-pavement-visual-severity-7280cb15abf42bc8-split-dhash12-33e0701a.json --similarity-audit ../datasets/processed/tabular/attain-cross-subset-dhash12-33e0701adc723a69.json
```

Verificacoes apos a correcao: 28 testes stdlib do contrato, 3 testes do
produtor Attain, 26 testes pytest tabulares focados, Ruff, mypy focado e
`git diff --check` passaram. O avaliador recarregou o artefato de SHA-256
`229de230a9206c5da6203e6f89e3ec51047d87e8eaece470f63f43a1a18973c0`
e reproduziu as 1.404 probabilidades do split auditado; o processo YOLOX
continuava ativo no encerramento desta verificacao. Este
resultado nao habilita uma nova rodada de ajuste na mesma VALIDATION; a
proxima evidencia util e um corpus com grupos de cena/rota verificaveis e
labels equivalentes a decisao operacional desejada.

### Fechamento do gate de treino pela API e sensibilidade de sequencia

O caminho publico `train_xgboost(VerifiedTrainingBundle, ...)` agora valida o
audit de similaridade e o split externo **antes** de importar `psutil` ou
`xgboost`. Isso fecha a possibilidade de montar um bundle manual com o split
historico e contornar a exigencia que ja existia no CLI. Teste sintetico
rejeita tanto audit ausente quanto split historico apresentado com audit;
nenhum fit ocorre nesses casos.

Uma analise somente leitura dos nomes numerados de imagem mediu a sensibilidade
da quarentena: os 193 pares dHash envolvem diretamente 143/1.087 imagens
elegiveis; expandir cada candidato em 5, 10 ou 25 numeros vizinhos marcaria
respectivamente 475, 625 ou 887 imagens elegiveis. Esses vizinhos **nao**
foram declarados duplicatas e nenhum split ou modelo foi refeito com eles.
A grande diferenca reforca que dHash isolado nao estabelece independencia de
cenas; faltam IDs oficiais de video, rota e sessao.

Triagem de fonte complementar: o [cartao oficial do PaveBench](https://huggingface.co/datasets/MML-Group/PaveBench)
descreve imagens top-down e perguntas de severidade, com licenca
`CC BY-NC-SA 4.0`. Nao se confirmou ali um rotulo independente de severidade
por objeto compativel com este target nem permissao para uso fora das condicoes
da licenca. Nenhuma linha foi importada. O portal original do CNRDD permanece
sem anotacoes acessiveis/verificadas neste worktree.

Verificacao focal atual: 29 testes stdlib tabulares, 26 testes pytest de
`test_tabular_phase8.py`, Ruff, mypy e `git diff --check` passaram. Nenhum
treino, import de YOLOX, acesso a GPU, promocao ou integracao foi executado
nesta continuacao.

### Fonte das decisoes atuais no UrMind

Novos `RiskAssessment.factors.decision_trace` produzidos pelo CoreService
declaram schema `urmind-decision-trace-v2` e
`severity_source=risk_source=priority_source=rules`. Isso descreve o fluxo
que existe hoje; os valores e as regras nao foram alterados. Traces historicos
v1 permanecem intactos. Nenhum modelo Attain foi ligado ao Worker ou ao
backend publico. O teste focal de persistencia do trace passou. Uma futura
fonte XGBoost so pode substituir `severity_source` depois de equivalencia
do alvo, avaliacao independente e promocao verificadas.

No produtor de snapshots reais, a linhagem visual so e congelada quando
**todas** as deteccoes vinculadas possuem `created_at` com fuso horario.
Uma deteccao sem esse instante deixa o hash do checkpoint indisponivel no
snapshot, em vez de atribuir disponibilidade temporal sem prova. Dois testes
focados do CoreService passaram; Ruff e mypy do arquivo tambem passaram.

### Deslocamento de dominio medido antes de novo fit

O preflight do split Attain auditado agora calcula uma analise **descritiva**
de formato de imagem e medias das features por papel, sem acessar labels nem
ajustar modelo. Em TRAIN, 618/2.652 linhas sao 640x640, 1.381 sao 1479x508
e 653 sao 1920x1080. Em VALIDATION, todas as 1.404 linhas sao 640x640.
A distancia de variacao total entre essas distribuicoes de formato e
`0,766968`; a altura media normalizada da caixa e `0,1210` em TRAIN e
`0,2682` em VALIDATION. O log do run anterior mostra log loss de TRAIN
caindo de `0,2714` para `0,1964` entre iteracoes 0 e 10, enquanto o da
VALIDATION sobe de `0,2865` para `0,3109`. Esse contraste e compativel
com deslocamento de dominio e/ou sobreajuste, mas nao identifica causa.

O avaliador e futuros metadados de run incluem o mesmo diagnostico. Um teste
sintetico verifica distribuicoes completamente separadas (`TV=1`) e que o
diagnostico nao consulta labels. O preflight real passou sem fit; 11 testes
externos focados, Ruff, mypy e `git diff --check` passaram. O modelo anterior
e seus hashes permanecem inalterados. Ajustar pesos ou features repetindo a
mesma VALIDATION nao produziria avaliacao final independente.

### Auditoria final das fontes acessiveis — 25/09/2026 21:29 UTC

Leitura somente dos cabecalhos das **1.087 imagens elegiveis** do Attain:
495 WS v1 e 592 WS v2. Nenhuma contem tags EXIF ou blocos embutidos de
data, camera, GPS ou XMP. Nao ha metadado de cena/rota recuperavel **por esse
caminho**. Isso nao demonstra que as cenas sejam distintas; os manifests e
o pacote original foram examinados na continuacao abaixo. Nenhuma imagem ou
anotacao foi modificada.

Nova consulta agregada somente leitura ao projeto `Urmind DEV`
(`impmeitwtusjtwjouggy`): `events=0`, `reviews=0`, `detections=0`,
`risk_assessments=0`, `predictions=0`, `captures=1`. Portanto os labels
internos elegiveis seguem `review_confirmed=0`, `severity=0`, `risk=0`,
`priority=0`. A busca restrita por tabelas de label/ground truth/review
encontrou apenas `reviews`, vazia. `public.tabular_dataset_versions` **nao
existe** neste banco. As notas cronologicas anteriores que dizem
`tabular_dataset_versions=0` nao devem ser interpretadas como contagem
verificada; a consulta atual distinguiu tabela ausente de tabela vazia.

Com zero Ground Truth operacional e sem grupos oficiais de cena na fonte
externa, nenhum novo target operacional, split final ou fit adicional foi
autorizado por evidencia. Os artefatos experimentais Attain ficam preservados,
sem promocao; o backend continua usando regras para severidade, risco e
prioridade. Retomar somente quando houver uma fonte nova com labels compativeis
e grupos verificaveis, ou outro dado real que altere esses gates.

## Diagnostico focal do Attain e da comparacao — sem novo fit

### Run e validade da comparacao

O run preservado e
`xgb-pavement_visual_severity_low_high-20260925T205942931568Z-7280cb15`.
Seu alvo e `pavement_visual_severity_low_high` v1, por objeto D20/D40,
anotado no Attain como Low/High. Isto **nao** e `RiskAssessment.severity`,
`review_confirmed`, risco nem prioridade. O DatasetVersion e
`attain-pavement-visual-severity-7280cb15abf42bc8`, SHA-256 de conteudo
`7280cb15abf42bc8a23aaa5c7371d1458bad199c285303454d0eef82cbf23ac5`;
o split auditado tem SHA-256 de arquivo
`b69621289f3aaa43d14d54f4b80374a94bf0510dad1438fe23e5e22b17b6c190`.
TRAIN tem 2.652 objetos/504 hashes de imagem (2.402 Low, 250 High);
VALIDATION tem 1.404 objetos/440 hashes (1.297 Low, 107 High);
522 objetos/143 hashes foram excluidos. As 11 features e sua ordem estao no
`run.json`; hash do schema
`95f96c2a022a1919ed357bcedbd1fcc09f3161e83af67d4b7d6676c54371da37`.
O artefato `model.json` continua com SHA-256
`229de230a9206c5da6203e6f89e3ec51047d87e8eaece470f63f43a1a18973c0`.

O avaliador existente foi executado novamente **sem carregar XGBoost, Torch,
imagens ou TEST**. Validou hashes dos artefatos, identidade/role das 1.404
linhas, hashes de imagem/anotacao, versoes e probabilidades salvas. Calculou
baseline e modelo nas mesmas linhas e nos mesmos labels. O baseline exato e
`train_only_smoothed_damage_type_prior`: prevalencia High por D20/D40,
suavizacao de Laplace, estimada exclusivamente em TRAIN. Na VALIDATION,
baseline: log loss `0,2532241617`, Brier `0,0669099074`, AP `0,1629254423`;
XGBoost: log loss `0,2865385038`, Brier `0,0777168693`, AP
`0,1130324116`. Ambos previram zero High no limiar 0,5; o XGBoost tem
`BEST_ITERATION=0`, `BEST_SCORE=0,2865384930`. As predicoes do modelo salvo
haviam sido reproduzidas integralmente em verificacao anterior. O comparativo
e **aritmeticamente comparavel neste split interno**, mas **nao comprova
generalizacao independente**: falta identidade oficial de cena/rota/sessao,
a mesma VALIDATION ja orientou tres runs e as fontes diferem de dominio.
Os dois primeiros runs usaram ainda o split antigo com uma cena cruzada
confirmada. Nenhum run foi promovido, calibrado ou integrado. O resultado nao
identifica, por si, um defeito da arquitetura XGBoost.

### Formato real, transformacao e features

Os 1.087 arquivos de imagem elegiveis foram inspecionados somente por cabecalho
para formato/modo/dimensoes, sem repetir a busca EXIF: todos sao JPEG RGB.
WS v1: 495 imagens `640x640`; WS v2: 134 `640x640`, 316 `1479x508`,
142 `1920x1080`. No corpus completo elegivel, WS v1 tem 1.159 objetos com
caixa YOLO normalizada e 400 com poligono YOLO; WS v2 tem 3.019 objetos com
caixa em pixels Pascal VOC XML, dos quais 110 tambem trazem poligono. Em todo
WS v2 ha 985 objetos com caixa e poligono; os retangulos envolventes diferem
no maximo 1,5 pixel. O importador agora rejeita divergencia acima de 2 pixels
ou poligono malformado, quando este existe; todos os 847 XML mantiveram a
mesma contagem de objetos/exclusoes do manifesto na nova checagem. O produtor
converte cada poligono YOLO em seu retangulo envolvente e cada caixa XML em
coordenadas normalizadas, verifica
dimensoes XML contra a imagem e usa `build_severity_detection_features()`
para todas as linhas. O extrator recorta do frame completo, converte para RGB
e redimensiona o recorte para `32x32`; a analise offline usa o mesmo extrator
e a mesma ordem de 11 features. Nao ha transformacao aprendida a ser ajustada
em todo o corpus. Uma verificacao real leve recalculou as 11 features em cinco
amostras: YOLO caixa, YOLO poligono e XML nas tres resolucoes, com diferenca
maxima zero para o export existente. Teste sintetico novo compara a conversao
YOLO poligonal com XML de uma mesma caixa. Isso verifica os caminhos medidos,
nao todos os pixels/labels nem equivalencia estatistica dos dominios.

No split do run, TRAIN usa apenas WS v2/XML e VALIDATION apenas WS v1/YOLO.
O preflight/avaliador agora registra tambem o protocolo de anotacao por role:
`PASCAL_VOC_XML_PIXEL_BOX=2652` em TRAIN e
`YOLO_TXT_NORMALIZED_BOX_OR_POLYGON=1404` em VALIDATION; variacao total
`1,0`. A variacao total das dimensoes e `0,7669683258`. A altura media
normalizada da bbox e `0,1210` contra `0,2682`. A diferenca nao e JPEG/PNG
nem RGB/BGR, tampouco uma ordem diferente de features: e resolucao,
enquadramento/protocolo de caixa e possivelmente camera/cena. Atribuir a queda
de metrica a uma dessas causas isolada seria inferencia sem experimento.
Nenhum arquivo raw foi convertido ou alterado para ocultar o dominio.
O [artigo original do Attain](https://pmc.ncbi.nlm.nih.gov/articles/PMC12167439/)
descreve os arquivos publicados como redimensionados para `640x640` ou
`1479x508`, mas 142 imagens WS v2 elegiveis da publicacao v1 local tem
`1920x1080` nos proprios cabecalhos. Essa discrepancia entre texto e bytes
tambem aparece no mapa oficial `Attain_SMP_WS_V2.0_data.yaml`, que lista so
os dois primeiros tamanhos. O extrator usa as dimensoes reais de cada
arquivo. Sem uma tabela de camera/origem por imagem, a resolucao nao
identifica o aparelho.

O mapeamento sintatico de labels continua D20 = `Alligator crack`, D40 =
`Pothole`, com High/Low separados do tipo e nunca incluidos como feature.
O mapa WS v1 contem dois IDs originais, `Alligator crack - Low` (479 objetos
na anotacao) e `Alligator crack - low` (808), reunidos como LOW; ambos os
nomes originais permanecem no export. O mapa oficial usa IDs distintos
(`1` e `2`): o primeiro ocorre em 112 imagens e o segundo em 314; **nenhuma
imagem contem os dois IDs**. As faixas numericas de nomes se sobrepoem, mas
nao demonstram sessao ou lote. A ausencia de coocorrencia pode refletir
convencao por lote e nao prova que os significados diferem ou coincidem.
A equivalencia semantica dessa variante
e a rubrica tecnica Low/High entre WS v1 e WS v2 ainda exigem esclarecimento
dos autores. Os dois YAML oficiais enumeram classes e contagens, sem definir
criterios de severidade. O artigo original descreve niveis de severidade de
maneira internamente inconsistente (fala em dois niveis e enumera Low,
Medium e High)
e nao fornece nesta publicacao um limiar por dano que resolva o mapeamento.
O preflight e o avaliador historico agora devolvem
`label_mapping_status=SYNTACTIC_ONLY_RUBRIC_UNVERIFIED` sem mudar hashes ou
metricas dos artefatos. A chamada direta de fit informa tambem
`LABEL_RUBRIC_UNVERIFIED` antes de qualquer import pesado. No preflight real,
as 4.578 linhas mantiveram `BLOCKED_GROUP_EVIDENCE`, `fit_called=false`,
`scene_group_status=UNKNOWN`; XGBoost e Torch nao foram importados.
O gate da rubrica e **independente** do gate de grupos: um teste simula um
retorno de split com grupos verificados e comprova que `train_xgboost()`
ainda rejeita `LABEL_RUBRIC_UNVERIFIED` antes de importar `psutil`. Assim,
resolver so os grupos nao libera um novo fit com o mapeamento atual. Os
14 testes externos focados passaram, sem executar treino.
O parser le `Medium` quando aparece no vocabulario geral da fonte, mas o
target binario deste DatasetVersion admite apenas `LOW` e `HIGH`. O produtor
agora rejeita qualquer `Medium` em D20/D40 antes de criar uma linha, em vez
de convertê-lo implicitamente em zero. A varredura focal dos 1.656 arquivos
de anotacao locais encontrou zero `Medium` em D20/D40; as 4.578 linhas do
export preservado mantem exatamente o target binario original (4.191 LOW,
387 HIGH). O artefato anterior nao foi reescrito.
Confianca de detector, medida fisica, contexto, GPS e horarios
de captura nao foram fabricados. As caixas sao anotacoes humanas, nao saidas
do YOLOX; seu uso em producao exigiria outra DatasetVersion com detector
identificado e teste do desvio de caixas.

### Grupos e inventario oficial

O inventario de metadados da API oficial [Mendeley Attain v1](https://data.mendeley.com/datasets/nykrzdm74f/1)
foi percorrido nas cinco paginas (`4589` arquivos): 637 pares OS v1,
809 pares WS v1, 847 pares WS v2 e tres mapas YAML. Nao consta arquivo
nomeado de rota, sessao, camera, sequencia ou manifesto adicional. O OS v1
nao fornece severity por objeto e nao entrou neste DatasetVersion. Os
manifests locais de 1.656 pares WS guardam IDs oficiais de arquivo e hashes,
mas seus campos de grupo sao `SCENE_GROUP_UNKNOWN` e `captured_at=null`.
Os YAML WS v1/v2 declaram caminhos relativos `train`, `val` e `test`, mas
nao listam quais IDs de imagem pertencem a cada papel nem fornecem grupo
de video/rota. A API oficial de arquivos confirmou `items 0-0/809` no unico
folder ID de imagens WS v1 e `items 0-0/847` no unico folder ID de imagens
WS v2; a aquisicao completa ja havia validado o `folder_id` de cada arquivo.
Assim, essas tres strings nao sao um split verificavel deste export.
A API de hierarquia de pastas exigiu autenticacao (HTTP 401), sem tentativa
de contornar esse acesso; a conclusao aqui se limita aos arquivos publicados
e aos folder IDs que a API anonima de arquivos retorna.
Data de upload na API nao e data de captura. Numeracao do arquivo tambem
nao comprova rota nem sessao. A pagina oficial informa cameras em para-brisas
dianteiro e traseiro, sem atribuir camera a cada imagem publicada.
O [artigo dos autores](https://pmc.ncbi.nlm.nih.gov/articles/PMC12167439/)
detalha cerca de 20 horas de video continuo, frames extraidos a cada 250 ms
e 2.293 imagens selecionadas depois por qualidade/visibilidade. Nao fornece
IDs de video, sessao, rota, camera ou frame por arquivo publicado. Essa origem
torna plausivel dependencia entre imagens selecionadas, sem demonstrar que
arquivos numericamente vizinhos sejam frames consecutivos.

`GROUP_VERIFIED`: 1.087 grupos de **imagem exata** por SHA-256, mantendo
todos os seus objetos juntos; zero grupos oficiais de rota/sessao.
`GROUP_INFERRED`: 193 pares dHash ate 12 bits entre subsets, 143 imagens,
18 componentes candidatos; pelo menos o par `WS v1 000269` / `WS v2 000439`
foi visto no mesmo trecho. Similaridade nao prova que os 18 componentes
sejam rotas nem encontra todos os pares dependentes.
`GROUP_UNKNOWN`: atribuicao de cena/rota/sessao das 1.087 imagens. O split
auditado tem sobreposicao zero de SHA-256 e zero pares candidatos dHash
cruzando TRAIN/VALIDATION, mas independencia exaustiva e ordem temporal
continuam `NOT_VERIFIED`. Nao criar um novo TEST a partir do mesmo material
nem escolher outra seed apos consultar esta VALIDATION.

A sensibilidade na borda da quarentena encontrou **18 pares TRAIN/VALIDATION
com dHash 13**, enquanto o limite do audit era 12. A menor distancia restante
e 13. Uma inspecao visual focal encontrou falsos candidatos e o par
`WS v2 000443` / `WS v1 000277` sugestivo de trecho relacionado; esta e
uma inferencia visual, nao identificacao oficial de rota. O preflight e o
avaliador agora exibem `similarity_boundary` com limite, menor distancia,
pares na distancia seguinte e `scene_independence_verified=false`, sem
consultar labels ou ajustar o modelo. O avaliador do run anterior confirmou
`pairs_at_cutoff_plus_one=18` e manteve seu status/metricas historicos.
Nenhum split, DatasetVersion ou artefato foi reescrito. O resultado reforca
que “zero pares ate 12” nao e prova de independencia.
Para impedir novo fit com esse contrato ainda sem grupos de cena, o preflight
real retorna `BLOCKED_GROUP_EVIDENCE` e `fit_called=false`. A chamada direta
`train_xgboost()` tambem rejeita o bundle antes de importar `psutil` ou
`xgboost`; teste sintetico cobre esse caminho. A avaliacao **historica**
continua disponivel por IDs e hashes e nao foi apagada. Um futuro split
verificado precisara trazer grupos de cena rastreaveis e atualizar o contrato
de validacao; nenhum ID foi inventado aqui.

### Registro canonico e proxima evidencia

O ORM `backend/app/models/core.py` e a migration
`backend/alembic/versions/0002_align_urmind_core.py` definem o registro
canonico `public.dataset_versions`; `backend/app/datasets/registration.py`
monta seu payload. A existencia dessa tabela no banco DEV **nao foi
verificada nesta rodada**. A tabela diferente `public.tabular_dataset_versions`
estava ausente na ultima consulta, nao vazia. A fonte externa ja tem contrato
local `EXTERNAL_ANNOTATION` e DatasetVersion por conteudo em
`backend/app/ml/tabular.py`, sem Events, Reviews ou GPS ficticios. Nenhuma
escrita no banco/migration foi feita; o modelo continua fora do backend
publico e `severity_source=rules`.

Para uma nova comparacao controlada, obter **IDs oficiais de video/sessao,
rota/trecho, camera e frame por imagem** cobrindo os 1.087 arquivos elegiveis,
ou uma fonte externa genuinamente separada, com labels Low/High por objeto,
rubrica compativel e grupos verificaveis. Esclarecer a variante Low/low e
equivalencia de severidade entre subsets. Com esses dados, fixar split por
grupos e tempo antes de qualquer escolha de features/parametros, preparar
avaliacao final independente, e comparar baseline/XGBoost nas mesmas linhas
sem reutilizar TEST. Para o sistema YOLOX + XGBoost, provar exposicao das
imagens ao detector e avaliar suas caixas reais apos o artefato YOLOX final.
Enquanto isso, `READY_FOR_CONTROLLED_RETRAINING=NO` e
`XGBOOST_READY_FOR_INTEGRATION=NO`.

Solicitacao objetiva aos autores, **preparada e nao enviada**: “Para o
Attain v1, poderiam fornecer uma tabela que associe cada arquivo de imagem
WS v1/WS v2 ao video/sessao original, rota ou trecho, camera dianteira ou
traseira, indice/tempo do frame e eventuais imagens duplicadas entre subsets?
Tambem pedimos a rubrica Low/High por dano, a relacao entre os IDs
`Alligator crack - Low` e `Alligator crack - low` (que nao coocorrem em uma
imagem), inclusive se representam lotes/avaliadores diferentes, e se a
rubrica permaneceu identica entre WS v1 e WS v2. Por que 142 arquivos WS v2
publicados tem `1920x1080` se o artigo descreve imagens finais apenas em `640x640` ou
`1479x508`? Esses metadados serao usados apenas para
separar cenas relacionadas na avaliacao, preservando os arquivos originais.”

Revisao focal: sem fonte de leakage futura nova, label nos nomes de features,
fit, TEST, inferencia YOLOX, GPU, mudanca no ambiente compartilhado ou escrita
na working copy original. O teste de formato mostra um **diagnostico** mais
preciso, nao uma correcao estatistica do dominio. Os gates de grupos de cena,
rubrica/target operacional e avaliacao independente permanecem abertos.

| Gate focal | Evidencia desta rodada | Estado |
|---|---|---|
| Origem, licenca e bytes | Mendeley v1/CC BY 4.0; hashes ja vinculados ao DatasetVersion | PASS para procedencia local |
| Label por objeto | Low/High nos TXT/XML; dois IDs Low/low em WS v1 | PASS sintatico; rubrica semantica UNKNOWN |
| Caixa e feature | YOLO caixa/poligono ou XML normalizados; extrator unico; cinco amostras reais recomputadas | PASS nos caminhos verificados |
| Cena/rota/camera | EXIF ausente; inventario oficial e manifests sem IDs por imagem | BLOCKED_DATA para independencia |
| Duplicatas | 1.087 hashes exatos; 193 pares dHash ate 12 em 18 componentes; 18 pares cruzados a 13 | PASS so para identidade exata/quarentena conhecida |
| Split e tempo | zero SHA e candidatos dHash ate 12 cruzados; 18 pares a 13; captura/ordem oficial ausente | PASS restrito; novo fit BLOCKED_GROUP_EVIDENCE |
| Detector | caixas humanas; exposicao YOLOX UNKNOWN | BLOCKED para sistema combinado |
| Comparacao | mesmo manifesto e 1.404 labels; baseline supera XGBoost nas tres metricas | PASS aritmetico; FAIL selecao |
| Avaliacao final e integracao | sem TEST independente, equivalencia operacional ou promocao | BLOCKED |

Revisao adversarial focal: (1) **HIGH**, o split por hash/dHash pode manter
sequencias relacionadas em papeis diferentes; evidencia: nenhum ID de rota nos
1.087 registros e par de cena cruzada no split antigo; impacto: metricas
nao independentes; correcao pendente: grupos oficiais ou avaliacao externa;
verificacao: zero pares dHash cruzados so cobre o metodo. (2) **MEDIUM**, o
relatorio antigo de deslocamento mostrava dimensoes, mas ocultava que
TRAIN/VALIDATION tinham protocolos de anotacao distintos; correcao aplicada:
contagens por role e variacao total no preflight/avaliador, teste que detecta
a diferenca mesmo com dimensoes iguais; risco residual: desvio de dominio
continua real. (3) **MEDIUM**, a variante `Low`/`low` e a equivalencia com
severidade operacional nao tem rubrica comprovada; origem original preservada,
solicitacao aos autores preparada; sem promocao.

Verificacao desta rodada: 14 testes focados de contrato/extrator e seis
testes do produtor passaram; os 847 XML reais mantiveram o contrato de
objetos/exclusoes; cinco linhas reais de protocolos/resolucoes
distintos tiveram as 11 features recomputadas exatamente; avaliador vinculado
reproduziu baseline/metricas sem importar XGBoost ou Torch; Ruff `check`,
mypy focado e `git diff --check` passaram. `ruff format --check` do arquivo
inteiro aponta trechos anteriores fora do formato; as linhas novas nao
aparecem no diff do formatador. Testes de treino, TEST final, inferencia YOLOX
e integracao operacional nao foram executados. `VERIFICATION_VERDICT=PASS`
para o diagnostico de software desta rodada; `SCIENTIFIC_RELEASE=BLOCKED`.
Arquivos alterados nesta rodada: `backend/app/ml/tabular.py`,
`backend/tests/test_external_tabular_contract.py`,
`scripts/datasets/acquire_attain_annotations.py`,
`scripts/datasets/test_acquire_attain_annotations.py`, este handoff e
`docs/ml/DATA_READINESS.md`. Nenhum novo fit, consulta Supabase, migration,
commit, merge, deploy ou alteracao do processo YOLOX foi executado.

### Fatos

O run Attain de severidade visual tem 2.652/1.404 linhas em TRAIN/VALIDATION,
baseline superior nas tres metricas, protocolos XML/YOLO em papeis distintos,
e 1.087 hashes de imagem elegiveis. Restam 18 pares cruzados a dHash 13
apos quarentena ate 12. Os 847 XML e seus poligonos opcionais
passaram a verificacao focal contra o manifesto existente.
O artigo descreve video continuo e extracao a cada 250 ms; o export local
tem 142 imagens elegiveis `1920x1080` nao descritas entre os tamanhos finais
do artigo. Dois pares dHash 13 inspecionados nesta continuacao eram cenas
visivelmente distintas, confirmando que a triagem tambem produz falsos
candidatos.
Nenhum dos objetos D20/D40 nas 1.656 anotacoes locais tinha `Medium`;
o produtor agora impede a conversao implicita desse nivel para LOW.

### Inferencias

O desvio de resolucao/protocolo pode contribuir para a piora, mas a causa
isolada nao foi demonstrada. Os 18 componentes dHash sao candidatos a
dependencia visual, nao IDs oficiais de rota.
O metodo de captura aumenta a plausibilidade de correlacao entre imagens,
mas nao permite recuperar uma sessao a partir da numeracao publicada.

### Desconhecidos

Sessao, rota, camera, frame/tempo de captura por imagem, completude da busca
visual, rubrica Low/High entre subsets, significado da duplicacao Low/low e
equivalencia com a severidade operacional do UrMind.
Os IDs Low/low nao coocorrem por imagem; falta saber se refletem lotes de
anotacao com a mesma rubrica.
Permanece desconhecida a origem da discrepancia `1920x1080` e a camera
correspondente a cada arquivo.

### Blockers

Sem grupo oficial de cena/rota ou avaliacao externa realmente independente,
sem esclarecimento da rubrica e sem caixas/linhagem do detector final, nao ha
base para alegar generalizacao nem integrar este artefato ao sistema.

### Decisao conservadora

Preservar Attain para desenvolvimento e o run como `EXPERIMENTAL_CANDIDATE`;
nao refazer split/seed nem novo fit nesta rodada. Regras atuais seguem no
backend; `READY_FOR_CONTROLLED_RETRAINING=NO` e `SCIENTIFIC_RELEASE=BLOCKED`.

## RECONCILIADO no backend principal — 26/09/2026

Este worktree foi reconciliado na working copy principal (`ml/urmind-training-prep`). Não
há mais código tabular exclusivo aqui; **não desenvolver neste worktree**.

- `backend/app/ml/tabular.py`: merge de três vias (base `e57db61`). Um único módulo, um
  único `train_xgboost` e uma única CLI. `train_xgboost` despacha por vínculo:
  `VerifiedTrainingBundle` (DatasetVersion/split/aprovação registrados por hash; Event e
  alvo externo Attain) → `_fit_verified_xgboost`; exemplos Event com `TabularAuthorization`
  presa ao hash do dataset + `TrainingContract` pré-registrado → ajuste + calibração +
  bootstrap pareado por grupo + registro de promoção lido por `load_promoted_model`.
  Exemplos sem vínculo são sempre bloqueados. O parser estrito deste worktree virou o
  `parse_ground_truth_export` canônico; `validate_ground_truth_export` e `_file_sha256`
  ficaram com uma definição cada. Pendência documentada: os dois modos de vínculo ainda
  coexistem para o alvo `review_confirmed`; convergir o caminho export+autorização para o
  bundle registrado antes do primeiro treino real.
- Integrados sem conflito: `services/core.py`, `services/features.py`,
  `services/__init__.py`, `tests/test_features.py`. Copiados: testes do contrato externo,
  do extrator de severidade e stdlib; configs Attain; `scripts/datasets/acquire_attain_annotations.py`
  e seu teste. `pyproject.toml`: `psutil==7.1.0` no extra `tabular-ml`.
- Formulário `datasets/annotations/tabular_labeling_protocol.json` regenerado pelo gerador
  oficial (v2), byte-idêntico ao deste worktree.
- Artefatos processados/runs Attain (`datasets/processed/tabular`, 24 arquivos, 30 MB)
  copiados para o projeto principal com SHA-256 conferido. O bruto `datasets/raw/attain`
  (151 MB) **permanece só neste worktree**: mover exige aviso de espaço e decisão do
  proprietário; até lá o worktree não pode ser removido.
- Semânticas separadas: A `review_confirmed` (operacional, 0 rótulos no DEV); B
  `pavement_visual_severity_low_high` (Attain, três runs `EXPERIMENTAL_CANDIDATE`, todos
  abaixo do baseline — preservados, nunca promovidos nem ligados ao Worker); C severity/
  risk/priority/recurrence (`TARGET_APPROVAL_PENDING`).
- Runtime: `predict_review_confirmed` agora é chamado por `CoreService.assess_event` e
  gravado como `decision_trace.review_confirmed_advisory` (`advisory_only`). Sem
  `TABULAR_MODEL_DIR` o status é `DISABLED`; falha de previsão vira `ERROR` sem bloquear a
  avaliação; severidade, risco, prioridade, Review e publicação continuam por regras/humanos.
- Verificação: suíte backend 1482 passed / 36 skipped / 0 failed; mypy e Ruff limpos.
