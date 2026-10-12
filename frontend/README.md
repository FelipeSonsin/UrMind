# Frontend UrMind

Aplicação React, TypeScript e Vite para registrar evidências de problemas urbanos, acompanhar relatos, revisar ocorrências e consultar o mapa público.

## Estado atual

- Fotos podem vir do dispositivo, de um arquivo ou de um quadro da câmera remota do robô. O registro preserva a evidência e solicita localização quando ela falta.
- A interface apresenta a situação real do relato, sem criar detecções ou previsões fictícias.
- A revisão humana decide a classe, a localização e a publicação. O navegador **não carrega YOLOX, YOLO26 ou XGBoost** nesta versão.
- Autenticação e dados vêm da API e do Supabase; rascunhos locais usam IndexedDB.

## Plano de IA

O próximo ciclo prevê **YOLO26s (Small) treinado e avaliado no Roboflow** para candidatos visuais e **XGBoost** para análise tabular de severidade ou risco. O primeiro treino deve atender às 27 categorias prioritárias, com evidência complementar nas categorias não confirmáveis por foto. A revisão humana dos labels foi dispensada para preparar o treino; nenhum dataset completo ou modelo ativo é declarado por essa dispensa. A interface só poderá mostrar resultados automáticos quando houver dados autorizados, avaliação, integração e ativação aprovadas. O [Master Plan](../docs/Planinng/MASTER_PLAN.md) é a fonte de verdade para esse ciclo.

## Interface

