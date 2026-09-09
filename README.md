# URMIND

> **Frontend reconstruído em 07/09/2026:** a pasta [`frontend/`](frontend/README.md)
> agora contém a base React + TypeScript + Vite/PWA, com rascunhos locais,
> captura de foto, consulta de ocorrências e MapLibre preparado. Funciona sem as
> integrações externas para registro local; upload, inferência e previsões não
> são simulados. Para executar: `cd frontend`, `npm.cmd ci`, `npm.cmd run dev`.
> Consulte o README do frontend para o escopo e as limitações desta entrega.

Sistema de percepção e decisão urbana auditável. A cadeia começa numa foto de
celular ou na câmera/sensores do Scout e termina numa ocorrência
georreferenciada com classe, confiança, severidade, prioridade, responsável
sugerido, ação recomendada e histórico — reconstruível por IDs e versões de
modelo.

**Regra central:** nenhuma LLM (ChatGPT, Gemini, Claude, Qwen, Ollama ou
qualquer outra) participa de detectar, classificar, prever, priorizar,
raciocinar ou gerar a resposta do produto. O runtime é feito de modelos
treinados pela equipe, processamento matemático/estatístico, regras
verificáveis e templates Jinja2.

## Planejamento oficial

Fonte de verdade única: [`docs/Planinng/MASTER_PLAN.md`](docs/Planinng/MASTER_PLAN.md)
(revisão de 05/09/2026). O `UrMind Planejamento Completo.docx` ao lado é o mesmo
planejamento em outro formato e está sincronizado com ele; pelo §31.19, se os
dois divergirem o desenvolvimento para até serem igualados de novo.

Qualquer decisão de arquitetura, taxonomia, esquema de banco ou critério de
aceitação sai desse documento. Não há documentos auxiliares concorrentes.

## Decisões de infraestrutura desta fase

| Decisão | Consequência |
|---------|--------------|
| **Sem Docker.** | Não há `docker compose up`. O backend roda direto no Python local (§3.1). |
| **Supabase é a única infraestrutura gerenciada.** | PostgreSQL + PostGIS, Storage, Auth, Realtime e Queues/pgmq vêm do Supabase (§4). |
| **Alembic é a única fonte de alterações estruturais.** | Revisões em `backend/alembic/versions/`, aplicadas por `python -m alembic upgrade head` (§18.2). |
| **Um único modelo de dados.** | Só existe o núcleo Capture → Detection → Event do §5. Nenhuma entidade paralela com taxonomia própria. |
| **Acesso a dados direto ao PostgreSQL.** | SQLAlchemy 2 + psycopg 3, via Supavisor em session mode (§4.3). |

O MASTER_PLAN exclui explicitamente (§3.1) Docker, Compose, Celery, Redis,
Caddy, RabbitMQ, Kubernetes, Mosquitto como dependência central, Photon
self-hosted, LLM no runtime e Google Maps como dependência obrigatória.
A fila assíncrona será o Supabase Queues/pgmq (§7), não Celery/Redis.

## Stack em uso hoje

| Camada | Tecnologia |
|--------|-----------|
| Linguagem | Python 3.12+ |
| API | FastAPI + Pydantic v2 |
| Banco | Supabase PostgreSQL + PostGIS |
| Acesso a dados | SQLAlchemy 2 + GeoAlchemy2 + psycopg 3 |
| EXIF | Pillow (JPEG/TIFF; ExifTool fica para HEIC/RAW) |
| Respostas | Jinja2 sobre dado estruturado, sem LLM |
| Migrações | Alembic (revisões assíncronas sobre psycopg 3) |
| Logs | structlog em JSON, com `correlation_id` |
| Testes | pytest |
| Lint | ruff |

Previstas pelo MASTER_PLAN §28 e ainda **não** presentes no código: PyTorch,
YOLOX-s, ONNX Runtime, OpenCV, ExifTool, SciPy, librosa, XGBoost, SHAP,
Supabase Storage/Auth/Queues, DVC,
MLflow, Evidently.

## Estado atual

O que está no disco e verificado:

- **Fundação do backend e do banco.** O caminho `API → domínio → persistência →
  Supabase/PostGIS` está montado e testado.
