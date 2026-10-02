"""A agenda da clínica (finance/clinica_agenda.py) e as telas (web/painel_clinica_agenda.py).

Em cima da semente da Espaço Pelle (350): o Dr. Manoel atende seg a sex,
08:00–12:00 (2 encaixes) e 13:30–16:30, na sede. As migrações da agenda de
sempre (098, 099, 130, 136, 179) e a 360 rodam de verdade.
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
  valor_estimado_centavos bigint not null default 0,
  atualizado_em timestamptz default now(), criado_em timestamptz default now());
create table prospeccao_atividades (id bigserial primary key, prospeccao_id bigint, membro_id bigint,
  tipo text, resultado text, descricao text default '', criado_em timestamptz default now());
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
-- a clínica com o funil de 01/10/2026 aplicado (Consulta, Em tratamento, Retorno)
insert into funil_etapas (conta_id, chave, fase) values
  (39,'novo','venda'),(39,'contatado','venda'),(39,'follow_up','venda'),(39,'qualificado','venda'),
  (39,'consulta','venda'),(39,'proposta','venda'),(39,'ganho','fechamento'),(39,'tratamento','pos'),
  (39,'retorno','pos'),(39,'perdido','fechamento');
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
        c.execute((BASE / "360_clinica_agenda.sql").read_text(encoding="utf-8"))
        c.execute((BASE / "471_clinica_tratamento_proposto.sql").read_text(encoding="utf-8"))
        c.execute((BASE / "477_clinica_resultados.sql").read_text(encoding="utf-8"))
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
        assert _status(c, eid)[0] == "follow_up"      # o card anda igual, venha de onde vier
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


# ------------------------------------------------------------------ fase 3a: o card anda com a agenda

def _status(c, eid):
    return c.execute("""select p.status, p.valor_estimado_centavos from prospeccao p
                         join eventos_agenda e on e.prospeccao_id = p.id where e.id=%s""", (eid,)).fetchone()


def test_faltou_volta_pro_follow_up_e_reabrir_devolve(pool):
    with pool.connection() as c:
        eid, _ = _marcar(c)
        assert _status(c, eid)[0] == "qualificado"
        assert ca.mudar_situacao(c, CLINICA, eid, "faltou") is None
        assert _status(c, eid)[0] == "follow_up"
        nota = c.execute("select descricao from prospeccao_atividades order by id desc limit 1").fetchone()[0]
        assert nota.startswith("Faltou à consulta de 28/09 08:00")
        assert ca.mudar_situacao(c, CLINICA, eid, "agendado") is None
        assert _status(c, eid)[0] == "qualificado"
        assert [r[0] for r in c.execute("select motivo from funil_movimentos").fetchall()] == ["agenda"] * 3


def test_cancelou_tambem_volta_pro_follow_up(pool):
    with pool.connection() as c:
        eid, _ = _marcar(c)
        ca.mudar_situacao(c, CLINICA, eid, "cancelou")
        assert _status(c, eid)[0] == "follow_up"


def test_remarcar_a_falta_devolve_pra_consulta_agendada(pool):
    """A nota diz "faltou, remarcar": remarcar é o caminho, não só o Reabrir."""
    with pool.connection() as c:
        eid, _ = _marcar(c)
        ca.mudar_situacao(c, CLINICA, eid, "faltou")
        assert _status(c, eid)[0] == "follow_up"
        assert ca.remarcar(c, CLINICA, eid, ca.utc(SEG, time(14)), AGORA, membro_id=51) is None
        assert _status(c, eid)[0] == "qualificado"
        # faltou de novo: volta pro Follow-up, com nota nova
        ca.mudar_situacao(c, CLINICA, eid, "faltou")
        assert _status(c, eid)[0] == "follow_up"
        assert c.execute("select count(*) from prospeccao_atividades where descricao like 'Faltou%'"
                         ).fetchone()[0] == 2


def test_falta_de_um_nao_tira_o_card_de_quem_tem_outra_marcada(pool):
    """A mãe marca pra ela e pro filho do mesmo celular: o filho falta, mas o card
    continua em Consulta agendada, porque a dela ainda está marcada."""
    with pool.connection() as c:
        filho, _ = _marcar(c, h=8, nome="Pedro")
        mae, _ = _marcar(c, h=9, nome="Maria Clara")
        ca.mudar_situacao(c, CLINICA, filho, "faltou")
        assert _status(c, filho)[0] == "qualificado"
        ca.mudar_situacao(c, CLINICA, mae, "faltou")          # agora não sobra consulta nenhuma
        assert _status(c, mae)[0] == "follow_up"


def _ate_atendimento(c, eid):
    for s in ("confirmado", "presente", "atendimento"):
        assert ca.mudar_situacao(c, CLINICA, eid, s) is None


def test_presente_leva_o_card_pra_consulta(pool):
    """O paciente veio: é a coluna que faltava entre Agendado e o plano."""
    with pool.connection() as c:
        eid, _ = _marcar(c)
        assert ca.mudar_situacao(c, CLINICA, eid, "confirmado") is None
        assert _status(c, eid)[0] == "qualificado"
        assert ca.mudar_situacao(c, CLINICA, eid, "presente") is None
        assert _status(c, eid)[0] == "consulta"
        assert c.execute("select de, para, motivo from funil_movimentos order by id desc limit 1"
                         ).fetchone() == ("qualificado", "consulta", "agenda")


def _marcar_tipo(c, tipo, h=8, dia=SEG, nome="Maria Clara", fone="(99) 98888-0001"):
    eid, erro = ca.agendar(c, CLINICA, profissional_id=_manoel(c)["id"], servico_id=_tipo(c, tipo)["id"],
                           inicio=ca.utc(dia, time(h)), nome=nome, fone=fone, agora=AGORA)
    assert erro is None, erro
    return eid


def test_presente_so_abre_consulta_em_horario_de_consulta(pool):
    """"Veio ou faltou" depende do tipo do horário: o ato único (exame, teste alérgico,
    procedimento avulso) não passa por Consulta; resolve-se no Finalizar."""
    with pool.connection() as c:
        teste = _marcar_tipo(c, "Testes alérgicos")
        assert _status(c, teste)[0] == "qualificado"
        _ate_atendimento(c, teste)
        assert _status(c, teste)[0] == "qualificado"            # Presente não mexe
        assert ca.mudar_situacao(c, CLINICA, teste, "finalizado", tratamento="nao") is None
        assert _status(c, teste)[0] == "ganho"                  # o ato único conclui no Finalizar
        proc = _marcar_tipo(c, "Procedimento clínico", h=9, nome="Outra", fone="99 97777-0097")
        _ate_atendimento(c, proc)
        assert _status(c, proc)[0] == "qualificado"            # procedimento avulso também não
        consulta = _marcar_tipo(c, "Consulta", h=10, nome="Mais uma", fone="99 97777-0098")
        _ate_atendimento(c, consulta)
        assert _status(c, consulta)[0] == "consulta"


def test_ato_unico_finalizado_sem_a_pergunta_nao_fica_em_agendado(pool):
    """O "um toque" (e a sessão de pacote, que não pergunta do tratamento): o horário
    que não abre Consulta resolve o card no Finalizar, mesmo sem a resposta."""
    with pool.connection() as c:
        teste = _marcar_tipo(c, "Testes alérgicos")
        _ate_atendimento(c, teste)
        assert ca.mudar_situacao(c, CLINICA, teste, "finalizado") is None
        assert _status(c, teste)[0] == "ganho"


def test_consulta_nova_reabre_o_card_concluido_e_o_retorno_nao(pool):
    """Marcar consulta nova reabre o card em Agendado (o paciente voltou com outra
    queixa). O retorno e a sessão de quem concluiu não reabrem nada."""
    with pool.connection() as c:
        eid, _ = _marcar(c)
        lead = ca.evento(c, CLINICA, eid)["lead"]
        c.execute("update prospeccao set status='ganho', valor_estimado_centavos=300000 where id=%s", (lead,))
        _marcar_tipo(c, "Retorno", h=10)
        assert _status(c, eid) == ("ganho", 300000)
        _marcar_tipo(c, "Consulta", dia=SEG + timedelta(days=7))
        assert _status(c, eid) == ("qualificado", 0)            # venda nova: sem o valor da anterior
        assert c.execute("select de, para, motivo from funil_movimentos order by id desc limit 1"
                         ).fetchone() == ("ganho", "qualificado", "agenda")
        assert c.execute("select descricao from prospeccao_atividades order by id desc limit 1"
                         ).fetchone()[0] == "Consulta nova marcada: o card voltou para Agendado."


def test_funil_de_antes_a_consulta_nova_nao_reabre(pool):
    with pool.connection() as c:
        c.execute("delete from funil_etapas where conta_id=%s and chave in ('consulta','tratamento','retorno')",
                  (CLINICA,))
        eid, _ = _marcar(c)
        c.execute("update prospeccao set status='ganho' where id=%s", (ca.evento(c, CLINICA, eid)["lead"],))
        _marcar_tipo(c, "Consulta", dia=SEG + timedelta(days=7))
        assert _status(c, eid)[0] == "ganho"


def test_propos_tratamento_segura_o_card_em_consulta_com_o_plano_a_montar(pool):
    """O card só vai pra Plano enviado quando o plano é ENVIADO (clinica_planos)."""
    with pool.connection() as c:
        eid, _ = _marcar(c)
        _ate_atendimento(c, eid)
        assert ca.mudar_situacao(c, CLINICA, eid, "finalizado", tratamento="sim", valor_centavos=320000) is None
        assert _status(c, eid) == ("consulta", 320000)
        nota = c.execute("select descricao from prospeccao_atividades order by id desc limit 1").fetchone()[0]
        assert nota == "Consulta finalizada: o médico propôs tratamento (R$ 3.200,00): plano a montar."


def test_sem_proposta_com_retorno_pedido_vai_pro_retorno(pool):
    """A receita com volta em 30 dias: o retorno existe sem tratamento nenhum."""
    with pool.connection() as c:
        eid, _ = _marcar(c)
        _ate_atendimento(c, eid)
        assert ca.mudar_situacao(c, CLINICA, eid, "finalizado", tratamento="nao", retorno_dias=30) is None
        assert _status(c, eid) == ("retorno", 50000)


def test_quem_esta_em_tratamento_ou_em_retorno_nao_sai_da_coluna_pela_agenda(pool):
    """"Veio ou faltou" depende de onde o card está: a sessão de pacote e o horário de
    retorno não jogam um paciente que já fechou de volta na venda."""
    with pool.connection() as c:
        sessao, _ = _marcar(c)
        c.execute("update prospeccao set status='tratamento' where id=%s", (ca.evento(c, CLINICA, sessao)["lead"],))
        _ate_atendimento(c, sessao)
        assert _status(c, sessao)[0] == "tratamento"
        assert ca.mudar_situacao(c, CLINICA, sessao, "finalizado") is None
        assert _status(c, sessao)[0] == "tratamento"            # sem pacote aqui: quem tira é o saldo
        volta, _ = _marcar(c, h=9, nome="Outro", fone="99 97777-0093")
        c.execute("update prospeccao set status='retorno' where id=%s", (ca.evento(c, CLINICA, volta)["lead"],))
        assert ca.mudar_situacao(c, CLINICA, volta, "faltou") is None
        assert _status(c, volta)[0] == "retorno"


def test_retorno_feito_conclui_ou_volta_pra_consulta(pool):
    with pool.connection() as c:
        alta, _ = _marcar(c)
        c.execute("update prospeccao set status='retorno' where id=%s", (ca.evento(c, CLINICA, alta)["lead"],))
        _ate_atendimento(c, alta)
        assert _status(c, alta)[0] == "retorno"                 # Presente não tira do Retorno
        assert ca.mudar_situacao(c, CLINICA, alta, "finalizado", tratamento="nao") is None
        assert _status(c, alta)[0] == "ganho"
        plano, _ = _marcar(c, h=9, nome="Outra", fone="99 97777-0094")
        c.execute("update prospeccao set status='retorno' where id=%s", (ca.evento(c, CLINICA, plano)["lead"],))
        _ate_atendimento(c, plano)
        assert ca.mudar_situacao(c, CLINICA, plano, "finalizado", tratamento="sim") is None
        assert _status(c, plano)[0] == "consulta"


def test_conta_com_o_funil_de_antes_segue_a_regra_de_antes(pool):
    """A conta que ainda não aceitou o modelo novo (sem as colunas Consulta e Retorno)
    não muda de comportamento: Presente não mexe, propôs → plano, sem proposta → fechado."""
    with pool.connection() as c:
        c.execute("delete from funil_etapas where conta_id=%s", (CLINICA,))
        for ch in ("novo", "contatado", "follow_up", "qualificado", "proposta", "ganho", "perdido"):
            c.execute("insert into funil_etapas (conta_id, chave) values (%s,%s)", (CLINICA, ch))
        eid, _ = _marcar(c)
        _ate_atendimento(c, eid)
        assert _status(c, eid)[0] == "qualificado"
        assert ca.mudar_situacao(c, CLINICA, eid, "finalizado", tratamento="sim", valor_centavos=320000) is None
        assert _status(c, eid) == ("proposta", 320000)
        eid2, _ = _marcar(c, h=9, nome="Outra", fone="99 97777-0095")
        _ate_atendimento(c, eid2)
        assert ca.mudar_situacao(c, CLINICA, eid2, "finalizado", tratamento="nao", retorno_dias=30) is None
        assert _status(c, eid2) == ("ganho", 50000)


def test_funil_ainda_nao_aberto_segue_a_regra_de_antes(pool):
    """Sem etapa gravada, as colunas novas não valem: é em funil_etapas que a régua, a
    varredura dos pacotes e o "venda fechada" as enxergam (revisão de 01/10/2026)."""
    with pool.connection() as c:
        c.execute("delete from funil_etapas where conta_id=%s", (CLINICA,))
        eid, _ = _marcar(c)
        _ate_atendimento(c, eid)
        assert _status(c, eid)[0] == "qualificado"
        assert ca.mudar_situacao(c, CLINICA, eid, "finalizado", tratamento="sim") is None
        assert _status(c, eid)[0] == "proposta"


def test_coluna_retorno_criada_a_mao_em_fase_de_venda_nao_e_a_do_modelo(pool):
    """A chave da etapa criada à mão é o nome dela. "Retorno" em fase de venda tem
    outro sentido: o paciente não vai pra lá, e a venda não sai dos números."""
    with pool.connection() as c:
        c.execute("update funil_etapas set fase='venda' where conta_id=%s and chave='retorno'", (CLINICA,))
        eid, _ = _marcar(c)
        _ate_atendimento(c, eid)
        assert ca.mudar_situacao(c, CLINICA, eid, "finalizado", tratamento="nao", retorno_dias=30) is None
        assert _status(c, eid)[0] == "ganho"


def test_nao_de_outro_atendimento_nao_fecha_o_card_que_espera_o_plano(pool):
    """A mãe e o filho no mesmo celular são o mesmo card. O médico propôs tratamento à
    mãe; o "não" do filho não leva o card pra Concluído com o valor PROPOSTO, como se
    fosse venda. Nem o cancelamento de outro horário o tira de Consulta."""
    with pool.connection() as c:
        mae, _ = _marcar(c)
        filho, _ = _marcar(c, h=9, nome="Filho")
        depois, _ = _marcar(c, h=10, nome="Filho")
        assert ca.evento(c, CLINICA, filho)["lead"] == ca.evento(c, CLINICA, mae)["lead"]
        _ate_atendimento(c, mae)
        c.commit()                      # cada clique é uma transação: o relógio anda entre eles
        assert ca.mudar_situacao(c, CLINICA, mae, "finalizado", tratamento="sim", valor_centavos=320000) is None
        c.commit()
        _ate_atendimento(c, filho)
        c.commit()
        assert ca.mudar_situacao(c, CLINICA, filho, "finalizado", tratamento="nao") is None
        c.commit()
        assert _status(c, mae) == ("consulta", 320000)
        # a tela tinha prometido Retorno ou Concluído: a linha do tempo diz por que ficou
        assert "O card segue em Consulta" in c.execute(
            "select descricao from prospeccao_atividades order by id desc limit 1").fetchone()[0]
        assert ca.mudar_situacao(c, CLINICA, depois, "cancelou") is None
        assert _status(c, mae) == ("consulta", 320000)
        assert c.execute("select tratamento_proposto from eventos_agenda where id in (%s,%s,%s) order by id",
                         (mae, filho, depois)).fetchall() == [(True,), (False,), (None,)]


def test_nao_de_um_nao_fecha_enquanto_o_outro_do_card_esta_na_clinica(pool):
    with pool.connection() as c:
        mae, _ = _marcar(c)
        filho, _ = _marcar(c, h=9, nome="Filho")
        _ate_atendimento(c, mae)
        _ate_atendimento(c, filho)
        assert ca.mudar_situacao(c, CLINICA, filho, "finalizado", tratamento="nao") is None
        assert _status(c, mae)[0] == "consulta"                 # a mãe ainda está em atendimento
        assert ca.mudar_situacao(c, CLINICA, mae, "finalizado", tratamento="nao") is None
        assert _status(c, mae)[0] == "ganho"                    # o "não" do filho não segura ninguém


def test_proposta_de_outra_passagem_nao_segura_o_card(pool):
    """Só segura a proposta feita DEPOIS de o card entrar em Consulta: a do ano
    passado, de outra passagem pela clínica, não prende o paciente lá."""
    with pool.connection() as c:
        velho, _ = _marcar(c)
        _ate_atendimento(c, velho)
        assert ca.mudar_situacao(c, CLINICA, velho, "finalizado", tratamento="sim") is None
        lead = ca.evento(c, CLINICA, velho)["lead"]
        c.execute("update eventos_agenda set situacao_em = now() - interval '90 days' where id=%s", (velho,))
        c.execute("update funil_movimentos set criado_em = now() - interval '91 days' where prospeccao_id=%s", (lead,))
        c.execute("update prospeccao set status='qualificado' where id=%s", (lead,))
        novo, _ = _marcar(c, h=9)
        _ate_atendimento(c, novo)
        assert _status(c, novo)[0] == "consulta"
        assert ca.mudar_situacao(c, CLINICA, novo, "finalizado", tratamento="nao") is None
        assert _status(c, novo)[0] == "ganho"


def test_chegou_esquecido_de_outro_dia_nao_prende_o_card_em_consulta(pool):
    """Nada fecha o Presente sozinho. O "Chegou" que a recepção esqueceu de finalizar
    na semana passada não é "outro paciente na clínica" hoje (2ª revisão de 01/10/2026)."""
    with pool.connection() as c:
        esquecido, _ = _marcar(c)
        assert ca.mudar_situacao(c, CLINICA, esquecido, "presente") is None
        hoje, _ = _marcar(c, dia=SEG + timedelta(days=7))
        assert ca.evento(c, CLINICA, hoje)["lead"] == ca.evento(c, CLINICA, esquecido)["lead"]
        _ate_atendimento(c, hoje)
        assert ca.mudar_situacao(c, CLINICA, hoje, "finalizado", tratamento="nao") is None
        assert _status(c, hoje)[0] == "ganho"


def test_card_arrastado_pra_consulta_depois_da_proposta_continua_esperando_o_plano(pool):
    """O card estava em Concluído quando o médico propôs (não anda sozinho); a recepção
    o arrasta pra Consulta pra montar o plano. O movimento à mão é mais novo que a
    proposta e não a apaga: o "não" seguinte não fecha o card."""
    with pool.connection() as c:
        eid, _ = _marcar(c)
        lead = ca.evento(c, CLINICA, eid)["lead"]
        c.execute("update prospeccao set status='ganho' where id=%s", (lead,))
        _ate_atendimento(c, eid)
        assert ca.mudar_situacao(c, CLINICA, eid, "finalizado", tratamento="sim") is None
        assert _status(c, eid)[0] == "ganho"
        c.commit()
        c.execute("update prospeccao set status='consulta' where id=%s", (lead,))
        c.execute("""insert into funil_movimentos (conta_id, prospeccao_id, de, para, motivo)
                     values (%s,%s,'ganho','consulta','manual')""", (CLINICA, lead))
        outro, _ = _marcar(c, h=9, nome="Filho")
        _ate_atendimento(c, outro)
        assert ca.mudar_situacao(c, CLINICA, outro, "finalizado", tratamento="nao") is None
        assert _status(c, outro)[0] == "consulta"


def test_cancelar_por_fora_a_consulta_ja_finalizada_nao_desfaz_o_plano_a_montar(pool):
    from finance import agenda as ag
    with pool.connection() as c:
        eid, _ = _marcar(c)
        _ate_atendimento(c, eid)
        assert ca.mudar_situacao(c, CLINICA, eid, "finalizado", tratamento="sim") is None
        c.commit()
    assert ag.cancelar_evento(pool, CLINICA, eid)
    with pool.connection() as c:
        assert _status(c, eid)[0] == "consulta"


def test_etapa_retorno_do_funil_antigo_criada_a_mao_nao_e_de_onde_a_agenda_tira(pool):
    """Conta sem o modelo novo, com uma etapa "Retorno" criada à mão em fase de venda
    (a chave é o nome): o card que está nela não sai pelo Finalizar, como na regra de antes."""
    with pool.connection() as c:
        c.execute("delete from funil_etapas where conta_id=%s and chave in ('consulta','tratamento','retorno')",
                  (CLINICA,))
        c.execute("insert into funil_etapas (conta_id, chave, fase) values (%s,'retorno','venda')", (CLINICA,))
        eid, _ = _marcar(c)
        c.execute("update prospeccao set status='retorno' where id=%s", (ca.evento(c, CLINICA, eid)["lead"],))
        _ate_atendimento(c, eid)
        assert ca.mudar_situacao(c, CLINICA, eid, "finalizado") is None
        assert _status(c, eid)[0] == "retorno"
        eid2, _ = _marcar(c, h=9, nome="Outra", fone="99 97777-0096")
        c.execute("update prospeccao set status='retorno' where id=%s", (ca.evento(c, CLINICA, eid2)["lead"],))
        _ate_atendimento(c, eid2)
        assert ca.mudar_situacao(c, CLINICA, eid2, "finalizado", tratamento="nao") is None
        assert _status(c, eid2)[0] == "retorno"


def test_chegou_por_engano_e_cancelado_por_fora_volta_pro_follow_up(pool):
    """Depois de Presente a agenda da clínica não cancela; a agenda de sempre, sim. O
    card que só estava em Consulta por causa desse horário não fica preso lá, fora de
    toda cobrança e sem horário nenhum."""
    from finance import agenda as ag
    with pool.connection() as c:
        eid, _ = _marcar(c)
        assert ca.mudar_situacao(c, CLINICA, eid, "presente") is None
        assert _status(c, eid)[0] == "consulta"
        c.commit()
    assert ag.cancelar_evento(pool, CLINICA, eid)
    with pool.connection() as c:
        assert _status(c, eid)[0] == "follow_up"


def test_finalizado_sem_tratamento_fecha_com_o_valor_da_consulta(pool):
    with pool.connection() as c:
        eid, _ = _marcar(c)
        _ate_atendimento(c, eid)
        assert ca.mudar_situacao(c, CLINICA, eid, "finalizado", tratamento="nao") is None
        assert _status(c, eid) == ("ganho", 50000)


def test_finalizado_sem_resposta_nao_prende_em_consulta_e_card_adiante_nao_volta(pool):
    with pool.connection() as c:
        eid, _ = _marcar(c)
        _ate_atendimento(c, eid)
        assert _status(c, eid)[0] == "consulta"
        assert ca.mudar_situacao(c, CLINICA, eid, "finalizado") is None
        assert _status(c, eid)[0] == "ganho"                    # sem a pergunta, vale "não": conclui
        eid2, _ = _marcar(c, h=9, nome="Outra", fone="99 97777-0091")
        lead = ca.evento(c, CLINICA, eid2)["lead"]
        c.execute("update prospeccao set status='proposta' where id=%s", (lead,))
        ca.mudar_situacao(c, CLINICA, eid2, "faltou")
        assert _status(c, eid2)[0] == "proposta"          # alguém já levou adiante: fica


def test_tela_finalizar_com_resultado_a_entregar(cli, pool):
    seg = _proxima_segunda()
    with pool.connection() as c:
        eid, erro = ca.agendar(c, CLINICA, profissional_id=_manoel(c)["id"], servico_id=_tipo(c, "Consulta")["id"],
                               inicio=ca.utc(seg, time(8)), nome="Ana", fone="99 97777-0099")
        assert erro is None
        _ate_atendimento(c, eid)
        c.commit()
    r = cli.post(f"/painel/clinica/agenda/evento/{eid}/situacao",
                 data={"nova": "finalizado", "tratamento": "nao", "resultado": "1", "resultado_em": "31/02"})
    assert "inválida" in cli.get(r.headers["location"]).text
    cli.post(f"/painel/clinica/agenda/evento/{eid}/situacao",       # a data sem a caixa vale como pedido
             data={"nova": "finalizado", "tratamento": "nao",
                   "resultado_em": (seg + timedelta(days=10)).isoformat()})
    with pool.connection() as c:
        assert c.execute("select previsto_em, estado from clinica_resultados where evento_id=%s", (eid,)
                         ).fetchone() == (seg + timedelta(days=10), "aguardando")
        assert _status(c, eid)[0] == "retorno"


def test_tela_finalizar_pergunta_o_tratamento(cli, pool):
    seg = _proxima_segunda()
    with pool.connection() as c:
        eid, erro = ca.agendar(c, CLINICA, profissional_id=_manoel(c)["id"], servico_id=_tipo(c, "Consulta")["id"],
                               inicio=ca.utc(seg, time(8)), nome="Ana", fone="99 97777-0092")
        assert erro is None
        _ate_atendimento(c, eid)
        c.commit()
    html = cli.get(f"/painel/clinica/agenda/evento/{eid}").text
    assert "O médico propôs tratamento?" in html and "Resultado a entregar" in html
    assert "vai para Retorno, se o médico pediu ou há resultado a entregar, ou Concluído" in html and "fica em Consulta" in html
    # a conta que ainda não aplicou o modelo lê pra onde o card vai NELA
    with pool.connection() as c:
        guardadas = c.execute("select chave, fase from funil_etapas where conta_id=%s", (CLINICA,)).fetchall()
        c.execute("delete from funil_etapas where conta_id=%s and chave in ('consulta','tratamento','retorno')",
                  (CLINICA,))
        c.commit()
    antes = cli.get(f"/painel/clinica/agenda/evento/{eid}").text
    assert "Não — Fechado" in antes and "Sim — Plano de tratamento" in antes and "Retorno, se" not in antes
    with pool.connection() as c:
        c.execute("delete from funil_etapas where conta_id=%s", (CLINICA,))
        for ch, fase in guardadas:
            c.execute("insert into funil_etapas (conta_id, chave, fase) values (%s,%s,%s)", (CLINICA, ch, fase))
        c.commit()
    # valor ilegível não finaliza calado sem o valor: volta pedindo o formato
    cli.post(f"/painel/clinica/agenda/evento/{eid}/situacao",
             data={"nova": "finalizado", "tratamento": "sim", "valor": "mil e quinhentos"})
    assert "Valor inválido" in cli.get(f"/painel/clinica/agenda/evento/{eid}").text
    with pool.connection() as c:
        assert ca.evento(c, CLINICA, eid)["situacao"] == "atendimento"
    r = cli.post(f"/painel/clinica/agenda/evento/{eid}/situacao",
                 data={"nova": "finalizado", "tratamento": "sim", "valor": "1.500,00"})
    # o médico propôs tratamento: a recepção vai direto montar o plano (fase 5)
    assert r.headers["location"] == f"/painel/clinica/planos/novo?evento={eid}"
    with pool.connection() as c:
        assert _status(c, eid) == ("consulta", 150000)



def test_agenda_antiga_manda_a_clinica_pra_agenda_nova(monkeypatch):
    """Aba aberta antes do deploy ou favorito velho: a recepção cai na agenda nova."""
    from web import painel_agenda as pag
    from web import portal
    estado = {"nicho": "clinica"}
    monkeypatch.setattr(pag, "conta_logada", lambda request: (CLINICA,))
    monkeypatch.setattr(portal, "nicho_da_conta", lambda conta: estado["nicho"])
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="teste")
    app.include_router(pag.router)

    @app.get("/_papel/{papel}")
    def _papel(request: Request, papel: str):
        request.session["papel"] = papel
        return {}

    cli = TestClient(app, follow_redirects=False)
    cli.get("/_papel/vendedor")
    assert cli.get("/painel/agenda").headers["location"] == "/painel/clinica/agenda"
    # quem não vende (financeiro) fica na agenda da conta: a rota segue e vai ao banco
    class Chegou(Exception):
        pass

    def _pool():
        raise Chegou
    monkeypatch.setattr(pag, "get_pool", _pool)
    cli.get("/_papel/financeiro")
    with pytest.raises(Chegou):
        cli.get("/painel/agenda")
    # e conta que não é clínica nunca é desviada
    estado["nicho"] = "eventos"
    cli.get("/_papel/vendedor")
    with pytest.raises(Chegou):
        cli.get("/painel/agenda")
