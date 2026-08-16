"""O botão "Sinal recebido" do funil, pelas ROTAS de verdade.

Testar `agenda.confirmar_pre_reserva` direto não prova nada sobre a tela: já
aconteceu neste repo de o mecanismo estar certo e o botão ser um no-op porque a
rota não chamava ninguém. Aqui passa-se pelo HTTP:

  GET  /painel/servicos/lista        -> é ele que decide se o botão APARECE
  POST /painel/servicos/sinal-recebido -> é ele que firma a data

Banco dedicado e descartável, no padrão de tests/test_orcamento_excluir.py.
"""
import os
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from psycopg_pool import ConnectionPool
from starlette.middleware.sessions import SessionMiddleware

from finance import agenda as ag
from web import painel_servicos as ps

CONTA = 7
OUTRA = 8
BASE = Path(__file__).resolve().parent.parent / "db" / "migracoes"


@pytest.fixture()
def cliente(monkeypatch):
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_sinal_recebido"
    with admin.connection() as c:
        c.autocommit = True
        # conexão pendurada do caso anterior segura o drop; derruba antes de tentar
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity where datname=%s",
                  (dbname,))
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    pool = ConnectionPool(url, min_size=1, max_size=3, open=True,
                          kwargs={"prepare_threshold": None})
    with pool.connection() as c:
        c.execute("create table contas (id bigserial primary key, nome text)")
        # 098_agenda referencia membros (dono do compromisso); só a coluna importa aqui
        c.execute("create table membros (id bigserial primary key, conta_id bigint, "
                  "nome text, papel text)")
        # titulos existe aqui pra a busca do título do sinal RODAR de verdade: sem a
        # tabela, o erro seria engolido pelo try/except da rota e `titulo_baixado`
        # viria None por motivo errado. Nestes casos o contrato nunca é fechado, então
        # o certo é não achar título nenhum — e é isso que se quer provar.
        c.execute("""create table titulos (id bigserial primary key, conta_id bigint,
            tipo text, descricao text, contraparte text default '', valor_centavos int,
            vencimento date, status text default 'aberto', recorrente boolean default false,
            categoria text default '', lancamento_id bigint, pago_em date,
            criado_por bigint, orcamento_id bigint, parcela_idx int,
            criado_em timestamptz default now())""")
        c.commit()
    with pool.connection() as c:
        ps._garantir_tabela(c)          # cria orcamentos como em produção
    with pool.connection() as c:
        for nome in ("098_agenda.sql", "099_agenda_tipo.sql", "130_evento_desfecho.sql",
                     "131_evento_link_online.sql", "160_agenda_pre_reserva.sql",
                     "161_orcamento_sinal.sql"):
            c.execute((BASE / nome).read_text(encoding="utf-8"))
        c.execute("insert into contas (id, nome) values (%s,'Buffet Teste')", (CONTA,))
        c.execute("insert into contas (id, nome) values (%s,'Vizinha')", (OUTRA,))
        c.commit()

    monkeypatch.setattr(ps, "get_pool", lambda: pool)
    conta = [None] * 15
    conta[0], conta[11], conta[12], conta[14] = CONTA, True, True, True
    monkeypatch.setattr(ps, "conta_logada", lambda request: tuple(conta))

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="teste-de-sessao")
    app.include_router(ps.router)

    @app.post("/_entrar")
    async def _entrar(request: Request):
        request.session["papel"] = "dono"
        request.session["membro_id"] = None
        return {"ok": True}

    c = TestClient(app)
    c.pool = pool
    c.post("/_entrar")
    yield c
    pool.close()


def _orcamento_com_data_segurada(c, *, conta_id=CONTA, dias=3, sinal=181000):
    """Um orçamento de evento aprovado cuja data está SEGURADA esperando o sinal —
    o estado em que o botão precisa aparecer."""
    ate = ag.agora_brt() + timedelta(days=dias)
    ev = ag.criar_evento(c.pool, conta_id, "Casamento — Ana",
                         ag.agora_brt() + timedelta(days=30), pre_reserva_ate=ate)
    with c.pool.connection() as cx:
        oid = cx.execute(
            """insert into orcamentos (conta_id, cliente, empresa, status, criado_por,
                 setup_centavos, modo, evento_agenda_id, sinal_centavos)
               values (%s,'Ana','Ana','aprovada','',745000,'evento',%s,%s) returning id""",
            (conta_id, ev["id"], sinal)).fetchone()[0]
        cx.commit()
    return oid, ev["id"]


