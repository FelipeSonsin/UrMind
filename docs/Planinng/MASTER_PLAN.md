# MASTER_PLAN

## UrMind Planejamento Completo

**Resumo técnico:** Supabase como backend gerenciado, algoritmo próprio, YOLOX, datasets, geolocalização, mapa e Scout futuro; nenhuma LLM participa do runtime.

**Data da revisão técnica:** 05/09/2026

> **Como usar este documento:** este é o caminho técnico oficial do UrMind. Ele explica o que será construído, por que cada tecnologia existe, onde ela executa, o que recebe, o que produz e como se conecta às outras partes. O objetivo é permitir que a equipe e os agentes de desenvolvimento saibam o que estão fazendo sem transformar o planejamento em um exemplo rígido de código ou estrutura de pastas.

> **Regra central:** o UrMind não utilizará ChatGPT, Gemini, Claude, Qwen, Ollama ou qualquer outra LLM para detectar, classificar, prever, priorizar, raciocinar, identificar responsável ou gerar respostas do produto. O runtime será formado por modelos treinados pela equipe, visão computacional, processamento de sinais, modelos tradicionais de Machine Learning, regras verificáveis e templates estruturados.

> **Regra de infraestrutura:** o projeto não utiliza Docker nem Docker Compose. O único backend gerenciado é o **Supabase**. FastAPI, Worker, treinamento, inferência e ferramentas de desenvolvimento executam diretamente em ambientes Python nativos (`.venv`) no notebook/servidor da equipe.

> **Regra de sincronização documental:** `MASTER_PLAN.md` e `UrMind Planejamento Completo.docx` representam o mesmo planejamento oficial. Mudanças funcionais devem ser aplicadas aos dois no mesmo ciclo de revisão. Nenhum dos dois pode conter requisitos técnicos diferentes do outro.

---

# 0. Requisitos de origem que não podem desaparecer do projeto

Esta seção preserva explicitamente os requisitos funcionais informados no documento **FECART – Entendimento do Projeto** e nas decisões confirmadas durante o desenvolvimento. Eles podem ser detalhados tecnicamente, mas não devem ser removidos ou alterados silenciosamente.

- O UrMind deve identificar problemas urbanos em vias públicas que afetem **segurança, mobilidade ou acessibilidade**, localizar o problema, entender o que ele pode afetar, estimar prioridade e indicar uma possível ação.
- Exemplos de escopo incluem **buracos, calçadas danificadas, bueiros abertos e problemas de sinalização**. Eles entram como classes reais somente quando houver dataset, protocolo de anotação, treinamento e validação adequados.
- O Scout futuro continua sendo a plataforma física de coleta, com **câmera + GPS + IMU + áudio** sincronizados no mesmo evento. O som nunca identifica sozinho um problema; ele funciona como evidência complementar.
- A geolocalização deve preservar **latitude/longitude, precisão/accuracy, timestamp, direção e velocidade quando disponíveis**, além da associação ao `RoadSegment`.
- O módulo de movimentação do Scout é separado do detector de danos urbanos e contempla futuramente **lane_detection, lane_segmentation, área trafegável, obstáculos e erro lateral**, inicialmente em Webots e pista fechada/controlada.
- O projeto mantém um **Gêmeo Digital Operacional 2D**, com mapa, posição/rota do Scout futuramente, sensores, detecções, RoadSegments, estado, risco e decisões.
- A base cartográfica oficial permanece **OpenStreetMap + PostGIS + MapLibre**. Um `RouteProvider` Google Maps pode existir somente como integração opcional futura, se houver requisito real e se custo/limites forem aceitos; ele não é dependência do núcleo.
- O desenvolvimento atual continua **foto-first** enquanto o Scout físico ainda não existe; quando o Scout for montado, ele alimentará o mesmo núcleo de `Capture → Detection → Event`, sem criar um segundo sistema.

## 0.1 Hardware do Scout já definido pelo projeto

Os itens abaixo são requisitos já informados e devem permanecer registrados no planejamento. Os valores são os valores fornecidos pela equipe e não devem ser tratados como cotação atual de mercado.

| Componente já definido | Valor informado | Papel no projeto |
|---|---:|---|
| Módulo microSD SPI | R$ 15,00 | buffer offline, evidências brutas e material para dataset |
| ESP32 DevKit V1 + ESP32-CAM | R$ 75,00 | aquisição dos sensores/estado e captura visual |
| Sensor IMU MPU-6050 | R$ 25,00 | aceleração, giroscópio, vibração e impacto |
| Microfone MEMS INMP441 | R$ 35,00 | áudio digital como evidência complementar |
| Módulo GPS u-blox NEO-M8N | R$ 49,00 | posição, tempo GPS/UTC e sincronização futura via TIMEPULSE |
| Arduino Uno | R$ 40,00 | controlador auxiliar previsto; papel final será validado na montagem sem remover o item |
| Bateria LiPo 2S | R$ 52,00 | alimentação do Scout |
| Conversor step-down | R$ 8,00 | adequação de tensão para a eletrônica |
| Chave liga/desliga geral | R$ 3,00 | controle geral de alimentação |
| Conector de bateria XT60 — 3 pares | R$ 5,00 | conexão de alimentação |
| Kit de parafusos M2.5/M3 + standoffs de nylon | R$ 20,00 | fixação e organização mecânica |
| Conversor USB-Serial | R$ 14,00 | programação e diagnóstico quando necessário |

**Subtotal dos itens com preço informado: R$ 341,00.**

Também permanecem como itens de apresentação já citados: **fita LED e lona preta para a mesa**, com custo ainda não definido.

## 0.2 Itens físicos que ainda precisam ser definidos/adquiridos para um Scout móvel completo

Esses itens complementam a ideia sem substituir os componentes já escolhidos. Não são considerados comprados e nenhum preço é inventado.

- chassi do veículo;
- motores com redução, rodas e apoio/caster;
- driver de motores dimensionado ao conjunto escolhido;
- cartão microSD compatível;
- carregador/balanceador apropriado para a bateria LiPo 2S e proteção elétrica adequada;
- fusível/proteção de alimentação;
- fios, conectores e placa de montagem;
- suportes rígidos para câmera, GPS e IMU;
- mecanismo físico de parada para testes controlados;
- sensor de distância simples como camada adicional de segurança em pista controlada, caso o teste do chassi justifique.

**Observação sobre o áudio:** o INMP441 é mantido como o microfone definido. Ele pode registrar forma de onda/transientes; o sistema só deve afirmar nível sonoro absoluto em dB SPL se houver uma cadeia de calibração adequada. Caso a medição metrológica de dB se torne requisito, será necessário adicionar a referência/calibração apropriada sem substituir silenciosamente o INMP441 já definido.

---

# 1. O que o UrMind precisa fazer

