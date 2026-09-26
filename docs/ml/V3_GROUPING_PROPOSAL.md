# V3 — proposta de agrupamento e quase-duplicatas

Status: **proposta, não implementada**. Regras de execução em `V3_GUARDRAILS.md`.
Objetivo: fechar `SMOKE_PARTITION_GATE` / isolamento de grupos sem que imagens da
mesma cena ou quase-duplicatas atravessem TRAIN, VALIDATION, holdout V3 e Frozen Test.

## 1. Evidências e limiares

Distâncias de Hamming sobre pHash/dHash de 64 bits, com orientação, cinza e
redimensionamento canônico. A calibração reproduzível está em
`datasets/reports/v3_grouping_calibration.md`; o produtor é
`scripts/datasets/calibrate_v3_grouping.mjs`.

| Sinal | Mesmo grupo como proposta automática | Limite da evidência |
|---|---|---|
| SHA256 dos bytes | Igual | Duplicata exata |
| pHash (DCT 32→8×8) | Distância ≤ 40 | Máximo medido em recorte de 40% do conjunto de calibração |
| dHash (gradiente 9×8) | Distância ≤ 34 | Máximo medido em recorte de 40% do conjunto de calibração |
| pHash e dHash | Ambos dentro dos limites | A proposta não confirma o grupo; pares fora dos limites ainda podem ser agrupados por evidência de sequência/metadados |

A regra conservadora teve zero falso negativo em 84 pares positivos sintéticos de
12 imagens RDD TRAIN, incluindo recortes de 10%, 25% e 40%, reescala, alteração
de brilho/contraste e JPEG agressivo. Os limites altos podem gerar falsos positivos;
isso é preferível a separar a mesma cena em splits diferentes. Pares do mesmo país
foram gerados como candidatos negativos, mas os insumos não possuem cidade, rota
ou adjudicação de cena suficiente para confirmar que são distintos. Frames de
índice adjacente também não têm confirmação de mesma via. Por essa ausência,
`GROUP_GATE=NO`. Os limiares são proposta de agrupamento, nunca autorização de
consumo ou certificação de isolamento.
## 2. Metadados, quando existirem

- **GPS EXIF**: distância ≤ 30 m ⇒ mesma cena candidata (independe do hash).
- **Timestamp EXIF**: mesma câmera/dispositivo e Δt ≤ 10 s ⇒ mesma sequência candidata.
- Ausência de EXIF não é evidência de independência; cai para os demais sinais.

## 3. Padrões de nome/sequência do RDD2022

- Chave de origem: `<país>_<índice>` (ex.: `Japan_012345`); imagens do mesmo país
  com índices consecutivos (janela ≤ 5) são candidatas a mesma sequência de captura.
- Nunca agrupar entre países distintos só pelo índice.
- A regra de janela é heurística: só gera **candidatos**, que exigem confirmação
  por hash ou metadado para virar proposta de grupo.

## 4. Composição e decisão

1. Arestas = pares que satisfazem qualquer regra acima; grupos = componentes
   conexos (union-find), com a regra que gerou cada aresta registrada.
2. Um grupo inteiro vai para um único split. Grupo que toque Frozen Test ou
   holdout é bloqueado para TRAIN (leitura do Frozen Test só para esta checagem).
3. Saída: artefato de proposta com, por grupo, membros, evidências, distâncias e
   campo `"decision": ""`. O agente **para** aqui; confirmação é humana.

## 5. Os 8 grupos `UNCONFIRMED` do smoke

- Itens: `smoke_rdd_{India,Japan}_{D00,D10,D20,D40}`.
- Os 3 ambíguos (India D10/D20/D40) seguem **excluídos** do subconjunto candidato;
  seus grupos ainda são calculados para checar vazamento, não para uso.
- Para os 5 candidatos semânticos (India D00, Japan D00/D10/D20/D40): rodar as
  regras 1–3 contra TRAIN, VALIDATION e Frozen Test; emitir o grupo de cada um
  com `"decision": ""`.
- Enquanto o usuário não decidir: status permanece `UNCONFIRMED`,
  `GROUP_GATE`/`SPLIT_GATE` = NO e o smoke V3 continua bloqueado.
