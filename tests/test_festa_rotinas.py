"""A proposta, a data segurada e o pós-festa (migração 421, finance/festa_rotinas.py) —
o funil novo de eventos, parte 2b (docs/mockups/funil_novo_rotinas.html).

O que cada bloco segura:

* A VALIDADE: só com o número da conta, só pra proposta emitida depois dele, nunca
  depois da festa; o selo do card anda de "vale até" a "venceu"; o link do cliente lê
  a mesma regra.
* A DISPUTA: outro cliente pedindo a data segurada encolhe a reserva pra 48h (nunca
  estica, nunca a da IA) e avisa quem segura — uma vez.
* A RESERVA VENCIDA: o card sai de Data segurada pra Proposta, como 'agenda' (a TRAVA
  3 não deixa o gatilho devolver), com a nota e o selo.
* O PÓS-FESTA: o vendedor recebe o texto pronto; a IA manda na conversa dela; uma vez
  por festa; com o link quando há.
* A CONFIG e a MIGRAÇÃO (Prime com 7 dias, 48h e o pós-festa ligado).
"""
import inspect
import os
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from finance import agenda as ag
from finance import agente
from finance import cockpit as ck
from finance import festa_rotinas as fx
from finance import follow_up as fu

BASE = Path(__file__).resolve().parents[1] / "db" / "migracoes"
BRT = ag.BRT
PRIME, OUTRA = 34, 35
AGORA = datetime(2026, 9, 28, 11, 0, tzinfo=BRT)          # segunda, 11h
HOJE = AGORA.date()

_SQL = """
create table nichos (id bigserial primary key, slug text, nome text);
create table contas (id bigint primary key, nome text, nicho_id bigint);
create table membros (id bigserial primary key, conta_id bigint, nome text, email text,
  ativo boolean default true, whatsapp text, whatsapp_id text);
create table prospeccao (id bigserial primary key, conta_id bigint, vendedor_id bigint,
  empresa text, contato text, whatsapp text, telefone text, status text default 'novo',
  estagio text default 'lead', evento_em date, orcamento_id bigint, criado_em timestamptz default now(),
  atualizado_em timestamptz default now());
create table orcamentos (id bigserial primary key, conta_id bigint, status text,
  criado_em timestamptz default now());
create table eventos_agenda (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  titulo text, inicio timestamptz, status text default 'ativo', tipo text default 'empresa',
  tipo_evento text, pre_reserva_ate timestamptz);
create table funil_etapas (id bigserial primary key, conta_id bigint, chave text, rotulo text,
  ordem int default 0, fase text default 'venda', gatilho text);
create table funil_movimentos (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  de text, para text, motivo text, membro_id bigint, criado_em timestamptz default now());
create table prospeccao_atividades (id bigserial primary key, prospeccao_id bigint,
  membro_id bigint, tipo text, descricao text, criado_em timestamptz default now());
create table conversas (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  canal text default 'whatsapp', contato_ref text, agente_ativo boolean default false,
  status text default 'aberta', ultima_msg_em timestamptz default now(), chip_id bigint);
create table mensagens (id bigserial primary key, conversa_id bigint, canal text,
  direcao text, autor text, texto text, provider_sid text, status text,
  criado_em timestamptz default now());
create unique index idx_mensagens_sid_conversa
  on mensagens (conversa_id, provider_sid) where provider_sid is not null;
create table ia_orcamentos (orcamento_id bigint primary key, conta_id bigint);
"""


@pytest.fixture()
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_festa_rotinas_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity "
                  "where datname=%s and pid <> pg_backend_pid()", (dbname,))
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=2, max_size=6, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        c.execute("insert into contas (id, nome) values (%s,'Prime'), (%s,'Outra')", (PRIME, OUTRA))
        for m in ("388_regra_por_chip.sql", "401_ia_fora_da_esteira.sql",
                  "414_visita_rotinas.sql", "421_festa_rotinas.sql",
                  "425_revisao_motores_parte1.sql"):
            c.execute((BASE / m).read_text(encoding="utf-8"))
        # o marco do "daqui pra frente": a regra da Prime existe desde a semana passada
        c.execute("""update visita_rotinas_config set validade_desde=%s, pos_festa_desde=%s
                      where conta_id=%s""", (AGORA - timedelta(days=7), AGORA - timedelta(days=7), PRIME))
        c.execute("""insert into funil_etapas (conta_id, chave, rotulo, ordem, fase, gatilho) values
                     (%s,'proposta','Proposta',40,'venda','negociacao_valores'),
                     (%s,'evento_realizado','Data segurada',70,'venda','orcamento_aprovado'),
                     (%s,'pos_festa','Pós-festa',905,'pos','festa_passou')""", (PRIME, PRIME, PRIME))
        c.commit()
    yield p
    p.close()