O UrMind identifica problemas urbanos em vias públicas que possam afetar segurança, mobilidade ou acessibilidade. A cadeia funcional deve transformar uma evidência do mundo real em um registro rastreável: detectar o problema, saber onde ele está, entender o contexto, estimar sua gravidade e prioridade, sugerir o responsável e uma ação possível e, somente quando houver histórico suficiente, produzir previsões.

A ideia física continua sendo o Scout: um pequeno veículo com câmera, GPS, IMU e áudio. Entretanto, como o Scout ainda não existe, o desenvolvimento atual será **foto-first**. Isso permite construir e validar banco, geolocalização, visão computacional, classificação, análise e mapa antes do hardware. Quando o Scout for montado, ele se torna uma nova fonte automática de evidências para o mesmo núcleo.

## 1.1 Dois caminhos de entrada, um único núcleo

| Entrada | Evidências disponíveis | Resultado permitido |
|---|---|---|
| Foto tirada pelo UrMind | imagem + GPS do celular + accuracy + timestamp | classe visual, localização, contexto, análise visual/geoespacial e mapa |
| Foto existente | imagem + GPS EXIF quando existir | mesmo fluxo; sem GPS, exige localização manual |
| Scout futuro | câmera + GPS + IMU + áudio + timestamps sincronizados | classe visual + evidências físicas + confiança multimodal + mapa |

Depois da entrada, os dois caminhos convergem para os mesmos objetos: **Capture → Detection → Event → localização/contexto → análise → mapa → revisão**.

---

# 2. Caminho técnico oficial — visão geral

## 2.1 Fluxo atual, antes do robô

**Celular/PWA ou upload → Supabase Auth → Supabase Storage → Capture no PostgreSQL/PostGIS → Supabase Queue → Worker Python → OpenCV → YOLOX/ONNX Runtime → Detection → Event → RoadSegment/PostGIS → contexto → severidade/prioridade → responsável/ação → resposta Jinja2 → MapLibre → revisão humana**

## 2.2 Fluxo futuro, quando o Scout existir

**Scout → câmera/GPS/IMU/áudio → Gateway Python → sincronização temporal → YOLOX + processamento de sinais → fusão multimodal → mesmos objetos Capture/Detection/Event → Supabase/PostGIS → análise → dashboard**

## 2.3 Quem conversa com quem

| Sistema | Fala com | O que troca |
|---|---|---|
| PWA React | Supabase Auth/Storage/Data API | login, foto, metadados, leitura autorizada |
| PWA React | FastAPI quando necessário | operações de negócio/administrativas que não devem ficar no cliente |
| FastAPI | Supabase PostgreSQL | domínio, consultas, regras, auditoria e geoprocessamento |
| FastAPI | APIs públicas | contexto urbano e ambiental |
| Supabase PostgreSQL | Supabase Queues (pgmq) | jobs de inferência e processamento assíncrono |
| Worker Python | Supabase Queue | lê/reserva/finaliza jobs |
| Worker Python | Supabase Storage | baixa evidência e publica artefatos derivados |
| Worker Python | PostgreSQL/PostGIS | grava Detection, Event, versões e resultados |
| Frontend | Supabase Realtime | recebe mudanças relevantes do estado do sistema |
| MapLibre | dados GeoJSON da aplicação | pontos, linhas, RoadSegments, heatmaps e filtros |
| Scout futuro | Gateway local | câmera e lotes temporizados de sensores |

---

# 3. Decisões tecnológicas finais e motivo de cada escolha

| Parte | Tecnologia oficial | Onde roda | Por que está no UrMind |
|---|---|---|---|
| Linguagem principal | Python 3.12 | notebook/servidor | ecossistema forte para API, ML, visão, sinais e geoprocessamento |
| API | FastAPI + Uvicorn + Pydantic v2 | `.venv` nativo | API tipada, validação, OpenAPI e integração natural com Python [R9] |
| ORM/banco | SQLAlchemy 2 + psycopg 3 | backend/Worker | acesso profissional ao PostgreSQL; psycopg 3 é suportado diretamente pelo SQLAlchemy [R7][R8] |
| Migrações | Alembic | ambiente Python | histórico incremental e revisável de schema [R10] |
| Geo ORM | GeoAlchemy2 | backend | integra SQLAlchemy com PostGIS [R11] |
| Banco | Supabase PostgreSQL | Supabase | fonte oficial de dados transacionais e relacionais |
| Geoespacial | PostGIS | Supabase | Point/LineString, índices e consultas espaciais [R1] |
| Arquivos | Supabase Storage | Supabase | evidências e artefatos fora das tabelas; RLS e S3 compatível [R3][R4] |
| Autenticação | Supabase Auth | Supabase | usuários/JWT integrados ao RLS [R5] |
| Autorização | PostgreSQL RLS | Supabase | controle por linha para dados expostos ao cliente [R2] |
| Fila assíncrona | Supabase Queues/pgmq | Supabase | fila durável em Postgres, sem Redis/Celery [R6] |
| Atualização de tela | Supabase Realtime | Supabase/browser | dashboard recebe mudanças sem polling constante [R12] |
| Visão treino | PyTorch + YOLOX-s | GPU disponível | detector anchor-free open source, licença Apache-2.0 [R13] |
| Visão deploy | ONNX + ONNX Runtime | Worker/Gateway | mesmo modelo pode executar em CPU/GPU/edge [R14][R15] |
| Imagem | OpenCV | Worker/Gateway | leitura, preprocessamento, qualidade e operações de visão |
| ML tabular | XGBoost + scikit-learn | Worker | fusão futura, risco/previsões quando houver dados [R23] |
| Explicação ML | SHAP | Worker/relatórios | explica modelos de árvore sem gerar texto por LLM [R24] |
| Sinais | NumPy + SciPy + librosa | Gateway/Worker | features de IMU e áudio quando o Scout existir |
| Anotação | CVAT Online Free | navegador | anota bounding boxes em serviço gerenciado, sem infraestrutura local obrigatória [R18][R19] |
| Versionamento de dados | DVC | desenvolvimento | Git referencia versões sem colocar dataset grande no Git [R20] |
| Experimentos | MLflow local | desenvolvimento | parâmetros, métricas, runs e artefatos [R21] |
| Drift/qualidade ML | Evidently Python | desenvolvimento/operação | data quality e drift como biblioteca Python [R22] |
| Frontend | React + TypeScript + Vite | navegador | PWA, dashboard e componentes tipados |
| Captura móvel | MediaDevices + Geolocation API | celular | câmera e posição com permissão do usuário; requer HTTPS [R16][R17] |
| Mapa | MapLibre GL JS | navegador | mapa 2D WebGL e GeoJSON sem Google Maps obrigatório [R25][R26] |
| EXIF | ExifTool + Pillow | backend/Worker | GPS/timestamp de fotos já existentes |
| Base cartográfica | OpenStreetMap | frontend/geoprocessamento | rede viária e POIs abertos |
| Geocodificação MVP | Nominatim público, baixo volume | API externa | endereço aproximado; uso limitado, identificado e com cache [R27] |
| Rotas futuras | pgRouting, somente quando necessário | Supabase | algoritmos de rota já suportados no Supabase [R28] |
| Firmware futuro | ESP-IDF + esp32-camera | ESP32s | drivers oficiais do hardware [R29][R30] |
| Sincronização futura | u-blox NEO-M8N TIMEPULSE | Scout | pulso sincronizado GPS/UTC para alinhar modalidades [R31] |

