# Limpeza de treinamentos anteriores — 2026-09-24

Autorização: usuário confirmou exclusão dos treinamentos antigos nesta conversa.

Escopo: artefatos locais de treinamentos executados; código, datasets, `.env`, peso pré-treinado de origem e restrições de avaliação preservados.

Estado inicial: inventário validado antes da remoção. 81 arquivos, 2.251.857.511 bytes lógicos. Tamanho lógico não equivale a espaço físico liberado pelo OneDrive. Nenhum comando Python de treinamento/Worker identificado em execução na inspeção.

Método: remoção para Lixeira do Windows, por arquivo explicitamente inventariado; sem hidratar placeholders, seguir symlinks, reset Git ou apagar diretórios amplos. Recuperação depende da Lixeira/OneDrive; não é garantida.

O ModelVersion DEV antigo será arquivado operacionalmente, conservando REJECTED e o histórico. Sem modelo disponível, inferência deve falhar fechada. Não houve treinamento XGBoost comprovado no inventário.

## Arquivos inventariados

| Caminho | Bytes |
|---|---:|
| `models/checkpoints/yolox_s_model_v1/best.pt` | 107862219 |
| `models/checkpoints/yolox_s_model_v1/last.pt` | 107862219 |
| `models/checkpoints/yolox_s_model_v2_screening_e1/best.pt` | 107864651 |
| `models/checkpoints/yolox_s_model_v2_screening_e1/last.pt` | 107864651 |
| `models/checkpoints/yolox_s_model_v2_screening_e2_memory_efficient/best.pt` | 107864651 |
| `models/checkpoints/yolox_s_model_v2_screening_e2_memory_efficient/last.pt` | 107864651 |
| `models/checkpoints/yolox_s_model_v2_screening_e2_memory_efficient/launcher.log` | 10592 |
| `models/checkpoints/yolox_s_model_v2_screening_e2_memory_efficient/training.stderr.log` | 5265705 |
| `models/checkpoints/yolox_s_model_v2_screening_e2_memory_efficient/training.stdout.log` | 0 |
| `models/checkpoints/yolox_s_quality_rebuild/best.pt` | 107864651 |
| `models/checkpoints/yolox_s_quality_rebuild/last.pt` | 107864651 |
| `models/checkpoints/yolox_s_quality_rebuild/launcher.log` | 3531 |
| `models/checkpoints/yolox_s_quality_rebuild/training.stderr.log` | 13079151 |
| `models/checkpoints/yolox_s_quality_rebuild/training.stdout.log` | 0 |
| `models/checkpoints/yolox_s_quality_rebuild/validation_e1_common_operating_point.json` | 13284 |
| `models/checkpoints/yolox_s_quality_rebuild/validation_operating_point_ema.json` | 9132 |
| `models/checkpoints/yolox_s_quality_rebuild/validation_operating_point_raw.json` | 9127 |
| `models/experiments/phase3/e0_v1_on_validation_v2.json` | 13438 |
| `models/experiments/phase3/e1_best_ema.json` | 13258 |
| `models/experiments/phase3/e1_best_raw.json` | 13288 |
| `models/experiments/phase3/e1_train.log` | 5222448 |
| `models/experiments/phase3/e1_train_part1_oom.log` | 1323776 |
| `models/experiments/phase3/e2_train.log` | 1947768 |
| `mlruns/mlflow.db` | 1289666560 |
| `mlruns/artifacts/08ddeacc64f845aa9851d04fa0931deb/artifacts/benchmark-baseline_early-epoch9-3c57b897fe38.json` | 691 |
| `mlruns/artifacts/0e1f012a426b465ba103a11582840044/artifacts/checkpoint-references/checkpoint-best.json` | 123 |
| `mlruns/artifacts/0e1f012a426b465ba103a11582840044/artifacts/checkpoint-references/checkpoint-last.json` | 123 |
| `mlruns/artifacts/0e1f012a426b465ba103a11582840044/artifacts/run-metadata/effective-config.json` | 2595 |
| `mlruns/artifacts/35569cbf58914dcca543cd1669ba7322/artifacts/checkpoint-references/checkpoint-best.json` | 123 |
| `mlruns/artifacts/35569cbf58914dcca543cd1669ba7322/artifacts/checkpoint-references/checkpoint-last.json` | 123 |
| `mlruns/artifacts/35569cbf58914dcca543cd1669ba7322/artifacts/run-metadata/effective-config.json` | 2165 |
| `mlruns/artifacts/3e81874d2a3844e4bcd654cc5f302655/artifacts/checkpoint-references/checkpoint-best.json` | 110 |
| `mlruns/artifacts/3e81874d2a3844e4bcd654cc5f302655/artifacts/checkpoint-references/checkpoint-last.json` | 110 |
| `mlruns/artifacts/3e81874d2a3844e4bcd654cc5f302655/artifacts/run-metadata/dry-run-summary.json` | 452 |
| `mlruns/artifacts/3e81874d2a3844e4bcd654cc5f302655/artifacts/run-metadata/effective-config.json` | 1440 |
| `mlruns/artifacts/3f59e774f6b44ebbb94d0a3b2a8a8e94/artifacts/evaluation_full-baseline_early-epoch9-3c57b897fe38.json` | 11530 |
| `mlruns/artifacts/4c557dfdae5b4e48b0733f7ad73ebe2c/artifacts/checkpoint-references/checkpoint-best.json` | 117 |
| `mlruns/artifacts/4c557dfdae5b4e48b0733f7ad73ebe2c/artifacts/checkpoint-references/checkpoint-last.json` | 117 |
| `mlruns/artifacts/4c557dfdae5b4e48b0733f7ad73ebe2c/artifacts/run-metadata/code-state.json` | 352642 |
| `mlruns/artifacts/4c557dfdae5b4e48b0733f7ad73ebe2c/artifacts/run-metadata/effective-config.json` | 2656 |
| `mlruns/artifacts/4c557dfdae5b4e48b0733f7ad73ebe2c/artifacts/summary/training_summary.json` | 546 |
| `mlruns/artifacts/4d7a2cbb55074d998948f3663f1c93e0/artifacts/benchmark-7cb16240837f.json` | 690 |
| `mlruns/artifacts/6ac67aacf7404bff974955d970fefebd/artifacts/checkpoint-references/checkpoint-last.json` | 110 |
| `mlruns/artifacts/6ac67aacf7404bff974955d970fefebd/artifacts/run-metadata/effective-config.json` | 1440 |
| `mlruns/artifacts/6b5ec5802ffe4e92b33c5bd55f83ae72/artifacts/run-metadata/effective-config.json` | 1440 |
| `mlruns/artifacts/77a9e13db1ee494ab8dfbca7815aca5f/artifacts/model-closure-6024b3c39e3c.json` | 33845 |
| `mlruns/artifacts/79dcea53c5f44c01bc0b145bcecae7d9/artifacts/checkpoint-references/checkpoint-best.json` | 110 |
| `mlruns/artifacts/79dcea53c5f44c01bc0b145bcecae7d9/artifacts/checkpoint-references/checkpoint-last.json` | 110 |
| `mlruns/artifacts/79dcea53c5f44c01bc0b145bcecae7d9/artifacts/run-metadata/effective-config.json` | 1440 |
| `mlruns/artifacts/79dcea53c5f44c01bc0b145bcecae7d9/artifacts/run-metadata/yolox-s-model-v1-early-stop-approval-a6b01994df7a.json` | 841 |
| `mlruns/artifacts/839b32c134654a83b48463a824bd3ce0/artifacts/benchmark-v2-final-epoch29-7cb16240837f.json` | 2577 |
| `mlruns/artifacts/8be78d6029b54c349fa043399a4c3d6a/artifacts/checkpoint-references/checkpoint-best.json` | 140 |
| `mlruns/artifacts/8be78d6029b54c349fa043399a4c3d6a/artifacts/checkpoint-references/checkpoint-last.json` | 140 |
| `mlruns/artifacts/8be78d6029b54c349fa043399a4c3d6a/artifacts/run-metadata/effective-config.json` | 2712 |
| `mlruns/artifacts/8be78d6029b54c349fa043399a4c3d6a/artifacts/summary/training_summary.json` | 567 |
| `mlruns/artifacts/b24a130468b14ac9a1e2044d1a9fb6f8/artifacts/run-metadata/effective-config.json` | 2709 |
| `mlruns/artifacts/f7444ad9f20d42e69e98ea7d4f59f80a/artifacts/checkpoint-references/checkpoint-best.json` | 123 |
| `mlruns/artifacts/f7444ad9f20d42e69e98ea7d4f59f80a/artifacts/checkpoint-references/checkpoint-last.json` | 123 |
| `mlruns/artifacts/f7444ad9f20d42e69e98ea7d4f59f80a/artifacts/run-metadata/effective-config.json` | 2165 |
| `mlruns/artifacts/f7444ad9f20d42e69e98ea7d4f59f80a/artifacts/summary/training_summary.json` | 548 |
| `mlruns/artifacts/fda1c2b0822f40f89f0acf2ea13c7c71/artifacts/run-metadata/effective-config.json` | 1440 |
| `mlruns/artifacts/fea43cdbd401447abc625856d8f690e7/artifacts/checkpoint-references/checkpoint-last.json` | 110 |
| `mlruns/artifacts/fea43cdbd401447abc625856d8f690e7/artifacts/run-metadata/effective-config.json` | 1440 |
| `models/serving/yolox-s-model-v1-3c57b897fe38.json` | 4062 |
| `models/serving/yolox-s-model-v1-7cb16240837f.json` | 4614 |
| `models/serving/yolox-s-model-v1-7cb16240837f.onnx` | 35855173 |
| `models/serving/yolox-s-model-v1-benchmark-7cb16240837f.json` | 688 |
| `models/serving/yolox-s-model-v1-benchmark-v2-68ffe68c9a35.json` | 2579 |
| `models/serving/yolox-s-model-v1-closure-6024b3c39e3c.json` | 33847 |
| `models/serving/yolox-s-model-v1-early-stop-approval-a6b01994df7a.json` | 841 |
| `models/serving/yolox-s-model-v1-promotion-epoch30.json` | 3329 |
| `models/serving/yolox-s-model-v1-registration-epoch30.json` | 15059 |
| `models/serving/yolox-s-model-v1-validation-calibration-21d4bcab15ac.json` | 104372 |
| `models/serving/yolox-s-quality-rebuild-7d91f7f6f0c0.json` | 10423 |
| `models/serving/yolox-s-quality-rebuild-7d91f7f6f0c0.onnx` | 35855173 |
| `models/serving/yolox-s-quality-rebuild-benchmark-validation-v2.json` | 2441 |
| `models/serving/yolox-s-quality-rebuild-benchmark-verified.json` | 2552 |
| `models/serving/yolox-s-quality-rebuild-benchmark.json` | 2343 |
| `models/serving/yolox-s-quality-rebuild-operating-point-lock-verified.json` | 13065 |
| `models/serving/yolox-s-quality-rebuild-operating-point-lock.json` | 13065 |
| `mlflow.db` | 0 |

