"""Pacote com saldo, retorno programado e os lembretes (finance/clinica_pacotes.py).

Em cima da semente da Espaço Pelle (350). A Lúcia aceita um plano de 4 sessões de
Procedimento estético com o Dr. Manoel; cada atendimento finalizado baixa uma.
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
from finance import clinica_pacotes as ckp
from finance import clinica_planos as cp
from tests.test_clinica_agenda import _SQL, AGORA, BASE, CLINICA, SEG

FONE = "+5599988880001"


@pytest.fixture()
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True,
                           kwargs={"autocommit": True, "prepare_threshold": None})
    dbname = "zaq_clinica_pacotes_teste"
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
        c.execute("""alter table mensagens add column midia_tipo text;
                     create table titulos (id bigserial primary key, conta_id bigint, status text default 'aberto',
                                           vencimento date, valor_centavos bigint);""")
        for m in ("071_servicos_catalogo.sql", "148_servico_categoria_foto.sql", "153_servico_icone.sql",
                  "098_agenda.sql", "099_agenda_tipo.sql", "130_evento_desfecho.sql",
                  "136_visita_agenda.sql", "179_agenda_tipo_e_hora_sugerida.sql", "348_clinica_base.sql",
                  "350_clinica_semente_espaco_pelle.sql"):
            c.execute((BASE / m).read_text(encoding="utf-8"))
        c.execute("alter table eventos_agenda add column if not exists marcado_por text")
        for m in ("360_clinica_agenda.sql", "363_clinica_repasses.sql", "369_clinica_vagas.sql",
                  "379_clinica_planos.sql", "381_clinica_pacotes.sql"):
            c.execute((BASE / m).read_text(encoding="utf-8"))
        c.execute((BASE / next(BASE.glob("346_*.sql")).name).read_text(encoding="utf-8"))
        c.execute("""update servicos_catalogo set setup_centavos=80000, volta_dias=null
                      where conta_id=39 and nome='Procedimento estético'""")
        c.commit()
    yield p
    p.close()


@pytest.fixture()
def zap(monkeypatch):
    estado = types.SimpleNamespace(saiu=[], avisos=[])

    def _mandar(c, conta_id, canal, destino, texto, conversa_id=None):
        estado.saiu.append((conversa_id, texto))
        return {"ok": True, "sid": f"s{len(estado.saiu)}"}
    monkeypatch.setattr(agente, "_mandar", _mandar)
    from finance import clinica_agente as cla
    monkeypatch.setattr(cla, "_aviso", lambda *a, **k: estado.avisos.append(a[3]))
    from finance import empresa

    def _titulo(pool, conta_id, tipo, descricao, valor, venc, **kw):
        with pool.connection() as c:
            tid = c.execute("insert into titulos (conta_id, vencimento, valor_centavos) values (%s,%s,%s) returning id",
                            (conta_id, venc, valor)).fetchone()[0]
            c.commit()
        return {"id": tid}
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
                 values (%s,'whatsapp','in','lead','oi',%s)""", (conv, AGORA - timedelta(days=5)))
    c.commit()
    return lead, conv


def _plano_aceito(pool, lead, forma="cartao"):
    with pool.connection() as c:
        itens, _e = cp.limpar_itens(
            [{"servico_id": _tipo(c, "Procedimento estético")["id"], "sessoes": 4, "valor": ""},
             {"servico_id": None, "nome": "Protocolo domiciliar", "sessoes": 1, "valor": "420"}],
            {t["id"]: t for t in cc.listar_tipos(c, CLINICA)})
        pid, _e = cp.salvar(c, CLINICA, lead=lead, evento_id=None, profissional_id=_manoel(c), paciente="Lúcia Ferreira",
                            fone=FONE, itens=itens, desconto_pct="0", pix_desconto_pct="0", cartao_parcelas="4",
                            parcelado=True, membro_id=51, pode_aprovar=True)
        c.commit()
        cp.enviar(c, CLINICA, pid, 51, AGORA)
        token = cp.plano(c, CLINICA, pid)["token"]
    assert cp.aceitar(pool, token, nome="Lúcia Ferreira", forma=forma, agora=AGORA)
    return pid


