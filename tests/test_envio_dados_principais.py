"""Nem proposta nem contrato saem sem os dados principais (regra do dono,
01/10/2026).

O caso que originou a regra: o contrato do orçamento nº 47 da Prime chegou na mão
da cliente com "⚠️ Campos sem valor neste contrato: cliente.doc" — ninguém tinha
informado o CPF. O dono: "o ideal é não deixar mais mandar nem orçamento e nem
contrato sem os dados principais preenchidos". Os principais, escolhidos por ele:
nome, CPF ou CNPJ, data e horário do evento (no orçamento de evento) e a forma de
pagamento de cada parcela.

Banco PRÓPRIO: a régua lê o cadastro (`clientes` + `pessoas`) e a ficha do lead
(`prospeccao`) pra achar o documento, e nas outras suítes essas tabelas não
existem — lá a consulta falharia e a régua liberaria (ela é tolerante), o que
faria estes testes passarem sem medir nada.
"""
from __future__ import annotations

import json
import os

import pytest
from psycopg_pool import ConnectionPool

from finance import contrato as ctr

SQL = """
create table clientes (id bigserial primary key, dono_id bigint, pessoa_id bigint);
create table pessoas (id bigserial primary key, cpf text, cnpj text);
create table orcamentos (id bigserial primary key, conta_id bigint, empresa text,
  cliente text, cnpj text, cpf text, evento jsonb, parcelas jsonb, modo text,
  canal text, cliente_id bigint);
create table prospeccao (id bigserial primary key, conta_id bigint, orcamento_id bigint,
  cnpj text, cpf text);
"""

CONTA = 34
PARCELAS_OK = [{"venc": "2026-11-01", "valor_centavos": 100000, "forma": "Pix"}]
EVENTO_OK = {"data": "2027-03-18", "inicio": "18:00"}


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_envio_dados_principais"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname} with (force)")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(SQL)
        c.commit()
    yield p
    p.close()


def _orc(pool, *, empresa="Natália Teste", cnpj=None, cpf="70022233344", evento=EVENTO_OK,
         parcelas=PARCELAS_OK, modo="evento", canal=None, cliente_id=None) -> int:
    with pool.connection() as c:
        oid = c.execute(
            """insert into orcamentos (conta_id, empresa, cnpj, cpf, evento, parcelas, modo,
                 canal, cliente_id) values (%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s)
               returning id""",
            (CONTA, empresa, cnpj, cpf, json.dumps(evento), json.dumps(parcelas), modo,
             canal, cliente_id)).fetchone()[0]
        c.commit()
    return oid


def test_completo_pode_mandar(pool):
    assert ctr.pendencias_pra_mandar(pool, CONTA, _orc(pool)) == []


def test_o_caso_do_47_sem_cpf_nao_sai(pool):
    assert ctr.pendencias_pra_mandar(pool, CONTA, _orc(pool, cpf=None)) == ["CPF ou CNPJ"]


def test_documento_incompleto_nao_vale(pool):
    assert "CPF ou CNPJ" in ctr.pendencias_pra_mandar(pool, CONTA, _orc(pool, cpf="123"))


def test_o_documento_do_cadastro_vale(pool):
    with pool.connection() as c:
        pid = c.execute("insert into pessoas (cpf) values ('70022233355') returning id").fetchone()[0]
        cid = c.execute("insert into clientes (dono_id, pessoa_id) values (%s,%s) returning id",
                        (CONTA, pid)).fetchone()[0]
        c.commit()
    assert ctr.pendencias_pra_mandar(pool, CONTA, _orc(pool, cpf=None, cliente_id=cid)) == []


def test_o_documento_da_ficha_do_lead_vale(pool):
    """O vendedor anota o CPF na ficha do app — é lá que ele chega pela conversa."""
    oid = _orc(pool, cpf=None)
    with pool.connection() as c:
        c.execute("insert into prospeccao (conta_id, orcamento_id, cpf) values (%s,%s,'70022233366')",
                  (CONTA, oid))
        c.commit()
    assert ctr.pendencias_pra_mandar(pool, CONTA, oid) == []


def test_cadastro_de_outra_conta_nao_vale(pool):
    with pool.connection() as c:
        pid = c.execute("insert into pessoas (cpf) values ('70022233377') returning id").fetchone()[0]
        cid = c.execute("insert into clientes (dono_id, pessoa_id) values (999,%s) returning id",
                        (pid,)).fetchone()[0]
        c.commit()
    assert ctr.pendencias_pra_mandar(pool, CONTA, _orc(pool, cpf=None, cliente_id=cid)) == [
        "CPF ou CNPJ"]


def test_sem_nome_ou_com_telefone_no_nome(pool):
    assert "nome do cliente" in ctr.pendencias_pra_mandar(pool, CONTA, _orc(pool, empresa=""))
    assert "nome do cliente" in ctr.pendencias_pra_mandar(
        pool, CONTA, _orc(pool, empresa="86998192489"))


def test_evento_sem_data_ou_com_hora_que_o_sistema_nao_le(pool):
    falta = ctr.pendencias_pra_mandar(pool, CONTA, _orc(pool, evento={"inicio": "18:00/23:40"}))
    assert falta == ["data do evento", "horário de início"]


def test_a_data_antiga_do_app_vale(pool):
    """O app gravava dd/mm/aaaa até 01/10/2026 (#945): não é falta."""
    assert ctr.pendencias_pra_mandar(
        pool, CONTA, _orc(pool, evento={"data": "18/03/2028", "inicio": "18h"})) == []


