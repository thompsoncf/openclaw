"""A agenda da clínica (finance/clinica_agenda.py) e as telas (web/painel_clinica_agenda.py).

Em cima da semente da Espaço Pelle (350): o Dr. Manoel atende seg a sex,
08:00–12:00 (2 encaixes) e 13:30–16:30, na sede. As migrações da agenda de
sempre (098, 099, 136) e a 351 rodam de verdade.
"""
import os
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from psycopg_pool import ConnectionPool
from starlette.middleware.sessions import SessionMiddleware

from finance import agente
from finance import clinica_agenda as ca
from finance import clinica_config as cc
from web import painel_clinica_agenda as pa

BASE = Path(__file__).resolve().parent.parent / "db" / "migracoes"
CLINICA = 39

_SQL = """
create table nichos (id bigserial primary key, slug text);
create table contas (id bigserial primary key, tipo text, nome text, nicho_id bigint);
create table membros (id bigserial primary key, conta_id bigint, nome text, email text,
  papel text, ativo boolean default true, whatsapp text, whatsapp_id text);
create table prospeccao (id bigserial primary key, conta_id bigint, vendedor_id bigint,
  empresa text not null, contato text, telefone text, whatsapp text, tipo text, origem text,
  temperatura text, status text default 'novo', estagio text default 'base',
  atualizado_em timestamptz default now(), criado_em timestamptz default now());
create table conversas (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  canal text default 'whatsapp', contato_ref text, contato_nome text,
  ultima_msg_em timestamptz default now());
create table mensagens (id bigserial primary key, conversa_id bigint, canal text, direcao text,
  autor text, texto text, membro_id bigint, provider_sid text, criado_em timestamptz default now());
create table funil_regua (conta_id bigint primary key, gatilhos_modo text default 'off',
  cobranca_modo text default 'off', janela_dias text, janela_abre time, janela_fecha time,
  sem_resposta_min int, bola_nossa_min int, bola_cliente_min int, escala_min int, teto_avisos_dia int);
create table funil_etapas (id bigserial primary key, conta_id bigint, chave text, fase text);
create table funil_movimentos (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  de text, para text, motivo text, membro_id bigint, criado_em timestamptz default now());
insert into nichos (id, slug) values (1,'clinica'),(2,'eventos');
insert into contas (id, tipo, nome, nicho_id) values (39,'pj','Clínica',1),(34,'pj','Festa',2);
insert into membros (id, conta_id, nome, papel) values (51,39,'Recepção','vendedor');
"""


@pytest.fixture()
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True,
                           kwargs={"autocommit": True, "prepare_threshold": None})
    dbname = "zaq_clinica_agenda_teste"
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
        for m in ("071_servicos_catalogo.sql", "148_servico_categoria_foto.sql", "153_servico_icone.sql",
                  "098_agenda.sql", "099_agenda_tipo.sql", "130_evento_desfecho.sql",
                  "136_visita_agenda.sql", "179_agenda_tipo_e_hora_sugerida.sql", "348_clinica_base.sql", "350_clinica_semente_espaco_pelle.sql"):
            c.execute((BASE / m).read_text(encoding="utf-8"))
        c.execute("alter table eventos_agenda add column if not exists marcado_por text")
        c.execute((BASE / "351_clinica_agenda.sql").read_text(encoding="utf-8"))
        c.commit()
    yield p
    p.close()


SEG = date(2026, 9, 28)
AGORA = datetime(2026, 9, 25, 12, tzinfo=timezone.utc)      # sexta, 09:00 em Brasília


def _manoel(c):
    return cc.listar_profissionais(c, CLINICA)[0]


def _tipo(c, nome):
    return next(t for t in cc.listar_tipos(c, CLINICA) if t["nome"] == nome)


def _marcar(c, h=8, m=0, dia=SEG, nome="Maria Clara", fone="(99) 98888-0001", **kw):
    return ca.agendar(c, CLINICA, profissional_id=_manoel(c)["id"], servico_id=_tipo(c, "Consulta")["id"],
                      inicio=ca.utc(dia, time(h, m)), nome=nome, fone=fone, agora=AGORA, **kw)


@pytest.fixture()
def envios(monkeypatch):
    feitos = []

    def _mandar(c, conta_id, canal, destino, texto, conversa_id=None):
        feitos.append({"destino": destino, "texto": texto, "conversa_id": conversa_id})
        return {"ok": True, "sid": f"s{len(feitos)}"}
    monkeypatch.setattr(agente, "_mandar", _mandar)
    return feitos


