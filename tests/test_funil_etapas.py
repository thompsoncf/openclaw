"""Etapas do funil personalizáveis por conta (migração 130):

- _etapas semeia o padrão na 1ª vez (novo/ganho/perdido fixas);
- adicionar cria etapa nova de miolo (chave única, ordem < ganho);
- renomear muda o rótulo (inclusive das fixas — a chave é estável);
- remover só quando a etapa não é fixa E está sem leads;
- mover reordena o miolo.

Banco dedicado e descartável com o schema mínimo (mesmo padrão do teste de blindagem).
"""
import os
from types import SimpleNamespace

import pytest
from psycopg_pool import ConnectionPool
from starlette.responses import RedirectResponse

from web import painel_prospeccao as pp

_BASE_SQL = """
create table contas (id bigserial primary key, tipo text, nome text);
create table prospeccao (id bigserial primary key, conta_id bigint, status text, estagio text);
create table funil_etapas (id bigserial primary key, conta_id bigint, chave text, rotulo text,
  ordem int not null default 0, fixa boolean not null default false,
  criado_em timestamptz not null default now(), unique (conta_id, chave));
"""


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_funil_etapas_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_BASE_SQL)
        c.commit()
    yield p
    p.close()


def _conta(c, nome):
    return c.execute("insert into contas (tipo, nome) values ('pj',%s) returning id", (nome,)).fetchone()[0]


def _ctx(conta):
    return {"conta_id": conta, "membro_id": 1, "gerencia": True, "pode_atribuir": True}


def _mp(monkeypatch, pool, conta):
    monkeypatch.setattr(pp, "get_pool", lambda: pool)
    monkeypatch.setattr(pp, "_acesso", lambda req: (_ctx(conta), None))
    return SimpleNamespace(session={})


def test_seed_padrao(pool):
    with pool.connection() as c:
        conta = _conta(c, "Seed")
        et = pp._etapas(c, conta)
    chaves = [e["chave"] for e in et]
    assert chaves == ["novo", "contatado", "qualificado", "proposta", "ganho", "perdido"]
    fixas = {e["chave"] for e in et if e["fixa"]}
    assert fixas == {"novo", "ganho", "perdido"}
    # 2ª chamada não duplica
    with pool.connection() as c:
        assert len(pp._etapas(c, conta)) == 6


def test_adicionar_etapa(pool, monkeypatch):
    with pool.connection() as c:
        conta = _conta(c, "Add")
    req = _mp(monkeypatch, pool, conta)
    resp = pp.prospeccao_etapa_nova(req, rotulo="Reunião marcada")
    assert isinstance(resp, RedirectResponse)
    with pool.connection() as c:
        et = pp._etapas(c, conta)
    nova = next(e for e in et if e["rotulo"] == "Reunião marcada")
    assert nova["fixa"] is False
    assert nova["ordem"] < pp._ORDEM_GANHO          # entra no miolo, antes de Ganho
    assert nova["chave"].startswith("reuniao")      # slug do rótulo
    # e a ordem geral mantém Ganho/Perdido no fim
    assert [e["chave"] for e in et][-2:] == ["ganho", "perdido"]


def test_remover_bloqueado_com_lead(pool, monkeypatch):
    with pool.connection() as c:
        conta = _conta(c, "Trava")
        et = pp._etapas(c, conta)
        cont = next(e for e in et if e["chave"] == "contatado")
        c.execute("insert into prospeccao (conta_id, status, estagio) values (%s,'contatado','lead')", (conta,))
        c.commit()
    req = _mp(monkeypatch, pool, conta)
    pp.prospeccao_etapa_remover(req, eid=cont["id"])
    assert "mova" in req.session["prosp_aviso"].lower()
    with pool.connection() as c:
        ainda = c.execute("select 1 from funil_etapas where id=%s", (cont["id"],)).fetchone()
    assert ainda is not None                        # não removeu


def test_remover_ok_sem_lead(pool, monkeypatch):
    with pool.connection() as c:
        conta = _conta(c, "RemoveOk")
        et = pp._etapas(c, conta)
        qual = next(e for e in et if e["chave"] == "qualificado")
    req = _mp(monkeypatch, pool, conta)
    pp.prospeccao_etapa_remover(req, eid=qual["id"])
    with pool.connection() as c:
        foi = c.execute("select 1 from funil_etapas where id=%s", (qual["id"],)).fetchone()
    assert foi is None
    assert "removida" in req.session["prosp_aviso"].lower()


def test_fixa_nao_remove_mas_renomeia(pool, monkeypatch):
    with pool.connection() as c:
        conta = _conta(c, "Fixa")
        et = pp._etapas(c, conta)
        novo = next(e for e in et if e["chave"] == "novo")
    req = _mp(monkeypatch, pool, conta)
    # remover fixa: bloqueado
    pp.prospeccao_etapa_remover(req, eid=novo["id"])
    assert "fixa" in req.session["prosp_aviso"].lower()
    # renomear fixa: permitido (chave continua 'novo')
    pp.prospeccao_etapa_renomear(req, eid=novo["id"], rotulo="Entrada")
    with pool.connection() as c:
        r = c.execute("select chave, rotulo from funil_etapas where id=%s", (novo["id"],)).fetchone()
    assert r == ("novo", "Entrada")


def test_mover_reordena_miolo(pool, monkeypatch):
    with pool.connection() as c:
        conta = _conta(c, "Mover")
        et = pp._etapas(c, conta)
        cont = next(e for e in et if e["chave"] == "contatado")
    req = _mp(monkeypatch, pool, conta)
    pp.prospeccao_etapa_mover(req, eid=cont["id"], dir="dir")   # contatado passa qualificado
    with pool.connection() as c:
        ordem = [e["chave"] for e in pp._etapas(c, conta)]
    assert ordem.index("qualificado") < ordem.index("contatado")
    assert ordem[0] == "novo" and ordem[-2:] == ["ganho", "perdido"]


def test_nao_gerencia_nao_edita(pool, monkeypatch):
    with pool.connection() as c:
        conta = _conta(c, "SemPerm")
        et = pp._etapas(c, conta)
    monkeypatch.setattr(pp, "get_pool", lambda: pool)
    monkeypatch.setattr(pp, "_acesso", lambda req: ({"conta_id": conta, "membro_id": 9,
                                                     "gerencia": False, "pode_atribuir": False}, None))
    req = SimpleNamespace(session={})
    pp.prospeccao_etapa_nova(req, rotulo="Hack")
    with pool.connection() as c:
        n = c.execute("select count(*) from funil_etapas where conta_id=%s", (conta,)).fetchone()[0]
    assert n == len(et)     # nada foi adicionado
