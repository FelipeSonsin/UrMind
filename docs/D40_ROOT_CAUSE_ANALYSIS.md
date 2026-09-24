# D40_ROOT_CAUSE_ANALYSIS — por que o pothole colapsou no MODEL V1

> Registro histórico preservado em 24/09/2026. Pesos/experimentos anteriores
> foram removidos conforme TRAINING_CLEANUP_2026-09-24.md. Os 18 artefatos
> derivados de dataset V2 citados foram removidos para a Lixeira em 24/09/2026,
> com inventário SHA-256 em docs/audits/V2_DATASET_CLEANUP_2026-09-24.md.
> Referências abaixo documentam lições, não autorização para novo treino.

Escopo: explicar, com evidência medida, o resultado do V1 em D40 no TEST histórico:

```
D40   TP=52   FP=1619   FN=280
      precision=0.031119   recall=0.156627
      AP50=0.012372        AP50:95=0.004644
```

Nenhuma causa abaixo é conjectura: cada uma aponta para o artefato que a mede.
Fontes: `datasets/reports/rdd2022_geometry.json` (geometria por classe/país/split,
medida no espaço do modelo, letterbox 640), `datasets/manifests/rdd2022_subset_selection.jsonl`
(inventário reconciliado por imagem) e `datasets/splits/rdd2022_subset_splits.json`
(split V1).

A conclusão central é que **imbalance não é a causa dominante**. O V1 tinha 5891
caixas D40 em treino — 17,1% de todas as caixas, mais que D10 (16,7%). Uma classe
com 17% da massa de rótulos não colapsa para precision 0,03 por escassez.

---

## Causa 1 — O split V1 é um holdout por país inteiro (causa primária)

O split V1 não estratificou: alocou países inteiros a cada papel.

| papel | países |
|---|---|
| TRAIN | India, Japan, Norway |
| VALIDATION | China |
| TEST | Czech, United_States |

Consequência direta no prior de D40:

| país | imagens | negativos | % neg | D40 boxes | D40 % das caixas do país |
|---|---|---|---|---|---|
| India | 7144 | 3921 | 54,9% | 3187 | **46,7%** |
| Japan | 8693 | 794 | 9,1% | 2243 | 13,6% |
| Norway | 7993 | 5079 | 63,5% | 461 | 4,1% |
| China | 3858 | 5 | 0,1% | 321 | 4,2% |
| Czech | 2829 | 1757 | 62,1% | 197 | 11,3% |
| United_States | 4805 | 0 | 0,0% | 135 | **1,2%** |

O modelo aprendeu o prior de D40 sobretudo na India, onde quase metade das caixas
é pothole, e foi medido em United_States, onde D40 é 1,2% das caixas. É um
deslocamento de prior de cerca de **40x** entre o domínio de treino e o de teste.
Um detector calibrado para o primeiro necessariamente superdispara no segundo, e
superdisparo com prior baixo é exatamente FP=1619 contra TP=52.

Isto não era desconhecido: `scripts/datasets/build_detection_manifests.py` já
registrava, entre os `accepted_model_v1_risks`, "domain shift forte por país no
split" e "D40 minoritária em VALIDATION e TEST". O risco foi aceito, e o
resultado do V1 é a sua materialização.

## Causa 2 — Rótulos D40 da Norway são micro-caixas e funcionam como ruído

Geometria de D40 por país, no espaço do modelo (640x640):

| país | D40 boxes | % small (<32²) | área mediana (px²) |
|---|---|---|---|
| India | 3187 | 14,2% | 3559 |
| Japan | 2243 | 24,4% | 1911 |
| **Norway** | **461** | **93,3%** | **101** |
| Czech | 197 | 24,4% | 1659 |
| United_States | 135 | 64,4% | 799 |
| China | 321 | 42,1% | 1444 |

A Norway, que está inteiramente em TRAIN, tem 93,3% das suas caixas D40 abaixo de
32x32 px, com área mediana de **101 px²** — cerca de 10x10 pixels. Um objeto de
10 px em 640 não carrega textura de pothole; o modelo só pode associá-lo a
"mancha escura pequena". Treinar com 461 exemplos desse tipo ensina precisamente
o detector a disparar D40 em qualquer imperfeição minúscula do pavimento, que é o
padrão de FP observado.

## Causa 3 — Deslocamento de escala de D40 entre treino e teste

| split | D40 boxes | % small | área mediana |
|---|---|---|---|
| train | 5891 | 24,3% | 2330 |
| validation | 321 | 42,1% | 1444 |
| test | 332 | 40,7% | 1161 |

A D40 de teste é cerca de **metade** da área da D40 de treino, e tem quase o dobro
da proporção de objetos pequenos. O modelo foi otimizado para uma escala e medido
em outra. O mesmo efeito aparece, ainda mais forte, em D00 (área mediana 1735 em
train contra 13639 em validation, quase 8x).

## Causa 4 — A VALIDATION do V1 era cega a falso positivo

A validation do V1 tinha **5 imagens negativas em 3858 (0,13%)**. Isso não foi uma
escolha de proporção: é consequência mecânica de atribuir a China inteira à
validation, já que a China tem apenas 5 negativos no total.