# ------------------------------------------------------------------ marcar

def test_marcar_ocupa_o_horario_e_cria_o_card(pool):
    with pool.connection() as c:
        eid, erro = _marcar(c, origem="Instagram")
        assert erro is None and eid
        livres = ca.livres(c, CLINICA, _manoel(c)["id"], _tipo(c, "Consulta")["id"], SEG, 1, AGORA)
        assert ca.utc(SEG, time(8)) not in [x["inicio"] for x in livres]
        ev = ca.evento(c, CLINICA, eid)
        assert ev["situacao"] == "agendado" and ev["fim_txt"] == "08:30" and ev["origem"] == "Instagram"
        lead = c.execute("select status, whatsapp, origem from prospeccao where id=%s", (ev["lead"],)).fetchone()
        assert lead == ("qualificado", "+5599988880001", "agenda_clinica")
        assert c.execute("select para, motivo from funil_movimentos").fetchall() == [("qualificado", "agenda")]
        # o mesmo celular de novo acha o mesmo card
        eid2, _ = _marcar(c, h=9, fone="99 98888-0001", nome="Maria")
        assert ca.evento(c, CLINICA, eid2)["lead"] == ev["lead"]


def test_nao_marca_em_cima_nem_fora_nem_no_passado(pool):
    with pool.connection() as c:
        assert _marcar(c)[1] is None
        assert "não está livre" in _marcar(c, nome="Outra", fone="99 97777-0002")[1]
        assert "não está livre" in _marcar(c, h=12, m=30, nome="Outra", fone="99 97777-0002")[1]
        assert "já passou" in _marcar(c, dia=date(2026, 9, 24), nome="Outra", fone="99 97777-0002")[1]
        # quem não faz o atendimento
        erro = ca.agendar(c, CLINICA, profissional_id=_manoel(c)["id"], servico_id=_tipo(c, "Vacina")["id"],
                          inicio=ca.utc(SEG, time(10)), nome="X", fone="99 97777-0003", agora=AGORA)[1]
        assert "não faz" in erro


def test_encaixe_ate_o_limite_do_dia(pool):
    with pool.connection() as c:
        assert _marcar(c)[1] is None
        assert _marcar(c, nome="A", fone="99 97777-1001", encaixe=True)[1] is None
        assert _marcar(c, nome="B", fone="99 97777-1002", encaixe=True)[1] is None
        assert "encaixes do dia" in _marcar(c, nome="C", fone="99 97777-1003", encaixe=True)[1]
        # encaixe fora do horário de atendimento, nunca
        assert "dentro do horário" in _marcar(c, h=18, nome="D", fone="99 97777-1004", encaixe=True)[1]


def test_status_seguem_o_fluxo_e_cancelado_libera(pool):
    with pool.connection() as c:
        eid, _ = _marcar(c)
        assert ca.mudar_situacao(c, CLINICA, eid, "finalizado") is not None     # pula etapas: não
        for s in ("confirmado", "presente", "atendimento", "finalizado"):
            assert ca.mudar_situacao(c, CLINICA, eid, s) is None
        eid2, _ = _marcar(c, h=9)
        assert ca.mudar_situacao(c, CLINICA, eid2, "cancelou") is None
        assert c.execute("select status from eventos_agenda where id=%s", (eid2,)).fetchone()[0] == "cancelado"
        assert ca.utc(SEG, time(9)) in [x["inicio"] for x in
                                        ca.livres(c, CLINICA, _manoel(c)["id"], _tipo(c, "Consulta")["id"], SEG, 1, AGORA)]


def test_remarcar_so_para_horario_livre(pool):
    with pool.connection() as c:
        eid, _ = _marcar(c)
        _marcar(c, h=10, nome="Outro", fone="99 97777-0009")
        assert "não está livre" in ca.remarcar(c, CLINICA, eid, ca.utc(SEG, time(10)), AGORA)
        assert ca.remarcar(c, CLINICA, eid, ca.utc(SEG, time(14)), AGORA) is None
        ev = ca.evento(c, CLINICA, eid)
        assert ev["hora"] == "14:00" and ev["situacao"] == "agendado"
        # remarcar pra ele mesmo (sem mexer) não trava em si
        assert ca.remarcar(c, CLINICA, eid, ca.utc(SEG, time(14)), AGORA) is None


