"""O Receber da agenda da clínica (finance/clinica_recebimentos.py, entrega 2a do CRM).

Desenho: docs/mockups/clinica_crm_telas.html, seção 04, aprovado em 01/10/2026; decisão B
do dono em 02/10/2026 (a qualquer hora do dia, sem sinal). O lançamento real no
Financeiro está em tests/test_clinica_recebimentos_financeiro.py; aqui ele é trocado
por um registro, pra testar a agenda sem as tabelas do livro-caixa.
"""
from __future__ import annotations

from datetime import time

import pytest

from finance import clinica_agenda as ca
from finance import clinica_recebimentos as crb
from tests.test_clinica_agenda import (AGORA, CLINICA, SEG, _ate_atendimento, _manoel,  # noqa: F401
                                       _marcar, _proxima_segunda, _tipo, cli, pool)


@pytest.fixture()
def lancou(monkeypatch):
    """O livro-caixa e o título trocados por registros (o real está no teste do Financeiro)."""
    feitos = []

    def _receita(pool, c, conta_id, ev, valor, forma, membro_id, cliente_id):
        feitos.append((ev["id"], valor, forma))
        return 700 + len(feitos)

    def _titulo(pool, conta_id, tipo, descricao, valor, venc, **kw):
        feitos.append(("titulo", valor, descricao))
        return {"id": 900 + len(feitos)}
    monkeypatch.setattr(crb, "_lancar_receita", _receita)
    from finance import empresa
    monkeypatch.setattr(empresa, "criar_titulo", _titulo)
    return feitos


def _pgto(c, eid):
    ev = ca.evento(c, CLINICA, eid)
    crb.anotar(c, CLINICA, [ev])
    return ev["pgto"]


def test_o_selo_de_cada_linha(pool):
    with pool.connection() as c:
        consulta, _ = _marcar(c)
        assert _pgto(c, consulta) is None                       # ainda não chegou: nada
        _ate_atendimento(c, consulta)
        assert _pgto(c, consulta) == "a_receber"
        retorno, _ = ca.agendar(c, CLINICA, profissional_id=_manoel(c)["id"], servico_id=_tipo(c, "Retorno")["id"],
                                inicio=ca.utc(SEG, time(9)), nome="Outra", fone="99 97777-0081", agora=AGORA)
        _ate_atendimento(c, retorno)
        assert _pgto(c, retorno) == "sem_custo"                 # retorno sem preço no catálogo
        teste, _ = ca.agendar(c, CLINICA, profissional_id=_manoel(c)["id"],
                              servico_id=_tipo(c, "Testes alérgicos")["id"],
                              inicio=ca.utc(SEG, time(10)), nome="Mais uma", fone="99 97777-0083", agora=AGORA)
        _ate_atendimento(c, teste)
        assert _pgto(c, teste) == "a_receber"                   # sem preço é "sob consulta", não grátis


def test_receber_do_chegou_em_diante_e_uma_vez_so(pool, lancou):
    with pool.connection() as c:
        eid, _ = _marcar(c)
        c.commit()
    erro = crb.receber(pool, CLINICA, eid, valor_centavos=50000, forma="pix", membro_id=51)
    assert "presente" in erro and lancou == []
    with pool.connection() as c:
        assert ca.mudar_situacao(c, CLINICA, eid, "presente") is None
        c.commit()
    assert crb.receber(pool, CLINICA, eid, valor_centavos=0, forma="pix", membro_id=51) ==         "Digite o valor recebido (formato 150,00)."
    assert crb.receber(pool, CLINICA, eid, valor_centavos=50000, forma="cheque", membro_id=51) == "Escolha a forma de pagamento."
    assert crb.receber(pool, CLINICA, eid, valor_centavos=50000, forma="pix", membro_id=51) is None
    assert crb.receber(pool, CLINICA, eid, valor_centavos=50000, forma="pix", membro_id=51) == "Esse atendimento já foi recebido."
    assert lancou == [(eid, 50000, "pix")]
    with pool.connection() as c:
        assert _pgto(c, eid) == "pago"
        assert crb.do_evento(c, CLINICA, eid)["forma_d"] == "Pix"
        assert c.execute("select lancamento_id, titulo_id from clinica_recebimentos where evento_id=%s",
                         (eid,)).fetchone() == (701, None)