## 3.1 Tecnologias explicitamente fora do caminho oficial

**Docker, Docker Compose, Celery, Redis, Caddy, RabbitMQ, Kubernetes, Eclipse Mosquitto como dependência central, Photon self-hosted, LLMs no runtime e Google Maps como dependência obrigatória.**

Google Maps permanece, no máximo, como `RouteProvider` opcional futuro se um requisito real justificar custo/limites. A base oficial é **OSM + PostGIS + MapLibre**.

---

# 4. Supabase — o centro persistente do UrMind

Supabase não executará YOLOX. Ele é a camada persistente e de coordenação. O processamento de IA continua em Python. Essa separação evita tentar colocar um modelo pesado em um ambiente inadequado e mantém cada serviço fazendo o que foi projetado para fazer.

## 4.1 O que fica no Supabase

- PostgreSQL: entidades, relações, estados, regras, contexto e auditoria.
- PostGIS: localização, RoadSegments e consultas espaciais.
- Storage: fotos, recortes, modelos ONNX aprovados e artefatos selecionados.
- Auth: identidade dos usuários.
- RLS: autorização de dados por usuário/papel.
- Queues/pgmq: jobs de inferência e tarefas assíncronas.
- Realtime: mudanças relevantes para o dashboard.

## 4.2 O que NÃO fica executando dentro do Supabase

- treinamento PyTorch/YOLOX;
- inferência ONNX pesada;
- OpenCV em lote;
- XGBoost de grandes lotes;
- processamento completo de áudio/IMU;
- CVAT/MLflow/Evidently como serviços permanentes.

Esses processos rodam em Python nativo no notebook/servidor. Edge Functions não são escolhidas para YOLOX porque o ambiente hospedado possui limites de CPU, memória e duração incompatíveis com o objetivo de inferência pesada [R32].

## 4.3 Conexão FastAPI/Worker → PostgreSQL

O planejamento separa dois usos:

- **Aplicação persistente:** FastAPI e Worker utilizam uma conexão PostgreSQL adequada a clientes persistentes. Em redes IPv4-only, o Supavisor em **session mode** é a escolha indicada pelo próprio Supabase.
- **Migrações/backup:** Alembic e ferramentas nativas preferem a conexão direta quando o ambiente possui IPv6. Se isso não estiver disponível, usar uma alternativa suportada pelo Supabase, sem hardcode de credenciais [R33].

As URLs ficam em variáveis de ambiente; nenhuma senha entra no Git.

## 4.4 Storage e limites reais

As fotos e modelos não devem ser gravados como `bytea` nas tabelas. Supabase Storage guarda os objetos e PostgreSQL guarda `bucket`, `object_path`, checksum, tamanho, tipo MIME e vínculos.

O upload padrão é recomendado para arquivos pequenos; o próprio Supabase recomenda TUS resumable para arquivos acima de 6 MB. No plano Free, o limite global por arquivo não pode ultrapassar 50 MB [R34][R35]. Portanto:

- fotos JPEG individuais: Storage;
- recortes/artefatos pequenos: Storage;
- modelo ONNX aprovado, se couber na cota: Storage;
- **RDD2022 completo (12,36 GB): não copiar inteiro para um projeto Free**. O raw fica na fonte oficial/local; o Supabase guarda metadados, checksums e subconjuntos curados quando a cota permitir [R36].

## 4.5 Dataset e DVC usando o próprio Supabase quando couber

Supabase Storage é compatível com o protocolo S3 [R4]. DVC aceita armazenamento S3-compatible com `endpointurl` configurável [R20]. Assim, depois de uma prova de conceito pequena, um bucket privado do Supabase pode funcionar como **remote DVC para conjuntos curados/artefatos que caibam na cota**, usando credenciais S3 somente no ambiente controlado.

Isso não significa duplicar datasets públicos gigantes. Para RDD2022, DVC registra a versão/manifesto e o projeto mantém o raw local ou recuperável da fonte oficial.

---

# 5. Modelo de dados — o que cada objeto significa

O banco deve representar o fluxo real, não nomes parecidos para a mesma coisa.

| Objeto | Significado | Não confundir com |
|---|---|---|
| Mission | uma sessão de coleta | Device |
| Device | Scout/câmera/dispositivo identificado | Mission |
| Capture | evidência visual recebida | Detection |
| Detection | saída real de um modelo para uma Capture | Event |
| Event | ocorrência urbana consolidada | frame individual |
| RoadSegment | trecho da via | coordenada do evento |
| SensorAsset | janela/lote de IMU ou áudio | cada amostra como linha |
| EventContext | dados externos associados ao evento | Detection |
| RiskAssessment | severidade/prioridade e fatores | Prediction |
| ResponsibilityRule | regra de competência | resultado gerado por LLM |
| ActionCatalog | catálogo fechado de ações | texto livre |
| Prediction | estimativa futura validada | observação atual |
| Review | correção/aceite humano | alteração silenciosa |
| ModelVersion | versão do modelo e métricas | arquivo sem rastreabilidade |
| DatasetVersion | versão, origem, split e licença | pasta sem versão |
| AuditLog | histórico de alterações relevantes | log de debug |

## 5.1 Campos geográficos principais

- `Capture.original_location`: localização observada, preferencialmente `geography(Point, 4326)` para distância métrica.
- `Capture.accuracy_m`: precisão informada pelo dispositivo/receptor; nunca chamar de “localização exata”.
- `Capture.location_source`: `gps_device`, `gps_scout`, `exif`, `manual` ou `imported`.
- `RoadSegment.geom`: `geometry(LineString, 4326)` para mapa e operações geométricas.
- `Event.snapped_point`: ponto calculado na via, separado do ponto original.
- distância ao RoadSegment: calculada em metros por PostGIS, sem substituir a coordenada original.

---

# 6. Fluxo de foto — primeira versão funcional sem Scout

## 6.1 Foto tirada dentro do UrMind

1. Usuário autenticado abre “Registrar problema”.
2. A PWA pede permissão de câmera e localização.
3. `getUserMedia()` abre a câmera; Geolocation API obtém latitude, longitude, accuracy, timestamp e, quando disponíveis, heading/speed.
4. Como essas APIs exigem contexto seguro, o modo móvel deve ser servido em **HTTPS** [R16][R17].
5. A foto é enviada a um bucket privado do Supabase Storage.
6. A aplicação registra Capture com `object_path`, GPS e metadados.
7. Uma função/trigger de banco insere um job na Supabase Queue `inference_jobs`.
8. Worker Python reserva o job, baixa a imagem e executa inferência.
9. O resultado real vira Detection; sem modelo disponível, fica `model_not_available` e não cria resultado falso.
10. Quando os critérios forem atendidos, Detection é consolidada em Event.
11. PostGIS associa o evento a um RoadSegment.
12. O dashboard recebe atualização e mostra o marcador.

