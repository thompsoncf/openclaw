"""O motor do "voltar a chamar" com banco (finance/voltar_a_chamar.py).

Cada teste monta a conversa da clínica como ela acontece: o paciente escreve, a
recepção passa o preço da consulta, e o relógio anda. As regras sem banco estão em
test_voltar_a_chamar_regras.py.

Schema mínimo dos caminhos exercitados, e a migração 346 de verdade por cima.
"""
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from finance import agente
from finance import voltar_a_chamar as vac

BASE = Path(__file__).resolve().parent.parent / "db" / "migracoes"
CLINICA = 39

_SQL = """
create table nichos (id bigserial primary key, slug text);
create table contas (id bigserial primary key, tipo text, nome text, nicho_id bigint);
create table membros (id bigserial primary key, conta_id bigint, nome text, email text,
  papel text, ativo boolean default true, whatsapp text, whatsapp_id text);
create table prospeccao (id bigserial primary key, conta_id bigint, vendedor_id bigint,
  empresa text, contato text, telefone text, whatsapp text, status text default 'novo',
  atualizado_em timestamptz default now(), criado_em timestamptz default now());
create table prospeccao_atividades (id bigserial primary key, prospeccao_id bigint,
  membro_id bigint, tipo text, resultado text, descricao text default '',
  criado_em timestamptz default now());
create table conversas (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  canal text default 'whatsapp', contato_ref text, contato_nome text,
  ultima_msg_em timestamptz default now(), criado_em timestamptz default now());
create table mensagens (id bigserial primary key, conversa_id bigint, canal text,
  direcao text, autor text, texto text, membro_id bigint, provider_sid text,
  midia_tipo text, criado_em timestamptz default now());
create table funil_regua (conta_id bigint primary key,
  gatilhos_modo text default 'off', cobranca_modo text default 'off',
  janela_dias text, janela_abre time, janela_fecha time, sem_resposta_min int,
  bola_nossa_min int, bola_cliente_min int, escala_min int, teto_avisos_dia int);
create table funil_etapas (id bigserial primary key, conta_id bigint, chave text, fase text);
create table funil_movimentos (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  de text, para text, motivo text, membro_id bigint, criado_em timestamptz default now());
insert into nichos (id, slug) values (1,'clinica'),(2,'eventos'),(3,'consultoria'),(4,'seguros');
insert into contas (id, tipo, nome, nicho_id) values
  (39,'pj','Clínica',1),(34,'pj','Festa',2),(50,'pj','Consultoria',3),(60,'pj','Corretora',4);
"""


@pytest.fixture()
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True,
                           kwargs={"autocommit": True, "prepare_threshold": None})
    dbname = "zaq_voltar_teste"
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
        c.execute((BASE / "346_voltar_a_chamar.sql").read_text(encoding="utf-8"))
        c.commit()
    yield p
    p.close()


def _br(dia, h, m=0):
    """Brasília → UTC. Setembro de 2026: segunda 21, sexta 25."""
    return datetime(2026, 9, dia, h, m, tzinfo=timezone.utc) + timedelta(hours=3)


LIGOU = _br(21, 8)
PRECO = "O investimento da consulta com o Dr. é de R$ 500,00 😊"


def _modo(pool, conta, modo, ligado_em=LIGOU, teto=None):
    with pool.connection() as c:
        c.execute("""insert into voltar_a_chamar_config (conta_id, modo, ligado_em, teto_dia)
                     values (%s,%s,%s,%s) on conflict (conta_id) do update
                     set modo=excluded.modo, ligado_em=excluded.ligado_em,
                         teto_dia=excluded.teto_dia""", (conta, modo, ligado_em, teto))
        c.commit()


def _lead(pool, conta=CLINICA, nome="Maria Clara", fone="5586999990001", status="novo"):
    with pool.connection() as c:
        lead = c.execute("""insert into prospeccao (conta_id, empresa, contato, whatsapp, status)
                            values (%s,%s,%s,%s,%s) returning id""",
                         (conta, nome, nome, fone, status)).fetchone()[0]
        conv = c.execute("""insert into conversas (conta_id, prospeccao_id, contato_ref, contato_nome)
                            values (%s,%s,%s,%s) returning id""",
                         (conta, lead, fone, nome)).fetchone()[0]
        c.commit()
    return lead, conv


def _msg(pool, conv, direcao, texto, quando, autor=None):
    with pool.connection() as c:
        mid = c.execute("""insert into mensagens (conversa_id, canal, direcao, autor, texto, criado_em)
                           values (%s,'whatsapp',%s,%s,%s,%s) returning id""",
                        (conv, direcao, autor or ("lead" if direcao == "in" else "humano"),
                         texto, quando)).fetchone()[0]
        c.commit()
    return mid


def _paciente_recebe_preco(pool, conta=CLINICA, quando=None, **kw):
    """O paciente pergunta, a recepção responde o preço 10 minutos depois."""
    quando = quando or _br(21, 10)
    lead, conv = _lead(pool, conta=conta, **kw)
    _msg(pool, conv, "in", "Oi, quanto é a consulta?", quando - timedelta(minutes=10))
    _msg(pool, conv, "out", PRECO, quando)
    return lead, conv


def _toques(pool, conta=CLINICA):
    with pool.connection() as c:
        return c.execute("""select toque, estado, devido_em, enviado_por from voltar_a_chamar_toques
                             where conta_id=%s order by prospeccao_id, toque""", (conta,)).fetchall()


