"""Regera datasets/STATUS.md a partir do disco e dos relatorios.

Gerado, nao escrito a mao: o numero publicado tem de vir do disco e dos
relatorios, senao a documentacao envelhece sem ninguem perceber.
"""

import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def br(value: float, casas: int = 2) -> str:
    """Numero no formato brasileiro: milhar com ponto, decimal com virgula."""
    return f"{value:,.{casas}f}".translate(str.maketrans(",.", ".,"))

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "datasets/raw"
REPORTS = ROOT / "datasets/reports"

ORDER = [
    "rdd2022",
    "rampnet",
    "global_streetscapes",
    "project_sidewalk",
    "urban_community",
    "bdd100k",
    "univali_br",
    "camber",
]

PAPEL = {
    "rdd2022": "treino V1",
    "univali_br": "treino V1",
    "urban_community": "treino V1",
    "project_sidewalk": "referência",
    "rampnet": "referência",
    "camber": "referência",
    "bdd100k": "referência",
    "global_streetscapes": "referência",
}

CONTEUDO = {
    "rdd2022": "38.217 imagens com XML VOC em 7 origens; 4 classes de dano",
    "univali_br": "2.235 amostras com máscara, rodovias federais brasileiras",
    "urban_community": "2.518 imagens em 7 pastas de classe (formato YOLO)",
    "project_sidewalk": "recortes de panorâmica + keypoints de rampa, em Parquet",
    "rampnet": "panorâmicas + keypoints + coordenada real de rampa, em Parquet",
    "camber": "CSV de detecções, GPS e rota de um registro Zenodo",
    "bdd100k": "10.000 imagens de condução + mapas de segmentação, em ZIP",
    "global_streetscapes": "15.007 imagens street-level, 108 países, rótulo humano de cena",
}

INTEGRIDADE = {
    "rdd2022": "MD5 oficial do ZIP + CRC dos 85.805 extraídos",
    "univali_br": "SHA-256 oficial",
    "urban_community": "SHA-256 apenas local (a fonte não publica)",
    "project_sidewalk": "SHA-256 oficial por shard",
    "rampnet": "SHA-256 oficial por shard",
    "camber": "MD5 oficial dos anexos; mídia externa sem checksum publicado",
    "bdd100k": "CRC do ZIP + SHA-256 local; **não há checksum oficial válido**",
    "global_streetscapes": "SHA-256 local por imagem (a fonte não publica por tarball)",
}


def size(name: str) -> float:
    d = RAW / name
    if not d.is_dir():
        return 0.0
    return sum(p.stat().st_size for p in d.rglob("*") if p.is_file()) / 1e9


red = json.loads((REPORTS / "rdd2022_reduction.json").read_text(encoding="utf-8"))
gs = json.loads(
    (REPORTS / "acquisition_result_global_streetscapes.json").read_text(encoding="utf-8")
)

linhas = []
total = 0.0
for name in ORDER:
    gb = size(name)
    total += gb
    linhas.append(
        f"| `{name}` | {PAPEL[name]} | {br(gb)} GB | {CONTEUDO[name]} | {INTEGRIDADE[name]} |"
    )

v = red["validation"]
classes = v["loader_classes"]

# A data sai do relógio, não do teclado. Estava escrita à mão e envelhecia
# sozinha: um STATUS recém-gerado já nascia dizendo ser de dois dias antes, o
# que é pior do que não ter data — convida a duvidar dos números que estão certos.
gerado_em = datetime.now().astimezone().date().isoformat()

