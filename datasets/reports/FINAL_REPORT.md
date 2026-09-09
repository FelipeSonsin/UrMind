> **Relatório histórico da auditoria anterior à aquisição autorizada em 2026-09-08.** Os tamanhos e as afirmações de ausência de downloads abaixo pertencem àquela etapa. Para o estado posterior consulte `datasets/STATUS.md` e `reports/rdd2022_reduction.json`.

# UrMind — relatório da etapa de datasets

Revisão complementar de presença, procedência, caches e divisão igual: [READINESS_REVIEW.md](READINESS_REVIEW.md).


Gerado em 2026-09-08T16:38:52.455991+00:00. GB = 1.000.000.000 bytes. Sem treino ou inferência.


## 1–2. Estrutura inicial e final

Inicial: raw, processed, annotations, metadata, splits, downloads, reports e manifests. Havia nove scripts e relatórios anteriores, mas nenhuma seleção por imagem nem split em lista.

Final: raw, metadata, manifests, splits e reports. Por autorização posterior do usuário, foram removidas SOMENTE as pastas vazias processed, downloads e annotations. Nenhum arquivo dessas pastas existia. annotation (singular) não existia.

Os nomes/pastas dentro de raw foram preservados. Não foram criados placeholders adicionais. A pasta de planejamento encontrada é docs/Planinng; ambos os documentos atuais foram lidos integralmente.


## 3–5. Tamanhos atuais

| Área | Bytes lógicos | GB decimais | Bytes em disco |
|---|---|---|---|
| datasets | 29256963860 | 29.2570 GB | 29256963860 |
| RDD2022 | 27099910750 | 27.0999 GB | 27099910750 |

ML contabilizado conservadoramente: 29257559374 bytes. A conta usa o maior entre lógico e alocado, somado ao conteúdo adicional identificado fora de datasets. Ambientes/dependências não são conteúdo essencial de dataset.

Alocação medida via GetCompressedFileSizeW; não é mais a estimativa por clusters do relatório anterior. Não inclui toda a sobrecarga estrutural do volume. Cloud-only é contado logicamente e não hidratado.

Placeholders cloud-only em datasets: 0.


## 6–7. Classes no RDD2022

| Classe | Imagens com a classe | Objetos válidos | % dos objetos |
|---|---|---|---|
| URMIND_ROAD_D00 | 13548 | 26016 | 47.2967 |
| URMIND_ROAD_D10 | 7709 | 11830 | 21.5067 |
| URMIND_ROAD_D20 | 8412 | 10616 | 19.2997 |
| URMIND_ROAD_D40 | 3674 | 6544 | 11.8969 |

Uma imagem pode conter mais de uma classe; as contagens de imagens por classe não devem ser somadas como total único. Labels originais permanecem no inventário e em raw.


## 8. Distribuição por país/origem

| Origem | Imagens totais | Objetos V1 | Negativas | Bytes de imagens | Arquivos na origem | Bytes lógicos da pasta |
|---|---|---|---|---|---|---|
| China_Drone | 2401 | 3068 | 5 | 168137595 | 4802 | 169548077 |
| China_MotorBike | 2477 | 4650 | 0 | 191829105 | 4454 | 193411971 |
| Czech | 3538 | 1745 | 1757 | 256340962 | 6367 | 257133998 |
| India | 9665 | 6831 | 3921 | 652441423 | 17371 | 655717341 |
| Japan | 13133 | 16469 | 794 | 1069919514 | 23639 | 1077533186 |
| Norway | 10201 | 11229 | 5247 | 11021775651 | 18362 | 11028102805 |
| United_States | 6005 | 11014 | 0 | 447312845 | 10810 | 451028501 |

São seis países e sete origens: China_Drone e China_MotorBike pertencem ao mesmo país. Detalhes de classes, alocação e contagens por origem estão em rdd2022_audit.json.


## 9–11. Negativos, integridade e ausências

11724 negativas confirmadas (XML válido, zero objetos originais). 2893 imagens apenas com classes fora da V1, mantidas separadas. 2 XMLs associados a imagens com erro e 0 imagens com falha de decodificação.

9035 imagens sem XML, todas no test oficial. Imagens referenciadas por XML mas ausentes: 0. Lista nominal oficial: 85805 arquivos, 0 ausentes e 0 extras.