@pytest.fixture()
def sem_envio(monkeypatch):
    """Qualquer tentativa de mandar WhatsApp estoura o teste."""
    def _nao(*a, **k):
        raise AssertionError("mandou WhatsApp sem poder")
    monkeypatch.setattr(agente, "_mandar", _nao)


@pytest.fixture()
def envios(monkeypatch):
    feitos = []

    def _mandar(c, conta_id, canal, destino, texto, conversa_id=None):
        feitos.append({"conta": conta_id, "canal": canal, "destino": destino,
                       "texto": texto, "conversa_id": conversa_id})
        return {"ok": True, "sid": f"sid-{len(feitos)}"}
    monkeypatch.setattr(agente, "_mandar", _mandar)
    return feitos


# ------------------------------------------------------------------ o fato

def test_preco_sem_resposta_gera_o_toque_de_3h(pool, sem_envio):
    _modo(pool, CLINICA, "sugere")
    _paciente_recebe_preco(pool)
    r = vac.rodar(pool, agora=_br(21, 10, 30))
    assert r["contas"] == 1 and r["novos"] == 4
    t = _toques(pool)
    assert [x[0] for x in t] == [1, 2, 3, 4]
    assert all(x[1] == "pendente" for x in t)
    assert t[0][2] == _br(21, 13)


def test_rodar_duas_vezes_nao_duplica(pool, sem_envio):
    _modo(pool, CLINICA, "sugere")
    _paciente_recebe_preco(pool)
    vac.rodar(pool, agora=_br(21, 10, 30))
    assert vac.rodar(pool, agora=_br(21, 10, 32))["novos"] == 0
    assert len(_toques(pool)) == 4


def test_conversa_viva_segura_o_toque(pool, envios):
    """Responder não encerra: o toque espera a conversa parar 3 horas."""
    _modo(pool, CLINICA, "ligado")
    lead, conv = _paciente_recebe_preco(pool)
    vac.rodar(pool, agora=_br(21, 10, 30))
    _msg(pool, conv, "in", "Vou ver com meu marido e te falo", _br(21, 11))
    vac.rodar(pool, agora=_br(21, 13, 5))            # o de 3h venceu, mas a conversa tem 2h
    assert envios == []
    assert {x[1] for x in _toques(pool)} == {"pendente"}
    with pool.connection() as c:
        tela = vac.hoje(c, CLINICA, _br(21, 13, 10), vac.config(c, CLINICA))
    assert tela["voltar"] == []
    assert [x["lead"] for x in tela["responderam"]] == [lead]
    assert "marido" in tela["responderam"][0]["frase"]
    # parou há 3h: aparece na tela com a mensagem pronta (e sai da lista de baixo)...
    with pool.connection() as c:
        tela = vac.hoje(c, CLINICA, _br(21, 14, 10), vac.config(c, CLINICA))
    assert [t["toque"] for t in tela["voltar"]] == [1]
    assert tela["responderam"] == []
    # ...mas SOZINHO não sai: a última palavra foi do paciente, a recepção lê antes
    vac.rodar(pool, agora=_br(21, 14, 5))
    assert envios == []


def test_agradeceu_e_parou_recebe_o_toque_do_dia_seguinte(pool, envios):
    """O caso de 10 dos 11 na conta 39: o paciente responde, a recepção responde, e
    a conversa morre ali sem ninguém marcar."""
    _modo(pool, CLINICA, "ligado")
    _, conv = _paciente_recebe_preco(pool)
    _msg(pool, conv, "in", "Ok, obrigada!", _br(21, 10, 40))
    _msg(pool, conv, "out", "Por nada 😊", _br(21, 10, 45))
    vac.rodar(pool, agora=_br(21, 10, 50))
    vac.rodar(pool, agora=_br(21, 13, 55))           # a recepção falou há pouco: o de 3h pula
    assert envios == []
    vac.rodar(pool, agora=_br(22, 10, 5))            # +1 dia, conversa parada
    assert len(envios) == 1 and "dúvida" in envios[0]["texto"]


def test_pergunta_do_paciente_sem_resposta_nao_vira_toque(pool, envios):
    """Pergunta sem resposta é da recepção (Esperando resposta), não de mensagem pronta."""
    _modo(pool, CLINICA, "ligado")
    _, conv = _paciente_recebe_preco(pool)
    _msg(pool, conv, "in", "Tem horário no sábado?", _br(21, 10, 40))
    for dia, h in ((21, 14), (22, 10), (24, 10), (28, 10)):
        vac.rodar(pool, agora=_br(dia, h, 5))
    assert envios == []
    with pool.connection() as c:
        tela = vac.hoje(c, CLINICA, _br(28, 10, 10), vac.config(c, CLINICA))
    assert tela["voltar"] == [] and len(tela["responderam"]) == 1


def test_prontinho_encerra_como_marcou(pool, sem_envio):
    _modo(pool, CLINICA, "sugere")
    _, conv = _paciente_recebe_preco(pool)
    vac.rodar(pool, agora=_br(21, 10, 30))
    _msg(pool, conv, "out", "Prontinho!! Seu agendamento foi realizado", _br(21, 12))
    vac.rodar(pool, agora=_br(21, 12, 5))
    assert {x[1] for x in _toques(pool)} == {"marcou"}