## 6.2 Por que a PWA pode falar diretamente com Supabase

Supabase possui cliente JavaScript oficial para Auth, Data API, Storage e Realtime; no browser ele é inicializado com a URL do projeto e a **publishable key**. A secret key permanece apenas em componentes controlados e nunca vai para o browser [R37][R48].

No UrMind, o cliente pode acessar somente operações explicitamente liberadas. Operações administrativas, regras sensíveis e integrações externas continuam no FastAPI/Worker.

## 6.3 Foto já existente

- ExifTool tenta extrair GPS e timestamp.
- Com GPS válido: `location_source=exif`.
- Sem GPS: a interface exige o usuário marcar/confirmar no mapa.
- O UrMind **não tenta adivinhar a rua pela aparência** da fotografia.
- Correção manual mantém a posição anterior em auditoria.

---

# 7. Supabase Queues + Worker Python — processamento assíncrono

Supabase Queues é uma fila pull-based e durável baseada em `pgmq`, com mensagens que permanecem até serem processadas/removidas e uma janela de visibilidade para consumidores [R6][R38].

## 7.1 Fluxo do job

**Capture pending_inference → queue message com `capture_id` → Worker lê/reserva → baixa Storage → executa modelo → grava Detection/estado → arquiva/remove job**.

A mensagem carrega IDs e metadados mínimos; nunca a imagem inteira.

## 7.2 Regras de confiabilidade

- idempotência por `job_id`/`capture_id`;
- Detection possui constraint/chave que evita duplicação indevida para o mesmo modelo/run;
- falha não gera `inference_completed`;
- job só é finalizado após persistência consistente;
- retries possuem limite e erro final auditável;
- Worker desligado não perde jobs; eles permanecem na Queue.

## 7.3 Por que a fila fica no Supabase

Celery e Redis não entram no caminho oficial porque criariam uma segunda infraestrutura persistente. Como a Queue já existe dentro do Supabase/PostgreSQL, o UrMind reduz serviços e pontos de falha sem perder processamento assíncrono.

---

# 8. Visão computacional — como o algoritmo distingue os problemas

## 8.1 Detector oficial

O detector visual base é **YOLOX-s**, treinado em PyTorch e exportado para ONNX. O repositório oficial é Apache-2.0 e declara suporte a ONNX e outros backends [R13].

O “algoritmo próprio” não significa reinventar uma arquitetura neural do zero. Significa que a equipe controla os dados, taxonomia, treino, pesos finais, limiares, pós-processamento, versão do modelo e lógica do UrMind.

## 8.2 Taxonomia V1

| ID interno | Classe | Fonte inicial |
|---|---|---|
| URMIND_ROAD_D00 | trinca longitudinal | RDD2022 D00 |
| URMIND_ROAD_D10 | trinca transversal | RDD2022 D10 |
| URMIND_ROAD_D20 | trinca malha/alligator | RDD2022 D20 |
| URMIND_ROAD_D40 | buraco/pothole | RDD2022 D40 |

O RDD2022 possui 47.420 imagens de seis países, mais de 55 mil instâncias e exatamente essas quatro categorias principais [R36][R39].

### Correção importante: `URMIND_UNKNOWN`

`URMIND_UNKNOWN` é **estado de aplicação**, não uma classe YOLOX da V1. Ele aparece quando o sistema não consegue atribuir uma classe conhecida com confiança/qualidade suficientes. Só deve virar uma classe treinada se um dataset futuro definir explicitamente o que “unknown” significa.

Classes como bueiro aberto, calçada e sinalização entram somente depois de dataset real e protocolo de anotação próprio. Não reutilizar uma classe só porque visualmente “parece parecida”. Isso preserva o escopo funcional original sem anunciar como pronta uma classe que ainda não foi treinada e validada.

## 8.3 Como o aprendizado acontece

1. datasets aprovados são registrados em `dataset_versions`;
2. rótulos externos são mapeados para a taxonomia canônica;
3. imagens próprias são anotadas no CVAT;
4. split é feito por sessão/rota/local/origem, não por frames aleatórios;
5. treino utiliza somente o conjunto train;
6. ajuste usa validation;
7. teste final fica congelado;
8. modelo aprovado é exportado ONNX;
9. Worker usa somente versão promovida;
10. falsos positivos/negativos voltam para revisão e novo ciclo.

## 8.4 Por que separar por rota/sessão

Frames vizinhos são quase duplicados. Se um frame cair no treino e o seguinte no teste, a métrica fica artificialmente alta. O split deve impedir vazamento entre cenas relacionadas.

## 8.5 Métricas obrigatórias

- Precision e Recall por classe;
- F1 por classe;
- AP@50 e AP@50:95 por classe;
- mAP agregado;
- matriz de confusão;
- latência/FPS no hardware real de inferência.

Nenhum limiar mínimo é inventado antes do primeiro baseline.

---

# 9. ONNX Runtime — como o modelo entra no produto

PyTorch é usado para treinamento. O modelo promovido é exportado para ONNX e verificado. ONNX Runtime carrega o arquivo para inferência e permite usar CPU ou Execution Providers como CUDA/OpenVINO conforme o hardware disponível [R14][R15].

O Worker registra:

- `model_version`;
- hash/checksum do ONNX;
- `dataset_version`;
- classes;
- input size/opset;
- limiar utilizado;
- métricas de promoção;
- Execution Provider usado.

Se não houver modelo oficial, o sistema retorna estado claro como `model_not_available`. Não existe confidence hardcoded.

---

# 10. Dataset, CVAT, DVC e modelos — política prática

## 10.1 CVAT Online Free

CVAT Online possui um plano Free gerenciado, adequado para começar sem infraestrutura local própria. Atualmente o Free possui limites de projetos/tarefas/armazenamento e exporta anotações; limites podem mudar e devem ser revistos antes de cada etapa [R18][R19].

O UrMind não depende de recursos pagos do CVAT. As imagens originais continuam versionadas fora do Git; o CVAT é ferramenta de anotação.

## 10.2 DVC

Git versiona código e arquivos `.dvc`; DVC controla quais dados/modelos correspondem àquele commit. Como Supabase Storage é S3-compatible e DVC suporta endpoint S3 compatível, o projeto pode validar um remote DVC no Supabase para artefatos/subconjuntos que caibam nas cotas [R4][R20].

Credenciais S3 são server-side/local e nunca entram no repositório.

## 10.3 MLflow