Anomalias: Japan_001265.xml contém caixa D20 degenerada; India_006389.xml contém D0w0 ambíguo. Não foram corrigidas em raw. A seleção exclui a imagem inteira com anotação problemática, sem inventar correção.

MD5 do ZIP e dos três anexos oficiais RDD2022 conferidos; SHA-256 do UNIVALI confere. Os dois anexos CSV/metadata do CAMBER conferem com o MD5 oficial; MP4/GPX externos não têm checksum oficial registrado. Urban Community confere com o checksum local anterior, que não é checksum oficial. A lista nominal não equivale à checagem de CRC das entradas internas.


## 12–14. Duplicatas e espaço potencial

Duplicatas exatas: 2 grupos; economia potencial de 151410 bytes. Candidatos visuais: 296 grupos; limite superior hipotético de 118624915 bytes, sujeito a revisão.

SHA-256 em todas as imagens locais. Similaridade: dHash horizontal+vertical de 128 bits, Hamming <= 4, filtro de desvio padrão >= 10 e componentes conexos. Relações transitivas ficam no mesmo grupo. Candidatos não provam duplicação nem identidade de sessão. Não somar economia visual e exata porque podem se sobrepor.

O ZIP mestre RDD2022 ocupa 13.264.172.619 bytes, além da extração. Removê-lo no futuro, após autorização e decisão sobre recuperação, poderia reduzir a pasta para aproximadamente 13,84 GB sem eliminar imagens extraídas. Isso NÃO foi executado e não é classificado como duplicata byte a byte das imagens.


## 15–21. Proposta de seleção e justificativa

Manifesto: datasets/manifests/rdd2022_subset_selection.jsonl. 35490 imagens, 55003 objetos V1, 11724 negativos; 10848866625 bytes de imagens (10.8489 GB). O tamanho de XMLs e manifestos deve ser acrescentado em uma futura materialização, cujo pico precisa de novo preflight.

| Classe | Imagens no subset | Objetos no subset |
|---|---|---|
| URMIND_ROAD_D00 | 13547 | 26015 |
| URMIND_ROAD_D10 | 7709 | 11830 |
| URMIND_ROAD_D20 | 8411 | 10614 |
| URMIND_ROAD_D40 | 3674 | 6544 |

| Origem | Imagens selecionadas | Objetos | GB imagens |
|---|---|---|---|
| China_Drone | 1924 | 3068 | 0.1383 GB |
| China_MotorBike | 1934 | 4650 | 0.1499 GB |
| Czech | 2829 | 1745 | 0.2051 GB |
| India | 7144 | 6831 | 0.4816 GB |
| Japan | 8693 | 16466 | 0.7015 GB |
| Norway | 8161 | 11229 | 8.8144 GB |
| United_States | 4805 | 11014 | 0.3581 GB |

Seleção conservadora: preserva todas as imagens de train com XML íntegro e classe V1 ou negativo confirmado, exceto cópias byte a byte com annotation idêntica. Duplicatas com anotações diferentes permanecem para revisão. Mantém near-duplicates, com isolamento no split. Não usa score/byte, cota artificial, oversampling ou augmentation.

Representatividade das quatro classes, origens e exemplos difíceis é preservada sem poda para caber num número arbitrário. Objetos pequenos e difficult do XML são registrados como indícios de dificuldade, não avaliação humana de qualidade visual. O conjunto íntegro já cabe abaixo do intervalo 12–14 GB; não há justificativa para preenchê-lo com redundância.

Fila de revisão: 2895 imagens. Exclusões de cópias exatas com mesma annotation: 0. Seed: 20260908; algoritmo v2 e hashes registrados. Nenhuma imagem copiada, movida ou removida.


## 22. Proposta de train/validation/test

| Split | Imagens | Objetos | Proporção | Grupos |
|---|---|---|---|---|
| train | 23998 | 34526 | 67.62% | country:India, country:Japan, country:Norway |
| validation | 3858 | 7718 | 10.87% | country:China |
| test | 7634 | 12759 | 21.51% | country:Czech, country:United_States |

Países inteiros, união de componentes de duplicatas exatas/visuais, busca exaustiva sobre os grupos para cobertura de classes e proporções próximas de 70/15/15. Verificação independente: nenhum caminho, hash ou grupo detectado em múltiplos splits. Isso é isolamento dos vínculos conhecidos, não prova universal de ausência de leakage. Rotas, sessões e localizações não estão documentadas de forma utilizável no pacote.

