"""Vaga liberada da clínica (finance/clinica_vagas.py e a tela /painel/clinica/vagas).

Em cima da semente da Espaço Pelle (350): o Dr. Manoel atende seg a sex, 08:00–12:00
e 13:30–16:30. Uma consulta cancelada na segunda 28/09 às 08:00 abre a vaga; quem
cabe nela é chamado na ordem dos grupos (pediu horário, já vem no dia, recebeu o
preço, retorno vencido), e fica com quem responder "1" primeiro.
"""
import os
import types
from datetime import datetime, time, timedelta, timezone

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from psycopg_pool import ConnectionPool
from starlette.middleware.sessions import SessionMiddleware

from finance import agente
from finance import clinica_agenda as ca
from finance import clinica_config as cc
from finance import clinica_vagas as cvg
from tests.test_clinica_agenda import _SQL, AGORA, BASE, CLINICA, SEG


@pytest.fixture()
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True,
                           kwargs={"autocommit": True, "prepare_threshold": None})
    dbname = "zaq_clinica_vagas_teste"
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
        for m in ("360_clinica_agenda.sql", "363_clinica_repasses.sql", "365_clinica_vagas.sql"):
            c.execute((BASE / m).read_text(encoding="utf-8"))
        c.execute((BASE / next(BASE.glob("346_*.sql")).name).read_text(encoding="utf-8"))
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
    return estado


def _manoel(c):
    return cc.listar_profissionais(c, CLINICA)[0]["id"]


def _tipo(c, nome):
    return next(t for t in cc.listar_tipos(c, CLINICA) if t["nome"] == nome)["id"]


def _contato(c, nome, fone):
    lead = c.execute("""insert into prospeccao (conta_id, empresa, contato, whatsapp, status, estagio)
                        values (39,%s,%s,%s,'novo','lead') returning id""", (nome, nome, fone)).fetchone()[0]
    conv = c.execute("""insert into conversas (conta_id, prospeccao_id, contato_ref, contato_nome)
                        values (39,%s,%s,%s) returning id""", (lead, fone, nome)).fetchone()[0]
    return lead, conv


def _diz(c, conv, texto, autor="lead"):
    c.execute("""insert into mensagens (conversa_id, canal, direcao, autor, texto)
                 values (%s,'whatsapp',%s,%s,%s)""", (conv, "in" if autor == "lead" else "out", autor, texto))
    c.commit()


def _pediu(c, nome, fone):
    lead, conv = _contato(c, nome, fone)
    c.execute("""insert into clinica_repasses (conta_id, conversa_id, prospeccao_id, motivo, motivos, criado_em)
                 values (39,%s,%s,'marcar','{marcar}',%s)""", (conv, lead, AGORA - timedelta(hours=5)))
    return lead, conv


def _preco(c, nome, fone, dias=2):
    lead, conv = _contato(c, nome, fone)
    mid = c.execute("""insert into mensagens (conversa_id, canal, direcao, autor, texto, criado_em)
                       values (%s,'whatsapp','out','humano','A consulta é R$ 500',%s) returning id""",
                    (conv, AGORA - timedelta(days=dias))).fetchone()[0]
    c.execute("""insert into voltar_a_chamar_toques (conta_id, prospeccao_id, conversa_id, preco_msg_id, toque,
                                                      estado, devido_em)
                 values (39,%s,%s,%s,2,'pendente',%s)""", (lead, conv, mid, AGORA + timedelta(hours=3)))
    return lead, conv


def _retorno(c, nome, fone):
    lead, conv = _contato(c, nome, fone)
    c.execute("update servicos_catalogo set volta_dias=30 where id=%s", (_tipo(c, "Consulta"),))
    c.execute("""insert into eventos_agenda (conta_id, titulo, inicio, fim, tipo, prospeccao_id, profissional_id,
                                             servico_id, situacao, status, paciente_nome, paciente_fone)
                 values (39,'x',%s,%s,'empresa',%s,%s,%s,'finalizado','ativo',%s,%s)""",
              (AGORA - timedelta(days=45), AGORA - timedelta(days=45, minutes=-30), lead, _manoel(c),
               _tipo(c, "Consulta"), nome, fone))
    return lead, conv