MLflow registra cada execução de treino como run, com parâmetros, métricas e artefatos [R21]. Para manter a implantação simples, o UrMind usa MLflow **localmente durante desenvolvimento**. Somente os modelos aprovados e seus metadados obrigatórios são promovidos para Supabase (`model_versions` + Storage).

## 10.4 Evidently

Evidently roda como biblioteca Python para comparar dados novos com dados de referência, detectar drift e executar avaliações de qualidade [R22]. Não precisa ser um servidor permanente no MVP.

---

# 11. Geolocalização e PostGIS — de coordenada a RoadSegment

## 11.1 Regra principal

GPS informa uma estimativa, não uma posição “exata”. O UrMind sempre armazena `accuracy_m` quando disponível e distingue ponto original de ponto ajustado à via.

## 11.2 Processo

1. salvar `original_location`;
2. selecionar RoadSegments candidatos por proximidade com `ST_DWithin` [R46];
3. ordenar candidatos por distância;
4. usar `ST_ClosestPoint` para obter `snapped_point` sobre a linha [R47];
5. calcular distância métrica entre original e snapped;
6. se distância/accuracy forem ruins, exigir revisão;
7. nunca substituir silenciosamente original_location.

## 11.3 Rede viária

A V1 importa apenas a área piloto do OpenStreetMap. `road_segments` guarda OSM id/tags úteis e a LineString. Não carregar o Brasil inteiro sem necessidade.

## 11.4 Rotas

pgRouting é suportado pelo Supabase [R28], porém **não é requisito do modo foto**. Ele só entra quando existir necessidade real de planejar rota do Scout ou calcular caminhos. Isso evita desenvolver uma função prematuramente.

---

# 12. Endereço e mapas sem Google Maps obrigatório

## 12.1 Reverse geocoding

Nominatim transforma coordenada em endereço aproximado, mas seu resultado é o objeto OSM adequado mais próximo, não uma verdade absoluta [R40]. O serviço público da OSMF exige uso leve: máximo absoluto de 1 request/s, User-Agent/Referer válido, atribuição e cache; bulk é desencorajado [R27].

Portanto o UrMind:

- consulta somente quando o usuário/evento realmente precisa;
- mantém cache por coordenada/área;
- identifica o aplicativo;
- mostra atribuição OSM;
- trata endereço como contexto, não como fonte da coordenada;
- mantém o provider desacoplado para troca futura sem reescrever o sistema.

## 12.2 Mapa 2D

MapLibre GL JS renderiza mapa interativo WebGL e aceita GeoJSON, adequado para Points/LineStrings, clusters e heatmaps [R25][R26].

O Gêmeo Digital Operacional 2D deve mostrar:

- captures/events;
- RoadSegments;
- classe e status;
- prioridade;
- original vs snapped point quando necessário;
- filtros;
- posição/rota do Scout futuramente;
- sensores e gráficos futuramente.

---

# 13. APIs públicas de contexto — como entram sem controlar o sistema

As APIs externas **enriquecem** o Event. Elas nunca são requisito para a existência de uma detecção comprovada.

| Fonte | Uso | Regra |
|---|---|---|
| OSM/Geofabrik/Overpass | vias, POIs e base espacial | usar recortes e cache |
| GeoSampa WFS | camadas oficiais de São Paulo | contexto territorial quando o piloto for SP |
| IBGE SIDRA/Setores/Malhas | população e recortes | exposição/vulnerabilidade com cuidado metodológico |
| INEP | escolas | proximidade a equipamento educacional |
| CNES/DATASUS | saúde | proximidade a unidade de saúde |
| Open-Meteo | chuva/histórico | contexto ambiental e futuros modelos |
| TCE-SP | contexto administrativo | nunca inferir culpa automaticamente |
| BrasilAPI/ViaCEP | códigos/CEP | fallback administrativo |

Cada integração possui timeout, cache, origem, timestamp da consulta e estado `context_unavailable` quando falhar.

---

# 14. Severidade, prioridade, responsável e ação

## 14.1 Severidade

Severidade é específica da classe. Na fase foto-only, o sistema usa somente evidência visual que possa ser defensavelmente extraída. Sem calibração/depth, não converte pixels em centímetros nem afirma profundidade real de buraco.

Quando o Scout existir, o impacto do IMU poderá acrescentar evidência física após calibração experimental.

## 14.2 Prioridade

A V1 começa com regras transparentes e versionadas, porque ainda não há dataset histórico de “prioridade correta” suficiente para treinar ML com honestidade.

Fatores possíveis:

- classe e severidade disponível;
- confiança e qualidade da evidência;
- exposição/movimento do local quando houver dado confiável;
- proximidade de escola/saúde/travessia;
- impacto em acessibilidade;
- recorrência no mesmo trecho;
- condição ambiental relevante;
- incerteza.

Indicadores socioeconômicos **não podem reduzir atendimento de áreas vulneráveis**. Se forem usados, a regra deve ser documentada e revisada para evitar reproduzir desigualdades.

## 14.3 Responsável

Não é ML nem LLM. O backend consulta `responsibility_rules` com:

**jurisdição + tipo de ativo + classe + fonte/regra vigente → responsável sugerido**.

Sem base confiável: `requires_triage`.

## 14.4 Ação

`actions_catalog` contém ações padronizadas e versionadas como inspeção, sinalização temporária ou reparo, sempre como sugestão. O UrMind não substitui decisão técnica/administrativa do órgão.

---

# 15. Respostas do UrMind sem LLM

Jinja2 transforma dados estruturados em texto. O template só pode falar o que existe no resultado.

A resposta pode conter:

- problema/classe;
- confiança;
- localização e accuracy;
- RoadSegment/endereço contextual;
- evidências disponíveis;
- severidade/prioridade;
- responsável e fundamento;
- ação sugerida;
- previsão somente se existir;
- limitações.

Se um dado não existe, fica “não disponível” ou é omitido. Isso é a camada anti-alucinação.

---

# 16. Realtime, dashboard e revisão humana

## 16.1 Realtime

Para MVP com baixo volume, `Postgres Changes` é simples. A própria documentação do Supabase recomenda Broadcast para maior escala; portanto o plano é:

- V1: Postgres Changes nas tabelas estritamente necessárias;
- escala futura: migrar eventos de UI para Broadcast se o volume justificar [R12][R41].

Não transmitir cada amostra de IMU/áudio por Realtime.

## 16.2 Revisão humana

Revisor pode confirmar, rejeitar ou corrigir classe/local. A inferência original permanece registrada; correção vira `Review` e AuditLog.

Revisões confirmadas alimentam a próxima versão do dataset, mas **não causam retreinamento automático em produção**.

---

# 17. Segurança e privacidade

