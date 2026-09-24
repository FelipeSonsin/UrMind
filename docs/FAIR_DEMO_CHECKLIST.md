# UrMind — Checklist da demonstração na feira

Revisado em 24/09/2026. Ambiente: **Urmind DEV** (`impm…ggy`). Modelo visual:
`2527af02-…` agora **ARCHIVED**, após exclusão autorizada dos artefatos antigos.
O resultado científico permanece **rejeitado no Frozen Test**. Não há inferência
visual disponível; os passos abaixo dependentes de ONNX estão bloqueados até
novo modelo autorizado. Não apresentar resultados salvos como inferência nova.

Legenda da coluna "Evidência":

- `AUTO` — coberto por teste automatizado (pytest/Vitest/Playwright com API simulada).
- `DEV` — verificado contra o Supabase DEV real.
- `MANUAL` — ainda precisa ser feito com um aparelho real, na rede da feira.

Nada marcado como `AUTO` substitui o teste `MANUAL` correspondente: o Playwright
usa API simulada e navegador desktop/emulado.

Atualização mobile: viewport 320px incluído na regressão; detalhe sem overflow
horizontal. Rotas de captura/login/revisão carregam sob demanda. Precache PWA
continua ~2,24 MiB: validar instalação inicial com rede lenta na feira; redução
do entrypoint não significa redução equivalente de todo o download offline.

## 1. Antes de abrir o estande (bloqueadores)

| Item | Como verificar | Evidência |
|---|---|---|
| URL HTTPS estável | abrir no celular pela rede da feira; cadeado válido, sem aviso | MANUAL |
| QR code | gerar **só** depois da URL HTTPS definitiva; apontar para `/#/` | MANUAL |
| API respondendo | `GET /api/v1/health` → `status: ok`, `database: connected` | MANUAL |
| API pronta | `GET /api/v1/ready` → 200 `ready` (503 = não abrir o estande) | AUTO (503 sem banco) |
| Worker rodando | `python -m app.worker --stats` mostra a fila e nenhum erro | MANUAL |
| Modo de visão | `VISION_EXECUTION_MODE` e `SHADOW_MODEL_VERSION_ID` definidos no `.env` do DEV | MANUAL |
| Anonymous Sign-In | ligado no painel Auth do DEV | DEV: duas sessões distintas e JWT/JWKS validados; identidades descartáveis removidas |
| Redirect URLs de Auth | a URL HTTPS da feira está em Auth → URL Configuration | MANUAL |
| CORS | se PWA e API estiverem em origens diferentes, `CORS_ALLOWED_ORIGINS` com a origem HTTPS | MANUAL |
| Recorte OSM | importar só a área do estande depois de ter coordenada real (`app.services.osm_import --bbox … --commit`) | MANUAL (DEV tem 0 RoadSegments) |

## 2. Fluxo principal no celular

| Cenário | Esperado | Evidência |
|---|---|---|
| Android Chrome — câmera | abre a câmera traseira e captura | MANUAL |
| iPhone Safari — câmera | idem, se houver iPhone disponível | MANUAL |
| Escolher imagem da galeria | aceita JPEG/PNG/WebP até 10 MB | AUTO |
| GPS permitido | coordenada + accuracy exibidas antes do envio | MANUAL |
| GPS negado | pede localização manual no mapa; não inventa ponto | MANUAL (sem teste de permissão negada) |
| Câmera negada | oferece a galeria; nada quebra | MANUAL (sem teste de permissão negada) |
| Localização manual | ponto marcado vira `location_source=manual` | AUTO (backend) |
| Envio | redireciona para `#/processando/<capture_id>` | AUTO |
| Recarregar durante o processamento | o estado volta pela API, não pelo estado do React | AUTO |
| Rede lenta / offline | rascunho fica local; nada é declarado enviado sem resposta do servidor | AUTO (offline do app instalado) / MANUAL (rede lenta) |
| Falha de upload (Storage) | mensagem de erro; a foto não some do rascunho | AUTO (backend 502) / MANUAL (tela) |

## 3. Estados de processamento (nenhum é inventado)

Sequência real do Worker: `queued → processing_detection → building_event →
enriching_context → building_features → completed` (ou `needs_review`).

| Estado | O que o público vê | Evidência |
|---|---|---|
| `detection_completed` | "Detecção concluída; análise ainda em andamento" — **não** é concluído | AUTO |
| `no_supported_detection` | nenhuma das classes suportadas identificada; captura preservada | AUTO; alias persistido antigo `no_detection` continua legível |
| `model_not_available` | "não há modelo experimental autorizado" — sem resultado falso | AUTO |
| resultado shadow | selo **ANÁLISE EXPERIMENTAL** | AUTO |
| `needs_review` | "precisa de revisão humana" | AUTO |
| `completed` | só com Event + snapshot de features + RiskAssessment + DecisionTrace | AUTO |

## 4. Demonstração e mapa

| Item | Esperado | Evidência |
|---|---|---|
| `#/demo` | só ocorrências **confirmadas por revisão humana**, cada uma com o selo **EXEMPLO REVISADO** | AUTO |
| `#/demo` sem revisões | "Nenhum exemplo revisado disponível ainda" (estado atual do DEV) | AUTO |
| Exemplo × foto do visitante | a página diz que os exemplos **não** são o resultado da foto enviada | AUTO |
| Classes novas (árvore caída, bueiro…) | aparecem como **Em desenvolvimento**, nunca como reconhecidas | AUTO |
| Mapa | marcadores com atribuição OpenStreetMap | AUTO |

## 5. Segurança durante a feira

