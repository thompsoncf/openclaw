"""Plano de tratamento da clínica (finance/clinica_planos.py e as telas).

Em cima da semente da Espaço Pelle (350). A consulta de sexta 25/09 (AGORA) acabou,
o Dr. Manoel propôs tratamento; a recepção monta o plano, manda, e o Zaq cobra.
"""
import os
import types
from datetime import date, datetime, time, timedelta, timezone

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from psycopg_pool import ConnectionPool
from starlette.middleware.sessions import SessionMiddleware

from finance import agente
from finance import clinica_agenda as ca
from finance import clinica_config as cc
from finance import clinica_planos as cp
from tests.test_clinica_agenda import _SQL, AGORA, BASE, CLINICA

FONE = "+5599988880001"


@pytest.fixture()
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True,
                           kwargs={"autocommit": True, "prepare_threshold": None})
    dbname = "zaq_clinica_planos_teste"
    with admin.connection() as c:
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity "
                  "where datname=%s and pid <> pg_backend_pid()", (dbname,))
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=4, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        c.execute("alter table mensagens add column midia_tipo text")
        for m in ("071_servicos_catalogo.sql", "148_servico_categoria_foto.sql", "153_servico_icone.sql",
                  "098_agenda.sql", "099_agenda_tipo.sql", "130_evento_desfecho.sql",
                  "136_visita_agenda.sql", "179_agenda_tipo_e_hora_sugerida.sql", "348_clinica_base.sql",
                  "350_clinica_semente_espaco_pelle.sql"):
            c.execute((BASE / m).read_text(encoding="utf-8"))
        c.execute("alter table eventos_agenda add column if not exists marcado_por text")
        c.execute("""create table titulos (id bigserial primary key, conta_id bigint, status text default 'aberto',
                                           vencimento date, valor_centavos bigint)""")
        for m in ("360_clinica_agenda.sql", "363_clinica_repasses.sql", "369_clinica_vagas.sql",
                  "379_clinica_planos.sql", "381_clinica_pacotes.sql", "471_clinica_tratamento_proposto.sql",
                  "474_clinica_plano_pago_e_nao_fechou.sql", "477_clinica_resultados.sql"):
            c.execute((BASE / m).read_text(encoding="utf-8"))
        c.execute((BASE / next(BASE.glob("346_*.sql")).name).read_text(encoding="utf-8"))
        c.execute("update servicos_catalogo set setup_centavos=80000 where conta_id=39 and nome='Procedimento estético'")
        c.commit()
    yield p
    p.close()


@pytest.fixture()
def zap(monkeypatch):
    estado = types.SimpleNamespace(saiu=[], avisos=[], titulos=[], baixas=[])

    def _mandar(c, conta_id, canal, destino, texto, conversa_id=None):
        estado.saiu.append((conversa_id, texto))
        return {"ok": True, "sid": f"s{len(estado.saiu)}"}
    monkeypatch.setattr(agente, "_mandar", _mandar)
    from finance import clinica_agente as cla
    monkeypatch.setattr(cla, "_aviso", lambda *a, **k: estado.avisos.append(a[3]))
    from finance import empresa

    def _titulo(pool, conta_id, tipo, descricao, valor, venc, **kw):
        estado.titulos.append((descricao, valor, venc))
        with pool.connection() as c:
            tid = c.execute("insert into titulos (conta_id, vencimento, valor_centavos) values (%s,%s,%s) returning id",
                            (conta_id, venc, valor)).fetchone()[0]
            c.commit()
        return {"id": tid}
    monkeypatch.setattr(empresa, "criar_titulo", _titulo)

    def _baixa(pool, conta_id, titulo_id, **kw):
        estado.baixas.append(titulo_id)
        with pool.connection() as c:
            ok = c.execute("update titulos set status='pago' where id=%s and conta_id=%s and status='aberto' "
                           "returning id", (titulo_id, conta_id)).fetchone()
            c.commit()
        return {"ok": bool(ok), "erro": None if ok else "Título já está 'pago'."}
    monkeypatch.setattr(empresa, "dar_baixa_titulo", _baixa)
    return estado


def _manoel(c):
    return cc.listar_profissionais(c, CLINICA)[0]["id"]


def _tipo(c, nome):
    return next(t for t in cc.listar_tipos(c, CLINICA) if t["nome"] == nome)


def _paciente(c, nome="Lúcia Ferreira", fone=FONE):
    lead = c.execute("""insert into prospeccao (conta_id, empresa, contato, whatsapp, status, estagio)
                        values (39,%s,%s,%s,'qualificado','lead') returning id""", (nome, nome, fone)).fetchone()[0]
    conv = c.execute("""insert into conversas (conta_id, prospeccao_id, contato_ref, contato_nome)
                        values (39,%s,%s,%s) returning id""", (lead, fone, nome)).fetchone()[0]
    c.execute("""insert into mensagens (conversa_id, canal, direcao, autor, texto, criado_em)
                 values (%s,'whatsapp','in','lead','quanto é a consulta?',%s)""", (conv, AGORA - timedelta(days=5)))
    c.commit()
    return lead, conv


def _diz(c, conv, texto, autor="lead"):
    c.execute("""insert into mensagens (conversa_id, canal, direcao, autor, texto)
                 values (%s,'whatsapp',%s,%s,%s)""", (conv, "in" if autor == "lead" else "out", autor, texto))
    c.commit()


