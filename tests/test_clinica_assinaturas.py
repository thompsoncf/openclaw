"""A assinatura da clínica (finance/clinica_assinaturas.py e /painel/clinica/assinaturas).

Reaproveita o banco e o cenário do test_clinica_pacotes (a semente da Espaço Pelle, o
Dr. Manoel, "Procedimento estético" a R$ 0 de tabela e a consulta de R$ 500).
"""
from datetime import date, time

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from finance import clinica_agenda as ca
from finance import clinica_assinaturas as cas
from finance import clinica_pacotes as ckp
from finance import clinica_planos as cp
from tests.test_clinica_agenda import CLINICA
from tests.test_clinica_pacotes import (BASE, FONE, _finalizar, _paciente, _plano_aceito, _sessao,  # noqa: F401
                                        _tipo, pool, zap)

HOJE = date(2026, 9, 25)


@pytest.fixture()
def banco(pool):  # noqa: F811
    """O banco do test_clinica_pacotes + a 384; o título a receber vai pra tabela
    mínima de títulos do fixture (o Financeiro de verdade tem o próprio teste)."""
    with pool.connection() as c:
        c.execute((BASE / "384_clinica_assinaturas.sql").read_text(encoding="utf-8"))
        c.commit()
    return pool


def _plano(c, **kw):
    campos = {"plano_id": None, "nome": "Pele em dia", "preco": "149", "dia": "10",
              "servico_id": str(_tipo(c, "Procedimento estético")["id"]), "sessoes": "1",
              "desconto_procedimento": "15", "desconto_produto": "10", "prioridade": True}
    campos.update(kw)
    pid, erro = cas.salvar_plano(c, CLINICA, **campos)
    assert erro is None, erro
    c.commit()
    return pid


def _titulo(c, tid):
    return c.execute("select valor_centavos, vencimento, status from titulos where id=%s and conta_id=%s",
                     (tid, CLINICA)).fetchone()


def test_plano_valida_o_que_da(banco, zap):  # noqa: F811
    pool = banco
    with pool.connection() as c:
        assert cas.salvar_plano(c, CLINICA, plano_id=None, nome="", preco="149", dia="10", servico_id="",
                                sessoes="0", desconto_procedimento="0", desconto_produto="0",
                                prioridade=False)[1]
        assert "Mensalidade" in cas.salvar_plano(c, CLINICA, plano_id=None, nome="X", preco="abc", dia="10",
                                                 servico_id="", sessoes="0", desconto_procedimento="0",
                                                 desconto_produto="0", prioridade=False)[1]
        assert "atendimento incluso" in cas.salvar_plano(c, CLINICA, plano_id=None, nome="X", preco="149",
                                                         dia="10", servico_id="", sessoes="1",
                                                         desconto_procedimento="0", desconto_produto="0",
                                                         prioridade=False)[1]
        assert "50%" in cas.salvar_plano(c, CLINICA, plano_id=None, nome="X", preco="149", dia="10",
                                         servico_id="", sessoes="0", desconto_procedimento="60",
                                         desconto_produto="0", prioridade=False)[1]
        pid = _plano(c)
        p = cas.plano(c, CLINICA, pid)
    assert p["preco_centavos"] == 14900 and p["dia"] == 10 and p["sessoes_mes"] == 1
    assert p["beneficios"] == "1 Procedimento estético por mês · 15% em procedimentos · 10% em produtos · prioridade nas vagas"


def test_assinar_lanca_a_mensalidade_do_mes_e_nao_duplica(banco, zap):  # noqa: F811
    pool = banco
    with pool.connection() as c:
        pid = _plano(c)
        lead, _conv = _paciente(c)
    aid, erro = cas.assinar(pool, CLINICA, plano_id=pid, lead=lead, paciente="", fone="", inicio="2026-09-25",
                            membro_id=None, hoje=HOJE)
    assert erro is None, erro
    _aid2, erro = cas.assinar(pool, CLINICA, plano_id=pid, lead=lead, paciente="", fone="", inicio="",
                              membro_id=None, hoje=HOJE)
    assert "já é assinante" in erro
    with pool.connection() as c:
        ms = c.execute("""select competencia, vencimento, titulo_id from clinica_assinatura_mensalidades
                           where assinante_id=%s and conta_id=%s""", (aid, CLINICA)).fetchall()
        assert len(ms) == 1 and ms[0][0] == date(2026, 9, 1)
        assert ms[0][1] == date(2026, 9, 25)          # assinou depois do dia 10: vence no dia em que assinou
        t = _titulo(c, ms[0][2])
        assert t[0] == 14900 and t[2] == "aberto"
    # o poller no mês seguinte lança outubro, no dia 10; rodar de novo não duplica
    assert cas.gerar(pool, CLINICA, date(2026, 10, 3)) == 1
    assert cas.gerar(pool, CLINICA, date(2026, 10, 4)) == 0
    with pool.connection() as c:
        venc = c.execute("""select vencimento from clinica_assinatura_mensalidades
                             where assinante_id=%s and competencia='2026-10-01'""", (aid,)).fetchone()[0]
        assert venc == date(2026, 10, 10)
        # cancelada: novembro não sai
        assert cas.cancelar(c, CLINICA, aid, "mudou de cidade", None)
        c.commit()
    assert cas.gerar(pool, CLINICA, date(2026, 11, 2)) == 0