@pytest.fixture()
def rec(monkeypatch):
    out = {"cliente": [], "zap": [], "push": []}

    def _mandar(c, conta, canal, destino, texto, conversa_id=None):
        out["cliente"].append((conversa_id, texto))
        return {"ok": True, "sid": f"s{len(out['cliente'])}"}
    monkeypatch.setattr(agente, "_mandar", _mandar)
    monkeypatch.setattr(fu, "_mandar_zap", lambda pool_, conta, numero, texto:
                        out["zap"].append((numero, texto)) or {"ok": True})
    monkeypatch.setattr(ck, "enviar_push", lambda pool_, conta, mid, t, corpo, url="", **k:
                        out["push"].append((mid, t, corpo)) or 1)
    return out


def _membro(pool, nome="Jacqueline", zap="5586900000001"):
    with pool.connection() as c:
        m = c.execute("insert into membros (conta_id, nome, whatsapp) values (%s,%s,%s) returning id",
                      (PRIME, nome, zap)).fetchone()[0]
        c.commit()
    return m


def _lead(pool, vend, status="proposta", dia=None, nome="Iara Souza", orc=None):
    with pool.connection() as c:
        lid = c.execute("""insert into prospeccao (conta_id, vendedor_id, contato, status, evento_em,
                                                   orcamento_id)
                           values (%s,%s,%s,%s,%s,%s) returning id""",
                        (PRIME, vend, nome, status, dia, orc)).fetchone()[0]
        c.commit()
    return lid


def _orc(pool, criado, status="enviado"):
    with pool.connection() as c:
        o = c.execute("insert into orcamentos (conta_id, status, criado_em) values (%s,%s,%s) returning id",
                      (PRIME, status, criado)).fetchone()[0]
        c.commit()
    return o


def _reserva(pool, lead, dia, ate, status="pre_reservado"):
    with pool.connection() as c:
        e = c.execute("""insert into eventos_agenda (conta_id, prospeccao_id, titulo, inicio, status,
                                                    tipo_evento, pre_reserva_ate)
                         values (%s,%s,'Festa',%s,%s,'15 anos',%s) returning id""",
                      (PRIME, lead, datetime(dia.year, dia.month, dia.day, 19, tzinfo=BRT),
                       status, ate)).fetchone()[0]
        c.commit()
    return e


def _selos(pool, ids, agora=AGORA):
    with pool.connection() as c:
        return fx.selos(c, PRIME, [{"id": i} for i in ids], agora)


# ══════════════════════════════════════════════ 1. a validade

def test_validade_so_com_o_numero_so_depois_do_marco_e_nunca_depois_da_festa():
    desde = datetime(2026, 9, 20, 10, tzinfo=BRT)
    cfg = {"proposta_validade_dias": 7, "validade_desde": desde}
    assert fx.validade(cfg, date(2026, 9, 25), None) == date(2026, 10, 2)
    assert fx.validade(cfg, date(2026, 9, 25), date(2026, 9, 29)) == date(2026, 9, 29)
    assert fx.validade(cfg, date(2026, 9, 19), None) is None          # emitida antes do marco
    assert fx.validade({"proposta_validade_dias": None, "validade_desde": desde},
                       date(2026, 9, 25), None) is None


def test_o_selo_da_proposta_anda_de_vale_ate_a_venceu(pool):
    v = _membro(pool)
    a = _lead(pool, v, orc=_orc(pool, AGORA - timedelta(days=2)))    # vale até 03/10
    b = _lead(pool, v, orc=_orc(pool, AGORA - timedelta(days=6)))    # vence amanhã
    d = _lead(pool, v, orc=_orc(pool, AGORA - timedelta(days=7)))    # vence hoje (emitida no marco)
    e = _lead(pool, v, orc=_orc(pool, AGORA - timedelta(days=8)))    # emitida antes do marco
    r = _lead(pool, v, orc=_orc(pool, AGORA - timedelta(days=2), status="rascunho"))
    s = _selos(pool, [a, b, d, e, r])
    assert s[a] == ("vale até 03/10", "nt")
    assert s[b] == ("vence amanhã", "at")
    assert s[d] == ("vence hoje", "at")
    assert e not in s and r not in s
    later = _selos(pool, [a], AGORA + timedelta(days=6))
    assert later[a] == ("venceu 03/10", "bad")