def _plano(c, lead, desconto="0", pode=True, sessoes=4, valor="800,00"):
    itens, erro = cp.limpar_itens(
        [{"servico_id": _tipo(c, "Procedimento estético")["id"], "sessoes": sessoes, "valor": valor},
         {"servico_id": None, "nome": "Protocolo domiciliar", "sessoes": 1, "valor": "420"}],
        {t["id"]: t for t in cc.listar_tipos(c, CLINICA)})
    assert erro is None
    pid, erro = cp.salvar(c, CLINICA, lead=lead, evento_id=None, profissional_id=_manoel(c), paciente="Lúcia Ferreira",
                          fone=FONE, itens=itens, desconto_pct=desconto, pix_desconto_pct="8", cartao_parcelas="4",
                          parcelado=True, membro_id=51, pode_aprovar=pode)
    assert erro is None
    c.commit()
    return pid


# ------------------------------------------------------------------ as contas

def test_as_contas_batem_no_centavo():
    itens = [{"valor_unit_centavos": 80000, "sessoes": 4}, {"valor_unit_centavos": 42000, "sessoes": 1}]
    r = cp.calcular(itens, 0, 8, 4)
    assert (r["subtotal"], r["total"], r["pix"]) == (362000, 362000, 333040)
    assert r["primeira"] + r["parcela"] * 3 == r["total"]
    r = cp.calcular([{"valor_unit_centavos": 100000, "sessoes": 1}], 0, 0, 3)
    assert (r["primeira"], r["parcela"]) == (33334, 33333)


def test_parcelas_do_aceite_mes_a_mes():
    p = {"pix": 90000, "total": 100000, "parcelas": 3, "primeira": 33334, "parcela": 33333}
    assert cp.parcelas_do_aceite(p, "pix", date(2026, 1, 31)) == [("à vista no Pix", 90000, date(2026, 1, 31))]
    cart = cp.parcelas_do_aceite(p, "cartao", date(2026, 1, 31))
    assert [x[2] for x in cart] == [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31)]
    assert sum(x[1] for x in cart) == 100000 and cart[0][0] == "parcela 1/3 (cartão)"
    assert cp.parcelas_do_aceite(p, "parcelado", date(2026, 1, 31))[0][0] == "entrada (boleto/Pix)"


# ------------------------------------------------------------------ montar e o teto

def test_desconto_acima_do_teto_espera_o_dono(pool, zap):
    with pool.connection() as c:
        lead, _conv = _paciente(c)
        pid = _plano(c, lead, desconto="15", pode=False)
        assert cp.plano(c, CLINICA, pid)["status"] == "aguardando_aprovacao"
        r = cp.enviar(c, CLINICA, pid, 51, AGORA)
        assert not r["ok"] and "aprovar" in r["erro"]
        assert zap.saiu == []
        assert cp.aprovar_desconto(c, CLINICA, pid, 52, cp.plano(c, CLINICA, pid)["versao"])
        c.commit()
        assert cp.plano(c, CLINICA, pid)["status"] == "rascunho"
        pid2 = _plano(c, lead, desconto="2", pode=False)        # 2% + Pix 8% = 9,8% efetivo: dentro
        assert cp.plano(c, CLINICA, pid2)["status"] == "rascunho"


def test_enviar_manda_a_proposta_e_o_card_anda(pool, zap):
    with pool.connection() as c:
        lead, conv = _paciente(c)
        pid = _plano(c, lead)
        r = cp.enviar(c, CLINICA, pid, 51, AGORA)
        assert r["ok"]
        p = cp.plano(c, CLINICA, pid)
        assert (p["status"], p["validade_ate"]) == ("enviado", date(2026, 10, 2))
        assert c.execute("select status, valor_estimado_centavos from prospeccao where id=%s",
                         (lead,)).fetchone() == ("proposta", 362000)
        assert c.execute("select de, para, motivo from funil_movimentos order by id desc limit 1"
                         ).fetchone() == ("qualificado", "proposta", "plano")
        assert cp.enviar(c, CLINICA, pid, 51, AGORA)["ok"] is False          # não sai duas vezes
    assert len(zap.saiu) == 1 and zap.saiu[0][0] == conv
    texto = zap.saiu[0][1]
    assert texto.startswith("Oi, Lúcia! Dr. Manoel montou o seu plano de tratamento 💚")
    assert "Procedimento estético · 4 sessões · R$ 3.200" in texto and "Total: R$ 3.620" in texto
    assert "À vista no Pix: R$ 3.330,40 (−8%)" in texto and "4× de R$ 905 no cartão" in texto
    assert f"/plano/{p['token']}" in texto and "responda 1 para o Pix, 2 para o cartão ou 3" in texto


@pytest.mark.parametrize("de", ["consulta", "retorno"])
def test_plano_enviado_tira_o_card_de_consulta_e_de_retorno(pool, zap, de):
    """Consulta guarda o paciente que espera o plano; Retorno, o que voltou e ganhou
    proposta nova. É o ENVIO do plano que leva pra Plano ou orçamento enviado."""
    with pool.connection() as c:
        lead, _conv = _paciente(c)
        c.execute("update prospeccao set status=%s where id=%s", (de, lead))
        c.commit()
        assert cp.enviar(c, CLINICA, _plano(c, lead), 51, AGORA)["ok"]
        assert c.execute("select status, valor_estimado_centavos from prospeccao where id=%s",
                         (lead,)).fetchone() == ("proposta", 362000)


def _aceito(pool, lead, forma="cartao"):
    with pool.connection() as c:
        pid = _plano(c, lead)
        assert cp.enviar(c, CLINICA, pid, 51, AGORA)["ok"]
        token = cp.plano(c, CLINICA, pid)["token"]
    assert cp.aceitar(pool, token, nome="Lúcia Ferreira", forma=forma, agora=AGORA)
    return pid


def _card(c, lead):
    return c.execute("select status, valor_estimado_centavos from prospeccao where id=%s", (lead,)).fetchone()


