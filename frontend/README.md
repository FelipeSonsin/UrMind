# Frontend UrMind

Base React + TypeScript + Vite, conforme `docs/Planinng/MASTER_PLAN.md` (§3, §6, §16, §20 e §28). Esta entrega antecipa a preparação do frontend a pedido do projeto enquanto as integrações ainda não estão disponíveis; não declara concluído o fluxo foto → inferência → mapa.

## Executar no Windows

Requisito: Node.js 22.12+ (validado neste ambiente com Node 24).

```powershell
cd frontend
npm.cmd ci
npm.cmd run dev
```

Abra http://127.0.0.1:5173. O uso de `npm.cmd` evita depender da política de execução de scripts `.ps1` do PowerShell.

O aplicativo abre mesmo sem FastAPI ou Supabase. Para consultar a API existente, execute o backend em `127.0.0.1:8000`; o Vite encaminha `/api` para ele. Não é necessário alterar CORS para esse fluxo local.

Opcionalmente copie `.env.example` para `.env.local`. `API_PROXY_TARGET` configura o servidor do proxy; `VITE_API_BASE_URL` configura a base pública da API. Em produção, o proxy Vite não existe: configure `/api` na mesma origem no servidor de hospedagem, ou forneça uma URL pública com CORS autorizado no backend. Não coloque senhas, secret keys ou credenciais S3 em variáveis `VITE_*`.

## O que funciona

- Navegação responsiva: visão geral, registro, rascunhos, ocorrências, mapa, análises e integrações.
- Câmera via `getUserMedia`, importação JPEG/PNG/WebP, verificação do conteúdo decodificável e limite local de 10 MB.
- Localização manual validada; posição, precisão, horário, direção e velocidade do dispositivo quando disponíveis para foto tirada no aplicativo.
- Rascunhos reais em IndexedDB: foto, ID estável, origem, observação e metadados; edição e exclusão individual. Persistir um rascunho não registra Capture no banco.
- Consulta de saúde e até 500 ocorrências recentes pelas rotas HTTP existentes, com validação das respostas, filtros locais, detalhes, timeout e estados de erro. As contagens se referem apenas ao conjunto consultado.
- MapLibre com pontos GeoJSON nas coordenadas originais de ocorrências reais. A coordenada ajustada à via permanece separada nos detalhes.
- PWA: cache somente do aplicativo, com aviso de atualização; rascunhos persistidos separadamente no IndexedDB. O frontend nunca coloca respostas da API ou mapas externos no cache offline.

Sem uma API funcional, métricas são “—”, e não zero. Nenhum exemplo de ocorrência, sensor ou previsão é inserido no aplicativo.

## Limites explícitos desta fase

- Sem Auth, Storage, Realtime, upload, fila, inferência e sincronização. Rascunhos não são enviados automaticamente ao reconectar. A integração futura deverá confirmar upload e persistência antes de declarar sincronização, mantendo os IDs estáveis.
- Sem leitura EXIF no navegador. O serviço Python já existe, mas não tem rota de upload conectada. Fotos importadas preservam o arquivo original e `captured_at = null` até obter a data real; o horário de importação não é usado como horário da foto. A localização dessas fotos é manual. Seleção pelo mapa ainda pendente.
- Sem classificações locais, cálculo duplicado do motor de risco ou geração de texto. Descrições Jinja2, prioridade, responsável e ação aguardam contratos HTTP. As páginas informam essa dependência.
- Previsões aguardam histórico e validação temporal (§24); revisões aguardam Auth, rota de escrita e auditoria. Scout e sensores são fase futura.
- `VITE_MAP_STYLE_URL` é opcional. Sem ela, MapLibre exibe somente pontos reais sobre fundo neutro, sem buscar tiles externos. Para ruas e contexto, configure um estilo com dados OSM e atribuições/licenciamento apropriados. Não há geocodificação ou coordenada atribuída automaticamente ao usuário.
- IndexedDB é armazenamento local temporário; não substitui o banco oficial. Navegação privada, quota ou limpeza do navegador podem impedir persistência/remover rascunhos. Não há backup remoto nesta fase.
- Câmera e localização exigem permissão e contexto seguro: localhost no computador ou HTTPS no celular. HTTP pelo IP da rede local não basta. Permissões reais em celular e instalação em diferentes plataformas ainda exigem validação no dispositivo.

## Estrutura

```text
frontend/
  public/                  ícone e assets estáticos
  src/
    components/            câmera, foto e mapa
    domain/                contratos e validação alinhados ao backend
    pages/                 registro e consulta de ocorrências
    services/              cliente HTTP e rascunhos IndexedDB
    App.tsx                navegação, visão geral e estado das integrações
    main.tsx               inicialização e tratamento de erro de renderização
    styles.css             layout responsivo e estados acessíveis
  tests/                   testes de navegador; fixtures somente aqui
  vite.config.ts           proxy e PWA
```

## Verificação

```powershell
npm.cmd test
npm.cmd run build
npm.cmd run test:e2e
npm.cmd run format:check
npm.cmd run preview
```

Os testes de navegador usam Microsoft Edge instalado, nos tamanhos desktop e mobile. Em outro ambiente, adapte `channel` no `playwright.config.ts` ao navegador disponível. O build deve existir antes do teste de navegador.

Validação desta reconstrução: build de produção concluído, 8 testes unitários e 10 testes de navegador aprovados, incluindo persistência/edição de rascunhos, rejeição de imagem inválida, filtros, mapa e recarga offline. Capturas de tela desktop e mobile foram revisadas. O Vite informa um aviso de tamanho do módulo MapLibre (~1 MB sem compressão); ele é carregado separadamente da interface principal e também incluído no cache do aplicativo para uso offline.

Use `npm.cmd run format` para padronizar a formatação dos arquivos. Os testes de câmera real, GPS real e integrações externas permanecem dependentes dos dispositivos e serviços correspondentes.

Para experimentar offline: execute build e preview, abra http://127.0.0.1:4173, aguarde a instalação do service worker, recarregue para ativar o controle da página e desative a rede no navegador. A navegação e os rascunhos continuam locais; dados remotos e tiles não ficam disponíveis offline.

Referências técnicas consultadas: [Vite](https://vite.dev/guide/), [Vite PWA](https://vite-pwa-org.netlify.app/guide/service-worker-strategies-and-behaviors) e [MapLibre](https://maplibre.org/maplibre-gl-js/docs/API/classes/Map/). O planejamento oficial permanece como fonte das decisões do projeto.