- RLS em toda tabela exposta ao Data API [R2].
- publishable key somente no cliente; secret key somente servidor/Worker [R37].
- Storage privado por padrão; upload/leitura controlados por RLS [R3].
- credenciais S3 do Storage somente em ambiente controlado [R42].
- validar MIME, extensão, tamanho e conteúdo da imagem.
- paths de Storage gerados pelo sistema; não confiar em filename do usuário.
- logs nunca registram tokens, senhas ou secrets.
- painel público, se existir, deve preferir recorte do defeito e ocultar rostos/placas desnecessários.
- cada resultado registra model_version, dataset_version e timestamp.

---

# 18. Testes e qualidade

## 18.1 Backend

- pytest para domínio, validação, regras e serviços;
- testes de integração reais com Supabase dev para PostGIS, RLS, Storage e Queues;
- Ruff para lint;
- mypy para tipos.

## 18.2 Banco

Alembic é a única fonte de alterações estruturais. Autogenerate gera candidato que deve ser revisado manualmente antes de aplicar [R10][R43].

Sem PostgreSQL local, migrations são validadas no projeto Supabase de desenvolvimento com backup/controle e dados descartáveis. Não usar SQLite para fingir compatibilidade com PostGIS.

## 18.3 ML

- treino/validation/test separados;
- seed e config registrados;
- dataset/version/checksum;
- métricas por classe;
- comparação PyTorch vs ONNX;
- casos difíceis registrados;
- latência medida no hardware real.

---

# 19. Observabilidade sem criar nova infraestrutura

O MVP não precisa de uma “pilha” de monitoramento separada.

- logs estruturados Python com `capture_id`, `job_id`, `detection_id`, `event_id`;
- Supabase Dashboard para banco, Storage, Queues e Realtime;
- `pg_stat_statements` para identificar SQL caro quando necessário [R44];
- MLflow local para treino;
- Evidently Python para drift/qualidade;
- métricas simples do Worker: jobs pendentes, sucesso, falha, latência e retries.

---

# 20. Funcionamento offline

## Sem Scout

Se a PWA estiver sem rede, pode manter uma captura pendente no armazenamento do navegador e solicitar envio posterior. Não declarar sincronização concluída até o Supabase confirmar.

## Com Scout futuramente

- câmera/sensores/GPS continuam gravando em microSD;
- Gateway mantém spool de arquivos/jobs locais;
- ao reconectar, envio idempotente por IDs estáveis;
- não manter um segundo banco oficial concorrente ao Supabase.

---

# 21. Scout futuro — hardware e software conectados ao núcleo

O Scout não muda o banco nem o algoritmo visual; ele adiciona modalidades. A lista de componentes e valores preservada na seção 0.1 continua válida e não é substituída por esta descrição funcional.

## 21.1 Responsabilidades

| Parte | Tecnologia | Função futura |
|---|---|---|
| ESP32-CAM | ESP-IDF + esp32-camera | quadros JPEG/snapshots [R29] |
| ESP32 DevKit V1 | ESP-IDF | GPS, IMU, áudio, microSD e estado |
| Arduino Uno | firmware Arduino quando necessário | controlador auxiliar previsto; uso final depende da divisão real de I/O/motores |
| MPU-6050 | I2C | aceleração/giroscópio |
| INMP441 | I2S/DMA | PCM digital; I2S é suportado pelo ESP32 [R30] |
| NEO-M8N | UART + TIMEPULSE/PPS | GPS/UTC e sincronização [R31] |
| microSD | SPI | buffer offline/dataset |
| Gateway | Python + HTTP local | recebe lotes e associa timestamps |

A comunicação local será HTTP/HTTPS em snapshots/lotes para evitar broker adicional no MVP. ESP-IDF possui cliente HTTP/HTTPS e fluxo de streaming [R45].

## 21.2 Sincronização

O TIMEPULSE do NEO-M8N pode ser sincronizado à grade GPS/UTC. Os dois ESP32s recebem uma referência comum; cada frame/lote ganha timestamp. O Gateway associa janelas por tempo [R31].

Frequências e tolerâncias só são definidas depois de medir o protótipo.

## 21.3 IMU

Pipeline futura:

**calibração → filtragem → magnitude/RMS/jerk/picos/energia → janelas rotuladas → XGBoost auxiliar**.

SciPy fornece ferramentas de processamento de sinais e análise espectral, incluindo estimativa de densidade espectral pelo método de Welch; elas são candidatas técnicas, não regras automáticas [R50].

Não existe regra “pico = buraco”. Lombada, frenagem e pavimento normal entram como negativos.

## 21.4 Áudio

INMP441 fornece áudio digital I2S. Sem calibração metrológica, o sistema não afirma dB SPL absoluto. `librosa` oferece features como RMS, MFCC, Mel spectrogram e spectral centroid; o UrMind só mantém as que demonstrarem ganho nos dados reais [R51].

Áudio nunca classifica sozinho o problema urbano.

---

# 22. Motor de Decisão Multimodal — fase posterior ao Scout

Quando existirem eventos reais pareados:

**YOLOX + qualidade da imagem + IMU + áudio + GPS/contexto → vetor por evento → XGBoost de fusão → confiança consolidada**.

Regras:

- a classe urbana continua prioritariamente visual;
- modalidades ausentes são marcadas como ausentes, não preenchidas;
- o modelo só é treinado com eventos reais rotulados;
- SHAP explica features do XGBoost, mas não cria causalidade [R24];
- comparar câmera-only, câmera+IMU e câmera+IMU+áudio antes de afirmar ganho.

---

# 23. Visão computacional para movimentação do Scout

Esse módulo é separado do detector de danos urbanos.

Objetivos futuros em **Webots/pista fechada**. Webots é um simulador robótico open source e multiplataforma, adequado para modelar e testar robôs antes do hardware real [R49]:

- lane detection;
- lane segmentation;
- área trafegável;
- obstáculos;
- erro lateral;
- lógica de parada.

Ele não é “direção autônoma pronta para via pública”. Primeiro validação em simulador e ambiente controlado; depois estudo de sim-to-real.

---

# 24. Previsões e projeções — somente depois de histórico

A V1 funciona sem previsão. Prever antes de acumular histórico seria fabricar precisão.

| Tarefa futura | Possível técnica | Dados mínimos |
|---|---|---|
| recorrência | XGBoost classifier | eventos históricos por RoadSegment + tempo/contexto |
| risco do trecho | XGBoost/LightGBM | histórico suficiente e validação temporal |
| hotspots | H3 + DBSCAN/estatística espacial | volume georreferenciado |
| evolução | regressão/modelo temporal | observações repetidas do mesmo trecho |
| cenários | simulação com modelo validado | dados de intervenção; não chamar de causal sem evidência |

Validação deve ser temporal. Previsão registra horizonte, versão, erro/incerteza e data.

---

# 25. Ordem de construção atual — sem Scout