def _cancelada(c, h=8, dia=SEG, nome="Ivone Castro", fone="99 97777-0100"):
    eid, erro = ca.agendar(c, CLINICA, profissional_id=_manoel(c), servico_id=_tipo(c, "Consulta"),
                           inicio=ca.utc(dia, time(h)), nome=nome, fone=fone, agora=AGORA)
    assert erro is None
    assert ca.mudar_situacao(c, CLINICA, eid, "cancelou") is None
    c.commit()
    return eid


def _cenario(c):
    """Paula pediu horário, Rita recebeu o preço, Rui está com retorno vencido; Zé já
    marcou; "5599..." não tem nome; a vaga é a segunda 08:00 do Dr. Manoel."""
    gente = {"paula": _pediu(c, "Paula Pedido", "+5599911110001"),
             "rita": _preco(c, "Rita Preço", "+5599911110002"),
             "rui": _retorno(c, "Rui Retorno", "+5599911110003"),
             "ze": _preco(c, "Zé Já Marcou", "+5599911110004"),
             "anon": _preco(c, "5599911110005", "+5599911110005")}
    ca.agendar(c, CLINICA, profissional_id=_manoel(c), servico_id=_tipo(c, "Consulta"),
               inicio=ca.utc(SEG + timedelta(days=2), time(9)), lead_id=gente["ze"][0], agora=AGORA)
    _cancelada(c)
    assert cvg.detectar(c, CLINICA, AGORA) == 1
    c.commit()
    vid = c.execute("select id from clinica_vagas").fetchone()[0]
    return gente, vid


def _janela(c):
    from finance import funil_regua as fr
    j = fr.config(c, CLINICA)
    c.commit()
    return j


# ------------------------------------------------------------------ nascer e quem cabe

def test_cancelamento_vira_vaga_e_os_grupos_saem_em_ordem(pool):
    with pool.connection() as c:
        _gente, vid = _cenario(c)
        v = cvg.vaga(c, CLINICA, vid)
        assert (v["estado"], v["dia"], v["hora"], v["dur"]) == ("aguardando", "seg 28/09", "08:00", 30)
        assert v["cancelou"] == "Ivone Castro"
        chamar, fora = cvg.candidatos(c, CLINICA, v, AGORA)
    assert [(p["nome"], p["grupo"], p["tipo"]) for p in chamar] == [
        ("Paula Pedido", "pediu", "Consulta"), ("Rita Preço", "preco", "Consulta"),
        ("Rui Retorno", "retorno", "Retorno")]
    assert chamar[1]["porque"] == "recebeu o preço da consulta há 2 dias"
    assert chamar[2]["porque"].startswith("retorno vencido há ")
    motivos = {p["nome"]: p["motivo"] for p in fora}
    assert motivos["Zé Já Marcou"] == "já marcou consulta"
    assert motivos["5599911110005"] == "sem nome no WhatsApp"


def test_cancelamento_em_cima_da_hora_nao_vira_vaga(pool):
    with pool.connection() as c:
        _cancelada(c)
        assert cvg.detectar(c, CLINICA, ca.utc(SEG, time(7, 30))) == 0


def test_vaga_esperando_perto_demais_vai_pro_balcao(pool):
    with pool.connection() as c:
        _gente, vid = _cenario(c)
        cvg.fechar(c, CLINICA, ca.utc(SEG, time(7, 30)))
        assert cvg.vaga(c, CLINICA, vid)["estado"] == "balcao"


def test_marcaram_por_fora_a_vaga_fecha(pool):
    with pool.connection() as c:
        _gente, vid = _cenario(c)
        ca.agendar(c, CLINICA, profissional_id=_manoel(c), servico_id=_tipo(c, "Consulta"),
                   inicio=ca.utc(SEG, time(8)), nome="Balcão", fone="99 97777-0200", agora=AGORA)
        cvg.fechar(c, CLINICA, AGORA)
        assert cvg.vaga(c, CLINICA, vid)["estado"] == "ocupada"


# ------------------------------------------------------------------ o convite