def test_o_link_do_cliente_le_a_mesma_regra():
    fonte = inspect.getsource(__import__("web.proposta", fromlist=["x"]))
    assert "_frt.validade_do_orcamento(pool or get_pool(), conta_id, criado, dia_evento)" in fonte


# ══════════════════════════════════════════════ 2. a data segurada

def test_outro_cliente_pede_a_data_a_reserva_encolhe_pra_48h_e_avisa_uma_vez(pool, rec):
    v = _membro(pool)
    dia = date(2027, 2, 13)
    a = _lead(pool, v, "evento_realizado", dia)
    ev = _reserva(pool, a, dia, AGORA + timedelta(days=10))
    assert fx.rodar(pool, AGORA)["disputas"] == 0                    # ninguém pediu ainda
    _lead(pool, v, "contatado", dia, nome="Léo")
    assert fx.rodar(pool, AGORA)["disputas"] == 1
    with pool.connection() as c:
        ate = c.execute("select pre_reserva_ate from eventos_agenda where id=%s", (ev,)).fetchone()[0]
        antes = c.execute("select prazo_antes from data_disputas where evento_id=%s", (ev,)).fetchone()[0]
    assert ate == AGORA + timedelta(hours=48)
    assert antes == AGORA + timedelta(days=10)
    aviso = [z for z in rec["zap"] if "Outro cliente pediu" in z[1]]
    assert len(aviso) == 1 and "sábado 13/02" in aviso[0][1] and "passou pra 48h" in aviso[0][1]
    assert fx.rodar(pool, AGORA + timedelta(minutes=5))["disputas"] == 0
    s = _selos(pool, [a])
    assert s[a] == ("⏳ 48h pro sinal · outro cliente pediu a data", "bad")


def test_a_venda_fechada_com_pre_reserva_na_agenda_nao_encolhe(pool, rec):
    """A Prime tem uma venda fechada cuja festa ficou como pré-reserva: encolher o
    prazo dela liberaria a data de quem já comprou."""
    v = _membro(pool)
    dia = date(2026, 11, 29)
    a = _lead(pool, v, "ganho", dia)
    ev = _reserva(pool, a, dia, AGORA + timedelta(days=3))
    _lead(pool, v, "proposta", dia, nome="Quer a mesma")
    assert fx.rodar(pool, AGORA)["disputas"] == 0
    with pool.connection() as c:
        assert c.execute("select pre_reserva_ate from eventos_agenda where id=%s", (ev,)).fetchone()[0] \
            == AGORA + timedelta(days=3)


def test_a_disputa_nunca_estica_nem_encolhe_a_reserva_da_ia(pool, rec):
    v = _membro(pool)
    dia = date(2027, 2, 20)
    a = _lead(pool, v, "evento_realizado", dia)
    ev_curta = _reserva(pool, a, dia, AGORA + timedelta(hours=20))
    _lead(pool, v, "proposta", dia, nome="Outro")
    dia2 = date(2027, 2, 27)
    orc = _orc(pool, AGORA)
    b = _lead(pool, v, "evento_realizado", dia2, orc=orc)
    ev_ia = _reserva(pool, b, dia2, AGORA + timedelta(hours=70))
    _lead(pool, v, "proposta", dia2, nome="Mais um")
    with pool.connection() as c:
        c.execute("insert into ia_orcamentos (orcamento_id, conta_id) values (%s,%s)", (orc, PRIME))
        c.commit()
    assert fx.rodar(pool, AGORA)["disputas"] == 2                    # os dois avisados
    with pool.connection() as c:
        prazos = dict(c.execute("select id, pre_reserva_ate from eventos_agenda").fetchall())
    assert prazos[ev_curta] == AGORA + timedelta(hours=20)
    assert prazos[ev_ia] == AGORA + timedelta(hours=70)