def test_card_em_consulta_agendada_encerra_como_marcou(pool, sem_envio):
    _modo(pool, CLINICA, "sugere")
    lead, _ = _paciente_recebe_preco(pool)
    vac.rodar(pool, agora=_br(21, 10, 30))
    with pool.connection() as c:
        c.execute("update prospeccao set status='qualificado' where id=%s", (lead,))
        c.commit()
    vac.rodar(pool, agora=_br(21, 10, 35))
    assert {x[1] for x in _toques(pool)} == {"marcou"}


def test_lead_perdido_encerra_como_dispensado(pool, sem_envio):
    _modo(pool, CLINICA, "sugere")
    lead, _ = _paciente_recebe_preco(pool)
    vac.rodar(pool, agora=_br(21, 10, 30))
    with pool.connection() as c:
        c.execute("update prospeccao set status='perdido' where id=%s", (lead,))
        c.commit()
    vac.rodar(pool, agora=_br(21, 10, 35))
    assert {x[1] for x in _toques(pool)} == {"dispensado"}


# ------------------------------------------------------------------ quem fica de fora

def test_ganho_perdido_equipe_e_conversa_aberta_por_nos_ficam_de_fora(pool, sem_envio):
    _modo(pool, CLINICA, "ligado")
    _paciente_recebe_preco(pool, fone="5586999990011", status="ganho")
    _paciente_recebe_preco(pool, fone="5586999990012", status="perdido")
    # alguém da equipe que escreve pro número da clínica
    with pool.connection() as c:
        c.execute("insert into membros (conta_id, nome, whatsapp_id) values (%s,'Recepção','86 99999-0013')",
                  (CLINICA,))
        c.commit()
    _paciente_recebe_preco(pool, fone="5586999990013")
    # a clínica abriu a conversa (o fornecedor nunca escreveu antes do preço)
    _, conv = _lead(pool, fone="5586999990014", nome="Fornecedor")
    _msg(pool, conv, "out", "Qual o preço da consulta do seu plano?", _br(21, 10))
    vac.rodar(pool, agora=_br(21, 10, 30))
    assert _toques(pool) == []


def test_outros_perfis_com_modo_ligado_recebem_zero(pool, sem_envio):
    for conta in (34, 50, 60):
        _modo(pool, conta, "ligado")
        _paciente_recebe_preco(pool, conta=conta, fone=f"55869999{conta:05d}")
    r = vac.rodar(pool, agora=_br(21, 13, 5))
    assert r["novos"] == 0 and r["enviados"] == 0
    for conta in (34, 50, 60):
        assert _toques(pool, conta) == []


def test_modo_off_nao_registra_nada(pool, sem_envio):
    _modo(pool, CLINICA, "off")
    _paciente_recebe_preco(pool)
    assert vac.rodar(pool, agora=_br(21, 13, 5))["contas"] == 0
    assert _toques(pool) == []


def test_so_um_rodar_passa_pelo_lock(pool, sem_envio):
    _modo(pool, CLINICA, "sugere")
    _paciente_recebe_preco(pool)
    with pool.connection() as outro:
        outro.execute("select pg_advisory_lock(%s)", (vac._LOCK,))
        try:
            assert vac.rodar(pool, agora=_br(21, 10, 30))["contas"] == 0
        finally:
            outro.execute("select pg_advisory_unlock(%s)", (vac._LOCK,))
    assert vac.rodar(pool, agora=_br(21, 10, 30))["contas"] == 1


# ------------------------------------------------------------------ o envio

def test_sugere_nunca_manda_sozinho(pool, sem_envio):
    _modo(pool, CLINICA, "sugere")
    _paciente_recebe_preco(pool)
    vac.rodar(pool, agora=_br(21, 10, 30))
    r = vac.rodar(pool, agora=_br(21, 13, 5))
    assert r["enviados"] == 0
    assert _toques(pool)[0][1] == "pendente"


def test_ligado_sai_pelo_chip_da_conversa_e_grava_bot(pool, envios):
    _modo(pool, CLINICA, "ligado")
    lead, conv = _paciente_recebe_preco(pool)
    vac.rodar(pool, agora=_br(21, 10, 30))
    assert envios == []                          # antes das 13h nada sai
    r = vac.rodar(pool, agora=_br(21, 13, 5))
    assert r["enviados"] == 1
    assert envios[0]["conversa_id"] == conv      # o chip sai da conversa
    assert envios[0]["destino"] == "5586999990001"
    assert envios[0]["texto"].startswith("Oi, Maria!")
    with pool.connection() as c:
        m = c.execute("""select autor, texto, provider_sid from mensagens
                          where conversa_id=%s order by id desc limit 1""", (conv,)).fetchone()
        t = c.execute("""select estado, enviado_por, mensagem_id is not null from voltar_a_chamar_toques
                          where prospeccao_id=%s and toque=1""", (lead,)).fetchone()
        ativ = c.execute("select count(*) from prospeccao_atividades where prospeccao_id=%s",
                         (lead,)).fetchone()[0]
    assert m[0] == "bot" and m[1] == envios[0]["texto"] and m[2] == "sid-1"
    assert t == ("enviado", "agente", True)
    assert ativ == 1
    # o toque que acabou de sair não faz o de amanhã ser pulado
    vac.rodar(pool, agora=_br(22, 10, 5))
    assert len(envios) == 2


def test_ligado_fora_da_janela_espera(pool, envios):
    _modo(pool, CLINICA, "ligado")
    _paciente_recebe_preco(pool, quando=_br(21, 17))     # o de 3h cairia às 20h
    vac.rodar(pool, agora=_br(21, 17, 30))
    vac.rodar(pool, agora=_br(21, 21))
    vac.rodar(pool, agora=_br(22, 7, 59))
    assert envios == []
    vac.rodar(pool, agora=_br(22, 17, 1))                 # +1 dia
    assert len(envios) == 1


