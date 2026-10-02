"""Trava: regra de negócio não usa o dia do SERVIDOR — usa o de Brasília.

O servidor e o banco rodam em UTC. `date.today()`, `datetime.now()` sem fuso e o
`current_date` do Postgres viram o dia às 21h de Brasília, e cada um deles já
virou bug de cliente: o lançamento 733 (22/08/2026, ver `finance/relogio.py`), a
baixa, o link da reforma e o CNO (#878, 27/09/2026). O jeito certo existe desde
agosto — `finance.relogio.hoje()` / `agora()` / `dia_br()` — e mesmo assim o
errado continuava entrando, porque nada impedia.

Este teste impede. Ele lê o código (sem rodar nada) e conta, por arquivo:

* `date.today()`, `datetime.today()`, `datetime.utcnow()` e `datetime.now()`
  SEM fuso — em qualquer grafia (`_date.today()`, `dt.date.today()`...);
* nas strings SQL: `current_date`, `localtimestamp`, `now()::date`,
  `current_timestamp::date` e o corte de um INSTANTE no dia do banco —
  `coluna_em::date` / `inicio::date` (as colunas timestamptz desta base terminam
  em `_em`; o jeito certo é `(coluna_em at time zone 'America/Sao_Paulo')::date`).

Comentário, docstring e comentário SQL (`--`) não contam — explicar o bug não é
cometê-lo.

A contagem de HOJE está em `tests/dados/fuso_excecoes.json`. É uma catraca:

* arquivo com MAIS ocorrências do que lá → falha: use o relógio de Brasília;
* arquivo com MENOS → falha também, pedindo pra baixar o número. Assim a lista só
  encolhe e ninguém "gasta" a folga que um conserto deixou.

Quando o uso é técnico de verdade (nome de arquivo, log), ele pode ficar — mas
aí o número no json sobe no mesmo PR, à vista de quem revisa.

POR QUE O BANCO NÃO VAI PARA BRASÍLIA: dezenas de consultas já descontam 3h à mão
(`(inicio - interval '3 hours')::date`, `to_char(e.inicio - interval '3
hours', ...)`, `dt + timedelta(hours=-3)`) contando com o banco em UTC. Pôr a
sessão em Brasília descontaria duas vezes. A conexão fixa UTC (db/conexao.py) e
o dia do Brasil vem do `relogio` — ou de `at time zone 'America/Sao_Paulo'`.
"""
import ast
import json
import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
PASTAS = ("finance", "web", "core", "services", "db", "contas")
EXCECOES = RAIZ / "tests" / "dados" / "fuso_excecoes.json"

_SQL_PROIBIDO = ("current_date", "localtimestamp", "now()::date", "current_timestamp::date")
#: instante cortado no dia do banco (UTC): `criado_em::date`, `e.inicio::date`
_SQL_CORTE_UTC = re.compile(r"\b(?:\w+\.)?(?:\w+_em|inicio)::date\b")
_COMENTARIO_SQL = re.compile(r"--[^\n]*")


def _docstrings(arvore) -> set:
    ids = set()
    for no in ast.walk(arvore):
        if isinstance(no, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            corpo = no.body
            if corpo and isinstance(corpo[0], ast.Expr) and isinstance(corpo[0].value, ast.Constant) \
                    and isinstance(corpo[0].value.value, str):
                ids.add(id(corpo[0].value))
    return ids


def _nomes(arvore):
    """Que nomes do arquivo apontam para `datetime.date`, `datetime.datetime` e
    para o próprio módulo `datetime` — com ou sem apelido."""
    eh_date, eh_datetime, eh_modulo = set(), set(), set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.ImportFrom) and no.module == "datetime":
            for a in no.names:
                if a.name == "date":
                    eh_date.add(a.asname or "date")
                elif a.name == "datetime":
                    eh_datetime.add(a.asname or "datetime")
        elif isinstance(no, ast.Import):
            for a in no.names:
                if a.name == "datetime":
                    eh_modulo.add(a.asname or "datetime")
    return eh_date, eh_datetime, eh_modulo


def _qual_classe(no, eh_date, eh_datetime, eh_modulo):
    if isinstance(no, ast.Name):
        if no.id in eh_date:
            return "date"
        if no.id in eh_datetime:
            return "datetime"
    if isinstance(no, ast.Attribute) and isinstance(no.value, ast.Name) \
            and no.value.id in eh_modulo and no.attr in ("date", "datetime"):
        return no.attr
    return None