def test_a_reserva_vencida_devolve_o_card_pra_proposta_e_o_gatilho_nao_o_leva_de_volta(pool, rec):
    v = _membro(pool)
    dia = date(2027, 3, 6)
    a = _lead(pool, v, "evento_realizado", dia)
    _reserva(pool, a, dia, AGORA - timedelta(hours=1), status="cancelado")
    assert fx.rodar(pool, AGORA)["reservas_vencidas"] == 1
    with pool.connection() as c:
        st = c.execute("select status from prospeccao where id=%s", (a,)).fetchone()[0]
        mov = c.execute("select de, para, motivo from funil_movimentos where prospeccao_id=%s",
                        (a,)).fetchall()
        nota = c.execute("select descricao from prospeccao_atividades where prospeccao_id=%s",
                         (a,)).fetchone()[0]
    assert st == "proposta"
    assert mov == [("evento_realizado", "proposta", "agenda")]       # 'agenda' = mão humana
    assert "venceu" in nota and "sem o sinal" in nota
    assert _selos(pool, [a])[a] == ("reserva venceu", "bad")
    assert fx.rodar(pool, AGORA)["reservas_vencidas"] == 0


def test_a_reserva_de_pe_nao_volta_e_mostra_a_contagem(pool, rec):
    v = _membro(pool)
    dia = date(2027, 3, 13)
    a = _lead(pool, v, "evento_realizado", dia)
    _reserva(pool, a, dia, AGORA + timedelta(hours=41, minutes=30))
    b = _lead(pool, v, "evento_realizado", date(2027, 3, 20))
    _reserva(pool, b, date(2027, 3, 20), AGORA + timedelta(days=9))
    assert fx.rodar(pool, AGORA)["reservas_vencidas"] == 0
    s = _selos(pool, [a, b])
    assert s[a] == ("⏳ 41h pro sinal", "at")
    assert s[b] == ("⏳ sinal até 07/10", "at")


# ══════════════════════════════════════════════ 3. o pós-festa

def test_pos_festa_do_vendedor_vira_aviso_com_o_texto_pronto_uma_vez(pool, rec):
    v = _membro(pool)
    with pool.connection() as c:
        c.execute("update visita_rotinas_config set avaliacao_link='https://g.page/r/prime' "
                  "where conta_id=%s", (PRIME,))
        c.commit()
    a = _lead(pool, v, "pos_festa", HOJE - timedelta(days=1), nome="Ana Paula")
    assert fx.rodar(pool, AGORA)["pos_festa"] == 1
    assert fx.rodar(pool, AGORA + timedelta(minutes=5))["pos_festa"] == 0
    aviso = [z for z in rec["zap"] if "🎉 A festa de Ana foi ontem" in z[1]]
    assert len(aviso) == 1
    assert "Obrigada por celebrar com a gente ontem 🎉" in aviso[0][1]
    assert "https://g.page/r/prime" in aviso[0][1] and "me passa o contato" in aviso[0][1]
    assert rec["cliente"] == []                                      # o vendedor é quem manda


def test_pos_festa_da_ia_a_ia_manda_na_conversa_dela(pool, rec):
    zaq = _membro(pool, "ZAQ SDR", "5586900000009")
    with pool.connection() as c:
        c.execute("""insert into chip_regra (conta_id, chip_id, ativa, membro_id, ia_ligada)
                     values (%s,36,true,%s,true)""", (PRIME, zaq))
        c.commit()
    a = _lead(pool, zaq, "pos_festa", HOJE - timedelta(days=1), nome="Ana Paula")
    with pool.connection() as c:
        conv = c.execute("""insert into conversas (conta_id, prospeccao_id, contato_ref, agente_ativo)
                            values (%s,%s,'5586988887777',true) returning id""", (PRIME, a)).fetchone()[0]
        c.commit()
    assert fx.rodar(pool, AGORA)["pos_festa"] == 1
    assert len(rec["cliente"]) == 1 and rec["cliente"][0][0] == conv
    txt = rec["cliente"][0][1]
    assert txt.startswith("Oi, Ana! Obrigada por celebrar com a gente ontem 🎉 Deu tudo certo?")
    assert "avaliação" not in txt                                    # sem link, sem avaliação
    assert "me passa o contato" in txt


def test_pos_festa_nao_alcanca_festa_de_antes_de_ligar_nem_conta_desligada(pool, rec):
    v = _membro(pool)
    _lead(pool, v, "pos_festa", HOJE - timedelta(days=20))
    with pool.connection() as c:
        c.execute("update visita_rotinas_config set pos_festa=false where conta_id=%s", (PRIME,))
        c.commit()
    _lead(pool, v, "pos_festa", HOJE - timedelta(days=1))
    assert fx.rodar(pool, AGORA)["pos_festa"] == 0


