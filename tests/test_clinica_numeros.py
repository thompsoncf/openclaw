"""Os números da clínica (finance/clinica_numeros.py e /painel/clinica/numeros).

Reaproveita o banco e o cenário do test_clinica_pacotes: a semente da Espaço Pelle
(Dr. Manoel, seg a sex, 08:00–12:00 e 13:30–16:30 = 7 h por dia), a consulta de
R$ 500 e um plano de 4 sessões de Procedimento estético.
"""
from datetime import date, datetime, time, timedelta, timezone

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from finance import clinica_agenda as ca
from finance import clinica_numeros as cn
from tests.test_clinica_agenda import AGORA, CLINICA, SEG
from tests.test_clinica_pacotes import _finalizar, _manoel, _paciente, _plano_aceito, _sessao, _tipo, pool, zap  # noqa: F401

OUT = (date(2026, 10, 1), date(2026, 11, 1))


def test_periodo_do_mes():
    assert cn.periodo_do_mes("2026-09", date(2026, 1, 5)) == (date(2026, 9, 1), date(2026, 10, 1))
    assert cn.periodo_do_mes("lixo", date(2026, 12, 20)) == (date(2026, 12, 1), date(2027, 1, 1))
    assert cn.periodo_do_mes("9999-01", date(2026, 3, 2)) == (date(2026, 3, 1), date(2026, 4, 1))


def test_ocupacao_e_buraco_da_agenda(pool, zap):  # noqa: F811
    with pool.connection() as c:
        lead, _conv = _paciente(c)
        _sessao(c, lead, date(2026, 10, 5), tipo="Consulta")
        _sessao(c, lead, date(2026, 10, 6), tipo="Consulta")
        c.commit()
        d = cn.numeros(c, CLINICA, *OUT, agora=ca.utc(date(2026, 11, 1), time(9)))
    manoel = next(o for o in d["ocupacao"] if o["prof"] == "Dr. Manoel")
    assert (manoel["grade_h"], manoel["vendido_h"]) == (154.0, 1.0)          # 22 dias úteis × 7 h
    assert manoel["vazios"] == (154 * 60 - 60) // 30
    assert d["vazios_valor"] == d["vazios"] * 50000 and d["consulta_preco"] == 50000


def test_consulta_plano_fechado_ticket_e_sessoes(pool, zap):  # noqa: F811
    set_ = (date(2026, 9, 1), date(2026, 10, 1))
    with pool.connection() as c:
        lead, _conv = _paciente(c)
        _finalizar(c, _sessao(c, lead, date(2026, 9, 28), tipo="Consulta"), tratamento="sim")
        faltou = _sessao(c, lead, date(2026, 9, 29), tipo="Consulta")
        for s in ("confirmado",):
            ca.mudar_situacao(c, CLINICA, faltou, s)
        ca.mudar_situacao(c, CLINICA, faltou, "faltou")
        c.commit()
    _plano_aceito(pool, lead)                                # enviado e aceito em 25/09 (AGORA)
    with pool.connection() as c:
        _finalizar(c, _sessao(c, lead, date(2026, 9, 30)))   # 1ª sessão do pacote: não é receita nova
        d = cn.numeros(c, CLINICA, *set_, agora=ca.utc(date(2026, 10, 1), time(9)))
    assert (d["consultas"], d["planos_enviados"], d["planos_aceitos"]) == (1, 1, 1)
    assert d["consulta_proposta_pct"] == 100 and d["proposta_fechada_pct"] == 100
    assert d["valor_aceito"] == 362000 and d["receita_avulsa"] == 50000
    assert d["atendimentos"] == 2 and d["ticket"] == (362000 + 50000) // 2
    assert (d["vendidas"], d["usadas"], d["devidas"]) == (4, 1, 3)
    assert d["faltas"] == 1 and d["falta_pct"] == 33
    assert d["origem"]["recepcao"] == 3                       # a consulta, a falta e a sessão


@pytest.fixture()
def cli(pool, monkeypatch):  # noqa: F811
    from web import painel_clinica_agenda as pa
    from web import painel_clinica_numeros as pn
    monkeypatch.setattr(pn, "get_pool", lambda: pool)
    monkeypatch.setattr(pa, "conta_logada", lambda request: (CLINICA,))
    monkeypatch.setattr(pa, "nicho_da_conta", lambda conta: "clinica")
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="teste")
    app.include_router(pn.router)

    @app.get("/_papel/{papel}")
    def _papel(request: Request, papel: str):
        request.session["papel"] = papel
        request.session["membro_id"] = 51
        return {}
    c = TestClient(app, follow_redirects=False)
    return c


def test_tela_so_pra_gerencia(cli):
    cli.get("/_papel/vendedor")
    assert cli.get("/painel/clinica/numeros").headers["location"] == "/painel/clinica/agenda"
    cli.get("/_papel/dono")
    html = cli.get("/painel/clinica/numeros?mes=2026-10").text
    assert "Números da clínica · out/2026" in html and "Ocupação da agenda" in html and "Dr. Manoel" in html


def test_encaixe_em_cima_nao_conta_o_minuto_duas_vezes(pool, zap):  # noqa: F811
    with pool.connection() as c:
        lead, _conv = _paciente(c)
        _sessao(c, lead, date(2026, 10, 5), tipo="Consulta")
        outro, _ = _paciente(c, nome="Rita Souza", fone="5511988887777")
        eid, erro = ca.agendar(c, CLINICA, profissional_id=_manoel(c), servico_id=_tipo(c, "Consulta")["id"],
                               inicio=ca.utc(date(2026, 10, 5), time(8)), lead_id=outro, agora=AGORA, encaixe=True)
        assert erro is None, erro
        c.commit()
        d = cn.numeros(c, CLINICA, *OUT, agora=ca.utc(date(2026, 11, 1), time(9)))
    manoel = next(o for o in d["ocupacao"] if o["prof"] == "Dr. Manoel")
    assert manoel["vendido_h"] == 0.5 and manoel["vazios"] == 154 * 2 - 1


def test_sessao_do_pacote_conta_no_mes_do_atendimento(pool, zap):  # noqa: F811
    with pool.connection() as c:
        lead, _conv = _paciente(c)
    _plano_aceito(pool, lead)
    with pool.connection() as c:
        _finalizar(c, _sessao(c, lead, date(2026, 10, 2)))    # a baixa grava now(), o atendimento é de outubro
        d = cn.numeros(c, CLINICA, *OUT, agora=ca.utc(date(2026, 11, 1), time(9)))
    assert d["usadas"] == 1 and d["atendimentos"] == 1 and d["receita_avulsa"] == 0


def test_profissional_desativado_aparece_no_mes_que_atendeu(pool, zap):  # noqa: F811
    with pool.connection() as c:
        lead, _conv = _paciente(c)
        _sessao(c, lead, date(2026, 10, 5), tipo="Consulta")
        c.execute("update clinica_profissionais set ativo=false where conta_id=%s and id=%s", (CLINICA, _manoel(c)))
        c.commit()
        d = cn.numeros(c, CLINICA, *OUT, agora=ca.utc(date(2026, 11, 1), time(9)))
    manoel = next(o for o in d["ocupacao"] if o["prof"] == "Dr. Manoel")
    assert (manoel["ativo"], manoel["grade_h"], manoel["fora_h"]) == (False, 0.0, 0.5)