def test_sessao_inclusa_usa_o_beneficio_e_nao_baixa_o_pacote(banco, zap):  # noqa: F811
    pool = banco
    with pool.connection() as c:
        pid = _plano(c)
        lead, _conv = _paciente(c)
    _plano_aceito(pool, lead)                                   # 4 sessões de Procedimento estético
    assert cas.assinar(pool, CLINICA, plano_id=pid, lead=lead, paciente="", fone="", inicio="2026-09-25",
                       membro_id=None, hoje=HOJE)[1] is None
    with pool.connection() as c:
        e1 = _sessao(c, lead, date(2026, 9, 28))
        _finalizar(c, e1)                                       # 1 por mês: a assinatura cobre
        e2 = _sessao(c, lead, date(2026, 9, 29))
        _finalizar(c, e2)                                       # a 2ª do mês baixa do pacote
        k = ckp.listar(c, CLINICA, HOJE)[0]
        usos = c.execute("select evento_id from clinica_assinatura_usos where conta_id=%s", (CLINICA,)).fetchall()
    assert [u[0] for u in usos] == [e1]
    assert k["usadas"] == 1


def test_mae_assinante_nao_da_beneficio_ao_filho_no_mesmo_celular(banco, zap):  # noqa: F811
    pool = banco
    with pool.connection() as c:
        pid = _plano(c)
        lead, _conv = _paciente(c)
    cas.assinar(pool, CLINICA, plano_id=pid, lead=lead, paciente="", fone="", inicio="2026-09-25",
                membro_id=None, hoje=HOJE)
    with pool.connection() as c:
        assert cas.do_paciente(c, CLINICA, None, FONE, "Lúcia")["nome"] == "Pele em dia"
        assert cas.do_paciente(c, CLINICA, None, FONE, "Pedro Ferreira") is None
        assert cas.desconto_procedimento(c, CLINICA, lead, FONE, "") == (15.0, "Pele em dia")


def test_desconto_do_assinante_nao_pede_aprovacao(banco, zap):  # noqa: F811
    pool = banco
    with pool.connection() as c:
        pid = _plano(c)
        lead, _conv = _paciente(c)
    cas.assinar(pool, CLINICA, plano_id=pid, lead=lead, paciente="", fone="", inicio="2026-09-25",
                membro_id=None, hoje=HOJE)
    itens = [{"servico_id": None, "nome": "Peeling", "sessoes": 2, "valor_unit_centavos": 50000,
              "preco_catalogo_centavos": 0}]
    with pool.connection() as c:
        args = dict(lead=lead, evento_id=None, profissional_id=None, paciente="Lúcia Ferreira", fone=FONE,
                    itens=itens, pix_desconto_pct="0", cartao_parcelas="1", parcelado=False, membro_id=None,
                    pode_aprovar=False)
        p1, erro = cp.salvar(c, CLINICA, desconto_pct="15", **args)
        assert erro is None and cp.plano(c, CLINICA, p1)["status"] == "rascunho"
        p2, _ = cp.salvar(c, CLINICA, desconto_pct="20", **args)
        assert cp.plano(c, CLINICA, p2)["status"] == "aguardando_aprovacao"
        c.rollback()


def test_quem_pode_assinar_e_prioridade(banco, zap):  # noqa: F811
    pool = banco
    with pool.connection() as c:
        pid = _plano(c)
        lead, _conv = _paciente(c)
        for d in (28, 29, 30):
            _finalizar(c, _sessao(c, lead, date(2026, 9, d), tipo="Consulta"))
        podem = cas.quem_pode_assinar(c, CLINICA, ca.utc(date(2026, 10, 1), time(12)))
        assert [p["lead"] for p in podem] == [lead] and podem[0]["visitas"] == 3
    cas.assinar(pool, CLINICA, plano_id=pid, lead=lead, paciente="", fone="", inicio="2026-09-25",
                membro_id=None, hoje=HOJE)
    with pool.connection() as c:
        assert cas.quem_pode_assinar(c, CLINICA, ca.utc(date(2026, 10, 1), time(12))) == []
        assert cas.prioritarios(c, CLINICA) == {lead}


@pytest.fixture()
def cli(banco, monkeypatch):  # noqa: F811
    pool = banco
    from web import painel_clinica_agenda as pa
    from web import painel_clinica_assinaturas as pw
    monkeypatch.setattr(pw, "get_pool", lambda: pool)
    monkeypatch.setattr(pa, "conta_logada", lambda request: (CLINICA,))
    monkeypatch.setattr(pa, "nicho_da_conta", lambda conta: "clinica")
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="teste")
    app.include_router(pw.router)

    @app.get("/_papel/{papel}")
    def _papel(request: Request, papel: str):
        request.session["papel"] = papel
        request.session["membro_id"] = 51
        return {}
    return TestClient(app, follow_redirects=False)


def test_tela_recepcao_ativa_e_so_gerencia_mexe_no_plano(cli, banco):  # noqa: F811
    pool = banco
    cli.get("/_papel/vendedor")
    r = cli.post("/painel/clinica/assinaturas/plano", data={"nome": "X", "preco": "100"})
    assert r.status_code == 303
    with pool.connection() as c:
        assert cas.planos(c, CLINICA) == []
    cli.get("/_papel/dono")
    cli.post("/painel/clinica/assinaturas/plano", data={"nome": "Pele em dia", "preco": "149", "dia": "10",
                                                         "desconto_procedimento": "15", "prioridade": "1"})
    with pool.connection() as c:
        pid = cas.planos(c, CLINICA)[0]["id"]
    cli.get("/_papel/vendedor")
    r = cli.post("/painel/clinica/assinaturas/nova", data={"plano_id": str(pid), "paciente": "Rita Souza",
                                                            "fone": "11988887777"})
    assert r.headers["location"].endswith("aviso=ativa")
    html = cli.get("/painel/clinica/assinaturas").text
    assert "Rita Souza" in html and "Pele em dia" in html and "Novo plano" not in html
    assert "cancelar" not in html.split("Assinantes</h3>")[1].split("Já vieram")[0]