- **Núcleo geoespacial** na revisão `0001_core_geospatial`: capturas,
  detecções, eventos, snap à via, busca por raio e auditoria.
- **Leitura de EXIF** (§6.3): coordenada, precisão e instante extraídos do
  arquivo, com estado explícito quando faltam.
- **Ingestão de foto** (§25 passo 4): monta o `Capture`, decide entre origem
  `exif` e `manual`, e preserva em auditoria o que o EXIF dizia quando o ponto é
  corrigido à mão.
- **Motor de severidade e prioridade** (§14.1–14.2): regras versionadas,
  auditáveis, com fator ausente fora da conta e cobertura declarada.
- **Respostas sem LLM** (§15): relatório em Jinja2 que só afirma o que existe.
- **Ferramental de dados e avaliação** (`app/ml/`, §8): mapeamento de taxonomia,
  split sem vazamento e as métricas obrigatórias do §8.5.
- **Camada de dataset** (`app/datasets/`, §25 passo 6): catálogo das oito fontes
  do escopo, inventário do disco, adaptadores de leitura e montagem do payload de
  `dataset_versions`. **As oito estão em disco e são lidas**, somando ~35 GB dos
  40 GB; sete cabem no teto de 7 GB por fonte e o RDD2022 é exceção registrada
  (`datasets/STATUS.md`): RDD2022 (as 55.006 caixas das quatro classes da V1
  completas, MD5 conferido), UNIVALI/DNIT (2.235 amostras,
  SHA-256), Urban Community, Project Sidewalk e RampNet (Parquet com imagem
  embutida, SHA-256 oficial), BDD100K (ZIP lido fechado), CAMBER e Global
  Streetscapes. Estado e limitações por fonte em `datasets/STATUS.md`.
- 230 testes offline passando; os 3 de integração contra PostGIS real existem e
  ficam pulados enquanto `DATABASE_URL` não estiver preenchida.

O que ainda **não** existe: projeto Supabase provisionado, Storage e Auth,
importação da malha OSM, detector visual treinado, Worker e
fila, integração da PWA com Auth/Storage e inferência, previsões, Scout
(firmware ESP32), Gateway de borda, sincronização por PPS
e fusão multimodal.

**Frontend (parcial):** a antiga interface Jinja2 + HTMX foi substituída pela base
React + TypeScript + Vite/PWA em `frontend/`, conforme §3 e §28. Há captura pela
câmera, importação de imagem, localização manual/GPS e rascunhos IndexedDB;
consulta de ocorrências e MapLibre usam dados reais quando disponíveis. Auth,
Storage, inferência, relatórios HTTP e revisão ainda aguardam integração. Jinja2
permanece exclusivamente na geração de respostas estruturadas do backend (§15).

**Próximos passos, na ordem do §25:** provisionar o Supabase de desenvolvimento
e aplicar as migrations (passo 2) → Storage + Capture com foto real (passo 3) →
EXIF e localização manual (passo 4) → importar o recorte OSM do piloto e medir
os limiares de snap (passo 5) → gravar os manifestos já produzidos em
`dataset_versions` quando o banco existir (passo 6, restante) → baseline YOLOX
sobre o split do RDD2022 (passo 7).

## Estrutura

