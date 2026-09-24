# Feira: uma origem HTTPS, API e Worker canônicos

Atualizado em 24/09/2026. Somente Urmind DEV (`impm…ggy`). Sem detector urbano
autorizado. Relato recebido não significa problema detectado. CLIP/YuNet continuam
UNCALIBRATED até aprovação da calibração consentida; cenas/rostos não verificados
seguem NEEDS_REVIEW. Publicação exige revisão e atestado humano de privacidade.

## Iniciar

Na raiz, PowerShell:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/deploy/start_fair.ps1 -DryRun
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/deploy/start_fair.ps1
```

O script lê, mas não altera, os dois `.env`. Confirma DEV sem mostrar valores,
compila o frontend com API relativa `/api/v1`, define SERVE_FRONTEND_DIR somente
no processo, inicia API em loopback e Worker, verifica health/readiness e abre
quick tunnel. Os logs e `fair_qr.png` ficam em `%LOCALAPPDATA%/UrMind/fair/<run>`.
Não versionar esses arquivos. Não compartilhar logs sem revisão de privacidade.

Dependências: Node/npm, ambiente backend, `qrcode[pil]==8.2` (extra `fair`),
cloudflared oficial. Nesta máquina foram instalados qrcode8.2 e cloudflared2026.9.3
fora do repositório. SHA256 do executável Windows amd64:
`f096265ec2fcbe9bb6e2d64268db167ced3fcbb83d894bdb9e2fcdb26f2ea7e2`.
Fonte: https://github.com/cloudflare/cloudflared/releases/tag/2026.9.3.
O script verifica esse hash para o binário local; também aceita instalação oficial no PATH.

## Ação manual no Supabase

Copiar a URL HTTPS impressa para **Auth → URL Configuration → Redirect URLs**, no
DEV. O agente/script não modifica essa configuração. Quick tunnel muda a URL a
cada execução: atualizar Redirect URLs e imprimir o QR novo; QR antigo não é estável.
Túnel nomeado já configurado pode ser usado com `-TunnelName NOME -PublicUrl https://DOMINIO`.
Não fornecer tokens/credenciais como argumentos ou em mensagens.

## Supervisão e encerramento

Manter o terminal aberto. Se API, Worker ou túnel terminarem, o supervisor encerra
os demais processos que criou e conserva os logs. Não mata serviços preexistentes.
Ctrl+C encerra a sessão. Corrigir a causa e executar novamente é o procedimento de
restart/rollback operacional; não há serviço permanente instalado nem reboot automático.
`-Port 8765 -RunSeconds 40` permite uma sessão curta de verificação com encerramento.
Não esvazia Storage, fila ou banco. Não inicia treinamento nem restaura modelos.

Prova desta rodada: HTTP200 na página HTTPS com CSP; `/api/v1/ready` retornou
`ready/connected`; `/api/v1/health` retornou `ok/connected`, PostGIS3.3; QRPNG1144bytes.
Sessão temporária encerrada e porta8765 liberada. Isso não prova câmera/GPS físicos.

## Celular e revisor

1. Abrir URL nova/QR; conferir contexto HTTPS confiável e consentimento antes do envio.
2. Enviar foto própria nítida, descrição e GPS/EXIF ou ponto manual confirmado.
3. Conferir protocolo, `#/meus-relatos`, timeline e refresh em `#/processando/:capture_id`.
4. Sem modelo, marcador continua como relato/análise indisponível, sem classe inventada.
5. Foto escura/pequena/desfocada deve ser rejeitada antes de Storage; duplicata deve
   ser recusada. Rascunho deve permanecer. Interior/selfie não têm rejeição garantida
   enquanto C2/C4 estiverem sem calibração: exigir revisão, não afirmar proteção ativa.
6. Relato próximo: confirmar anexação apenas se realmente for o mesmo problema.
7. Revisor: `#/login` → área interna → fila → relato. Confirmar/corrigir como rótulo
   humano. Respeitar consenso/adjudicação e publicar usando a revisão atual do próprio
   responsável pelo atestado visual. Conferir derivada sem EXIF no mapa público.
8. Visitante B não vê relato privado de A; logout cancela carregamentos. Testar permissões
   negadas, rede lenta e offline. Sem internet, não simular análise; usar demonstração
   revisada identificada, se disponível, e retomar envio ao reconectar.

## Integrações e OSM

```powershell
cd backend
.venv/Scripts/python.exe -m app.services.external_sources live-check --persist --json
# Somente dry-run: nenhuma escrita de RoadSegment
.venv/Scripts/python.exe -m app.services.osm_import --center=-23.5560,-46.6370 --radius-km 3 --label fecap-demo
```

**Não executar `--commit` antes da confirmação humana do centro/raio.** O dry-run
consulta vias reais e mostra contagem/bbox, sem abrir transação de importação.
Para SIDRA São Paulo: `IBGE_SIDRA_MUNICIPALITY_CODE=3550308`; já observado configurado
nesta rodada. Nenhuma edição automática de `.env`.

## Merge

Não foi feito merge nem push. Após revisão e fechamento dos itens OPEN, o usuário
pode integrar `feature/areas-mapa-porteiro` à branch de destino escolhida. Não usar
reset/clean nem incluir os arquivos científicos locais excluídos do checkpoint.