def test_teto_diario_da_conta(pool, envios):
    _modo(pool, CLINICA, "ligado", teto=2)
    for i in range(3):
        _paciente_recebe_preco(pool, fone=f"558699999{i:04d}", nome=f"Paciente{i}")
    vac.rodar(pool, agora=_br(21, 10, 30))
    for minuto in range(5, 10):
        vac.rodar(pool, agora=_br(21, 13, minuto))
    assert len(envios) == 2


def test_um_por_ciclo(pool, envios):
    _modo(pool, CLINICA, "ligado")
    for i in range(3):
        _paciente_recebe_preco(pool, fone=f"558699999{i:04d}", nome=f"Paciente{i}")
    vac.rodar(pool, agora=_br(21, 10, 30))
    assert vac.rodar(pool, agora=_br(21, 13, 5))["enviados"] == 1


def test_recepcao_escreveu_depois_o_toque_do_dia_e_pulado(pool, envios):
    _modo(pool, CLINICA, "ligado")
    lead, conv = _paciente_recebe_preco(pool)
    vac.rodar(pool, agora=_br(21, 10, 30))
    # a rajada logo depois do preço não conta...
    _msg(pool, conv, "out", "A consulta dura uns 40 minutos", _br(21, 10, 2))
    # ...mas a recepção voltar a falar à tarde, sim
    _msg(pool, conv, "out", "Oi Maria, conseguiu ver?", _br(21, 12, 30))
    vac.rodar(pool, agora=_br(21, 13, 5))
    assert envios == []
    with pool.connection() as c:
        est = c.execute("select estado from voltar_a_chamar_toques where prospeccao_id=%s and toque=1",
                        (lead,)).fetchone()[0]
    assert est == "pulado"


# ------------------------------------------------------------------ repescagem

def test_preco_de_antes_de_ligar_gera_so_a_repescagem(pool, envios):
    _modo(pool, CLINICA, "ligado", ligado_em=_br(21, 8))
    _paciente_recebe_preco(pool, quando=_br(10, 10))          # 11 dias antes de ligar
    # respondeu e a conversa morreu: também é parado
    _, conv_resp = _paciente_recebe_preco(pool, quando=_br(11, 10), fone="5586999990002")
    _msg(pool, conv_resp, "in", "Obrigada, vou pensar", _br(11, 11))
    # marcou: fica de fora
    _, conv_marc = _paciente_recebe_preco(pool, quando=_br(12, 10), fone="5586999990003")
    _msg(pool, conv_marc, "out", "Prontinho!! Te espero dia 15", _br(12, 10, 20))
    # ainda conversando (última mensagem há menos de 1 dia): espera
    _, conv_viva = _paciente_recebe_preco(pool, quando=_br(13, 10), fone="5586999990004")
    _msg(pool, conv_viva, "in", "Posso te responder amanhã?", _br(20, 20))
    vac.rodar(pool, agora=_br(21, 10))
    t = _toques(pool)
    assert [(x[0], x[1]) for x in t] == [(0, "pendente"), (0, "pendente")]
    # e nem com o modo ligado ela sai sozinha
    vac.rodar(pool, agora=_br(21, 11))
    assert envios == []


def test_conversa_sem_card_tambem_entra(pool, envios):
    """As 84 conversas que a conta 39 trouxe ao conectar o WhatsApp (23/09/2026)
    entraram sem card — e 8 dos 11 pacientes que receberam o preço estão nelas.
    O toque sai pelo número da conversa, e o "Já marcou" só encerra a sequência."""
    _modo(pool, CLINICA, "ligado", ligado_em=_br(21, 8))
    with pool.connection() as c:
        conv = c.execute("""insert into conversas (conta_id, contato_ref, contato_nome)
                            values (%s,'5586999990077','Luana Castro') returning id""",
                         (CLINICA,)).fetchone()[0]
        c.commit()
    _msg(pool, conv, "in", "Oi, quanto fica a consulta?", _br(12, 9, 50))
    _msg(pool, conv, "out", PRECO, _br(12, 10))
    # e um paciente novo, depois de ligar, também sem card
    with pool.connection() as c:
        conv2 = c.execute("""insert into conversas (conta_id, contato_ref, contato_nome)
                             values (%s,'5586999990078','Rita') returning id""",
                          (CLINICA,)).fetchone()[0]
        c.commit()
    _msg(pool, conv2, "in", "Boa tarde, valor da consulta?", _br(21, 9, 50))
    _msg(pool, conv2, "out", PRECO, _br(21, 10))
    vac.rodar(pool, agora=_br(21, 10, 30))
    with pool.connection() as c:
        linhas = c.execute("""select conversa_id, toque, estado, prospeccao_id from voltar_a_chamar_toques
                               order by conversa_id, toque""").fetchall()
    assert (conv, 0, "pendente", None) in linhas
    assert [x[1] for x in linhas if x[0] == conv2] == [1, 2, 3, 4]
    vac.rodar(pool, agora=_br(21, 13, 5))
    assert [(e["conversa_id"], e["destino"]) for e in envios] == [(conv2, "5586999990078")]
    assert envios[0]["texto"].startswith("Oi, Rita!")
    with pool.connection() as c:
        tid = c.execute("select id from voltar_a_chamar_toques where conversa_id=%s", (conv,)).fetchone()[0]
    assert vac.ja_marcou(pool, CLINICA, tid, None)
    with pool.connection() as c:
        assert c.execute("select estado from voltar_a_chamar_toques where id=%s", (tid,)).fetchone()[0] == "marcou"
        assert c.execute("select count(*) from funil_movimentos").fetchone()[0] == 0
    with pool.connection() as c:
        p = vac.placar(c, CLINICA, _br(1, 0), _br(30, 0), vac.config(c, CLINICA))
    assert p["receberam"] == 2 and p["marcaram"] == 1