| Passo | Entregável | Só avançar quando... |
|---|---|---|
| 0 | limpar decisões antigas | Supabase + processos Python nativos são o caminho oficial; infraestrutura paralela antiga não entra no runtime |
| 1 | backend + migrations | API inicia, testes e banco coerentes |
| 2 | Supabase/PostGIS/Auth/RLS | conexão, extensão e políticas validadas |
| 3 | Storage + Capture | foto real entra e fica rastreável |
| 4 | EXIF + localização manual | nenhum GPS é inventado |
| 5 | RoadSegments/PostGIS | original e snapped separados |
| 6 | dataset/taxonomia/DVC/CVAT | splits e classes documentados |
| 7 | YOLOX PyTorch | baseline real medido |
| 8 | ONNX + Worker + Queues | job real produz Detection real |
| 9 | Event/deduplicação | rastreabilidade Capture→Detection→Event |
| 10 | contexto externo | falha de API não quebra Event |
| 11 | severidade/prioridade/regras | fatores auditáveis |
| 12 | responsável/ação/Jinja2 | sem LLM e sem texto inventado |
| 13 | React/PWA/MapLibre | fluxo foto→mapa funciona em HTTPS |
| 14 | revisão/MLOps/segurança | revisão e versões fecham ciclo |
| 15 | integração end-to-end | caminho completo demonstrado |

Depois disso começa a trilha física: Scout → sensores → sincronização → fusão → Webots → piloto.

---

# 26. Critérios de “pronto” por componente

Um componente só é IMPLEMENTADO se:

- existe comportamento real, não arquivo vazio;
- usa dado real ou fixture apenas em teste;
- erros são representados explicitamente;
- possui teste adequado;
- persiste versão/origem quando necessário;
- não depende de segredo versionado;
- não contradiz este planejamento;
- não apresenta mock como produção.

Estados de projeto recomendados: **IMPLEMENTADO, PARCIAL, PREPARADO, PENDENTE, BLOQUEADO, INCORRETO**.

---

# 27. O que não deve ser inventado

- confidence fixa;
- coordenada fixa;
- Detection sem modelo;
- Event sem evidência;
- previsão sem histórico;
- sensor falso no dashboard;
- centímetros/profundidade a partir de pixels sem calibração;
- órgão responsável sem regra/fonte;
- “dB” absoluto do INMP441 sem calibração;
- texto gerado por LLM preenchendo lacunas;
- métrica de teste contaminada por frames do treino.

---

# 28. Inventário final de tecnologias

| Área | Tecnologias |
|---|---|
| Backend | Python 3.12, FastAPI, Uvicorn, Pydantic v2 |
| Persistência | SQLAlchemy 2, psycopg 3, Alembic, GeoAlchemy2 |
| Supabase | PostgreSQL, PostGIS, Storage, Auth, RLS, Realtime, Queues/pgmq |
| Visão | PyTorch, YOLOX-s, OpenCV, ONNX, ONNX Runtime |
| ML | NumPy, pandas, scikit-learn, XGBoost, SHAP |
| Sinais futuros | SciPy, librosa, PyArrow/Parquet |
| Dados/MLOps | CVAT Online Free, DVC, MLflow local, Evidently |
| Frontend | React, TypeScript, Vite, PWA, supabase-js |
| Mapa | MapLibre GL JS, GeoJSON, OpenStreetMap, H3 futuro |
| Arquivos | ExifTool, Pillow, Supabase Storage S3-compatible |
| Hardware futuro | PlatformIO/ESP-IDF, esp32-camera, I2C, I2S/DMA, UART, SPI/microSD, HTTP/HTTPS |
| Qualidade | pytest, Ruff, mypy, Git/GitHub, GitHub Actions quando aplicável |

---

# 29. Matriz de rastreabilidade — requisito → tecnologia → resultado

| Requisito | Tecnologia | Resultado persistido |
|---|---|---|
| registrar foto | PWA + Storage | Capture |
| localizar | Geolocation/EXIF + PostGIS | original_location/accuracy |
| associar via | PostGIS | road_segment_id/snapped_point |
| detectar dano | YOLOX + ONNX Runtime | Detection |
| consolidar ocorrência | regras/serviço | Event |
| contexto | APIs + EventContext | EventContext |
| prioridade | regras/XGBoost futuro | RiskAssessment |
| responsável | tabela de regras | responsibility result |
| ação | catálogo versionado | action result |
| resposta | Jinja2 | relatório estruturado |
| mapa | MapLibre | visualização GeoJSON |
| fila | Supabase Queues | inference job |
| versão dataset | DVC + dataset_versions | lineage |
| versão modelo | MLflow + model_versions | lineage |
| revisão | Auth/RLS + Review | correção auditável |
| Scout futuro | ESP32s + Gateway | Capture/SensorAsset |
| fusão futura | XGBoost + SHAP | confiança multimodal |
| previsão futura | ML temporal/tabular | Prediction |

---

# 30. Fontes técnicas pesquisadas e validadas

As decisões foram verificadas em documentação oficial, repositórios oficiais e fontes primárias e foram novamente conferidas em 05/09/2026 nos pontos críticos de Supabase/PostGIS/Queues/Storage, YOLOX, ONNX Runtime, ESP-IDF, MapLibre, Webots e demais integrações centrais. Serviços gratuitos e cotas podem mudar; antes de uma entrega pública, revisar novamente limites e termos.

**[R1] Supabase — PostGIS**  
https://supabase.com/docs/guides/database/extensions/postgis

**[R2] Supabase — Row Level Security**  
https://supabase.com/docs/guides/database/postgres/row-level-security

**[R3] Supabase — Storage Access Control / RLS**  
https://supabase.com/docs/guides/storage/security/access-control

**[R4] Supabase — Storage S3 Compatibility**  
https://supabase.com/docs/guides/storage/s3/compatibility

**[R5] Supabase — Auth**  
https://supabase.com/docs/guides/auth

**[R6] Supabase — Queues / pgmq**  
https://supabase.com/docs/guides/queues  
https://supabase.com/docs/guides/queues/pgmq

**[R7] SQLAlchemy 2 — PostgreSQL / psycopg**  
https://docs.sqlalchemy.org/en/20/dialects/postgresql.html

**[R8] Psycopg 3**  
https://www.psycopg.org/psycopg3/docs/

**[R9] FastAPI**  
https://fastapi.tiangolo.com/

**[R10] Alembic**  
https://alembic.sqlalchemy.org/en/latest/

**[R11] GeoAlchemy2**  
https://geoalchemy-2.readthedocs.io/en/stable/

**[R12] Supabase Realtime**  
https://supabase.com/docs/guides/realtime  
https://supabase.com/docs/guides/realtime/subscribing-to-database-changes

**[R13] Megvii BaseDetection — YOLOX oficial (Apache-2.0)**  
https://github.com/Megvii-BaseDetection/YOLOX

**[R14] ONNX Runtime — Python**  
https://onnxruntime.ai/docs/get-started/with-python.html

**[R15] ONNX Runtime — Execution Providers**  
https://onnxruntime.ai/docs/execution-providers/