# ══════════════════════════════════════════════ a config, a migração e as telas

def test_a_migracao_liga_a_prime_e_e_idempotente(pool):
    with pool.connection() as c:
        c.execute((BASE / "421_festa_rotinas.sql").read_text(encoding="utf-8"))
        c.commit()
        cfg = fx.config(c, PRIME)
        assert (cfg["proposta_validade_dias"], cfg["reserva_disputada_h"], cfg["pos_festa"]) == (7, 48, True)
        assert fx.config(c, OUTRA)["proposta_validade_dias"] is None


def test_salvar_config_valida_e_so_move_o_marco_quando_liga(pool):
    with pool.connection() as c:
        assert "1 a 90" in fx.salvar_config(c, OUTRA, validade_dias="200", disputada_h="",
                                            pos_festa=False, avaliacao_link="")
        c.rollback()
        assert "não parece um endereço" in fx.salvar_config(c, OUTRA, validade_dias="7",
                                                            disputada_h="48", pos_festa=True,
                                                            avaliacao_link="meu link")
        c.rollback()
        assert fx.salvar_config(c, OUTRA, validade_dias="7", disputada_h="48", pos_festa=True,
                                avaliacao_link="g.page/r/abc") is None
        c.commit()
        cfg = fx.config(c, OUTRA)
        assert cfg["avaliacao_link"] == "https://g.page/r/abc"
        marco = cfg["validade_desde"]
        assert fx.salvar_config(c, OUTRA, validade_dias="10", disputada_h="0", pos_festa=True,
                                avaliacao_link="") is None
        c.commit()
        cfg = fx.config(c, OUTRA)
        assert cfg["validade_desde"] == marco and cfg["proposta_validade_dias"] == 10
        assert cfg["reserva_disputada_h"] is None and cfg["avaliacao_link"] == ""


def test_os_campos_na_regua_e_o_selo_no_card():
    from web import painel_prospeccao as pp
    tpl = pp._REGUA_TPL
    for campo in ('name="validade_dias"', 'name="disputada_h"', 'name="pos_festa"',
                  'name="avaliacao_link"'):
        assert campo in tpl, campo
    rota = inspect.getsource(pp.regua_rotinas_festa)
    assert "_frt.salvar_config(" in rota and "async def" not in rota
    assert '{% if c.selo_festa %}<div class="kbvis {{ c.selo_festa[1] }}"' in pp._KANBAN_TPL
    pp._env.parse(tpl)
    pp._env.parse(pp._KANBAN_TPL)


# ══════════════════════════════════════════════ a revisão de 27/09/2026 (parte 1)

def _ia_com_conversa(pool, nome_lead="Ana Paula"):
    zaq = _membro(pool, "ZAQ SDR", "5586900000009")
    with pool.connection() as c:
        c.execute("""insert into chip_regra (conta_id, chip_id, ativa, membro_id, ia_ligada)
                     values (%s,36,true,%s,true)""", (PRIME, zaq))
        c.commit()
    a = _lead(pool, zaq, "pos_festa", HOJE - timedelta(days=1), nome=nome_lead)
    with pool.connection() as c:
        conv = c.execute("""insert into conversas (conta_id, prospeccao_id, contato_ref, agente_ativo)
                            values (%s,%s,'5586988887777',true) returning id""", (PRIME, a)).fetchone()[0]
        c.commit()
    return zaq, a, conv


def test_pos_festa_que_falha_tenta_3_vezes_espacadas_e_desiste(pool, rec, monkeypatch):
    """Antes, a falha apagava a linha e o relógio mandava de novo a cada ~2 min."""
    _zaq, a, _conv = _ia_com_conversa(pool)
    tentativas = []

    def _falha(c, conta, canal, destino, texto, conversa_id=None):
        tentativas.append(texto)
        return {"ok": False, "erro": "chip fora do ar"}
    monkeypatch.setattr(agente, "_mandar", _falha)
    t = AGORA
    for _ in range(12):                                     # 12 ciclos de 2 min
        fx.rodar(pool, t)
        t += timedelta(minutes=2)
    assert len(tentativas) == 1                             # 30 min entre as tentativas
    for i in range(1, 6):
        fx.rodar(pool, AGORA + timedelta(minutes=31 * i))
    assert len(tentativas) == fx.MAX_FALHAS                 # e desiste na terceira
    with pool.connection() as c:
        falhas, enviado = c.execute("select envio_falhas, enviado_em from pos_festa_envios "
                                    "where prospeccao_id=%s", (a,)).fetchone()
    assert falhas == fx.MAX_FALHAS and enviado is None