def test_o_pagamento_leva_pra_em_tratamento_e_a_baixa_por_fora_tambem(pool, zap):
    """Plano aceito espera o pagamento. A baixa feita pelo financeiro (fora do Zaq
    da clínica) é percebida pelo relógio e leva o card do mesmo jeito."""
    with pool.connection() as c:
        lead, _conv = _paciente(c)
    pid = _aceito(pool, lead)
    with pool.connection() as c:
        assert cp.conferir_pagamentos(c, CLINICA) == 0               # nada pago: fica
        assert _card(c, lead)[0] == "proposta"
        assert [p["id"] for p in cp.em_aberto(c, CLINICA, AGORA)["a_pagar"]] == [pid]
        c.execute("update titulos set status='pago' where id=%s", (cp.plano(c, CLINICA, pid)["titulos"][1],))
        c.commit()
        assert cp.conferir_pagamentos(c, CLINICA) == 1
        assert _card(c, lead)[0] == "tratamento"
        assert c.execute("select de, para, motivo from funil_movimentos order by id desc limit 1"
                         ).fetchone() == ("proposta", "tratamento", "pagamento")
        assert cp.plano(c, CLINICA, pid)["pago_em"] is not None
        assert cp.conferir_pagamentos(c, CLINICA) == 0               # uma vez só
        assert cp.em_aberto(c, CLINICA, AGORA)["a_pagar"] == []


def test_recebi_da_baixa_na_primeira_parcela_e_leva_o_card(pool, zap):
    with pool.connection() as c:
        lead, _conv = _paciente(c)
    pid = _aceito(pool, lead)
    r = cp.receber(pool, CLINICA, pid, 51)
    assert r == {"ok": True, "destino": "tratamento"}
    with pool.connection() as c:
        primeira = cp.plano(c, CLINICA, pid)["titulos"][0]
        assert zap.baixas == [primeira]                               # só a primeira, a entrada
        assert c.execute("select count(*) from titulos where status='pago'").fetchone()[0] == 1
        assert _card(c, lead)[0] == "tratamento"
    assert cp.receber(pool, CLINICA, pid, 51)["ok"] and zap.baixas == [primeira]   # de novo: não baixa outra


def test_recebi_sem_contas_a_receber_ou_plano_nao_aceito(pool, zap, monkeypatch):
    with pool.connection() as c:
        lead, _conv = _paciente(c)
        pid = _plano(c, lead)
    assert "não está aceito" in cp.receber(pool, CLINICA, pid, 51)["erro"]
    from finance import empresa
    monkeypatch.setattr(empresa, "criar_titulo", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("fora")))
    pid2 = _aceito(pool, lead)
    assert "gere primeiro" in cp.receber(pool, CLINICA, pid2, 51)["erro"]


def test_plano_pago_sem_sessoes_conclui(pool, zap):
    with pool.connection() as c:
        lead, _conv = _paciente(c)
        itens, _e = cp.limpar_itens([{"servico_id": None, "nome": "Protocolo domiciliar", "sessoes": 1, "valor": "420"}],
                                    {t["id"]: t for t in cc.listar_tipos(c, CLINICA)})
        pid, _e = cp.salvar(c, CLINICA, lead=lead, evento_id=None, profissional_id=_manoel(c), paciente="Lúcia Ferreira",
                            fone=FONE, itens=itens, desconto_pct="0", pix_desconto_pct="0", cartao_parcelas="1",
                            parcelado=False, membro_id=51, pode_aprovar=True)
        c.commit()
        assert cp.enviar(c, CLINICA, pid, 51, AGORA)["ok"]
        token = cp.plano(c, CLINICA, pid)["token"]
    assert cp.aceitar(pool, token, nome="Lúcia Ferreira", forma="pix", agora=AGORA)
    assert cp.receber(pool, CLINICA, pid, 51)["destino"] == "ganho"


def _atendido(c, lead, *, proposta="sim", retorno_dias=None, h=9):
    """A consulta do paciente, até o Finalizar (o card vai pra Consulta, plano a montar)."""
    seg = date(2026, 9, 28)
    eid, erro = ca.agendar(c, CLINICA, profissional_id=_manoel(c), servico_id=_tipo(c, "Consulta")["id"],
                           inicio=ca.utc(seg, time(h)), lead_id=lead, agora=AGORA)
    assert erro is None, erro
    for s in ("confirmado", "presente", "atendimento"):
        assert ca.mudar_situacao(c, CLINICA, eid, s) is None
    assert ca.mudar_situacao(c, CLINICA, eid, "finalizado", tratamento=proposta, retorno_dias=retorno_dias) is None
    c.commit()
    return eid


def test_paciente_nao_quis_o_plano_conclui_com_o_valor_da_consulta(pool, zap):
    """PLANO QUE NÃO FECHA NÃO PERDE O PACIENTE: o card vai pra Concluído, e o valor
    volta a ser o da consulta (não o do plano, que não foi vendido)."""
    with pool.connection() as c:
        lead, _conv = _paciente(c)
        _atendido(c, lead)
        assert _card(c, lead)[0] == "consulta"
        pid = _plano(c, lead)
        assert cp.enviar(c, CLINICA, pid, 51, AGORA)["ok"]
        assert _card(c, lead) == ("proposta", 362000)
        assert cp.nao_fechou(c, CLINICA, pid, "inventado", 51) is None
        assert cp.nao_fechou(c, CLINICA, pid, "preco", 51) == "ganho"
        c.commit()
        assert _card(c, lead) == ("ganho", 50000)
        p = cp.plano(c, CLINICA, pid)
        assert (p["status"], p["nao_fechou_motivo"]) == ("recusado", "Achou caro")
        assert c.execute("select descricao from prospeccao_atividades order by id desc limit 1"
                         ).fetchone()[0] == "Plano não fechado (Achou caro). O card foi para Concluído."
        assert cp.nao_fechou(c, CLINICA, pid, "preco", 51) is None    # já encerrado