| Item | Esperado | Evidência |
|---|---|---|
| Visitante anônimo | envia foto, vê **só** a própria captura | AUTO |
| Isolamento entre visitantes | captura de outro usuário responde 404, sem revelar que existe | AUTO |
| Limite por visitante | 4 fotos/hora → 429 | AUTO |
| Limite global | 60 fotos/hora de visitantes → 429 | AUTO |
| Revisão / eventos internos | exigem `app_metadata.urmind_role` reviewer/admin | AUTO + DEV (policies) |
| Storage | bucket `captures` privado, 10 MB, só JPEG/PNG/WebP | DEV |
| `anon` no banco | nenhum grant; RLS em todas as tabelas | DEV |
| Retry da fila | falha transitória refaz até 3 tentativas; depois `failed` auditável | AUTO |

## 6. Dados pessoais e retenção (comportamento técnico atual)

Isto descreve o que o código faz hoje. Não é parecer jurídico.

| Dado | Onde fica | Comportamento atual |
|---|---|---|
| Imagem original | Storage privado `captures/<usuário>/<AAAA>/<MM>/<sha256>-<id>.<ext>` | guardada **com o EXIF original** (inclui GPS/aparelho, se existirem); sem exclusão automática |
| Acesso ao original | URL assinada de 600 s | somente para revisor; nunca entregue pela API pública |
| Cópia pública | JPEG sem EXIF/ICC em path gerado pelo servidor no bucket privado | ligada à publicação de cada Event; proxy verifica hash, revisão e autorização atual, sem expor path |
| Publicação | `POST /api/v1/events/{id}/publication` | revisor confirma privacidade visual e revisão atual; Event confirmado; AuditLog e compensação Storage/DB |
| Nome do arquivo do cliente | — | nunca usado no path |
| Capture | `public.captures` | coordenada original, accuracy, origem da localização e `uploaded_by`; sem exclusão automática |
| Detection / Event | `public.detections`, `public.events` | sem exclusão automática; coordenada original nunca é sobrescrita |
| RiskAssessment com snapshot | `public.risk_assessments` | **não pode ser apagado** (trigger da 0019) |
| Review / AuditLog | `public.reviews`, `public.audit_log` | revisor fica no AuditLog, não no log da aplicação |
| Logs da aplicação | stdout JSON | sem token, senha, imagem binária ou id de revisor |

A remoção de metadados da cópia está implementada e testada; não remove rostos
ou placas dos pixels. O revisor só deve publicar imagem cujo conteúdo visual
possa ser publicado. Se precisar de desidentificação visual adicional, não publique.
Retirada de publicação bloqueia novas leituras; não recupera cópias já baixadas.
Derivadas anteriores permanecem privadas para auditoria, sem limpeza automática.
Permanecem pendentes: política de retenção e rotina de exclusão a pedido,
incluindo compatibilidade com snapshots imutáveis. Isto não é obrigação jurídica inferida.

## 7. Implantação

- HTTPS obrigatório (câmera e geolocalização exigem contexto seguro).
- PWA e API na mesma origem (FastAPI servindo `frontend/dist`) evitam CORS.
- Cabeçalhos já enviados: `X-Content-Type-Options`, `Referrer-Policy`,
  `X-Frame-Options: DENY`, `Permissions-Policy` (câmera/GPS só da própria origem)
  e HSTS quando a requisição chega por HTTPS.
- HSTS só é enviado quando o FastAPI termina o TLS; atrás de proxy que termina TLS,
  configure HSTS no proxy.
- CSP ativa no backend que serve a PWA: scripts próprios, workers próprios/blob,
  hosts explícitos Supabase/OpenFreeMap/CARTO, sem `unsafe-eval`. MapLibre tem
  worker empacotado; marcador renderizado testado sob CSP no servidor local.
  Origem customizada de mapa requer revisão da allowlist. Docs interativos da API
  não recebem essa CSP. Hosting separado deve aplicar a mesma política.
  Validação na URL HTTPS final e no aparelho real continua pendente.
- Rollback: o frontend é estático (manter o `dist` anterior). Head atual
  `0021_history_snapshot_retention`: não retirar proteção de snapshots históricos;
  downgrade é recusado quando há snapshots. CLI `history --freeze` grava evidência
  persistente e não deve ser usada como health check. A migration anterior
  `0020_public_image_quota`: não remover sua tabela com a API atual ativa,
  pois leituras de imagem falham fechadas sem persistência de quota. Seu downgrade
  remove somente o ledger temporário, não evidências; coordenar código e schema.

## 8. Iniciar e verificar (sem copiar credenciais)

No terminal do backend canônico: `.venv/Scripts/python.exe -m app`.
No terminal separado do mesmo backend: `.venv/Scripts/python.exe -m app.worker`.
O operador configura manualmente o ambiente DEV e shadow específico; nunca use
shadow em produção. Faça `npm run build` no frontend; para servir o build pelo
backend, configure `SERVE_FRONTEND_DIR` apontando ao `frontend/dist` canônico.
Não há URL HTTPS estável nem QR definitivo comprovado nesta rodada.

Antes de começar o E2E, forneça foto própria fora dos datasets e localização
confirmada. O teste `frontend/tests/real-e2e.spec.ts` exige autorização da imagem;
não o habilite com imagem científica. O mapa público só mostra publicação
autorizada após revisão; o autor pode consultar seu resultado autenticado antes disso.

Limites de tentativas: 8 por identidade e 30 globais por minuto **por processo**,
incluindo uploads inválidos; 2 decodificadores simultâneos. Quotas persistidas
de 4/h por visitante e 60/h globais continuam no PostgreSQL. Para vários processos,
o limite de tentativas requer coordenação adicional; não afirmar proteção distribuída.
Sem internet: manter rascunho local e indicar indisponibilidade; nunca simular análise.
