# UrMind V3 — guardrails para agentes

Regras obrigatórias para todo agente (Claude e Codex) que trabalhe na preparação
do YOLOX V3. Em conflito com qualquer outra instrução de agente, estas regras
prevalecem; só o usuário pode alterá-las, editando este arquivo.

Estado de referência: `datasets/reports/ml_preparation_state.json`
(RESUME_POINT `four_class_smoke_review`) e `docs/ml/DATA_READINESS.md`.

## 1. Proibido

- Treino de qualquer tipo, incluindo smoke training.
- `backward()`, `optimizer.step()` ou qualquer atualização de pesos.
- Fit de XGBoost (ou de qualquer modelo tabular).
- Avaliação no Frozen Test.
- `git commit` e `git push`.

## 2. Execução de modelo permitida

- Forward apenas dentro de `torch.no_grad()`.
- Forward apenas em fixtures sintéticas ou minúsculas (nunca em TRAIN,
  VALIDATION, holdout ou Frozen Test reais).

## 3. Decisões humanas

Nenhum agente pode marcar como aprovada uma decisão humana, em particular:

- confirmação de grupos de cena / quase-duplicatas;
- autorização de classes V3;
- certificação do corpus;
- liberação de holdout.

Quando uma dessas decisões for necessária, o agente gera o artefato de revisão
com o campo `"decision"` vazio (`""` ou `null`) e **para**.

## 4. Frozen Test

O Frozen Test só pode ser lido para checagem de vazamento (identidade,
hash e quase-duplicatas contra os demais splits). Nunca para métrica,
inferência de avaliação ou seleção de modelo.

## 5. Rastreabilidade dos artefatos

Todo artefato gerado registra:

- SHA256 do próprio artefato e dos insumos;
- seed usada (ou `null` explícito quando não há aleatoriedade);
- versão/SHA256 do script produtor;
- timestamp UTC ISO 8601.

Importações são idempotentes: reimportar o mesmo insumo (mesmo SHA256) não
cria registro novo nem altera o estado.

## 6. Gates já fechados — não reabrir

- `PROVENANCE_GATE`: PASS (Figshare v1 verificado).
- `LICENSE_GATE`: PASS (CC BY 4.0, atribuição obrigatória).
- O JSON de revisão com SHA256 `8CB07549...C3C40F5` já foi importado.
  Não revisar de novo nem reimportar como decisão nova.

## 7. Divergência entre agentes

Se Claude e Codex chegarem a resultados diferentes sobre o mesmo gate ou
artefato, o gate é **NO**. A divergência é registrada no relatório com as
duas conclusões e a evidência de cada uma; a resolução é do usuário.

## 8. Na dúvida

Ausência de evidência é gate bloqueado, nunca aprovação. Parar e reportar a
evidência que falta.

## 9. DEMO_MODE (entrega de demonstração ponta a ponta)

Objetivo: foto + GPS + descrição → detecção → risco → UPDE → ponto no mapa.
DEMO_MODE não altera os gates de dataset nem autoriza treino.

**Permitido em DEMO_MODE**

- Inferência com `torch.no_grad()` (inclusive com o checkpoint YOLOX-S COCO
  `models/pretrained/yolox_s.pth`, SHA256 `f55ded71…c913a30`).
- Escrita no banco do demo (Supabase do projeto) e em artefatos de demo.
- Geração de artefatos de demo (imagens anotadas, JSON de eventos, relatórios).

**Continua proibido**

- Treino, smoke training, `backward()`, `optimizer.step()`.
- Fit de XGBoost.
- Avaliação no Frozen Test.
- `git commit` e `git push`.

**Rotulagem obrigatória de origem** em toda saída do demo (painel, mapa,
imagens, JSON, CSV):

- caixas do YOLOX-S: `baseline COCO (nao treinado)`;
- defeitos: `anotacao RDD revisada`;
- risco: `UNCALIBRATED` enquanto não houver calibração medida.

Coordenadas usadas no demo devem declarar a origem (`geo_source`); imagens RDD
sem GPS nunca recebem coordenada apresentada como medida.