def test_recusado_pelo_link_com_retorno_a_fazer_vai_pro_retorno(pool, zap):
    with pool.connection() as c:
        lead, _conv = _paciente(c)
        _atendido(c, lead, retorno_dias=30)
        pid = _plano(c, lead)
        cp.enviar(c, CLINICA, pid, 51, AGORA)
        token = cp.plano(c, CLINICA, pid)["token"]
        assert cp.recusar(c, token)
        c.commit()
        assert _card(c, lead)[0] == "retorno"
        assert cp.plano(c, CLINICA, pid)["nao_fechou_motivo"] == "recusou pelo link"


def test_plano_que_nao_fecha_com_resultado_a_entregar_vai_pro_retorno(pool, zap):
    with pool.connection() as c:
        lead, _conv = _paciente(c)
        eid = _atendido(c, lead)
        from finance import clinica_pacotes as ckp
        assert ckp.pedir_resultado(c, CLINICA, ca.evento(c, CLINICA, eid), None)
        pid = _plano(c, lead)
        cp.enviar(c, CLINICA, pid, 51, AGORA)
        assert cp.nao_fechou(c, CLINICA, pid, "preco", 51) == "retorno"


def test_plano_vencido_tira_o_card_da_coluna_do_plano(pool, zap):
    with pool.connection() as c:
        lead, _conv = _paciente(c)
        _atendido(c, lead)
        pid = _plano(c, lead)
        cp.enviar(c, CLINICA, pid, 51, AGORA)
        assert cp.cobrar(pool, c, CLINICA, AGORA + timedelta(days=8))["vencidos"] == 1
        assert _card(c, lead)[0] == "ganho"
        assert cp.plano(c, CLINICA, pid)["nao_fechou_motivo"] == "venceu sem resposta"


def test_o_que_segura_o_card_na_coluna_do_plano(pool, zap):
    """Fica onde está: quem nunca foi atendido (é venda, segue com a vendedora) e quem
    ainda tem outro plano em jogo."""
    with pool.connection() as c:
        lead, _conv = _paciente(c)
        pid = _plano(c, lead)
        cp.enviar(c, CLINICA, pid, 51, AGORA)
        assert cp.nao_fechou(c, CLINICA, pid, "adiou", 51) == "fica"
        assert _card(c, lead)[0] == "proposta"                        # nunca veio: segue a venda
        outro, _conv = _paciente(c, nome="Rita Souza", fone="+5599988880002")
        _atendido(c, outro)
        p1, p2 = _plano(c, outro), _plano(c, outro)
        cp.enviar(c, CLINICA, p1, 51, AGORA)
        assert cp.nao_fechou(c, CLINICA, p1, "preco", 51) == "fica"
        assert _card(c, outro)[0] == "proposta"                       # o p2 ainda está montando
        assert cp.nao_fechou(c, CLINICA, p2, "preco", 51) == "ganho"
        assert _card(c, outro)[0] == "ganho"


def test_o_paciente_que_saiu_sem_querer_o_plano(pool, zap):
    """Antes de o plano ser montado: o card sai de Consulta ("plano a montar")."""
    with pool.connection() as c:
        lead, _conv = _paciente(c)
        eid = _atendido(c, lead)
        assert cp.nao_quis_sem_plano(c, CLINICA, eid, "inventado", 51) is None
        assert cp.nao_quis_sem_plano(c, CLINICA, eid, "outra_clinica", 51) == "ganho"
        assert "Fez em outra clínica" in c.execute(
            "select descricao from prospeccao_atividades order by id desc limit 1").fetchone()[0]
        outro, _conv = _paciente(c, nome="Rita Souza", fone="+5599988880002")
        eid2 = _atendido(c, outro, h=10)
        c.execute("update clinica_planos set evento_id=%s where id=%s", (eid2, _plano(c, outro)))
        assert cp.nao_quis_sem_plano(c, CLINICA, eid2, "preco", 51) is None   # já tem plano: é pelo plano


def test_o_plano_da_mae_recusado_nao_fecha_o_plano_a_montar_do_filho(pool, zap):
    """Mãe e filho no mesmo card, os dois com proposta. O plano da mãe é recusado; o do
    filho ainda nem foi montado: o card volta pra Consulta, não vai pra Concluído."""
    with pool.connection() as c:
        lead, _conv = _paciente(c)
        mae = _atendido(c, lead, h=9)
        _atendido(c, lead, h=10)                                      # o filho, mesmo card
        pid = _plano(c, lead)
        c.execute("update clinica_planos set evento_id=%s where id=%s", (mae, pid))
        assert cp.enviar(c, CLINICA, pid, 51, AGORA)["ok"]
        assert _card(c, lead)[0] == "proposta"
        assert cp.nao_fechou(c, CLINICA, pid, "preco", 51) == "consulta"
        assert _card(c, lead) == ("consulta", 50000)
        assert "outro atendimento dele ainda espera a clínica" in c.execute(
            "select descricao from prospeccao_atividades order by id desc limit 1").fetchone()[0]


def test_o_valor_volta_ao_da_consulta_e_nao_ao_da_ultima_sessao(pool, zap):
    with pool.connection() as c:
        lead, _conv = _paciente(c)
        _atendido(c, lead, h=9)                                       # consulta, R$ 500
        sessao, erro = ca.agendar(c, CLINICA, profissional_id=_manoel(c),
                                  servico_id=_tipo(c, "Procedimento estético")["id"],
                                  inicio=ca.utc(date(2026, 9, 28), time(11)), lead_id=lead, agora=AGORA)
        assert erro is None
        for s in ("confirmado", "presente", "atendimento", "finalizado"):
            assert ca.mudar_situacao(c, CLINICA, sessao, s) is None   # R$ 800, a última passagem
        pid = _plano(c, lead)
        cp.enviar(c, CLINICA, pid, 51, AGORA)
        assert cp.nao_fechou(c, CLINICA, pid, "adiou", 51) == "ganho"
        assert _card(c, lead) == ("ganho", 50000)


