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
        for m in ("360_clinica_agenda.sql", "363_clinica_repasses.sql", "369_clinica_vagas.sql",
                  "373_clinica_planos.sql"):
            c.execute((BASE / m).read_text(encoding="utf-8"))
        c.execute((BASE / next(BASE.glob("346_*.sql")).name).read_text(encoding="utf-8"))
        c.execute("update servicos_catalogo set setup_centavos=80000 where conta_id=39 and nome='Procedimento estético'")
        c.commit()
    yield p
    p.close()


@pytest.fixture()
def zap(monkeypatch):
    estado = types.SimpleNamespace(saiu=[], avisos=[], titulos=[])

    def _mandar(c, conta_id, canal, destino, texto, conversa_id=None):
        estado.saiu.append((conversa_id, texto))
        return {"ok": True, "sid": f"s{len(estado.saiu)}"}
    monkeypatch.setattr(agente, "_mandar", _mandar)
    from finance import clinica_agente as cla
    monkeypatch.setattr(cla, "_aviso", lambda *a, **k: estado.avisos.append(a[3]))
    from finance import empresa

    def _titulo(pool, conta_id, tipo, descricao, valor, venc, **kw):
        estado.titulos.append((descricao, valor, venc))
        return {"id": len(estado.titulos)}
    monkeypatch.setattr(empresa, "criar_titulo", _titulo)
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
        assert cp.aprovar_desconto(c, CLINICA, pid, 52)
        c.commit()
        assert cp.plano(c, CLINICA, pid)["status"] == "rascunho"
        pid2 = _plano(c, lead, desconto="8", pode=False)        # dentro do teto de 10%
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
        assert cp.enviar(c, CLINICA, pid, 51, AGORA)["ok"] is False          # não sai duas vezes
    assert len(zap.saiu) == 1 and zap.saiu[0][0] == conv
    texto = zap.saiu[0][1]
    assert texto.startswith("Oi, Lúcia! Dr. Manoel montou o seu plano de tratamento 💚")
    assert "Procedimento estético · 4 sessões · R$ 3.200" in texto and "Total: R$ 3.620" in texto
    assert "À vista no Pix: R$ 3.330,40 (−8%)" in texto and "4× de R$ 905 no cartão" in texto
    assert f"/plano/{p['token']}" in texto and "responda 1 para o Pix, 2 para o cartão ou 3" in texto


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
        assert c.execute("select status from prospeccao where id=%s", (lead,)).fetchone()[0] == "ganho"
        assert c.execute("select motivo from funil_movimentos order by id desc limit 1").fetchone()[0] == "plano"
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