def test_recorrente_nao_pede_data_de_evento(pool):
    """A ZAQ vende mensalidade: orçamento dela nunca terá data de festa (regra 6)."""
    assert ctr.pendencias_pra_mandar(
        pool, CONTA, _orc(pool, modo="recorrente", evento=None, parcelas=None)) == []


def test_parcela_sem_forma_de_pagamento(pool):
    pl = PARCELAS_OK + [{"venc": "2026-12-01", "valor_centavos": 5000, "forma": ""}]
    assert ctr.pendencias_pra_mandar(pool, CONTA, _orc(pool, parcelas=pl)) == [
        "forma de pagamento das parcelas"]
    # parcela zerada é linha esquecida, não plano — não cobra forma dela
    pl0 = PARCELAS_OK + [{"venc": "", "valor_centavos": 0, "forma": ""}]
    assert ctr.pendencias_pra_mandar(pool, CONTA, _orc(pool, parcelas=pl0)) == []


def test_venda_de_estande_fica_fora(pool):
    """Outlet Chic: o cadastro do estande tem a conferência própria — não mistura."""
    assert ctr.pendencias_pra_mandar(
        pool, CONTA, _orc(pool, canal="pagina_stands", cpf=None, evento=None)) == []


def test_em_lote_responde_cada_um(pool):
    a, b = _orc(pool), _orc(pool, cpf=None)
    d = ctr.pendencias_em_lote(pool, CONTA, [a, b])
    assert d[a] == [] and d[b] == ["CPF ou CNPJ"]


# ------------------------------------------------------------ as portas de envio
def test_o_app_nao_manda_o_contrato_e_nem_tenta(pool, monkeypatch):
    from finance import cockpit as ck
    oid = _orc(pool, cpf=None)
    monkeypatch.setattr(ck, "contrato_do_orcamento",
                        lambda *a, **k: {"id": 1, "link": "https://x/contrato/t"})
    saiu = []
    monkeypatch.setattr(ck, "enviar_mensagem", lambda *a, **k: saiu.append(a) or {"ok": True})
    r = ck.enviar_contrato_conversa(pool, CONTA, 7, 1, oid)
    assert r["ok"] is False and r["faltam"] == ["CPF ou CNPJ"]
    assert "CPF ou CNPJ" in r["erro"] and saiu == []


def test_o_app_nao_manda_a_proposta_na_conversa(pool, monkeypatch):
    from finance import cockpit as ck
    oid = _orc(pool, cpf=None)
    monkeypatch.setattr(ck, "_orc_do_link", lambda *a, **k: oid)
    saiu = []
    monkeypatch.setattr(ck, "enviar_mensagem", lambda *a, **k: saiu.append(a) or {"ok": True})
    r = ck.enviar_proposta_conversa(pool, CONTA, 7, 1, "https://x/proposta/tok")
    assert r["ok"] is False and saiu == []


def test_completo_o_app_manda(pool, monkeypatch):
    from finance import cockpit as ck
    oid = _orc(pool)
    monkeypatch.setattr(ck, "contrato_do_orcamento",
                        lambda *a, **k: {"id": 1, "link": "https://x/contrato/t"})
    monkeypatch.setattr(ck, "enviar_mensagem", lambda *a, **k: {"ok": False, "erro": "sem chip"})
    r = ck.enviar_contrato_conversa(pool, CONTA, 7, 1, oid)
    assert r == {"ok": False, "erro": "sem chip"}, "passou da trava e tentou mandar"


def test_o_conferir_da_ia_recusa_sem_tirar_da_fila(monkeypatch):
    """A recusa vem ANTES de reivindicar: o orçamento continua esperando conferência."""
    from finance import ia_orcamento as iao
    monkeypatch.setattr(ctr, "pendencias_pra_mandar", lambda *a, **k: ["CPF ou CNPJ"])

    class _Pool:
        def connection(self):
            raise AssertionError("não podia ter tocado no banco")

    r = iao.mandar(_Pool(), CONTA, 7, 1)
    assert r["ok"] is False and r["faltam"] == ["CPF ou CNPJ"]


def test_toda_porta_de_envio_passa_pela_regua():
    """Porta nova esquecendo a trava falha aqui, em vez de mandar contrato
    incompleto pro cliente."""
    import inspect
    from finance import agente, cockpit as ck, ia_orcamento as iao
    from web import painel_servicos as ps, painel_cockpit as pc
    for f in (ck.enviar_proposta_conversa, ck.enviar_proposta_email,
              ck.enviar_contrato_conversa, ck.enviar_contrato_email):
        assert "_bloqueio_de_envio(" in inspect.getsource(f), f.__name__
    assert "pendencias_pra_mandar" in inspect.getsource(iao.mandar)
    assert "pendencias_pra_mandar" in inspect.getsource(ps.painel_servicos_enviar_email)
    assert "pendencias_em_lote" in inspect.getsource(agente._orcamento)
    # as telas: o envio do computador desliga o botão, o copiar link pergunta antes
    assert "document.getElementById('env-enviar').disabled=!!ENV_PEND.length" in ps._JS_CRU
    assert "_travaEnvio(it,'a proposta')" in ps._JS_CRU
    assert "var pend=j.pendencias||[]" in pc._ORC_JS