```
.
├── README.md
├── docs/
│   └── Planinng/MASTER_PLAN.md     planejamento oficial (fonte de verdade)
├── frontend/                         React + TypeScript + Vite/PWA (ver README próprio)
└── backend/
    ├── pyproject.toml              dependências e configuração de ruff/pytest
    ├── .env.example                modelo de ambiente
    ├── alembic.ini                  configuração do Alembic (sem credenciais)
    ├── alembic/
    │   ├── env.py                   lê DATABASE_URL do Settings; ignora objetos PostGIS
    │   └── versions/
    │       └── 0001_core_geospatial.py  núcleo canônico (MASTER_PLAN §5)
    ├── app/
    │   ├── main.py                 aplicação FastAPI e ciclo de vida
    │   ├── config.py               configuração única, lida do ambiente
    │   ├── logging.py              structlog JSON + correlation_id
    │   ├── api/v1/                 namespace único /api/v1
    │   ├── schemas/                contratos Pydantic v2
    │   ├── services/               regras de decisão determinísticas
    │   │   ├── core.py             captura, evento e enriquecimento geoespacial
    │   │   ├── exif.py             o que a foto declara (§6.3)
    │   │   ├── photo_ingest.py     foto existente → Capture (§25 passo 4)
    │   │   ├── risk.py             severidade e prioridade (§14)
    │   │   └── report.py           resposta sem LLM (§15)
    │   ├── templates/              templates Jinja2 de resposta
    │   ├── ml/                     dataset, split e avaliação (§8, §10)
    │   │   ├── taxonomy.py         rótulo externo → classe canônica (§8.2)
    │   │   ├── splits.py           split por sessão/rota, sem vazamento (§8.4)
    │   │   └── metrics.py          precision, recall, F1, AP, mAP, confusão (§8.5)
    │   ├── datasets/               fontes externas → dataset_versions (§25 passo 6)
    │   │   ├── catalog.py          o que cada fonte é, permite e não resolve
    │   │   ├── inventory.py        estado real dos arquivos em datasets/raw/
    │   │   ├── adapters.py         VOC, YOLO, máscara, GPX e CSV → formato canônico
    │   │   ├── records.py          formato canônico comum a todas as fontes
    │   │   ├── registration.py     payload de dataset_versions, ou recusa com motivo
    │   │   └── cli.py              python -m app.datasets.cli
    │   ├── repositories/           única camada que emite SQL
    │   ├── models/                 ORM espelhando as revisões
    │   └── db/                     engine, sessão e acesso ao Alembic
    └── tests/
```

Fronteiras que o código respeita:

- `api/` não contém SQL nem regra de decisão.
- `repositories/` é a única camada que executa queries.
- `services/` decide sem conhecer HTTP e sem chamar nenhum modelo generativo.
- As revisões do Alembic são a fonte da verdade do esquema; os models espelham,
  e um teste quebra se os dois divergirem.

## Fundação do backend e do banco

| Camada | Onde | Responsabilidade |
|--------|------|------------------|
| API | `app/api/v1/routes.py`, `app/api/v1/core.py` | HTTP, validação de entrada e códigos de status |
| Contratos | `app/schemas/core.py` | Taxonomia UrMind, modos de evidência e invariantes |
| Domínio | `app/services/core.py` | Decisão determinística, idempotência e orquestração |
| Persistência | `app/repositories/core.py` | SQLAlchemy 2 + PostGIS; única camada com SQL |
| Modelos | `app/models/core.py` | ORM espelhando a revisão |
| Algoritmo | `app/services/risk.py` | Severidade por classe, prioridade renormalizada e incerteza |
| Resposta | `app/services/report.py` + `app/templates/` | Texto preenchido, nunca gerado |
| Avaliação | `app/ml/` | Taxonomia, split e métricas do ciclo de treino |
| Dataset | `app/datasets/` | Catálogo, inventário, leitura e registro das fontes externas |
| Sessão | `app/db/session.py` | Engine assíncrona psycopg 3, pool e unidade de trabalho |
| Migrations | `alembic/versions/` + `app/db/migrate.py` | Alembic; o módulo só embrulha os comandos para uso em testes |

A revisão `0001_core_geospatial` cria os objetos lógicos do MASTER_PLAN §5 —
`missions`, `devices`, `captures`, `sensor_assets`, `detections`, `events`,
`road_segments`, `event_context`, `risk_assessments`, `responsibility_rules`,
`actions_catalog`, `predictions`, `reviews`, `model_versions`,
`dataset_versions`, `audit_log` — com PostGIS, índices GiST, RLS ligada em todas
e as funções `snap_to_road` e `events_near`.

Regras do planejamento que já são código testado:

- A coordenada original nunca é sobrescrita; o snap à via vive em
  `snapped_point`, ao lado de `road_segment_id` e `distance_to_road_m` (§11.2).
- Imagem sem localização não vira ocorrência geográfica (§11.1).
- Evidência só de sensor não pode afirmar classe visual (§31.9).
- GPS impreciso, confiança baixa ou ponto longe de via compatível vão para
  revisão humana — incerteza alta nunca vira urgência inventada (§27).
- `capture_key` e `event_key` tornam o reenvio idempotente (§7.2).

## O algoritmo (§14 e §15)

