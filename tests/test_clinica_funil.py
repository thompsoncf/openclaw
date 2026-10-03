"""A tela "Funil da clínica" (finance/clinica_funil.py, web/painel_clinica_funil.py).

O banco imita o da Espaço Pelle em 03/10/2026: o funil semeado pelo perfil genérico, com
"Agenda" e "Consulta" renomeados à mão pelo dono, 3 cartões em Novo e 1 em Agenda.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from psycopg_pool import ConnectionPool
from starlette.middleware.sessions import SessionMiddleware

from finance import clinica_funil as cfu
from finance import funil_modelo as fm
from tests.test_regua_tela import _SQL

CONTA = 39
AGORA = datetime(2026, 10, 3, 15, tzinfo=timezone.utc)


@pytest.fixture()
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_clinica_funil_teste"
    with admin.connection() as c:
        c.autocommit = True
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity where datname=%s and pid <> pg_backend_pid()",
                  (dbname,))
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        for chave, rotulo, ordem, fixa, sai, semeado in (
                ("novo", "Novo", 0, True, False, "recorrente"),
                ("contatado", "Contatado", 10, False, False, "recorrente"),
                ("follow_up", "Follow-up", 20, False, False, "recorrente"),
                ("qualificado", "Agenda", 30, False, False, fm.DO_DONO),
                ("proposta", "Consulta", 40, False, False, fm.DO_DONO),
                ("ganho", "Fechado", 900, True, True, "recorrente"),
                ("perdido", "Perdido", 910, True, False, "recorrente")):
            c.execute("""insert into funil_etapas (conta_id, chave, rotulo, ordem, fixa, sai_do_quadro, semeado_de)
                         values (%s,%s,%s,%s,%s,%s,%s)""", (CONTA, chave, rotulo, ordem, fixa, sai, semeado))
        for st in ("novo", "novo", "novo", "qualificado"):
            c.execute("insert into prospeccao (conta_id, status) values (%s,%s)", (CONTA, st))
        c.commit()
    yield p
    p.close()


TUDO = {"resposta": "observando", "prazo": "observando", "reabre": "ligado"}


def _etapa(c, chave):
    return c.execute("""select rotulo, fase, gatilho, gatilho_ativo, teto_dias, renovacoes_max, reativa_para
                          from funil_etapas where conta_id=%s and chave=%s""", (CONTA, chave)).fetchone()


def test_tudo_do_desenho_vem_marcado_inclusive_o_nome_do_dono(pool):
    with pool.connection() as c:
        e = cfu.estado(c, CONTA)
    assert not e["aplicado"]
    ids = {it["id"]: it for it in e["itens"]}
    for criar in ("criar:consulta", "criar:tratamento", "criar:retorno"):
        assert ids[criar]["marcado"]
    assert ids["rotulo:qualificado"]["para"] == "Agendado" and ids["rotulo:qualificado"]["marcado"]   # nome do dono
    assert ids["rotulo:proposta"]["para"] == "Plano ou orçamento enviado" and ids["rotulo:proposta"]["marcado"]
    assert ids["rotulo:ganho"]["para"] == "Concluído"
    assert all(it["marcado"] for it in e["itens"])
    assert e["cards"] == {"novo": 3, "qualificado": 1}


def test_aplicar_cria_as_colunas_e_liga_as_regras_sem_mexer_em_cartao(pool):
    with pool.connection() as c:
        itens = [it["id"] for it in cfu.estado(c, CONTA)["itens"]]
        _feito, erro = cfu.aplicar(c, CONTA, itens, TUDO)
        assert erro is None
        c.commit()
        assert cfu.aplicado(c, CONTA)
        assert _etapa(c, "tratamento")[1] == "pos" and _etapa(c, "retorno")[1] == "pos"
        assert _etapa(c, "qualificado")[0] == "Agendado" and _etapa(c, "consulta")[0] == "Consulta"
        assert _etapa(c, "contatado")[:6] == ("Em conversa", "venda", "resposta_nossa", True, 3, 2)
        assert _etapa(c, "perdido")[6] == "contatado"
        assert c.execute("select gatilhos_modo, teto_modo from funil_regua where conta_id=%s",
                         (CONTA,)).fetchone() == ("observando", "observando")
        assert dict(c.execute("select status, count(*) from prospeccao group by status").fetchall()) == \
            {"novo": 3, "qualificado": 1}                    # nenhum cartão mudou de coluna
        assert cfu.modos(c, CONTA) == TUDO
        assert cfu.estado(c, CONTA)["itens"] == []         # igual ao modelo


def test_sem_as_tres_colunas_nao_e_o_funil_da_clinica(pool):
    with pool.connection() as c:
        itens = [it["id"] for it in cfu.estado(c, CONTA)["itens"] if it["id"] != "criar:retorno"]
        _feito, erro = cfu.aplicar(c, CONTA, itens, TUDO)
        assert "marque as três" in erro
        c.rollback()
        assert not cfu.aplicado(c, CONTA)


def test_regras_so_depois_de_aplicar_e_desligar_desfaz(pool):
    with pool.connection() as c:
        assert "primeiro" in cfu.salvar_regras(c, CONTA, TUDO)
        cfu.aplicar(c, CONTA, [it["id"] for it in cfu.estado(c, CONTA)["itens"]], TUDO)
        assert "Escolha" in cfu.salvar_regras(c, CONTA, dict(TUDO, reabre="observando"))   # reabre não tem ensaio
        assert cfu.salvar_regras(c, CONTA, {"resposta": "off", "prazo": "off", "reabre": "off"}) is None
        assert _etapa(c, "contatado")[3:5] == (False, None)
        assert _etapa(c, "perdido")[6] is None
        assert cfu.modos(c, CONTA) == {"resposta": "off", "prazo": "off", "reabre": "off"}
        assert cfu.salvar_regras(c, CONTA, {"resposta": "ligado", "prazo": "ligado", "reabre": "ligado"}) is None
        assert cfu.modos(c, CONTA) == {"resposta": "ligado", "prazo": "ligado", "reabre": "ligado"}


def test_o_ensaio_conta_o_que_teria_feito_na_semana(pool):
    with pool.connection() as c:
        for lead, quando in ((1, AGORA - timedelta(days=1)), (1, AGORA - timedelta(days=2)), (2, AGORA - timedelta(days=3)),
                             (3, AGORA - timedelta(days=9))):
            c.execute("""insert into funil_movimentos (conta_id, prospeccao_id, de, para, motivo, criado_em)
                         values (%s,%s,'novo','contatado','simulado:resposta_nossa',%s)""", (CONTA, lead, quando))
        c.execute("""insert into funil_avisos (conta_id, prospeccao_id, estado, nivel, simulado, criado_em)
                     values (%s,1,'teto','aviso',true,%s), (%s,2,'teto','aviso',false,%s)""",
                  (CONTA, AGORA - timedelta(days=1), CONTA, AGORA - timedelta(days=1)))
        assert cfu.ensaio(c, CONTA, AGORA) == {"resposta": 2, "prazo": 1}


@pytest.fixture()
def cli(pool, monkeypatch):
    from web import painel_clinica_agenda as pa
    from web import painel_clinica_funil as pf
    monkeypatch.setattr(pf, "get_pool", lambda: pool)
    monkeypatch.setattr(pa, "conta_logada", lambda request: (CONTA,))
    monkeypatch.setattr(pa, "nicho_da_conta", lambda conta: "clinica")
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="teste")
    app.include_router(pf.router)

    @app.get("/_papel/{papel}")
    def _papel(request: Request, papel: str):
        request.session["papel"] = papel
        return {}
    c = TestClient(app, follow_redirects=False)
    c.get("/_papel/dono")
    return c


def test_tela_aplicar_e_depois_as_regras(cli, pool):
    html = cli.get("/painel/clinica/funil").text
    assert "Aplicar o funil da clínica" in html and "Plano ou orçamento enviado" in html and "Agendado" in html
    with pool.connection() as c:
        itens = [it["id"] for it in cfu.estado(c, CONTA)["itens"]]
    r = cli.post("/painel/clinica/funil", data={"item": itens, **TUDO})
    assert r.headers["location"] == "/painel/clinica/funil?aviso=aplicado"
    html = cli.get(r.headers["location"]).text
    assert "Nenhum cartão mudou de coluna" in html and "Salvar as regras" in html and "O ensaio" in html
    r = cli.post("/painel/clinica/funil/regras", data={"resposta": "ligado", "prazo": "observando", "reabre": "ligado"})
    assert r.headers["location"] == "/painel/clinica/funil?aviso=regras"
    with pool.connection() as c:
        assert cfu.modos(c, CONTA) == {"resposta": "ligado", "prazo": "observando", "reabre": "ligado"}


def test_vendedor_nao_abre_a_tela(cli):
    cli.get("/_papel/vendedor")
    assert cli.get("/painel/clinica/funil").headers["location"] == "/painel/clinica/agenda"
    assert cli.post("/painel/clinica/funil/regras", data=TUDO).headers["location"] == "/painel/clinica/agenda"
