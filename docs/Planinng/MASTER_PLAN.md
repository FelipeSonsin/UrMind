# UrMind — Master Plan

**Revisão:** 27/09/2026

**Estado:** planejamento técnico. Nenhum modelo descrito neste documento está treinado, aprovado ou ativo no produto.

## 1. Direção do projeto

O UrMind registra problemas urbanos com foto e localização, organiza a revisão humana e publica ocorrências confirmadas no mapa. O desenvolvimento atual é centrado em fotos enviadas pelo celular; o Scout com câmera, GPS, IMU e áudio continua como fonte futura de evidências para o mesmo núcleo.

**Stack de inteligência artificial planejada:**

| Componente | Papel planejado | Plataforma | Estado atual |
|---|---|---|---|
| YOLO11 (também chamado YOLOv11) | Localizar candidatos a problemas visuais em imagens | Dataset, anotação, treinamento e avaliação no Roboflow | Planejado; sem modelo ativo |
| XGBoost | Estimar severidade ou risco a partir de dados tabulares revisados | Treino e avaliação controlados fora do serviço web | Planejado; sem modelo ativo |

YOLO11 e XGBoost não substituem a decisão humana. A saída de qualquer modelo futuro será uma sugestão rastreável. A classificação, a localização, a prioridade e a publicação continuarão sujeitas às regras do produto e à revisão exigida para cada etapa.

Os treinos anteriores de YOLOX e XGBoost não integram este plano. Arquivos históricos eventualmente preservados no Git, em armazenamento ou no banco **não são autorização para reutilizar dados, rótulos, pesos ou métricas**. Um novo ciclo deve começar com dados e objetivos aprovados.

## 2. Situação implementada hoje

1. O usuário envia uma foto com localização do aparelho ou EXIF; quando falta localização confiável, confirma o ponto no mapa.
2. A API valida o arquivo e registra a captura. A câmera remota do robô pode fornecer um quadro para o mesmo fluxo de registro.
3. A fila encaminha a captura para revisão humana. Ela não executa YOLOX, YOLO11 nem XGBoost.
4. Revisores verificam evidência, classe, localização, privacidade e decisão. Ocorrências confirmadas podem aparecer no mapa público.

O frontend React é publicado separadamente da API FastAPI. Supabase mantém autenticação, banco geográfico, fila e armazenamento; Render hospeda a API. A interface e a API devem informar claramente quando uma análise automática não está disponível. Nenhum resultado antigo deve ser apresentado como capacidade atual.

## 3. Escopo funcional que permanece

- **Relato e evidência:** foto original preservada, protocolo, data, origem, localização e informações de qualidade.
- **Taxonomia:** as categorias do produto permanecem como escopo de atendimento; somente classes com dados autorizados e avaliação suficiente podem entrar no detector. A taxonomia operacional não comprova capacidade de um modelo.
- **Geografia:** PostGIS e segmentos viários ligam ocorrências ao território; MapLibre apresenta o mapa. Endereço e contexto externo são complementares, com origem identificada.
- **Revisão:** decisões, correções e conflitos são registráveis. Publicação e resposta ao cidadão dependem de evidência verificável.
- **Scout futuro:** sensores sincronizados alimentam o mesmo cadastro de capturas. O áudio não identifica sozinho um defeito e o módulo de movimentação do Scout é independente da detecção urbana.
- **Privacidade:** imagens públicas exigem análise de conteúdo sensível e revisão quando a proteção automática não for comprovada.

## 4. Plano de dados para YOLO11 no Roboflow

### 4.1 Aprovação antes de importar

Definir o problema visual, as classes iniciais, o tipo de anotação e a licença de cada fonte. Para localizar defeitos com caixas, criar um projeto de **detecção de objetos**. Máscaras exigem uma decisão separada sobre segmentação; converter máscaras antigas em caixas sem revisão não torna esses rótulos aptos para treino.

Registrar para cada conjunto: proprietário, permissão de uso, origem, local e período de coleta, consentimento ou base aplicável, restrições de redistribuição, quantidade de imagens, qualidade e exclusões. Não importar automaticamente as fontes brutas remanescentes do ciclo antigo.

### 4.2 Anotação e controle de qualidade

Escrever um guia de anotação antes de rotular. Definir exemplos positivos, negativos, ambíguos e critérios de caixa; revisar divergências entre anotadores. Auditar amostras por classe, cidade, tipo de via, iluminação, clima, câmera e tamanho do defeito. Remover duplicatas e imagens sem direito de uso antes de gerar versões.

No Roboflow, manter um projeto com classes controladas, imagens revisadas e uma **versão imutável do dataset** para cada experimento. Dividir treino, validação e teste por grupos de origem, rota, local e período para evitar que quadros quase iguais apareçam em partições diferentes. Congelar o teste antes da comparação de modelos. Aumentos de dados devem atuar somente no treino.