# ------------------------------------------------------------------ as ações

def test_nao_e_paciente_vale_pra_sempre(pool, sem_envio):
    _modo(pool, CLINICA, "sugere")
    _paciente_recebe_preco(pool)
    vac.rodar(pool, agora=_br(21, 10, 30))
    with pool.connection() as c:
        tid = c.execute("select min(id) from voltar_a_chamar_toques").fetchone()[0]
    assert vac.nao_e_paciente(pool, CLINICA, tid, None)
    assert {x[1] for x in _toques(pool)} == {"nao_paciente"}
    # o mesmo número volta como lead novo, com preço novo: continua fora
    _paciente_recebe_preco(pool, quando=_br(24, 10))
    vac.rodar(pool, agora=_br(24, 10, 30))
    assert {x[1] for x in _toques(pool)} == {"nao_paciente"}


def test_sair_bloqueia_pra_sempre(pool, sem_envio):
    _modo(pool, CLINICA, "sugere")
    _, conv = _paciente_recebe_preco(pool)
    vac.rodar(pool, agora=_br(21, 10, 30))
    _msg(pool, conv, "in", "SAIR", _br(21, 11))
    vac.rodar(pool, agora=_br(21, 11, 5))
    assert {x[1] for x in _toques(pool)} == {"saiu"}
    with pool.connection() as c:
        assert c.execute("select motivo from voltar_a_chamar_bloqueios").fetchall() == [("saiu",)]


def test_mandar_duas_vezes_manda_uma(pool, envios):
    _modo(pool, CLINICA, "sugere")
    _paciente_recebe_preco(pool)
    vac.rodar(pool, agora=_br(21, 13, 5))
    with pool.connection() as c:
        tid = c.execute("select id from voltar_a_chamar_toques where toque=1").fetchone()[0]
        c.execute("insert into membros (id, conta_id, nome) values (7,%s,'Ana da recepção')", (CLINICA,))
        c.commit()
    assert vac.mandar_sugerido(pool, CLINICA, tid, 7, agora=_br(21, 13, 6))["ok"]
    assert vac.mandar_sugerido(pool, CLINICA, tid, 7, agora=_br(21, 13, 7)) == {
        "ok": False, "erro": "ja_tratado"}
    assert len(envios) == 1
    with pool.connection() as c:
        autor, membro = c.execute("select autor, membro_id from mensagens order by id desc limit 1").fetchone()
        por = c.execute("select enviado_por from voltar_a_chamar_toques where id=%s", (tid,)).fetchone()[0]
    assert (autor, membro, por) == ("humano", 7, "recepcao")


def test_ja_marcou_leva_o_card_pra_consulta_agendada(pool, sem_envio):
    _modo(pool, CLINICA, "sugere")
    lead, _ = _paciente_recebe_preco(pool)
    vac.rodar(pool, agora=_br(21, 10, 30))
    with pool.connection() as c:
        tid = c.execute("select min(id) from voltar_a_chamar_toques").fetchone()[0]
    assert vac.ja_marcou(pool, CLINICA, tid, None)
    with pool.connection() as c:
        assert c.execute("select status from prospeccao where id=%s", (lead,)).fetchone()[0] == "qualificado"
        assert c.execute("select de, para, motivo from funil_movimentos").fetchall() == [
            ("novo", "qualificado", "manual")]
    assert {x[1] for x in _toques(pool)} == {"marcou"}


def test_o_proprio_toque_nao_vira_fato(pool, sem_envio):
    """Um toque com texto de preço (o dono não consegue salvar isso pela tela, mas
    o banco aceita) e o eco dele pelo celular não viram fato novo."""
    _modo(pool, CLINICA, "sugere")
    lead, conv = _lead(pool)
    _msg(pool, conv, "in", "oi", _br(21, 9))
    texto = "Oi Maria, a consulta segue R$ 500, quer marcar?"
    mid = _msg(pool, conv, "out", texto, _br(21, 10))
    _msg(pool, conv, "out", texto, _br(21, 10, 1))      # o eco do celular
    with pool.connection() as c:
        c.execute("""insert into voltar_a_chamar_toques (conta_id, prospeccao_id, conversa_id,
                       preco_msg_id, toque, estado, devido_em, texto, mensagem_id, criado_em)
                     values (%s,%s,%s,0,0,'enviado',%s,%s,%s,%s)""",
                  (CLINICA, lead, conv, _br(1, 10), texto, mid, _br(1, 10)))
        c.commit()
    vac.rodar(pool, agora=_br(21, 10, 30))
    assert len(_toques(pool)) == 1