def test_dia_e_semana(pool):
    with pool.connection() as c:
        _marcar(c)
        eid, _ = _marcar(c, h=9)
        ca.mudar_situacao(c, CLINICA, eid, "faltou")
        d = ca.dia(c, CLINICA, SEG, AGORA)
        assert [col["prof"]["nome"] for col in d["colunas"]] == ["Dr. Manoel"]
        assert d["linhas"][0] == time(8) and time(12) not in d["linhas"] and time(13, 30) in d["linhas"]
        assert d["faltas"] == 1 and d["a_confirmar"] == 1 and d["confirmados"] == 0
        tipos = [cel["tipo"] for cel in d["colunas"][0]["celulas"][:3]]
        assert tipos == ["ev", "livre", "ev"]      # 08:00 marcado, 08:30 livre, 09:00 (faltou) aparece
        s = ca.semana(c, CLINICA, _manoel(c)["id"], SEG, AGORA)
        assert [col["rotulo"][:3] for col in s["colunas"]] == ["seg", "ter", "qua", "qui", "sex", "sáb"]
        assert s["colunas"][5]["ocupacao"] is None and s["colunas"][0]["ocupacao"] > 0


# ------------------------------------------------------------------ mensagens

def test_mensagem_nunca_diz_o_procedimento(pool):
    with pool.connection() as c:
        juliana = cc.listar_profissionais(c, CLINICA)[1]
        cc.salvar_grade(c, CLINICA, profissional_id=juliana["id"], local_id=cc.listar_locais(c, CLINICA)[0]["id"],
                        dias=[1], inicio="08:00", fim="12:00")
        eid, erro = ca.agendar(c, CLINICA, profissional_id=juliana["id"],
                               servico_id=_tipo(c, "Procedimento estético")["id"],
                               inicio=ca.utc(SEG, time(8)), nome="Bia Souza", fone="99 97777-0005", agora=AGORA)
        assert erro is None
        ev = ca.evento(c, CLINICA, eid)
        for txt in (ca.texto_marcado(c, CLINICA, ev), ca.texto_vespera(c, CLINICA, ev)):
            assert "estético" not in txt.lower() and "procedimento" not in txt.lower()
            assert "atendimento" in txt and "Juliana" in txt and "Rua Óscar Galvão, 38" in txt
        # concordância: "seu atendimento… marcado", "sua consulta… marcada"
        assert ca.texto_marcado(c, CLINICA, ev).startswith("Prontinho!! ✅ Bia, seu atendimento com Juliana está marcado")
        ev_consulta = dict(ev, categoria="consulta", tipo="Consulta")
        assert "sua consulta com Juliana está marcada" in ca.texto_marcado(c, CLINICA, ev_consulta)


def _conversa_de(c, eid, fone):
    lead = ca.evento(c, CLINICA, eid)["lead"]
    return c.execute("insert into conversas (conta_id, prospeccao_id, contato_ref) values (39,%s,%s) returning id",
                     (lead, fone)).fetchone()[0]


def _resposta(c, conv, texto, quando):
    c.execute("insert into mensagens (conversa_id, direcao, texto, criado_em) values (%s,'in',%s,%s)",
              (conv, texto, quando))