### 4.3 Treino, avaliação e escolha

Treinar YOLO11 no Roboflow apenas depois da aprovação do conjunto. Registrar ID do projeto, versão do dataset, arquitetura e tamanho escolhidos, classes, configuração, data, responsável e identificador do modelo. Escolher tamanho e limiar por medição no dispositivo e no fluxo pretendidos, não pelo nome da arquitetura.

Avaliar por classe e por recorte geográfico: precisão, recall, F1, mAP, falsos positivos, falsos negativos, desempenho em defeitos pequenos e latência. Inspecionar erros reais em fotos brasileiras distintas das usadas no treino. Nenhuma métrica de validação substitui o teste congelado. Nenhum resultado é declarado aprovado por este documento.

## 5. Plano de dados para XGBoost

Definir primeiro **uma variável alvo revisada por humanos**: por exemplo, uma classe de severidade operacional. Risco e prioridade são decisões diferentes; podem incluir contexto territorial, exposição e regras públicas além da severidade. Não usar rótulo inferido pelo próprio YOLO11 como verdade de treinamento do XGBoost.

Preparar uma tabela por ocorrência elegível, com origem e instante de cada variável, tratamento de ausências e autorização de uso. Candidatos a atributos incluem classe confirmada, dimensões verificadas, contexto da via e informações ambientais disponíveis no momento da decisão. Identificadores, dados posteriores à decisão e respostas humanas que definem o alvo não podem vazar para os atributos.

Separar treino, validação e teste por ocorrência, local e tempo; comparar XGBoost com regras existentes e uma referência simples. Medir desempenho por classe e território, calibração, erro em casos críticos e efeito de dados faltantes. Registrar parâmetros, dados, métricas, explicações e decisão de promoção. Uma previsão futura será **assessoria**, com possibilidade de revisão e contestação.

## 6. Integração futura sem ativação implícita

Fluxo proposto após aprovação:

`Capture → validação técnica → YOLO11/Roboflow → candidato visual → revisão → Event confirmado → atributos tabulares → XGBoost → recomendação revisável → decisão e mapa`

O treino ocorre fora do processo da API e fora do build do frontend. Credenciais do Roboflow ficam somente em ambiente servidor autorizado. O produto deve identificar versão do dataset, versão do modelo e configuração de inferência para cada resultado; dados de teste e rótulos privados não entram no navegador.

Antes de qualquer uso com cidadãos, executar uma fase isolada de avaliação e uma fase de observação sem decisão automática. Ativar somente com responsável, limites de uso, monitoramento, mecanismo de desativação e plano de reversão definidos. A ausência de modelo, falha externa ou baixa confiança devolve a captura à revisão humana, sem fabricar uma detecção.

## 7. Portões de decisão

| Etapa | Evidência necessária para avançar |
|---|---|
| Dados | fontes autorizadas, licença e privacidade verificadas, protocolo de anotação, duplicatas resolvidas |
| Versão do dataset | classes e partições congeladas, auditoria de rótulos, teste isolado |
| YOLO11 | treino reproduzível no Roboflow, avaliação por classe e território, latência medida, erros revisados |
| XGBoost | alvo humano, ausência de vazamento, baseline, avaliação temporal e geográfica, calibração |
| Integração | contratos de API, segurança, falha segura, rastreabilidade e testes de ponta a ponta |
| Ativação | aprovação humana explícita, versão fixada, monitoramento e reversão testada |

Nenhuma etapa pode ser inferida apenas pela presença de arquivos, modelos ou campos no banco. Até a última etapa, o sistema segue com revisão humana e informa que a detecção automática planejada ainda não está disponível.

## 8. Próximos trabalhos

1. Confirmar as primeiras classes visuais e o protocolo de anotação.
2. Selecionar fontes novas com permissão comprovada; auditar qualidade e representatividade.
3. Criar o projeto e a versão do dataset no Roboflow; revisar partições e treinar um primeiro YOLO11.
4. Avaliar o detector em teste isolado e em fotos reais do ambiente de uso.
5. Definir o alvo tabular, montar dados revisados e comparar XGBoost com regras simples.
6. Projetar a integração somente depois das avaliações e autorizações anteriores.

## 9. Referências técnicas

- [Roboflow: treinamento de YOLO11 com dataset próprio](https://blog.roboflow.com/yolov11-how-to-train-custom-data/)
- [Roboflow: uso de YOLO11 na plataforma](https://blog.roboflow.com/use-yolo11-with-roboflow/)
- [XGBoost: introdução e interface de classificação](https://xgboost.readthedocs.io/en/stable/get_started.html)

Este Master Plan é a fonte de verdade editorial. **UrMind Planejamento Completo.docx** é a versão Word gerada a partir dele; os dois devem ser publicados no mesmo commit.
