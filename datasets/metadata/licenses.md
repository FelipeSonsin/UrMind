# Verificação de licenças e procedência

Revisão em 2026-09-09, **depois** das aquisições autorizadas. As oito fontes estão em disco;
a coluna "Situação" descreve o que foi efetivamente conferido, não o que se pretende conferir.

| Fonte | Evidência | Situação |
|---|---|---|
| RDD2022 | Snapshot local `source.figshare.json`, DOI 10.6084/m9.figshare.21431547.v1 | CC BY 4.0 conforme registro oficial. MD5 do ZIP e CRC dos 85.805 arquivos extraídos conferidos. |
| UNIVALI/DNIT | https://data.mendeley.com/datasets/t576ydh9v8/4 | CC BY 4.0 confirmado na ficha da versão 4. SHA-256 oficial confere. |
| Urban Community | https://www.kaggle.com/datasets/rajeevpaudel1/urban-community-issues | Ficha indexada do autor declara CC0. **Não comprova direitos das imagens originais** nem publica checksum oficial; o mapa numérico id→classe foi inferido das pastas e continua exigindo revisão. |
| Project Sidewalk | https://huggingface.co/datasets/projectsidewalk/rampnet-crop-model-dataset-round1 | MIT declarada na ficha oficial, revisão `521f74ff`. SHA-256 oficial dos 5 shards confere. As imagens vêm do Google Street View: **a MIT cobre o pacote, não a redistribuição da imagem original**. |
| RampNet | https://huggingface.co/datasets/projectsidewalk/rampnet-dataset | MIT declarada na ficha oficial, revisão `ee882e3f`. SHA-256 oficial dos 5 shards confere. Mesma ressalva sobre a origem GSV. |
| CAMBER #50 | https://zenodo.org/records/21361827 | Snapshot oficial declara CC BY 4.0. MD5 dos dois anexos confere; **os termos das mídias externas (MP4/GPX) não foram confirmados** e elas não têm checksum oficial publicado. |
| BDD100K (10K + seg) | https://doc.bdd100k.com/download.html | Licença BDD100K de uso educacional/pesquisa, distinta da BSD do código. **Não há checksum oficial utilizável**: o MD5 publicado tem 31 dígitos hexadecimais e é inválido. Integridade local por CRC do ZIP e SHA-256. |
| Global Streetscapes | https://huggingface.co/datasets/NUS-UAL/global-streetscapes | CC BY-SA 4.0 declarada na ficha oficial, sem gating. Substitui o Mapillary MSLS. **Compartilhamento pela mesma licença**: derivar destas imagens obriga a atribuir e a manter a CC BY-SA. Sem checksum por tarball; SHA-256 local por imagem. |

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
de direitos das imagens. Onde a origem é imagem de rua de terceiros — Project Sidewalk,
RampNet e Global Streetscapes — a licença do pacote e a licença da fotografia original são
coisas distintas, e a segunda não foi verificada aqui. Checksum confere bytes, não autoria.