def test_aprovar_manda_a_primeira_rodada_sem_procedimento(pool, zap):
    with pool.connection() as c:
        gente, vid = _cenario(c)
        assert cvg.aprovar(c, CLINICA, vid, 51, AGORA, _janela(c)) == "mandada"
        v = cvg.vaga(c, CLINICA, vid)
        assert (v["estado"], v["rodada"]) == ("oferta", 1)
        assert v["oferta_ate"] == AGORA + timedelta(minutes=20)
    convs = [cv for cv, _t in zap.saiu]
    assert convs == [gente["paula"][1], gente["rita"][1], gente["rui"][1]]
    texto = zap.saiu[0][1]
    assert texto.startswith("Oi, Paula! Abriu uma vaga com Dr. Manoel na segunda, 28/09, às 08:00, no Espaço Pelle")
    assert "Responda 1 para confirmar" in texto and "PARAR" in texto
    assert "consulta" not in texto.lower() and "retorno" not in texto.lower()
    with pool.connection() as c:                       # aprovar de novo não manda de novo
        assert cvg.aprovar(c, CLINICA, vid, 51, AGORA, _janela(c)) == "ja_tratada"
    assert len(zap.saiu) == 3


def test_quem_responde_1_primeiro_fica_com_a_vaga(pool, zap):
    with pool.connection() as c:
        gente, vid = _cenario(c)
        cvg.aprovar(c, CLINICA, vid, 51, AGORA, _janela(c))
        _diz(c, gente["rita"][1], "1")
        _diz(c, gente["paula"][1], "1")
        _diz(c, gente["rui"][1], "2")
        assert cvg.processar(c, CLINICA, AGORA) == 3
        v = cvg.vaga(c, CLINICA, vid)
        assert v["estado"] == "preenchida"
        ev = ca.evento(c, CLINICA, v["preenchida_evento_id"])
        assert (ev["paciente"], ev["marcado_por"], ev["hora"]) == ("Rita Preço", "vaga", "08:00")
        assert c.execute("select status from prospeccao where id=%s", (gente["rita"][0],)).fetchone()[0] == "qualificado"
        estados = dict(c.execute("select nome, estado from clinica_vaga_ofertas").fetchall())
    assert estados == {"Rita Preço": "ganhou", "Paula Pedido": "perdeu", "Rui Retorno": "recusou"}
    respostas = {cv: t for cv, t in zap.saiu[3:]}
    assert respostas[gente["rita"][1]].startswith("Prontinho!! ✅ Rita, sua consulta")
    assert "acabou de ser preenchida" in respostas[gente["paula"][1]]
    assert respostas[gente["rui"][1]].startswith("Sem problema")
    assert "⚡ Vaga preenchida" in zap.avisos
    with pool.connection() as c:                       # e não responde duas vezes
        assert cvg.processar(c, CLINICA, AGORA) == 0


def test_parar_nunca_mais_recebe_aviso_de_vaga(pool, zap):
    with pool.connection() as c:
        gente, vid = _cenario(c)
        cvg.aprovar(c, CLINICA, vid, 51, AGORA, _janela(c))
        _diz(c, gente["paula"][1], "PARAR")
        cvg.processar(c, CLINICA, AGORA)
        assert zap.saiu[-1][1].startswith("Pronto! Não te mando mais avisos de vaga")
        _cancelada(c, h=9, nome="Outra Pessoa", fone="99 97777-0300")
        cvg.detectar(c, CLINICA, AGORA + timedelta(days=1))
        vid2 = c.execute("select id from clinica_vagas where id<>%s", (vid,)).fetchone()[0]
        chamar, fora = cvg.candidatos(c, CLINICA, cvg.vaga(c, CLINICA, vid2), AGORA + timedelta(days=1))
    assert "Paula Pedido" not in [p["nome"] for p in chamar]
    assert {p["nome"]: p["motivo"] for p in fora}["Paula Pedido"] == "pediu PARAR aos avisos de vaga"


def test_sim_depois_de_outra_conversa_nao_e_resposta(pool, zap):
    with pool.connection() as c:
        gente, vid = _cenario(c)
        cvg.aprovar(c, CLINICA, vid, 51, AGORA, _janela(c))
        c.execute("update clinica_vaga_ofertas set enviada_em = now() - interval '10 minutes'")
        c.commit()
        _diz(c, gente["rita"][1], "Quer que eu te mande a localização?", autor="bot")
        _diz(c, gente["rita"][1], "sim")
        assert cvg.processar(c, CLINICA, AGORA) == 0
        _diz(c, gente["rita"][1], "1")                   # o número puro ainda vale
        assert cvg.processar(c, CLINICA, AGORA) == 1


