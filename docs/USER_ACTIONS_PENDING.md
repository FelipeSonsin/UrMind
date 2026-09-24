# Ações humanas pendentes — 24/09/2026

1. **OPEN — pasta de calibração externa ao repositório**, com manifest.csv:
   `file,label,subtype,author,license_or_consent,notes`.
   Mínimos implementados: 20 street_positive sem rostos (`subtype=no_faces`),
   20 scene_negative (indoor/selfie/document/screenshot/food/pet/sky/other),
   5 face_large e 5 face_small, todos distintos e consentidos/licenciados.
   Não usar datasets científicos. Informar apenas o caminho, não enviar nomes
   pessoais no chat. O manifesto pessoal permanece fora do Git.
   Validação sem inferência, a partir da raiz:
   `backend/.venv/Scripts/python.exe scripts/photo_gate/calibrate.py --dir "CAMINHO_EXTERNO"`.
   `--run --activate` executa inferência autorizada somente nesse corpus, solicita
   inspeção humana das derivadas temporárias, e ativa apenas se todos os gates passarem.
   Não é treinamento; calibração do porteiro não aprova detector urbano.
2. **OPEN — confirmar centro OSM/raio:** proposta −23.5560, −46.6370, raio3km,
   FECAP/Liberdade. Nenhuma importação persistente executada nesta rodada.
3. **OPEN — execução física na feira:** iniciar o runbook, cadastrar manualmente a
   nova URL em Redirect URLs do DEV, testar câmera/GPS/permissões/rede em celular.
   Há um Worker iniciado em terminal próprio nesta máquina; encerrá-lo nesse terminal
   antes de usar `start_fair.ps1`, que recusa duplicar o processo e não o encerra.
   Não compartilhar senha/token. Quick tunnel temporário não fornece QR permanente.
4. **RESOLVED por observação — SIDRA:** 3550308 já estava configurado e o provedor
   respondeu; não precisa alterar essa linha agora.

Pendências de implementação NÃO são transferidas ao usuário: filtros e ordenação
globais da fila interna ainda constam OPEN no relatório. Contagem e exportação GT
agregadas foram implementadas e testadas.
O contexto de Capture sem Event foi conectado; a presença de RoadSegment real depende
do recorte OSM confirmado, sem coordenadas inventadas.