**[R16] MDN — MediaDevices.getUserMedia**  
https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia

**[R17] MDN — Geolocation API**  
https://developer.mozilla.org/en-US/docs/Web/API/Geolocation_API

**[R18] CVAT — Overview/Online**  
https://docs.cvat.ai/docs/getting_started/overview/  
https://docs.cvat.ai/docs/products/online/

**[R19] CVAT Online — planos**  
https://www.cvat.ai/pricing/cvat-online

**[R20] DVC — documentação e S3-compatible storage**  
https://dvc.org/doc  
https://dvc.org/blog/june-20-community-gems/

**[R21] MLflow Tracking**  
https://mlflow.org/docs/latest/tracking

**[R22] Evidently**  
https://docs.evidentlyai.com/

**[R23] XGBoost**  
https://xgboost.readthedocs.io/

**[R24] SHAP**  
https://shap.readthedocs.io/

**[R25] MapLibre GL JS**  
https://maplibre.org/maplibre-gl-js/docs/

**[R26] MapLibre — GeoJSONSource**  
https://maplibre.org/maplibre-gl-js/docs/API/classes/GeoJSONSource/

**[R27] OpenStreetMap Foundation — Nominatim Usage Policy**  
https://operations.osmfoundation.org/policies/nominatim/

**[R28] Supabase — pgRouting**  
https://supabase.com/docs/guides/database/extensions/pgrouting

**[R29] Espressif — esp32-camera oficial**  
https://github.com/espressif/esp32-camera

**[R30] Espressif — ESP-IDF I2S**  
https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-reference/peripherals/i2s.html

**[R31] u-blox — NEO-M8 Data Sheet / TIMEPULSE**  
https://content.u-blox.com/sites/default/files/NEO-M8-FW3_DataSheet_UBX-15031086.pdf

**[R32] Supabase — Edge Functions limits**  
https://supabase.com/docs/guides/functions/limits

**[R33] Supabase — Connecting to Postgres / Supavisor**  
https://supabase.com/docs/guides/database/connecting-to-postgres

**[R34] Supabase — Standard Uploads**  
https://supabase.com/docs/guides/storage/uploads/standard-uploads

**[R35] Supabase — Storage file limits**  
https://supabase.com/docs/guides/storage/uploads/file-limits

**[R36] RDD2022 — Figshare / dataset primário**  
https://figshare.com/articles/dataset/RDD2022_-_The_multi-national_Road_Damage_Dataset_released_through_CRDDC_2022/21431547

**[R37] Supabase — API keys**  
https://supabase.com/docs/guides/getting-started/api-keys

**[R38] Supabase Queues — Quickstart/API**  
https://supabase.com/docs/guides/queues/quickstart  
https://supabase.com/docs/guides/queues/api

**[R39] RoadDamageDetector — repositório ligado ao RDD/CRDDC**  
https://github.com/sekilab/RoadDamageDetector

**[R40] Nominatim — Reverse Geocoding**  
https://nominatim.org/release-docs/latest/api/Reverse/

**[R41] Supabase — Realtime scalability guidance**  
https://supabase.com/docs/guides/realtime/subscribing-to-database-changes

**[R42] Supabase — S3 Authentication**  
https://supabase.com/docs/guides/storage/s3/authentication

**[R43] Alembic — Autogenerate**  
https://alembic.sqlalchemy.org/en/latest/autogenerate.html

**[R44] Supabase — pg_stat_statements**  
https://supabase.com/docs/guides/database/extensions/pg_stat_statements

**[R45] Espressif — ESP HTTP Client**  
https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-reference/protocols/esp_http_client.html

**[R46] PostGIS — ST_DWithin**  
https://postgis.net/docs/ST_DWithin.html

**[R47] PostGIS — ST_ClosestPoint**  
https://postgis.net/docs/ST_ClosestPoint.html

**[R48] Supabase — cliente JavaScript oficial / supabase-js**  
https://github.com/supabase/supabase-js  
https://supabase.com/docs/reference/javascript/initializing

**[R49] Cyberbotics — Webots Robot Simulator (Apache-2.0)**  
https://github.com/cyberbotics/webots

**[R50] SciPy — Signal processing / Welch**  
https://docs.scipy.org/doc/scipy/reference/signal.html  
https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.welch.html

**[R51] librosa — Feature extraction**  
https://librosa.org/doc/0.10.2/feature.html


**[R52] Supabase — Storage S3 Compatibility (revalidado)**  
https://supabase.com/docs/guides/storage/s3/compatibility

**[R53] Supabase — Queues (revalidado)**  
https://supabase.com/docs/guides/queues

**[R54] ONNX Runtime — Execution Providers (revalidado)**  
https://onnxruntime.ai/docs/execution-providers/

**[R55] Espressif — ESP HTTP Client (revalidado)**  
https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-reference/protocols/esp_http_client.html

**[R56] MapLibre — GeoJSONSource (revalidado)**  
https://maplibre.org/maplibre-gl-js/docs/API/classes/GeoJSONSource/

**[R57] Cyberbotics — Webots Robot Simulator (revalidado)**  
https://github.com/cyberbotics/webots

---

# 31. Decisões que não devem ser alteradas sem evidência técnica

1. Supabase é o único backend gerenciado e o banco oficial.
2. Não utilizar Docker/Docker Compose.
3. PostGIS é o mecanismo geoespacial principal.
4. Supabase Queues + Worker Python substituem Celery/Redis.
5. YOLOX-s é o detector base da V1; troca exige benchmark/licença/requisito real.
6. YOLOX não executa dentro do Supabase.
7. Dataset raw grande não é duplicado no Supabase Free; Supabase guarda metadados/artefatos selecionados.
8. `URMIND_UNKNOWN` é estado do sistema, não classe treinada da V1.
9. Câmera define a classe; IMU/áudio são evidências complementares futuras.
10. Foto-first é o caminho atual enquanto não há Scout.
11. Coordenada original nunca é sobrescrita pelo snap na via.
12. Google Maps não é dependência principal.
13. Responsável e ação vêm de regras/catálogos verificáveis.
14. Previsão só existe com histórico suficiente e validação temporal.
15. Nenhuma LLM participa do runtime/respostas.
16. Limiares, pesos e frequências nascem de baseline/medição, não de suposição.
17. A lista de hardware da seção 0.1 não pode ser removida nem substituída silenciosamente; mudanças exigem decisão explícita da equipe.
18. O documento FECART – Entendimento do Projeto continua sendo referência funcional de origem: câmera, GPS, IMU, áudio, movimento do Scout, Gêmeo Digital 2D e exemplos de problemas devem permanecer representados.
19. `MASTER_PLAN.md` e `UrMind Planejamento Completo.docx` devem permanecer tecnicamente equivalentes; se houver divergência, o desenvolvimento para até os dois serem sincronizados.

**Fim do UrMind Planejamento Completo.**