def test_desistiu_depois_de_aceitar_e_antes_de_pagar(pool, zap):
    """O aceito que nunca é pago tem saída: o plano encerra com o motivo, o pacote que
    nasceu no aceite é encerrado e o card sai da coluna do plano."""
    with pool.connection() as c:
        lead, _conv = _paciente(c)
        _atendido(c, lead)
    pid = _aceito(pool, lead)
    with pool.connection() as c:
        assert c.execute("select count(*) from clinica_pacotes where plano_id=%s and estado='ativo'",
                         (pid,)).fetchone()[0] == 1
        assert cp.nao_fechou(c, CLINICA, pid, "adiou", 51) == "ganho"
        c.commit()
        assert c.execute("select estado, encerrado_motivo from clinica_pacotes where plano_id=%s", (pid,)
                         ).fetchone() == ("encerrado", "desistiu antes de pagar (Vai pensar / adiou)")
        assert cp.em_aberto(c, CLINICA, AGORA)["a_pagar"] == []
        assert cp.receber(pool, CLINICA, pid, 51)["ok"] is False      # não está mais aceito


def test_o_pagamento_nao_tira_de_perdido_e_leva_de_concluido_pra_em_tratamento(pool, zap):
    with pool.connection() as c:
        lead, _conv = _paciente(c)
        c.execute("update prospeccao set status='ganho' where id=%s", (lead,))
        c.commit()
    pid = _aceito(pool, lead)                                         # o paciente que já tinha concluído
    with pool.connection() as c:
        assert _card(c, lead)[0] == "ganho"                           # aceitar não mexe
    assert cp.receber(pool, CLINICA, pid, 51)["destino"] == "tratamento"
    with pool.connection() as c:
        outro, _conv = _paciente(c, nome="Rita Souza", fone="+5599988880002")
    p2 = _aceito(pool, outro)
    with pool.connection() as c:
        c.execute("update prospeccao set status='perdido' where id=%s", (outro,))
        c.commit()
    assert cp.receber(pool, CLINICA, p2, 51) == {"ok": True, "destino": None}
    with pool.connection() as c:
        assert _card(c, outro)[0] == "perdido"


def test_o_relogio_confere_a_conta_que_so_tem_plano_aceito(pool, zap):
    with pool.connection() as c:
        lead, _conv = _paciente(c)
    pid = _aceito(pool, lead)
    with pool.connection() as c:
        c.execute("update titulos set status='pago' where id=%s", (cp.plano(c, CLINICA, pid)["titulos"][0],))
        c.commit()
    assert cp.rodar(pool, AGORA)["pagos"] == 1
    with pool.connection() as c:
        assert _card(c, lead)[0] == "tratamento"


def test_funil_de_antes_nada_muda(pool, zap):
    with pool.connection() as c:
        c.execute("delete from funil_etapas where conta_id=%s and chave in ('consulta','tratamento','retorno')",
                  (CLINICA,))
        lead, _conv = _paciente(c)
        _atendido(c, lead)
        assert _card(c, lead)[0] == "proposta"                        # o Finalizar de antes
        pid = _plano(c, lead)
        cp.enviar(c, CLINICA, pid, 51, AGORA)
        assert cp.nao_fechou(c, CLINICA, pid, "preco", 51)
        assert _card(c, lead)[0] == "proposta"                        # como sempre foi
    with pool.connection() as c:
        lead2, _conv = _paciente(c, nome="Rita Souza", fone="+5599988880002")
    pid2 = _aceito(pool, lead2)
    with pool.connection() as c:
        assert _card(c, lead2)[0] == "ganho"                          # aceitou: Fechado, como sempre
    assert cp.receber(pool, CLINICA, pid2, 51) == {"ok": True, "destino": None}
    with pool.connection() as c:
        assert cp.em_aberto(c, CLINICA, AGORA)["a_pagar"] == []      # o aceite já fechou o card


def test_telas_recebi_e_nao_quis(cli, pool, zap):
    with pool.connection() as c:
        lead, _conv = _paciente(c)
        eid = _atendido(c, lead)
    html = cli.get(f"/painel/clinica/planos/novo?evento={eid}").text
    assert "O paciente não quis o plano" in html and "Fez em outra clínica" in html
    r = cli.post("/painel/clinica/planos/nao-quis", data={"evento": str(eid), "motivo": ""})
    assert "evento=" in r.headers["location"]
    r = cli.post("/painel/clinica/planos/nao-quis", data={"evento": str(eid), "motivo": "adiou"})
    assert r.headers["location"] == "/painel/clinica/planos?aviso=nao_quis"
    with pool.connection() as c:
        assert _card(c, lead)[0] == "ganho"
        outro, _conv = _paciente(c, nome="Rita Souza", fone="+5599988880002")
    pid = _aceito(pool, outro)
    pagina = cli.get(f"/painel/clinica/planos/{pid}").text
    assert "Recebi o pagamento" in pagina and "aguardando" in pagina
    assert "aguardando pagamento" in cli.get("/painel/clinica/planos").text
    r = cli.post(f"/painel/clinica/planos/{pid}/recebi")
    assert "aviso=recebido" in r.headers["location"]
    assert "Recebi o pagamento" not in cli.get(f"/painel/clinica/planos/{pid}").text
    with pool.connection() as c:
        p3 = _plano(c, outro)
    assert "Escolha o motivo" in cli.get(cli.post(f"/painel/clinica/planos/{p3}/nao_quis",
                                                  data={"motivo": ""}).headers["location"]).text
    r = cli.post(f"/painel/clinica/planos/{p3}/nao_quis", data={"motivo": "preco"})
    assert "aviso=nao_quis" in r.headers["location"]
    assert "Achou caro" in cli.get(f"/painel/clinica/planos/{p3}").text