- Tema claro com verde-petróleo como cor de ação e Public Sans como única família tipográfica, inclusive nos protocolos; algarismos de largura fixa só em tabelas e indicadores. As situações têm cor própria, a mesma na lista e no mapa: azul para "em análise", verde para confirmado, âmbar para "falta o local" e cinza para encerrado; o verde-petróleo fica só para ações. A gravidade aparece num selo neutro com a placa do mapa (mesma forma, cor e contorno; `src/components/signShapes.ts` é a fonte única do mapa, da legenda e do selo), para não competir com a cor das situações. Endereço sempre como "rua, bairro" (a cidade só quando falta o bairro). Protocolos e códigos levam `translate="no"`, para o tradutor do navegador não alterá-los.
- **Linguagem simples:** a tela fala como quem usa, sem termos de bastidor (backend, API, porteiro, frame, códigos de status). Frases técnicas que vêm da API (regras, fontes, limites, área piloto) ganham a versão simples em `src/content/plainLanguage.ts`; frase nova da API aparece como veio, para nunca afirmar o que a API não disse. Os "Detalhes técnicos" também são simples: o caminho da análise chega da API com chaves e códigos (`location_source`, `high`, `risk-v1`) e a tela mostra nomes e valores em português (`src/content/technical.ts`). Os botões do mapa têm dicas em português (`src/content/mapLocale.ts`) e cada página dá o próprio nome à aba do navegador.
- **Sem ruído:** nada de informação repetida na mesma tela nem de linhas "não disponível". O que é técnico (coordenadas, confiança do modelo, regras com versão, fontes, caminho da análise, fila antiga da análise automática) continua disponível em **Detalhes técnicos**, fechado por padrão.
- No computador, a navegação fica numa barra lateral fixa. No celular, vira uma barra inferior com "Registrar" no centro e "Mais" para a câmera do robô, os rascunhos e as páginas informativas. No celular, a Câmera do robô diz onde ela é usada (no computador que recebe o vídeo) e o que fazer com o aparelho; receber o vídeo nele fica recolhido.
- **Buscar página ou relato (Ctrl+K ou ⌘K):** busca páginas, relatos desta sessão e protocolos. Abre pelo botão da lateral ou pela lupa do topo no celular.
- **Caminho de um relato** (Transparência): as etapas do sistema, como o Master Plan descreve, com o que acontece e quem decide. Só a etapa que ainda não funciona leva o rótulo "Planejado". A análise automática só aparece ativa quando há modelo e a taxonomia libera classes para ele. No celular, as etapas viram uma lista vertical.
- **Checagem da foto** (Registrar relato): o resultado em uma linha; resolução, luz, nitidez e arquivo, com os limites da API (`/public/photo-policy`), ficam em "Ver medidas". O servidor repete a checagem no envio.
- **Mapa:** uma legenda só, com o que está desenhado; "a definir" é um aro cinza vazado, como o selo cinza da lista. Para marcar um ponto há um alvo fixo no centro e uma instrução só: "Arraste o mapa até o alvo ficar sobre o local". Um toque leva o mapa até o ponto tocado, as setas do teclado também movem o mapa e "minha localização" marca onde a pessoa está; movimento feito pelo próprio site (enquadrar, buscar endereço) não marca nada. "Ver os pontos em lista" dá a mesma informação em texto (com a contagem já acima do mapa, sem repetir o número); no celular, os filtros do mapa ficam em "Filtros", com a contagem à vista ao lado; onde a página já lista os relatos (Meus relatos, Mapa interno), o mapa não repete a lista.
- **Um nome para cada coisa:** "Falta o local" (situação) e "Informar o local" (ação), "Atualizar", "Ver no mapa", "Ver ocorrência", "Solução indicada", "Todos os tipos", "Confirmar" e "Recusar", iguais em todas as telas; para a equipe, todo relato ainda sem decisão é "Aguardando revisão" e toda ocorrência, "Aguardando decisão", com a mesma cor azul na lista, no detalhe e no mapa. Os motivos de recusa da foto vêm de uma lista só (`src/content/photoReasons.ts`). Datas de filtro são digitadas como dd/mm/aaaa no mesmo campo na fila e no mapa (`src/components/DateField.tsx`): o campo nativo seguiria o idioma do navegador. Ações são sempre verde-petróleo; laranja é só situação ou alerta.
- **Ações que não se desfazem** (excluir rascunho, recusar relato ou ocorrência, marcar como repetido, retirar do mapa público, desvincular foto) perguntam no próprio lugar, com o efeito escrito, antes de agir: `src/components/ConfirmButton.tsx`. Na pergunta, a confirmação é o botão mais forte (vermelho cheio) e, na revisão, "Confirmar" sai da barra até a pergunta fechar; Esc cancela. Nada usa a janela de confirmação do navegador para isso.
- **Ocorrência pública:** o cidadão vê a gravidade, a solução indicada, o local e a foto (no celular, nessa ordem: o que será feito e onde vêm antes da foto). Sem foto publicada, uma linha diz por quê, no lugar de um quadro vazio; o endereço aparece só no cartão Local. A prioridade, que junta fatores do modelo, fica em "Detalhes técnicos", em frases. Nada dá a entender que a prefeitura foi avisada: o cartão diz "Quem costuma resolver" e a página da ocorrência e "Sobre" dizem que o UrMind não avisa a prefeitura e que o pedido de conserto é feito no SP156. Aberta a partir de "Meus relatos" (`#/resultado/<id>`), a ocorrência volta para "Meus relatos" e diz que veio do relato da pessoa. A promessa de privacidade é a mesma em todo o site: a foto só é publicada depois que a equipe confere que não há rostos, placas de veículos ou dados pessoais à mostra.
- **Seu relato:** a foto enviada (só para quem enviou) e o endereço no topo; sem local, o mapa vem logo depois da situação, dentro do mesmo cartão, com a busca de endereço ou CEP (a mesma do formulário, `src/components/AddressSearch.tsx`), uma instrução e um botão ("Confirmar este local"). Abaixo da situação, uma linha lembra que o UrMind não avisa a prefeitura e aponta o SP156. Botões de ação principal não ficam cinza: antes de mexer no mapa, "Confirmar este local" e "Confirmar local" dizem o que falta. Um protocolo que não abre mostra qual relato e "Tentar de novo"; sem internet no aparelho, fala da conexão; com internet, diz que o UrMind não respondeu e aponta a "Situação do serviço", sem culpar a pessoa.
- **Área da equipe:** a fila abre em "Aguardando revisão", com os mais urgentes primeiro (sem prioridade calculada, os mais antigos); cada relato é revisado em tela própria: antes de decidir, "Pular este relato" é um link discreto; depois, "Próximo relato" vira o botão principal. A decisão ("Confirmar relato", "Recusar relato") fica sempre à vista no rodapé; no celular, só o verbo aparece, em duas metades iguais, e o leitor de tela continua ouvindo o nome completo. A ocorrência tem o mesmo formulário (tipo, local e notas) e, antes da primeira decisão, não mostra gravidade nem prioridade (revisão às cegas). O tipo já vem da última decisão da equipe. A seção Local mostra o ponto atual e de onde ele veio; o mapa é o caminho principal para corrigir, e as coordenadas ficam em "Digitar coordenadas", com o erro junto do campo. A regra das duas pessoas aparece uma vez, e o histórico mostra o voto de cada pessoa ("Uma pessoa da equipe confirmou como Buraco"), sem parecer o resultado final. Recusar um relato pede o motivo, como a ocorrência, e diz o que quem enviou vai ver. No celular, as barras fixas encostam nas abas. Na fila e no Painel, selo só no que já foi decidido; a fila nunca esconde um filtro: o filtro ativo aparece por extenso (no celular, no próprio "Filtros"), "Limpar filtros" volta ao padrão, a aba "Fila" sempre abre em "Aguardando revisão" e o subtítulo acompanha a situação escolhida; o Painel conta os relatos dos últimos 7 dias (o "hoje" do servidor é o dia UTC, que começa às 21h em Brasília, e não aparece); Ocorrências filtra por situação e tipo, sem busca de texto (a lista só mostra o tipo); no celular, os filtros do Mapa interno ficam recolhidos em "Filtros", como na fila; o catálogo de tipos fica numa linha recolhida no fim da fila. O Painel mostra quatro números que levam à lista do que contam, os três próximos a revisar e os serviços externos numa frase ("A busca de endereços está fora do ar: os relatos continuam chegando…"); a lista completa e os tempos ficam recolhidos. Na Administração, os quatro campos da foto ficam em 2×2, a luz aparece em porcentagem e cada campo diz o valor padrão, as regras raras ficam em "Regras avançadas" e, com alteração não salva, a barra de salvar acompanha a rolagem e o formulário ganha espaço no fim, para nada ficar coberto; a área da equipe tem margem no fim da página. O código da área da equipe é baixado só em `/admin/`.
- **Safari e iPhone (motor WebKit):** a troca de página não usa a animação de transição, que no WebKit derrubava a página quando a próxima ainda carregava; a foto dos rascunhos fica guardada como texto (base64), formato que todo navegador aceita; o campo em foco ganha contorno, que o Safari desenha também em listas de opções e caixas de marcar.
- Movimento curto e com propósito (troca de página, etapa em andamento, confirmação de cópia). Com "reduzir movimento" ligado no sistema, nada se anima.
- **Acabamento medido:** duas varreduras percorrem todas as rotas declaradas em `src/app/routes.ts`, as telas de endereço variável e os estados (vazio, servidor fora do ar, busca, mapa, foto escolhida, rascunho, acesso restrito). `tests/plain-language.spec.ts` abre tudo o que está recolhido e lê também dicas, nomes acessíveis, textos de exemplo, descrições de imagem e o título da aba; falha com o trecho exato se aparecer termo de bastidor, código interno ou texto em inglês. `tests/design-quality.spec.ts` mede cada tela em dez tamanhos (celulares de 320 a 430 px, celular deitado, tablets e monitores até 1920 px, com toque nos aparelhos de toque): contraste WCAG AA, área de toque de 24 px, sem rolagem lateral nem texto cortado, um título principal e níveis de título em ordem, nome em todo botão e campo, uma família de letra, foco visível, nenhum erro de script e nada animado com "reduzir movimento". As telas e os dados simulados ficam em `tests/screens.ts`.
- O Início não mostra diagnóstico técnico (API, banco, trechos) nem consulta o estado do sistema; isso fica em `#/system` ("Situação do serviço"). A página pública não lista categorias sem detector. Os testes E2E conferem as duas regras.

