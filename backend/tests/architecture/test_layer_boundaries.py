"""Fronteiras da arquitetura oficial descrita no MASTER_PLAN e no README."""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

BACKEND = Path(__file__).resolve().parents[2]
APP = BACKEND / "app"
REPO = BACKEND.parent


def python_files(package: Path) -> list[Path]:
    return sorted(
        path for path in package.rglob("*.py") if "__pycache__" not in path.parts
    )


def imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module)
    return found


@pytest.mark.parametrize("path", python_files(APP / "api"), ids=lambda path: path.name)
def test_api_does_not_access_persistence_directly(path: Path) -> None:
    """Endpoints validam HTTP e delegam; acesso a dados fica nos repositórios."""
    imported = imports(path)
    forbidden = {
        name
        for name in imported
        if name.startswith(("sqlalchemy", "geoalchemy2", "psycopg", "app.models", "app.db"))
    }
    assert not forbidden, f"{path.name} acessa persistência: {sorted(forbidden)}"


@pytest.mark.parametrize("path", python_files(APP / "services"), ids=lambda path: path.name)
def test_services_do_not_depend_on_http(path: Path) -> None:
    """Regras e orquestração do produto não dependem de FastAPI ou Starlette."""
    forbidden = {
        name
        for name in imports(path)
        if name.split(".")[0] in {"fastapi", "starlette"}
    }
    assert not forbidden, f"{path.name} depende de HTTP: {sorted(forbidden)}"


def test_only_the_canonical_repository_module_executes_queries() -> None:
    repository_modules = {path.name for path in python_files(APP / "repositories")}
    assert repository_modules == {"__init__.py", "core.py"}


def _has_real_content(path) -> bool:
    """O caminho descartado voltou de fato — ou é só cache do interpretador?

    `backend/migrations` reapareceu contendo unicamente
    `__pycache__/env.cpython-313.pyc`: bytecode órfão de um `env.py` que não
    existe mais. Bastava alguém ter importado o módulo antes da remoção para o
    arquivo ficar lá. Tratar isso como "a arquitetura antiga voltou" é alarme
    falso, e alarme falso que não se consegue apagar ensina a ignorar o teste.

    Arquivo isolado conta sempre. Diretório conta quando tem algum arquivo que
    não seja cache Python — uma migration de verdade continua reprovando.
    """
    if not path.exists():
        return False
    if path.is_file():
        return True
    return any(
        p.is_file() and p.suffix not in (".pyc", ".pyo")
        for p in path.rglob("*")
    )


def test_deprecated_architecture_does_not_return() -> None:
    """O passo 0 do plano elimina a infraestrutura e o domínio paralelos antigos."""
    deprecated_paths = (
        REPO / "compose.yaml",
        REPO / "compose.yml",
        REPO / "Makefile",
        REPO / "infrastructure",
        REPO / "ml",
        REPO / "scout-agent",
        BACKEND / "migrations",
        APP / "db" / "models.py",
        APP / "integrations" / "auth" / "keycloak.py",
        APP / "integrations" / "storage" / "seaweedfs.py",
    )
    present = [
        path.relative_to(REPO).as_posix()
        for path in deprecated_paths
        if _has_real_content(path)
    ]
    assert not present, f"Caminhos da arquitetura descartada reapareceram: {present}"


# --------------------------------------------------------- contrato de artefatos


def _contract() -> dict:
    yaml = pytest.importorskip("yaml")
    caminho = REPO / "datasets" / "metadata" / "artifact_contract.yaml"
    return yaml.safe_load(caminho.read_text(encoding="utf-8"))


def _registry() -> dict:
    return json.loads(
        (REPO / "datasets" / "metadata" / "artifact_registry.json").read_text(
            encoding="utf-8-sig"
        )
    )


def _required_artifacts() -> list[str]:
    """Os artefatos que o validador de proveniência cobra, lidos dele mesmo."""
    sys.path.insert(0, str(REPO / "scripts" / "datasets"))
    import validate_manifests

    return list(validate_manifests.DERIVED)


