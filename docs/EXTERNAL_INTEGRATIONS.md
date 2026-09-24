# UrMind — integrações externas aprovadas

Atualização DEV 24/09/2026: head `0021_history_snapshot_retention`, arquivo de histórico
forward-only protegido em AuditLog (sem acesso público), quota pública
compartilhada no PostgreSQL; 18 integrações passaram na execução final. Uma
execução anterior apresentou timeout/502 de Storage; não considerar o serviço
infalível. Remoção compensatória usa DELETE no bucket com lista exata de objetos,
como o SDK oficial, e aceita repetição idempotente. Não se remove Storage por SQL.

O registro preserva os 14 itens originais e inclui seis dependências já usadas
no runtime: Supabase, Nominatim, Open-Meteo, GeoSampa, BrasilAPI e ViaCEP.
`check` é offline e
não baixa dados; `live-check` é opt-in e usa requisições pequenas. Downloads
bulk só ocorrem por comandos explícitos.

As seis entradas adicionais documentam timeout, retry, cache, privacidade e
aplicabilidade a partir da implementação existente. Os TTLs vêm dos próprios
providers; não existe uma segunda configuração operacional. Supabase possui
políticas distintas por subsistema, por isso timeout/TTL únicos permanecem
ausentes. URL configurada não comprova disponibilidade, credenciais válidas ou
acesso ao banco. Saúde, último sucesso e última falha ficam `UNKNOWN` sem
evidência observada. O `live-check` atual não sonda essas seis entradas e as
identifica explicitamente como não verificadas.

| Serviço | Tipo | Função | Runtime? | Auth? | ENV | Status | Fase |
|---|---|---|---:|---:|---|---|---|
| Geofabrik | `bulk_data_source` | PBF Sudeste com MD5 e provenance | não | não | `GEOFABRIK_SUDESTE_PBF_URL`, `GEOFABRIK_SUDESTE_MD5_URL` | implementado; download explícito | V1 geoespacial |
| Overpass | `runtime_api` | POIs e pequenos recortes OSM | sim, degradável | não | `OVERPASS_API_URL` | implementado | V1 contexto |
| OpenFreeMap | `map_provider` | style principal do `UrbanMap` MapLibre | sim | não | `VITE_MAP_STYLE_URL` | implementado | V1 frontend |
| CARTO | `map_provider` | fallback opcional do mapa | opcional | sim, key pública | `VITE_CARTO_BASEMAPS_API_KEY` | `FRONTEND_CONFIG_UNKNOWN` no diagnóstico backend | V1 opcional |
| IBGE SIDRA | `runtime_api` | população municipal com código territorial explícito | sim, degradável | não | `IBGE_SIDRA_BASE_URL`, `IBGE_SIDRA_MUNICIPALITY_CODE` | conectado ao EventContext; exige território | V1 contexto |
| IBGE CNEFE | `bulk_data_source` | CSV por UF ou município | não | não | `IBGE_CNEFE_2022_BASE_URL` | preparado | offline |
| RDD2022 | `dataset` | treino das classes D00/D10/D20/D40 | treino | não | — | `DONE` | MODEL V1 |
| UNIVALI | `dataset` | teste de domínio brasileiro, nunca treino atual | avaliação | não | — | `PARTIAL`; fonte e split prontos, semântica ainda bloqueia métrica final | avaliação |
| timm | `ml_library` | backbones futuros opcionais | não | não | — | `PREPARED_FOR_FUTURE` | futura |
| YOLOX | `model_tool` | detector oficial YOLOX-s | sim | não | — | `DONE` | MODEL V1 |
| SAM2 | `model_tool` | máscaras e tooling de dataset | não | não | — | `PREPARED_FOR_V2` | V2/WSL |
| Hugging Face | `data_model_platform` | download público versionado | aquisição | só privado/upload | `HF_TOKEN` | público implementado | dados |
| Kaggle | `training_platform` | executar o trainer oficial fora da máquina | não | sim | `KAGGLE_API_TOKEN` | preparado / `KAGGLE_AUTH_REQUIRED` | futura |
| Webots | `simulator` | futura fonte de câmera do Gateway existente | não | não | — | `NOT_INSTALLED_PREPARED` | trilha Scout |

## Operação

Execute a partir de `backend/`:

```powershell
python -m app.services.external_sources check
python -m app.services.external_sources live-check
python -m app.services.external_sources download-geofabrik --destination ..\datasets\downloads\geofabrik
python -m app.services.external_sources download-cnefe --destination ..\datasets\downloads\cnefe --uf SP --municipality-code 3550308
```

O downloader Geofabrik usa streaming, arquivo `.part`, MD5 oficial, rename
atômico e manifesto de provenance. O CNEFE exige UF e/ou município, registra a
seleção e SHA-256 local; o IBGE não publica checksum ao lado desses ZIPs.
`osmium` e `osm2pgsql` são ferramentas de processamento posteriores e opcionais:
ausência delas é `TOOL_NOT_INSTALLED`, nunca falha do downloader.

Para ativar o contexto SIDRA, copie para o `.env` real somente o código IBGE
oficial de sete dígitos do município coberto pelo deployment:

```dotenv
IBGE_SIDRA_MUNICIPALITY_CODE=3550308
```

O valor acima é apenas o exemplo oficial de São Paulo. Não o utilize para outro
município. Sem essa configuração, o Worker persiste SIDRA como
`context_unavailable` e não faz inferência territorial silenciosa. Com o código,
o Worker consulta a estimativa populacional municipal mais recente da tabela
SIDRA 6579, variável 9324, e registra tabela, variável, período, território,
`retrieved_at` e `correlation_id` na provenance.

## Plataformas e ferramentas futuras

- Hugging Face público reutiliza `scripts/datasets/acquire_registered.py` com
  revisão imutável e `token=False`. Operação privada chama o guard e, sem token,
  retorna `HF_AUTH_REQUIRED`.
- Kaggle deve executar `python -m app.ml.training` com o mesmo config,
  `DatasetVersion`, split, taxonomia e formato de checkpoint. O retorno segue
  avaliação local, ONNX e registry; nunca é promovido automaticamente. Sem token,
  retorna `KAGGLE_AUTH_REQUIRED`.
- `timm` consta apenas no extra opcional `future-vision`; não substitui YOLOX.
- SAM2 fica fora da `.venv` atual e deve usar ambiente WSL/Ubuntu separado. Nenhum
  checkpoint é baixado nesta fase.
- Webots implementará `ScoutCameraSource` de `app.gateway`; não cria backend,
  fila ou banco. Todo envelope futuro precisa carregar `provenance=simulation` e
  não pode ser enviado como `CaptureSource.SCOUT` até existir contrato/migration
  explícitos para simulação.

## Segurança e cache

O cliente HTTP comum injeta timeout, retry finito com backoff exponencial,
`User-Agent`, `X-Correlation-ID` e logging estruturado. Query params chamados
`key`, `token`, `api_key` e `access_token` são redigidos em logs. Overpass usa
cache por coordenada; SIDRA usa cache longo por tabela/variável/período/território.
Credenciais opcionais nunca bloqueiam o startup e nunca usam prefixo `VITE_`, com
exceção da key CARTO deliberadamente pública e restrita por origem.