## Onde mexer

| Quero mudar…                                                                  | Arquivo                                                                                                    |
| ----------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| Cores, fontes, espaços, raios, sombras, camadas                               | `src/styles/tokens.css`                                                                                    |
| Estilo de um componente ou de uma página                                      | `src/styles/components/*.css` e `src/styles/pages/*.css` (ordem em `src/styles/index.css`)                 |
| Menu lateral, barra inferior do celular, topo e rodapé                        | `src/layout/` (`PublicNav.tsx`, `TeamNav.tsx`, `Topbar.tsx`, `Footer.tsx`) e `src/styles/layout/shell.css` |
| Uma rota ou o nome de uma página no menu                                      | `src/app/routes.ts`                                                                                        |
| Qual componente aparece em cada rota                                          | `src/pages/citizen/CitizenPage.tsx` (site do cidadão) e `src/pages/team/TeamArea.tsx` (equipe)             |
| Uma página pública (Início, Mapa, Ocorrências, Transparência, Sistema, Sobre) | `src/pages/public/`                                                                                        |
| Registrar relato, Meus relatos, Seu relato, Rascunhos, Câmera do robô         | `src/pages/citizen/`                                                                                       |
| Painel, fila de revisão, mapa interno, ocorrências e auditoria da equipe      | `src/pages/team/`                                                                                          |
| Lista e mapa de relatos (compartilhados entre cidadão e equipe)               | `src/components/reports/`                                                                                  |
| Revisão de um relato e detalhe interno de uma ocorrência                      | `src/components/EventDetail.tsx`                                                                           |
| Confirmação de ações que não se desfazem                                      | `src/components/ConfirmButton.tsx`                                                                         |
| Textos e estado das etapas do "Caminho de um relato"                          | `src/content/systemFlow.ts`                                                                                |
| Versão simples de frases da API (regras, fontes, limites, área piloto)        | `src/content/plainLanguage.ts`                                                                             |
| Nomes e valores dos "Detalhes técnicos" (etapas, fontes, versões, fatores)    | `src/content/technical.ts`                                                                                 |
| Dicas dos botões do mapa (aproximar, afastar, onde estou…)                    | `src/content/mapLocale.ts`                                                                                 |
| Destinos da busca "Buscar página ou relato"                                   | `src/layout/commands.ts`                                                                                   |
| Sessão, rascunhos, envio, acompanhamento, relatos e dados da equipe           | `src/app/hooks/` (reunidos em `src/app/AppState.tsx`)                                                      |
| Contratos da API e regras de exibição                                         | `src/domain/`                                                                                              |
| Chamadas à API, Supabase e armazenamento local                                | `src/services/`                                                                                            |