def test_aceite_na_conta_com_o_funil_de_antes_fecha_o_card(pool, zap):
    with pool.connection() as c:
        c.execute("delete from funil_etapas where conta_id=%s and chave in ('consulta','tratamento','retorno')",
                  (CLINICA,))
        lead, _conv = _paciente(c)
        pid = _plano(c, lead)
        cp.enviar(c, CLINICA, pid, 51, AGORA)
        token = cp.plano(c, CLINICA, pid)["token"]
    assert cp.aceitar(pool, token, nome="Lúcia Ferreira", forma="cartao", agora=AGORA)
    with pool.connection() as c:
        assert c.execute("select status from prospeccao where id=%s", (lead,)).fetchone()[0] == "ganho"


# ------------------------------------------------------------------ o aceite

def test_aceite_pelo_link_gera_titulos_e_fecha_o_card(pool, zap):
    with pool.connection() as c:
        lead, _conv = _paciente(c)
        pid = _plano(c, lead)
        cp.enviar(c, CLINICA, pid, 51, AGORA)
        token = cp.plano(c, CLINICA, pid)["token"]
    assert cp.aceitar(pool, token, nome="Lúcia Ferreira", forma="cartao", ip="1.2.3.4", agora=AGORA)
    assert not cp.aceitar(pool, token, nome="Lúcia Ferreira", forma="pix", agora=AGORA)   # uma vez só
    with pool.connection() as c:
        p = cp.plano(c, CLINICA, pid)
        assert (p["status"], p["aceito_forma"], p["aceito_por"], p["titulos"]) == ("aceito", "cartao", "link", [1, 2, 3, 4])
        # PLANO ACEITO NÃO É PLANO PAGO: o card fica em Plano enviado, esperando o pagamento
        assert c.execute("select status from prospeccao where id=%s", (lead,)).fetchone()[0] == "proposta"
        assert p["pago_em"] is None
        assert "aguardando o pagamento" in c.execute(
            "select descricao from prospeccao_atividades order by id desc limit 1").fetchone()[0]
    assert sum(v for _d, v, _venc in zap.titulos) == 362000
    assert [venc for _d, _v, venc in zap.titulos] == [date(2026, 9, 25), date(2026, 10, 25),
                                                      date(2026, 11, 25), date(2026, 12, 25)]
    assert zap.titulos[0][0] == "Plano de tratamento · Lúcia Ferreira — parcela 1/4 (cartão)"
    assert zap.avisos == ["✅ Plano aceito: Lúcia Ferreira"]


def test_aceite_vencido_nao_vale(pool, zap):
    with pool.connection() as c:
        lead, _conv = _paciente(c)
        pid = _plano(c, lead)
        cp.enviar(c, CLINICA, pid, 51, AGORA)
        token = cp.plano(c, CLINICA, pid)["token"]
    assert not cp.aceitar(pool, token, nome="Lúcia", forma="pix", agora=AGORA + timedelta(days=8))


def test_2_no_whatsapp_aceita_no_cartao(pool, zap):
    with pool.connection() as c:
        lead, conv = _paciente(c)
        pid = _plano(c, lead)
        cp.enviar(c, CLINICA, pid, 51, AGORA)
        _diz(c, conv, "2")
        saiu = []
        assert cp.processar(pool, c, CLINICA, AGORA, conversa_id=conv, responder=saiu.append) == 1
        p = cp.plano(c, CLINICA, pid)
        assert (p["status"], p["aceito_forma"], p["aceito_por"]) == ("aceito", "cartao", "whatsapp")
    assert saiu[0].startswith("Perfeito! Anotei: Cartão de crédito.")


def test_1_depois_de_outro_pedido_nao_e_do_plano(pool, zap):
    """Plano enviado, depois o lembrete da consulta ("Responda 1"): o "1" é do lembrete."""
    with pool.connection() as c:
        lead, conv = _paciente(c)
        pid = _plano(c, lead)
        cp.enviar(c, CLINICA, pid, 51, AGORA)
        _diz(c, conv, "Amanhã você tem consulta às 10h. Responda 1 para confirmar.", autor="bot")
        _diz(c, conv, "1")
        assert cp.processar(pool, c, CLINICA, AGORA) == 0
        assert cp.plano(c, CLINICA, pid)["status"] == "enviado"


def test_recusar_pelo_link(pool, zap):
    with pool.connection() as c:
        lead, _conv = _paciente(c)
        pid = _plano(c, lead)
        cp.enviar(c, CLINICA, pid, 51, AGORA)
        assert cp.recusar(c, cp.plano(c, CLINICA, pid)["token"])
        c.commit()
        assert cp.plano(c, CLINICA, pid)["status"] == "recusado"
    assert zap.avisos == ["Plano recusado: Lúcia Ferreira"]


# ------------------------------------------------------------------ cobrar a decisão

def test_d1_d3_sem_procedimento_e_uma_por_dia(pool, zap):
    with pool.connection() as c:
        lead, conv = _paciente(c)
        pid = _plano(c, lead)
        cp.enviar(c, CLINICA, pid, 51, AGORA)
        seg = AGORA + timedelta(days=3)                      # segunda 28/09, 09:00
        assert cp.cobrar(pool, c, CLINICA, seg)["toques"] == 1
        assert cp.cobrar(pool, c, CLINICA, seg + timedelta(hours=2))["toques"] == 0   # 1 por dia
        assert cp.cobrar(pool, c, CLINICA, seg + timedelta(days=1))["toques"] == 1   # terça: o D+3
        assert cp.cobrar(pool, c, CLINICA, seg + timedelta(days=2))["toques"] == 0   # acabou
    d1, d3 = zap.saiu[1][1], zap.saiu[2][1]
    assert d1.startswith("Oi, Lúcia! Conseguiu ver o seu plano de tratamento?")
    assert "Pix à vista ou em até 4× no cartão. Ele vale até 02/10" in d3
    assert "Procedimento" not in d1 + d3 and "estético" not in d1 + d3