def _item(c, oid):
    itens = c.get("/painel/servicos/lista").json()["itens"]
    return next(i for i in itens if i["id"] == oid)


def test_lista_manda_o_que_o_botao_precisa(cliente):
    """Se a rota não mandar `pre_reserva_ate`, o botão simplesmente não é desenhado
    — foi assim que um painel já mostrou reserva que o botão não sabia usar."""
    oid, _ = _orcamento_com_data_segurada(cliente)
    it = _item(cliente, oid)
    assert it["pre_reserva_ate"] and it["sinal"] == "R$ 1.810,00"
    assert it["sinal_pago"] is False


def test_confirmar_firma_a_data_e_o_botao_some(cliente):
    oid, ev_id = _orcamento_com_data_segurada(cliente)
    r = cliente.post("/painel/servicos/sinal-recebido", json={"id": oid})
    assert r.status_code == 200
    # titulo_baixado None porque o contrato não foi fechado: não existe título ainda.
    # Quem dá a baixa nesse caminho é o próprio fechar_orcamento, depois.
    assert r.json() == {"ok": True, "ja_estava": False, "reserva_firmada": True,
                        "titulo_baixado": None}
    with cliente.pool.connection() as cx:
        assert cx.execute("select status, pre_reserva_ate from eventos_agenda where id=%s",
                          (ev_id,)).fetchone() == ("ativo", None)
        assert cx.execute("select sinal_pago_em from orcamentos where id=%s",
                          (oid,)).fetchone()[0] is not None
    # e a tela para de oferecer o botão sozinha (a subconsulta zera)
    it = _item(cliente, oid)
    assert it["pre_reserva_ate"] == "" and it["sinal_pago"] is True


def test_confirmar_duas_vezes_nao_quebra(cliente):
    """O botão é clicável de novo enquanto a resposta não volta; a segunda vez não
    pode ser erro nem desfazer nada."""
    oid, ev_id = _orcamento_com_data_segurada(cliente)
    cliente.post("/painel/servicos/sinal-recebido", json={"id": oid})
    r = cliente.post("/painel/servicos/sinal-recebido", json={"id": oid})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "ja_estava": True, "reserva_firmada": False,
                        "titulo_baixado": None}
    with cliente.pool.connection() as cx:
        assert cx.execute("select status from eventos_agenda where id=%s",
                          (ev_id,)).fetchone()[0] == "ativo"


def test_orcamento_de_outra_conta_nao_e_confirmado(cliente):
    """Escopo multi-tenant: o id vem da tela, e tela não é fonte confiável."""
    oid, ev_id = _orcamento_com_data_segurada(cliente, conta_id=OUTRA)
    r = cliente.post("/painel/servicos/sinal-recebido", json={"id": oid})
    assert r.status_code == 404
    with cliente.pool.connection() as cx:
        assert cx.execute("select status from eventos_agenda where id=%s",
                          (ev_id,)).fetchone()[0] == ag.PRE_RESERVADO


def test_orcamento_sem_data_segurada_nao_aparece_como_pendente(cliente):
    """Proposta recorrente (ou evento sem sinal) não pode ganhar o aviso âmbar."""
    with cliente.pool.connection() as cx:
        oid = cx.execute(
            """insert into orcamentos (conta_id, cliente, empresa, status, criado_por,
                 setup_centavos, modo) values (%s,'Clínica','Clínica','aprovada','',
                 920000,'recorrente') returning id""", (CONTA,)).fetchone()[0]
        cx.commit()
    it = _item(cliente, oid)
    assert it["pre_reserva_ate"] == "" and it["sinal"] == ""


def test_sinal_confirmado_mesmo_sem_agenda_nao_perde_o_pagamento(cliente):
    """A agenda é o segundo passo, não o primeiro: orçamento com sinal mas sem
    compromisso vinculado (caso raro — a reserva falhou na assinatura) ainda
    registra o pagamento."""
    with cliente.pool.connection() as cx:
        oid = cx.execute(
            """insert into orcamentos (conta_id, cliente, empresa, status, criado_por,
                 setup_centavos, modo, sinal_centavos)
               values (%s,'Bia','Bia','aprovada','',300000,'evento',50000) returning id""",
            (CONTA,)).fetchone()[0]
        cx.commit()
    r = cliente.post("/painel/servicos/sinal-recebido", json={"id": oid})
    assert r.status_code == 200 and r.json()["reserva_firmada"] is False
    with cliente.pool.connection() as cx:
        assert cx.execute("select sinal_pago_em from orcamentos where id=%s",
                          (oid,)).fetchone()[0] is not None