# Quatro derivadas do RDD2022 nasceram antes de `_core.provenance()` existir e
# não declaram `script`. A política do projeto é não reescrever artefato
# histórico — reescrever a posteriori um relatório que registrou uma execução
# real transforma evidência em narrativa —, e `reduce_rdd2022.py` é destrutivo,
# então regerá-las não é uma opção barata. Ficam nomeadas aqui para que a dívida
# seja explícita e, principalmente, para que ela não CRESÇA: qualquer artefato
# novo sem produtor reprova. `derived_manifest_validation.json` continua
# reportando as quatro a cada execução.
SEM_PRODUTOR_HISTORICO = frozenset(
    {
        "reports/rdd2022_reduction.json",
        "reports/rdd2022_reconciliation.json",
        "reports/rdd2022_subset_proposal.json",
        "splits/rdd2022_subset_splits.json",
    }
)


def _declared_producer(relativo: str) -> str | None:
    """Quem diz ter produzido o artefato — o próprio artefato responde.

    `_core.provenance()` grava `script` em toda derivada. Perguntar ao arquivo
    evita uma segunda tabela de "quem produz o quê", que envelheceria sozinha.

    Manifesto `.jsonl` é a exceção estrutural: ele é uma linha por registro e não
    tem onde guardar cabeçalho. Quem responde por ele é o relatório companheiro,
    que grava o caminho e o SHA-256 do manifesto que acabou de escrever.
    """
    caminho = REPO / "datasets" / relativo
    if not caminho.is_file() or caminho.suffix == ".jsonl":
        return None
    payload = json.loads(caminho.read_text(encoding="utf-8-sig"))
    return payload.get("script") or payload.get("generated_by")


def _producer_of_jsonl(relativo: str, ordem: list[str]) -> str | None:
    """Etapa da ordem cujo relatório cita este manifesto `.jsonl`."""
    for etapa in ordem:
        for report in (REPO / "datasets" / "reports").glob("*.json"):
            texto = report.read_text(encoding="utf-8-sig")
            if relativo in texto and f'"script": "{etapa}"' in texto:
                return etapa
    return None


def test_todo_artefato_obrigatorio_tem_produtor_conhecido():
    """Artefato cobrado sem produtor declarado é órfão com aparência de etapa."""
    sem_produtor = [
        rel
        for rel in _required_artifacts()
        if (REPO / "datasets" / rel).is_file()
        and not _declared_producer(rel)
        and rel not in SEM_PRODUTOR_HISTORICO
    ]

    assert not sem_produtor, f"artefatos sem `script`/`generated_by`: {sem_produtor}"


def test_a_divida_historica_de_proveniencia_nao_cresce():
    """A lista de artefatos sem produtor é para encolher, nunca para aumentar."""
    atuais = {
        rel
        for rel in _required_artifacts()
        if (REPO / "datasets" / rel).is_file() and not _declared_producer(rel)
    }

    assert atuais <= SEM_PRODUTOR_HISTORICO, (
        f"artefato novo sem produtor declarado: {sorted(atuais - SEM_PRODUTOR_HISTORICO)}"
    )


def test_produtor_de_artefato_obrigatorio_existe_e_esta_registrado():
    registrados = {
        item.get("path") or item.get("script")
        for grupo in _registry().values()
        if isinstance(grupo, list)
        for item in grupo
        if isinstance(item, dict)
    }
    faltando = []
    for rel in _required_artifacts():
        produtor = _declared_producer(rel)
        if not produtor:
            continue  # coberto por `test_a_divida_historica_de_proveniencia_nao_cresce`
        if not (REPO / produtor).is_file():
            faltando.append(f"{rel}: produtor {produtor} não existe em disco")
        elif produtor not in registrados:
            faltando.append(f"{rel}: produtor {produtor} fora do registry")

    assert not faltando, faltando