def _sessao(c, lead, dia, h=8, tipo="Procedimento estético"):
    eid, erro = ca.agendar(c, CLINICA, profissional_id=_manoel(c), servico_id=_tipo(c, tipo)["id"],
                           inicio=ca.utc(dia, time(h)), lead_id=lead, agora=AGORA)
    assert erro is None, erro
    return eid


def _finalizar(c, eid, **kw):
    for s in ("confirmado", "presente", "atendimento"):
        assert ca.mudar_situacao(c, CLINICA, eid, s) is None
    assert ca.mudar_situacao(c, CLINICA, eid, "finalizado", **kw) is None
    c.commit()


def _br(dia: date, h=9):
    return ca.utc(dia, time(h))


# ------------------------------------------------------------------ o saldo

def test_plano_aceito_vira_saldo_so_do_catalogo(pool, zap):
    with pool.connection() as c:
        lead, _conv = _paciente(c)
    _plano_aceito(pool, lead)
    with pool.connection() as c:
        ks = ckp.listar(c, CLINICA)
    assert [(k["nome"], k["total"], k["usadas"], k["intervalo"], k["validade_ate"]) for k in ks] == [
        ("Procedimento estético", 4, 0, 21, date(2027, 9, 25))]          # o protocolo (avulso) não vira saldo
    with pool.connection() as c:                                           # aceitar de novo não duplica
        assert ckp.criar_do_plano(c, CLINICA, cp.plano(c, CLINICA, ks[0]["plano_id"])) == []


def test_finalizar_baixa_uma_sessao_e_conclui_na_ultima(pool, zap):
    with pool.connection() as c:
        lead, _conv = _paciente(c)
    _plano_aceito(pool, lead)
    with pool.connection() as c:
        eid = _sessao(c, lead, SEG)
        assert ckp.para_o_evento(c, CLINICA, ca.evento(c, CLINICA, eid))["usadas"] == 0
        _finalizar(c, eid)
        k = ckp.do_evento(c, CLINICA, eid)
        assert (k["usadas"], k["saldo"], k["proxima"], k["proxima_n"]) == (1, 3, SEG + timedelta(days=21), 2)
        for n in range(1, 4):
            _finalizar(c, _sessao(c, lead, SEG + timedelta(days=n), h=9))
        k = ckp.listar(c, CLINICA)[0]
        assert (k["usadas"], k["estado"]) == (4, "concluido")
        assert len(ckp.consumos(c, CLINICA, k["id"])) == 4
        extra = _sessao(c, lead, SEG + timedelta(days=7))
        _finalizar(c, extra)                                               # sem saldo: não baixa
        assert ckp.do_evento(c, CLINICA, extra) is None


def test_consulta_que_nao_e_do_pacote_nao_baixa(pool, zap):
    with pool.connection() as c:
        lead, _conv = _paciente(c)
    _plano_aceito(pool, lead)
    with pool.connection() as c:
        eid = _sessao(c, lead, SEG, tipo="Consulta")
        _finalizar(c, eid, tratamento="nao")
        assert ckp.do_evento(c, CLINICA, eid) is None
        assert ckp.listar(c, CLINICA)[0]["usadas"] == 0


# ------------------------------------------------------------------ o retorno

def test_retorno_pedido_chama_7_dias_antes_e_sai_quando_marca(pool, zap):
    with pool.connection() as c:
        lead, conv = _paciente(c, nome="Rui Retorno", fone="+5599911110003")
        eid = _sessao(c, lead, SEG, tipo="Consulta")
        _finalizar(c, eid, tratamento="nao", retorno_dias=30)
        r = ckp.retornos(c, CLINICA, _br(date(2026, 10, 21)), dias=7)
        assert [(x["paciente"], x["vence_em"]) for x in r] == [("Rui Retorno", date(2026, 10, 28))]
        assert ckp.lembrar(c, CLINICA, _br(date(2026, 10, 20)))["retorno"] == 0   # ainda não é hora
        assert ckp.lembrar(c, CLINICA, _br(date(2026, 10, 21)))["retorno"] == 1
        assert ckp.lembrar(c, CLINICA, _br(date(2026, 10, 22)))["retorno"] == 0   # uma vez só
    assert zap.saiu[-1][1] == ("Oi, Rui! Está chegando a hora do seu retorno com Dr. Manoel (até 28/10). "
                               "Qual o melhor dia pra você? É só responder por aqui 😊")
    with pool.connection() as c:
        _sessao(c, lead, date(2026, 10, 27), tipo="Retorno")
        c.commit()
        ckp.fechar_retornos(c, CLINICA, _br(date(2026, 10, 22)))
        c.commit()
        assert ckp.retornos(c, CLINICA, _br(date(2026, 10, 22))) == []