def test_placar_3_de_13(pool, sem_envio):
    _modo(pool, CLINICA, "sugere")
    convs = [_paciente_recebe_preco(pool, fone=f"558699998{i:04d}", quando=_br(2 + i, 10))[1]
             for i in range(13)]
    for conv in convs[:3]:
        with pool.connection() as c:
            q = c.execute("select max(criado_em) from mensagens where conversa_id=%s", (conv,)).fetchone()[0]
        _msg(pool, conv, "out", "Prontinho!! Te espero", q + timedelta(days=1))
    # 2 dos 10 que não marcaram foram chamados de novo
    for conv in convs[3:5]:
        with pool.connection() as c:
            q = c.execute("select max(criado_em) from mensagens where conversa_id=%s", (conv,)).fetchone()[0]
        _msg(pool, conv, "out", "Oi, conseguiu ver?", q + timedelta(days=1))
    with pool.connection() as c:
        p = vac.placar(c, CLINICA, _br(1, 0), _br(30, 0), vac.config(c, CLINICA))
    assert p == {"receberam": 13, "marcaram": 3, "nunca_chamados": 8, "taxa": 23}


# ------------------------------------------------------------------ achados da revisão do #843

def test_nao_quero_no_comeco_de_um_pedido_nao_bloqueia(pool, sem_envio):
    """"Não quero esperar muito, tem horário?" quer marcar — não é pedido de saída."""
    _modo(pool, CLINICA, "sugere")
    _, conv = _paciente_recebe_preco(pool)
    vac.rodar(pool, agora=_br(21, 10, 30))
    _msg(pool, conv, "in", "Não quero esperar muito, tem horário essa semana?", _br(21, 11))
    vac.rodar(pool, agora=_br(21, 11, 5))
    with pool.connection() as c:
        assert c.execute("select count(*) from voltar_a_chamar_bloqueios").fetchone()[0] == 0
        tela = vac.hoje(c, CLINICA, _br(21, 11, 10), vac.config(c, CLINICA))
    assert [e["conversa_id"] for e in tela["esperando"]] == [conv]


def test_quem_pediu_pra_sair_e_volta_aparece_em_esperando(pool, sem_envio):
    _modo(pool, CLINICA, "sugere")
    _, conv = _paciente_recebe_preco(pool)
    vac.rodar(pool, agora=_br(21, 10, 30))
    _msg(pool, conv, "in", "Não quero, obrigada", _br(21, 11))
    vac.rodar(pool, agora=_br(21, 11, 5))
    assert {x[1] for x in _toques(pool)} == {"saiu"}
    _msg(pool, conv, "in", "Oi, mudei de ideia, tem horário sábado?", _br(22, 9))
    with pool.connection() as c:
        tela = vac.hoje(c, CLINICA, _br(22, 9, 5), vac.config(c, CLINICA))
    assert [e["conversa_id"] for e in tela["esperando"]] == [conv]


def test_o_de_3h_nao_escorrega_pra_manha_seguinte(pool, envios):
    """Preço às 15h, paciente responde às 16h30 (a conversa só para às 19h30, depois
    de fechar): o toque de +3h morre na virada, e o de +1 dia sai no dia dele."""
    _modo(pool, CLINICA, "ligado")
    _, conv = _paciente_recebe_preco(pool, quando=_br(21, 15))
    vac.rodar(pool, agora=_br(21, 15, 5))
    _msg(pool, conv, "in", "Vou ver, obrigada", _br(21, 16, 30))
    _msg(pool, conv, "out", "Fico no aguardo 😊", _br(21, 16, 35))
    vac.rodar(pool, agora=_br(22, 8, 5))
    assert envios == []
    with pool.connection() as c:
        est = c.execute("select estado from voltar_a_chamar_toques where toque=1").fetchone()[0]
    assert est == "pulado"
    vac.rodar(pool, agora=_br(22, 15, 5))            # +1 dia
    assert len(envios) == 1 and "dúvida" in envios[0]["texto"]


def test_um_por_dia_tambem_na_tela_e_no_clique(pool, envios):
    """O toque 2 ficou esperando o clique até quinta; o 3 vence no mesmo dia. Depois
    de mandar o 2, o 3 não aparece nem sai clicando."""
    _modo(pool, CLINICA, "sugere")
    _paciente_recebe_preco(pool)                     # segunda 10h
    vac.rodar(pool, agora=_br(21, 10, 30))
    with pool.connection() as c:
        t2, t3 = [r[0] for r in c.execute(
            "select id from voltar_a_chamar_toques where toque in (2,3) order by toque").fetchall()]
        # o 3 vence quinta 10h; o 2 continua pendente (ninguém clicou)
        c.execute("update voltar_a_chamar_toques set estado='pendente' where id=%s", (t2,))
        c.commit()
    assert vac.mandar_sugerido(pool, CLINICA, t2, None, agora=_br(24, 9))["ok"]
    with pool.connection() as c:
        tela = vac.hoje(c, CLINICA, _br(24, 13, 5), vac.config(c, CLINICA))
    assert tela["voltar"] == []
    assert vac.mandar_sugerido(pool, CLINICA, t3, None, agora=_br(24, 13, 5)) == {
        "ok": False, "erro": "hoje_ja"}
    # e o enviar direto também recusa no mesmo dia
    with pool.connection() as c:
        r = vac.enviar(c, CLINICA, t3, vac.config(c, CLINICA), por="recepcao", agora=_br(24, 13, 6))
    assert r == {"ok": False, "erro": "hoje_ja"}
    assert len(envios) == 1