O efeito é metodológico e grave: a seleção de checkpoint, o early stopping e
qualquer calibração de threshold foram conduzidos por uma métrica que quase não
pode enxergar falso positivo em fundo. O colapso de precisão de D40 era, por
construção, invisível ao critério de seleção. É por isso que o melhor checkpoint
ficou na época 30 e ~150 épocas seguintes não produziram novo best enquanto a
loss de treino continuava caindo: o que degradava não estava sendo medido.

## Causa 5 — D40 é intrinsecamente a menor classe do conjunto

Agregado, no espaço do modelo:

| classe | boxes | % small | área mediana | log2(w/h) mediano |
|---|---|---|---|---|
| D00 | 26015 | 23,6% | 4289 | −0,56 |
| D10 | 11830 | 11,5% | 4200 | +2,41 |
| D20 | 10614 | 1,6% | 33362 | +0,47 |
| **D40** | **6544** | **26,0%** | **2189** | +0,73 |

D40 tem a menor área mediana e a maior proporção de objetos pequenos. Mesmo sem
os problemas acima, é a classe mais difícil para um detector de entrada 640. Isto
é um agravante real, mas é o único item da lista que não é um defeito corrigível
de dataset ou de metodologia.

## Causa 6 — Confusão plausível com D20

D20 (trinca tipo couro de jacaré) coocorre com D40 em 1086 imagens, o terceiro par
mais frequente do conjunto. As duas classes descrevem pavimento em estágio
avançado de degradação e são visualmente contíguas. O V1 também erra em D20 no
mesmo sentido (precision 0,2445 com recall 0,5296: superdisparo), o que é
consistente com uma fronteira D20/D40 mal resolvida — e não com escassez de dados.

---

## Observação metodológica sobre orientação (D00 x D10)

A medição de `log2(w/h)` acima tem uma consequência operacional que vale registrar
aqui porque afeta a augmentation do V2: D00 (longitudinal) tem mediana **−0,56**
(mais alta que larga) e D10 (transversal) tem mediana **+2,41** (cerca de 5,3x mais
larga que alta). As duas classes se separam essencialmente pela orientação em
relação à via. Qualquer rotação ou cisalhamento move uma classe em direção à
outra e corrompe o rótulo. Por isso `degrees` e `shear` são forçados a zero no
pipeline V2, com guarda que falha em vez de aceitar o contrário.

---

## O que a Fase 3 corrige, e o que não corrige

| causa | corrigida no dataset V2 | como |
|---|---|---|
| 1. holdout por país | sim | estratificação dentro de cada país para China, India, Japan e Norway; Czech + United_States (o TEST V1 inteiro) ficam fora como sonda report-only |
| 3. escala train/test | sim | consequência da mesma estratificação |
| 4. validation cega a FP | sim | 1370 negativos (31,7% de 4326) contra 5 (0,13%) |
| 2. micro-caixas Norway | **não** | os rótulos da fonte não foram alterados; segue como risco aberto |
| 5. D40 pequena por natureza | não | é propriedade do domínio, não defeito |
| 6. fronteira D20/D40 | parcialmente | mais negativos e prior coerente ajudam; a fronteira em si é questão de taxonomia |

As causas 1, 3 e 4 eram defeitos de partição e foram eliminadas por construção no
split V2, com 0 violações de leakage medidas. A causa 2 é uma decisão em aberto:
remover ou reponderar as caixas D40 da Norway altera rótulos da fonte e exige
decisão humana explícita, então não foi feita automaticamente.

**Importante:** as causas acima explicam o colapso, mas não garantem que o V2
melhore. A melhora só pode ser afirmada depois de medir o V2 na VALIDATION_V2
contra o baseline do V1 na mesma VALIDATION_V2 — comparação que não existia no V1,
porque não havia conjunto comum honesto entre os dois.

## Evidência nova (PHASE 3): o V1 na VALIDATION_V2, por país

Correção do split (2026-09-21): a versão inicial do split V2 deixava 4805 imagens
de United_States do TEST V1 em train/validation/holdout. O TEST V1 inteiro foi
isolado na sonda `DOMAIN_SHIFT_PROBE_V2`, e o split corrigido foi reaprovado.

`models/experiments/phase3/e0_v1_on_validation_v2.json` (checkpoint V1 raw,
conf 0,25 / NMS 0,5, o operating point do V1):

| recorte | V1 treinou nele? | mAP50:95 | D40 AP50:95 |
|---|---|---|---|
| Japan | sim | 0,4718 | 0,5134 |
| India | sim | 0,4025 | 0,3762 |
| Norway | sim | 0,1866 | 0,1009 |
| China | não (só seleção de checkpoint) | **0,0370** | **0,0003** |
| VALIDATION_V2 agregada | 80% vista | 0,3224 | 0,3693 |

Isto confirma a causa 1 de forma direta: a mesma rede tem D40 AP50:95 de 0,51 no
domínio em que treinou e 0,0003 num país que não viu. Não é escassez de D40; é
generalização entre domínios quase nula. Consequência metodológica: a agregada
de VALIDATION_V2 **não** mede o V1 de forma justa (ele memorizou 80% dela), e a
comparação V1 vs V2 precisa ser lida no recorte China e na sonda de domain shift.

A Norway, onde o V1 treinou, fica bem abaixo de India/Japan (0,19 contra 0,40 a
0,47; D40 0,10), o que é consistente com a causa 2 (micro-caixas D40 na Norway).