## Limites

## Resultado da execução

Resultado final: **1205 passed / 20 skipped / 2 warnings** na suíte backend.
Skips: 19 DEV opt-in e 1 live externo; não executados nesta limpeza. Nenhuma
prova E2E real revendicada. Verificação após os testes: zero arquivos nos
diretórios checkpoints/experiments/mlruns, mlflow.db raiz ausente, dois registros
Frozen Test preservados. Limpeza de artefatos executada; readiness científica
continua BLOCKED. Os arquivos removidos não foram recriados pela suíte.

- 81/81 arquivos inventariados enviados à Lixeira e ausentes dos paths originais.
- Em models/mlruns restam o peso de origem pretrained e dois registros Frozen Test.
  Nenhum artefato XGBoost encontrado. Diretórios vazios podem permanecer: comando
  de remoção desses diretórios foi bloqueado pela política da ferramenta e não repetido.
- Backup `backups/supabase_pre_0002` não é treinamento; preservado.
- DEV confirmado por plugin; ModelVersion ARCHIVED e shadow_authorized=false,
  com AuditLog `archive_training_artifacts`. REJECTED e promoted_at nulo preservados.
- Nenhuma exclusão de entidade científica no banco, reset de migrations ou Auth.
- Código/datasets/scripts/configs/contratos e `.env` preservados.
- Não se garante expurgo em cópias OneDrive/backups externos; Lixeira não esvaziada.
- Primeiro comando de testes apontou para nomes inexistentes e não executou testes;
  substituído pela suíte real do repositório.
- Primeira suíte: 1204 passed / 1 failed / 20 skipped. Falha concreta: teste de
  bloqueio shadow dependia do manifesto excluído; register_model lia o artefato
  antes de recusar o ambiente. Corrigida ordem da guarda, antes do I/O, e teste
  usa path temporário inexistente, mantendo a asserção de recusa do outro projeto.
- Regressão focada: 33 testes de resolução de modelo passaram; Ruff focado,
  mypy (72 módulos) e diff check passaram. Sem criação/treino/promoção de modelo.
- Skills efetivamente aplicadas: urmind-dataset-integrity, correction-review,
  supabase:supabase. Conector Supabase para confirmar alvo, arquivar modelo
  e verificar status. Nenhum conector de outro ambiente utilizado.

Excluir pesos não elimina contaminação histórica do TEST. Ledger e resultado Frozen Test permanecem como registro, sem nova avaliação. Contratos/manifests e relatórios anteriores são históricos, não autorização para novo treino. Readiness de YOLOX/XGBoost continua bloqueada por revisão/holdout e Ground Truth, além das remediações operacionais abertas.