def test_mandar_com_modo_desligado_nao_sai(pool, envios):
    _modo(pool, CLINICA, "sugere")
    _paciente_recebe_preco(pool)
    vac.rodar(pool, agora=_br(21, 13, 5))
    with pool.connection() as c:
        tid = c.execute("select id from voltar_a_chamar_toques where toque=1").fetchone()[0]
    _modo(pool, CLINICA, "off")
    assert vac.mandar_sugerido(pool, CLINICA, tid, None, agora=_br(21, 13, 6)) == {
        "ok": False, "erro": "desligado"}
    assert envios == []


def test_mandar_reconfere_sair_na_hora_do_clique(pool, envios):
    """A tela ficou aberta; o paciente respondeu SAIR depois. O clique não manda."""
    _modo(pool, CLINICA, "sugere")
    _, conv = _paciente_recebe_preco(pool)
    vac.rodar(pool, agora=_br(21, 13, 5))
    with pool.connection() as c:
        tid = c.execute("select id from voltar_a_chamar_toques where toque=1").fetchone()[0]
    _msg(pool, conv, "in", "Sair", _br(21, 13, 6))
    assert vac.mandar_sugerido(pool, CLINICA, tid, None, agora=_br(21, 13, 7))["ok"] is False
    assert envios == []


def test_foto_ou_pergunta_sem_interrogacao_no_bloco_segura_o_toque(pool, envios):
    _modo(pool, CLINICA, "ligado")
    _, conv = _paciente_recebe_preco(pool)
    _msg(pool, conv, "in", "Tem horário sábado?", _br(21, 10, 40))
    _msg(pool, conv, "in", "ou segunda cedo", _br(21, 10, 41))
    _, conv2 = _paciente_recebe_preco(pool, fone="5586999990002", nome="Bia")
    with pool.connection() as c:
        c.execute("""insert into mensagens (conversa_id, canal, direcao, autor, texto, midia_tipo, criado_em)
                     values (%s,'whatsapp','in','lead','📷 Foto','imagem',%s)""", (conv2, _br(21, 10, 40)))
        c.commit()
    for dia, h in ((21, 14), (22, 10), (24, 10)):
        vac.rodar(pool, agora=_br(dia, h, 5))
    assert envios == []
    with pool.connection() as c:
        tela = vac.hoje(c, CLINICA, _br(24, 10, 10), vac.config(c, CLINICA))
    assert tela["voltar"] == []


def test_placar_conta_quem_marca_depois_de_7_dias_e_mantem_quem_saiu(pool, sem_envio):
    _modo(pool, CLINICA, "sugere")
    _, conv1 = _paciente_recebe_preco(pool, fone="5586999990011", quando=_br(2, 10))
    _msg(pool, conv1, "out", "Prontinho!! Te espero", _br(12, 10))          # 10 dias depois
    lead2, _ = _paciente_recebe_preco(pool, fone="5586999990012", quando=_br(3, 10))
    with pool.connection() as c:                                           # card agendado
        c.execute("update prospeccao set status='qualificado' where id=%s", (lead2,))
        c.execute("""insert into voltar_a_chamar_bloqueios (conta_id, numero8, motivo)
                     values (%s,'99990013','saiu'),(%s,'99990014','nao_paciente')""",
                  (CLINICA, CLINICA))
        c.commit()
    _paciente_recebe_preco(pool, fone="5586999990013", quando=_br(4, 10))   # pediu pra sair
    _paciente_recebe_preco(pool, fone="5586999990014", quando=_br(5, 10))   # não é paciente
    with pool.connection() as c:
        p = vac.placar(c, CLINICA, _br(1, 0), _br(30, 0), vac.config(c, CLINICA))
    assert (p["receberam"], p["marcaram"]) == (3, 2)


# ------------------------------------------------------------------ 2ª revisão do #843

def _msg_id(pool, mid, conv, direcao, texto, quando):
    """Mensagem com id escolhido — o histórico importado chega com id fora da ordem
    da data (medido na conta 39: 1.379 de 2.046 mensagens)."""
    with pool.connection() as c:
        c.execute("""insert into mensagens (id, conversa_id, canal, direcao, autor, texto, criado_em)
                     values (%s,%s,'whatsapp',%s,%s,%s,%s)""",
                  (mid, conv, direcao, "lead" if direcao == "in" else "humano", texto, quando))
        c.commit()


def test_historico_fora_de_ordem_vale_a_data_e_nao_o_id(pool, sem_envio):
    _modo(pool, CLINICA, "sugere", ligado_em=_br(21, 8))
    # o paciente escreveu ANTES (pela data), mas ficou com id MAIOR que o preço
    _, conv = _lead(pool, fone="5586999990031", nome="Ana")
    _msg_id(pool, 9000, conv, "out", PRECO, _br(10, 10))
    _msg_id(pool, 9500, conv, "in", "quanto é a consulta?", _br(10, 9, 50))
    # e o "Prontinho" de DEPOIS ficou com id MENOR que o preço
    _, conv2 = _lead(pool, fone="5586999990032", nome="Bia")
    _msg_id(pool, 8001, conv2, "in", "valor da consulta?", _br(11, 9, 50))
    _msg_id(pool, 8003, conv2, "out", PRECO, _br(11, 10))
    _msg_id(pool, 8002, conv2, "out", "Prontinho!! Te espero sexta", _br(11, 11))
    vac.rodar(pool, agora=_br(21, 10))
    with pool.connection() as c:
        linhas = c.execute("select conversa_id, toque from voltar_a_chamar_toques").fetchall()
    assert linhas == [(conv, 0)]        # Ana é repescagem; Bia marcou e fica de fora