Teste é proposta independente por domínio/país e deve ser congelado antes do futuro treinamento. Não mede generalização brasileira. A proposta não foi promovida a configuração de YOLOX.

Limitação do split: validação chinesa tem apenas 5 negativos, enquanto o teste tem 1.757. A validação não basta sozinha para medir falsos positivos em vias sem dano. Antes do treino, revisar protocolo de validação por domínio, sem acessar resultados do teste. As distribuições por país são naturalmente diferentes; não foram equalizadas artificialmente.

Uma duplicata exata cruza train/test OFICIAIS dos Estados Unidos (United_States_003260 e United_States_005833). O test oficial não entra na seleção supervisionada. No split proposto, todos os membros selecionados de qualquer cluster conhecido ficam no mesmo lado.


## 23. Tamanho das pastas efetivas

| Pasta | Bytes lógicos | Bytes em disco | Arquivos |
|---|---|---|---|
| raw | 29170734632 | 29170734632 | 99804 |
| metadata | 25478 | 25478 | 8 |
| splits | 2466489 | 2466489 | 4 |
| reports | 651631 | 651631 | 14 |
| manifests | 83073932 | 83073932 | 5 |

As medidas são um snapshot anterior à gravação deste próprio relatório; pequenas diferenças de metadados são esperadas. Pastas vazias removidas tinham zero arquivos e zero bytes de conteúdo.


## 24–26. Orçamento, disco e OneDrive

Alvo agregado 35 GB; máximo 40 GB, distribuição individual orientativa e redistribuível. Espaço livre medido: 167559860224 bytes (167.5599 GB); preservar pelo menos 10 GB no pico.

Projeto em pasta OneDrive: SIM, detectado pelo caminho. Não foi movido, não houve pausa/reconfiguração de sincronização nem hidratação forçada. DVC não foi configurado e não foi encontrada configuração .dvc na raiz ou no backend; nenhum cache DVC foi criado.


## 27–28. Arquivos externos e preservação

| Base dinâmica | Localização relativa | Bytes lógicos |
|---|---|---|
| user_profile | Downloads/FECART IDEIA DO PROJETO.docx | 16026 |
| user_profile | OneDrive - Fundação Escola de Comércio Álvares Penteado/Fecart 2°Ano | 26704367 |

Busca limitada aos nomes relacionados em Downloads/Desktop/pasta pai; não é varredura completa do computador. Associação ao projeto é candidata pelo nome, não confirmação do conteúdo. Nenhum arquivo externo foi aberto para usar como planejamento, movido ou apagado.

Verificação de preservação de raw: True. Assinatura de caminhos, tamanhos e mtime idêntica entre o baseline desta execução e a verificação final. O baseline foi tomado durante a auditoria, antes da remoção das pastas vazias. Ele não prova o histórico anterior à sessão. Não houve operação de escrita em raw.


## 29–31. Arquivos modificados, scripts e metadata

- .gitignore
- datasets/.gitignore
- datasets/README.md
- datasets/STATUS.md
- datasets/metadata/sources.csv
- datasets/metadata/storage_budget.yaml
- datasets/manifests/rdd2022.json
- datasets/manifests/univali_br.json
- datasets/manifests/camber.json
- scripts/datasets/_core.py
- scripts/datasets/_budget.py
- scripts/datasets/_taxonomy.py
- scripts/datasets/audit_storage.py
- scripts/datasets/audit_rdd2022.py
- scripts/datasets/find_duplicates.py
- scripts/datasets/propose_subset.py
- scripts/datasets/make_splits.py
- scripts/datasets/validate_portability.py


Scripts adicionais desta continuação: audit_external.py, audit_readiness.py, build_report.py, plan_storage.py, safety_snapshot.py, verify_pipeline.py, verify_sources.py. Dependências declaradas em scripts/datasets/requirements.txt.


Metadata atual: artifact_contract.yaml, artifact_registry.json, class_mapping.yaml, licenses.md, sources.csv, storage_budget.yaml, taxonomy.yaml, upstream_checksums.json. Taxonomia e mapeamentos originais preservados; fontes/orçamento atualizados. Registro de hashes dos artefatos em metadata/artifact_registry.json.