def test_vespera_de_segunda_sai_na_sexta_e_as_respostas(pool, envios):
    """Segunda 28/09. Domingo está fora da janela: o lembrete sai na sexta, e diz o dia."""
    with pool.connection() as c:
        eid1, _ = _marcar(c, nome="Ana", fone="99 97777-0011")
        eid2, _ = _marcar(c, h=9, nome="Rui", fone="99 97777-0012")
        eid3, _ = _marcar(c, h=10, nome="Lia", fone="99 97777-0013")
        ca.salvar_config(c, CLINICA, "ligado", 10)
        c.commit()
    sexta_9h = datetime(2026, 9, 25, 12, tzinfo=timezone.utc)
    sexta_11h = datetime(2026, 9, 25, 14, tzinfo=timezone.utc)
    assert ca.rodar(pool, agora=sexta_9h)["enviadas"] == 0             # antes das 10h
    assert ca.rodar(pool, agora=sexta_11h)["enviadas"] == 3
    assert ca.rodar(pool, agora=sexta_11h)["enviadas"] == 0            # nunca duas vezes
    assert all("Na segunda, 28/09, você tem consulta" in e["texto"] and "Responda 1" in e["texto"]
               for e in envios)
    with pool.connection() as c:
        conv1 = _conversa_de(c, eid1, "5599977770011")
        conv2 = _conversa_de(c, eid2, "5599977770012")
        conv3 = _conversa_de(c, eid3, "5599977770013")
        depois = sexta_11h + timedelta(minutes=5)
        _resposta(c, conv1, "oi, tudo bem?", depois)                   # conversa antes da resposta
        _resposta(c, conv1, "1", depois + timedelta(minutes=1))
        _resposta(c, conv2, "2, não vou poder", depois)
        _resposta(c, conv3, "15h tem vaga?", depois)                   # "15h" não é "1"
        c.commit()
    assert ca.rodar(pool, agora=sexta_11h + timedelta(minutes=10))["respostas"] == 2
    with pool.connection() as c:
        assert ca.evento(c, CLINICA, eid1)["situacao"] == "confirmado"
        ev2 = ca.evento(c, CLINICA, eid2)
        assert ev2["situacao"] == "agendado" and ev2["pede_remarcar_em"]
        assert ca.evento(c, CLINICA, eid3)["situacao"] == "agendado"
        # quem pediu pra remarcar aparece em qualquer dia que a recepção abrir
        assert [e["id"] for e in ca.dia(c, CLINICA, date(2026, 9, 25), sexta_11h)["remarcar"]] == [eid2]


def test_lembrete_manual_com_a_confirmacao_desligada_tambem_le_a_resposta(pool, envios):
    with pool.connection() as c:
        eid, _ = _marcar(c, nome="Ana", fone="99 97777-0021")
        conv = _conversa_de(c, eid, "5599977770021")
        c.execute("update eventos_agenda set confirmacao_enviada_em=%s where id=%s", (AGORA, eid))
        _resposta(c, conv, "Sim, confirmo", AGORA + timedelta(minutes=3))
        c.commit()
    assert ca.rodar(pool, agora=AGORA + timedelta(minutes=5))["respostas"] == 1
    with pool.connection() as c:
        assert ca.evento(c, CLINICA, eid)["situacao"] == "confirmado"


def test_envio_que_falha_tenta_de_novo(pool, monkeypatch):
    monkeypatch.setattr(agente, "_mandar", lambda *a, **k: {"ok": False, "erro": "desconectado"})
    with pool.connection() as c:
        eid, _ = _marcar(c, nome="Ana", fone="99 97777-0031")
        ca.salvar_config(c, CLINICA, "ligado", 10)
        c.commit()
    ca.rodar(pool, agora=datetime(2026, 9, 25, 14, tzinfo=timezone.utc))
    with pool.connection() as c:
        assert ca.evento(c, CLINICA, eid)["confirmacao_enviada_em"] is None


def test_texto_diz_o_dia_certo_e_so_promete_lembrete_se_ligado(pool):
    with pool.connection() as c:
        eid, _ = _marcar(c, nome="Ana", fone="99 97777-0041")
        ev = ca.evento(c, CLINICA, eid)
        assert ca.texto_vespera(c, CLINICA, ev, datetime(2026, 9, 27, 14, tzinfo=timezone.utc)).startswith(
            "Oi, Ana! Amanhã você tem consulta")
        assert "Na segunda, 28/09," in ca.texto_vespera(c, CLINICA, ev, AGORA)
        assert "Na véspera" not in ca.texto_marcado(c, CLINICA, ev)
        assert "Na véspera" in ca.texto_marcado(c, CLINICA, ev, True)


def test_confirmacao_desligada_ou_outro_nicho_nao_manda(pool, envios):
    with pool.connection() as c:
        _marcar(c)
        c.execute("insert into clinica_agenda_config (conta_id, confirmacao_modo) values (34,'ligado')")
        c.commit()
    assert ca.rodar(pool, agora=datetime(2026, 9, 25, 14, tzinfo=timezone.utc))["contas"] == 0
    assert envios == []


# ------------------------------------------------------------------ as telas

@pytest.fixture()
def cli(pool, monkeypatch):
    estado = {"nicho": "clinica"}
    monkeypatch.setattr(pa, "get_pool", lambda: pool)
    monkeypatch.setattr(pa, "conta_logada", lambda request: (CLINICA,))
    monkeypatch.setattr(pa, "nicho_da_conta", lambda conta: estado["nicho"])
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="teste")
    app.include_router(pa.router)

    @app.get("/_papel/{papel}")
    def _papel(request: Request, papel: str):
        request.session["papel"] = papel
        request.session["membro_id"] = 51
        return {}

    c = TestClient(app, follow_redirects=False)
    c.get("/_papel/vendedor")
    c.estado = estado
    return c