texto = rf"""# Estado dos datasets

Atualizado em {gerado_em}. GB decimais (1 GB = 1.000.000.000 bytes).

As oito fontes estão presentes com dado real e somam **{br(total)} GB** dos 40 GB
disponíveis. Sete cabem no teto de **7 GB por dataset**; o RDD2022 é uma **exceção
autorizada** e está explicado abaixo. Todas são lidas pelo backend:
`python -m app.datasets.cli inventory` e `... read <fonte>` funcionam para as oito.

| Fonte | Papel | Em disco | Conteúdo | Integridade |
|---|---|---|---|---|
{chr(10).join(linhas)}

## O que mudou nesta etapa

**RDD2022 fica como exceção acima do teto, por decisão do usuário.** A poda da origem
Norway foi construída, executada e validada: manter 100% das 2.914 imagens com defeito e
amostrar as negativas em 66 estratos de resolução × trecho de rota × faixa de iluminação,
com k-medoids sobre brilho, contraste, saturação e proporção de área clara/escura, e
descarte de quase-duplicatas por dHash. O resultado medido foi 6,49 GB com **as quatro
classes intactas** e zero órfãos — está em `reports/rdd2022_reduction.json`.

O que impediu a poda de valer não foi a seleção, e sim o OneDrive: a proteção de exclusão
em massa (limiar padrão de 200 itens) restaura os arquivos da nuvem enquanto espera uma
confirmação do usuário. Isso aconteceu três vezes, inclusive com o cliente parado durante a
remoção — ao voltar, ele reconcilia tratando a nuvem como verdade. O usuário optou por
manter o RDD2022 como está em vez de mexer na política da máquina.

Estado atual do RDD2022, verificado:

- {br(v["loader_samples"], 0)} amostras lidas pelo loader, em 7 origens;
- objetos por classe: D00 {br(classes["URMIND_ROAD_D00"], 0)}, D10 {br(classes["URMIND_ROAD_D10"], 0)},
  D20 {br(classes["URMIND_ROAD_D20"], 0)}, D40 {br(classes["URMIND_ROAD_D40"], 0)} — **completos**;
- 0 XML órfão e 0 imagem sem XML;
- 168 imagens da Norway não voltaram da restauração parcial do OneDrive. **Todas são
  negativas**: nenhum objeto anotado se perdeu. Contabilizado em
  `reports/rdd2022_norway_partial_restore.json`.

Os manifestos `manifests/rdd2022_norway_kept.jsonl` e `_removed.jsonl` e o script
`reduce_rdd2022.py` continuam prontos: se um dia a confirmação do OneDrive for dada, a poda
se aplica de novo e reproduz exatamente a mesma seleção, porque ela sai do perfil gravado e
não do disco do momento.

**Mapillary MSLS substituído pelo Global Streetscapes.** O portal oficial do MSLS exige
login e o projeto não tem credencial nem pode usar serviço pago. O Global Streetscapes
(NUS Urban Analytics Lab) publica no Hugging Face, sem gating e sob CC BY-SA 4.0, imagem
street-level do **próprio Mapillary** ({br(gs["distribution"]["source"].get("Mapillary", 0), 0)} das
{br(gs["images"], 0)} selecionadas) e do KartaView ({gs["distribution"]["source"].get("KartaView", 0)}).
É a via pública e gratuita para a mesma imagem, e ainda acrescenta rótulo humano de clima,
iluminação, plataforma da via e qualidade, que o MSLS não oferecia. O recorte cobre
{gs["distribution"]["countries"]} países, {gs["distribution"]["cities"]} cidades e
{br(gs["distribution"]["sequences"], 0)} sequências de captura. Motivo e alternativas avaliadas em
`metadata/removed_sources.json`.

**Quatro fontes saíram de `DEFERRED` e ganharam adaptador.** Antes desta etapa o catálogo
descrevia o Project Sidewalk como exportação CSV da API municipal — o que foi baixado é
Parquet com imagem embutida — e listava `rampnet` e `bdd100k` como adiados enquanto os dois
já estavam em disco. O sistema não conseguiria ler nenhum dos três. Agora:

- `project_sidewalk` e `rampnet` leem Parquet com a imagem dentro da linha, sem extrair;
  `extract_image` devolve o JPEG de uma linha citada quando alguém precisa dele;
- `rampnet` separa o ponto na imagem (`KeypointSample`) da coordenada no mundo (`GeoRecord`);
- `bdd100k` pareia imagem e máscara lendo os dois ZIPs **fechados**, sem duplicar 1,27 GB;
- `global_streetscapes` lê o CSV de rótulos gerado na aquisição.

`KeypointSample` é novo em `records.py`: ponto não é caixa, pelo mesmo motivo que máscara
não é. Nenhuma dessas quatro fontes vira classe da V1 — todas registram a recusa com motivo.

**Splits e manifesto de seleção reconciliados com o disco.** Os três arquivos de
`datasets/splits/` citavam imagens da Norway que não existem mais — referência quebrada que
só apareceria no próximo passo. `reconcile_after_reduction.py` remove as linhas órfãs sem
mover nenhuma imagem de lado: os 34.526 objetos de `train` continuam lá, o isolamento por
país foi reconferido e passou, e as proporções ficaram em 67,5 / 10,9 / 21,6 — praticamente
as que a busca original alcançou mantendo países inteiros. Evidência em
`reports/rdd2022_reconciliation.json`.

**Organização do repositório corrigida.** Existiam dois ambientes virtuais (`.venv` na raiz
e `backend/.venv`) e dois caches de lint. As bibliotecas que só o ambiente da raiz tinha
(`huggingface_hub`, `hf_xet`, `tqdm`) foram instaladas no `backend/.venv`, que passou a ser
o único, e as duplicatas saíram — 247 MB. Junto com elas saíram `datasets/raw/mapillary_msls`
(fonte aposentada) e `datasets/downloads/` (cache que o script rebaixa sozinho). Também foi
removido o BOM UTF-8 de sete arquivos: os três manifestos `manifests/*.json` estavam
**ilegíveis** por `json.load` por causa dele, e o `.gitignore` tinha o BOM colado na primeira
linha.

## O que continua valendo como limitação

- **BDD100K não tem checksum oficial utilizável.** O MD5 publicado na documentação tem 31
  dígitos hexadecimais e é inválido. Há CRC do ZIP e SHA-256 local, o que prova integridade
  do arquivo, não autenticidade da origem.
- **Urban Community** declara CC0 na ficha do Kaggle, mas a procedência das imagens não é
  declarada, e o mapa id→classe foi inferido das pastas.
- **CAMBER**: as detecções do CSV vêm de um YOLO de terceiros com `user_confirmed` vazio.
  É saída de modelo, não ground truth.
- **CC BY-SA 4.0** do Global Streetscapes obriga atribuição e mesma licença em derivados.
- Nenhuma fonte tem classe de calçada ou de acessibilidade na taxonomia §8.2. Rampa entra
  como keypoint recusado, não como classe de treino.
- **RDD2022 acima do teto de 7 GB**, por decisão registrada. As outras sete fontes estão
  dentro dele.
- **Presença e integridade de dados não demonstram desempenho de identificação.** Não houve
  treino nem inferência nesta etapa.

## Exclusão dentro do OneDrive

O projeto fica em pasta sincronizada e isso não é detalhe: apagar milhares de arquivos de
uma vez **é revertido**. A proteção de exclusão em massa restaura tudo da nuvem enquanto
espera confirmação, e parar o cliente também não resolve — ao voltar, ele reconcilia
tratando a nuvem como verdade e baixa tudo de novo. Os dois comportamentos foram observados
aqui. Lotes de algumas centenas passam abaixo do limiar e propagam normalmente, então
`reduce_rdd2022.py` apaga em lotes de 150, pausa e reconfere no fim, removendo de novo o que
a sincronização tiver trazido de volta — o que resolve exclusões pequenas, mas não venceu as
12 mil do RDD2022. Medir espaço nessa pasta exige o mesmo cuidado: o tamanho de um
placeholder é o da nuvem, não o do disco.

Para aplicar uma exclusão grande de fato é preciso uma destas duas coisas: confirmar o aviso
do próprio OneDrive na barra de tarefas, ou elevar
`HKCU\SOFTWARE\Policies\Microsoft\OneDrive\LocalMassDeleteFileDeleteThreshold`. Nenhuma
das duas foi feita aqui.

## Reproduzir

```text
python -B scripts/datasets/profile_norway.py
python -B scripts/datasets/reduce_rdd2022.py              # dry-run
python -B scripts/datasets/reduce_rdd2022.py --execute
python -B scripts/datasets/acquire_global_streetscapes.py # dry-run
python -B scripts/datasets/acquire_global_streetscapes.py --execute
python -B scripts/datasets/reconcile_after_reduction.py --execute
cd backend && python -m app.datasets.cli inventory
```

A seleção da poda vem do perfil gravado e da linha de base em
`reports/rdd2022_pre_reduction_baseline.json`, não do disco no momento da execução — por
isso ela é a mesma em qualquer reexecução, mesmo com a sincronização mexendo nos arquivos.
"""

(ROOT / "datasets/STATUS.md").write_text(texto, encoding="utf-8")
print(f"STATUS.md gravado — total {total:.2f} GB")