# ------------------------------------------------------------------ os lembretes

def test_lembra_a_proxima_sessao_sem_dizer_o_procedimento(pool, zap):
    with pool.connection() as c:
        lead, conv = _paciente(c)
    _plano_aceito(pool, lead)
    with pool.connection() as c:
        _finalizar(c, _sessao(c, lead, SEG))
        liberou = SEG + timedelta(days=21)                                 # seg 19/10
        assert ckp.lembrar(c, CLINICA, _br(liberou - timedelta(days=1)))["sessao"] == 0
        assert ckp.lembrar(c, CLINICA, _br(liberou))["sessao"] == 1
        assert ckp.lembrar(c, CLINICA, _br(liberou + timedelta(days=3)))["sessao"] == 0   # 14 dias de folga
    texto = zap.saiu[-1][1]
    assert texto == "Oi, Lúcia! Sua 2ª sessão já pode ser marcada 😊 Quer que eu veja um horário pra você? É só responder por aqui."
    assert "estético" not in texto.lower()


def test_quem_ja_marcou_a_proxima_nao_e_lembrado(pool, zap):
    with pool.connection() as c:
        lead, _conv = _paciente(c)
    _plano_aceito(pool, lead)
    with pool.connection() as c:
        _finalizar(c, _sessao(c, lead, SEG))
        _sessao(c, lead, date(2026, 10, 26), h=9)
        c.commit()
        assert ckp.lembrar(c, CLINICA, _br(SEG + timedelta(days=21)))["sessao"] == 0


def test_pacote_perto_de_vencer_avisa_e_depois_vence(pool, zap):
    with pool.connection() as c:
        lead, _conv = _paciente(c)
    _plano_aceito(pool, lead)
    with pool.connection() as c:
        c.execute("update clinica_pacotes set validade_ate=%s", (date(2026, 12, 18),))
        _finalizar(c, _sessao(c, lead, date(2026, 10, 14), h=9))   # a próxima só libera em 04/11
        c.execute("update clinica_agenda_config set pacote_lembretes='ligado'")
        c.commit()
        assert ckp.lembrar(c, CLINICA, _br(date(2026, 10, 19)))["validade"] == 1       # 60 dias antes
        assert "3 sessões do seu pacote, válidas até 18/12/2026" in zap.saiu[-1][1]
        assert ckp.lembrar(c, CLINICA, _br(date(2026, 12, 21)))["vencidos"] == 1
        assert ckp.listar(c, CLINICA)[0]["estado"] == "vencido"


def test_uma_automatica_por_dia_somando_os_lembretes(pool, zap):
    with pool.connection() as c:
        lead, conv = _paciente(c)
    _plano_aceito(pool, lead)
    with pool.connection() as c:
        _finalizar(c, _sessao(c, lead, SEG, tipo="Procedimento estético"))
        dia = SEG + timedelta(days=21)
        c.execute("""insert into clinica_lembretes (conta_id, conversa_id, tipo, ref_id, enviado_em)
                     values (39,%s,'validade',0,%s)""", (conv, _br(dia, 8)))
        c.commit()
        assert ckp.lembrar(c, CLINICA, _br(dia))["sessao"] == 0
        from finance import clinica_vagas as cvg
        assert cvg._recebeu_hoje(c, CLINICA, conv, ca.utc(dia, time(0)))


# ------------------------------------------------------------------ parcela atrasada e encerrar

