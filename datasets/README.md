# Datasets do UrMind

**Etapa atual (2026-09-09): recortes adquiridos e as oito fontes legíveis pelo backend.**
Sete fontes cabem no teto de 7 GB por dataset; o **RDD2022 é exceção autorizada** — a poda
da origem Norway foi construída e validada, mas o OneDrive a reverteu e o usuário decidiu
manter a fonte como está (`reports/rdd2022_norway_partial_restore.json`). O total fica
dentro dos 40 GB. Remoções autorizadas e registradas: o ZIP redundante do RDD2022
(`reports/rdd_archive_release.json`) e a poda da Norway (`reports/rdd2022_reduction.json`,
aplicada e depois desfeita pela sincronização). Fora dessas, nenhum original foi tocado.

Esta pasta mantém os dados originais, a procedência e os artefatos portáteis de auditoria.
Situação de presença, procedência e prontidão por fonte: [revisão complementar](reports/READINESS_REVIEW.md).
O planejamento atual está em `docs/Planinng/MASTER_PLAN.md` e no DOCX da mesma pasta
(a grafia encontrada é **Planinng**, não Planning). As instruções desta etapa limitam
as operações a datasets. A autorização posterior inclui downloads registrados e
liberação do ZIP redundante verificado, sem treino nem alterações de backend/frontend.

## Estrutura com uso efetivo

```text
datasets/
  raw/          originais preservados e aquisições registradas
  metadata/     taxonomia, mapeamentos, fontes, licenças e orçamento
  manifests/    inventário por imagem e proposta de seleção reproduzível
  splits/       listas relativas de train, validation e test
  reports/      auditorias, integridade, duplicatas, validações e relatório final
  README.md
  STATUS.md
scripts/datasets/  comandos independentes do backend
```

`processed/`, `downloads/` e `annotations/` não existem. `downloads/` chegou a ser usada como
cache de aquisição e foi removida: os CSV de rótulo que ela guardava são rebaixados pelo
próprio script, e a seleção resultante já vive em `raw/global_streetscapes/labels/`. As três
serão criadas somente quando houver operação efetiva autorizada: `processed/` para labels
convertidos e configurações; `downloads/` para transferência temporária com pico orçado;
`annotations/` para anotações próprias/derivadas. Nenhuma delas deve receber cópias
desnecessárias de imagens.

Há **um único ambiente virtual**, `backend/.venv`, e um único cache de lint,
`backend/.ruff_cache`. Um `.venv` e um `.ruff_cache` duplicados na raiz foram removidos em
2026-09-09, depois de mover para o ambiente canônico as bibliotecas que só existiam neles
(`huggingface_hub`, `hf_xet`, `tqdm`). Rode o lint de dentro de `backend/`
(`ruff check app tests ../scripts`) para não recriar um cache na raiz.

`raw/bdd100k/` deixou de ser um nome legado: o recorte oficial 10K está lá. `raw/mapillary_msls/`
foi aposentado — o portal do MSLS exige login e a fonte foi substituída por
`raw/global_streetscapes/`, que redistribui imagem do próprio Mapillary sob CC BY-SA 4.0.
O motivo e as alternativas avaliadas estão em `metadata/removed_sources.json`.
Os originais são preservados, exceto as duas remoções autorizadas citadas acima.

## Fontes, finalidades e estado

A tabela completa de URLs, versões, tamanhos em bytes, checksums, licenças, classes e status
está em [metadata/sources.csv](metadata/sources.csv). Evidências e pendências de licença
estão em [metadata/licenses.md](metadata/licenses.md). O catálogo do backend não é dependência
dos scripts desta etapa.