O motor de decisão é de regras, não de ML — e isso é escolha do §14.2: sem
histórico de "prioridade correta", treinar um modelo agora produziria um número
com aparência de ciência e lastro nenhum. Três invariantes sustentam o módulo, e
cada uma tem teste:

- **Pixel não vira centímetro.** A área da caixa é `apparent_extent`, uma fração
  do quadro. Sem calibração e sem depth, ela não é tamanho no mundo: um buraco
  pequeno de perto ocupa mais quadro que um grande de longe (§14.1, §27).
- **Foto sozinha nunca emite `critical`.** O teto do caminho visual é `high`;
  criticidade exige profundidade medida ou impacto de IMU calibrado, que só
  existirão com o Scout (§14.1, §21.3).
- **Fator ausente sai da conta.** Contexto externo pode faltar (§13). O peso do
  fator ausente sai junto com ele e a nota é renormalizada pelo que sobrou, para
  que "prioridade baixa" nunca se confunda com "quase não sei nada". O quanto
  sobrou vai em `coverage`.

Sobre desigualdade (§14.2): o motor **não recebe indicador socioeconômico
algum**. A regra é que esses indicadores nunca reduzam atendimento de área
vulnerável, e a garantia da V1 é não deixar a variável entrar — há um teste que
falha se alguém adicionar renda, vulnerabilidade ou censo à entrada.

A resposta do §15 fecha o ciclo: o texto é **preenchido, não gerado**. O
template roda com `StrictUndefined`, então um campo esquecido quebra o teste em
vez de sumir da página, e todo dado ausente aparece como "não disponível".

**Dívida conhecida — limiares não calibrados.** O §31.16 exige que limiar nasça
de baseline medido. Nenhum destes foi:

| Onde | Valores | Some quando |
|---|---|---|
| `app/services/core.py` | `MAX_SNAP_DISTANCE_M = 50`, `MAX_TRUSTED_ACCURACY_M = 30` | passo 5 do §25, medindo contra a malha real |
| `app/services/core.py` | confiança mínima `0.5` | passo 7, primeiro baseline do detector |
| `app/services/risk.py` | `PROVISIONAL_THRESHOLDS` (7 valores) e `_WEIGHTS` | piloto com eventos revisados por humano |

Todos estão marcados no código, e cada resultado do motor carrega
`thresholds_are_calibrated: false` para que ninguém leia esses números como
medidos.

## Dataset e avaliação (§8)

Estas três peças existem **antes** do primeiro treino de propósito: sem elas não
há como medir baseline nenhum, e o §8.5 termina dizendo que nenhum limiar mínimo
é inventado antes desse baseline.

**Taxonomia** (`app/ml/taxonomy.py`) — o mapa RDD2022 → UrMind é fechado nas
quatro classes da V1. Rótulo fora dela não vira `URMIND_UNKNOWN` nem some da
contagem: é recusado com o motivo escrito. O `D50` do RDD, por exemplo, é tampa
de poço, mas não pode virar `URMIND_MANHOLE` — essa classe exige dataset e
protocolo de anotação próprios (§8.2).

**Split** (`app/ml/splits.py`) — não existe função de split aleatório aqui, e a
ausência é deliberada. O §8.4 avisa que frames vizinhos são quase duplicados: um
sorteio por frame coloca a mesma cena no treino e no teste e faz a métrica subir
sem que o detector melhore. O split é por grupo (sessão, rota, local, origem),
grupo inteiro num split só, com `find_leakage()` conferindo o resultado de forma
independente. Quando há poucos grupos, um dos conjuntos pode ficar vazio — e
isso aparece em `warnings`, em vez de passar despercebido.

**Métricas** (`app/ml/metrics.py`) — precision, recall e F1 por classe, AP@50 e
AP@50:95 por classe, mAP e matriz de confusão. Duas escolhas mudam o que os
números significam:

- **Classe sem ground truth devolve `None`, não zero.** Se o recorte de teste
  não tem nenhum buraco, o AP de buraco é indefinido: 0,0 diria que o modelo
  falhou, 1,0 diria que acertou, e as duas coisas seriam mentira. O mAP também
  ignora essas classes, senão o número global passaria a depender de quantas
  classes vazias existem no recorte.