Componentes visuais comuns ficam em `src/components/ui.tsx`: marca, cabeçalho de página, estado vazio, esqueleto de carregamento, selo de gravidade, botão de copiar e a trilha do relato (Recebido → Em análise → Confirmado). Cada página lê o estado compartilhado com `useAppState()`.

## Desenvolvimento local

Requisito: Node.js 22.12 ou superior.

```powershell
cd frontend
npm ci
npm run dev
```

Abra http://127.0.0.1:5173. O Vite encaminha `/api` ao backend local quando o proxy está configurado. Use `frontend/.env.example` como referência para variáveis públicas. Não coloque senhas ou chaves privadas em variáveis `VITE_*`.

## Verificação

```powershell
npm test
npm run build
npm run test:e2e
```

Só as varreduras de linguagem simples e de acabamento:

```powershell
npm run build
npx playwright test tests/plain-language.spec.ts tests/design-quality.spec.ts
```

Os testes de navegador exigem o navegador e os serviços previstos na configuração do Playwright. Os aparelhos são emulados no Edge (tamanho de tela e toque); as duas varreduras também rodam no motor do Safari, sozinhas: `npx playwright install webkit` e `$env:PW_WEBKIT=1; npx playwright test tests/plain-language.spec.ts tests/design-quality.spec.ts --project=webkit --workers=1`. Nenhuma emulação substitui o teste num celular físico. O build local valida a aplicação que a Vercel publica a partir do repositório.