## 32. Git e portabilidade

Não existe repositório .git nesta cópia; não foi inicializado. .gitignore exclui raw, processed/downloads futuros, caches, arquivos temporários e ambientes, mantendo README, metadata, manifests, splits e relatórios versionáveis. Não foi incluída imagem, arquivo compactado nem credencial no Git.

Referências estruturadas verificadas: 196012; aprovação de portabilidade: True. Caminhos de dados relativos à raiz, resolução dinâmica com pathlib; sem symlinks/junctions criados. Registros antigos do backend marcados como legados e caminhos normalizados.


## 33–34. Erros corrigidos, integração e riscos restantes

- Corrigidos: GB/GiB; falso negativo por rótulo fora da V1; caixa inválida/valor não finito; falhas silenciosas em leituras; parser YAML parcial; escrita fora das pastas derivadas; dados cloud-only; algoritmo visual quadrático e truncamento dos grupos; independência de ordem; ligação por hashes; budget global com pico e reserva de disco.

- A cadeia de datasets funciona e foi verificada com os dados reais. IDs de taxonomia compatíveis com schema existente. Não foi testado nem implementado runtime, backend, frontend ou treinamento.

- Backend legado permite override externo URMIND_DATASETS_DIR, trabalha com caminhos relativos ao dataset e não consome automaticamente a seleção atual; seu critério usable exclui negativos. Ele continua intacto por restrição de escopo. artifact_contract.yaml documenta a ligação futura; não declarar integração automática pronta.

- Licenças pendentes nas fontes futuras e abrangência às mídias externas CAMBER. Urban declara CC0 na ficha indexada, mas procedência detalhada e mapa numérico ainda exigem revisão. RDD2022 não substitui validação brasileira. Duas annotations precisam de decisão humana futura. Near-duplicates exigem confirmação visual.

- Reconstrução sem arquivos grandes exige nova autorização e preflight: o ZIP externo contém ZIPs por origem; extração ingênua com todas as cópias simultâneas pode ultrapassar 40 GB. Scripts desta etapa não extraem nada.

- A regra de orçamento protege estes scripts; não é quota do sistema operacional e não impede adições manuais ou sincronização do OneDrive. Uma auditoria futura contabiliza esse consumo e bloqueia novas operações quando necessário.


## 35. Prioridade de futuras aquisições (nenhuma autorizada agora)

1. Primeiro aproveitar e validar UNIVALI/DNIT já local; aquisição adicional brasileira somente se trouxer classes/regiões ausentes, após licença e protocolo. Preservar a reserva orientativa de 2 GB para dados próprios.

2. Project Sidewalk: pequena exportação de uma cidade/área para acessibilidade. RampNet apenas em recorte útil e revisado; a divisão igual posterior reserva 4,125 GB a cada fonte, sem obrigação de preencher.

3. CAMBER: novos CSV/GPS/metadata com relevância temporal e geográfica; vídeos somente se necessários. [Registro oficial](https://zenodo.org/records/21361827).

4. Urban Community: não expandir antes de resolver procedência, licença e mapa de classes do material já local.

5. BDD100K subset somente quando houver trabalho de navegação. 6. Mapillary subset somente para contexto/reconhecimento de lugar, com variante explicitamente escolhida.

Essa ordem é uma recomendação técnica para o escopo atual. Por solicitação posterior, storage_budget.yaml v3 reserva 4,125 GB para cada uma das oito fontes (33 GB), 2 GB para dados próprios e 5 GB para modelos/processamento. É planejamento, não redução física do RDD2022 nem garantia de qualidade. A seleção existente de 10,85 GB foi preservada. Nenhuma aquisição cabe automaticamente: o pico e a ocupação real devem ser recalculados. Caches comuns de desenvolvimento podem usar suas localizações convencionais; dados volumosos de datasets/modelos devem permanecer no projeto.


Verificações finais: artifact_hash_chain, summary_recalculation, negative_definition, taxonomy, selection_order_independence, split_order_independence, split_coverage, duplicate_group_isolation, budget_boundaries, peak_storage, disk_reserve, raw_write_refusal, path_traversal_refusal, visual_index_real_sample, python_syntax, active_directory_structure. Análise estática Ruff executada separadamente. A etapa para aqui e aguarda autorização para qualquer aquisição, alteração em raw ou integração fora do escopo.