def _proxima_segunda() -> date:
    """Uma segunda-feira futura (a da semana que vem), seja que dia for hoje."""
    d = ca.hoje_br() + timedelta(days=1)
    while d.isoweekday() != 1:
        d += timedelta(days=1)
    return d + timedelta(days=7)


def test_telas_abrem_e_a_recepcao_marca(cli, pool, envios):
    seg = _proxima_segunda()
    for url in ("/painel/clinica/agenda", f"/painel/clinica/agenda?data={seg}",
                f"/painel/clinica/agenda?vista=semana&data={seg}", "/painel/clinica/agenda/novo",
                f"/painel/clinica/agenda/novo?data={seg}&hora=08:00&busca=ma"):
        r = cli.get(url)
        assert r.status_code == 200, (url, r.text[:300])
    with pool.connection() as c:
        manoel, consulta = _manoel(c)["id"], _tipo(c, "Consulta")["id"]
    r = cli.post("/painel/clinica/agenda/novo", data={
        "prof": manoel, "tipo": consulta, "inicio": ca.utc(seg, time(8)).isoformat(),
        "nome": "<b>Paula</b>", "fone": "99 97777-4444", "origem": "Google", "acao": "confirmar"})
    assert r.status_code == 303 and "aviso=marcado_msg" in r.headers["location"],         cli.get(r.headers["location"]).text.split('class="erro"')[-1][:200]
    assert envios and envios[0]["texto"].startswith("Prontinho!!")
    html = cli.get(f"/painel/clinica/agenda?data={seg}").text
    assert "&lt;b&gt;Paula" in html and "<b>Paula" not in html
    with pool.connection() as c:
        eid = c.execute("select id from eventos_agenda").fetchone()[0]
    assert cli.get(f"/painel/clinica/agenda/evento/{eid}").status_code == 200
    r = cli.post(f"/painel/clinica/agenda/evento/{eid}/situacao", data={"nova": "confirmado"})
    assert "aviso=situacao" in r.headers["location"]


def test_so_clinica_e_config_so_gerencia(cli):
    r = cli.post("/painel/clinica/agenda/config", data={"modo": "ligado"})
    assert r.headers["location"] == "/painel/clinica/agenda"
    cli.estado["nicho"] = "eventos"
    assert cli.get("/painel/clinica/agenda").headers["location"] == "/painel"
    cli.get("/_papel/financeiro")
    cli.estado["nicho"] = "clinica"
    assert cli.get("/painel/clinica/agenda").headers["location"] == "/painel"


# ------------------------------------------------------------------ 2ª leva: revisão do #850

def test_cancelar_pela_agenda_de_sempre_libera_e_nao_manda_vespera(pool, envios):
    from finance import agenda as ag
    with pool.connection() as c:
        eid, _ = _marcar(c)
        c.commit()
    assert ag.cancelar_evento(pool, CLINICA, eid)
    with pool.connection() as c:
        assert ca.evento(c, CLINICA, eid)["situacao"] == "cancelou"
        assert ca.utc(SEG, time(8)) in [x["inicio"] for x in
                                        ca.livres(c, CLINICA, _manoel(c)["id"], _tipo(c, "Consulta")["id"], SEG, 1, AGORA)]
        ca.salvar_config(c, CLINICA, "ligado", 10)
        c.commit()
    ca.rodar(pool, agora=datetime(2026, 9, 25, 14, tzinfo=timezone.utc))
    assert envios == []


def test_remarcar_pela_agenda_de_sempre_e_recusado(pool):
    from finance import agenda as ag
    with pool.connection() as c:
        eid, _ = _marcar(c)
        c.commit()
    assert ag.remarcar_evento(pool, CLINICA, eid, ca.utc(SEG, time(9))) is False
    with pool.connection() as c:
        assert ca.evento(c, CLINICA, eid)["hora"] == "08:00"