- **P/R/F1 dependem de limiar de confiança; AP não.** São perguntas diferentes —
  uma mede o ponto de operação escolhido, a outra a qualidade do ranqueamento em
  todos os limiares. O limiar usado vai gravado no resultado.

A matriz de confusão casa as caixas de forma agnóstica de classe antes de
comparar rótulos, então uma trinca longitudinal detectada como buraco aparece
como confusão entre as duas, e não como um falso positivo e um falso negativo
sem relação.

Falta do §8.5 apenas **latência e FPS**, que exigem medição no hardware real de
inferência e entram junto com o ONNX Runtime (§9).

## Datasets externos (§25 passo 6)

`app/datasets/` é a ponte entre as oito fontes do escopo e a tabela
`dataset_versions`. Nenhum módulo dela baixa arquivo, extrai ZIP ou escreve no
banco: essas operações gastam disco, rede ou cota e continuam sendo decisão
explícita de quem opera.

```bash
cd backend
python -m app.datasets.cli inventory                     # o que existe de fato
python -m app.datasets.cli read univali_br               # aceito x recusado
python -m app.datasets.cli register univali_br --write   # manifesto do payload
```

Duas fontes chegam em formato que não é pasta de arquivos soltos, e os
adaptadores as leem **no lugar**, sem extrair: Project Sidewalk e RampNet
publicam Parquet com o JPEG dentro da linha, e o BDD100K publica ZIP. Extrair
dobraria o espaço em disco sem acrescentar informação; `adapters.extract_image`
devolve uma imagem específica quando alguém precisa dela.

**O catálogo separa três papéis, e a diferença é o que impede exagero.**
`training_v1` tem imagem e rótulo que o §8.2 aceita hoje; `geo_reference` tem
coordenada e ocorrência, mas não anotação de treino, e por isso não vira
`Capture` nem `Detection` no banco — não foi observação do sistema;
`deferred` fica declarada sem adaptador, para que a existência da pasta não seja
confundida com dataset pronto. Nenhuma fonte está mais nesse último grupo:
RampNet e BDD100K ganharam adaptador quando os recortes oficiais chegaram, e o
Mapillary MSLS foi substituído pelo Global Streetscapes, que redistribui a mesma
imagem sob CC BY-SA 4.0 sem exigir login (`datasets/metadata/removed_sources.json`).

**O que cada fonte contribui de verdade é menor do que o nome sugere, e isso
está no código.** Do Urban Community Issues, sete pastas de classe, só `pothole`
entra: `open_manhole` não vira `URMIND_MANHOLE` porque bueiro exige dataset e
protocolo próprios (§8.2), e `cracks` é trinca genérica que não distingue
D00/D10/D20. Do UNIVALI/DNIT, só a máscara `POTHOLE` entra, pelo mesmo motivo.
Do CAMBER, nada: o CSV traz saída de um YOLO de terceiros com `user_confirmed`
vazio, e saída de modelo não é ground truth. Cada recusa carrega o motivo
escrito e vai contada no manifesto — um dataset com 90% fora do escopo não pode
parecer igual a um totalmente aproveitado.

**`.part` não é ZIP, e tamanho certo não fecha download.** O inventário reporta
`incomplete` mesmo quando o byte-count já bate com o publicado, porque o
downloader pode não ter fechado o arquivo; quem confere o checksum e renomeia é
uma pessoa. `--verify` lê os arquivos de ponta a ponta e é o único comando que
custa tempo de verdade.

**Registrar exige as três condições do §8.3, e a recusa diz qual falhou:**
arquivos obrigatórios completos no disco, ao menos uma amostra na taxonomia V1 e
split por grupo sem conjunto vazio. O payload gravado guarda quais grupos caíram
em cada split, a contagem por classe, o que foi recusado e o estado do disco no
momento — é o que permite reproduzir ou contestar um resultado depois (§10.2).

Os grupos de split por fonte estão em `catalog.py`: país no RDD2022, trecho de
rodovia no UNIVALI, rota no CAMBER, cidade no Project Sidewalk. O Urban
Community só oferece a pasta de classe como grupo, o que é fraco — e está
registrado como fraco, em vez de silenciosamente tratado como suficiente. Na
prática o registro dele é recusado hoje: todas as 300 imagens com buraco moram
na mesma pasta, e um grupo só não produz três conjuntos.