@pytest.mark.parametrize(
    "pipeline", ["univali_derived_pipeline", "urban_community_derived_pipeline"]
)
def test_nenhum_artefato_do_pipeline_fica_orfao(pipeline):
    """Todo arquivo que o pipeline declara precisa ter uma etapa que o produza.

    Era exatamente o que faltava: `univali_human_audit_status.json` era cobrado
    pelo validador e `prepare_univali_human_audit.py` não aparecia em `order`.
    O artefato existia, o produtor existia, e nada ligava um ao outro.
    """
    contrato = _contract()[pipeline]
    ordem = contrato["order"]

    orfaos = []
    for chave, valor in contrato.items():
        if chave == "order" or not isinstance(valor, str):
            continue
        if not valor.startswith(("datasets/reports/", "datasets/manifests/", "datasets/splits/")):
            continue
        if not valor.endswith((".json", ".jsonl")):
            continue
        caminho = REPO / valor
        if not caminho.is_file():
            continue
        relativo = valor.removeprefix("datasets/")
        produtor = _declared_producer(relativo) or _producer_of_jsonl(valor, ordem)
        if produtor is None:
            orfaos.append(f"{chave}={valor} sem etapa que declare tê-lo produzido")
        elif produtor not in ordem:
            orfaos.append(f"{chave}={valor} produzido por {produtor}, fora de `order`")

    assert not orfaos, orfaos


def test_a_auditoria_humana_do_univali_esta_na_ordem_e_antes_do_split():
    """A dependência real: a folha usa os painéis, e o split usa o status dela."""
    ordem = _contract()["univali_derived_pipeline"]["order"]

    preparo = ordem.index("scripts/datasets/prepare_univali_human_audit.py")
    visual = ordem.index("scripts/datasets/render_univali_audit.py")
    split = ordem.index("scripts/datasets/make_univali_splits.py")

    assert visual < preparo < split


@pytest.mark.parametrize(
    "pipeline", ["univali_derived_pipeline", "urban_community_derived_pipeline"]
)
def test_toda_etapa_do_pipeline_existe_e_esta_registrada(pipeline):
    registrados = {
        item.get("path") or item.get("script")
        for grupo in _registry().values()
        if isinstance(grupo, list)
        for item in grupo
        if isinstance(item, dict)
    }
    problemas = [
        etapa
        for etapa in _contract()[pipeline]["order"]
        if not (REPO / etapa).is_file() or etapa not in registrados
    ]

    assert not problemas, f"etapas inexistentes ou fora do registry: {problemas}"


# ------------------------------------------------- geradores não fixam data


GENERATORS = ("refresh_status.py", "refresh_sources.py", "refresh_readiness.py")