def test_fica_a_receber_vira_titulo(pool, lancou):
    with pool.connection() as c:
        eid, _ = _marcar(c)
        _ate_atendimento(c, eid)
        assert ca.mudar_situacao(c, CLINICA, eid, "finalizado", tratamento="nao") is None
        c.commit()
    assert crb.receber(pool, CLINICA, eid, valor_centavos=50000, forma="fiado", membro_id=51) is None
    with pool.connection() as c:
        assert _pgto(c, eid) == "fica"
        assert c.execute("select titulo_id from clinica_recebimentos where evento_id=%s", (eid,)).fetchone()[0] == 901
    assert lancou == [("titulo", 50000, "Atendimento · Maria (consulta) — fica a receber")]


def test_o_lancamento_que_falha_no_meio_nao_deixa_nada(pool, monkeypatch):
    """A receita entra no mesmo commit do recebimento: a falha depois de gravar desfaz os dois."""
    def _quebra(pool, c, *a, **k):
        c.execute("update clinica_recebimentos set valor_centavos = 1")   # já gravou alguma coisa
        raise RuntimeError("caixa fora")
    monkeypatch.setattr(crb, "_lancar_receita", _quebra)
    with pool.connection() as c:
        eid, _ = _marcar(c)
        assert ca.mudar_situacao(c, CLINICA, eid, "presente") is None
        c.commit()
    assert "Nada foi registrado" in crb.receber(pool, CLINICA, eid, valor_centavos=50000, forma="pix", membro_id=51)
    with pool.connection() as c:
        assert crb.do_evento(c, CLINICA, eid) is None
        assert _pgto(c, eid) == "a_receber"


def test_nao_recebe_retorno_sem_custo_nem_atendimento_de_outra_conta(pool, lancou):
    with pool.connection() as c:
        retorno, _ = ca.agendar(c, CLINICA, profissional_id=_manoel(c)["id"], servico_id=_tipo(c, "Retorno")["id"],
                                inicio=ca.utc(SEG, time(9)), nome="Outra", fone="99 97777-0084", agora=AGORA)
        assert ca.mudar_situacao(c, CLINICA, retorno, "presente") is None
        c.commit()
    assert crb.receber(pool, CLINICA, retorno, valor_centavos=10000, forma="pix", membro_id=51) == "Esse retorno é sem custo."
    assert crb.receber(pool, 34, retorno, valor_centavos=10000, forma="pix", membro_id=51) == "Agendamento não encontrado."
    assert lancou == []


def test_telas_receber_e_o_dia(cli, pool, lancou):
    seg = _proxima_segunda()
    with pool.connection() as c:
        eid, erro = ca.agendar(c, CLINICA, profissional_id=_manoel(c)["id"], servico_id=_tipo(c, "Consulta")["id"],
                               inicio=ca.utc(seg, time(8)), nome="Ana", fone="99 97777-0082")
        assert erro is None
        assert ca.mudar_situacao(c, CLINICA, eid, "presente") is None
        c.commit()
    html = cli.get(f"/painel/clinica/agenda/evento/{eid}").text
    assert "Receber" in html and 'value="500,00"' in html
    dia = cli.get(f"/painel/clinica/agenda?data={seg.isoformat()}").text
    assert "<b>a receber</b>" in dia
    r = cli.post(f"/painel/clinica/agenda/evento/{eid}/receber", data={"valor": "450,00", "forma": "debito"})
    assert "aviso=recebido" in r.headers["location"]
    assert lancou == [(eid, 45000, "debito")]
    html = cli.get(f"/painel/clinica/agenda/evento/{eid}").text
    assert "Pago: R$ 450,00 · cartão de débito" in html
    assert " · pago" in cli.get(f"/painel/clinica/agenda?data={seg.isoformat()}").text
    r = cli.post(f"/painel/clinica/agenda/evento/{eid}/receber", data={"valor": "450,00", "forma": "debito"})
    assert "já foi recebido" in cli.get(r.headers["location"]).text