| Dataset / fonte oficial | Finalidade | Rótulos / uso | Estratégia e situação |
|---|---|---|---|
| [RDD2022](https://figshare.com/articles/dataset/RDD2022_-_The_multi-national_Road_Damage_Dataset_released_through_CRDDC_2022/21431547) | road_damage | D00, D10, D20, D40 | 6,49 GB após podar **somente** a origem Norway; as outras seis ficaram byte a byte. Nenhum objeto anotado foi perdido — as contagens das quatro classes são idênticas antes e depois. Manifestos `rdd2022_norway_kept.jsonl` e `_removed.jsonl`. |
| [UNIVALI/DNIT v4](https://data.mendeley.com/datasets/t576ydh9v8/4) | road_damage | POTHOLE em máscara; trinca genérica não vira D00/D10/D20 | Já local; CC BY 4.0 confirmado. Conversão de máscara em caixas fica para etapa própria. |
| [Urban Community Issues](https://www.kaggle.com/datasets/rajeevpaudel1/urban-community-issues) | urban_damage | classes originais preservadas; mapa inferido requer revisão | Já local; ficha indexada declara CC0, procedência detalhada pendente. Não misturar automaticamente ao detector de pavimento. |
| [Project Sidewalk](https://huggingface.co/datasets/projectsidewalk/rampnet-crop-model-dataset-round1) | accessibility | recortes e keypoints de rampas | 5,36 GB em Parquet, 5 shards com SHA-256 oficial conferido. Adaptador `project_sidewalk` lê a imagem de dentro da linha, sem extrair. Keypoint não vira caixa. |
| [RampNet](https://huggingface.co/datasets/projectsidewalk/rampnet-dataset) | accessibility | panoramas, keypoints normalizados e coordenada real | 6,84 GB dos 462 GB publicados, SHA-256 oficial conferido. Adaptador `rampnet` separa o ponto na imagem (`KeypointSample`) da coordenada no mundo (`GeoRecord`, a única desta etapa que exercita PostGIS). |
| [CAMBER #50](https://zenodo.org/records/21361827) | longitudinal_risk | CSV, GPS, contexto temporal | Amostra já local; saídas de modelo não são ground truth. Registro declara CC BY 4.0; abrangência às mídias externas pendente. Priorizar metadados, poucos vídeos. |
| [BDD100K](https://doc.bdd100k.com/download.html) | navigation | BDD10K e máscaras de segmentação | 1,27 GB em dois ZIPs, lidos **fechados** pelo adaptador `bdd100k` para não duplicar o espaço. Nenhuma classe da V1: toda amostra sai com máscara recusada e motivo. Não há checksum oficial válido — o MD5 publicado tem 31 dígitos hex. |
| [Global Streetscapes](https://huggingface.co/datasets/NUS-UAL/global-streetscapes) | urban_context | contexto urbano com rótulo humano de cena | **Substitui o Mapillary MSLS**, cujo portal exige login. 6,0 GB, 15.007 imagens, 108 países, 398 cidades, 1.946 sequências; 14.610 vêm do próprio Mapillary e 397 do KartaView, sob CC BY-SA 4.0 e sem gating. Seleção estratificada por continente × plataforma da via × clima × iluminação. |
| Dados próprios | finalidade conforme protocolo | classes revisadas por humanos | Reserva orientativa; nenhuma imagem fabricada. |

Consulte `reports/storage_audit.json` para tamanhos locais atualizados e `metadata/sources.csv`
para tamanho original conhecido. Campo vazio significa **desconhecido**, não tamanho zero.

## Orçamento por fonte

Valores em **GB decimais (1 GB = 1.000.000.000 bytes)**. A versão 5 do
`metadata/storage_budget.yaml` deixou de tratar as alocações como referências
redistribuíveis: agora há **teto de 7 GB por dataset**, com alvo de 6,5 GB, além do
máximo agregado de 40 GB. Pelo menos 10 GB precisam permanecer livres no disco no pico.

| Fonte | Em disco | Teto |
|---|---|---|
| rdd2022 | 12,72 GB | **exceção autorizada** |
| rampnet | 6,84 GB | 7 GB |
| global_streetscapes | 6,07 GB | 7 GB |
| project_sidewalk | 5,36 GB | 7 GB |
| urban_community | 1,49 GB | 1,5 GB |
| bdd100k | 1,27 GB | 7 GB |
| univali_br | 0,48 GB | 0,5 GB |
| camber | 0,10 GB | 0,2 GB |

O RDD2022 é a única fonte acima do teto, e por decisão registrada: a poda que o levaria a
6,49 GB existe, foi validada e não pôde ser aplicada porque a sincronização a reverte (ver
a seção seguinte). Fonte completa que já cabe abaixo do teto não é inflada para preencher
GB, e nada é baixado apenas para atingir uma cota. O Global Streetscapes é o caso oposto: os sete
tarballs oficiais somam 23,85 GB, então o script decide a seleção pelos CSV de rótulo
e **grava só o que foi escolhido**, lendo os tarballos em streaming — não baixa 23,85 GB
para depois apagar 17,85 GB.

### Exclusão dentro do OneDrive

O projeto fica numa pasta sincronizada. Apagar milhares de arquivos de uma vez **é
revertido**: a proteção de exclusão em massa do OneDrive restaura tudo da nuvem enquanto
espera confirmação, e parar o cliente não resolve — ao voltar, ele reconcilia tratando a
nuvem como verdade e baixa tudo de novo. Ambos os comportamentos foram observados em
2026-09-08. Por isso `reduce_rdd2022.py` apaga em lotes de 150, pausa para a sincronização
acompanhar e reconfere no fim, removendo de novo o que a nuvem tiver trazido de volta. Isso
resolve exclusões pequenas — como os 111 XML órfãos que a restauração parcial deixou — mas
não venceu as 12 mil do RDD2022. Para uma exclusão grande valer é preciso confirmar o aviso
do OneDrive na barra de tarefas ou elevar
`HKCU\SOFTWARE\Policies\Microsoft\OneDrive\LocalMassDeleteFileDeleteThreshold`; nenhuma
das duas foi feita. Medir espaço nessa pasta exige cuidado equivalente: o tamanho de um
placeholder é o da nuvem, não o do disco.

Caches comuns de desenvolvimento podem permanecer nas localizações convencionais das
ferramentas. Não é necessário realocá-los para o projeto. A exigência de armazenamento
interno se aplica aos datasets, modelos/checkpoints e outros dados volumosos do UrMind;
um cache de modelos com muitos GB não é tratado como simples cache de desenvolvimento.

O preflight considera o consumo atual mais o pico adicional, incluindo arquivos simultâneos.
Adições manuais são contabilizadas na próxima auditoria e nunca provocam exclusão automática.
Aumentar o máximo depende de decisão posterior do usuário.

## Comandos reproduzíveis

Python 3.12+ e as dependências indicadas em `scripts/datasets/requirements.txt`.
Use um ambiente Python disponível; a `.venv` copiada de outra máquina não é portável.
Os comandos abaixo são executados da raiz. Os scripts também podem ser chamados de outro
local: encontram a raiz a partir do próprio arquivo, sem caminhos específicos de usuário.

```text
python -B scripts/datasets/audit_storage.py
python -B scripts/datasets/verify_sources.py
python -B scripts/datasets/audit_rdd2022.py
python -B scripts/datasets/find_duplicates.py
python -B scripts/datasets/propose_subset.py --seed 20260908
python -B scripts/datasets/make_splits.py --seed 20260908
python -B scripts/datasets/validate_portability.py --check-files
python -B scripts/datasets/verify_pipeline.py
python -B scripts/datasets/audit_readiness.py
python -B scripts/datasets/audit_readiness.py --video-only
python -B scripts/datasets/validate_manifests.py
python -B scripts/datasets/plan_storage.py --operation revisao --delta-gb 0 --peak-gb 0
```

O estado das fontes é **gerado**, nunca escrito à mão (exige `backend/.venv`, porque usa
os mesmos adaptadores que o sistema):

```text
backend/.venv/Scripts/python -B scripts/datasets/refresh_readiness.py
backend/.venv/Scripts/python -B scripts/datasets/refresh_readiness.py --verify
```

`refresh_readiness.py` produz `reports/dataset_readiness.json` lendo o catálogo em
`backend/app/datasets/catalog.py`, medindo o disco e rodando cada adaptador. Ele sai com
código 1 quando o catálogo discorda do disco, e a divergência vai no relatório em vez de
ser resolvida em silêncio. `audit_readiness.py` cuida só da auditoria profunda de formato
(`reports/dataset_format_audit.json`): ele não descreve mais o estado das fontes, porque a
lista fixa que mantinha envelheceu e passou a contradizer o disco.

Nenhum desses comandos baixa, extrai, apaga originais ou executa modelos. As escritas ficam
em pastas derivadas. `--no-report`, onde disponível, não grava saída. Dados cloud-only não
são hidratados para leitura; isso é registrado como cobertura incompleta, nunca como validação.

As escritas de artefatos usam um arquivo temporário na mesma pasta, verificam o orçamento
e substituem o relatório apenas depois da gravação completa. Um `.tmp` remanescente de uma
interrupção bloqueia nova escrita até revisão; não há limpeza automática de arquivos.

`audit_external.py` faz apenas uma busca limitada por nomes relacionados nas localizações
usuais, sem mover/excluir arquivos. `safety_snapshot.py` registra uma assinatura de nomes,
tamanhos e datas de raw; `--verify` compara ao baseline existente sem sobrescrevê-lo.

## Fonte de verdade do estado dos datasets

Existe **uma** e ela é código: `backend/app/datasets/catalog.py`. Ela declara identidade,
licença, papel, uso (`TRAIN`, `VALIDATION`, `TEST`, `EXTERNAL_TEST`, `GEO_REFERENCE`,
`CONTEXT_ONLY`, `UNUSABLE`), formato de anotação e a etapa do §25 que consome a fonte.

Tudo o mais é derivado dela e do disco:

| Artefato | Gerado por | Responde |
|---|---|---|
| `reports/dataset_readiness.json` | `refresh_readiness.py` | estado, contagens, divergências |
| `reports/dataset_format_audit.json` | `audit_readiness.py` | formato decodifica? |
| `reports/derived_manifest_validation.json` | `validate_manifests.py` | derivadas têm proveniência? |
| `metadata/sources.csv` | `refresh_sources.py` | tabela para leitura humana |

Contagem escrita à mão em documentação envelhece sem ninguém perceber — foi o que aconteceu
com a versão anterior do `dataset_readiness.json`, que declarou por semanas
`project_sidewalk`, `rampnet` e `bdd100k` como `no_dataset_data` enquanto os três estavam em
disco, com adaptador e checksum oficial conferido. Ao mudar qualquer coisa sobre uma fonte,
mude o catálogo e regenere; não edite o JSON.

## Dado existente não é dado treinável

São perguntas diferentes e o relatório separa as colunas:

- `stored_bytes` — quanto ocupa. É orçamento.
- `box_annotations` — caixas na taxonomia V1. **É só isto que o YOLOX da V1 treina.**
- `mask_annotations` — anotação humana real, em geometria que o detector não lê.
- `cloud_only_files` — existe no OneDrive, não está no disco. `exists()` mente aqui.

Uma fonte pode ter 6,8 GB, 17 mil amostras e zero caixas. Não é defeito dela: é a resposta
correta para "quanto deste dado o detector consegue aprender".

## UNIVALI/DNIT — versão derivada e holdout brasileiro

O UNIVALI anota por máscara e o detector consome caixa. A conversão existe desde
2026-09-10 como **versão derivada registrada**, em cinco etapas que se consomem em cadeia,
cada uma gravando o SHA-256 do que leu:

```text
backend/.venv/Scripts/python -B scripts/datasets/audit_univali_masks.py
backend/.venv/Scripts/python -B scripts/datasets/convert_univali_masks.py
backend/.venv/Scripts/python -B scripts/datasets/validate_univali_boxes.py
backend/.venv/Scripts/python -B scripts/datasets/render_univali_audit.py
backend/.venv/Scripts/python -B scripts/datasets/make_univali_splits.py
```

| Etapa | Produz | Responde |
|---|---|---|
| `audit_univali_masks.py` | `manifests/univali_br_mask_scan.jsonl`, `reports/univali_mask_semantics.json` | que valores, regiões e identificadores as máscaras realmente têm |
| `convert_univali_masks.py` | `manifests/univali_br_boxes.jsonl`, `reports/univali_conversion.json` | máscara → componentes conexos → caixas |
| `validate_univali_boxes.py` | `reports/univali_box_validation.json` | geometria, pares, rótulos, duplicatas, contaminação |
| `render_univali_audit.py` | `processed/univali_br/visual_audit/`, `reports/univali_visual_audit.json` | a caixa está no lugar certo? (olho humano) |
| `make_univali_splits.py` | `splits/univali_br_external_test_splits.json` + listas `.txt` | partição por grupo com holdout congelado |

Três decisões ficam explícitas porque são o tipo de coisa que some num diff:

**A caixa derivada NÃO é `URMIND_ROAD_D40`.** Ela sai como `UNIVALI_POTHOLE`. A fonte
declara que a máscara é buraco, e o projeto registra essa correspondência candidata em
`metadata/class_mapping.yaml`, mas ela nunca foi conferida contra a documentação oficial
da UNIVALI nem contra inspeção humana. Promover por semelhança de nome é o que o §8.2
proíbe. `convert_univali_masks.py --assert-v1-mapping <evidência.json>` aplica o mapa
quando a verificação existir, e recusa rodar se o arquivo de evidência estiver incompleto.

**`CRACK` não vira nada.** A fonte publica uma única categoria de trinca e não declara
subtipo. Escolher entre D00, D10 e D20 por geometria seria fabricar anotação.

**Nenhum limiar de área foi escolhido.** O padrão é `--min-area 0`: nada é descartado por
tamanho. Cada caixa carrega sua área e um `size_tier` tirado dos quantis do próprio
dataset, e o relatório mede quanto cada limiar candidato custaria — de 1 px até os 1024 px
da convenção COCO para "objeto pequeno". Escolher o corte é decisão humana registrada,
não default de script.

O UNIVALI inteiro é **EXTERNAL_TEST_BR**: nenhuma amostra entra em treino. É a única fonte
com anotação humana em rodovia brasileira, e gastá-la treinando destruiria o único
termômetro honesto de desempenho no domínio real. Dentro dela a partição é por **rodovia**,
não por trecho nem por imagem: capturas do mesmo trecho são sequenciais, e o dHash
praticamente não as detecta como parecidas — a proteção precisa vir do identificador
publicado pela fonte, não de similaridade de pixel (§8.4). Um dos lados é
`holdout_br_frozen`, congelado antes de qualquer treino e medido uma única vez.

### PyYAML e o `.venv`

Os scripts desta pasta escrevem via `_core.write_text_safe`, que chama o preflight de
orçamento, que carrega `storage_budget.yaml` — ou seja, **PyYAML é obrigatório para
qualquer escrita**, inclusive nas etapas que parecem só de leitura. Ele vive em
`backend/.venv`. Um Python de sistema sem PyYAML roda a medição inteira e falha na hora
de gravar, desperdiçando a varredura.

## Contrato dos artefatos

`taxonomy.yaml` define as quatro classes canônicas; `class_mapping.yaml` preserva a camada
rótulo original → classe canônica. `URMIND_UNKNOWN` é estado do sistema, não rótulo automático.

Toda versão derivada de um dataset — poda, subconjunto, split, conversão — precisa registrar
origem, versão, transformação, parâmetros, script, data, entradas, saídas, descartes, motivos
dos descartes e integridade. O contrato está em
`metadata/artifact_contract.yaml#derived_manifest_contract` e `validate_manifests.py` confere.
O original nunca é sobrescrito: derivada é sempre arquivo novo.
Negativo confirmado significa XML válido com **zero objetos originais**. Imagens com somente
rótulos fora da V1, XML inválido ou sem XML não são confundidas com negativos.

`rdd2022_inventory.jsonl` guarda uma linha por imagem, caminhos relativos à raiz, origem,
labels originais, contagens canônicas, tamanho, SHA-256 da imagem/annotation, qualidade e grupo.
`rdd2022_subset_selection.jsonl` referencia esses mesmos originais. Não contém imagens.
Os candidatos visuais usam dHash de 128 bits e exigem revisão humana; não provam duplicação.

Os splits mantêm países inteiros e unem componentes de duplicatas exatas/visuais. As duas
origens chinesas pertencem ao mesmo país. Uma verificação independente impede caminhos,
hashes ou grupos detectados em lados diferentes. Isso mede generalização entre países;
não comprova desempenho no Brasil nem ausência de relações não documentadas. O teste é uma
proposta a congelar antes do futuro treino, sem reajustá-lo depois de observar resultados.

Os manifestos `rdd2022.json`, `univali_br.json` e `camber.json` continuam sendo registros
do backend e não substituem os manifestos por imagem desta etapa. O que mudou é que o
catálogo do backend deixou de estar defasado em relação ao disco: `project_sidewalk`,
`rampnet`, `bdd100k` e `global_streetscapes` ganharam adaptador e saíram de `DEFERRED`,
e `python -m app.datasets.cli inventory` reporta as oito fontes como presentes. O frontend
não foi alterado.

## Transferência entre máquinas

**Opção A:** transferir o projeto com `datasets/` preenchida; executar as verificações de
integridade e portabilidade. Manter a estrutura relativa dos arquivos originais.

**Opção B:** transferir código, metadata, manifests, splits, reports e documentação. Após
nova autorização, obter exatamente a versão indicada nos registros oficiais, validar os
checksums em `metadata/upstream_checksums.json`, e reconstruir a mesma estrutura de raw.
Somente então rodar auditoria/seleção com a mesma seed/configuração e comparar os hashes.
A reconstrução depende da disponibilidade da fonte. Os novos downloaders cobrem somente
os recortes registrados, com dry-run e verificação de integridade.
A extração futura exige orçamento para ZIP externo, ZIPs internos e conteúdo final simultâneos;
não executar uma extração integral que ultrapasse o pico de 40 GB.

Imagens, ZIPs, processed e downloads não entram no Git; metadata, manifests e splits entram.
Não existe `.git` nesta cópia; o `.gitignore` está preparado para um futuro repositório.
DVC local foi inicializado sem remote e não recebeu cópias/cache de datasets. O projeto está em OneDrive;
sincronização e localização não foram alteradas.

## Aquisição e leitura sem cópias

Os comandos abaixo são dry-run por padrão. `--execute` baixa somente arquivos registrados;
`--resume` retoma parciais da mesma versão. Não executar duas instâncias para o mesmo destino.
O pico inclui os outros downloads registrados ainda pendentes.

```text
python -B scripts/datasets/acquire_registered.py --dataset project_sidewalk --resume
python -B scripts/datasets/acquire_registered.py --dataset rampnet --resume
python -B scripts/datasets/acquire_bdd100k.py --resume
python -B scripts/datasets/acquire_global_streetscapes.py
python -B scripts/datasets/profile_norway.py
python -B scripts/datasets/reduce_rdd2022.py
python -B scripts/datasets/reconcile_after_reduction.py
python -B scripts/datasets/refresh_status.py
python -B scripts/datasets/refresh_sources.py
python -B scripts/datasets/refresh_registry.py
```

Os três `refresh_*` regeram `STATUS.md`, `metadata/sources.csv` e
`metadata/artifact_registry.json` a partir do disco e dos relatórios. Documentação de
tamanho e contagem não é escrita à mão aqui: número digitado envelhece sem ninguém
perceber, e é assim que um README passa a descrever um projeto que não existe mais.

Os três últimos são desta etapa. `profile_norway.py` mede clima/iluminação/contraste de
cada imagem da Norway e não altera nada; `reduce_rdd2022.py` é dry-run por padrão e só
remove com `--execute`, sempre reproduzindo a mesma seleção a partir do perfil gravado.

Project Sidewalk/RampNet usam SHA-256 oficial. BDD usa CRC de todos os membros ZIP e SHA-256
local: o MD5 publicado para imagens tem comprimento inválido e não autentica o pacote.
BDD é transferido pelos links HTTP publicados no portal de Berkeley, sem desativar validação TLS.
Planos, versões, links e limitações estão em `metadata/acquisition_plan.json` e
`metadata/bdd100k_acquisition_plan.json`. Arquivos `.part` não são conteúdo pronto.

Após concluir as transferências, `audit_acquired.py --dataset project_sidewalk` e
`audit_acquired.py --dataset rampnet` decodificam todas as imagens em memória e validam
keypoints. Os manifestos usam `container_path` relativo e `row_index`. O iterador
`audit_acquired.iter_samples` lê os bytes e labels originais sem extrair imagens para disco.
Keypoints não são bounding boxes de danos. As partições originais são registradas sem
presumir ausência de leakage entre panoramas ou entre essas duas fontes relacionadas.

O baseline de `safety_snapshot.py` pertence à auditoria anterior: as novas aquisições e a
remoção autorizada do ZIP produzem diferenças esperadas. Não sobrescrever esse baseline
para ocultar alterações. Consulte `reports/rdd_archive_release.json` para a liberação.

Presença e integridade de dados não demonstram desempenho de identificação. A integração
futura com modelos especializados e sua avaliação permanecem fora desta etapa.