def test_segunda_rodada_sai_sozinha_depois_de_20_min(pool, zap):
    with pool.connection() as c:
        gente, vid = _cenario(c)
        extra = [_preco(c, f"Pessoa {n}", f"+55999111200{n:02d}", dias=3) for n in range(1, 4)]
        cvg.aprovar(c, CLINICA, vid, 51, AGORA, _janela(c))
        assert len(zap.saiu) == 3
        r = cvg.passar_conta(c, CLINICA, AGORA + timedelta(minutes=10))
        assert r["enviadas"] == 0                       # ainda vale a 1ª
        r = cvg.passar_conta(c, CLINICA, AGORA + timedelta(minutes=21))
        assert r["enviadas"] == 3                       # as 3 que sobraram (até 5)
        assert cvg.vaga(c, CLINICA, vid)["rodada"] == 2
        # 1ª: Paula (pediu), Rita (preço há 2 dias), Pessoa 1 (preço há 3 dias);
        # 2ª: Pessoa 2, Pessoa 3 e Rui (retorno vem depois de quem recebeu o preço)
        assert zap.saiu[2][0] == extra[0][1]
        assert {cv for cv, _t in zap.saiu[3:]} == {extra[1][1], extra[2][1], gente["rui"][1]}
        cvg.passar_conta(c, CLINICA, AGORA + timedelta(minutes=42))
        assert cvg.vaga(c, CLINICA, vid)["estado"] == "livre"


def test_automatico_manda_sem_aprovar(pool, zap):
    with pool.connection() as c:
        _cenario(c)
        cvg.salvar_config(c, CLINICA, "auto", 20)
        c.commit()
        assert cvg.passar_conta(c, CLINICA, AGORA)["enviadas"] == 3


def test_desligado_nao_procura_vaga(pool, zap):
    with pool.connection() as c:
        cvg.salvar_config(c, CLINICA, "off", 20)
        _cancelada(c)
        assert cvg.passar_conta(c, CLINICA, AGORA) == {"novas": 0, "enviadas": 0, "respostas": 0}
        assert c.execute("select count(*) from clinica_vagas").fetchone()[0] == 0


def test_uma_mensagem_automatica_por_pessoa_por_dia(pool, zap):
    with pool.connection() as c:
        gente, vid = _cenario(c)
        c.execute("""update voltar_a_chamar_toques set estado='enviado', enviado_em=%s
                      where conversa_id=%s""", (AGORA - timedelta(hours=1), gente["rita"][1]))
        c.commit()
        chamar, fora = cvg.candidatos(c, CLINICA, cvg.vaga(c, CLINICA, vid), AGORA)
    assert "Rita Preço" not in [p["nome"] for p in chamar]
    assert {p["nome"]: p["motivo"] for p in fora}["Rita Preço"] == "já recebeu mensagem automática hoje"


def test_convite_ocupa_o_toque_do_dia_do_voltar_a_chamar(pool, zap):
    with pool.connection() as c:
        gente, vid = _cenario(c)
        cvg.aprovar(c, CLINICA, vid, 51, AGORA, _janela(c))
        assert c.execute("select estado from voltar_a_chamar_toques where conversa_id=%s",
                         (gente["rita"][1],)).fetchone()[0] == "pulado"


def test_agente_nao_oferece_horario_em_oferta(pool, zap):
    from finance import clinica_agente as cla
    with pool.connection() as c:
        gente, vid = _cenario(c)
        cvg.aprovar(c, CLINICA, vid, 51, AGORA, _janela(c))
        lead, _conv = _contato(c, "Nova Paciente", "+5599911119999")
        menu = cla.cardapio(c, CLINICA, lead, AGORA)
    assert cla.codigo(_manoel_pool(pool), _tipo_pool(pool, "Consulta"), ca.utc(SEG, time(8))) \
        not in [s["codigo"] for s in menu["slots"]]


def _manoel_pool(pool):
    with pool.connection() as c:
        return _manoel(c)


def _tipo_pool(pool, nome):
    with pool.connection() as c:
        return _tipo(c, nome)