@pytest.mark.parametrize("script", GENERATORS)
def test_gerador_nao_tem_data_escrita_a_mao(script):
    """Data no código envelhece sozinha e desacredita os números que estão certos.

    `refresh_status.py` publicava `Atualizado em 2026-09-09` como literal: um
    STATUS recém-gerado já nascia dizendo ser de dias antes. Quem lê não tem como
    saber se o resto do documento também ficou para trás.
    """
    caminho = REPO / "scripts" / "datasets" / script
    if not caminho.is_file():
        pytest.skip(f"{script} não existe neste checkout")

    # Data em docstring é NARRATIVA: registra quando algo aconteceu
    # ("mapillary_msls, aposentado em 2026-09-08") e é informação legítima. O que
    # não pode existir é data em string de código, porque essa chega à saída e
    # afirma quando o artefato foi gerado — afirmação que mente no dia seguinte.
    arvore = ast.parse(caminho.read_text(encoding="utf-8"), filename=str(caminho))
    docstrings = {
        id(no.body[0].value)
        for no in ast.walk(arvore)
        if isinstance(no, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        and no.body
        and isinstance(no.body[0], ast.Expr)
        and isinstance(no.body[0].value, ast.Constant)
        and isinstance(no.body[0].value.value, str)
    }
    # `removed_at` de uma fonte aposentada é fato datado: mudá-lo para hoje
    # seria mentir sobre quando a remoção aconteceu. A exceção é nominal e
    # pequena de propósito — todo campo novo com data precisa ser discutido.
    fatos_historicos = ("2026-09-08",)
    padrao = re.compile(r"\b20\d{2}-\d{2}-\d{2}\b")
    suspeitas = [
        no.value
        for no in ast.walk(arvore)
        if isinstance(no, ast.Constant)
        and isinstance(no.value, str)
        and id(no) not in docstrings
        and padrao.search(no.value)
        and no.value not in fatos_historicos
    ]

    assert not suspeitas, f"{script} tem data escrita à mão no código: {suspeitas}"


def test_status_publica_a_data_de_hoje():
    """O documento gerado precisa datar a si mesmo pelo relógio, não pelo teclado."""
    caminho = REPO / "datasets" / "STATUS.md"
    if not caminho.is_file():
        pytest.skip("STATUS.md ainda não gerado")

    cabecalho = caminho.read_text(encoding="utf-8")[:400]
    gerado = re.search(r"Atualizado em (\d{4}-\d{2}-\d{2})", cabecalho)

    assert gerado, "STATUS.md não declara a data de geração"
    assert gerado.group(1) >= "2026-09-12", (
        "STATUS.md está datado antes da última regeneração conhecida; "
        "provavelmente voltou a ter data fixa"
    )


# --------------------------------------------- registro de scripts sincronizado


def _sha_file(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _registry_drift(entradas: list[dict], raiz: Path, em_disco: list[Path]) -> dict:
    """Mesma regra de `refresh_registry.py --check`, sobre um estado qualquer."""
    registrados = {e["path"]: e.get("sha256") for e in entradas}
    atuais = {p.relative_to(raiz).as_posix(): _sha_file(p) for p in em_disco}
    return {
        "stale": sorted(c for c, d in registrados.items() if c in atuais and atuais[c] != d),
        "missing_on_disk": sorted(c for c in registrados if c not in atuais),
        "unregistered": sorted(c for c in atuais if c not in registrados),
    }


def _script(tmp_path: Path, nome: str, texto: str) -> Path:
    caminho = tmp_path / "scripts" / nome
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(texto, encoding="utf-8")
    return caminho


def test_registro_com_hash_correto_nao_acusa_nada(tmp_path):
    a = _script(tmp_path, "a.py", "x = 1\n")
    entradas = [{"path": "scripts/a.py", "sha256": _sha_file(a)}]

    assert _registry_drift(entradas, tmp_path, [a]) == {
        "stale": [],
        "missing_on_disk": [],
        "unregistered": [],
    }


def test_script_alterado_com_registro_antigo_e_stale(tmp_path):
    a = _script(tmp_path, "a.py", "x = 1\n")
    entradas = [{"path": "scripts/a.py", "sha256": _sha_file(a)}]
    a.write_text("x = 2\n", encoding="utf-8")

    assert _registry_drift(entradas, tmp_path, [a])["stale"] == ["scripts/a.py"]


def test_script_registrado_que_sumiu_e_acusado(tmp_path):
    entradas = [{"path": "scripts/sumiu.py", "sha256": "0" * 64}]

    assert _registry_drift(entradas, tmp_path, [])["missing_on_disk"] == ["scripts/sumiu.py"]


def test_script_sem_registro_e_acusado(tmp_path):
    novo = _script(tmp_path, "novo.py", "y = 1\n")

    assert _registry_drift([], tmp_path, [novo])["unregistered"] == ["scripts/novo.py"]


def test_registro_real_esta_sincronizado_com_os_scripts():
    """Estado real, calculado agora: nenhum hash esperado está escrito aqui."""
    em_disco = sorted(
        p for p in (REPO / "scripts" / "datasets").glob("*.py") if p.name != "__init__.py"
    )
    diferencas = _registry_drift(_registry().get("scripts", []), REPO, em_disco)

    assert diferencas == {"stale": [], "missing_on_disk": [], "unregistered": []}, (
        f"registro fora de sincronia: {diferencas}. "
        "Rode `python -B scripts/datasets/refresh_registry.py` depois da última edição."
    )
