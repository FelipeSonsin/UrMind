# UrMind mobile: GPS, mapa Brasil, integrações e prontidão pré-treino

Data: 2026-09-24. Escopo: frontend/backend canônicos na branch
`feature/areas-mapa-porteiro` e, quando indispensável, Supabase **DEV**. Este
documento registra o desenho aprovado em conversa; não autoriza treinamento,
Frozen Test, promoção de modelo ou alteração de `.env`.

## Objetivo e estado observado

O visitante deve registrar foto de celular com localização declarada e consultar
o relato em um mapa operacional limitado ao Brasil. No código atual, a câmera
já chama `navigator.geolocation.getCurrentPosition` após fotografar; a galeria
usa GPS EXIF quando presente e, sem ele, pede marcação manual. O backend relê
EXIF e preserva origem, precisão e divergência. `UrbanMap` ainda inicia em
`[0,0]`, permite navegar pelo globo e ajusta o enquadramento a qualquer ponto.
A navegação pública mistura ações do visitante com abas técnicas. O `live-check`
de 24/09 observou respostas 200 em Supabase, Open-Meteo, GeoSampa, BrasilAPI,
ViaCEP e OpenFreeMap; Overpass respondeu 504 após retries e Nominatim não foi
sondado porque o lease compartilhado estava ocupado. Isso não demonstra uma
credencial errada.

## Decisões de produto

1. **GPS da câmera:** manter solicitação automática no momento da captura.
   Mostrar claramente permissão, obtenção, `accuracy_m`, horário e eventual
   falha. Falha não cria coordenada fictícia: oferecer mapa manual.
2. **Foto da galeria:** GPS EXIF validado no servidor continua prioritário.
   Sem EXIF, oferecer localização manual ou a posição atual do aparelho
   **somente após confirmação explícita de que corresponde ao local da foto**.
   Guardar `location_source`, horário/precisão da posição e a natureza declarada
   dessa confirmação. Se EXIF/GPS parecerem errados ou fora do escopo, permitir
   correção manual sem sobrescrever a provenance original.
3. **Brasil operacional:** iniciar e limitar MapLibre ao enquadramento do
   Brasil, sem cópias repetidas do mundo. Novos relatos com ponto confirmado
   fora do Brasil são recusados antes do armazenamento definitivo, mantendo o
   rascunho local para correção. O backend valida com limite territorial
   versionado e identificado por provenance, não apenas com o viewport nem
   com retângulo que aceite países vizinhos. Fotos sem ponto permanecem no
   fluxo `location_required` já existente, sem marcador. Borda/ilhas e precisão
   ruim recebem estado revisável, não uma coordenada substituta.
4. **Dados existentes e científicos:** nenhuma imagem, label, split, holdout,
   DatasetVersion, Ground Truth, Event histórico ou lineage de treinamento é
   apagado, movido, reclassificado ou excluído por essa política geográfica.
   A restrição atua no ingresso e nos mapas operacionais novos; pipelines
   científicos continuam com seus próprios contratos de curadoria, países e
   splits. Registros antigos fora do Brasil permanecem consultáveis por vias
   autorizadas, mas não entram no mapa operacional brasileiro.
5. **Navegação:** usar quatro ações primárias públicas — Início, Registrar,
   Meus relatos e Mapa. “Sobre/privacidade” permanece acessível fora da barra
   principal. “Ao vivo”, “Transparência”, “Sistema” e páginas técnicas saem da
   navegação pública principal, sem apagar rotas, histórico ou controles
   internos. Revisão/admin continuam exclusivos de seus papéis.
6. **Integrações:** manter registry e clientes existentes. Medir saúde real,
   latência e modo de falha; corrigir somente falha reproduzida. Overpass 504
   deve degradar o item contextual sem impedir Capture/relato; reduzir chamadas
   repetidas usando cache/PostGIS existente, sem alternar indiscriminadamente
   para instâncias públicas. Nominatim respeita o lease global e distingue
   “não sondado por coordenação” de “provedor indisponível”. OpenFreeMap segue
   principal com fallback CARTO somente quando configurado. Não há exigência
   comprovada de nova API key neste momento.

## Componentes e fluxo

Estender `CapturePage`, `UrbanMap`, cliente de upload, validação de localização
no serviço canônico, consultas de marcadores e registry/health existentes.
Não criar outro mapa, upload, fila ou backend. Reutilizar `Capture` e campos de
provenance/quality já existentes; criar migration apenas se um contrato
persistente indispensável não couber nesses campos. O ponto original jamais é
substituído pelo snap em RoadSegment. Publicação e imagem sanitizada conservam
os gates atuais; relato sem detector não ganha classe, risco ou prioridade
inventados.

Fluxo: foto → resolução GPS dispositivo/EXIF/manual → validação de imagem e
território → Storage privado → Capture/Queue → Worker disponível ou
`model_not_available` → marcador do titular → revisão/publicação → mapa público.
Quando a localização é desconhecida, preservar o registro sem ponto e solicitar
correção. Quando está fora do Brasil, manter o rascunho e explicar como corrigir
antes do envio definitivo. Uma API contextual indisponível gera
`context_unavailable`, não falha do relato.

## Verificação

- Testes de câmera com permissão aceita/negada, GPS inválido, precisão e
  timestamp; galeria com/sem EXIF, confirmação da posição atual e seleção
  manual; nenhuma coordenada default ou inferida.
- Testes territoriais com pontos no Brasil, fora, na borda, ilhas e precisão
  incerta; backend e interface concordam; dados científicos/históricos ficam
  intactos. Mapa inicia/enquadra Brasil e não navega para outro continente.
- Testes de navegação 320 px, acesso por papel e rotas antigas sem links
  principais quebrados.
- `live-check` e testes simulados de timeout/502 para cada provedor usado em
  runtime; uma falha de Overpass/Nominatim não bloqueia o relato. Comparar
  `health` com o comportamento do Worker e registrar resultados observados.
- Regressão backend, integração DEV segura, Vitest, Playwright, Ruff, mypy,
  TypeScript, Prettier, build e `git diff --check`. Skips permanecem skips.
- Pré-treino: somente checagens de schemas, permissões, lineage, splits,
  disponibilidade e elegibilidade do Ground Truth. Reportar separadamente
  bloqueios de revisão humana/dados para YOLOX e XGBoost. Não executar treino,
  avaliação final, calibração final, SHAP final ou Frozen Test.

## Limites e implantação

O túnel `trycloudflare.com` atual é temporário; uma URL/QR estáveis são uma
trilha distinta que depende de domínio/hospedagem e não é produzida por este
recorte. Mudanças de backend requerem reinício do preview, portanto a URL de
teste poderá mudar. Não alterar Auth/.env automaticamente; qualquer ajuste
manual necessário deve ser solicitado com nome da variável, nunca seu valor
secreto. A fonte territorial oficial e sua licença/provenance deverão ser
registradas antes de ativar o bloqueio geográfico; sem esse artefato, a regra
fica fail-closed para a publicação de novos pontos fora do escopo, não é
substituída por um retângulo impreciso.