def test_pos_festa_com_prazo_estourado_nao_sai_duas_vezes(pool, rec, monkeypatch):
    _zaq, a, _conv = _ia_com_conversa(pool)
    tentativas = []

    def _lento(c, conta, canal, destino, texto, conversa_id=None):
        tentativas.append(texto)
        return {"ok": False, "erro": "Read timed out. (read timeout=15)"}
    monkeypatch.setattr(agente, "_mandar", _lento)
    fx.rodar(pool, AGORA)
    fx.rodar(pool, AGORA + timedelta(hours=1))
    assert len(tentativas) == 1
    with pool.connection() as c:
        assert c.execute("select enviado_em from pos_festa_envios where prospeccao_id=%s",
                         (a,)).fetchone()[0] is not None


def test_pos_festa_da_ia_sem_conversa_nao_avisa_o_numero_do_dono(pool, rec):
    zaq = _membro(pool, "ZAQ SDR", "5586900000009")
    with pool.connection() as c:
        c.execute("""insert into chip_regra (conta_id, chip_id, ativa, membro_id, ia_ligada)
                     values (%s,36,true,%s,true)""", (PRIME, zaq))
        c.commit()
    _lead(pool, zaq, "pos_festa", HOJE - timedelta(days=1), nome="Sem Conversa")
    assert fx.rodar(pool, AGORA)["pos_festa"] == 0
    assert not [z for z in rec["zap"] if z[0] == "5586900000009"]
    assert rec["push"] == []


def test_a_disputa_do_card_da_ia_nao_avisa_o_numero_do_dono(pool, rec):
    zaq = _membro(pool, "ZAQ SDR", "5586900000009")
    with pool.connection() as c:
        c.execute("""insert into chip_regra (conta_id, chip_id, ativa, membro_id, ia_ligada)
                     values (%s,36,true,%s,true)""", (PRIME, zaq))
        c.commit()
    dia = date(2027, 4, 3)
    a = _lead(pool, zaq, "evento_realizado", dia)
    _reserva(pool, a, dia, AGORA + timedelta(days=5))
    _lead(pool, zaq, "proposta", dia, nome="Quer também")
    assert fx.rodar(pool, AGORA)["disputas"] == 1           # a disputa fica registrada
    assert not [z for z in rec["zap"] if z[0] == "5586900000009"]


def test_o_teto_do_chip_segura_o_pos_festa_sem_gastar_tentativa(pool, rec):
    from finance import teto_chip as tc
    _zaq, a, _conv = _ia_com_conversa(pool)
    with pool.connection() as c:
        for _ in range(tc.POR_DIA):
            c.execute("""insert into envios_automaticos (conta_id, chip_id, origem, criado_em)
                         values (%s,null,'teste',%s)""", (PRIME, AGORA - timedelta(hours=2)))
        c.commit()
    assert fx.rodar(pool, AGORA)["pos_festa"] == 0
    assert rec["cliente"] == []
    with pool.connection() as c:
        falhas, em = c.execute("select envio_falhas, envio_falhou_em from pos_festa_envios "
                               "where prospeccao_id=%s", (a,)).fetchone()
    assert falhas == 0 and em is None                       # livre pro próximo ciclo


def test_a_migracao_425_marca_como_enviado_o_que_ja_saiu(pool):
    with pool.connection() as c:
        c.execute("alter table pos_festa_envios drop column envio_falhas, "
                  "drop column envio_falhou_em, drop column enviado_em")
        c.execute("insert into pos_festa_envios (prospeccao_id, conta_id, quem) values (9001,%s,'ia'), "
                  "(9002,%s,'vendedor')", (PRIME, PRIME))
        for _ in range(2):                                  # idempotente
            c.execute((BASE / "425_revisao_motores_parte1.sql").read_text(encoding="utf-8"))
        r = dict(c.execute("select prospeccao_id, enviado_em is not null from pos_festa_envios "
                           "where prospeccao_id in (9001,9002)").fetchall())
        c.commit()
    assert r == {9001: True, 9002: False}
