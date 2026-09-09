# Presença, procedência e uso dos datasets

Revisão complementar de 2026-09-08. Esta revisão e o orçamento v3 incorporam as
instruções posteriores sobre caches e divisão igual. Não houve download, remoção de
dados, conversão, treino, inferência ou alteração de backend/frontend.

## Resultado verificável

Há dados reais locais em quatro fontes. As outras quatro ainda não têm dados.
A infraestrutura de auditoria lê os formatos locais, mas isso **não significa que
o sistema de identificação esteja integrado, treinado ou avaliado**.

| Fonte | Tamanho local bruto (GB decimais) | Dados e validação | Uso possível e pendência |
|---|---:|---|---|
| RDD2022 | 27,0999 | 47.420 imagens auditadas; pacote com checksum oficial conferido | Pavimento D00/D10/D20/D40. Manifestos/splits prontos como proposta; integração de runtime pendente. Dois XMLs problemáticos excluídos da seleção. |
| UNIVALI/DNIT Brasil | 0,4832 | 2.235 imagens e 6.705 máscaras decodificadas; pares/dimensões íntegros, sem ausências | Segmentação brasileira. Máscaras ainda não convertidas em caixas. Trinca genérica não determina D00/D10/D20. |
| Urban Community Issues | 1,4918 | 2.518 imagens e 2.518 labels; coordenadas YOLO válidas; 100 labels vazios; nenhuma ausência ou label órfão | 5.573 objetos originais. Mapa numérico inferido e procedência detalhada ainda exigem revisão. Não aprovado automaticamente para treinamento. |
| Project Sidewalk | 0 de dados | Somente README de 288 bytes | Acessibilidade; requer recorte real e termos definidos. |
| RampNet | 0 de dados | Somente README de 391 bytes | Rampas de calçada; requer recorte real e termos das imagens. |
| CAMBER | 0,0958 | Um MP4 decodificado integralmente sem erro; 51 pontos GPX; 9 detecções CSV sem confirmação humana | Contexto histórico/geográfico. Saídas de modelo não são ground truth nem prova de defeitos reais. |
| BDD100K | 0 de dados | Somente README de 254 bytes | Navegação/percepção; aquisição e integração pendentes. |
| Mapillary MSLS | 0 de dados | Somente README de 368 bytes | Reconhecimento de lugar/contexto; não equivale a Mapillary Vistas. Aquisição e integração pendentes. |

Consumo total atual de datasets: aproximadamente **29,26 GB**, incluindo arquivos
compactados, extrações e manifestos. O preflight atual confirma consumo inferior a
40 GB e mais de 10 GB livres no disco. As medições exatas estão em `storage_audit.json`.
Não existem oito datasets preenchidos com 4,125 GB nesta máquina.

## Evidências de procedência