def test_o_1_pelo_agente_marca_na_hora(pool, zap):
    """Com o agente ligado, o "1" é tratado na volta dele, sem esperar o poller."""
    from finance import clinica_agente as cla
    with pool.connection() as c:
        gente, vid = _cenario(c)
        cvg.aprovar(c, CLINICA, vid, 51, AGORA, _janela(c))
        lead, conv = gente["paula"]
        _diz(c, conv, "1")
        saiu = []
        msgs = c.execute("select direcao, autor, texto from mensagens where conversa_id=%s order by id desc",
                         (conv,)).fetchall()
        cla.atender(pool, c, CLINICA, conv, {"pode_responder": True}, (True, lead, "+5599911110001",
                    "Paula", "+5599911110001", None, None, None, None, "whatsapp"), msgs, historico="",
                    gemeo_nota="", instr="", faqs="", cat_txt="", canal="whatsapp", destino="+5599911110001",
                    enviar=saiu.append, agora=AGORA)
        assert cvg.vaga(c, CLINICA, vid)["estado"] == "preenchida"
    assert saiu[0].startswith("Prontinho!! ✅ Paula")


# ------------------------------------------------------------------ a tela

@pytest.fixture()
def cli(pool, monkeypatch):
    from web import painel_clinica_agenda as pa
    from web import painel_clinica_vagas as pv
    monkeypatch.setattr(pv, "get_pool", lambda: pool)
    monkeypatch.setattr(pa, "conta_logada", lambda request: (CLINICA,))
    monkeypatch.setattr(pa, "nicho_da_conta", lambda conta: "clinica")
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="teste")
    app.include_router(pv.router)

    @app.get("/_papel/{papel}")
    def _papel(request: Request, papel: str):
        request.session["papel"] = papel
        request.session["membro_id"] = 51
        return {}
    c = TestClient(app, follow_redirects=False)
    c.get("/_papel/vendedor")
    return c


def test_tela_mostra_quem_cabe_e_aprova(cli, pool, zap, monkeypatch):
    """A tela usa o relógio de verdade: a vaga é na próxima semana, em horário de grade."""
    from finance import funil_regua as fr
    monkeypatch.setattr(fr, "dentro_da_janela", lambda quando, cfg: True)
    agora = datetime.now(timezone.utc)
    d = ca.hoje_br(agora) + timedelta(days=7)
    while d.isoweekday() > 5:
        d += timedelta(days=1)
    with pool.connection() as c:
        _pediu(c, 'Ana <b>Souza</b>', "+5599911110011")
        c.execute("update clinica_repasses set criado_em=now()")
        eid, erro = ca.agendar(c, CLINICA, profissional_id=_manoel(c), servico_id=_tipo(c, "Consulta"),
                               inicio=ca.utc(d, time(10)), nome="Ivone", fone="99 97777-0100", agora=agora)
        ca.mudar_situacao(c, CLINICA, eid, "cancelou")
        c.commit()
    html = cli.get("/painel/clinica/vagas").text
    assert "Vagas liberadas" in html and "esperando aprovar" in html and "Ivone cancelou" in html
    assert "Ana &lt;b&gt;Souza&lt;/b&gt;" in html and "<b>Souza</b>" not in html
    assert "Pediu horário" in html and "Aprovar e mandar" in html
    vid = pool.connection().__enter__().execute("select id from clinica_vagas").fetchone()[0]
    r = cli.post(f"/painel/clinica/vagas/{vid}/aprovar")
    assert r.headers["location"] == "/painel/clinica/vagas?aviso=mandada"
    assert len(zap.saiu) == 1
    assert "em oferta" in cli.get("/painel/clinica/vagas").text


def test_so_gerencia_muda_o_modo(cli, pool):
    r = cli.post("/painel/clinica/vagas/config", data={"modo": "auto", "teto_dia": "10"})
    assert "aviso" not in r.headers["location"]
    with pool.connection() as c:
        assert cvg.config(c, CLINICA)["modo"] == "aprova"
    cli.get("/_papel/dono")
    cli.post("/painel/clinica/vagas/config", data={"modo": "auto", "teto_dia": "10"})
    with pool.connection() as c:
        assert cvg.config(c, CLINICA) == {"modo": "auto", "teto_dia": 10}