def test_pare_depois_do_ultimo_toque_bloqueia(pool, envios):
    _modo(pool, CLINICA, "sugere", ligado_em=_br(21, 8))
    _, conv = _paciente_recebe_preco(pool, quando=_br(12, 10))
    vac.rodar(pool, agora=_br(21, 10))
    with pool.connection() as c:
        tid = c.execute("select id from voltar_a_chamar_toques").fetchone()[0]
    assert vac.mandar_sugerido(pool, CLINICA, tid, None, agora=_br(21, 10, 1))["ok"]
    _msg(pool, conv, "in", "Pare", _br(21, 11))
    vac.rodar(pool, agora=_br(21, 11, 5))
    with pool.connection() as c:
        assert c.execute("select motivo from voltar_a_chamar_bloqueios").fetchall() == [("saiu",)]


def test_recusa_em_frase_encerra_a_sequencia(pool, envios):
    _modo(pool, CLINICA, "ligado")
    _, conv = _paciente_recebe_preco(pool)
    vac.rodar(pool, agora=_br(21, 10, 30))
    _msg(pool, conv, "in", "Não, obrigada", _br(21, 10, 40))
    _msg(pool, conv, "out", "Tudo bem! Qualquer coisa estamos aqui 😊", _br(21, 10, 45))
    for dia, h in ((21, 14), (22, 10), (24, 10), (28, 10)):
        vac.rodar(pool, agora=_br(dia, h, 5))
    assert envios == []
    assert {x[1] for x in _toques(pool)} == {"dispensado"}


def test_o_cartao_mostra_o_que_o_paciente_disse(pool, sem_envio):
    _modo(pool, CLINICA, "sugere")
    _, conv = _paciente_recebe_preco(pool)
    _msg(pool, conv, "in", "Vou ver com meu marido", _br(21, 10, 40))
    vac.rodar(pool, agora=_br(21, 10, 50))
    with pool.connection() as c:
        tela = vac.hoje(c, CLINICA, _br(21, 14, 5), vac.config(c, CLINICA))
    assert tela["voltar"][0]["ultima"] == "Vou ver com meu marido"


def test_toque_encalhado_vence(pool, envios):
    """Ninguém clicou por semanas: ao passar pra 'ligado', não sai o toque velho."""
    _modo(pool, CLINICA, "sugere")
    _paciente_recebe_preco(pool)
    vac.rodar(pool, agora=_br(21, 10, 30))
    _modo(pool, CLINICA, "ligado")
    vac.rodar(pool, agora=datetime(2026, 10, 14, 13, 5, tzinfo=timezone.utc))   # 3 semanas depois
    assert envios == []
    assert {x[1] for x in _toques(pool)} == {"pulado"}


def test_um_por_dia_e_por_numero_nao_por_conversa(pool, envios):
    """O mesmo telefone com duas conversas (dois chips): uma mensagem por dia só."""
    _modo(pool, CLINICA, "ligado")
    _paciente_recebe_preco(pool, fone="5586999990041", nome="Carla")
    _paciente_recebe_preco(pool, fone="(86) 99999-0041", nome="Carla")
    vac.rodar(pool, agora=_br(21, 10, 30))
    for minuto in range(5, 10):
        vac.rodar(pool, agora=_br(21, 13, minuto))
    assert len(envios) == 1


def test_excluir_o_card_nao_trava_e_guarda_o_historico(pool, sem_envio):
    _modo(pool, CLINICA, "sugere")
    lead, conv = _paciente_recebe_preco(pool)
    vac.rodar(pool, agora=_br(21, 10, 30))
    with pool.connection() as c:
        c.execute("update conversas set prospeccao_id = null where id=%s", (conv,))
        c.execute("delete from prospeccao where id=%s", (lead,))
        c.commit()
        assert c.execute("""select count(*), count(prospeccao_id) from voltar_a_chamar_toques""").fetchone() == (4, 0)


def test_lembrete_ou_vaga_da_clinica_hoje_segura_o_toque(pool, envios):
    """O "1 por paciente por dia" soma as outras automáticas da clínica: quem recebeu
    o lembrete da sessão (ou a vaga) hoje não recebe o toque, nem na tela nem sozinho."""
    _modo(pool, CLINICA, "ligado")
    _lead_id, conv = _paciente_recebe_preco(pool, fone="5586999990077", nome="Beatriz")
    vac.rodar(pool, agora=_br(21, 10, 30))
    with pool.connection() as c:
        c.execute("""create table clinica_lembretes (id bigserial primary key, conta_id bigint, conversa_id bigint,
                                                      tipo text, ref_id bigint, enviado_em timestamptz,
                                                      mensagem_id bigint, estado text default 'enviado')""")
        c.execute("""insert into clinica_lembretes (conta_id, conversa_id, tipo, ref_id, enviado_em)
                     values (%s,%s,'sessao',1,%s)""", (CLINICA, conv, _br(21, 11)))
        c.commit()
        assert vac.hoje(c, CLINICA, _br(21, 13, 5), vac.config(c, CLINICA))["voltar"] == []
    vac.rodar(pool, agora=_br(21, 13, 5))
    assert envios == []
    # o lembrete que falhou (chip caído) não conta
    with pool.connection() as c:
        c.execute("update clinica_lembretes set estado='falhou'")
        c.commit()
    vac.rodar(pool, agora=_br(21, 13, 6))
    assert len(envios) == 1