def ocorrencias(fonte: str) -> list[str]:
    """Cada uso do relógio do servidor no arquivo, como 'linha: o quê'."""
    arvore = ast.parse(fonte)
    docs = _docstrings(arvore)
    eh_date, eh_datetime, eh_modulo = _nomes(arvore)
    achados = []
    for no in ast.walk(arvore):
        if isinstance(no, ast.Call) and isinstance(no.func, ast.Attribute):
            classe = _qual_classe(no.func.value, eh_date, eh_datetime, eh_modulo)
            metodo = no.func.attr
            sem_fuso = not no.args and not no.keywords
            if (classe == "date" and metodo == "today") or \
               (classe == "datetime" and (metodo in ("today", "utcnow") or (metodo == "now" and sem_fuso))):
                achados.append(f"{no.lineno}: {classe}.{metodo}()")
        elif isinstance(no, ast.Constant) and isinstance(no.value, str) and id(no) not in docs:
            texto = " ".join(_COMENTARIO_SQL.sub("", no.value).lower().split())
            for proibido in _SQL_PROIBIDO:
                achados += [f"{no.lineno}: {proibido}"] * texto.count(proibido)
            achados += [f"{no.lineno}: {m}" for m in _SQL_CORTE_UTC.findall(texto)]
    return achados


def contagem_atual() -> dict:
    out = {}
    for pasta in PASTAS:
        for arq in sorted((RAIZ / pasta).rglob("*.py")):
            if "node_modules" in arq.parts:
                continue
            achados = ocorrencias(arq.read_text(encoding="utf-8"))
            if achados:
                out[arq.relative_to(RAIZ).as_posix()] = len(achados)
    return out


def test_ninguem_novo_usa_o_dia_do_servidor():
    permitido = json.loads(EXCECOES.read_text(encoding="utf-8"))
    atual = contagem_atual()
    novos = []
    for arq, n in sorted(atual.items()):
        if n > permitido.get(arq, 0):
            achados = "\n      ".join(ocorrencias((RAIZ / arq).read_text(encoding="utf-8")))
            novos.append(f"  {arq}: {n} (a lista permite {permitido.get(arq, 0)})\n      {achados}")
    assert not novos, (
        "Uso NOVO do relógio do servidor (UTC). Das 21h à meia-noite ele já é o dia "
        "seguinte no Brasil. Use `finance.relogio.hoje()` / `agora()` / `dia_br()` no "
        "Python e passe o dia de Brasília como parâmetro no SQL (ou use "
        "`at time zone 'America/Sao_Paulo'`). Se for uso técnico de verdade (log, "
        "nome de arquivo), suba o número em tests/dados/fuso_excecoes.json no mesmo PR.\n"
        + "\n".join(novos))


def test_a_lista_de_excecoes_so_encolhe():
    permitido = json.loads(EXCECOES.read_text(encoding="utf-8"))
    atual = contagem_atual()
    sobrando = {arq: (n, atual.get(arq, 0)) for arq, n in permitido.items() if atual.get(arq, 0) < n}
    assert not sobrando, (
        "Alguém consertou uso do relógio do servidor — ótimo. Baixe o número em "
        "tests/dados/fuso_excecoes.json (ou apague a linha), senão a folga fica "
        "aberta pro próximo erro:\n"
        + "\n".join(f"  {arq}: lista diz {n}, o código tem {a}" for arq, (n, a) in sorted(sobrando.items())))


def test_a_trava_enxerga_cada_grafia():
    fonte = '''
"""docstring com date.today() e current_date não conta"""
import datetime as dt
from datetime import date as _date, datetime, timezone
# comentário com date.today() não conta
a = _date.today()
b = datetime.now()
c = datetime.utcnow()
d = dt.date.today()
e = dt.datetime.now()
f = datetime.now(timezone.utc)          # com fuso: pode
g = "select 1 where dia = current_date and x = now()::date"
h = "select (criado_em at time zone 'America/Sao_Paulo')::date"   # pode
i = "select e.inicio::date, cv.criado_em::date, %s::date from x"  # o %s::date pode
j = "select 1 -- comentário com current_date e criado_em::date não conta"
'''
    achados = [a.split(": ", 1)[1] for a in ocorrencias(fonte)]
    assert sorted(achados) == sorted([
        "date.today()", "datetime.now()", "datetime.utcnow()", "date.today()",
        "datetime.now()", "current_date", "now()::date", "e.inicio::date",
        "cv.criado_em::date"])


def test_a_conexao_do_app_fixa_utc(monkeypatch, test_db_url):
    """O combinado com o banco é UTC (ver o docstring deste arquivo). A opção vai
    na abertura da conexão, e o Postgres tem de aceitá-la."""
    import psycopg

    from db import conexao
    pegou = {}

    class _Pool:
        def __init__(self, url, **kw):
            pegou.update(kw)

    monkeypatch.setattr(conexao, "_pool", None)
    monkeypatch.setattr(conexao, "_PoolComConta", _Pool)
    monkeypatch.setenv("DATABASE_URL", test_db_url)
    conexao.get_pool()
    opcoes = pegou["kwargs"]["options"]
    assert "-c TimeZone=UTC" in opcoes
    with psycopg.connect(test_db_url, options=opcoes) as c:
        assert c.execute("select current_setting('TimeZone')").fetchone()[0] == "UTC"