- **RDD2022:** [repositório dos autores](https://github.com/sekilab/RoadDamageDetector)
  e registro Figshare versionado em `metadata/sources.csv`. ZIP e três anexos
  correspondem aos MD5 do snapshot oficial. A lista de 85.805 arquivos extraídos
  não tem ausências ou extras. Checksums não são avaliação humana das anotações.
- **UNIVALI/DNIT:** [Mendeley v4](https://data.mendeley.com/datasets/t576ydh9v8/4)
  confirma 2.235 imagens brasileiras, três máscaras por imagem e CC BY 4.0.
  O SHA-256 do pacote local confere com o registro de origem. A pasta local se
  chama `v1`, mas esse nome legado não muda a versão oficial v4; raw foi preservado.
- **Urban Community:** a [ficha indexada do autor no Kaggle](https://www.kaggle.com/datasets/rajeevpaudel1/urban-community-issues)
  declara CC0 e as contagens por categoria, que coincidem com os arquivos locais.
  A abertura direta da página não devolveu texto utilizável. Não há checksum
  oficial registrado; o SHA-256 é apenas referência local. Portanto, a correspondência
  byte a byte com uma versão imutável da publicação e os direitos de cada imagem
  **não estão comprovados**. A ficha não confirmou os IDs numéricos das classes.
- **CAMBER:** o [registro Zenodo #50](https://zenodo.org/records/21361827)
  identifica vídeo 50, rota 51 e as URLs externas do MP4/GPX, também presentes no
  metadata local. Os dois anexos correspondem aos MD5 oficiais. Os hashes do
  MP4/GPX são locais; não há checksum oficial registrado para esses dois arquivos.
  O snapshot declara CC BY 4.0; abrangência às mídias externas requer confirmação.
- **Fontes sem aquisição:** [Project Sidewalk](https://github.com/ProjectSidewalk/SidewalkWebpage),
  [RampNet](https://github.com/ProjectSidewalk/RampNet),
  [BDD100K](https://github.com/bdd100k/bdd100k) e
  [Mapillary MSLS](https://github.com/mapillary/mapillary_sls) são projetos reais.
  Identificar a fonte oficial não equivale a ter seus dados localmente.

## Divisão igual solicitada

Foram consideradas separadamente as oito fontes citadas, inclusive os nomes unidos
por `/` na solicitação. `metadata/storage_budget.yaml` v3 registra:

| Destinação | Alocação de planejamento |
|---|---:|
| RDD2022 | 4,125 GB |
| UNIVALI/DNIT | 4,125 GB |
| Urban Community | 4,125 GB |
| Project Sidewalk | 4,125 GB |
| RampNet | 4,125 GB |
| CAMBER | 4,125 GB |
| BDD100K subset | 4,125 GB |
| Mapillary subset | 4,125 GB |
| Dados próprios futuros | 2 GB |
| Modelos, checkpoints, caches ML e processamento | 5 GB |
| **Total máximo** | **40 GB** |

Os valores individuais são referências iguais, não ordem para preencher espaço.
O pico temporário também deve caber em 40 GB, preservando 10 GB livres no disco.
O RDD2022 atual excede sua referência individual, mas o conjunto atual cabe no
limite global. Não houve redução automática nem exclusão para aplicar a tabela.

O manifesto RDD2022 anterior seleciona 10,85 GB e continua preservado. Reduzi-lo
para 4,125 GB exigiria outra seleção, avaliação de diversidade e revisão do protocolo;
essa tabela não representa um novo subset aprovado. Alocar GB igualmente não
balanceia classes, resolução, número de exemplos, países ou qualidade. Também não
garante identificação sem erros. Não é tecnicamente justificável preencher UNIVALI
ou CAMBER apenas para igualar seu consumo ao de outros datasets.

## Caches e ligação com o sistema

Caches normais de ferramentas e dependências podem usar suas localizações
convencionais. Não serão movidos, apagados ou forçados para dentro do projeto.
Os dados volumosos do UrMind — datasets, modelos, checkpoints e caches grandes
de modelos/dados — continuam no projeto e dentro do orçamento. A reserva de disco
considera o espaço livre real, que também reflete caches comuns fora do projeto.

O backend legado não consome automaticamente os novos manifestos e seu critério
`usable` exclui imagens negativas. Nenhuma integração foi modificada nesta etapa.
Assim, a afirmação correta é: **arquivos e formatos auditados, preparação parcial;
identificação operacional não verificada**. Não foram produzidas métricas de IA.

Evidências executáveis:

- `scripts/datasets/audit_readiness.py`: decodificação completa de imagens/máscaras,
  pares, labels YOLO, CSV/GPX e estrutura do MP4; saída `dataset_readiness.json`.
- `scripts/datasets/audit_readiness.py --video-only`: usa ffmpeg já instalado para
  decodificar o vídeo sem gravar frames; saída `camber_video_decode.json`.
- `source_integrity.json`: verificações de checksum já executadas.
- `pipeline_verification.json`: auditoria da cadeia RDD2022, taxonomia e splits.

As proibições de download e alteração em raw continuam vigentes. Esta revisão não
adquiriu os quatro datasets ausentes nem prometeu desempenho do sistema.