def test_parcela_atrasada_so_trava_com_a_regra_ligada(pool, zap):
    with pool.connection() as c:
        lead, _conv = _paciente(c)
    _plano_aceito(pool, lead)
    with pool.connection() as c:
        est = _tipo(c, "Procedimento estético")["id"]
        c.execute("update titulos set vencimento = current_date - 10 where id = (select min(id) from titulos)")
        c.commit()
        assert ckp.bloqueio(c, CLINICA, lead, FONE, est) is None                  # nasce desligada
        ckp.salvar_config(c, CLINICA, validade="12", lembretes="ligado", aviso="7", bloqueia=True)
        c.commit()
        assert "parcela" in ckp.bloqueio(c, CLINICA, lead, FONE, est)
        c.execute("update titulos set status='pago'")
        c.commit()
        assert ckp.bloqueio(c, CLINICA, lead, FONE, est) is None


def test_encerrar_guarda_o_saldo_e_o_motivo(pool, zap):
    with pool.connection() as c:
        lead, _conv = _paciente(c)
    _plano_aceito(pool, lead)
    with pool.connection() as c:
        kid = ckp.listar(c, CLINICA)[0]["id"]
        assert ckp.encerrar(c, CLINICA, kid, "reembolso de 4 sessões", 52)
        c.commit()
        k = ckp.pacote(c, CLINICA, kid)
        assert (k["estado"], k["saldo"], k["motivo"]) == ("encerrado", 4, "reembolso de 4 sessões")


# ------------------------------------------------------------------ as telas

@pytest.fixture()
def cli(pool, monkeypatch):
    from web import painel_clinica_agenda as pa
    from web import painel_clinica_pacotes as pk
    monkeypatch.setattr(pk, "get_pool", lambda: pool)
    monkeypatch.setattr(pa, "get_pool", lambda: pool)
    monkeypatch.setattr(pa, "conta_logada", lambda request: (CLINICA,))
    monkeypatch.setattr(pa, "nicho_da_conta", lambda conta: "clinica")
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="teste")
    app.include_router(pk.router)
    app.include_router(pa.router)

    @app.get("/_papel/{papel}")
    def _papel(request: Request, papel: str):
        request.session["papel"] = papel
        request.session["membro_id"] = 51
        return {}
    c = TestClient(app, follow_redirects=False)
    c.get("/_papel/vendedor")
    return c


def test_telas_do_pacote_e_marcar_a_proxima(cli, pool, zap):
    with pool.connection() as c:
        lead, _conv = _paciente(c, nome="Lúcia <b>Ferreira</b>")
    _plano_aceito(pool, lead)
    with pool.connection() as c:
        eid = _sessao(c, lead, SEG)
        for s in ("confirmado", "presente", "atendimento"):
            ca.mudar_situacao(c, CLINICA, eid, s)
        c.commit()
    html = cli.get(f"/painel/clinica/agenda/evento/{eid}").text
    assert "Sessão do pacote: finalizar baixa 1 do saldo" in html and 'name="tratamento"' not in html
    r = cli.post(f"/painel/clinica/agenda/evento/{eid}/situacao", data={"nova": "finalizado", "retorno": ""})
    assert "aviso=situacao" in r.headers["location"]
    html = cli.get(f"/painel/clinica/agenda/evento/{eid}").text
    assert "Sessão 1 de 4 baixada" in html and "Marcar a 2ª sessão" in html
    assert f"lead={lead}" in html
    lista = cli.get("/painel/clinica/pacotes").text
    assert "Pacotes e retornos" in lista and "Lúcia Ferreira" in lista and "<b>Ferreira</b>" not in lista
    novo = cli.get(f"/painel/clinica/agenda/novo?lead={lead}").text
    assert "Lúcia &lt;b&gt;Ferreira&lt;/b&gt;" in novo


# ------------------------------------------------------------------ revisão do PR

def test_quem_esta_na_cadeira_nao_recebe_sua_sessao_ja_pode(pool, zap):
    with pool.connection() as c:
        lead, _conv = _paciente(c)
    _plano_aceito(pool, lead)
    with pool.connection() as c:
        _finalizar(c, _sessao(c, lead, SEG))
        dia = SEG + timedelta(days=21)                       # a 2ª libera e está marcada pra hoje 08:00
        eid = _sessao(c, lead, dia)
        ca.mudar_situacao(c, CLINICA, eid, "confirmado")
        ca.mudar_situacao(c, CLINICA, eid, "presente")
        c.commit()
        assert ckp.lembrar(c, CLINICA, ca.utc(dia, time(15)))["sessao"] == 0
        assert ckp.precisam_marcar(c, CLINICA, ca.utc(dia, time(15))) == []