Tudo vive dentro do projeto: o dado bruto em `datasets/raw/`, os manifestos
gerados em `datasets/manifests/`. Nenhum caminho absoluto aparece em código,
configuração ou manifesto — a raiz é resolvida a partir da posição do próprio
pacote, então mover ou clonar a pasta `FECART Sistema` em outra máquina não
quebra nada. Os manifestos são pequenos e versionáveis; o raw fica fora do Git
por `.gitignore`, como o §10.2 pede. O estado detalhado de cada fonte está em
`datasets/STATUS.md`.

## Como executar

Pré-requisitos: Python 3.12+ e um projeto Supabase com a extensão PostGIS
disponível.

1. Copie `backend/.env.example` para `backend/.env` e preencha `DATABASE_URL` —
   connection string do Postgres do projeto (Supabase → Project Settings →
   Database → session pooler). Sem ela a API sobe, mas `/api/v1/health` reporta
   `database: not_configured` e as rotas de domínio não operam.
   `SUPABASE_URL` e `SUPABASE_SECRET_KEY` ainda não são lidas por nenhum código;
   passam a ser obrigatórias no passo 3 do §25 (Storage). A chave secret fica
   **somente** no backend e nunca é enviada ao navegador (§17).

2. No PowerShell, a partir da raiz do projeto:

```powershell
py -m venv backend\.venv
backend\.venv\Scripts\python.exe -m pip install -e "backend[dev]"
Set-Location backend
.venv\Scripts\python.exe -m alembic upgrade head  # aplica as migrations pendentes
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

API interativa: `http://127.0.0.1:8000/api/v1/docs`

### Migrations

```powershell
.venv\Scripts\python.exe -m alembic current            # revisão aplicada no banco
.venv\Scripts\python.exe -m alembic upgrade head       # aplica as pendentes
.venv\Scripts\python.exe -m alembic upgrade head --sql # revisa o SQL sem aplicar
.venv\Scripts\python.exe -m alembic downgrade -1       # desfaz a última
```

O Alembic registra a revisão aplicada em `public.alembic_version`. Uma revisão
já aplicada é registro histórico e não se edita: crie a próxima
(`alembic revision -m "..."`) em vez de alterar a anterior.

O autogenerate serve apenas para gerar um candidato, que precisa ser lido e
corrigido à mão — `env.py` já exclui as tabelas do PostGIS e os índices GiST,
mas extensões, funções PL/pgSQL, políticas RLS e grants continuam sendo escritos
manualmente, porque o Alembic não os descreve (§18.2).

### Testes

```powershell
.venv\Scripts\python.exe -m pytest -q                       # suíte offline
.venv\Scripts\python.exe -m pytest tests/test_db_integration.py   # exige DATABASE_URL
.venv\Scripts\python.exe -m ruff check app tests
.venv\Scripts\python.exe -m mypy app
```

O teste de integração aplica as migrations, cria um trecho viário sintético,
registra captura e evento, confere o snap e a busca por raio, e limpa o que
criou.

## API

| Método | Rota | O que faz |
|--------|------|-----------|
| GET | `/api/v1/health` | Estado da API e versão do PostGIS |
| POST | `/api/v1/captures` | Registra captura + detecções (idempotente por `capture_key`) |
| POST | `/api/v1/events` | Consolida o evento, faz o snap à via e define o status |
| GET | `/api/v1/events` | Lista eventos, com filtro por classe e status |
| GET | `/api/v1/events/nearby` | Eventos num raio, do mais próximo ao mais distante |
| GET | `/api/v1/events/{id}` | Detalhe do evento |

## Segurança

- Todas as tabelas têm RLS ativada e o acesso de `anon`/`authenticated` é
  revogado; hoje apenas o backend opera dados. As policies do §17 entram quando
  a PWA passar a ler o Data API diretamente.
- A chave secret do Supabase existe só no servidor; o frontend nunca a recebe.
- Nenhum reconhecimento facial e nenhuma identificação de pessoas ou placas, em
  nenhuma versão.
- Cada resultado guarda a versão de modelo e de dataset que o produziu (§17).

## Atribuição

Dados de vias e pontos de interesse virão do OpenStreetMap, sob ODbL. A
atribuição é permanente em todo mapa exibido pelo sistema.