def test_paciente_respondeu_o_automatico_para(pool, zap):
    with pool.connection() as c:
        lead, conv = _paciente(c)
        pid = _plano(c, lead)
        cp.enviar(c, CLINICA, pid, 51, AGORA)
        _diz(c, conv, "vou ver com meu marido")
        assert cp.cobrar(pool, c, CLINICA, AGORA + timedelta(days=3))["toques"] == 0
        assert [p["id"] for p in cp.em_aberto(c, CLINICA, AGORA)["responderam"]] == [pid]


def test_vespera_avisa_a_recepcao_e_depois_vence(pool, zap):
    with pool.connection() as c:
        lead, _conv = _paciente(c)
        pid = _plano(c, lead)
        cp.enviar(c, CLINICA, pid, 51, AGORA)
        cp.salvar_config(c, CLINICA, teto="10", pix="0", parcelas="4", validade="7", cobranca="off")
        c.commit()
        assert cp.cobrar(pool, c, CLINICA, AGORA + timedelta(days=6))["avisos"] == 1     # qui 01/10
        assert cp.cobrar(pool, c, CLINICA, AGORA + timedelta(days=6, hours=2))["avisos"] == 0
        assert cp.cobrar(pool, c, CLINICA, AGORA + timedelta(days=8))["vencidos"] == 1   # sáb 03/10
        assert cp.plano(c, CLINICA, pid)["status"] == "vencido"
    assert zap.avisos == ["⏳ Plano vence amanhã: Lúcia Ferreira"]
    assert len(zap.saiu) == 1                                  # cobrança desligada: nenhum toque


# ------------------------------------------------------------------ as telas

@pytest.fixture()
def cli(pool, monkeypatch):
    from web import painel_clinica_agenda as pa
    from web import painel_clinica_planos as pp
    monkeypatch.setattr(pp, "get_pool", lambda: pool)
    monkeypatch.setattr(pa, "conta_logada", lambda request: (CLINICA,))
    monkeypatch.setattr(pa, "nicho_da_conta", lambda conta: "clinica")
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="teste")
    app.include_router(pp.router)

    @app.get("/_papel/{papel}")
    def _papel(request: Request, papel: str):
        request.session["papel"] = papel
        request.session["membro_id"] = 51
        return {}
    c = TestClient(app, follow_redirects=False)
    c.get("/_papel/vendedor")
    return c


def test_da_consulta_ao_link_e_o_aceite(cli, pool, zap):
    with pool.connection() as c:
        lead, _conv = _paciente(c, nome="Lúcia <b>Ferreira</b>")
        seg = ca.hoje_br() + timedelta(days=7)
        while seg.isoweekday() > 5:
            seg += timedelta(days=1)
        eid, erro = ca.agendar(c, CLINICA, profissional_id=_manoel(c), servico_id=_tipo(c, "Consulta")["id"],
                               inicio=ca.utc(seg, time(9)), lead_id=lead)
        assert erro is None
        c.commit()
        estetico = _tipo(c, "Procedimento estético")["id"]
    html = cli.get(f"/painel/clinica/planos/novo?evento={eid}").text
    assert 'value="Lúcia &lt;b&gt;Ferreira&lt;/b&gt;"' in html and "seu teto: 10.0%" in html
    r = cli.post("/painel/clinica/planos/salvar", data={
        "evento_id": str(eid), "lead": str(lead), "paciente": "Lúcia Ferreira", "fone": FONE,
        "profissional_id": "", "tipo_0": str(estetico), "sessoes_0": "4", "valor_0": "",
        "desconto": "5", "pix": "0", "parcelas": "3", "parcelado": "1", "acao": "enviar"})
    assert "aviso=enviado" in r.headers["location"]
    pid = int(r.headers["location"].split("/")[-1].split("?")[0])
    assert cli.get(f"/painel/clinica/planos/novo?evento={eid}").headers["location"] == f"/painel/clinica/planos/{pid}"
    with pool.connection() as c:
        token = cp.plano(c, CLINICA, pid)["token"]
    pub = cli.get(f"/plano/{token}").text
    assert "Procedimento estético" in pub and "R$ 3.040" in pub and "Aceitar" in pub
    r = cli.post(f"/plano/{token}/aceitar", data={"nome": "Lúcia Ferreira", "forma": "pix", "concordo": "1"})
    assert r.headers["location"] == f"/plano/{token}?ok=aceito"
    assert "Plano aceito" in cli.get(f"/plano/{token}").text
    assert cli.get("/plano/nao-existe").status_code == 404


def test_so_gerencia_muda_o_teto(cli, pool):
    cli.post("/painel/clinica/planos/config", data={"teto": "30", "pix": "0", "parcelas": "4",
                                                   "validade": "7", "cobranca": "ligado"})
    with pool.connection() as c:
        assert cp.config(c, CLINICA)["teto_desconto"] == 10.0
    cli.get("/_papel/dono")
    cli.post("/painel/clinica/planos/config", data={"teto": "30", "pix": "5", "parcelas": "6",
                                                   "validade": "10", "cobranca": "off"})
    with pool.connection() as c:
        assert cp.config(c, CLINICA) == {"teto_desconto": 30.0, "pix_desconto": 5.0, "cartao_parcelas": 6,
                                         "validade_dias": 10, "cobranca": "off"}


# ------------------------------------------------------------------ revisão do PR