def test_o_mesmo_procedimento_baixa_antes_do_pacote_mais_antigo(pool, zap):
    with pool.connection() as c:
        lead, _conv = _paciente(c)
        est, cons = _tipo(c, "Procedimento estético")["id"], _tipo(c, "Consulta")["id"]
        for sid, dias in ((cons, 3), (est, 1)):                # o de Consulta é o mais antigo
            c.execute("""insert into clinica_pacotes (conta_id, prospeccao_id, paciente_nome, paciente_fone, servico_id,
                                                      nome, sessoes_total, criado_em)
                         values (39,%s,'Lúcia Ferreira',%s,%s,'x',4,now() - %s * interval '1 day')""",
                      (lead, FONE, sid, dias))
        c.commit()
        eid = _sessao(c, lead, SEG)
        _finalizar(c, eid)
        assert ckp.do_evento(c, CLINICA, eid)["servico_id"] == est


def test_mae_e_filho_no_mesmo_card_nao_dividem_saldo(pool, zap):
    with pool.connection() as c:
        lead, _conv = _paciente(c)
    _plano_aceito(pool, lead)                                  # o pacote é da Lúcia
    with pool.connection() as c:
        eid, erro = ca.agendar(c, CLINICA, profissional_id=_manoel(c), servico_id=_tipo(c, "Procedimento estético")["id"],
                               inicio=ca.utc(SEG, time(8)), lead_id=lead, paciente="Pedro Ferreira", agora=AGORA)
        assert erro is None
        assert ckp.para_o_evento(c, CLINICA, ca.evento(c, CLINICA, eid)) is None
        _finalizar(c, eid, tratamento="nao")
        assert ckp.listar(c, CLINICA)[0]["usadas"] == 0


def test_retorno_marcado_antes_do_pedido_fecha_e_fone_vazio_nao_casa(pool, zap):
    with pool.connection() as c:
        lead, _conv = _paciente(c, nome="Rui Retorno", fone="+5599911110003")
        consulta = _sessao(c, lead, SEG, tipo="Consulta")
        volta = _sessao(c, lead, date(2026, 10, 26), tipo="Retorno")       # marcada ANTES de finalizar
        c.commit()
        _finalizar(c, consulta, tratamento="nao", retorno_dias=30)
        ckp.fechar_retornos(c, CLINICA, _br(date(2026, 10, 1)))
        c.commit()
        assert c.execute("select estado, marcado_evento_id from clinica_retornos").fetchone() == ("marcado", volta)
        ca.mudar_situacao(c, CLINICA, volta, "cancelou")                     # desmarcou: volta pra fila
        ckp.fechar_retornos(c, CLINICA, _br(date(2026, 10, 1)))
        c.commit()
        assert c.execute("select estado from clinica_retornos").fetchone()[0] == "aguardando"
        # retorno de paciente sem celular não é fechado pela consulta de outro sem celular
        c.execute("update clinica_retornos set paciente_fone='', prospeccao_id=null")
        outro = c.execute("""insert into eventos_agenda (conta_id, titulo, inicio, tipo, profissional_id, situacao,
                                                         status, paciente_fone, criado_em)
                             values (39,'x',%s,'empresa',%s,'agendado','ativo','',now()) returning id""",
                          (_br(date(2026, 10, 27)), _manoel(c))).fetchone()[0]
        ckp.fechar_retornos(c, CLINICA, _br(date(2026, 10, 1)))
        c.commit()
        assert c.execute("select estado from clinica_retornos").fetchone()[0] == "aguardando"
        assert outro


