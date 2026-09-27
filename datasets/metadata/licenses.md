# Verificação de licenças e procedência

Revisão em 2026-09-09, **depois** das aquisições autorizadas. As oito fontes estão em disco;
a coluna "Situação" descreve o que foi efetivamente conferido, não o que se pretende conferir.

| Fonte | Evidência | Situação |
|---|---|---|
| UNIVALI/DNIT | https://data.mendeley.com/datasets/t576ydh9v8/4 | CC BY 4.0 confirmado na ficha da versão 4. SHA-256 oficial confere. Reconferido na fonte oficial em 2026-09-16: DOI 10.17632/t576ydh9v8.4, v4, publicado em 2020-07-21, autores Passos, Cassaniga, Fernandes, Medeiros e Comunello. A ficha declara que as imagens vieram do **DNIT** pela Lei de Acesso à Informação, protocolo 50650.003556/2017-28: a procedência é declarada e lícita, mas o depósito CC BY 4.0 é dos autores, não do DNIT. |
| Urban Community | https://www.kaggle.com/datasets/rajeevpaudel1/urban-community-issues | **RED — BLOCKED_LICENSE / BLOCKED_PROVENANCE** (revisão de 2026-09-16). O uploader declara CC0, mas a declaração **não vale para todo o conteúdo**. 1.800 das 2.518 imagens têm nome compatível com o ID do Open Images — **inferido pelo padrão de ID**; **9 verificadas diretamente** contra o espelho oficial, 9/9 com tamanho idêntico. O Open Images lista essas imagens como CC BY 2.0 e não garante a licença individual. As outras 718 seguem sem origem comprovada. A CC0 do uploader não substitui o direito dos autores originais: **não republicar**. Reconstruir a partir do Open Images seria outro dataset, com outra proveniência. Sem checksum oficial; o mapa id→classe foi inferido das pastas. |
| Project Sidewalk | https://huggingface.co/datasets/projectsidewalk/rampnet-crop-model-dataset-round1 | MIT declarada na ficha oficial, revisão `521f74ff`. SHA-256 oficial dos 5 shards confere. As imagens vêm do Google Street View: **a MIT cobre o pacote, não a redistribuição da imagem original**. |
| RampNet | https://huggingface.co/datasets/projectsidewalk/rampnet-dataset | MIT declarada na ficha oficial, revisão `ee882e3f`. SHA-256 oficial dos 5 shards confere. Mesma ressalva sobre a origem GSV. |
| CAMBER #50 | https://zenodo.org/records/21361827 | **YELLOW — REVIEW_REQUIRED**, bloqueio `EXTERNAL_HEAVY_MEDIA_LICENSE_NOT_ESTABLISHED` (revisão de 2026-09-16). DOI 10.5281/zenodo.21361827, versão 1.0. O depósito é CC BY 4.0 e cobre `detections_50.csv` e `video_50_metadata.txt`; MD5 dos dois confere. MP4 e GPX ficam em S3 externo e **sua licença explícita não foi demonstrada**. Integridade verificada: MD5 do GPX local == ETag remoto; ETag multipart do MP4 == remoto. A metadata declara "Privacy-Blurred Video File Access" — declaração do publicador, sem verificação visual independente. Divergência de grant: Zenodo 101156387 vs CORDIS 101146800. Desbloqueio: confirmação escrita do publicador de que MP4/GPX são CC BY 4.0, ou depósito oficial deles com licença explícita. |
| BDD100K (10K + seg) | https://doc.bdd100k.com/download.html | Licença BDD100K de uso educacional/pesquisa, distinta da BSD do código. **Não há checksum oficial utilizável**: o MD5 publicado tem 31 dígitos hexadecimais e é inválido. Integridade local por CRC do ZIP e SHA-256. |
| Global Streetscapes | https://huggingface.co/datasets/NUS-UAL/global-streetscapes | **YELLOW — REVIEW_REQUIRED / BLOCKED_ATTRIBUTION**, bloqueio `ATTRIBUTION_INCOMPLETE` + `MASS_REDISTRIBUTION_TERMS_UNRESOLVED` (revisão de 2026-09-16). Metadata sob CC BY-SA 4.0, sem gating; **as imagens do Mapillary e do KartaView também são CC BY-SA 4.0 na origem**. Substitui o Mapillary MSLS. Pendências: o Mapillary exige crédito ao fotógrafo de cada imagem, e a seleção local traz `source`, `orig_id`, `sequence_id` e `sha256`, sem campo de autor (~15 mil imagens a atribuir); o **share-alike** obriga manter a CC BY-SA; os termos do Mapillary para extração e republicação em massa seguem sem resolução. No UrMind a fonte é CONTEXT_ONLY. Sem checksum por tarball; SHA-256 local por imagem. |

## Fonte aposentada

**Mapillary MSLS** (https://github.com/mapillary/mapillary_sls) saiu do escopo em 2026-09-08.
O portal oficial exige login para qualquer download, inclusive a amostra, e o projeto não tem
credencial nem pode usar serviço pago. Foi substituído pelo Global Streetscapes, que
redistribui legalmente imagem do próprio Mapillary (14.610 das 15.007 selecionadas) e do
KartaView (397). Motivo completo e alternativas avaliadas em `removed_sources.json`.

## O que estas evidências não provam

As URLs são fontes de referência, nunca instruções de download automático. Cada nova aquisição
exige autorização, versão imutável, finalidade, classes, licença, tamanho esperado e estimativa
de pico. A opção de reconstrução depende da disponibilidade e dos termos futuros da fonte; o
manifesto não garante que ela continuará hospedando os mesmos bytes.

Uma licença declarada na ficha do dataset é declaração do publicador, não auditoria da cadeia
de direitos das imagens. Onde a origem é imagem de rua de terceiros — Project Sidewalk e
RampNet — a licença do pacote e a licença da fotografia original são coisas distintas, e a
segunda não foi verificada aqui. No Global Streetscapes a licença das fotografias foi
verificada na origem (CC BY-SA 4.0 no Mapillary e no KartaView), mas a atribuição por imagem
e os termos de republicação em massa continuam pendentes. Checksum confere bytes, não autoria.