def test_teto_vale_pro_desconto_efetivo(pool, zap):
    """Desconto 10% + Pix 50%, ou a sessão abaixo do preço de tabela: vai pro dono."""
    with pool.connection() as c:
        lead, _conv = _paciente(c)
        tipos = {t["id"]: t for t in cc.listar_tipos(c, CLINICA)}
        est = _tipo(c, "Procedimento estético")["id"]
        for itens_raw, desc, pix in (([{"servico_id": est, "sessoes": 4, "valor": ""}], "10", "50"),
                                     ([{"servico_id": est, "sessoes": 4, "valor": "100"}], "0", "0")):
            itens, _e = cp.limpar_itens(itens_raw, tipos)
            pid, _e = cp.salvar(c, CLINICA, lead=lead, evento_id=None, profissional_id=None, paciente="Lúcia",
                                fone=FONE, itens=itens, desconto_pct=desc, pix_desconto_pct=pix,
                                cartao_parcelas="4", parcelado=True, membro_id=51, pode_aprovar=False)
            assert cp.plano(c, CLINICA, pid)["status"] == "aguardando_aprovacao"


def test_aprovar_so_o_que_o_dono_viu(pool, zap):
    with pool.connection() as c:
        lead, _conv = _paciente(c)
        pid = _plano(c, lead, desconto="15", pode=False)
        visto = cp.plano(c, CLINICA, pid)["versao"]
        c.execute("update clinica_planos set desconto_pct=40, atualizado_em=now() + interval '1 second' where id=%s",
                  (pid,))
        c.commit()
        assert not cp.aprovar_desconto(c, CLINICA, pid, 52, visto)
        assert cp.aprovar_desconto(c, CLINICA, pid, 52, cp.plano(c, CLINICA, pid)["versao"])


def test_whatsapp_fora_do_ar_volta_pra_rascunho(pool, zap, monkeypatch):
    monkeypatch.setattr(agente, "_mandar", lambda *a, **k: {"ok": False, "erro": "fora"})
    with pool.connection() as c:
        lead, conv = _paciente(c)
        pid = _plano(c, lead)
        c.execute("update prospeccao set status='consulta' where id=%s", (lead,))
        c.commit()
        r = cp.enviar(c, CLINICA, pid, 51, AGORA)
        assert not r["ok"] and "tente enviar de novo" in r["erro"]
        assert cp.plano(c, CLINICA, pid)["status"] == "rascunho"
        # o plano não saiu: o card continua em Consulta (plano a montar), onde ninguém o cobra
        assert c.execute("select status from prospeccao where id=%s", (lead,)).fetchone()[0] == "consulta"
        assert c.execute("select count(*) from funil_movimentos where para='proposta'").fetchone()[0] == 0
        _diz(c, conv, "1")
        assert cp.processar(pool, c, CLINICA, AGORA) == 0


def test_numero_que_responde_a_recepcao_nao_e_do_plano(pool, zap):
    """A recepção perguntou "em quantas vezes?" e o paciente disse "3"."""
    with pool.connection() as c:
        lead, conv = _paciente(c)
        pid = _plano(c, lead)
        cp.enviar(c, CLINICA, pid, 51, AGORA)
        _diz(c, conv, "No cartão, em quantas vezes?", autor="humano")
        _diz(c, conv, "3")
        assert cp.processar(pool, c, CLINICA, AGORA) == 0
        assert cp.plano(c, CLINICA, pid)["status"] == "enviado"


def test_3_sem_a_opcao_oferecida_nao_vale(pool, zap):
    with pool.connection() as c:
        lead, conv = _paciente(c)
        itens, _e = cp.limpar_itens([{"servico_id": _tipo(c, "Procedimento estético")["id"], "sessoes": 1,
                                      "valor": ""}], {t["id"]: t for t in cc.listar_tipos(c, CLINICA)})
        pid, _e = cp.salvar(c, CLINICA, lead=lead, evento_id=None, profissional_id=None, paciente="Lúcia",
                            fone=FONE, itens=itens, desconto_pct="0", pix_desconto_pct="0", cartao_parcelas="1",
                            parcelado=True, membro_id=51, pode_aprovar=True)
        c.commit()
        cp.enviar(c, CLINICA, pid, 51, AGORA)
        _diz(c, conv, "3")
        assert cp.processar(pool, c, CLINICA, AGORA) == 0


def test_card_ganha_o_valor_do_plano_mesmo_ja_em_proposta(pool, zap):
    with pool.connection() as c:
        lead, _conv = _paciente(c)
        c.execute("update prospeccao set status='proposta', valor_estimado_centavos=150000 where id=%s", (lead,))
        c.commit()
        pid = _plano(c, lead)
        cp.enviar(c, CLINICA, pid, 51, AGORA)
        assert c.execute("select valor_estimado_centavos from prospeccao where id=%s", (lead,)).fetchone()[0] == 362000


def test_gerar_titulos_que_faltaram(pool, zap, monkeypatch):
    from finance import empresa
    with pool.connection() as c:
        lead, _conv = _paciente(c)
        pid = _plano(c, lead)
        cp.enviar(c, CLINICA, pid, 51, AGORA)
        token = cp.plano(c, CLINICA, pid)["token"]
    orig = empresa.criar_titulo
    monkeypatch.setattr(empresa, "criar_titulo", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("caiu")))
    assert cp.aceitar(pool, token, nome="Lúcia", forma="pix", agora=AGORA)
    with pool.connection() as c:
        assert cp.plano(c, CLINICA, pid)["titulos"] == []
    monkeypatch.setattr(empresa, "criar_titulo", orig)
    assert cp.gerar_titulos(pool, CLINICA, pid) == 1
    assert cp.gerar_titulos(pool, CLINICA, pid) == 0              # nunca duplica