def test_retorno_pedido_agora_espera_e_sessao_de_pacote_nao_sugere_retorno(cli, pool, zap):
    with pool.connection() as c:
        lead, _conv = _paciente(c, nome="Rui Retorno", fone="+5599911110003")
        eid = _sessao(c, lead, SEG, tipo="Consulta")
        _finalizar(c, eid, tratamento="nao", retorno_dias=7)
        c.execute("update clinica_retornos set criado_em = %s", (_br(date(2026, 9, 28)),))
        c.commit()
        assert ckp.lembrar(c, CLINICA, _br(date(2026, 9, 29)))["retorno"] == 0          # dia seguinte: espera
        assert ckp.lembrar(c, CLINICA, _br(date(2026, 9, 30)))["retorno"] == 1


def test_lembrete_que_falhou_nao_conta(pool, zap, monkeypatch):
    with pool.connection() as c:
        lead, conv = _paciente(c, nome="Rui Retorno", fone="+5599911110003")
        _finalizar(c, _sessao(c, lead, SEG, tipo="Consulta"), tratamento="nao", retorno_dias=30)
        monkeypatch.setattr(agente, "_mandar", lambda *a, **k: {"ok": False})
        assert ckp.lembrar(c, CLINICA, _br(date(2026, 10, 21)))["retorno"] == 0
        from finance import clinica_vagas as cvg
        assert not cvg._recebeu_hoje(c, CLINICA, conv, ca.utc(date(2026, 10, 21), time(0)))
        monkeypatch.setattr(agente, "_mandar", lambda *a, **k: {"ok": True, "sid": "x"})
        assert ckp.lembrar(c, CLINICA, _br(date(2026, 10, 22)))["retorno"] == 1


def test_parcela_atrasada_vale_pro_agente_e_pra_vaga(pool, zap):
    with pool.connection() as c:
        lead, _conv = _paciente(c)
    _plano_aceito(pool, lead)
    with pool.connection() as c:
        c.execute("update titulos set vencimento = current_date - 10 where id = (select min(id) from titulos)")
        ckp.salvar_config(c, CLINICA, validade="12", lembretes="ligado", aviso="7", bloqueia=True)
        c.commit()
        eid, erro = ca.agendar(c, CLINICA, profissional_id=_manoel(c), servico_id=_tipo(c, "Procedimento estético")["id"],
                               inicio=ca.utc(SEG, time(8)), lead_id=lead, marcado_por="ia", agora=AGORA)
        assert eid is None and "parcela" in erro


def test_voltar_a_chamar_soma_a_cobranca_do_plano_e_o_lembrete(pool, zap):  # noqa: F811
    """Com as tabelas de verdade (379 e 381): a cobrança do plano de hoje e o lembrete
    de hoje seguram o toque do voltar a chamar do mesmo paciente."""
    from finance import voltar_a_chamar as vac
    with pool.connection() as c:
        lead, conv = _paciente(c)
    _plano_aceito(pool, lead)
    with pool.connection() as c:
        mid = c.execute("""insert into mensagens (conversa_id, canal, direcao, autor, texto, criado_em)
                           values (%s,'whatsapp','out','humano','A consulta é R$ 500',now()) returning id""",
                        (conv,)).fetchone()[0]
        c.execute("""insert into voltar_a_chamar_toques (conta_id, prospeccao_id, conversa_id, preco_msg_id, toque,
                                                          estado, devido_em)
                     values (39,%s,%s,%s,2,'pendente',now())""", (lead, conv, mid))
        c.commit()
        hoje = vac._inicio_do_dia(datetime.now(timezone.utc))
        sql = ("select count(*) from voltar_a_chamar_toques t where t.conta_id=%(conta)s and "
               + vac._nao_mandado_hoje(c))
        assert c.execute(sql, {"conta": CLINICA, "hoje": hoje}).fetchone()[0] == 1
        c.execute("update clinica_planos set toque1_em = now() where conta_id=%s", (CLINICA,))
        assert c.execute(sql, {"conta": CLINICA, "hoje": hoje}).fetchone()[0] == 0
        c.execute("update clinica_planos set toque1_em = null where conta_id=%s", (CLINICA,))
        c.execute("""insert into clinica_lembretes (conta_id, conversa_id, tipo, ref_id, enviado_em)
                     values (39,%s,'sessao',1,now())""", (conv,))
        assert c.execute(sql, {"conta": CLINICA, "hoje": hoje}).fetchone()[0] == 0
        c.rollback()