def test_consulta_nao_e_visita_e_titulo_nao_diz_procedimento(pool):
    from finance import visita
    with pool.connection() as c:
        juliana = cc.listar_profissionais(c, CLINICA)[1]
        cc.salvar_grade(c, CLINICA, profissional_id=juliana["id"], local_id=cc.listar_locais(c, CLINICA)[0]["id"],
                        dias=[1], inicio="08:00", fim="12:00")
        eid, _ = ca.agendar(c, CLINICA, profissional_id=juliana["id"],
                            servico_id=_tipo(c, "Procedimento estético")["id"], inicio=ca.utc(SEG, time(8)),
                            nome="Bia", fone="99 97777-0051", observacao="alergia a lidocaína", agora=AGORA)
        titulo, descricao, obs = c.execute(
            "select titulo, descricao, observacao_interna from eventos_agenda where id=%s", (eid,)).fetchone()
        assert titulo == "Bia · atendimento" and descricao is None and obs == "alergia a lidocaína"
        n = c.execute("select count(*) from eventos_agenda e where e.conta_id=39 and " + visita.sql_conta("e")).fetchone()[0]
        assert n == 0


def test_reabrir_falta_so_se_o_horario_continua_livre(pool):
    with pool.connection() as c:
        eid, _ = _marcar(c)
        assert ca.mudar_situacao(c, CLINICA, eid, "faltou") is None
        _marcar(c, nome="Outra", fone="99 97777-0061")               # alguém pegou as 08:00
        assert "ocupado" in ca.mudar_situacao(c, CLINICA, eid, "agendado")


def test_mesmo_celular_nome_digitado_e_em_bacabal(pool):
    with pool.connection() as c:
        eid1, _ = _marcar(c, nome="Maria", fone="99 98888-0071")
        eid2, _ = _marcar(c, h=9, nome="Joãozinho", fone="99 98888-0071")    # a mãe marca pro filho
        e1, e2 = ca.evento(c, CLINICA, eid1), ca.evento(c, CLINICA, eid2)
        assert e1["lead"] == e2["lead"] and e2["paciente"] == "Joãozinho"
        bacabal = next(x for x in cc.listar_locais(c, CLINICA) if x["nome"] == "Bacabal")
        assert ", em Bacabal" in ca._onde(c, CLINICA, dict(e1, local_id=bacabal["id"]))
        assert ", no Espaço Pelle" in ca._onde(c, CLINICA, e1)


def test_buscar_paciente_guarda_o_horario_e_encaixe_so_quando_escolhido(cli, pool, envios):
    seg = _proxima_segunda()
    with pool.connection() as c:
        manoel, consulta = _manoel(c)["id"], _tipo(c, "Consulta")["id"]
    nove = ca.utc(seg, time(9)).isoformat()
    r = cli.post("/painel/clinica/agenda/novo", data={"prof": manoel, "tipo": consulta, "inicio": nove,
                                                     "busca": "Maria", "nome": "Maria X", "acao": "buscar"})
    assert "Maria" not in r.headers["location"]                     # nada pessoal na URL
    html = cli.get(r.headers["location"]).text
    assert f'value="{nove}" checked' in html and 'value="Maria X"' in html
    # 08:00 ocupado + pedido como encaixe, mas a recepção escolheu 09:00 (livre): não vira encaixe
    cli.post("/painel/clinica/agenda/novo", data={"prof": manoel, "tipo": consulta,
                                                 "inicio": ca.utc(seg, time(8)).isoformat(), "nome": "A",
                                                 "fone": "99 97777-0081", "acao": "agendar"})
    cli.post("/painel/clinica/agenda/novo", data={"prof": manoel, "tipo": consulta, "inicio": nove,
                                                 "nome": "B", "fone": "99 97777-0082", "acao": "agendar"})
    with pool.connection() as c:
        assert c.execute("select count(*) from eventos_agenda where encaixe").fetchone()[0] == 0
    cli.post("/painel/clinica/agenda/novo", data={"prof": manoel, "tipo": consulta,
                                                 "inicio": "enc|" + ca.utc(seg, time(8)).isoformat(),
                                                 "nome": "C", "fone": "99 97777-0083", "acao": "agendar"})
    with pool.connection() as c:
        assert c.execute("select count(*) from eventos_agenda where encaixe").fetchone()[0] == 1


def test_data_absurda_nao_quebra(cli):
    assert cli.get("/painel/clinica/agenda?data=9999-12-31").status_code == 200
    r = cli.post("/painel/clinica/agenda/novo", data={"prof": "1", "tipo": "1",
                                                     "inicio": "9999-12-31T23:59:00+00:00", "acao": "agendar"})
    assert r.status_code == 303

